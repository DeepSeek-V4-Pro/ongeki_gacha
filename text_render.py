# -*- coding: utf-8 -*-
"""长文本回复的图片渲染。

把规则、列表一类较长的文本排版成浅色渐变卡片，避免在聊天里刷屏；
渲染失败时由调用方回退为合并转发或纯文本。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont


GRADIENT_STOPS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.00, (157, 216, 244)),
    (0.42, (247, 207, 239)),
    (0.76, (232, 215, 249)),
    (1.00, (250, 241, 250)),
)

TITLE_COLOR = (74, 64, 130)
TEXT_DARK = (60, 53, 106)
TEXT_MUTED = (126, 118, 154)
SECTION_COLOR = (95, 132, 201)
CARD_FILL = (255, 255, 255, 236)

SECTION_PATTERN = re.compile(r"^【.+】$")

FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
    Path("C:/Windows/Fonts/msyhbd.ttc"),
)
BOLD_FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/msyhbd.ttc"),
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
)

DEFAULT_WIDTH = 900
PADDING = 36
DEFAULT_MAX_PAGE_HEIGHT = 1150
HEADER_HEIGHT = 64
FOOTER_HEIGHT = 34
LINE_HEIGHTS: dict[str, int] = {
    "section": 40,
    "body": 34,
    "space": 16,
}


def _load_font(
    size: int,
    candidates: Sequence[Path],
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in candidates:
        if not path.is_file():
            continue
        try:
            return ImageFont.truetype(str(path), size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    """按像素宽度折行；中文按字符切分，英文按单词优先。"""
    text = str(text or "")
    if not text:
        return [""]
    if draw.textlength(text, font=font) <= max_width:
        return [text]

    lines: list[str] = []
    current = ""
    tokens = re.findall(r"<[^<>\s]*>|[A-Za-z0-9_./:+@#%<>=~-]+|\s+|.", text)
    for token in tokens:
        if token == "\n":
            lines.append(current.rstrip())
            current = ""
            continue
        candidate = f"{current}{token}"
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current.rstrip())
            current = token.lstrip() if token.isspace() else token
            if token.isspace():
                current = ""
        else:
            current = candidate
    if current:
        lines.append(current.rstrip())
    return lines or [""]


def _vertical_gradient(
    size: tuple[int, int],
    stops: Sequence[tuple[float, tuple[int, int, int]]] = GRADIENT_STOPS,
) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        color = stops[-1][1]
        for index in range(len(stops) - 1):
            start_pos, start_color = stops[index]
            end_pos, end_color = stops[index + 1]
            if start_pos <= ratio <= end_pos:
                local = (ratio - start_pos) / max(end_pos - start_pos, 1e-6)
                color = tuple(
                    int(start_color[i] + (end_color[i] - start_color[i]) * local)
                    for i in range(3)
                )
                break
        draw.line((0, y, width, y), fill=color)
    return image


def _build_rows(
    draw: ImageDraw.ImageDraw,
    body_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    inner_width: int,
    lines: Sequence[str],
) -> list[tuple[str, str]]:
    """把原始文本展开成排版行，小节标题与正文分开标记。"""
    rows: list[tuple[str, str]] = []
    for raw_line in lines:
        line = str(raw_line or "").rstrip()
        if not line:
            if rows and rows[-1][0] != "space":
                rows.append(("space", ""))
            continue
        if SECTION_PATTERN.match(line):
            rows.append(("section", line))
            continue
        for chunk in _wrap_text(draw, line, body_font, inner_width):
            rows.append(("body", chunk))
    while rows and rows[-1][0] in {"space", "section"}:
        rows.pop()
    return rows


def _paginate(
    rows: Sequence[tuple[str, str]],
    capacity: int,
) -> list[list[tuple[str, str]]]:
    """按高度把排版行切分为多页，避免小节标题孤零零留在页尾。"""
    pages: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    used = 0
    for row in rows:
        height = LINE_HEIGHTS[row[0]]
        if current and used + height > capacity:
            if current[-1][0] == "section":
                moved = current.pop()
                pages.append(current)
                current = [moved]
                used = LINE_HEIGHTS["section"]
            else:
                pages.append(current)
                current = []
                used = 0
        if row[0] == "space" and not current:
            continue
        current.append(row)
        used += height
    if current:
        pages.append(current)
    return pages or [[]]


def render_text_card(
    title: str,
    lines: Sequence[str],
    output_path: Path,
    width: int = DEFAULT_WIDTH,
    max_page_height: int = DEFAULT_MAX_PAGE_HEIGHT,
) -> list[Path]:
    """把标题与多行文本渲染成若干页卡片图片，返回全部输出路径。

    内容较短时只生成 `output_path` 一张图；超出单页高度时按页拆分，
    文件名追加 `_1`、`_2` 等页码后缀。
    """
    fonts = {
        "title": _load_font(30, BOLD_FONT_CANDIDATES),
        "section": _load_font(23, BOLD_FONT_CANDIDATES),
        "body": _load_font(20, FONT_CANDIDATES),
        "note": _load_font(15, FONT_CANDIDATES),
    }

    measure = Image.new("RGB", (8, 8))
    measure_draw = ImageDraw.Draw(measure)
    inner_width = width - PADDING * 2
    rows = _build_rows(measure_draw, fonts["body"], inner_width, lines)

    capacity = max(
        max_page_height - PADDING * 2 - FOOTER_HEIGHT - HEADER_HEIGHT,
        LINE_HEIGHTS["body"] * 2,
    )
    pages = _paginate(rows, capacity)
    total = len(pages)

    paths: list[Path] = []
    base_title = str(title or "").strip()
    for index, page_rows in enumerate(pages, start=1):
        page_title = (
            f"{base_title}（{index}/{total}）"
            if total > 1 and base_title
            else base_title
        )
        page_height = (
            PADDING * 2
            + FOOTER_HEIGHT
            + HEADER_HEIGHT
            + sum(LINE_HEIGHTS[kind] for kind, _ in page_rows)
        )
        page_height = min(page_height, max_page_height)

        canvas = _vertical_gradient((width, page_height))
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(
            (16, 16, width - 16, page_height - 16),
            radius=24,
            fill=CARD_FILL,
        )
        if page_title:
            draw.text((PADDING, PADDING), page_title, font=fonts["title"], fill=TITLE_COLOR)

        y = PADDING + HEADER_HEIGHT
        for kind, text in page_rows:
            if kind == "section":
                draw.text((PADDING, y + 4), text, font=fonts["section"], fill=SECTION_COLOR)
            elif kind == "body":
                draw.text((PADDING + 4, y + 2), text, font=fonts["body"], fill=TEXT_DARK)
            y += LINE_HEIGHTS[kind]

        if total > 1:
            draw.text(
                (PADDING, page_height - PADDING),
                f"{index} / {total}",
                font=fonts["note"],
                fill=TEXT_MUTED,
                anchor="ls",
            )
        draw.text(
            (width - PADDING, page_height - PADDING),
            "音击抽卡模拟器",
            font=fonts["note"],
            fill=TEXT_MUTED,
            anchor="rs",
        )

        if total == 1:
            path = output_path
        else:
            path = output_path.with_name(
                f"{output_path.stem}_{index}{output_path.suffix}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        canvas.convert("RGB").save(path, "PNG")
        paths.append(path)
    return paths


if __name__ == "__main__":
    render_text_card(
        "音击抽卡模拟器 · 规则",
        [
            "【抽卡】",
            "消耗：1 连 50 点、5 连 250 点、11 连 500 点",
            "11 连必得 SR 或以上；5 连每用户每周首次触发一次 SR 或以上保底",
            "【卡池】",
            "默认每 15 天轮换一个历史官方卡池；也可切换为按官方日期选池",
        ],
        Path("output/text_card_preview.png"),
    )
