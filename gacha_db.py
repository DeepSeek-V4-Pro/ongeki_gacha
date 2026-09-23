"""SQLite 持久化：用户、签到、库存与抽卡日志。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import json
import random
import sqlite3
import threading

from .gacha_core import CardCollection
from .growth_core import duplicate_fragments
from .growth_migration import change_item, migrate, MIGRATION_ID, ITEM_MIGRATION_ID, CURVE_MIGRATION_ID
from .starter_cards import STARTER_CARDS, STARTER_CARD_IDS
from .growth_core import CURVE_VERSION

WEEKLY_RESET_DAY = 3  # 0=Monday, 3=Thursday
WEEKLY_RESET_HOUR = 0
CHECKIN_JACKPOT_PROBABILITY = 0.0000005  # 0.00005%
CHECKIN_LUCKY_PROBABILITY = 0.0000095   # 0.00095%
CHECKIN_JACKPOT_BONUS = 99999
CHECKIN_LUCKY_BONUS = 9999


@dataclass(frozen=True)
class PlayerState:
    """用户当前状态。"""

    qq_id: str
    points: int
    total_checkins: int
    total_pulls: int
    last_checkin_date: str | None
    streak_days: int
    monthly_card_expires_at: str = ""
    monthly_card_purchase_count: int = 0
    half_price_5_pull_count: int = 0
    savings_bonus_level: int = 0
    savings_bonus_start_date: str = ""


@dataclass(frozen=True)
class InventoryEntry:
    """库存中的单张卡牌记录。"""

    card_id: int
    copies: int
    is_kaika: bool
    is_cho_kaika: bool
    bloom_stage: int = 0
    kaika_at: str | None = None
    cho_kaika_at: str | None = None
    growth_origin: str = "new"


@dataclass(frozen=True)
class DrawCommitment:
    """一次抽卡结果对应的库存变化。"""

    card_id: int
    is_new: bool
    copies: int
    is_kaika: bool
    is_cho_kaika: bool
    fragments: int = 0


@dataclass(frozen=True)
class DrawReceipt:
    """扣点并写入库存后的结果。"""

    success: bool
    points: int
    commitments: list[DrawCommitment]
    error: str = ""
    select_points: int = 0
    max_select_points: int = 0
    select_ready: bool = False
    select_claimed: bool = False


@dataclass(frozen=True)
class CheckinReceipt:
    """签到结果。"""

    success: bool
    reward: int
    points: int
    date: str
    error: str = ""
    bonus: int = 0
    bonus_kind: str = ""
    streak_days: int = 0
    streak_extra: int = 0
    weekly_reward: int = 0
    cycle_reward: int = 0
    monthly_reward: int = 0
    non_gacha_card_id: int | None = None
    non_gacha_is_new: bool = False
    non_gacha_copies: int = 0
    non_gacha_is_kaika: bool = False
    non_gacha_is_cho_kaika: bool = False
    growth_fragments: int = 0
    small_gifts: int = 0
    medium_gifts: int = 0
    large_gifts: int = 0


@dataclass(frozen=True)
class MonthlyCardReceipt:
    """购买或续费月卡结果。"""

    success: bool
    points: int
    expires_at: str
    remaining_days: int
    half_price_5_pull_count: int
    error: str = ""


@dataclass(frozen=True)
class GrantReceipt:
    """管理员发放点数结果。"""

    success: bool
    points: int
    error: str = ""


@dataclass(frozen=True)
class TaskRecord:
    """一条随机任务记录。"""

    id: int
    qq_id: str
    task_kind: str
    game: str
    song_id: str
    song_title: str
    artist: str
    difficulty_index: int | None
    difficulty_label: str
    target_level: str
    target_level_value: float
    requirement_text: str
    reward: int
    cover_url: str
    task_date: str
    status: str
    created_at: str
    submitted_at: str | None
    submitted_by: str
    reviewed_by: str
    reviewed_at: str | None
    reviewed_grade: str
    awarded: bool
    note: str


@dataclass(frozen=True)
class TaskReceipt:
    """接取/提交任务结果。"""

    success: bool
    error: str = ""
    task_id: int = 0
    task: TaskRecord | None = None


@dataclass(frozen=True)
class TaskReviewReceipt:
    """审核任务结果。"""

    success: bool
    points: int = 0
    grade: str = ""
    error: str = ""
    growth_fragments: int = 0
    medium_gifts: int = 0
    large_gifts: int = 0
    bloom_tickets: int = 0
    cooldown_text: str = ""


@dataclass(frozen=True)
class TaskResetReceipt:
    """管理员重置任务结果。"""

    success: bool
    error: str = ""


@dataclass(frozen=True)
class UltimateProgress:
    """用户终极任务进度。"""

    stage: int
    active_task_id: int | None
    finished: bool
    completed_at: str | None


@dataclass(frozen=True)
class PoolSelectState:
    """某个用户在某卡池中的天井/セレクト状态。"""

    pool_id: str
    select_points: int
    max_select_points: int
    is_claimed: bool

    @property
    def is_ready(self) -> bool:
        return (
            self.max_select_points > 0
            and self.select_points >= self.max_select_points
            and not self.is_claimed
        )


@dataclass(frozen=True)
class SelectClaimReceipt:
    """兑换天井选择卡结果。"""

    success: bool
    points: int
    card_id: int
    copies: int
    is_new: bool
    error: str = ""
    fragments: int = 0
    is_kaika: bool = False
    is_cho_kaika: bool = False


class GachaDatabase:
    """插件专用 SQLite 数据库。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._random = random.SystemRandom()
        self._growth_ready = False
        self._growth_enabled = False
        self._growth_rules: dict[str, Any] = {}

    def open(self) -> None:
        """创建目录、连接并初始化表结构。"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS players (
                qq_id            TEXT PRIMARY KEY,
                nickname         TEXT NOT NULL DEFAULT '',
                points           INTEGER NOT NULL DEFAULT 0,
                last_checkin_date TEXT,
                total_checkins   INTEGER NOT NULL DEFAULT 0,
                total_pulls      INTEGER NOT NULL DEFAULT 0,
                streak_days      INTEGER NOT NULL DEFAULT 0,
                monthly_card_expires_at TEXT NOT NULL DEFAULT '',
                monthly_card_purchased_at TEXT NOT NULL DEFAULT '',
                monthly_card_purchase_count INTEGER NOT NULL DEFAULT 0,
                half_price_5_pull_count INTEGER NOT NULL DEFAULT 0,
                savings_bonus_level INTEGER NOT NULL DEFAULT 0,
                weekly_5_guarantee_week TEXT NOT NULL DEFAULT '',
                weekly_5_guarantee_used INTEGER NOT NULL DEFAULT 0,
                created_at       TEXT NOT NULL,
                updated_at       TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS checkins (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                qq_id         TEXT NOT NULL,
                checkin_date  TEXT NOT NULL,
                reward        INTEGER NOT NULL,
                created_at    TEXT NOT NULL,
                UNIQUE(qq_id, checkin_date),
                FOREIGN KEY(qq_id) REFERENCES players(qq_id)
            );

            CREATE TABLE IF NOT EXISTS inventory (
                qq_id             TEXT NOT NULL,
                card_id           INTEGER NOT NULL,
                copies            INTEGER NOT NULL DEFAULT 1,
                is_kaika          INTEGER NOT NULL DEFAULT 0,
                is_cho_kaika      INTEGER NOT NULL DEFAULT 0,
                first_obtained_at TEXT NOT NULL,
                last_obtained_at  TEXT NOT NULL,
                PRIMARY KEY(qq_id, card_id),
                FOREIGN KEY(qq_id) REFERENCES players(qq_id)
            );

            CREATE TABLE IF NOT EXISTS gacha_logs (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                qq_id          TEXT NOT NULL,
                draw_count     INTEGER NOT NULL,
                cost           INTEGER NOT NULL,
                rarity_profile TEXT NOT NULL,
                result_json    TEXT NOT NULL,
                created_at     TEXT NOT NULL,
                FOREIGN KEY(qq_id) REFERENCES players(qq_id)
            );

            CREATE TABLE IF NOT EXISTS pool_select_state (
                qq_id             TEXT NOT NULL,
                pool_id           TEXT NOT NULL,
                select_points     INTEGER NOT NULL DEFAULT 0,
                max_select_points INTEGER NOT NULL DEFAULT 0,
                select_claimed    INTEGER NOT NULL DEFAULT 0,
                updated_at        TEXT NOT NULL,
                PRIMARY KEY(qq_id, pool_id),
                FOREIGN KEY(qq_id) REFERENCES players(qq_id)
            );

            CREATE TABLE IF NOT EXISTS admin_grants (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                grantor_id    TEXT NOT NULL,
                target_id     TEXT NOT NULL,
                amount        INTEGER NOT NULL,
                note          TEXT NOT NULL DEFAULT '',
                created_at    TEXT NOT NULL,
                FOREIGN KEY(target_id) REFERENCES players(qq_id)
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                qq_id               TEXT NOT NULL,
                task_kind           TEXT NOT NULL,
                game                TEXT NOT NULL,
                song_id             TEXT NOT NULL,
                song_title          TEXT NOT NULL,
                artist              TEXT NOT NULL DEFAULT '',
                difficulty_index    INTEGER,
                difficulty_label    TEXT NOT NULL DEFAULT '',
                target_level        TEXT NOT NULL DEFAULT '',
                target_level_value  REAL NOT NULL DEFAULT 0,
                requirement_text    TEXT NOT NULL DEFAULT '',
                reward              INTEGER NOT NULL DEFAULT 0,
                cover_url           TEXT NOT NULL DEFAULT '',
                task_date           TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'active',
                created_at          TEXT NOT NULL,
                submitted_at        TEXT,
                submitted_by        TEXT NOT NULL DEFAULT '',
                reviewed_by         TEXT NOT NULL DEFAULT '',
                reviewed_at         TEXT,
                reviewed_grade      TEXT NOT NULL DEFAULT '',
                awarded             INTEGER NOT NULL DEFAULT 0,
                note                TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(qq_id) REFERENCES players(qq_id)
            );

            CREATE TABLE IF NOT EXISTS task_audit (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                qq_id       TEXT NOT NULL,
                task_id     INTEGER NOT NULL,
                action      TEXT NOT NULL,
                actor_id    TEXT NOT NULL,
                grade       TEXT NOT NULL DEFAULT '',
                points      INTEGER NOT NULL DEFAULT 0,
                note        TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL,
                FOREIGN KEY(qq_id) REFERENCES players(qq_id)
            );

            CREATE TABLE IF NOT EXISTS daily_task_quota (
                qq_id       TEXT NOT NULL,
                task_date   TEXT NOT NULL,
                task_kind   TEXT NOT NULL,
                used_count  INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(qq_id, task_date, task_kind),
                FOREIGN KEY(qq_id) REFERENCES players(qq_id)
            );

            CREATE TABLE IF NOT EXISTS ultimate_completed_songs (
                qq_id          TEXT NOT NULL,
                game           TEXT NOT NULL,
                song_id        TEXT NOT NULL,
                completed_at   TEXT NOT NULL,
                PRIMARY KEY(qq_id, game, song_id),
                FOREIGN KEY(qq_id) REFERENCES players(qq_id)
            );

            CREATE TABLE IF NOT EXISTS ultimate_progress (
                qq_id           TEXT PRIMARY KEY,
                stage           INTEGER NOT NULL DEFAULT 0,
                active_task_id  INTEGER,
                finished        INTEGER NOT NULL DEFAULT 0,
                completed_at    TEXT,
                FOREIGN KEY(qq_id) REFERENCES players(qq_id)
            );

            CREATE TABLE IF NOT EXISTS ultimate_completed_charts (
                qq_id            TEXT NOT NULL,
                game             TEXT NOT NULL,
                song_id          TEXT NOT NULL,
                difficulty_index INTEGER NOT NULL DEFAULT -1,
                completed_at     TEXT NOT NULL,
                PRIMARY KEY(qq_id, game, song_id, difficulty_index),
                FOREIGN KEY(qq_id) REFERENCES players(qq_id)
            );

            CREATE TABLE IF NOT EXISTS timed_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                qq_id       TEXT NOT NULL,
                kind        TEXT NOT NULL,
                event_key   TEXT NOT NULL DEFAULT '',
                occurred_at REAL NOT NULL,
                payload     TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS pending_card_reveals (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                qq_id         TEXT NOT NULL,
                card_id       INTEGER NOT NULL,
                before_copies INTEGER NOT NULL DEFAULT 0,
                after_copies  INTEGER NOT NULL DEFAULT 0,
                is_kaika     INTEGER NOT NULL DEFAULT 0,
                is_cho_kaika INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS player_cooldowns (
                qq_id     TEXT NOT NULL,
                key       TEXT NOT NULL,
                last_at   TEXT NOT NULL,
                PRIMARY KEY(qq_id, key)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_timed_events_key
                ON timed_events(kind, event_key) WHERE event_key <> '';
            CREATE INDEX IF NOT EXISTS idx_timed_events_lookup
                ON timed_events(qq_id, kind, occurred_at);
            CREATE INDEX IF NOT EXISTS idx_pending_card_reveals_lookup
                ON pending_card_reveals(qq_id, id);

            CREATE TABLE IF NOT EXISTS app_settings (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL DEFAULT '',
                updated_at  TEXT NOT NULL
            );
            """
        )
        # 旧版按“曲目”记终极完成。优先从历史任务恢复当时的 difficulty_index，
        # 使同曲其他难度仍可抽取；只有恢复不到具体难度时才保留整曲锁（-1）。
        conn.execute(
            """
            INSERT OR IGNORE INTO ultimate_completed_charts(
                qq_id, game, song_id, difficulty_index, completed_at
            )
            SELECT qq_id, game, song_id, COALESCE(difficulty_index, -1),
                   COALESCE(reviewed_at, created_at)
            FROM tasks
            WHERE task_kind = 'ultimate'
              AND status = 'approved'
              AND awarded = 1
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO ultimate_completed_charts(
                qq_id, game, song_id, difficulty_index, completed_at
            )
            SELECT s.qq_id, s.game, s.song_id, -1, s.completed_at
            FROM ultimate_completed_songs AS s
            WHERE NOT EXISTS (
                SELECT 1 FROM ultimate_completed_charts AS c
                WHERE c.qq_id = s.qq_id
                  AND c.game = s.game
                  AND c.song_id = s.song_id
            )
            """
        )
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(players)").fetchall()}
        if "weekly_5_guarantee_week" not in existing_columns:
            conn.execute(
                "ALTER TABLE players "
                "ADD COLUMN weekly_5_guarantee_week TEXT NOT NULL DEFAULT ''"
            )
        if "weekly_5_guarantee_used" not in existing_columns:
            conn.execute(
                "ALTER TABLE players "
                "ADD COLUMN weekly_5_guarantee_used INTEGER NOT NULL DEFAULT 0"
            )
        if "streak_days" not in existing_columns:
            conn.execute(
                "ALTER TABLE players "
                "ADD COLUMN streak_days INTEGER NOT NULL DEFAULT 0"
            )
        if "monthly_card_expires_at" not in existing_columns:
            conn.execute(
                "ALTER TABLE players "
                "ADD COLUMN monthly_card_expires_at TEXT NOT NULL DEFAULT ''"
            )
        if "monthly_card_purchased_at" not in existing_columns:
            conn.execute(
                "ALTER TABLE players "
                "ADD COLUMN monthly_card_purchased_at TEXT NOT NULL DEFAULT ''"
            )
        if "monthly_card_purchase_count" not in existing_columns:
            conn.execute(
                "ALTER TABLE players "
                "ADD COLUMN monthly_card_purchase_count INTEGER NOT NULL DEFAULT 0"
            )
        if "half_price_5_pull_count" not in existing_columns:
            conn.execute(
                "ALTER TABLE players "
                "ADD COLUMN half_price_5_pull_count INTEGER NOT NULL DEFAULT 0"
            )
        if "savings_bonus_level" not in existing_columns:
            conn.execute(
                "ALTER TABLE players "
                "ADD COLUMN savings_bonus_level INTEGER NOT NULL DEFAULT 0"
            )
        if "savings_bonus_start_date" not in existing_columns:
            conn.execute(
                "ALTER TABLE players "
                "ADD COLUMN savings_bonus_start_date TEXT NOT NULL DEFAULT ''"
            )
        reveal_columns = {row[1] for row in conn.execute("PRAGMA table_info(pending_card_reveals)")}
        if "is_kaika" not in reveal_columns:
            conn.execute("ALTER TABLE pending_card_reveals ADD COLUMN is_kaika INTEGER NOT NULL DEFAULT 0")
        if "is_cho_kaika" not in reveal_columns:
            conn.execute("ALTER TABLE pending_card_reveals ADD COLUMN is_cho_kaika INTEGER NOT NULL DEFAULT 0")
        self._conn = conn

    def initialize_growth(self, cards: CardCollection, *, enabled: bool = False, rules: dict | None = None) -> dict:
        """启动时停写备份并迁移；关闭产出也不恢复按张数自动解花。"""
        with self._lock:
            conn = self._conn
            if conn is None:
                raise RuntimeError("数据库尚未打开")
            exists = conn.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone()
            applied = {row[0] for row in conn.execute("SELECT migration_id FROM schema_migrations")} if exists else set()
            migrated = {MIGRATION_ID, ITEM_MIGRATION_ID, CURVE_MIGRATION_ID} <= applied
            backup_path = None
            if not migrated and conn.execute("SELECT 1 FROM players LIMIT 1").fetchone():
                from uuid import uuid4
                backup_path = self._db_path.with_name(f"{self._db_path.stem}.before-growth-{uuid4().hex}.db")
                with backup_path.open("xb"):
                    pass
                backup = sqlite3.connect(backup_path)
                try:
                    conn.backup(backup)
                finally:
                    backup.close()
            result = migrate(conn, {c.id: c.rarity for c in cards.cards})
            self._growth_ready = True
            self._growth_enabled = enabled
            self._growth_rules = rules or {}
            self._starter_cards = {cid:card_id for cid,card_id in STARTER_CARDS.items() if card_id in cards.by_id}
            if enabled:
                conn.execute('BEGIN IMMEDIATE')
                try:
                    for player in conn.execute('SELECT qq_id FROM players').fetchall():
                        self._ensure_starter_cards(conn, player[0], self._now_iso())
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
            result["backup_path"] = str(backup_path) if backup_path else None
            return result

    def _grant_card(self, conn: sqlite3.Connection, qq_id: str, card_id: int,
                    rarity: str, now: str, *, source: str,
                    announce: bool = True) -> DrawCommitment:
        """仅在调用方写事务内执行；三类发卡和好感奖励共用此路径。"""
        if not self._growth_ready or not conn.in_transaction:
            raise RuntimeError("发卡必须先初始化养成迁移并开启事务")
        row = conn.execute("SELECT copies,bloom_stage FROM inventory WHERE qq_id=? AND card_id=?", (qq_id, card_id)).fetchone()
        previous = int(row[0]) if row else 0
        stage = int(row[1]) if row else 0
        fragments = duplicate_fragments(rarity, previous, previous + 1, source=source)
        if not self._growth_enabled:
            fragments = 0
        conn.execute("""INSERT INTO inventory
            (qq_id,card_id,copies,is_kaika,is_cho_kaika,first_obtained_at,last_obtained_at,bloom_stage)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(qq_id,card_id) DO UPDATE SET
            copies=excluded.copies,is_kaika=excluded.is_kaika,is_cho_kaika=excluded.is_cho_kaika,
            last_obtained_at=excluded.last_obtained_at""",
            (qq_id, card_id, previous+1, int(stage>=1), int(stage==2), now, now, stage))
        if fragments:
            change_item(conn, qq_id, "flower_fragment", fragments)
            key = json.dumps(["duplicate", qq_id, card_id, previous + 1], separators=(",", ":"))
            conn.execute("INSERT INTO growth_events VALUES(?,?,?,?,?,?,?)", (
                key, qq_id, source, json.dumps({"card_id": card_id, "copies": previous+1}),
                json.dumps({"flower_fragment": fragments}), "growth-v1", now))
        if announce and source == "affection_reward" and card_id in STARTER_CARD_IDS:
            conn.execute(
                """INSERT INTO pending_card_reveals(
                       qq_id, card_id, before_copies, after_copies,
                       is_kaika, is_cho_kaika, created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (qq_id, card_id, previous, previous + 1, int(stage>=1), int(stage==2), now),
            )
        return DrawCommitment(card_id, row is None, previous+1, stage>=1, stage==2, fragments)

    def _award_growth_daily(self, conn: sqlite3.Connection, qq_id: str, today: str,
                            source: str, event_id: str, now: str) -> tuple[int, int, int, int]:
        """与签到或审核同事务发奖；返回（小礼物, 中礼物, 花之碎片, 大礼物）。

        签到物品集中在每月前若干天的活动里：礼物日发礼物，其余活动日发碎片，
        活动之外的签到只发点数。任务物品仍按审核成功的UTC日期计每日上限。
        """
        if not self._growth_enabled:
            return 0, 0, 0, 0
        key = json.dumps(["daily_growth", source, qq_id, event_id], separators=(",", ":"))
        if conn.execute("SELECT 1 FROM growth_events WHERE event_key=?", (key,)).fetchone():
            return 0, 0, 0, 0
        rules = self._growth_rules
        small_gifts = medium_gifts = large_gifts = fragments = 0
        if source == "checkin":
            day = int(today[8:10])
            if 1 <= day <= rules["monthly_event_days"]:
                if day in rules["monthly_event_small_gift_days"]:
                    small_gifts = 1
                elif day in rules["monthly_event_medium_gift_days"]:
                    medium_gifts = 1
                elif day in rules["monthly_event_large_gift_days"]:
                    large_gifts = 1
                else:
                    fragments = rules["monthly_event_fragments"]
        else:
            def quota(action, wanted, cap, period=today):
                old = conn.execute("SELECT quantity FROM growth_daily_usage WHERE qq_id=? AND utc_date=? AND action=?", (qq_id, period, action)).fetchone()
                used = int(old[0]) if old else 0
                amount = min(wanted, max(0, cap-used))
                conn.execute("""INSERT INTO growth_daily_usage VALUES(?,?,?,?) ON CONFLICT(qq_id,utc_date,action)
                    DO UPDATE SET quantity=excluded.quantity""", (qq_id, period, action, used+amount))
                return amount
            medium_gifts = quota("task_gifts", int(source in rules["task_medium_gift_sources"]), rules["task_medium_gifts_daily_cap"])
            if source == "ultimate" and rules["ultimate_large_gifts_lifetime_cap"] > 0:
                large_gifts = quota("ultimate_large_gifts", 1, rules["ultimate_large_gifts_lifetime_cap"], "lifetime")
            fragments = quota("task_fragments", rules["task_fragments"][source], rules["task_fragments_daily_cap"])
        for item_id, amount in (("gift_small", small_gifts), ("gift_medium", medium_gifts),
                                ("gift_large", large_gifts), ("flower_fragment", fragments)):
            if amount:
                change_item(conn, qq_id, item_id, amount)
        conn.execute("INSERT INTO growth_events VALUES(?,?,?,?,?,?,?)", (
            key, qq_id, source, json.dumps({"event_id": event_id, "utc_date": today}),
            json.dumps({"gift_small": small_gifts, "gift_medium": medium_gifts,
                        "gift_large": large_gifts, "flower_fragment": fragments}), rules["version"], now))
        return small_gifts, medium_gifts, fragments, large_gifts

    def close(self) -> None:
        """关闭数据库连接。"""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            self._growth_ready = False
            self._growth_enabled = False
            self._growth_rules = {}

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def current_date_str(offset_hours: int) -> str:
        """返回国际时间 UTC 日期；offset_hours 为兼容保留参数。"""
        del offset_hours
        return datetime.now(timezone.utc).date().isoformat()

    def _date_str(self, offset_hours: int) -> str:
        return self.current_date_str(offset_hours)

    @staticmethod
    def monthly_card_remaining_days(expires_at: str, today: str) -> int:
        """返回月卡剩余天数，已过期或无效日期返回 0。"""
        try:
            expires = date.fromisoformat(str(expires_at or ""))
            current = date.fromisoformat(str(today or ""))
        except ValueError:
            return 0
        return (expires - current).days

    @staticmethod
    def _is_previous_date(previous: str | None, current: str) -> bool:
        """判断 previous 是否为 current 的前一天。"""
        if not previous:
            return False
        try:
            return date.fromisoformat(previous) == date.fromisoformat(current) - timedelta(days=1)
        except ValueError:
            return False

    @staticmethod
    def _weekly_5_key(offset_hours: int) -> str:
        """Return the Thursday 00:00 UTC reset key for the current week."""
        del offset_hours
        now = datetime.now(timezone.utc)
        days_since_thursday = (now.weekday() - WEEKLY_RESET_DAY) % 7
        reset_date = (now - timedelta(days=days_since_thursday)).date()
        if days_since_thursday == 0 and now.hour < WEEKLY_RESET_HOUR:
            reset_date -= timedelta(days=7)
        return reset_date.isoformat()

    @staticmethod
    def _sync_weekly_5(
        conn: sqlite3.Connection,
        qq_id: str,
        week_key: str,
        now: str,
    ) -> None:
        conn.execute(
            """
            UPDATE players
            SET weekly_5_guarantee_week = ?,
                weekly_5_guarantee_used = 0,
                updated_at = ?
            WHERE qq_id = ? AND weekly_5_guarantee_week <> ?
            """,
            (week_key, now, qq_id, week_key),
        )

    def _ensure_starter_cards(self, conn, qq_id, now):
        """只初始化17名角色的好感记录；第一张基础N卡改为查看好感页时懒获取。"""
        if not self._growth_enabled:
            return
        for cid in self._starter_cards:
            conn.execute('''INSERT OR IGNORE INTO player_characters
                (qq_id,character_id,affection_points,curve_version,created_at,updated_at)
                VALUES(?,?,0,?,?,?)''', (qq_id,cid,CURVE_VERSION,now,now))

    def grant_starter_card(self, qq_id: str, cid: int, *, reward_keys: tuple[str, ...] = ()) -> dict:
        """查看角色好感页时懒获取该角色的第一张基础N卡，幂等返回。"""
        card_id = self._starter_cards.get(int(cid))
        if card_id is None:
            return {"granted": False, "card_id": None, "copies": 0}
        with self._lock:
            conn = self._conn
            if conn is None:
                raise RuntimeError("数据库尚未打开")
            if not self._growth_ready or not self._growth_enabled:
                return {"granted": False, "card_id": card_id, "copies": 0}
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_player(conn, qq_id)
                row = conn.execute(
                    "SELECT copies FROM inventory WHERE qq_id=? AND card_id=?",
                    (qq_id, card_id),
                ).fetchone()
                # 节点卡与首张卡分别计数，避免先送礼后查看时把节点卡误当首张。
                claimed = 0
                if reward_keys:
                    placeholders = ','.join('?' for _ in reward_keys)
                    claimed = conn.execute(
                        f"SELECT COUNT(*) FROM affection_reward_claims WHERE qq_id=? "
                        f"AND character_id=? AND reward_key IN ({placeholders})",
                        (qq_id, cid, *reward_keys),
                    ).fetchone()[0]
                if row is not None and int(row[0]) > claimed:
                    conn.execute("COMMIT")
                    return {"granted": False, "card_id": card_id, "copies": int(row[0])}
                commitment = self._grant_card(
                    conn,
                    qq_id,
                    card_id,
                    "N",
                    self._now_iso(),
                    source="affection_reward",
                    announce=False,
                )
                conn.execute("COMMIT")
                return {
                    "granted": True,
                    "card_id": card_id,
                    "copies": commitment.copies,
                    "is_new": commitment.is_new,
                }
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def _ensure_player(self, conn: sqlite3.Connection, qq_id: str) -> sqlite3.Row:
        now = self._now_iso()
        conn.execute(
            """
            INSERT OR IGNORE INTO players(qq_id, created_at, updated_at)
            VALUES(?, ?, ?)
            """,
            (qq_id, now, now),
        )
        row = conn.execute("SELECT * FROM players WHERE qq_id = ?", (qq_id,)).fetchone()
        if row is None:
            raise RuntimeError(f"创建用户失败: {qq_id}")
        self._ensure_starter_cards(conn, qq_id, now)
        return row

    def weekly_5_guarantee_available(
        self,
        qq_id: str,
        *,
        tz_offset_hours: int,
    ) -> bool:
        """Whether this user may still use this week's 5-pull guarantee."""
        week_key = self._weekly_5_key(tz_offset_hours)
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_player(conn, qq_id)
                self._sync_weekly_5(conn, qq_id, week_key, self._now_iso())
                row = conn.execute(
                    "SELECT weekly_5_guarantee_used FROM players WHERE qq_id = ?",
                    (qq_id,),
                ).fetchone()
                conn.execute("COMMIT")
                return bool(row) and int(row["weekly_5_guarantee_used"]) == 0
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def claim_weekly_5_guarantee(
        self,
        qq_id: str,
        *,
        tz_offset_hours: int,
    ) -> bool:
        """Atomically claim this week's single 5-pull guarantee right."""
        week_key = self._weekly_5_key(tz_offset_hours)
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_player(conn, qq_id)
                self._sync_weekly_5(conn, qq_id, week_key, self._now_iso())
                row = conn.execute(
                    "SELECT weekly_5_guarantee_used FROM players WHERE qq_id = ?",
                    (qq_id,),
                ).fetchone()
                if row is None or int(row["weekly_5_guarantee_used"]) != 0:
                    conn.execute("ROLLBACK")
                    return False
                conn.execute(
                    """
                    UPDATE players
                    SET weekly_5_guarantee_week = ?,
                        weekly_5_guarantee_used = 1,
                        updated_at = ?
                    WHERE qq_id = ?
                    """,
                    (week_key, self._now_iso(), qq_id),
                )
                conn.execute("COMMIT")
                return True
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def _player_state(self, conn: sqlite3.Connection, qq_id: str) -> PlayerState:
        row = conn.execute("SELECT * FROM players WHERE qq_id = ?", (qq_id,)).fetchone()
        if row is None:
            raise RuntimeError(f"用户不存在: {qq_id}")
        return PlayerState(
            qq_id=str(row["qq_id"]),
            points=int(row["points"]),
            total_checkins=int(row["total_checkins"]),
            total_pulls=int(row["total_pulls"]),
            last_checkin_date=str(row["last_checkin_date"] or ""),
            streak_days=int(row["streak_days"] or 0),
            monthly_card_expires_at=str(row["monthly_card_expires_at"] or ""),
            monthly_card_purchase_count=int(row["monthly_card_purchase_count"] or 0),
            half_price_5_pull_count=int(row["half_price_5_pull_count"] or 0),
            savings_bonus_level=int(row["savings_bonus_level"] or 0),
            savings_bonus_start_date=str(row["savings_bonus_start_date"] or ""),
        )

    def get_player(self, qq_id: str) -> PlayerState:
        """获取或初始化用户。"""
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_player(conn, qq_id)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return self._player_state(conn, qq_id)

    def grant_points(
        self,
        grantor_id: str,
        target_id: str,
        amount: int,
        *,
        note: str = "",
    ) -> GrantReceipt:
        """管理员向指定用户发放点数，并记录发放日志。"""
        if amount <= 0:
            return GrantReceipt(success=False, points=0, error="发放点数必须大于 0")

        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_player(conn, target_id)
                now = self._now_iso()
                conn.execute(
                    """
                    UPDATE players
                    SET points = points + ?,
                        updated_at = ?
                    WHERE qq_id = ?
                    """,
                    (amount, now, target_id),
                )
                conn.execute(
                    """
                    INSERT INTO admin_grants(
                        grantor_id, target_id, amount, note, created_at
                    ) VALUES(?, ?, ?, ?, ?)
                    """,
                    (grantor_id, target_id, amount, str(note or ""), now),
                )
                conn.execute("COMMIT")
                player = self._player_state(conn, target_id)
                return GrantReceipt(success=True, points=player.points)
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def grant_savings_bonus(
        self,
        qq_id: str,
        *,
        thresholds: Iterable[int],
        bonuses: Iterable[int],
        reset_days: int = 60,
        tz_offset_hours: int = 0,
    ) -> tuple[int, int]:
        """发放囤点档位奖励；超过重置周期后清空档位重新计算。"""
        threshold_list = [int(value) for value in thresholds]
        bonus_list = [int(value) for value in bonuses]
        if len(threshold_list) != len(bonus_list):
            raise ValueError("囤点奖励门槛与奖励数量不一致")
        if any(value <= 0 for value in threshold_list) or any(value < 0 for value in bonus_list):
            raise ValueError("囤点奖励配置非法")
        if threshold_list != sorted(set(threshold_list)):
            raise ValueError("囤点奖励门槛必须递增")
        if reset_days < 1:
            raise ValueError("囤点奖励重置周期必须大于等于 1 天")

        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                player_row = self._ensure_player(conn, qq_id)
                points = int(player_row["points"] or 0)
                level = int(player_row["savings_bonus_level"] or 0)
                today = self.current_date_str(tz_offset_hours)
                start_raw = str(player_row["savings_bonus_start_date"] or "")
                reset = False
                try:
                    elapsed = (
                        date.fromisoformat(today)
                        - date.fromisoformat(start_raw)
                    ).days if start_raw else reset_days
                except ValueError:
                    elapsed = reset_days
                if not start_raw:
                    start_raw = today
                elif elapsed >= reset_days:
                    level = 0
                    start_raw = today
                    reset = True
                total_bonus = 0
                while level < len(threshold_list) and points >= threshold_list[level]:
                    total_bonus += bonus_list[level]
                    points += bonus_list[level]
                    level += 1
                if total_bonus or reset or not start_raw:
                    conn.execute(
                        """
                        UPDATE players
                        SET points = points + ?,
                            savings_bonus_level = ?,
                            savings_bonus_start_date = ?,
                            updated_at = ?
                        WHERE qq_id = ?
                        """,
                        (total_bonus, level, start_raw, self._now_iso(), qq_id),
                    )
                conn.execute("COMMIT")
                return total_bonus, points
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def purchase_monthly_card(
        self,
        qq_id: str,
        *,
        price: int,
        duration_days: int,
        renew_max_remaining_days: int,
        half_price_5_pull_count: int,
        tz_offset_hours: int,
    ) -> MonthlyCardReceipt:
        """购买或续费月卡，并发放半价五连次数。"""
        if price < 0 or duration_days <= 0 or renew_max_remaining_days < 0 or half_price_5_pull_count < 0:
            raise ValueError("月卡配置非法")
        today = self._date_str(tz_offset_hours)

        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                player_row = self._ensure_player(conn, qq_id)
                expires_at = str(player_row["monthly_card_expires_at"] or "")
                remaining_days = self.monthly_card_remaining_days(expires_at, today)
                current_points = int(player_row["points"] or 0)
                current_half_count = int(player_row["half_price_5_pull_count"] or 0)

                if remaining_days > renew_max_remaining_days:
                    conn.execute("ROLLBACK")
                    return MonthlyCardReceipt(
                        success=False,
                        points=current_points,
                        expires_at=expires_at,
                        remaining_days=remaining_days,
                        half_price_5_pull_count=current_half_count,
                        error=(
                            f"月卡剩余 {remaining_days} 天，"
                            f"剩余不超过 {renew_max_remaining_days} 天才能续费"
                        ),
                    )
                if current_points < price:
                    conn.execute("ROLLBACK")
                    return MonthlyCardReceipt(
                        success=False,
                        points=current_points,
                        expires_at=expires_at,
                        remaining_days=remaining_days,
                        half_price_5_pull_count=current_half_count,
                        error=f"点数不足（需要 {price}，当前 {current_points}）",
                    )

                if remaining_days > 0:
                    try:
                        expires_date = date.fromisoformat(expires_at)
                    except ValueError:
                        expires_date = None
                    if expires_date is None:
                        new_expires_at = (
                            date.fromisoformat(today) + timedelta(days=duration_days)
                        ).isoformat()
                    else:
                        new_expires_at = (
                            expires_date + timedelta(days=duration_days)
                        ).isoformat()
                else:
                    new_expires_at = (
                        date.fromisoformat(today) + timedelta(days=duration_days)
                    ).isoformat()

                updated = conn.execute(
                    """
                    UPDATE players
                    SET points = points - ?,
                        monthly_card_expires_at = ?,
                        monthly_card_purchased_at = ?,
                        monthly_card_purchase_count = monthly_card_purchase_count + 1,
                        half_price_5_pull_count = half_price_5_pull_count + ?,
                        updated_at = ?
                    WHERE qq_id = ? AND points >= ?
                    """,
                    (
                        price,
                        new_expires_at,
                        self._now_iso(),
                        half_price_5_pull_count,
                        self._now_iso(),
                        qq_id,
                        price,
                    ),
                )
                if updated.rowcount == 0:
                    conn.execute("ROLLBACK")
                    return MonthlyCardReceipt(
                        success=False,
                        points=current_points,
                        expires_at=expires_at,
                        remaining_days=remaining_days,
                        half_price_5_pull_count=current_half_count,
                        error="点数不足",
                    )
                conn.execute("COMMIT")
                player = self._player_state(conn, qq_id)
                new_remaining = self.monthly_card_remaining_days(
                    player.monthly_card_expires_at,
                    today,
                )
                return MonthlyCardReceipt(
                    success=True,
                    points=player.points,
                    expires_at=player.monthly_card_expires_at,
                    remaining_days=new_remaining,
                    half_price_5_pull_count=player.half_price_5_pull_count,
                )
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def half_price_5_pull_available(self, qq_id: str) -> bool:
        """查询是否还有月卡赠送的半价五连次数。"""
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            self._ensure_player(conn, qq_id)
            row = conn.execute(
                "SELECT half_price_5_pull_count FROM players WHERE qq_id = ?",
                (qq_id,),
            ).fetchone()
            return bool(row and int(row["half_price_5_pull_count"]) > 0)

    def claim_half_price_5_pull(self, qq_id: str) -> bool:
        """原子消费一次月卡半价五连次数。"""
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_player(conn, qq_id)
                updated = conn.execute(
                    """
                    UPDATE players
                    SET half_price_5_pull_count = half_price_5_pull_count - 1,
                        updated_at = ?
                    WHERE qq_id = ? AND half_price_5_pull_count > 0
                    """,
                    (self._now_iso(), qq_id),
                )
                if updated.rowcount == 0:
                    conn.execute("ROLLBACK")
                    return False
                conn.execute("COMMIT")
                return True
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def rollback_draw_claims(
        self,
        qq_id: str,
        *,
        half_price: bool = False,
        weekly: bool = False,
    ) -> None:
        """抽卡落地失败时恢复已预扣的半价次数和周保底资格。"""
        if not half_price and not weekly:
            return
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                now = self._now_iso()
                if half_price:
                    conn.execute(
                        """
                        UPDATE players
                        SET half_price_5_pull_count = half_price_5_pull_count + 1,
                            updated_at = ?
                        WHERE qq_id = ?
                        """,
                        (now, qq_id),
                    )
                if weekly:
                    conn.execute(
                        """
                        UPDATE players
                        SET weekly_5_guarantee_used = 0,
                            updated_at = ?
                        WHERE qq_id = ?
                        """,
                        (now, qq_id),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def daily_checkin(
        self,
        qq_id: str,
        *,
        min_reward: int,
        max_reward: int,
        streak_daily_step: int,
        streak_daily_max: int,
        streak_weekly_reward: int,
        streak_cycle_days: int,
        streak_cycle_reward: int,
        monthly_daily_bonus: int,
        non_gacha_card_ids: Iterable[tuple[int, str]] = (),
        non_gacha_checkin_probability: float = 0.0,
        tz_offset_hours: int,
    ) -> CheckinReceipt:
        """执行每日签到，重复日期不会重复发放。"""
        if min_reward < 0 or max_reward < min_reward:
            raise ValueError("签到奖励区间配置非法")
        if (
            streak_daily_step < 0
            or streak_daily_max < 0
            or streak_weekly_reward < 0
            or streak_cycle_days <= 0
            or streak_cycle_reward < 0
            or monthly_daily_bonus < 0
        ):
            raise ValueError("连续签到、卡池周期或月卡奖励配置非法")
        if not 0.0 <= non_gacha_checkin_probability <= 1.0:
            raise ValueError("非抽卡签到掉落概率必须在 0 到 1 之间")
        today = self._date_str(tz_offset_hours)

        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                player_row = self._ensure_player(conn, qq_id)
                existing = conn.execute(
                    "SELECT 1 FROM checkins WHERE qq_id = ? AND checkin_date = ?",
                    (qq_id, today),
                ).fetchone()
                if existing is not None:
                    conn.execute("ROLLBACK")
                    player = self._player_state(conn, qq_id)
                    return CheckinReceipt(
                        success=False,
                        reward=0,
                        points=player.points,
                        date=today,
                        error="今天已签到",
                    )

                if self._is_previous_date(
                    str(player_row["last_checkin_date"] or ""),
                    today,
                ):
                    streak_days = int(player_row["streak_days"] or 0) + 1
                else:
                    streak_days = 1
                streak_extra = min(
                    max(streak_days - 1, 0) * streak_daily_step,
                    streak_daily_max,
                )
                weekly_reward = streak_weekly_reward if streak_days % 7 == 0 else 0
                cycle_reward = (
                    streak_cycle_reward
                    if streak_days % streak_cycle_days == 0
                    else 0
                )
                expires_at = str(player_row["monthly_card_expires_at"] or "")
                monthly_active = self.monthly_card_remaining_days(expires_at, today) > 0
                monthly_reward = monthly_daily_bonus if monthly_active else 0
                reward = self._random.randint(min_reward, max_reward)
                bonus = 0
                bonus_kind = ""
                roll = self._random.random()
                if roll < CHECKIN_JACKPOT_PROBABILITY:
                    bonus = CHECKIN_JACKPOT_BONUS
                    bonus_kind = "jackpot"
                elif roll < CHECKIN_JACKPOT_PROBABILITY + CHECKIN_LUCKY_PROBABILITY:
                    bonus = CHECKIN_LUCKY_BONUS
                    bonus_kind = "lucky"
                total_reward = (
                    reward
                    + streak_extra
                    + weekly_reward
                    + cycle_reward
                    + monthly_reward
                    + bonus
                )
                now = self._now_iso()
                conn.execute(
                    """
                    UPDATE players
                    SET points = points + ?,
                        last_checkin_date = ?,
                        streak_days = ?,
                        total_checkins = total_checkins + 1,
                        updated_at = ?
                    WHERE qq_id = ?
                    """,
                    (total_reward, today, streak_days, now, qq_id),
                )
                conn.execute(
                    "INSERT INTO checkins(qq_id, checkin_date, reward, created_at) VALUES(?, ?, ?, ?)",
                    (qq_id, today, total_reward, now),
                )
                duplicate_reward = 0
                non_gacha_card_id: int | None = None
                non_gacha_is_new = False
                non_gacha_copies = 0
                non_gacha_is_kaika = False
                non_gacha_is_cho_kaika = False
                non_gacha_pool = [(cid,rarity) for cid,rarity in (non_gacha_card_ids or ()) if cid not in STARTER_CARD_IDS]
                if (
                    non_gacha_pool
                    and non_gacha_checkin_probability > 0
                    and self._random.random() < non_gacha_checkin_probability
                ):
                    chosen_card_id, chosen_rarity = self._random.choice(non_gacha_pool)
                    granted = self._grant_card(conn, qq_id, chosen_card_id, chosen_rarity, now, source="checkin")
                    non_gacha_is_new = granted.is_new
                    non_gacha_copies = granted.copies
                    non_gacha_is_kaika = granted.is_kaika
                    non_gacha_is_cho_kaika = granted.is_cho_kaika
                    duplicate_reward = granted.fragments
                    non_gacha_card_id = chosen_card_id
                small_gifts, medium_gifts, growth_fragments, large_gifts = self._award_growth_daily(
                    conn, qq_id, today, "checkin", today, now)
                conn.execute("COMMIT")
                player = self._player_state(conn, qq_id)
                return CheckinReceipt(
                    success=True,
                    reward=reward,
                    points=player.points,
                    date=today,
                    bonus=bonus,
                    bonus_kind=bonus_kind,
                    streak_days=streak_days,
                    streak_extra=streak_extra,
                    weekly_reward=weekly_reward,
                    cycle_reward=cycle_reward,
                    monthly_reward=monthly_reward,
                    non_gacha_card_id=non_gacha_card_id,
                    non_gacha_is_new=non_gacha_is_new,
                    non_gacha_copies=non_gacha_copies,
                    non_gacha_is_kaika=non_gacha_is_kaika,
                    non_gacha_is_cho_kaika=non_gacha_is_cho_kaika,
                    small_gifts=small_gifts,
                    medium_gifts=medium_gifts,
                    large_gifts=large_gifts,
                    growth_fragments=growth_fragments + duplicate_reward,
                )
            except Exception:
                conn.execute("ROLLBACK")
                raise

    @staticmethod
    def _upsert_pool_select_state(
        conn: sqlite3.Connection,
        qq_id: str,
        pool_id: str,
        max_select_points: int,
        now: str,
    ) -> sqlite3.Row:
        """创建或刷新用户的卡池天井状态行。"""
        conn.execute(
            """
            INSERT INTO pool_select_state(
                qq_id, pool_id, select_points, max_select_points,
                select_claimed, updated_at
            ) VALUES(?, ?, 0, ?, 0, ?)
            ON CONFLICT(qq_id, pool_id) DO UPDATE SET
                max_select_points = MAX(
                    max_select_points,
                    excluded.max_select_points
                ),
                updated_at = excluded.updated_at
            """,
            (qq_id, pool_id, int(max_select_points), now),
        )
        row = conn.execute(
            """
            SELECT select_points, max_select_points, select_claimed
            FROM pool_select_state
            WHERE qq_id = ? AND pool_id = ?
            """,
            (qq_id, pool_id),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"天井状态创建失败: {pool_id}")
        return row

    def get_pool_select_state(
        self,
        qq_id: str,
        pool_id: str,
    ) -> PoolSelectState:
        """返回用户当前卡池的天井状态。"""
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            self._ensure_player(conn, qq_id)
            row = conn.execute(
                """
                SELECT select_points, max_select_points, select_claimed
                FROM pool_select_state
                WHERE qq_id = ? AND pool_id = ?
                """,
                (qq_id, pool_id),
            ).fetchone()
            if row is None:
                return PoolSelectState(
                    pool_id=pool_id,
                    select_points=0,
                    max_select_points=0,
                    is_claimed=False,
                )
            return PoolSelectState(
                pool_id=pool_id,
                select_points=int(row["select_points"]),
                max_select_points=int(row["max_select_points"]),
                is_claimed=bool(row["select_claimed"]),
            )

    def claim_select_card(
        self,
        qq_id: str,
        pool_id: str,
        card_id: int,
        rarity: str,
        *,
        max_select_points: int,
    ) -> SelectClaimReceipt:
        """消耗满额天井点数并发放用户选择的卡。"""
        if max_select_points <= 0:
            return SelectClaimReceipt(
                success=False,
                points=0,
                card_id=card_id,
                copies=0,
                is_new=False,
                error="当前卡池没有天井机制",
            )
        now = self._now_iso()

        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                player = self._ensure_player(conn, qq_id)
                state = self._upsert_pool_select_state(
                    conn,
                    qq_id,
                    pool_id,
                    max_select_points,
                    now,
                )
                select_points = int(state["select_points"])
                if bool(state["select_claimed"]):
                    conn.execute("ROLLBACK")
                    return SelectClaimReceipt(
                        success=False,
                        points=int(player["points"]),
                        card_id=card_id,
                        copies=0,
                        is_new=False,
                        error="当前卡池已经兑换过天井卡",
                    )
                if select_points < max_select_points:
                    conn.execute("ROLLBACK")
                    return SelectClaimReceipt(
                        success=False,
                        points=int(player["points"]),
                        card_id=card_id,
                        copies=0,
                        is_new=False,
                        error=(
                            f"天井点数不足：{select_points}/"
                            f"{max_select_points}"
                        ),
                    )

                granted = self._grant_card(conn, qq_id, card_id, rarity, now, source="select_card")
                was_new = granted.is_new
                new_copies = granted.copies
                conn.execute(
                    """
                    UPDATE pool_select_state
                    SET select_points = 0,
                        select_claimed = 1,
                        updated_at = ?
                    WHERE qq_id = ? AND pool_id = ?
                    """,
                    (now, qq_id, pool_id),
                )
                conn.execute(
                    """
                    UPDATE players
                    SET total_pulls = total_pulls + 1,
                        updated_at = ?
                    WHERE qq_id = ?
                    """,
                    (now, qq_id),
                )
                result_json = json.dumps(
                    {"card_id": card_id, "rarity": rarity, "is_new": was_new},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                conn.execute(
                    """
                    INSERT INTO gacha_logs(
                        qq_id, draw_count, cost, rarity_profile,
                        result_json, created_at
                    ) VALUES(?, 1, 0, ?, ?, ?)
                    """,
                    (qq_id, rarity, result_json, now),
                )
                conn.execute("COMMIT")
                final_player = self._player_state(conn, qq_id)
                return SelectClaimReceipt(
                    success=True,
                    points=final_player.points,
                    card_id=card_id,
                    copies=new_copies,
                    is_new=was_new,
                    fragments=granted.fragments,
                    is_kaika=granted.is_kaika,
                    is_cho_kaika=granted.is_cho_kaika,
                )
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def perform_draw(self, qq_id, pool, count, *, cost, request_id, tz_offset_hours=0):
        """资格、扣款、发卡、天井和消息去重在同一事务提交。"""
        if not request_id or qq_id == 'unknown':
            raise ValueError("缺少账号或消息ID，未执行抽卡")
        if count not in (1, 5, 11) or cost <= 0:
            raise ValueError("抽卡数量或消耗无效")
        key = json.dumps(['draw', qq_id, request_id], separators=(',', ':'))
        request = json.dumps({'pool': pool.pool_id, 'count': count}, sort_keys=True)
        with self._lock:
            conn = self._conn
            if conn is None or not self._growth_ready:
                raise RuntimeError("数据库尚未初始化")
            conn.execute('BEGIN IMMEDIATE')
            try:
                old = conn.execute('SELECT request_json,result_json FROM growth_events WHERE event_key=?', (key,)).fetchone()
                if old is not None:
                    if old[0] != request:
                        raise ValueError("消息ID已用于另一笔抽卡")
                    saved = json.loads(old[1])
                    saved['receipt']['commitments'] = [DrawCommitment(**r) for r in saved['receipt']['commitments']]
                    saved['receipt'] = DrawReceipt(**saved['receipt'])
                    conn.rollback()
                    return saved
                player = self._ensure_player(conn, qq_id)
                now = self._now_iso()
                half = count == 5 and int(player['half_price_5_pull_count']) > 0
                charged = cost // 2 if half else cost
                if int(player['points']) < charged:
                    conn.rollback()
                    return {'receipt': DrawReceipt(False, int(player['points']), [], error='点数不足'),
                            'half_price_used': False, 'weekly_5_claimed': False, 'cost': charged}
                weekly = False
                if count == 5:
                    week = self._weekly_5_key(tz_offset_hours)
                    self._sync_weekly_5(conn, qq_id, week, now)
                    weekly = not conn.execute('SELECT weekly_5_guarantee_used FROM players WHERE qq_id=?', (qq_id,)).fetchone()[0]
                    if weekly:
                        conn.execute('UPDATE players SET weekly_5_guarantee_used=1 WHERE qq_id=?', (qq_id,))
                    if half:
                        conn.execute('UPDATE players SET half_price_5_pull_count=half_price_5_pull_count-1 WHERE qq_id=?', (qq_id,))
                cards = pool.draw(count, guarantee=count == 11 or weekly)
                if len(cards) != count:
                    raise ValueError("抽卡结果数量异常")
                receipt = self.commit_draw(qq_id, [(c.id, c.rarity) for c in cards], cost=charged,
                    pool_id=pool.pool_id, max_select_points=pool.pool_select_points or 0, _in_transaction=True)
                result = {'receipt': receipt, 'half_price_used': half, 'weekly_5_claimed': weekly, 'cost': charged}
                if not receipt.success:
                    conn.rollback()
                    return result
                serialized = {**result, 'receipt': asdict(receipt)}
                conn.execute('INSERT INTO growth_events VALUES(?,?,?,?,?,?,?)',
                    (key, qq_id, 'draw_request', request, json.dumps(serialized), CURVE_VERSION, now))
                conn.commit()
                return result
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise

    def commit_draw(
        self,
        qq_id: str,
        cards: Iterable[tuple[int, str]],
        *,
        cost: int,
        pool_id: str = "",
        max_select_points: int = 0,
        _in_transaction: bool = False,
    ) -> DrawReceipt:
        """扣点数、写库存与日志。"""
        drawn_cards = [(int(card_id), str(rarity)) for card_id, rarity in cards]
        if not drawn_cards:
            raise ValueError("抽卡结果不能为空")
        if cost < 0:
            raise ValueError("抽卡消耗不能为负数")
        now = self._now_iso()

        with nullcontext() if _in_transaction else self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            if _in_transaction:
                if not conn.in_transaction:
                    raise RuntimeError("抽卡外层事务未开启")
            else:
                conn.execute("BEGIN IMMEDIATE")
            try:
                player = self._ensure_player(conn, qq_id)
                updated = conn.execute(
                    """
                    UPDATE players
                    SET points = points - ?,
                        total_pulls = total_pulls + ?,
                        updated_at = ?
                    WHERE qq_id = ? AND points >= ?
                    """,
                    (cost, len(drawn_cards), now, qq_id, cost),
                )
                if updated.rowcount == 0:
                    if not _in_transaction:
                        conn.execute("ROLLBACK")
                    return DrawReceipt(
                        success=False,
                        points=int(player["points"]),
                        commitments=[],
                        error="点数不足",
                    )

                select_points = 0
                select_claimed = False
                if pool_id and max_select_points > 0:
                    state = self._upsert_pool_select_state(
                        conn,
                        qq_id,
                        pool_id,
                        max_select_points,
                        now,
                    )
                    current = int(state["select_points"])
                    select_claimed = bool(state["select_claimed"])
                    select_points = min(
                        current + len(drawn_cards),
                        max_select_points,
                    )
                    if select_points != current:
                        conn.execute(
                            """
                            UPDATE pool_select_state
                            SET select_points = ?,
                                updated_at = ?
                            WHERE qq_id = ? AND pool_id = ?
                            """,
                            (select_points, now, qq_id, pool_id),
                        )

                commitments: list[DrawCommitment] = []
                log_entries: list[dict[str, Any]] = []
                for card_id, rarity in drawn_cards:
                    granted = self._grant_card(conn, qq_id, card_id, rarity, now, source="draw")
                    was_new = granted.is_new
                    commitments.append(granted)
                    log_entries.append(
                        {
                            "card_id": card_id,
                            "rarity": rarity,
                            "is_new": was_new,
                        }
                    )

                result_json = json.dumps(log_entries, ensure_ascii=False, separators=(",", ":"))
                rarity_profile = ",".join(rarity for _, rarity in drawn_cards)
                conn.execute(
                    """
                    INSERT INTO gacha_logs(
                        qq_id, draw_count, cost, rarity_profile, result_json, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (qq_id, len(drawn_cards), cost, rarity_profile, result_json, now),
                )
                if not _in_transaction:
                    conn.execute("COMMIT")
                final_player = self._player_state(conn, qq_id)
                return DrawReceipt(
                    success=True,
                    points=final_player.points,
                    commitments=commitments,
                    select_points=select_points,
                    max_select_points=max_select_points,
                    select_ready=(
                        max_select_points > 0
                        and select_points >= max_select_points
                    ),
                    select_claimed=select_claimed,
                )
            except BaseException:
                if not _in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def get_inventory(self, qq_id: str) -> list[InventoryEntry]:
        """返回用户的全部库存。"""
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            rows = conn.execute(
                """
                SELECT *
                FROM inventory
                WHERE qq_id = ?
                ORDER BY card_id
                """,
                (qq_id,),
            ).fetchall()
            return [
                InventoryEntry(
                    card_id=int(row["card_id"]),
                    copies=int(row["copies"]),
                    is_kaika=(int(row["bloom_stage"]) >= 1) if self._growth_ready else bool(row["is_kaika"]),
                    is_cho_kaika=(int(row["bloom_stage"]) == 2) if self._growth_ready else bool(row["is_cho_kaika"]),
                    bloom_stage=int(row["bloom_stage"]) if self._growth_ready else (2 if row["is_cho_kaika"] else 1 if row["is_kaika"] else 0),
                    kaika_at=row["kaika_at"] if self._growth_ready else None,
                    cho_kaika_at=row["cho_kaika_at"] if self._growth_ready else None,
                    growth_origin=row["growth_origin"] if self._growth_ready else "legacy",
                )
                for row in rows
            ]

    # ==================== 随机任务 ====================

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            id=int(row["id"]),
            qq_id=str(row["qq_id"]),
            task_kind=str(row["task_kind"]),
            game=str(row["game"]),
            song_id=str(row["song_id"]),
            song_title=str(row["song_title"]),
            artist=str(row["artist"] or ""),
            difficulty_index=(
                int(row["difficulty_index"])
                if row["difficulty_index"] is not None
                else None
            ),
            difficulty_label=str(row["difficulty_label"] or ""),
            target_level=str(row["target_level"] or ""),
            target_level_value=float(row["target_level_value"] or 0),
            requirement_text=str(row["requirement_text"] or ""),
            reward=int(row["reward"] or 0),
            cover_url=str(row["cover_url"] or ""),
            task_date=str(row["task_date"] or ""),
            status=str(row["status"] or ""),
            created_at=str(row["created_at"] or ""),
            submitted_at=(str(row["submitted_at"]) if row["submitted_at"] else None),
            submitted_by=str(row["submitted_by"] or ""),
            reviewed_by=str(row["reviewed_by"] or ""),
            reviewed_at=(str(row["reviewed_at"]) if row["reviewed_at"] else None),
            reviewed_grade=str(row["reviewed_grade"] or ""),
            awarded=bool(row["awarded"]),
            note=str(row["note"] or ""),
        )

    @staticmethod
    def _consume_task_quota(
        conn: sqlite3.Connection,
        qq_id: str,
        task_kind: str,
        limit: int,
        task_date: str,
        now: str,
    ) -> bool:
        conn.execute(
            """
            INSERT OR IGNORE INTO daily_task_quota(
                qq_id, task_date, task_kind, used_count
            ) VALUES(?, ?, ?, 0)
            """,
            (qq_id, task_date, task_kind),
        )
        row = conn.execute(
            """
            SELECT used_count
            FROM daily_task_quota
            WHERE qq_id = ? AND task_date = ? AND task_kind = ?
            """,
            (qq_id, task_date, task_kind),
        ).fetchone()
        used = int(row["used_count"]) if row is not None else 0
        if used >= limit:
            return False
        conn.execute(
            """
            UPDATE daily_task_quota
            SET used_count = used_count + 1
            WHERE qq_id = ? AND task_date = ? AND task_kind = ?
            """,
            (qq_id, task_date, task_kind),
        )
        return True

    @staticmethod
    def _release_task_quota(
        conn: sqlite3.Connection,
        qq_id: str,
        task_kind: str,
        task_date: str,
        today: str,
    ) -> None:
        if task_date != today:
            return
        conn.execute(
            """
            UPDATE daily_task_quota
            SET used_count = MAX(used_count - 1, 0)
            WHERE qq_id = ? AND task_date = ? AND task_kind = ?
            """,
            (qq_id, task_date, task_kind),
        )

    @staticmethod
    def _audit_task(
        conn: sqlite3.Connection,
        qq_id: str,
        task_id: int,
        action: str,
        actor_id: str,
        *,
        grade: str = "",
        points: int = 0,
        note: str = "",
        now: str = "",
    ) -> None:
        conn.execute(
            """
            INSERT INTO task_audit(
                qq_id, task_id, action, actor_id, grade, points, note, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (qq_id, task_id, action, actor_id, grade, points, note, now),
        )

    @staticmethod
    def _ultimate_chart_completed(conn, qq_id, game, song_id, difficulty_index):
        # -1 是旧库无法恢复难度时的整曲锁；也禁止缺失难度绕过已完成谱面。
        index = difficulty_index if difficulty_index is not None else -1
        return conn.execute(
            "SELECT 1 FROM ultimate_completed_charts WHERE qq_id=? AND game=? AND song_id=? "
            "AND (difficulty_index=-1 OR difficulty_index=? OR ?=-1) LIMIT 1",
            (qq_id, game, song_id, index, index),
        ).fetchone() is not None

    def create_task(
        self,
        qq_id: str,
        *,
        task_kind: str,
        game: str,
        song_id: str,
        song_title: str,
        artist: str = "",
        difficulty_index: int | None = None,
        difficulty_label: str = "",
        target_level: str = "",
        target_level_value: float = 0.0,
        requirement_text: str = "",
        reward: int = 0,
        cover_url: str = "",
        note: str = "",
        normal_limit: int = 5,
        challenge_limit: int = 3,
        advanced_limit: int = 1,
        tz_offset_hours: int = 0,
    ) -> TaskReceipt:
        """接取任务并记录每日配额。"""
        if task_kind not in {"normal", "challenge", "advanced", "ultimate"}:
            return TaskReceipt(success=False, error="未知任务类型")
        kind_label = {
            "normal": "普通",
            "challenge": "挑战",
            "advanced": "高级挑战",
            "ultimate": "终极",
        }.get(task_kind, task_kind)
        now = self._now_iso()
        task_date = self.current_date_str(tz_offset_hours)

        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_player(conn, qq_id)
                if task_kind in {"normal", "challenge", "advanced"}:
                    limit = {
                        "normal": normal_limit,
                        "challenge": challenge_limit,
                        "advanced": advanced_limit,
                    }[task_kind]
                    if not self._consume_task_quota(
                        conn, qq_id, task_kind, limit, task_date, now
                    ):
                        conn.execute("ROLLBACK")
                        return TaskReceipt(
                            success=False,
                            error=f"今日{kind_label}任务次数已用完",
                        )

                active_ultimate = 0
                if task_kind == "ultimate":
                    if self._ultimate_chart_completed(conn, qq_id, game, song_id, difficulty_index):
                        conn.rollback()
                        return TaskReceipt(success=False, error="该终极谱面已完成，不能重复接取")
                    progress = conn.execute(
                        "SELECT * FROM ultimate_progress WHERE qq_id = ?",
                        (qq_id,),
                    ).fetchone()
                    helper_task_id = (
                        int(progress["active_task_id"])
                        if progress is not None and progress["active_task_id"] is not None
                        else None
                    )
                    if helper_task_id is not None:
                        helper = conn.execute(
                            "SELECT status FROM tasks WHERE id = ?",
                            (helper_task_id,),
                        ).fetchone()
                        if helper is not None and str(helper["status"]) in {
                            "active",
                            "submitted",
                        }:
                            conn.execute("ROLLBACK")
                            return TaskReceipt(
                                success=False,
                                error="已有未完成/待审核的终极任务",
                            )
                    active_ultimate = 1

                cursor = conn.execute(
                    """
                    INSERT INTO tasks(
                        qq_id, task_kind, game, song_id, song_title, artist,
                        difficulty_index, difficulty_label, target_level,
                        target_level_value, requirement_text, reward, cover_url,
                        task_date, status, created_at, note
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        qq_id,
                        task_kind,
                        game,
                        song_id,
                        song_title,
                        artist,
                        difficulty_index,
                        difficulty_label,
                        target_level,
                        target_level_value,
                        requirement_text,
                        reward,
                        cover_url,
                        task_date,
                        now,
                        note,
                    ),
                )
                task_id = int(cursor.lastrowid)
                if active_ultimate:
                    conn.execute(
                        """
                        INSERT INTO ultimate_progress(
                            qq_id, stage, active_task_id, finished, completed_at
                        ) VALUES(?, 0, ?, 0, NULL)
                        ON CONFLICT(qq_id) DO UPDATE SET
                            active_task_id = excluded.active_task_id,
                            finished = 0,
                            completed_at = NULL
                        """,
                        (qq_id, task_id),
                    )
                self._audit_task(
                    conn,
                    qq_id,
                    task_id,
                    "accept",
                    qq_id,
                    note=note,
                    now=now,
                )
                conn.execute("COMMIT")
                row = conn.execute(
                    "SELECT * FROM tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
                return TaskReceipt(
                    success=True,
                    task_id=task_id,
                    task=self._task_from_row(row),
                )
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def get_task(self, task_id: int) -> TaskRecord | None:
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            return self._task_from_row(row) if row is not None else None

    def list_tasks(
        self,
        qq_id: str,
        *,
        statuses: tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> list[TaskRecord]:
        if statuses is None:
            statuses = ("active", "submitted", "approved", "rejected", "reset", "expired")
        placeholders = ",".join("?" for _ in statuses)
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            rows = self._conn.execute(
                f"""
                SELECT * FROM tasks
                WHERE qq_id = ? AND status IN ({placeholders})
                ORDER BY id DESC
                LIMIT ?
                """,
                (qq_id, *statuses, limit),
            ).fetchall()
            return [self._task_from_row(row) for row in rows]

    def list_pending_tasks(self, limit: int = 100) -> list[TaskRecord]:
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            rows = self._conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'submitted'
                ORDER BY submitted_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [self._task_from_row(row) for row in rows]

    def submit_task(self, task_id: int, qq_id: str, *, note: str = "") -> TaskReceipt:
        now = self._now_iso()
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return TaskReceipt(success=False, error="任务不存在")
                if str(row["qq_id"]) != qq_id:
                    conn.execute("ROLLBACK")
                    return TaskReceipt(success=False, error="不能提交他人的任务")
                if str(row["status"]) not in {"active", "rejected"}:
                    conn.execute("ROLLBACK")
                    return TaskReceipt(
                        success=False,
                        error="该任务当前不可提交",
                    )
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'submitted',
                        submitted_at = ?,
                        submitted_by = ?,
                        note = ?
                    WHERE id = ?
                    """,
                    (now, qq_id, note, task_id),
                )
                self._audit_task(
                    conn,
                    qq_id,
                    task_id,
                    "submit",
                    qq_id,
                    note=note,
                    now=now,
                )
                conn.execute("COMMIT")
                row = conn.execute(
                    "SELECT * FROM tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
                return TaskReceipt(
                    success=True,
                    task_id=task_id,
                    task=self._task_from_row(row),
                )
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def approve_task(
        self,
        task_id: int,
        admin_id: str,
        *,
        grade: str,
        reward: int,
    ) -> TaskReviewReceipt:
        now = self._now_iso()
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return TaskReviewReceipt(success=False, error="任务不存在")
                if str(row["task_kind"]) == "ultimate":
                    conn.execute("ROLLBACK")
                    return TaskReviewReceipt(
                        success=False,
                        error="终极任务请使用 /终极完成",
                    )
                if str(row["status"]) != "submitted":
                    conn.execute("ROLLBACK")
                    return TaskReviewReceipt(
                        success=False,
                        error="任务尚未提交成绩（图片 + /任务完成 <ID>）",
                    )
                if int(row["awarded"] or 0):
                    conn.execute("ROLLBACK")
                    return TaskReviewReceipt(success=False, error="任务奖励已发放")
                qq_id = str(row["qq_id"])
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'approved',
                        reviewed_by = ?,
                        reviewed_at = ?,
                        reviewed_grade = ?,
                        awarded = 1
                    WHERE id = ?
                    """,
                    (admin_id, now, grade, task_id),
                )
                conn.execute(
                    """
                    UPDATE players
                    SET points = points + ?,
                        updated_at = ?
                    WHERE qq_id = ?
                    """,
                    (reward, now, qq_id),
                )
                self._audit_task(
                    conn,
                    qq_id,
                    task_id,
                    "approve",
                    admin_id,
                    grade=grade,
                    points=reward,
                    now=now,
                )
                player = conn.execute(
                    "SELECT points FROM players WHERE qq_id = ?",
                    (qq_id,),
                ).fetchone()
                _, medium_gifts, growth_fragments, large_gifts = self._award_growth_daily(
                    conn, qq_id, self.current_date_str(0), str(row["task_kind"]), str(task_id), now)
                bloom_tickets = 0
                cooldown_text = ""
                ticket_source = (self._growth_rules or {}).get("bloom_ticket_source") or {}
                if self._growth_ready and self._growth_enabled and ticket_source:
                    required_grade = str(ticket_source.get("grade") or "SSS").upper().replace("＋", "+")
                    actual_grade = str(grade or "").upper().replace("＋", "+")
                    grade_ok = actual_grade == required_grade or (
                        required_grade == "SSS" and actual_grade == "SSS+"
                    )
                    target_level = float(row["target_level_value"] or 0)
                    if (
                        str(row["task_kind"]) == str(ticket_source.get("kind") or "advanced")
                        and target_level >= float(ticket_source.get("min_level") or 13.5)
                        and grade_ok
                    ):
                        cooldown_days = max(int(ticket_source.get("cooldown_days") or 0), 0)
                        last = conn.execute(
                            "SELECT last_at FROM player_cooldowns WHERE qq_id=? AND key='bloom_ticket'",
                            (qq_id,),
                        ).fetchone()
                        allowed = True
                        remaining_days = 0
                        if last is not None:
                            try:
                                last_dt = datetime.fromisoformat(str(last["last_at"]))
                                now_dt = datetime.fromisoformat(now)
                                elapsed = (now_dt - last_dt).total_seconds()
                                window = cooldown_days * 86400
                                if elapsed < window:
                                    allowed = False
                                    remaining_days = max(1, -(-int(window - elapsed) // 86400))
                            except ValueError:
                                allowed = True
                        if allowed:
                            change_item(conn, qq_id, "bloom_ticket", 1)
                            conn.execute(
                                """INSERT INTO player_cooldowns(qq_id, key, last_at)
                                   VALUES(?, 'bloom_ticket', ?)
                                   ON CONFLICT(qq_id, key) DO UPDATE SET last_at=excluded.last_at""",
                                (qq_id, now),
                            )
                            bloom_tickets = 1
                        else:
                            cooldown_text = f"解花券冷却中（剩余{remaining_days}天）"
                conn.execute("COMMIT")
                return TaskReviewReceipt(
                    success=True,
                    medium_gifts=medium_gifts,
                    large_gifts=large_gifts,
                    growth_fragments=growth_fragments,
                    points=int(player["points"]),
                    grade=grade,
                    bloom_tickets=bloom_tickets,
                    cooldown_text=cooldown_text,
                )
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def reject_task(
        self,
        task_id: int,
        admin_id: str,
        *,
        note: str = "",
    ) -> TaskReviewReceipt:
        now = self._now_iso()
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return TaskReviewReceipt(success=False, error="任务不存在")
                if str(row["status"]) != "submitted":
                    conn.execute("ROLLBACK")
                    return TaskReviewReceipt(
                        success=False,
                        error="任务尚未提交成绩（图片 + /任务完成 <ID>）",
                    )
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'rejected',
                        reviewed_by = ?,
                        reviewed_at = ?,
                        reviewed_grade = 'reject',
                        note = ?
                    WHERE id = ?
                    """,
                    (admin_id, now, note, task_id),
                )
                self._audit_task(
                    conn,
                    str(row["qq_id"]),
                    task_id,
                    "reject",
                    admin_id,
                    note=note,
                    now=now,
                )
                conn.execute("COMMIT")
                return TaskReviewReceipt(success=True)
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def complete_ultimate(
        self,
        task_id: int,
        admin_id: str,
        *,
        reward: int,
        ultimate_total: int = 0,
    ) -> TaskReviewReceipt:
        now = self._now_iso()
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return TaskReviewReceipt(success=False, error="任务不存在")
                if str(row["task_kind"]) != "ultimate":
                    conn.execute("ROLLBACK")
                    return TaskReviewReceipt(success=False, error="该任务不是终极任务")
                if str(row["status"]) != "submitted":
                    conn.execute("ROLLBACK")
                    return TaskReviewReceipt(
                        success=False,
                        error="任务尚未提交成绩（图片 + /任务完成 <ID>）",
                    )
                if int(row["awarded"] or 0):
                    conn.execute("ROLLBACK")
                    return TaskReviewReceipt(success=False, error="任务奖励已发放")
                qq_id = str(row["qq_id"])
                game = str(row["game"])
                song_id = str(row["song_id"])
                difficulty_index = (
                    int(row["difficulty_index"])
                    if row["difficulty_index"] is not None
                    else -1
                )
                if self._ultimate_chart_completed(conn, qq_id, game, song_id, difficulty_index):
                    conn.rollback()
                    return TaskReviewReceipt(success=False, error="该终极谱面已领取奖励，不能重复结算")
                conn.execute(
                    """
                    INSERT OR IGNORE INTO ultimate_completed_charts(
                        qq_id, game, song_id, difficulty_index, completed_at
                    ) VALUES(?, ?, ?, ?, ?)
                    """,
                    (qq_id, game, song_id, difficulty_index, now),
                )
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'approved',
                        reviewed_by = ?,
                        reviewed_at = ?,
                        reviewed_grade = 'SSS+',
                        awarded = 1
                    WHERE id = ?
                    """,
                    (admin_id, now, task_id),
                )
                conn.execute(
                    """
                    UPDATE players
                    SET points = points + ?,
                        updated_at = ?
                    WHERE qq_id = ?
                    """,
                    (reward, now, qq_id),
                )
                progress = conn.execute(
                    "SELECT * FROM ultimate_progress WHERE qq_id = ?",
                    (qq_id,),
                ).fetchone()
                stage = int(progress["stage"] or 0) if progress is not None else 0
                next_stage = stage + 1
                finished = int(bool(ultimate_total) and next_stage >= ultimate_total)
                conn.execute(
                    """
                    INSERT INTO ultimate_progress(
                        qq_id, stage, active_task_id, finished, completed_at
                    ) VALUES(?, ?, NULL, ?, ?)
                    ON CONFLICT(qq_id) DO UPDATE SET
                        stage = excluded.stage,
                        active_task_id = NULL,
                        finished = excluded.finished,
                        completed_at = excluded.completed_at
                    """,
                    (qq_id, next_stage, finished, now if finished else None),
                )
                self._audit_task(
                    conn,
                    qq_id,
                    task_id,
                    "ultimate_complete",
                    admin_id,
                    grade="SSS+",
                    points=reward,
                    now=now,
                )
                player = conn.execute(
                    "SELECT points FROM players WHERE qq_id = ?",
                    (qq_id,),
                ).fetchone()
                _, medium_gifts, growth_fragments, large_gifts = self._award_growth_daily(
                    conn, qq_id, self.current_date_str(0), "ultimate", str(task_id), now)
                conn.execute("COMMIT")
                return TaskReviewReceipt(
                    success=True,
                    medium_gifts=medium_gifts,
                    large_gifts=large_gifts,
                    growth_fragments=growth_fragments,
                    points=int(player["points"]),
                    grade="SSS+",
                )
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def reset_task(
        self,
        task_id: int,
        admin_id: str,
        *,
        note: str = "",
        today: str = "",
    ) -> TaskResetReceipt:
        now = self._now_iso()
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return TaskResetReceipt(success=False, error="任务不存在")
                status = str(row["status"])
                if status not in {"active", "submitted", "rejected"}:
                    conn.execute("ROLLBACK")
                    return TaskResetReceipt(
                        success=False,
                        error=(
                            "只有未完成/待审核任务可重置"
                            ""
                        ),
                    )
                qq_id = str(row["qq_id"])
                task_kind = str(row["task_kind"])
                if task_kind in {"normal", "challenge", "advanced"}:
                    self._release_task_quota(
                        conn,
                        qq_id,
                        task_kind,
                        str(row["task_date"] or ""),
                        today,
                    )
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'reset',
                        reviewed_by = ?,
                        reviewed_at = ?,
                        reviewed_grade = 'reset',
                        note = ?
                    WHERE id = ?
                    """,
                    (admin_id, now, note, task_id),
                )
                if task_kind == "ultimate":
                    conn.execute(
                        """
                        UPDATE ultimate_progress
                        SET active_task_id = NULL
                        WHERE qq_id = ?
                        """,
                        (qq_id,),
                    )
                self._audit_task(
                    conn,
                    qq_id,
                    task_id,
                    "reset",
                    admin_id,
                    note=note,
                    now=now,
                )
                conn.execute("COMMIT")
                return TaskResetReceipt(success=True)
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def get_ultimate_progress(self, qq_id: str) -> UltimateProgress:
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            row = self._conn.execute(
                "SELECT * FROM ultimate_progress WHERE qq_id = ?",
                (qq_id,),
            ).fetchone()
            if row is None:
                return UltimateProgress(
                    stage=0,
                    active_task_id=None,
                    finished=False,
                    completed_at=None,
                )
            return UltimateProgress(
                stage=int(row["stage"] or 0),
                active_task_id=(
                    int(row["active_task_id"])
                    if row["active_task_id"] is not None
                    else None
                ),
                finished=bool(row["finished"]),
                completed_at=str(row["completed_at"]) if row["completed_at"] else None,
            )

    def get_ultimate_completed_keys(self, qq_id: str) -> set[str]:
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            rows = self._conn.execute(
                """
                SELECT game, song_id, difficulty_index
                FROM ultimate_completed_charts
                WHERE qq_id = ?
                """,
                (qq_id,),
            ).fetchall()
            keys: set[str] = set()
            for row in rows:
                song_key = f"{row['game']}:{row['song_id']}"
                index = int(row["difficulty_index"])
                keys.add(song_key if index < 0 else f"{song_key}:{index}")
            return keys

    def set_ultimate_finished(self, qq_id: str, finished: bool) -> None:
        """候选池耗尽时标记整条终极线完成；重新有候选后接取会再次置零。"""
        now = self._now_iso()
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            self._conn.execute(
                """
                INSERT INTO ultimate_progress(
                    qq_id, stage, active_task_id, finished, completed_at
                ) VALUES(?, 0, NULL, ?, ?)
                ON CONFLICT(qq_id) DO UPDATE SET
                    finished = excluded.finished,
                    completed_at = excluded.completed_at
                """,
                (qq_id, int(finished), now if finished else None),
            )

    def get_task_quota(
        self,
        qq_id: str,
        *,
        task_kind: str,
        task_date: str,
    ) -> int:
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            row = self._conn.execute(
                """
                SELECT used_count
                FROM daily_task_quota
                WHERE qq_id = ? AND task_date = ? AND task_kind = ?
                """,
                (qq_id, task_date, task_kind),
            ).fetchone()
            return int(row["used_count"]) if row is not None else 0

    def expire_daily_tasks(self, today: str) -> int:
        """自动过期指定日期前仍未完成的普通/挑战任务。

        已提交待审核的任务不处理，避免管理员来不及审核。
        """
        now = self._now_iso()
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            cursor = self._conn.execute(
                """
                UPDATE tasks
                SET status = 'expired',
                    note = CASE
                        WHEN note = '' THEN '每日任务自动过期'
                        ELSE note || ' | 每日任务自动过期'
                    END
                WHERE task_kind IN ('normal', 'challenge', 'advanced')
                  AND task_date < ?
                  AND status IN ('active', 'rejected')
                """,
                (today,),
            )
            return cursor.rowcount

    def cleanup_task_history(
        self,
        today: str,
        *,
        retention_days: int = 30,
    ) -> int:
        """清理已结束任务，保留最近 retention_days 天和全部待审核任务。"""
        retention = max(int(retention_days or 0), 0)
        cutoff = (
            date.fromisoformat(today) - timedelta(days=retention)
        ).isoformat()
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            cursor = self._conn.execute(
                """
                DELETE FROM tasks
                WHERE status IN ('approved', 'rejected', 'reset', 'expired')
                  AND COALESCE(reviewed_at, created_at) < ?
                """,
                (cutoff,),
            )
            return cursor.rowcount

    def cleanup_daily_task_quota(
        self,
        today: str,
        *,
        retention_days: int = 30,
    ) -> int:
        """清理 retention_days 天以前的每日任务配额记录。"""
        retention = max(int(retention_days or 0), 0)
        cutoff = (
            date.fromisoformat(today) - timedelta(days=retention)
        ).isoformat()
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            cursor = self._conn.execute(
                "DELETE FROM daily_task_quota WHERE task_date < ?",
                (cutoff,),
            )
            return cursor.rowcount

    def sync_time_based_state(
        self,
        *,
        tz_offset_hours: int = 0,
        savings_bonus_reset_days: int = 60,
    ) -> tuple[int, int]:
        """按真实日期同步所有用户的周保底与囤点周期。"""
        today = self.current_date_str(tz_offset_hours)
        week_key = self._weekly_5_key(tz_offset_hours)
        now = self._now_iso()
        reset_offset = f"+{max(int(savings_bonus_reset_days or 60), 1)} days"
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            weekly = self._conn.execute(
                """
                UPDATE players
                SET weekly_5_guarantee_week = ?,
                    weekly_5_guarantee_used = 0,
                    updated_at = ?
                WHERE weekly_5_guarantee_week <> ?
                """,
                (week_key, now, week_key),
            ).rowcount
            savings = self._conn.execute(
                """
                UPDATE players
                SET savings_bonus_level = 0,
                    savings_bonus_start_date = ?,
                    updated_at = ?
                WHERE savings_bonus_start_date <> ''
                  AND date(savings_bonus_start_date, ?) <= date(?)
                """,
                (today, now, reset_offset, today),
            ).rowcount
            return weekly, savings

    def get_setting(self, key: str) -> str:
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            row = self._conn.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (key,),
            ).fetchone()
            return str(row["value"]) if row is not None else ""

    def set_setting(self, key: str, value: str) -> None:
        now = self._now_iso()
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            self._conn.execute(
                """
                INSERT INTO app_settings(key, value, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, str(value or ""), now),
            )

    def add_timed_event(
        self,
        qq_id: str,
        kind: str,
        occurred_at: float,
        *,
        payload: str = "",
        event_key: str = "",
    ) -> None:
        """记录需要跨重启保留的时间型状态；event_key 非空时按 kind+key 去重。"""
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            if event_key:
                self._conn.execute(
                    "DELETE FROM timed_events WHERE kind = ? AND event_key = ?",
                    (kind, event_key),
                )
            self._conn.execute(
                """
                INSERT INTO timed_events(qq_id, kind, event_key, occurred_at, payload)
                VALUES(?, ?, ?, ?, ?)
                """,
                (qq_id, kind, event_key, float(occurred_at), str(payload or "")),
            )

    def get_timed_event(self, kind: str, event_key: str) -> tuple[float, str] | None:
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            row = self._conn.execute(
                """
                SELECT occurred_at, payload
                FROM timed_events
                WHERE kind = ? AND event_key = ?
                """,
                (kind, event_key),
            ).fetchone()
            if row is None:
                return None
            return float(row["occurred_at"]), str(row["payload"] or "")

    def list_timed_events(
        self,
        kind: str,
        *,
        qq_id: str | None = None,
        since: float = 0.0,
    ) -> list[tuple[float, str]]:
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            if qq_id is None:
                rows = self._conn.execute(
                    """
                    SELECT occurred_at, payload FROM timed_events
                    WHERE kind = ? AND occurred_at >= ?
                    ORDER BY occurred_at ASC, id ASC
                    """,
                    (kind, float(since)),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT occurred_at, payload FROM timed_events
                    WHERE kind = ? AND qq_id = ? AND occurred_at >= ?
                    ORDER BY occurred_at ASC, id ASC
                    """,
                    (kind, qq_id, float(since)),
                ).fetchall()
            return [(float(row["occurred_at"]), str(row["payload"] or "")) for row in rows]

    def prune_timed_events(self, before: float, *, kind: str | None = None) -> int:
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            cursor = self._conn.execute(
                "DELETE FROM timed_events WHERE occurred_at < ?" + (" AND kind = ?" if kind else ""),
                (float(before), kind) if kind else (float(before),),
            )
            return cursor.rowcount

    def list_pending_card_reveals(self, qq_id: str) -> list[dict[str, int]]:
        """读取待发送事件，发送确认后再删除。"""
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            rows = self._conn.execute(
                """SELECT id, card_id, before_copies, after_copies, is_kaika, is_cho_kaika
                   FROM pending_card_reveals
                   WHERE qq_id = ?
                   ORDER BY id ASC""",
                (qq_id,),
            ).fetchall()
            return [
                {
                    "id": int(row["id"]),
                    "card_id": int(row["card_id"]),
                    "before_copies": int(row["before_copies"]),
                    "after_copies": int(row["after_copies"]),
                    "is_kaika": bool(row["is_kaika"]),
                    "is_cho_kaika": bool(row["is_cho_kaika"]),
                }
                for row in rows
            ]

    def ack_card_reveal(self, qq_id: str, event_id: int) -> None:
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            self._conn.execute('DELETE FROM pending_card_reveals WHERE qq_id=? AND id=?', (qq_id,event_id))
