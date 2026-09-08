# -*- coding: utf-8 -*-
"""音游随机任务卡渲染。

风格参考 maimai chart_info.png：
- 浅蓝→浅粉→浅紫的柔和渐变背景；
- 月亮、云朵、彩虹、星点等轻量装饰；
- 白色半透明圆角内容卡，信息保持简洁。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFilter, ImageFont


KIND_COLORS: dict[str, tuple[int, int, int]] = {
    "normal": (122, 196, 174),
    "challenge": (116, 158, 232),
    "ultimate": (171, 144, 226),
}

KIND_LABELS: dict[str, str] = {
    "normal": "普通任务",
    "challenge": "挑战任务",
    "ultimate": "终极任务",
}

GAME_LABELS: dict[str, str] = {
    "ongeki": "音击",
    "maimai": "舞萌 DX",
    "chunithm": "中二节奏",
}

TEXT_DARK = (60, 53, 106)
TEXT_MUTED = (126, 118, 154)
TEXT_WHITE = (255, 255, 255)

GRADIENT_STOPS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.00, (157, 216, 244)),
    (0.42, (247, 207, 239)),
    (0.76, (232, 215, 249)),
    (1.00, (250, 241, 250)),
)


@dataclass(frozen=True)
class TaskCardData:
    """任务卡所需的展示信息。"""

    task_id: int | str
    kind: str
    game: str
    title: str
    artist: str
    level: str = ""
    requirement: str = ""
    reward: int = 0
    user_id: str = ""
    note: str = ""
    cover_path: Path | None = None


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """优先使用 Windows 中文字体，找不到时回退默认字体。"""
    candidates = (
        Path("C:/Windows/Fonts/msyhbd.ttc"),
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    )
    for path in candidates:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _vertical_gradient(
    size: tuple[int, int],
    stops: Sequence[tuple[float, tuple[int, int, int]]] = GRADIENT_STOPS,
) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        for index in range(len(stops) - 1):
            start_pos, start_color = stops[index]
            end_pos, end_color = stops[index + 1]
            if start_pos <= ratio <= end_pos:
                local = (ratio - start_pos) / max(end_pos - start_pos, 1e-6)
                color = tuple(
                    int(start_color[i] + (end_color[i] - start_color[i]) * local)
                    for i in range(3)
                )
                draw.line((0, y, width, y), fill=color)
                break
    return image


def _rounded_cover(
    data: TaskCardData,
    size: tuple[int, int],
) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=max(18, width // 9),
        fill=(238, 245, 253, 255),
    )

    if data.cover_path is not None and Path(data.cover_path).is_file():
        try:
            with Image.open(data.cover_path) as source:
                cover = source.convert("RGBA")
            cover.thumbnail((width, height), Image.Resampling.LANCZOS)
            mask = Image.new("L", cover.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, cover.width - 1, cover.height - 1),
                radius=max(18, min(cover.width, cover.height) // 9),
                fill=255,
            )
            image.paste(cover, ((width - cover.width) // 2, (height - cover.height) // 2), mask)
            return image
        except (OSError, ValueError):
            pass

    # 无曲绘时的柔和占位
    for y in range(height):
        ratio = y / max(height - 1, 1)
        color = (
            int(158 + (240 - 158) * ratio),
            int(217 + (184 - 217) * ratio),
            int(243 + (230 - 243) * ratio),
            255,
        )
        draw.line((0, y, width, y), fill=color)
    _draw_music_note(draw, (width / 2, height / 2), min(width, height) * 0.42)
    return image


def _draw_music_note(
    draw: ImageDraw.ImageDraw,
    center: tuple[float, float],
    size: float,
    fill: tuple[int, int, int, int] = (255, 255, 255, 235),
) -> None:
    """绘制一个不依赖字体图标的简单音符占位。"""
    cx, cy = center
    radius = size * 0.18
    stem_x = cx + radius * 0.9
    draw.ellipse(
        (cx - radius, cy + radius * 0.25, cx + radius, cy + radius * 1.7),
        fill=fill,
    )
    draw.rounded_rectangle(
        (stem_x - size * 0.035, cy - size * 0.34, stem_x + size * 0.035, cy + radius * 1.0),
        radius=size * 0.035,
        fill=fill,
    )


def _soft_glow(
    canvas: Image.Image,
    center: tuple[int, int],
    radius: int,
    color: tuple[int, int, int, int],
) -> None:
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.ellipse(
        (
            center[0] - radius,
            center[1] - radius,
            center[0] + radius,
            center[1] + radius,
        ),
        fill=color,
    )
    overlay = overlay.filter(ImageFilter.GaussianBlur(radius * 0.65))
    canvas.alpha_composite(overlay)


def _draw_background(canvas: Image.Image) -> None:
    draw = ImageDraw.Draw(canvas)

    # 柔和光斑
    _soft_glow(canvas, (790, 90), 190, (178, 232, 255, 90))
    _soft_glow(canvas, (170, 520), 230, (255, 205, 238, 72))
    _soft_glow(canvas, (930, 470), 170, (226, 212, 255, 76))

    # 星星
    stars = (
        (95, 42),
        (205, 28),
        (352, 68),
        (536, 24),
        (735, 40),
        (902, 58),
        (55, 165),
        (934, 168),
    )
    for x, y in stars:
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(255, 255, 255, 205))

    # 月亮
    moon_x, moon_y, moon_r = 78, 88, 38
    draw.ellipse(
        (moon_x - moon_r, moon_y - moon_r, moon_x + moon_r, moon_y + moon_r),
        fill=(255, 249, 234, 235),
    )
    draw.ellipse(
        (moon_x - moon_r + 8, moon_y - 4, moon_x + moon_r + 12, moon_y + moon_r + 8),
        fill=(244, 240, 232, 210),
    )
    draw.ellipse(
        (moon_x - moon_r + 13, moon_y - 7, moon_x + moon_r + 9, moon_y + moon_r + 5),
        fill=(250, 246, 236, 235),
    )

    # 云朵
    cloud_color = (255, 255, 255, 175)
    for cx, cy, scale in ((240, 104, 1.0), (650, 72, 0.8), (960, 250, 1.1), (140, 430, 0.9)):
        width = int(140 * scale)
        height = int(48 * scale)
        draw.ellipse(
            (cx - width, cy - height // 2, cx + width, cy + height // 2),
            fill=cloud_color,
        )
        draw.ellipse(
            (cx - width // 2, cy - height, cx + width // 2, cy),
            fill=cloud_color,
        )
        draw.ellipse(
            (cx + width // 4, cy - int(height * 0.65), cx + int(width * 0.75), cy + int(height * 0.35)),
            fill=cloud_color,
        )

    # 底部彩虹
    rainbow_colors = (
        (255, 191, 211, 170),
        (255, 215, 175, 165),
        (255, 237, 167, 160),
        (187, 231, 190, 155),
        (162, 220, 244, 150),
        (205, 189, 248, 150),
    )
    for index, color in enumerate(rainbow_colors):
        gap = 16
        radius = 235 - index * gap
        draw.arc(
            (540 - radius, 500 - radius, 540 + radius, 500 + radius),
            start=195,
            end=345,
            fill=color,
            width=max(6, 14 - index),
        )


def _draw_badge(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, color: tuple[int, int, int]) -> None:
    x, y = xy
    font = _font(22)
    padding_x, padding_y = 14, 7
    width = int(draw.textlength(text, font=font)) + padding_x * 2
    height = 36
    draw.rounded_rectangle(
        (x, y, x + width, y + height),
        radius=height // 2,
        fill=color,
    )
    draw.text(
        (x + width / 2, y + height / 2),
        text,
        font=font,
        fill=TEXT_WHITE,
        anchor="mm",
    )
    return width, height


def _draw_info_row(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    label: str,
    value: str,
    *,
    label_color: tuple[int, int, int] = (127, 116, 157),
    value_color: tuple[int, int, int] = TEXT_DARK,
    value_font_size: int = 20,
) -> None:
    x, y = xy
    label_font = _font(18)
    value_font = _font(value_font_size)
    draw.text((x, y), label, font=label_font, fill=label_color)
    value_x = x + 92
    draw.text((value_x, y - 1), value, font=value_font, fill=value_color)


def render_task_card(data: TaskCardData, output_path: Path, size: tuple[int, int] = (1000, 620)) -> Path:
    """渲染任务卡并保存 PNG，返回输出路径。"""
    width, height = size
    canvas = _vertical_gradient(size).convert("RGBA")
    _draw_background(canvas)
    draw = ImageDraw.Draw(canvas)

    # 中央白色半透明内容卡
    card = (64, 76, width - 64, height - 76)
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (card[0] + 6, card[1] + 12, card[2] + 6, card[3] + 12),
        radius=34,
        fill=(120, 100, 175, 45),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(12))
    canvas.alpha_composite(shadow)
    draw.rounded_rectangle(card, radius=34, fill=(255, 255, 255, 236))

    # 顶部标题
    title_font = _font(38)
    title = "随机任务"
    title_width = draw.textlength(title, font=title_font)
    draw.text(
        ((card[0] + card[2]) / 2, card[1] + 46),
        title,
        font=title_font,
        fill=(99, 82, 159),
        anchor="mm",
    )

    kind = str(data.kind or "normal").lower()
    kind_label = KIND_LABELS.get(kind, data.kind)
    kind_color = KIND_COLORS.get(kind, KIND_COLORS["normal"])
    _draw_badge(draw, (card[0] + 30, card[1] + 24), kind_label, kind_color)

    id_font = _font(20)
    id_text = f"任务ID：#{data.task_id}"
    draw.text(
        (card[2] - 30, card[1] + 30),
        id_text,
        font=id_font,
        fill=TEXT_MUTED,
        anchor="rm",
    )

    # 曲绘
    cover_size = (196, 196)
    cover_x = card[0] + 42
    cover_y = card[1] + 118
    cover = _rounded_cover(data, cover_size)
    draw.rounded_rectangle(
        (cover_x - 5, cover_y - 5, cover_x + cover_size[0] + 4, cover_y + cover_size[1] + 4),
        radius=26,
        fill=(255, 255, 255, 255),
        outline=(218, 208, 242, 255),
        width=2,
    )
    canvas.paste(cover, (cover_x, cover_y), cover)

    # 曲目信息
    info_x = cover_x + cover_size[0] + 38
    info_y = cover_y + 4
    game_label = GAME_LABELS.get(str(data.game).lower(), data.game)
    game_font = _font(20)
    draw.text((info_x, info_y), game_label, font=game_font, fill=(95, 132, 201))

    song_font = _font(31)
    song_y = info_y + 32
    song_title = str(data.title or "未知曲目")
    draw.text((info_x, song_y), song_title, font=song_font, fill=TEXT_DARK)

    artist_font = _font(20)
    artist_y = song_y + 46
    draw.text((info_x, artist_y), str(data.artist or "未知艺术家"), font=artist_font, fill=TEXT_MUTED)

    row_y = artist_y + 48
    level_label = "任务谱面" if str(data.kind or "").lower() != "normal" else "最高难度"
    _draw_info_row(draw, (info_x, row_y), level_label, str(data.level or "-"), value_font_size=23)
    _draw_info_row(draw, (info_x, row_y + 40), "任务要求", str(data.requirement or "任意难度"), value_font_size=22)
    _draw_info_row(draw, (info_x, row_y + 80), "完成奖励", f"{data.reward} 点", value_font_size=23)

    # 接取人
    if data.user_id:
        user_font = _font(18)
        draw.text(
            (card[0] + 42, card[3] - 46),
            f"接取人：{data.user_id}",
            font=user_font,
            fill=TEXT_MUTED,
        )

    if data.note:
        note_font = _font(17)
        draw.text(
            (card[0] + 320, card[3] - 46),
            data.note,
            font=note_font,
            fill=TEXT_MUTED,
        )

    # 底部品牌
    brand_font = _font(16)
    draw.text(
        (card[2] - 30, card[3] - 42),
        "MaiBot · 随机任务",
        font=brand_font,
        fill=(166, 156, 194),
        anchor="rm",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, "PNG")
    return output_path


if __name__ == "__main__":
    demo = TaskCardData(
        task_id="20260908-001",
        kind="challenge",
        game="maimai",
        title="Grievous Lady",
        artist="Team Grimoire",
        level="14+ (定数 14.6)",
        requirement="S 及以上",
        reward=30,
        user_id="123456789",
        note="请完成对应谱面并发送成绩截图",
    )
    render_task_card(demo, Path("output/task_card_preview.png"))
