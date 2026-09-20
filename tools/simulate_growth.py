"""可复现离线经济实验，复用运行抽卡器；结果不是用户收益承诺。"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import statistics
import tomllib

from ..gacha_core import CardPool, load_cards, max_detail_slots
from ..gacha_pools import GachaSchedule
from ..growth_core import REWARD_MAX_LEVEL, duplicate_fragments


def simulate(root: Path, *, runs: int = 30, days: int = 730, seed: int = 20260912) -> dict:
    if runs < 1 or days < 1:
        raise ValueError("runs/days 必须为正整数")
    card_root = root / "assets/card_data"
    cards = load_cards(card_root / "card_info_merged.json")
    schedule = GachaSchedule.load(card_root / "gacha_pools.json")
    if schedule.regular_pool is None or schedule.non_gacha_pool is None:
        raise ValueError("模拟必须具有真实常驻和签到池")
    config = tomllib.loads((root / "config.toml").read_text(encoding="utf-8-sig"))
    rules = json.loads((root / "assets/growth/rules_draft.json").read_text(encoding="utf-8"))
    curve = json.loads((root / "assets/growth/affection_curve.json").read_text(encoding="utf-8"))["thresholds"]
    targets = [c for c in cards.cards if c.id in schedule.regular_pool.cards
               and c.character_id == 1000 and c.rarity in {"R", "SR", "SSR"}][:10]
    split_targets = [next(c for c in cards.cards if c.id in schedule.regular_pool.cards
                         and c.character_id == cid and c.rarity == "R") for cid in range(1000, 1010)]
    if len(targets) != 10:
        raise ValueError("目标卡不足十张")
    reward_catalog = json.loads((root / "assets/growth/character_catalog.json").read_text(encoding="utf-8"))["characters"]
    rewards = {int(c["id"]): c["rewards"] for c in reward_catalog}
    results = []
    for profile, accompany, task, extra_budget in [
        ("checkin_only", False, False, 0),
        ("checkin_companion", True, False, 0),
        ("daily_normal_task", True, True, 0),
        ("many_duplicates", True, True, 1500),
    ]:
        for split in (False, True):
            selected = split_targets if split else targets
            for initial in ("already_max_stars", "from_zero"):
                rows = []
                for trial in range(runs if initial == "from_zero" else 1):
                    rng = random.Random(seed + trial)
                    pcfg = config["pool"]
                    pool = CardPool(cards, **{k: pcfg[k] for k in (
                        "weight_n", "weight_r", "weight_sr", "weight_sr_plus", "weight_ssr")},
                        pool=schedule.regular_pool, strict_pool_cards=True, pickup_multiplier=1, rng=rng)
                    inventory = Counter({c.id: max_detail_slots(c.rarity) for c in selected}
                                        if initial == "already_max_stars" else {})
                    affection = Counter()
                    claimed = set()
                    stages = [0] * 10
                    milestones = {}
                    fragments = balance = pulls = small = medium = 0
                    for day in range(1, days + 1):
                        ec = config["economy"]
                        balance += rng.randint(ec["min_reward"], ec["max_reward"])
                        balance += min((day - 1) * ec["streak_daily_step"], ec["streak_daily_max"])
                        balance += ec["streak_weekly_reward"] if day % 7 == 0 else 0
                        balance += ec["streak_cycle_reward"] if day % ec["streak_cycle_days"] == 0 else 0
                        balance += config["task"]["normal_reward"] if task else 0
                        balance += extra_budget
                        fragments += rules["checkin_fragments"] + (rules["weekly_fragments"] if day % 7 == 0 else 0)
                        fragments += rules["task_fragments"]["normal"] if task else 0
                        small += rules["checkin_small_gifts"]
                        medium += min(1, rules["task_medium_gifts_daily_cap"]) if task and "normal" in rules["task_medium_gift_sources"] else 0

                        def grant(card, source):
                            nonlocal fragments
                            old = inventory[card.id]
                            inventory[card.id] += 1
                            fragments += duplicate_fragments(card.rarity, old, old + 1, source=source)

                        if initial == "from_zero":
                            if rng.random() < ec["non_gacha_checkin_probability"]:
                                grant(cards.by_id[rng.choice(tuple(schedule.non_gacha_pool.cards))], "checkin")
                            while balance >= ec["cost_11"] and ec["cost_11"] > 0:
                                balance -= ec["cost_11"]
                                pulls += 11
                                for card in pool.draw(11):
                                    grant(card, "draw")
                        cid = selected[(day - 1) % 10].character_id if split else 1000
                        unlocked = any(inventory[c.id] > 0 and c.character_id == cid for c in cards.cards)
                        if unlocked:
                            if accompany:
                                affection[cid] = min(curve[REWARD_MAX_LEVEL], affection[cid] + rules["companion_points"])
                            used = min(small, (curve[REWARD_MAX_LEVEL] - affection[cid]) // rules["gift_points"]["small"])
                            affection[cid] += used * rules["gift_points"]["small"]
                            small -= used
                            used = min(medium, (curve[REWARD_MAX_LEVEL] - affection[cid]) // rules["gift_points"]["medium"])
                            affection[cid] += used * rules["gift_points"]["medium"]
                            medium -= used
                            for reward in rewards[cid]:
                                key = reward["reward_key"]
                                if key not in claimed and affection[cid] >= curve[int(reward["level"])]:
                                    claimed.add(key)
                                    if reward["kind"] == "NormalCard":
                                        grant(cards.by_id[int(reward["id"])], "affection_reward")
                        # 解花券来源为高级挑战13.5+/SSS：这里只模拟角色好感门槛。
                        for index, card in enumerate(selected):
                            while stages[index] < 2:
                                stage = stages[index]
                                if affection[card.character_id] < curve[rules["bloom_levels"][stage]]:
                                    break
                                stages[index] += 1
                                if index in (0, 9):
                                    milestones[f"card_{index + 1}_stage_{stage + 1}"] = day
                            if stages[index] < 2:
                                break
                    rows.append({"days": milestones, "fragments": fragments, "pulls": pulls,
                                 "points": balance, "stages": stages,
                                 "affection_points": dict(affection)})
                summary = {}
                for key in ("card_1_stage_1", "card_1_stage_2", "card_10_stage_1", "card_10_stage_2"):
                    values = sorted(r["days"][key] for r in rows if key in r["days"])
                    summary[key] = {"completed": len(values), "trials": len(rows),
                                    "median_completed_days": statistics.median(values) if values else None,
                                    "min_days": min(values) if values else None,
                                    "max_days": max(values) if values else None}
                results.append({"profile": profile, "initial": initial, "split_characters": split,
                                "target_cards": [c.id for c in selected], "milestones": summary,
                                "mean_end_fragments": statistics.mean(r["fragments"] for r in rows),
                                "mean_pulls": statistics.mean(r["pulls"] for r in rows),
                                "runs": rows})
    sources = [root / "config.toml", root / "gacha_core.py", card_root / "card_info_merged.json",
               card_root / "gacha_pools.json", root / "assets/growth/rules_draft.json",
               root / "assets/growth/affection_curve.json"]
    return {"seed": seed, "horizon_days": days, "source_sha256": {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        "assumptions": ["常驻真实池和当前配置权重；11连采用运行保底逻辑；不模拟活动轮替。",
                        "每天签到并主动消耗礼物；仅签到表示不陪伴、不做任务。每天任务为一次普通审核通过。",
                        "多重复组每天额外注入1500抽卡点用于敏感性实验，不是系统免费产出。",
                        "从零组用签到和任务点数尽量11连；无月卡、囤点奖、极低概率大奖或天井。",
                        "已满星组不再抽卡，用于隔离好感和材料瓶颈；多重复注入仅对从零组生效。",
                        "解花券暂定高级挑战目标≥13.5且SSS/SSS+获得，阶段里程碑只代表角色好感门槛到达。",
                        "十角色轮流按日收取陪伴和积攒的礼物；前一卡两阶段完成后才养下一卡。",
            "期限内未完成记为删失，完成样本中位数不能代表全部用户；目标卡ID明确列出。"],
        "results": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = simulate(Path(__file__).parents[1], runs=args.runs, days=args.days)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(report['results'])} scenario groups: {args.output}")
