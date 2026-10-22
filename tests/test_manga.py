import tempfile
import unittest
from pathlib import Path

from ..gacha_db import GachaDatabase


class MangaDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = GachaDatabase(Path(self.temp.name) / "test.db")
        self.db.open()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_no_repeat_then_starts_next_cycle(self):
        seen = []
        for _ in range(3):
            receipt = self.db.draw_manga("user", ("a", "b", "c"))
            self.assertEqual(receipt.cycle, 1)
            self.assertFalse(receipt.reset)
            seen.append(receipt.manga_id)
        self.assertEqual(set(seen), {"a", "b", "c"})

        reset = self.db.draw_manga("user", ("a", "b", "c"))
        self.assertTrue(reset.reset)
        self.assertEqual(reset.cycle, 2)
        self.assertEqual(reset.sent_count, 1)
        self.assertEqual(reset.remaining, 2)

    def test_each_user_has_an_independent_cycle(self):
        first = self.db.draw_manga("one", ("a", "b"))
        second = self.db.draw_manga("two", ("a", "b"))
        self.assertEqual(first.sent_count, 1)
        self.assertEqual(second.sent_count, 1)
        self.assertEqual(first.cycle, 1)
        self.assertEqual(second.cycle, 1)

    def test_failed_send_can_be_returned_to_unsent_pool(self):
        receipt = self.db.draw_manga("user", ("a", "b"))
        self.db.undo_manga_draw("user", receipt.cycle, receipt.manga_id)
        row = self.db._conn.execute(
            "SELECT COUNT(*) FROM manga_draws WHERE qq_id=?",
            ("user",),
        ).fetchone()
        self.assertEqual(row[0], 0)


if __name__ == "__main__":
    unittest.main()
