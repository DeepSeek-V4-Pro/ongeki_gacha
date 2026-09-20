"""高级挑战与终极任务「曲目+难度」候选规则。"""
import tempfile
from pathlib import Path
import unittest

from ..gacha_db import GachaDatabase
from ..task_catalog import CatalogChart, CatalogSong, pick_random_task


def _song() -> CatalogSong:
    return CatalogSong(
        game="chunithm",
        song_id="s1",
        title="Test",
        artist="",
        max_level=15.0,
        max_level_display="15",
        max_level_value=15.0,
        disabled=False,
        locked=False,
        cover_url="",
        charts=(
            CatalogChart(2, "expert", "EXPERT", "13.0", 13.0),
            CatalogChart(3, "master", "MASTER", "14.8", 14.8),
            CatalogChart(4, "ultima", "ULTIMA", "15.0", 15.0),
        ),
    )


class TaskCatalogTests(unittest.TestCase):
    def test_advanced_picks_lowest_qualifying_chart(self):
        selection = pick_random_task([_song()], "advanced", advanced_min_level=12.7)
        self.assertIsNotNone(selection)
        self.assertEqual(selection.chart.index, 2)

    def test_ultimate_blocks_only_completed_difficulty(self):
        song = _song()
        selection = pick_random_task(
            [song],
            "ultimate",
            completed_keys={f"{song.key}:3"},
            ultimate_min_level=14.7,
        )
        self.assertIsNotNone(selection)
        self.assertEqual(selection.chart.index, 4)
        legacy = pick_random_task(
            [song],
            "ultimate",
            completed_keys={song.key},
            ultimate_min_level=14.7,
        )
        self.assertIsNone(legacy)

    def test_ultimate_completion_is_per_chart_and_repeatable(self):
        with tempfile.TemporaryDirectory() as temp:
            db = GachaDatabase(Path(temp) / "tasks.db")
            db.open()
            try:
                first = db.create_task(
                    "u",
                    task_kind="ultimate",
                    game="chunithm",
                    song_id="s1",
                    song_title="Test",
                    difficulty_index=3,
                    difficulty_label="master",
                    target_level="14.8",
                    target_level_value=14.8,
                )
                self.assertTrue(first.success)
                self.assertTrue(db.submit_task(first.task_id, "u").success)
                self.assertTrue(db.complete_ultimate(first.task_id, "admin", reward=30000).success)
                self.assertEqual(db.get_ultimate_completed_keys("u"), {"chunithm:s1:3"})
                duplicate = db.create_task('u', task_kind='ultimate', game='chunithm',
                                           song_id='s1', song_title='Test', difficulty_index=3)
                self.assertFalse(duplicate.success)
                second = db.create_task(
                    "u",
                    task_kind="ultimate",
                    game="chunithm",
                    song_id="s1",
                    song_title="Test",
                    difficulty_index=4,
                    difficulty_label="ultima",
                    target_level="15.0",
                    target_level_value=15.0,
                )
                self.assertTrue(second.success)
            finally:
                db.close()

    def test_completion_rechecks_chart_reward_inside_transaction(self):
        with tempfile.TemporaryDirectory() as temp:
            db = GachaDatabase(Path(temp)/'tasks.db')
            db.open()
            try:
                task=db.create_task('u',task_kind='ultimate',game='chunithm',
                                    song_id='s1',song_title='Test',difficulty_index=3)
                db.submit_task(task.task_id,'u')
                # 模拟旧数据/另一写入方已结算同谱面。
                db._conn.execute("INSERT INTO ultimate_completed_charts VALUES('u','chunithm','s1',3,'old')")
                self.assertFalse(db.complete_ultimate(task.task_id,'admin',reward=30000).success)
                self.assertEqual(db.get_player('u').points,0)
                self.assertFalse(db.get_task(task.task_id).awarded)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
