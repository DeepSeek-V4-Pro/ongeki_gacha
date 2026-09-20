import sqlite3
import tempfile
from pathlib import Path
import unittest

from ..gacha_db import GachaDatabase
from ..growth_migration import migrate, preview_copy


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "legacy.db"
        db = GachaDatabase(self.path)
        db.open()
        db.close()
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("INSERT INTO players(qq_id,created_at,updated_at) VALUES('u','old','old')")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def add(self, cid, copies, kaika=0, cho=0):
        self.conn.execute("INSERT INTO inventory VALUES('u',?,?,?,?, 'old','old')", (cid, copies, kaika, cho))

    def test_examples_and_idempotency(self):
        counts = (5, 6, 7, 10, 11, 12, 13, 15, 5)
        rarities = {i + 1: "SSR" if i < 4 or i == 8 else "N" for i in range(9)}
        for cid, copies in enumerate(counts, 1):
            self.add(cid, copies, cho=int(cid == 9))
        report = migrate(self.conn, rarities)
        self.assertEqual(report["compensation"], 52)
        self.assertEqual([r[0] for r in self.conn.execute("SELECT bloom_stage FROM inventory ORDER BY card_id")],
                         [0, 1, 2, 2, 0, 1, 2, 2, 2])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM inventory WHERE kaika_at IS NOT NULL OR cho_kaika_at IS NOT NULL").fetchone()[0], 0)
        self.assertEqual(migrate(self.conn, rarities)["compensation"], 0)
        self.assertEqual(self.conn.execute("SELECT quantity FROM player_items").fetchone()[0], 52)
        self.assertEqual(self.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_unknown_resolves_from_original_snapshot(self):
        self.add(99, 10, cho=1)
        self.assertEqual(len(migrate(self.conn, {})["unresolved"]), 1)
        self.conn.execute("UPDATE inventory SET copies=20 WHERE card_id=99")
        self.assertEqual(migrate(self.conn, {99: "SSR"})["compensation"], 48)
        self.assertEqual(migrate(self.conn, {99: "SSR"})["compensation"], 0)

    def test_old_curve_points_scale_to_preserve_level(self):
        migrate(self.conn, {})
        self.conn.execute(
            """INSERT INTO player_characters(
                   qq_id,character_id,affection_points,curve_version,created_at,updated_at
               ) VALUES('u',1000,16500,'affection-v1','old','old')"""
        )
        self.conn.execute("DELETE FROM schema_migrations WHERE migration_id='affection-curve-v2'")
        migrate(self.conn, {})
        self.assertEqual(
            self.conn.execute(
                "SELECT affection_points,curve_version FROM player_characters WHERE qq_id='u' AND character_id=1000"
            ).fetchone(),
            (165000, "affection-v2"),
        )
        migrate(self.conn, {})
        self.assertEqual(
            self.conn.execute(
                "SELECT affection_points FROM player_characters WHERE qq_id='u' AND character_id=1000"
            ).fetchone()[0],
            165000,
        )

    def test_rollback_entire_schema_on_invalid_inventory(self):
        self.add(1, 10)
        self.add(2, -1)
        with self.assertRaises(ValueError):
            migrate(self.conn, {1: "SSR", 2: "SSR"})
        self.assertNotIn("bloom_stage", {r[1] for r in self.conn.execute("PRAGMA table_info(inventory)")})
        self.assertIsNone(self.conn.execute("SELECT name FROM sqlite_master WHERE name='player_items'").fetchone())

    def test_copy_reads_wal_and_preserves_original(self):
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.add(1, 10)
        target = Path(self.temp.name) / "preview.db"
        self.assertEqual(preview_copy(self.path, target, {1: "SSR"})["compensation"], 48)
        self.assertNotIn("bloom_stage", {r[1] for r in self.conn.execute("PRAGMA table_info(inventory)")})
        with self.assertRaises(ValueError):
            preview_copy(self.path, target, {1: "SSR"})


if __name__ == "__main__":
    unittest.main()
