"""养成物品获得提示图；有原作素材的物品显示素材，其余用通用底板。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from . import render_theme as t
from .render_components import canvas, contain, footer, save, surface

ROOT = Path(__file__).parent / "assets/growth/images"

ITEMS = {
    "gift_small": {"name": "小礼物", "asset": "gift_small.png", "note": "送礼时好感 +300"},
    "gift_medium": {"name": "中礼物", "asset": "gift_medium.png", "note": "送礼时好感 +1000"},
    "gift_large": {"name": "大礼物", "asset": "gift_large.png", "note": "送礼时好感 +10000"},
    "flower_fragment": {"name": "花之碎片", "asset": "bloom/petal.png", "note": "用于超解花"},
    "bloom_ticket": {"name": "解花券", "asset": "bloom_ticket.png", "note": "用于解花"},
}


def render_item_gain(
    items: dict[str, int],
    output: Path,
    *,
    title: str = "获得物品",
    subtitle: str = "",
    totals: dict[str, int] | None = None,
) -> Path | None:
    """渲染本次新增物品；没有实际新增时返回 None。"""
    rows = [(key, int(amount)) for key, amount in items.items() if int(amount or 0) > 0]
    if not rows:
        return None
    totals = totals or {}
    height = 300 + len(rows) * 250 + 120
    image, draw, _ = canvas(height, title, subtitle, accent=t.PINK)
    for index, (item_id, amount) in enumerate(rows):
        meta = ITEMS.get(item_id, {"name": item_id, "asset": None, "note": ""})
        y = 190 + index * 250
        surface(draw, (48, y, 1032, y + 226))
        draw.rounded_rectangle((80, y + 28, 300, y + 198), radius=22, fill="#E9EFF7")
        asset = ROOT / meta["asset"] if meta.get("asset") else None
        if asset is not None and asset.is_file():
            with Image.open(asset) as source:
                contain(image, source, (96, y + 42, 284, y + 184))
        else:
            draw.text(
                (190, y + 113),
                meta["name"][:1],
                font=t.font(72, True),
                fill=t.CYAN,
                anchor="mm",
            )
        draw.text((336, y + 44), meta["name"], font=t.font(44, True), fill=t.TEXT, anchor="lt")
        draw.text(
            (336, y + 106),
            f"获得 ×{amount}",
            font=t.font(34, True),
            fill=t.PINK,
            anchor="lt",
        )
        owned = totals.get(item_id)
        detail = f"当前持有 {owned}" if owned is not None else ""
        note = meta.get("note", "")
        draw.text(
            (336, y + 158),
            "｜".join(part for part in (detail, note) if part),
            font=t.font(26),
            fill=t.MUTED,
            anchor="lt",
        )
    footer(draw, height, command="/礼物｜/点数｜/好感｜/解花 <卡ID>")
    return save(image, output)
