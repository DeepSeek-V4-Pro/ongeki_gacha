"""数值推演与运行签到、任务发奖一致，兑换不会凭空创造礼物。"""
from pathlib import Path
import unittest

from . import test_growth_service as fixtures
from ..tools.affection_balance_report import monthly_rewards, task_gifts, simulate


class AffectionBalanceReportTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.GrowthServiceTests.setUpClass.__func__)
    setUp = fixtures.GrowthServiceTests.setUp
    tearDown = fixtures.GrowthServiceTests.tearDown

    def test_simulated_daily_rewards_match_committed_database(self):
        self.db.get_player('u')
        counts = {'normal': 3, 'challenge': 2, 'advanced': 1}
        wanted = task_gifts(self.catalog.rules, counts)
        conn = self.db._conn
        conn.execute('BEGIN IMMEDIATE')
        totals = [0,0,0,0,0]
        for kind, count in counts.items():
            for index in range(count):
                reward = self.db._award_growth_daily(conn, 'u', '2026-10-13', kind, f'{kind}-{index}', 'now')
                totals = [left+right for left,right in zip(totals, reward)]
        conn.commit()
        self.assertEqual((wanted['small'], wanted['medium']), (totals[0], totals[1]))
        self.assertEqual((totals[0], totals[1], totals[2], totals[3]), (3,3,1,0))
        for day in range(1, 32):
            gifts, fragments = monthly_rewards(self.catalog.rules, day)
            conn.execute('BEGIN IMMEDIATE')
            reward = self.db._award_growth_daily(conn, 'u', f'2026-10-{day:02}', 'checkin', str(day), 'now')
            conn.commit()
            self.assertEqual((gifts['small'],gifts['medium'],fragments,gifts['large']), reward[:4])

    def test_thirty_day_totals_and_exchange_inventory(self):
        import re
        import tomllib
        root = Path(__file__).parents[1]
        usage = (root/'USAGE.md').read_text(encoding='utf-8')
        config = next(tomllib.loads(block) for block in re.findall(r'```toml\s*\n(.*?)```', usage, re.S)
                      if '[economy]' in block and '[growth]' in block)
        # 金币与日期不影响这组免费任务/活动发放，不依赖被忽略的本地配置。
        from datetime import date
        result = simulate(config, self.catalog.rules, self.catalog.thresholds,
                          {'normal':3,'challenge':2,'advanced':1}, exchange=True,
                          horizon=30, start=date(2026,11,1))['checkpoints']['30']
        self.assertEqual(result['gifts'], {'small':94, 'medium':92, 'large':4})
        self.assertEqual(result['affection'], 169200)
        self.assertEqual(result['fragments_gained'], 34)
        self.assertEqual(result['fragments_remaining'], 10)
        self.assertEqual(result['exchanged_large_gifts'], 2)
