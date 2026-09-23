"""重启不应刷新尚未到期的持久化时间状态。"""
from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ..gacha_db import GachaDatabase


class RestartTimeStateTests(unittest.TestCase):
    def test_legacy_pending_reveals_gain_bloom_columns(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'legacy.db'
            with closing(sqlite3.connect(path)) as conn:
                conn.execute('''CREATE TABLE pending_card_reveals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, qq_id TEXT NOT NULL,
                    card_id INTEGER NOT NULL, before_copies INTEGER NOT NULL DEFAULT 0,
                    after_copies INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)''')
                conn.execute("INSERT INTO pending_card_reveals(qq_id,card_id,created_at) VALUES('u',100001,'now')")
                conn.commit()
            db = GachaDatabase(path)
            try:
                db.open()
                row = db.list_pending_card_reveals('u')[0]
                self.assertFalse(row['is_kaika'])
                self.assertFalse(row['is_cho_kaika'])
            finally:
                db.close()

    def test_same_day_restart_keeps_quotas_and_expiry(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.db"
            db = GachaDatabase(path)
            db.open()
            db.get_player("u")
            db._conn.execute(
                """UPDATE players SET weekly_5_guarantee_week='2026-09-17',
                   weekly_5_guarantee_used=1, savings_bonus_level=2,
                   savings_bonus_start_date='2026-09-01',
                   monthly_card_expires_at='2026-10-01',
                   half_price_5_pull_count=3 WHERE qq_id='u'"""
            )
            db._conn.execute(
                """INSERT INTO daily_task_quota(qq_id, task_date, task_kind, used_count)
                   VALUES('u', '2026-09-23', 'normal', 2)"""
            )
            db.close()
            db = GachaDatabase(path)
            db.open()
            try:
                with patch.object(GachaDatabase, "current_date_str", return_value="2026-09-23"), \
                     patch.object(GachaDatabase, "_weekly_5_key", return_value="2026-09-17"):
                    self.assertEqual(db.sync_time_based_state(savings_bonus_reset_days=60), (0, 0))
                    self.assertEqual(db.sync_time_based_state(savings_bonus_reset_days=60), (0, 0))
                    self.assertFalse(db.weekly_5_guarantee_available("u", tz_offset_hours=0))
                row = db._conn.execute("SELECT * FROM players WHERE qq_id='u'").fetchone()
                self.assertEqual(row["savings_bonus_level"], 2)
                self.assertEqual(row["savings_bonus_start_date"], "2026-09-01")
                self.assertEqual(row["monthly_card_expires_at"], "2026-10-01")
                self.assertEqual(row["half_price_5_pull_count"], 3)
                self.assertEqual(db.get_task_quota("u", task_kind="normal", task_date="2026-09-23"), 2)
                with patch.object(GachaDatabase, "current_date_str", return_value="2026-09-24"), \
                     patch.object(GachaDatabase, "_weekly_5_key", return_value="2026-09-24"):
                    self.assertEqual(db.sync_time_based_state(savings_bonus_reset_days=60), (1, 0))
                    self.assertEqual(db.sync_time_based_state(savings_bonus_reset_days=60), (0, 0))
                    self.assertTrue(db.weekly_5_guarantee_available("u", tz_offset_hours=0))
                self.assertEqual(db.get_task_quota("u", task_kind="normal", task_date="2026-09-23"), 2)
                with patch.object(GachaDatabase, "current_date_str", return_value="2026-10-30"), \
                     patch.object(GachaDatabase, "_weekly_5_key", return_value="2026-09-24"):
                    self.assertEqual(db.sync_time_based_state(savings_bonus_reset_days=60), (0, 0))
                with patch.object(GachaDatabase, "current_date_str", return_value="2026-10-31"), \
                     patch.object(GachaDatabase, "_weekly_5_key", return_value="2026-09-24"):
                    self.assertEqual(db.sync_time_based_state(savings_bonus_reset_days=60), (0, 1))
                    self.assertEqual(db.sync_time_based_state(savings_bonus_reset_days=60), (0, 0))
                row = db._conn.execute("SELECT * FROM players WHERE qq_id='u'").fetchone()
                self.assertEqual(row["savings_bonus_level"], 0)
                self.assertEqual(row["savings_bonus_start_date"], "2026-10-31")
                self.assertEqual(row["monthly_card_expires_at"], "2026-10-01")
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
