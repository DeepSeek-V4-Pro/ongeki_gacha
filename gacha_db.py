"""SQLite 持久化：玩家、签到、库存与抽卡日志。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import json
import random
import sqlite3
import threading

from .gacha_core import derive_growth


@dataclass(frozen=True)
class PlayerState:
    """玩家当前状态。"""

    qq_id: str
    points: int
    total_checkins: int
    total_pulls: int
    last_checkin_date: str | None


@dataclass(frozen=True)
class InventoryEntry:
    """库存中的单张卡牌记录。"""

    card_id: int
    copies: int
    is_kaika: bool
    is_cho_kaika: bool


@dataclass(frozen=True)
class DrawCommitment:
    """一次抽卡结果对应的库存变化。"""

    card_id: int
    is_new: bool
    copies: int
    is_kaika: bool
    is_cho_kaika: bool


@dataclass(frozen=True)
class DrawReceipt:
    """扣点并写入库存后的结果。"""

    success: bool
    points: int
    commitments: list[DrawCommitment]
    error: str = ""


@dataclass(frozen=True)
class CheckinReceipt:
    """签到结果。"""

    success: bool
    reward: int
    points: int
    date: str
    error: str = ""


class GachaDatabase:
    """插件专用 SQLite 数据库。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._random = random.SystemRandom()

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
            """
        )
        self._conn = conn

    def close(self) -> None:
        """关闭数据库连接。"""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def _date_str(self, offset_hours: int) -> str:
        tz = timezone(timedelta(hours=int(offset_hours)))
        return datetime.now(tz).date().isoformat()

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
            raise RuntimeError(f"创建玩家失败: {qq_id}")
        return row

    def _player_state(self, conn: sqlite3.Connection, qq_id: str) -> PlayerState:
        row = conn.execute("SELECT * FROM players WHERE qq_id = ?", (qq_id,)).fetchone()
        if row is None:
            raise RuntimeError(f"玩家不存在: {qq_id}")
        return PlayerState(
            qq_id=str(row["qq_id"]),
            points=int(row["points"]),
            total_checkins=int(row["total_checkins"]),
            total_pulls=int(row["total_pulls"]),
            last_checkin_date=str(row["last_checkin_date"] or ""),
        )

    def get_player(self, qq_id: str) -> PlayerState:
        """获取或初始化玩家。"""
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

    def daily_checkin(
        self,
        qq_id: str,
        *,
        min_reward: int,
        max_reward: int,
        tz_offset_hours: int,
    ) -> CheckinReceipt:
        """执行每日签到，重复日期不会重复发放。"""
        if min_reward < 0 or max_reward < min_reward:
            raise ValueError("签到奖励区间配置非法")
        today = self._date_str(tz_offset_hours)

        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_player(conn, qq_id)
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

                reward = self._random.randint(min_reward, max_reward)
                now = self._now_iso()
                conn.execute(
                    """
                    UPDATE players
                    SET points = points + ?,
                        last_checkin_date = ?,
                        total_checkins = total_checkins + 1,
                        updated_at = ?
                    WHERE qq_id = ?
                    """,
                    (reward, today, now, qq_id),
                )
                conn.execute(
                    "INSERT INTO checkins(qq_id, checkin_date, reward, created_at) VALUES(?, ?, ?, ?)",
                    (qq_id, today, reward, now),
                )
                conn.execute("COMMIT")
                player = self._player_state(conn, qq_id)
                return CheckinReceipt(
                    success=True,
                    reward=reward,
                    points=player.points,
                    date=today,
                )
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def commit_draw(
        self,
        qq_id: str,
        cards: Iterable[tuple[int, str]],
        *,
        cost: int,
    ) -> DrawReceipt:
        """扣点数、写库存与日志。"""
        drawn_cards = [(int(card_id), str(rarity)) for card_id, rarity in cards]
        if not drawn_cards:
            raise ValueError("抽卡结果不能为空")
        if cost < 0:
            raise ValueError("抽卡消耗不能为负数")
        now = self._now_iso()

        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
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
                    conn.execute("ROLLBACK")
                    return DrawReceipt(
                        success=False,
                        points=int(player["points"]),
                        commitments=[],
                        error="点数不足",
                    )

                commitments: list[DrawCommitment] = []
                log_entries: list[dict[str, Any]] = []
                for card_id, rarity in drawn_cards:
                    existing = conn.execute(
                        "SELECT copies FROM inventory WHERE qq_id = ? AND card_id = ?",
                        (qq_id, card_id),
                    ).fetchone()
                    was_new = existing is None
                    previous_copies = int(existing["copies"]) if existing is not None else 0
                    new_copies = previous_copies + 1
                    _, is_kaika, is_cho_kaika = derive_growth(rarity, new_copies)
                    conn.execute(
                        """
                        INSERT INTO inventory(
                            qq_id, card_id, copies, is_kaika, is_cho_kaika,
                            first_obtained_at, last_obtained_at
                        ) VALUES(?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(qq_id, card_id) DO UPDATE SET
                            copies = excluded.copies,
                            is_kaika = excluded.is_kaika,
                            is_cho_kaika = excluded.is_cho_kaika,
                            last_obtained_at = excluded.last_obtained_at
                        """,
                        (
                            qq_id,
                            card_id,
                            new_copies,
                            int(is_kaika),
                            int(is_cho_kaika),
                            now,
                            now,
                        ),
                    )
                    commitments.append(
                        DrawCommitment(
                            card_id=card_id,
                            is_new=was_new,
                            copies=new_copies,
                            is_kaika=is_kaika,
                            is_cho_kaika=is_cho_kaika,
                        )
                    )
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
                conn.execute("COMMIT")
                final_player = self._player_state(conn, qq_id)
                return DrawReceipt(
                    success=True,
                    points=final_player.points,
                    commitments=commitments,
                )
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def get_inventory(self, qq_id: str) -> list[InventoryEntry]:
        """返回玩家的全部库存。"""
        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            rows = conn.execute(
                """
                SELECT card_id, copies, is_kaika, is_cho_kaika
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
                    is_kaika=bool(row["is_kaika"]),
                    is_cho_kaika=bool(row["is_cho_kaika"]),
                )
                for row in rows
            ]
