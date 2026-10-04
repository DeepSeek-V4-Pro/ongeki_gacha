"""按真实日历对照 v8/v9 的单角色养成，复用运行抽卡器估算重复卡兑换。"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import random
import statistics
import tomllib

from ..gacha_core import CardPool, load_cards
from ..gacha_pools import GachaSchedule
from ..growth_core import affection_level, duplicate_fragments


def task_gifts(rules: dict, counts: dict) -> Counter:
    result = Counter()
    for size in ('small', 'medium'):
        sources = rules.get(f'task_{size}_gift_sources', [])
        wanted = sum(counts.get(kind, 0) for kind in sources)
        result[size] = min(wanted, rules.get(f'task_{size}_gifts_daily_cap', 0))
    return result


def monthly_rewards(rules: dict, day: int) -> tuple[Counter, int]:
    gifts = Counter()
    if day > rules['monthly_event_days']:
        return gifts, 0
    for size in ('small', 'medium', 'large'):
        if day in rules[f'monthly_event_{size}_gift_days']:
            gifts[size] = 1
            break
    ticket = day in rules.get('monthly_event_bloom_ticket_days', [])
    return gifts, rules['monthly_event_fragments'] if not gifts and not ticket else 0


def previous_rules(current: dict) -> dict:
    rules = deepcopy(current)
    rules.update(version='growth-balance-v8', task_small_gifts_daily_cap=0,
                 task_small_gift_sources=[], task_medium_gifts_daily_cap=1)
    rules.pop('large_gift_fragment_price', None)
    return rules


def simulate(config: dict, rules: dict, thresholds: tuple[int, ...], counts: dict, *,
             exchange=False, reserve=0, buy=False, pool=None, horizon=6000,
             start=date(2026, 10, 1), task_weekdays=None) -> dict:
    """每天先领签到和审核礼物，再买礼物、抽卡、兑换，最后集中送给一个角色。"""
    economy, task = config['economy'], config['task']
    gifts_total = Counter()
    inventory = Counter()
    weekly_bought = Counter()
    last_week = None
    affection = fragments = fragments_gained = pulls = exchanged = 0
    points = spent_gifts = 0
    milestones, checkpoints = {}, {}
    daily_gifts = task_gifts(rules, counts)
    daily_fragments = min(rules['task_fragments_daily_cap'], sum(
        count * rules['task_fragments'][kind] for kind, count in counts.items()))
    for day in range(1, horizon + 1):
        today = start + timedelta(days=day-1)
        tasks_today = task_weekdays is None or today.weekday() in task_weekdays
        week = today.isocalendar()[:2]
        if week != last_week:
            weekly_bought.clear()
            last_week = week
        points += (economy['min_reward'] + economy['max_reward']) / 2
        points += min((day-1)*economy['streak_daily_step'], economy['streak_daily_max'])
        points += economy['streak_weekly_reward'] if day % 7 == 0 else 0
        points += economy['streak_cycle_reward'] if day % economy['streak_cycle_days'] == 0 else 0
        if tasks_today:
            points += counts.get('normal', 0) * task['normal_reward']
            points += counts.get('challenge', 0) * task['challenge_reward_sss']
            points += counts.get('advanced', 0) * task['advanced_reward_sss']
        gifts, month_fragments = monthly_rewards(rules, today.day)
        if tasks_today:
            gifts.update(daily_gifts)
        gained = month_fragments + (daily_fragments if tasks_today else 0)
        if buy:
            for size, plan in rules['gift_purchase'].items():
                quantity = min(plan['weekly_cap']-weekly_bought[size], int(points // plan['price']))
                points -= quantity * plan['price']
                spent_gifts += quantity * plan['price']
                weekly_bought[size] += quantity
                gifts[size] += quantity
        if pool is not None:
            while points >= economy['cost_11']:
                points -= economy['cost_11']
                for card in pool.draw(11):
                    old = inventory[card.id]
                    inventory[card.id] += 1
                    gained += duplicate_fragments(card.rarity, old, old+1, source='draw')
                pulls += 11
        fragments += gained
        fragments_gained += gained
        if exchange:
            price = rules['large_gift_fragment_price']
            quantity = max(0, fragments-reserve) // price
            fragments -= quantity * price
            gifts['large'] += quantity
            exchanged += quantity
        affection += rules['companion_points'] + sum(
            quantity * rules['gift_points'][size] for size, quantity in gifts.items())
        gifts_total.update(gifts)
        for level in (50, 100, 200, 1000):
            if affection >= thresholds[level]:
                milestones.setdefault(str(level), day)
        if day in (30, 90, 365):
            checkpoints[str(day)] = {'affection': affection, 'level': affection_level(affection, thresholds),
                'gifts': dict(gifts_total), 'fragments_gained': fragments_gained,
                'fragments_remaining': fragments, 'exchanged_large_gifts': exchanged,
                'points_remaining': points, 'gift_purchase_spend': spent_gifts, 'pulls': pulls}
        if '1000' in milestones and day >= 365:
            break
    return {'milestone_days': milestones, 'checkpoints': checkpoints}


def build(root: Path, *, trials=30, horizon=6000, seed=20261004) -> dict:
    if trials < 1 or horizon < 1:
        raise ValueError('trials/horizon 必须为正整数')
    config = tomllib.loads((root/'config.toml').read_text(encoding='utf-8'))
    rules = json.loads((root/'assets/growth/rules_draft.json').read_text(encoding='utf-8'))
    thresholds = tuple(json.loads((root/'assets/growth/affection_curve.json').read_text(encoding='utf-8'))['thresholds'])
    profiles = [('签到陪伴', {}), ('每日1普通', {'normal': 1}),
                ('每日1普通1挑战', {'normal': 1, 'challenge': 1}),
                ('全部普通', {'normal': config['task']['normal_count']}),
                ('普通与挑战', {'normal': config['task']['normal_count'], 'challenge': config['task']['challenge_count']}),
                ('全部日常任务', {kind: config['task'][f'{kind}_count'] for kind in ('normal','challenge','advanced')})]
    rows = []
    for version, active_rules in (('v8', previous_rules(rules)), ('v9', rules)):
        for label, counts in profiles:
            for exchange in ((False, True) if version == 'v9' else (False,)):
                rows.append({'version': version, 'profile': label, 'exchange': exchange,
                             **simulate(config, active_rules, thresholds, counts, exchange=exchange, horizon=horizon)})
    counts = profiles[-1][1]
    for version, active_rules, exchange in (('v8', previous_rules(rules), False), ('v9', rules, True)):
        rows.append({'version': version, 'profile': '每周2天各1普通1挑战', 'exchange': exchange,
            **simulate(config, active_rules, thresholds, {'normal':1, 'challenge':1}, exchange=exchange,
                       task_weekdays=(5,6), horizon=horizon)})
    for reserve, buy in ((90, False), (0, True)):
        rows.append({'version': 'v9', 'profile': '全部日常任务', 'exchange': True,
                     'fragment_reserve': reserve, 'buy_gifts': buy,
                     **simulate(config, rules, thresholds, counts, exchange=True, reserve=reserve, buy=buy, horizon=horizon)})
    cards = load_cards(root/'assets/card_data/card_info_merged.json')
    schedule = GachaSchedule.load(root/'assets/card_data/gacha_pools.json')
    pool_config = config['pool']
    stochastic = []
    for label, counts, weekdays in ((profiles[0][0], profiles[0][1], None),
            ('每周2天各1普通1挑战', {'normal':1, 'challenge':1}, (5,6)),
            (profiles[2][0], profiles[2][1], None), (profiles[-1][0], profiles[-1][1], None)):
        samples = []
        for trial in range(trials):
            pool = CardPool(cards, **{key: pool_config[key] for key in
                ('weight_n','weight_r','weight_sr','weight_sr_plus','weight_ssr')}, pool=schedule.regular_pool,
                strict_pool_cards=pool_config['strict_pool_cards'], pickup_multiplier=pool_config['pickup_multiplier'],
                rng=random.Random(seed+trial))
            samples.append(simulate(config, rules, thresholds, counts, exchange=True, pool=pool,
                                    horizon=horizon, task_weekdays=weekdays))
        group = {'profile': label, 'trials': trials, 'milestone_days': {}, 'samples': samples}
        for level in ('100', '200', '1000'):
            values = sorted(sample['milestone_days'][level] for sample in samples if level in sample['milestone_days'])
            group['milestone_days'][level] = {'completed': len(values),
                'median': statistics.median(values) if values else None,
                'min': min(values) if values else None, 'max': max(values) if values else None}
        stochastic.append(group)
    sources = ['config.toml','assets/growth/rules_draft.json','assets/growth/affection_curve.json',
               'assets/card_data/card_info_merged.json','assets/card_data/gacha_pools.json','gacha_core.py',
               'growth_core.py','tools/affection_balance_report.py']
    return {'rule_version': rules['version'], 'start_date': '2026-10-01', 'seed': seed,
            'horizon_days': horizon, 'source_sha256': {path: hashlib.sha256((root/path).read_bytes()).hexdigest() for path in sources},
            'assumptions': ['从月初连续签到，按实际日历与UTC ISO周；集中养一个角色，每日陪伴，全部礼物立即送出。',
                '点数、任务次数和抽卡参数读取config.toml；养成采用随包规则JSON，不合并实例的growth覆盖配置。',
                '任务每天审核通过；礼物不要求SSS，点数按SSS上界计算，影响买礼物和抽卡的样本。',
                'v8为开发中间方案，不是1.3.1发布版；无新增任务礼物及碎片兑换。',
                '默认不买礼物、不抽卡、不花碎片超解花；兑换组将所有可用碎片兑换，保留90片组另列。',
                '抽卡组从空库存开始，使用真实常驻池、当前权重及十一连保底；无天井、月卡、囤点奖、签到随机卡或大奖。',
                '表中天数只表示好感门槛，不代表已满足满星、解花券及超解花材料条件。',
                '随机样本给出完成数、中位数与范围，期限内未到门槛不计为完成。'],
            'deterministic': rows, 'stochastic': stochastic}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--trials', type=int, default=30)
    parser.add_argument('--horizon', type=int, default=6000)
    args = parser.parse_args()
    report = build(Path(__file__).parents[1], trials=args.trials, horizon=args.horizon)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    for row in report['deterministic']:
        print(row['version'], row['profile'], 'exchange', row['exchange'],
              'reserve', row.get('fragment_reserve', 0), 'buy', row.get('buy_gifts', False), row['milestone_days'])
    for group in report['stochastic']:
        print('stochastic', group['profile'], group['milestone_days'])
