"""SQLite 持久化：玩家、签到、库存与抽卡日志。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import json
import random
import sqlite3
import threading

from .gacha_core import derive_growth

WEEKLY_RESET_DAY = 3  # 0=Monday, 3=Thursday
WEEKLY_RESET_HOUR = 7
CHECKIN_JACKPOT_PROBABILITY = 0.0000005  # 0.00005%
CHECKIN_LUCKY_PROBABILITY = 0.0000095   # 0.00095%
CHECKIN_JACKPOT_BONUS = 99999
CHECKIN_LUCKY_BONUS = 9999


@dataclass(frozen=True)
class PlayerState:
    """玩家当前状态。"""

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

    @staticmethod
    def current_date_str(offset_hours: int) -> str:
        """返回配置时区下的当前日期字符串。"""
        tz = timezone(timedelta(hours=int(offset_hours)))
        return datetime.now(tz).date().isoformat()

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
        """Return the Thursday-07:00 reset key for the current week."""
        tz = timezone(timedelta(hours=int(offset_hours)))
        now = datetime.now(tz)
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
            raise RuntimeError(f"玩家不存在: {qq_id}")
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

    def grant_points(
        self,
        grantor_id: str,
        target_id: str,
        amount: int,
        *,
        note: str = "",
    ) -> GrantReceipt:
        """管理员向指定玩家发放点数，并记录发放日志。"""
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
    ) -> tuple[int, int]:
        """Grant one-time rewards when a player crosses savings thresholds."""
        threshold_list = [int(value) for value in thresholds]
        bonus_list = [int(value) for value in bonuses]
        if len(threshold_list) != len(bonus_list):
            raise ValueError("囤点奖励门槛与奖励数量不一致")
        if any(value <= 0 for value in threshold_list) or any(value < 0 for value in bonus_list):
            raise ValueError("囤点奖励配置非法")
        if threshold_list != sorted(set(threshold_list)):
            raise ValueError("囤点奖励门槛必须递增")

        with self._lock:
            if self._conn is None:
                raise RuntimeError("数据库尚未打开")
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                player_row = self._ensure_player(conn, qq_id)
                points = int(player_row["points"] or 0)
                level = int(player_row["savings_bonus_level"] or 0)
                total_bonus = 0
                while level < len(threshold_list) and points >= threshold_list[level]:
                    total_bonus += bonus_list[level]
                    points += bonus_list[level]
                    level += 1
                if total_bonus:
                    conn.execute(
                        """
                        UPDATE players
                        SET points = points + ?,
                            savings_bonus_level = ?,
                            updated_at = ?
                        WHERE qq_id = ?
                        """,
                        (total_bonus, level, self._now_iso(), qq_id),
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
                non_gacha_card_id: int | None = None
                non_gacha_pool = list(non_gacha_card_ids or ())
                if (
                    non_gacha_pool
                    and non_gacha_checkin_probability > 0
                    and self._random.random() < non_gacha_checkin_probability
                ):
                    chosen_card_id, chosen_rarity = self._random.choice(non_gacha_pool)
                    existing = conn.execute(
                        "SELECT copies FROM inventory WHERE qq_id = ? AND card_id = ?",
                        (qq_id, chosen_card_id),
                    ).fetchone()
                    previous_copies = int(existing["copies"]) if existing is not None else 0
                    new_copies = previous_copies + 1
                    _, is_kaika, is_cho_kaika = derive_growth(
                        chosen_rarity,
                        new_copies,
                    )
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
                            chosen_card_id,
                            new_copies,
                            int(is_kaika),
                            int(is_cho_kaika),
                            now,
                            now,
                        ),
                    )
                    non_gacha_card_id = chosen_card_id
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
        pool_id: str = "",
        max_select_points: int = 0,
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
                    select_points=select_points,
                    max_select_points=max_select_points,
                    select_ready=(
                        max_select_points > 0
                        and select_points >= max_select_points
                    ),
                    select_claimed=select_claimed,
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
