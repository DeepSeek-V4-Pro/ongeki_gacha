"""养成迁移与副本演练；不自动打开或改写部署数据库。"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from .gacha_core import load_cards
from .growth_core import CURVE_VERSION, FRAGMENT_RATES, legacy_growth

MIGRATION_ID = "growth-schema-v1"
ITEM_MIGRATION_ID = "growth-schema-v2"
CURVE_MIGRATION_ID = "affection-curve-v2"
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS schema_migrations (
        migration_id TEXT PRIMARY KEY, applied_at TEXT NOT NULL, checksum TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS player_characters (
        qq_id TEXT NOT NULL REFERENCES players(qq_id),
        character_id INTEGER NOT NULL CHECK(character_id BETWEEN 1000 AND 1016),
        affection_points INTEGER NOT NULL DEFAULT 0 CHECK(affection_points >= 0),
        curve_version TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(qq_id, character_id))""",
    """CREATE TABLE IF NOT EXISTS player_growth_profile (
        qq_id TEXT PRIMARY KEY REFERENCES players(qq_id),
        partner_character_id INTEGER CHECK(partner_character_id BETWEEN 1000 AND 1016),
        title_id TEXT, nameplate_id TEXT, attachment_id TEXT,
        FOREIGN KEY(qq_id, partner_character_id) REFERENCES player_characters(qq_id, character_id))""",
    """CREATE TABLE IF NOT EXISTS player_items (
        qq_id TEXT NOT NULL REFERENCES players(qq_id),
        item_id TEXT NOT NULL CHECK(item_id IN ('gift_small','gift_medium','gift_large','flower_fragment')),
        quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0), PRIMARY KEY(qq_id, item_id))""",
    """CREATE TABLE IF NOT EXISTS affection_reward_claims (
        qq_id TEXT NOT NULL, character_id INTEGER NOT NULL, reward_key TEXT NOT NULL,
        config_version TEXT NOT NULL, claimed_at TEXT NOT NULL,
        PRIMARY KEY(qq_id, character_id, reward_key),
        FOREIGN KEY(qq_id, character_id) REFERENCES player_characters(qq_id, character_id))""",
    """CREATE TABLE IF NOT EXISTS player_cosmetics (
        qq_id TEXT NOT NULL REFERENCES players(qq_id),
        cosmetic_type TEXT NOT NULL CHECK(cosmetic_type IN ('Trophy','NamePlate','Attachment','ProfileVoice')),
        cosmetic_id TEXT NOT NULL, source TEXT NOT NULL, obtained_at TEXT NOT NULL,
        PRIMARY KEY(qq_id, cosmetic_type, cosmetic_id))""",
    """CREATE TABLE IF NOT EXISTS growth_daily_usage (
        qq_id TEXT NOT NULL REFERENCES players(qq_id), utc_date TEXT NOT NULL, action TEXT NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
        PRIMARY KEY(qq_id, utc_date, action))""",
    """CREATE TABLE IF NOT EXISTS growth_events (
        event_key TEXT PRIMARY KEY, qq_id TEXT NOT NULL REFERENCES players(qq_id),
        source TEXT NOT NULL, request_json TEXT NOT NULL, result_json TEXT NOT NULL,
        rule_version TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS growth_migration_inventory (
        qq_id TEXT NOT NULL, card_id INTEGER NOT NULL, copies INTEGER NOT NULL,
        old_kaika INTEGER NOT NULL, old_cho_kaika INTEGER NOT NULL,
        resolved INTEGER NOT NULL DEFAULT 0 CHECK(resolved IN (0,1)),
        diagnostic TEXT NOT NULL DEFAULT '', PRIMARY KEY(qq_id, card_id),
        FOREIGN KEY(qq_id, card_id) REFERENCES inventory(qq_id, card_id))""",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def change_item(conn: sqlite3.Connection, qq_id: str, item: str, delta: int) -> None:
    """调用方持有写事务；CHECK约束保证不能出现负库存。"""
    conn.execute("INSERT OR IGNORE INTO player_items(qq_id,item_id,quantity) VALUES(?,?,0)", (qq_id, item))
    conn.execute("UPDATE player_items SET quantity=quantity+? WHERE qq_id=? AND item_id=?", (delta, qq_id, item))


def _migrate_player_items(conn: sqlite3.Connection, timestamp: str) -> None:
    """v2：扩展物品白名单以支持解花券；旧库按原表重建，数据不改。"""
    applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE migration_id=?",
        (ITEM_MIGRATION_ID,),
    ).fetchone()
    if applied:
        return
    conn.execute(
        """CREATE TABLE player_items_v2 (
            qq_id TEXT NOT NULL REFERENCES players(qq_id),
            item_id TEXT NOT NULL CHECK(item_id IN
                ('gift_small','gift_medium','gift_large','flower_fragment','bloom_ticket')),
            quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
            PRIMARY KEY(qq_id, item_id))"""
    )
    conn.execute(
        """INSERT INTO player_items_v2(qq_id,item_id,quantity)
           SELECT qq_id,item_id,quantity FROM player_items"""
    )
    conn.execute("DROP TABLE player_items")
    conn.execute("ALTER TABLE player_items_v2 RENAME TO player_items")
    checksum = hashlib.sha256(b"player_items:bloom_ticket").hexdigest()
    conn.execute(
        "INSERT INTO schema_migrations(migration_id, applied_at, checksum) VALUES(?,?,?)",
        (ITEM_MIGRATION_ID, timestamp, checksum),
    )


def _migrate_affection_curve(conn: sqlite3.Connection, timestamp: str) -> None:
    """v2 曲线修正：原作公式是 Point×Scale/100，旧实现小了 10 倍。

为保留用户既有等级，把旧曲线下的好感点 ×10 后再切换到新阈值；
    这是一次性迁移，不会在后续启动重复放大。
    """
    applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE migration_id=?",
        (CURVE_MIGRATION_ID,),
    ).fetchone()
    if applied:
        return
    conn.execute(
        """UPDATE player_characters
           SET affection_points = affection_points * 10,
               curve_version = ?,
               updated_at = ?
           WHERE curve_version = 'affection-v1'""",
        (CURVE_VERSION, timestamp),
    )
    checksum = hashlib.sha256(b"affection-v1->v2:x10").hexdigest()
    conn.execute(
        "INSERT INTO schema_migrations(migration_id, applied_at, checksum) VALUES(?,?,?)",
        (CURVE_MIGRATION_ID, timestamp, checksum),
    )


def migrate(conn: sqlite3.Connection, rarities: dict[int, str]) -> dict:
    """单个 BEGIN IMMEDIATE 完成全部变更；未知库存保留快照，可按原张数补结算。"""
    if conn.in_transaction:
        raise RuntimeError("迁移不能嵌套事务")
    timestamp = now_iso()
    checksum = hashlib.sha256("\n".join(SCHEMA).encode()).hexdigest()
    report = {"migration_id": MIGRATION_ID, "migrated": 0, "compensation": 0, "unresolved": [], "diagnostics": []}
    conn.execute("BEGIN IMMEDIATE")
    try:
        for statement in SCHEMA:
            conn.execute(statement)
        previous = conn.execute("SELECT checksum FROM schema_migrations WHERE migration_id=?", (MIGRATION_ID,)).fetchone()
        if previous and previous[0] != checksum:
            raise ValueError("已执行迁移的结构摘要变化，需要新的迁移版本")
        _migrate_player_items(conn, timestamp)
        _migrate_affection_curve(conn, timestamp)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(inventory)")}
        for name, definition in {
            "bloom_stage": "INTEGER NOT NULL DEFAULT 0 CHECK(bloom_stage IN (0,1,2))",
            "kaika_at": "TEXT", "cho_kaika_at": "TEXT",
            "growth_origin": "TEXT NOT NULL DEFAULT 'new'",
        }.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE inventory ADD COLUMN {name} {definition}")
        if previous is None:
            conn.execute("""INSERT INTO growth_migration_inventory
                (qq_id,card_id,copies,old_kaika,old_cho_kaika)
                SELECT qq_id,card_id,copies,is_kaika,is_cho_kaika FROM inventory""")
        for row in conn.execute("""SELECT qq_id,card_id,copies,old_kaika,old_cho_kaika
                                    FROM growth_migration_inventory WHERE resolved=0""").fetchall():
            qq_id, card_id, copies, kaika, cho = row
            rarity = rarities.get(card_id)
            stage, fragments = legacy_growth(rarity, copies, bool(kaika), bool(cho))
            diagnostic = ""
            if rarity not in FRAGMENT_RATES:
                diagnostic = "unknown_rarity_or_card"
                report["unresolved"].append({"qq_id": qq_id, "card_id": card_id, "copies": copies})
            elif cho and copies < (13 if rarity == "N" else 7):
                diagnostic = "legacy_stage_exceeds_copies"
            if diagnostic:
                report["diagnostics"].append({"qq_id": qq_id, "card_id": card_id, "reason": diagnostic})
            conn.execute("""UPDATE inventory SET bloom_stage=MAX(bloom_stage,?),
                            is_kaika=CASE WHEN MAX(bloom_stage,?)>=1 THEN 1 ELSE 0 END,
                            is_cho_kaika=CASE WHEN MAX(bloom_stage,?)=2 THEN 1 ELSE 0 END,
                            growth_origin='legacy' WHERE qq_id=? AND card_id=?""",
                         (stage, stage, stage, qq_id, card_id))
            if rarity in FRAGMENT_RATES:
                key = json.dumps([MIGRATION_ID, qq_id, card_id], separators=(",", ":"))
                inserted = conn.execute("""INSERT OR IGNORE INTO growth_events
                    (event_key,qq_id,source,request_json,result_json,rule_version,created_at)
                    VALUES(?,?,'legacy_migration',?,?,?,?)""", (
                    key, qq_id, json.dumps({"card_id": card_id, "copies": copies}),
                    json.dumps({"stage": stage, "flower_fragment": fragments}), CURVE_VERSION, timestamp))
                if inserted.rowcount:
                    change_item(conn, qq_id, "flower_fragment", fragments)
                    report["compensation"] += fragments
                conn.execute("UPDATE growth_migration_inventory SET resolved=1,diagnostic=? WHERE qq_id=? AND card_id=?",
                             (diagnostic, qq_id, card_id))
                report["migrated"] += 1
            else:
                conn.execute("UPDATE growth_migration_inventory SET diagnostic=? WHERE qq_id=? AND card_id=?",
                             (diagnostic, qq_id, card_id))
        conn.execute("INSERT OR IGNORE INTO schema_migrations VALUES(?,?,?)", (MIGRATION_ID, timestamp, checksum))
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("迁移后外键检查失败")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return report


def preview_copy(source: Path, destination: Path, rarities: dict[int, str]) -> dict:
    """backup API 读取一致性副本；拒绝覆盖任何现有文件。"""
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if source == destination or destination.exists():
        raise ValueError("演练输出必须是不同且尚不存在的文件")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # 排他创建避免误覆盖；迁移失败保留副本以便诊断。
    with destination.open("xb"):
        pass
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(destination, isolation_level=None)) as copied:
            original.backup(copied)
            copied.execute("PRAGMA foreign_keys=ON")
            result = migrate(copied, rarities)
            result["integrity_check"] = copied.execute("PRAGMA integrity_check").fetchone()[0]
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--output-db", type=Path, required=True)
    parser.add_argument("--cards", type=Path, default=Path(__file__).parent / "assets/card_data/card_info_merged.json")
    args = parser.parse_args()
    result = preview_copy(args.source_db, args.output_db, {c.id: c.rarity for c in load_cards(args.cards).cards})
    args.output_db.with_suffix(".migration.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
