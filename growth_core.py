"""版本化养成纯函数，供迁移、用户交易和离线模拟共用。"""
from bisect import bisect_right

MAIN_CHARACTER_IDS = frozenset(range(1000, 1017))
CURVE_VERSION = "affection-v2"
REWARD_MAX_LEVEL = 1000
MAX_AFFECTION_LEVEL = 9999
FRAGMENT_RATES = {"N": 1, "R": 1, "SR": 3, "SRPlus": 4, "SSR": 8}
OVERFLOW_FRAGMENT_RATES = {"N": 2, "R": 2, "SR": 6, "SRPlus": 8, "SSR": 16}


def legacy_growth(rarity: str | None, copies: int, kaika: bool, cho_kaika: bool) -> tuple[int, int]:
    """仅用于迁移：保留旧阶段，补偿两次旧成长之后的冗余卡。"""
    if copies < 0:
        raise ValueError("旧库存数量为负，需人工修复")
    stage = 2 if cho_kaika else 1 if kaika else 0
    if rarity not in FRAGMENT_RATES:
        return stage, 0
    maximum = 11 if rarity == "N" else 5
    derived = 2 if copies >= maximum + 2 else 1 if copies >= maximum + 1 else 0
    return max(stage, derived), max(0, copies - maximum - 2) * OVERFLOW_FRAGMENT_RATES[rarity]


def build_thresholds(points: list[int], scales: list[int]) -> tuple[int, ...]:
    """构造 0～9999 级阈值：前 10 档照原曲线，之后按最高档每级需求平推。"""
    if len(points) < 10 or len(scales) < 10:
        raise ValueError("曲线源表不足十档")
    thresholds = [0]
    for level in range(REWARD_MAX_LEVEL):
        # 原作 makeInitimateTable：每级需求 = Point × Scale / 100。
        # 旧实现误用 /1000，使整条曲线缩小为原作的 1/10。
        numerator = points[(level % 100) // 10] * scales[level // 100]
        if numerator <= 0 or numerator % 100:
            raise ValueError(f"等级 {level} 的增量不是正整数")
        thresholds.append(thresholds[-1] + numerator // 100)
    last_step = thresholds[REWARD_MAX_LEVEL] - thresholds[REWARD_MAX_LEVEL - 1]
    if last_step <= 0:
        raise ValueError("最高档位每级需求必须为正整数")
    for _ in range(MAX_AFFECTION_LEVEL - REWARD_MAX_LEVEL):
        thresholds.append(thresholds[-1] + last_step)
    return tuple(thresholds)


def affection_level(points: int, thresholds: tuple[int, ...]) -> int:
    """好感等级；10 档后继续累计，心形最多显示到 99/99（Lv9999）。"""
    if points < 0:
        raise ValueError("好感点数不能为负")
    return min(bisect_right(thresholds, points) - 1, MAX_AFFECTION_LEVEL)


def affection_progress(points: int, thresholds: tuple[int, ...]) -> tuple[int, float, int, int]:
    """返回（等级, 本级进度比例, 本级已累计, 本级需求）。

    10 档之后本级需求固定为最高档位每级需求；达到 99/99 后比例保持 1。
    """
    level = affection_level(points, thresholds)
    if level >= MAX_AFFECTION_LEVEL:
        need = thresholds[MAX_AFFECTION_LEVEL] - thresholds[MAX_AFFECTION_LEVEL - 1]
        return level, 1.0, need, need
    need = thresholds[level + 1] - thresholds[level]
    current = points - thresholds[level]
    return level, current / need, current, need


def duplicate_fragments(rarity: str, old_copies: int, new_copies: int,
                        *, source: str) -> int:
    """重复卡即给碎片：满星内每张按基础值，超出满星部分按更高值。"""
    if old_copies < 0 or new_copies < old_copies:
        raise ValueError("发卡数量非法")
    if source == "affection_reward":
        return 0
    if source not in {"draw", "checkin", "select_card"}:
        raise ValueError("发卡来源必须明确重复补偿策略")
    maximum = 11 if rarity == "N" else 5

    def split(copies: int) -> tuple[int, int]:
        duplicate = min(max(copies, 1), maximum) - 1
        return duplicate, max(0, copies - maximum)

    old_duplicate, old_overflow = split(old_copies)
    new_duplicate, new_overflow = split(new_copies)
    return (
        (new_duplicate - old_duplicate) * FRAGMENT_RATES[rarity]
        + (new_overflow - old_overflow) * OVERFLOW_FRAGMENT_RATES[rarity]
    )
