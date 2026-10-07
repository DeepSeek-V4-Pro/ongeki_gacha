"""礼物价格计算；纯函数供交易、展示和数值推演共用。"""
from __future__ import annotations


def large_gift_tiers(rules: dict) -> list[dict]:
    return [
        {"price": rules["large_gift_fragment_price"], "cap": rules["large_gift_monthly_first_cap"]},
        {"price": rules["large_gift_second_price"], "cap": rules["large_gift_monthly_second_cap"]},
        {"price": rules["large_gift_final_price"], "cap": None},
    ]


def quote_large_gifts(rules: dict, used: int, quantity: int) -> list[dict]:
    """used 为本月已兑总数，cap 为每一档自己的额度。"""
    result = []
    for tier in large_gift_tiers(rules):
        cap = tier["cap"]
        skipped = used if cap is None else min(used, cap)
        used -= skipped
        count = quantity if cap is None else min(quantity, cap - skipped)
        if count:
            result.append({"unit_price": tier["price"], "quantity": count, "cost": count * tier["price"]})
            quantity -= count
        if not quantity:
            break
    return result


def affordable_large_gifts(rules: dict, used: int, fragments: int) -> int:
    quantity = 0
    for tier in large_gift_tiers(rules):
        cap = tier["cap"]
        skipped = used if cap is None else min(used, cap)
        used -= skipped
        count = fragments // tier["price"]
        if cap is not None:
            count = min(count, cap - skipped)
        quantity += count
        fragments -= count * tier["price"]
        if cap is None or count < cap - skipped:
            break
    return quantity


def large_gift_shop_lines(rules: dict, used: int = 0) -> list[str]:
    lines = []
    for number, tier in enumerate(large_gift_tiers(rules), 1):
        cap = tier["cap"]
        consumed = used if cap is None else min(used, cap)
        used -= consumed
        quota = "不限量" if cap is None else f"剩余 {cap - consumed}/{cap} 份"
        lines.append(f"第{number}档 {tier['price']} 碎片/份 · {quota}")
    return lines
