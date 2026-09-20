"""默认经济前后对照；期望点数流量，不将掉卡/重复碎片当作保底收益。

基线固定为 v5，当前值读取本地 config.toml 与 assets/growth/rules_draft.json。
报告同时校验「加入高级挑战后，日常任务点数上限增幅不超过 35%」。
"""
from __future__ import annotations

import json
from pathlib import Path
import tomllib


# v5：本次改动前的默认值，仅用于前后对照。
OLD = {
    "economy": {
        "min_reward": 70,
        "max_reward": 110,
        "streak_daily_step": 10,
        "streak_daily_max": 100,
        "streak_weekly_reward": 600,
        "streak_cycle_days": 15,
        "streak_cycle_reward": 1200,
        "savings_bonus_1": 100,
        "savings_bonus_2": 500,
        "savings_bonus_3": 2000,
    },
    "task": {
        "normal_count": 3,
        "challenge_count": 2,
        "advanced_count": 0,
        "normal_reward": 40,
        "challenge_reward_s": 60,
        "challenge_reward_ss": 80,
        "challenge_reward_sss": 100,
        "advanced_reward_s": 0,
        "advanced_reward_ss": 0,
        "advanced_reward_sss": 0,
        "ultimate_reward": 30000,
    },
    "monthly_card": {"daily_bonus": 80},
}

OLD_RULES = {
    "checkin_small_gifts": 1,
    "checkin_fragments": 2,
    "weekly_fragments": 10,
    "monthly_event_days": 7,
    "monthly_event_fragments": 20,
    "monthly_event_small_gift_days": [1, 3],
    "monthly_event_medium_gift_days": [5],
    "monthly_event_large_gift_days": [7],
    "companion_points": 300,
    "gift_points": {"small": 300, "medium": 1000, "large": 10000},
    "task_medium_gifts_daily_cap": 1,
    "task_medium_gift_sources": ["challenge"],
    "task_fragments_daily_cap": 4,
    "task_fragments": {"normal": 1, "challenge": 2, "advanced": 0, "ultimate": 0},
    "bloom_levels": [50, 100],
    "bloom_costs": [30, 90],
    "bloom_item_id": "flower_fragment",
    "ultimate_large_gifts_lifetime_cap": 0,
}


def _task_daily_max(task: dict) -> int:
    return (
        int(task["normal_count"]) * int(task["normal_reward"])
        + int(task["challenge_count"]) * int(task["challenge_reward_sss"])
        + int(task.get("advanced_count", 0)) * int(task.get("advanced_reward_sss", 0))
    )


def _profile_counts(profile: str, task: dict) -> tuple[int, int, int]:
    if profile == "签到陪伴":
        return 0, 0, 0
    if profile == "每日1普通":
        return 1, 0, 0
    if profile == "每日1普通1挑战SSS":
        return 1, 1, 0
    if profile == "全部普通+挑战SSS":
        return int(task["normal_count"]), int(task["challenge_count"]), 0
    return (
        int(task["normal_count"]),
        int(task["challenge_count"]),
        int(task.get("advanced_count", 0)),
    )


def _item_balance(current: dict, rules: dict, curve: list[int]) -> dict:
    """按满勤上界估算月度物品流量；碎片当前没有消耗端。"""
    growth = current["growth"]
    weeks = 30 / 7
    small_days = len(rules.get("monthly_event_small_gift_days") or [])
    medium_days = len(rules.get("monthly_event_medium_gift_days") or [])
    large_days = len(rules.get("monthly_event_large_gift_days") or [])
    small_month = small_days + growth["gift_purchase_small_weekly_cap"] * weeks
    medium_month = (
        medium_days
        + min(
            int(rules["task_medium_gifts_daily_cap"]),
            int(int(current["task"]["challenge_count"]) > 0 and "challenge" in rules["task_medium_gift_sources"])
            + int(int(current["task"]["advanced_count"]) > 0 and "advanced" in rules["task_medium_gift_sources"]),
        )
        * 30
        + growth["gift_purchase_medium_weekly_cap"] * weeks
    )
    large_month = large_days
    affection_per_month = (
        30 * int(rules["companion_points"])
        + small_month * int(rules["gift_points"]["small"])
        + medium_month * int(rules["gift_points"]["medium"])
        + large_month * int(rules["gift_points"]["large"])
    )
    fragments_fixed = (
        3 * int(rules["monthly_event_fragments"])
        + 30 * int(rules["task_fragments_daily_cap"])
    )
    cooldown_days = max(int(rules["bloom_ticket_source"]["cooldown_days"]), 1)
    tickets_per_month = 30 / cooldown_days
    return {
        "fragments_fixed_per_month": fragments_fixed,
        "fragments_sink_per_super_bloom": (
            int(rules["bloom_costs"][1])
            if rules["bloom_items"][1] == "flower_fragment"
            else None
        ),
        "fragments_note": "碎片只用于超解花；固定来源已降为活动周5/天、任务2/天上限",
        "bloom_tickets_per_month": round(tickets_per_month, 2),
        "bloom_items": list(rules["bloom_items"]),
        "bloom_ticket_costs": list(rules["bloom_costs"]),
        "gifts_per_month": {
            "small": round(small_month, 1),
            "medium": round(medium_month, 1),
            "large": large_month,
        },
        "affection_per_month": affection_per_month,
        "estimated_days_to_lv100": round(curve[100] / (affection_per_month / 30), 1),
        "estimated_days_to_lv200": round(curve[200] / (affection_per_month / 30), 1),
    }


def build(root: Path) -> dict:
    current = tomllib.loads((root / "config.toml").read_text(encoding="utf8"))
    rules = json.loads((root / "assets/growth/rules_draft.json").read_text(encoding="utf8"))
    curve = json.loads((root / "assets/growth/affection_curve.json").read_text(encoding="utf8"))["thresholds"]

    before_config = {
        "economy": {**current["economy"], **OLD["economy"]},
        "task": {**current["task"], **OLD["task"]},
        "monthly_card": {**current["monthly_card"], **OLD["monthly_card"]},
    }
    profiles = (
        "签到陪伴",
        "每日1普通",
        "每日1普通1挑战SSS",
        "全部普通+挑战SSS",
        "全部普通+挑战+高级挑战SSS",
    )
    rows = []
    for version, config, active_rules in (
        ("before", before_config, OLD_RULES),
        ("after", current, rules),
    ):
        economy = config["economy"]
        task = config["task"]
        small_days = set(active_rules.get("monthly_event_small_gift_days") or [])
        medium_days = set(active_rules.get("monthly_event_medium_gift_days") or [])
        large_days = set(active_rules.get("monthly_event_large_gift_days") or [])
        for profile in profiles:
            normal_count, challenge_count, advanced_count = _profile_counts(profile, task)
            points = affection = fragments = small = medium = large = 0
            milestones: dict[str, int] = {}
            totals: dict[int, dict] = {}
            stage = 0
            for day in range(1, 1001):
                points += (economy["min_reward"] + economy["max_reward"]) / 2
                points += min((day - 1) * economy["streak_daily_step"], economy["streak_daily_max"])
                points += economy["streak_weekly_reward"] if day % 7 == 0 else 0
                points += economy["streak_cycle_reward"] if day % economy["streak_cycle_days"] == 0 else 0
                points += normal_count * task["normal_reward"]
                points += challenge_count * task["challenge_reward_sss"]
                points += advanced_count * task.get("advanced_reward_sss", 0)

                day_of_month = (day - 1) % 30 + 1
                in_event = day_of_month <= active_rules.get("monthly_event_days", 0)
                gift_small = int(in_event and day_of_month in small_days)
                gift_medium = int(in_event and day_of_month in medium_days)
                gift_large = int(in_event and day_of_month in large_days)
                if not in_event:
                    gift_small = active_rules.get("checkin_small_gifts", 0)
                    fragments += active_rules.get("checkin_fragments", 0)
                    fragments += active_rules.get("weekly_fragments", 0) if day % 7 == 0 else 0
                elif day_of_month not in small_days | medium_days | large_days:
                    fragments += active_rules["monthly_event_fragments"]
                small += gift_small
                medium += gift_medium
                large += gift_large

                task_gift_sources = set(active_rules.get("task_medium_gift_sources") or [])
                task_gifts = min(
                    active_rules["task_medium_gifts_daily_cap"],
                    int(challenge_count > 0 and "challenge" in task_gift_sources)
                    + int(advanced_count > 0 and "advanced" in task_gift_sources),
                )
                medium += task_gifts
                affection = min(
                    curve[-1],
                    affection
                    + active_rules["companion_points"]
                    + gift_small * active_rules["gift_points"]["small"]
                    + gift_medium * active_rules["gift_points"]["medium"]
                    + gift_large * active_rules["gift_points"]["large"]
                    + task_gifts * active_rules["gift_points"]["medium"],
                )
                fragments += min(
                    active_rules["task_fragments_daily_cap"],
                    normal_count * active_rules["task_fragments"]["normal"]
                    + challenge_count * active_rules["task_fragments"]["challenge"]
                    + advanced_count * active_rules["task_fragments"].get("advanced", 0),
                )
                for level in (100, 200, 1000):
                    if affection >= curve[level]:
                        milestones.setdefault(f"level_{level}", day)
                while stage < 2 and affection >= curve[active_rules["bloom_levels"][stage]]:
                    # 解花券来源为高级挑战13.5+/SSS；这里只记录好感门槛，不假设券已发放。
                    stage += 1
                    milestones[f"affection_gate_stage_{stage}"] = day
                if day in (30, 90):
                    totals[day] = {
                        "points": points,
                        "eleven_pull_equivalent": round(points / economy["cost_11"], 2),
                        "small_gifts": small,
                        "medium_gifts": medium,
                        "large_gifts": large,
                        "fragments": fragments,
                        "stage": stage,
                    }
            rows.append(
                {
                    "version": version,
                    "profile": profile,
                    "totals": totals,
                    "milestones": milestones,
                }
            )

    before_daily = _task_daily_max(before_config["task"])
    after_daily = _task_daily_max(current["task"])
    increase = (after_daily - before_daily) / before_daily * 100 if before_daily else 0.0
    return {
        "assumptions": [
            "从第1天连续签到；已拥有并集中培养一个主角色，每日陪伴，礼物用于同一角色。",
            "基础签到按数学期望；挑战与高级挑战按SSS上界；无月卡、囤点奖、终极奖、管理员注入。",
            "十一连等价仅为点数/500，不是抽到目标卡或满星概率。",
            "未计5%签到掉卡及随机抽卡重复碎片，所得养成时间仅隔离固定奖励通道。",
            "解花券暂定高级挑战目标≥13.5且SSS/SSS+获得，15天冷却；阶段时间只计算角色好感门槛。",
            "满级后的礼物数量为累计发放量，不表示全部已消费。",
        ],
        "task_daily_max": {
            "before": before_daily,
            "after": after_daily,
            "increase_percent": round(increase, 3),
            "within_35_percent": increase <= 35.0,
        },
        "changes": {
            "before": OLD,
            "after": {
                section: {key: current[section][key] for key in values}
                for section, values in OLD.items()
            },
        },
        "monthly_card": {
            "price": current["monthly_card"]["price"],
            "days": current["monthly_card"]["duration_days"],
            "bonus_before": OLD["monthly_card"]["daily_bonus"] * current["monthly_card"]["duration_days"],
            "bonus_after": current["monthly_card"]["daily_bonus"] * current["monthly_card"]["duration_days"],
            "net_after": (
                current["monthly_card"]["daily_bonus"] * current["monthly_card"]["duration_days"]
                - current["monthly_card"]["price"]
            ),
            "two_half_price_coupon_savings": 250,
        },
        "savings_max_per_60_days": {
            "before": sum(OLD["economy"][f"savings_bonus_{i}"] for i in (1, 2, 3)),
            "after": sum(current["economy"][f"savings_bonus_{i}"] for i in (1, 2, 3)),
        },
        "rules": {
            "version": rules["version"],
            "monthly_event_fragments": rules["monthly_event_fragments"],
            "bloom_levels": rules["bloom_levels"],
            "bloom_items": rules["bloom_items"],
            "bloom_costs": rules["bloom_costs"],
            "bloom_ticket_source": rules["bloom_ticket_source"],
            "task_fragments": rules["task_fragments"],
            "task_fragments_daily_cap": rules["task_fragments_daily_cap"],
        },
        "item_balance": _item_balance(current, rules, curve),
        "rows": rows,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build(Path(__file__).parents[1])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print("task_daily_max", report["task_daily_max"])
    print("item_balance", report["item_balance"])
    for row in report["rows"]:
        print(row["version"], row["profile"], row["totals"][30], row["milestones"])
