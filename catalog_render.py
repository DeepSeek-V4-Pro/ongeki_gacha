"""卡册按角色分页渲染。

固定每页行数，由用户用命令指定页码，单次只发一张图，避免自动连发刷屏。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import render_theme as t
from .gacha_core import rarity_display
from .growth_core import MAIN_CHARACTER_IDS
from .render_components import canvas, footer, save, surface

PAGE_SIZE = 24
OTHER_KEY = "other"
OTHER_LABEL = "其他"
ROW_HEIGHT = 58
LIST_TOP = 190


def strip_rarity(name: str) -> str:
    """去掉卡名开头的稀有度标签，列表里不再重复显示。"""
    value = str(name or "")
    return re.sub(r"^【[^】]+】\s*", "", value).strip() or value


def buckets(cards) -> dict[object, list]:
    """把全部卡牌按主角色归档，其余统一放进“其他”。"""
    groups: dict[object, list] = {cid: [] for cid in sorted(MAIN_CHARACTER_IDS)}
    groups[OTHER_KEY] = []
    for card in cards.cards:
        group = card.character_id if card.character_id in groups else OTHER_KEY
        groups[group].append(card)
    for rows in groups.values():
        rows.sort(key=lambda card: card.id)
    return groups


def page_count(rows: list) -> int:
    return max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)


def page_slice(rows: list, page: int) -> tuple[list, int, int]:
    pages = page_count(rows)
    current = max(1, min(int(page or 1), pages))
    start = (current - 1) * PAGE_SIZE
    return rows[start:start + PAGE_SIZE], current, pages


def _fit(draw, text: str, font, max_width: int) -> str:
    value = str(text or "")
    if draw.textlength(value, font=font) <= max_width:
        return value
    while value and draw.textlength(value + "…", font=font) > max_width:
        value = value[:-1]
    return (value.rstrip() + "…") if value else "…"


def _owned_count(rows: list, owned: dict[int, int]) -> int:
    return sum(1 for card in rows if owned.get(card.id, 0) > 0)


def render_catalog_index(
    catalog,
    groups: dict[object, list],
    owned: dict[int, int],
    output: Path,
    *,
    partner: int | None = None,
) -> Path:
    """卡册总览：17名主角色与“其他”的拥有量，以及各自的翻页命令。"""
    keys = [*sorted(MAIN_CHARACTER_IDS), OTHER_KEY]
    height = 300 + len(keys) * 78 + 110
    total_cards = sum(len(groups[key]) for key in keys)
    total_owned = sum(_owned_count(groups[key], owned) for key in keys)
    image, draw, _ = canvas(
        height,
        "卡册",
        f"按角色查看｜已拥有 {total_owned} / {total_cards} 张｜发送 /卡册 <角色姓名> <页码>",
        accent=t.CYAN,
    )
    for index, key in enumerate(keys):
        rows = groups[key]
        y = LIST_TOP + index * 78
        if key == OTHER_KEY:
            label, query, accent = OTHER_LABEL, OTHER_LABEL, t.MUTED
        else:
            label = catalog.characters[key]["name"]
            query, accent = label, t.CYAN
        surface(draw, (48, y, 1032, y + 66))
        draw.rectangle((48, y, 54, y + 66), fill=accent)
        draw.text((78, y + 33), label, font=t.font(34, True), fill=t.TEXT, anchor="lm")
        marker = "｜当前伙伴" if key == partner else ""
        draw.text(
            (560, y + 33),
            f"已拥有 {_owned_count(rows, owned)} / {len(rows)} 张{marker}",
            font=t.font(26),
            fill=t.MUTED,
            anchor="lm",
        )
        draw.text(
            (1000, y + 33),
            f"/卡册 {query} 1",
            font=t.font(25, True),
            fill=t.PINK,
            anchor="rm",
        )
    footer(draw, height, command="/卡册 <角色姓名> <页码>｜/卡图 <卡ID>｜/好感 列表")
    return save(image, output)


def index_text(catalog, groups: dict[object, list], owned: dict[int, int],
               *, partner: int | None = None) -> list[str]:
    """图片生成失败时的卡册总览文字回退。"""
    lines = ["卡册总览"]
    for key in [*sorted(MAIN_CHARACTER_IDS), OTHER_KEY]:
        name = OTHER_LABEL if key == OTHER_KEY else catalog.characters[key]["name"]
        marker = "（当前伙伴）" if key == partner else ""
        lines.append(
            f"{name}{marker}：已拥有 {_owned_count(groups[key], owned)}/"
            f"{len(groups[key])}｜/卡册 {name} 1"
        )
    return lines


def page_text(name: str, rows: list, owned: dict[int, int], page: int,
              query: str) -> list[str]:
    """图片生成失败时的单页文字回退。"""
    page_rows, current, pages = page_slice(rows, page)
    lines = [
        f"卡册 · {name}（第 {current}/{pages} 页）",
        f"已拥有 {_owned_count(rows, owned)}/{len(rows)} 张",
    ]
    for card in page_rows:
        copies = owned.get(card.id, 0)
        lines.append(
            f"{rarity_display(card.rarity)} {card.id} {strip_rarity(card.name)}"
            + (f" ×{copies}" if copies else " 未持有")
        )
    if pages > 1:
        lines.append(
            f"翻页：/卡册 {query} "
            f"{current + 1 if current < pages else 1}"
        )
    return lines


def render_catalog_page(
    catalog,
    rows: list,
    owned: dict[int, int],
    output: Path,
    *,
    name: str,
    query: str,
    page: int,
    character: int | None = None,
    partner: int | None = None,
) -> tuple[Path, int, int]:
    """渲染指定角色卡册的其中一页；返回 (路径, 当前页, 总页数)。"""
    page_rows, current, pages = page_slice(rows, page)
    height = LIST_TOP + PAGE_SIZE * ROW_HEIGHT + 130
    marker = "｜当前伙伴" if character is not None and character == partner else ""
    image, draw, _ = canvas(
        height,
        f"卡册 · {name}",
        f"已拥有 {_owned_count(rows, owned)} / {len(rows)} 张{marker}"
        f"｜第 {current} / {pages} 页",
        accent=t.CYAN,
        character=character,
    )
    surface(draw, (48, LIST_TOP - 12, 1032, LIST_TOP - 12 + PAGE_SIZE * ROW_HEIGHT + 48))
    draw.text((176, LIST_TOP + 4), "ID", font=t.font(24, True), fill=t.MUTED, anchor="lt")
    draw.text((300, LIST_TOP + 4), "卡名", font=t.font(24, True), fill=t.MUTED, anchor="lt")
    draw.text((1000, LIST_TOP + 4), "持有", font=t.font(24, True), fill=t.MUTED, anchor="rt")
    for index, card in enumerate(page_rows):
        y = LIST_TOP + 40 + index * ROW_HEIGHT
        copies = owned.get(card.id, 0)
        label = rarity_display(card.rarity)
        color = t.RARITY.get(card.rarity, t.MUTED) if copies else "#C3CBD9"
        draw.rounded_rectangle((76, y + 7, 156, y + 45), radius=9, fill=color)
        draw.text((116, y + 26), label, font=t.font(23, True), fill="white", anchor="mm")
        draw.text(
            (176, y + 26),
            str(card.id),
            font=t.font(27, True),
            fill=t.TEXT if copies else t.MUTED,
            anchor="lm",
        )
        draw.text(
            (300, y + 26),
            _fit(draw, strip_rarity(card.name), t.font(27), 520),
            font=t.font(27),
            fill=t.TEXT if copies else t.MUTED,
            anchor="lm",
        )
        draw.text(
            (1000, y + 26),
            f"×{copies}" if copies else "未持有",
            font=t.font(27, True),
            fill=t.CYAN if copies else t.MUTED,
            anchor="rm",
        )
        if index < len(page_rows) - 1:
            draw.line((76, y + ROW_HEIGHT, 1004, y + ROW_HEIGHT), fill="#EDF1F7", width=2)
    if pages > 1:
        next_command = (
            f"/卡册 {query} {current + 1}"
            if current < pages
            else f"/卡册 {query} 1"
        )
    else:
        next_command = f"/卡册 {query} 1"
    footer(
        draw,
        height,
        page=current,
        pages=pages,
        command=(
            f"/卡图 <ID> 查看卡图"
            if pages <= 1
            else f"翻页 {next_command}｜/卡图 <ID> 查看卡图"
        ),
    )
    return save(image, output), current, pages
