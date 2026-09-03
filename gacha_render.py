"""抽卡结果图片合成。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .gacha_core import CardInfo, max_detail_slots


RARITY_COLORS: dict[str, str] = {
    "N": "#b8b8c4",
    "R": "#7da7ff",
    "SR": "#57b6ff",
    "SRPlus": "#7be8ff",
    "SSR": "#ffd35c",
}

OVERLAY_FILES = {
    "star_filled": "UI_Card_star_00.webp",
    "star_empty": "UI_Card_star_01.webp",
    "max_mark": "UI_Card_max_00.webp",
    "kaika": "UI_CMN_PrintMark_01_kaika.webp",
    "cho_kaika": "UI_CMN_PrintMark_02_tyoukaika.webp",
}


@dataclass(frozen=True)
class RenderCard:
    """渲染单张卡牌所需的完整状态。"""

    card: CardInfo
    copies: int
    is_new: bool
    is_kaika: bool
    is_cho_kaika: bool


class GachaRenderer:
    """用 Pillow 合成 1/5/11 连结果图。"""

    def __init__(self, cards_dir: Path, ui_dir: Path) -> None:
        self._cards_dir = cards_dir
        self._ui_dir = ui_dir
        self._fonts: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
        self._assets: dict[str, Image.Image] = {}

    def _font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        if size in self._fonts:
            return self._fonts[size]
        candidates = (
            (Path("C:/Windows/Fonts/msyhbd.ttc"), True),
            (Path("C:/Windows/Fonts/msyh.ttc"), False),
            (Path("C:/Windows/Fonts/simhei.ttf"), False),
        )
        font = ImageFont.load_default()
        for path, is_bold in candidates:
            if path.is_file():
                try:
                    font = ImageFont.truetype(str(path), size=size)
                    break
                except OSError:
                    continue
        self._fonts[size] = font
        return font

    def _asset(self, name: str) -> Image.Image:
        if name not in self._assets:
            filename = OVERLAY_FILES[name]
            path = self._ui_dir / filename
            with Image.open(path) as image:
                self._assets[name] = image.convert("RGBA")
        return self._assets[name]

    def _load_card(self, card: CardInfo) -> Image.Image:
        path = self._cards_dir / card.image_file
        if path.is_file():
            with Image.open(path) as image:
                return image.convert("RGBA")

        color = RARITY_COLORS.get(card.rarity, "#888888")
        image = Image.new("RGBA", (768, 1052), color)
        draw = ImageDraw.Draw(image)
        draw.text((70, 480), f"Missing Card {card.id}", fill="#ffffff", font=self._font(42))
        return image

    @staticmethod
    def _resize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
        return image.resize(size, Image.Resampling.LANCZOS)

    @staticmethod
    def _vertical_gradient(width: int, height: int) -> Image.Image:
        image = Image.new("RGBA", (width, height))
        draw = ImageDraw.Draw(image)
        top = (20, 22, 36, 255)
        bottom = (34, 38, 60, 255)
        for y in range(height):
            ratio = y / max(height - 1, 1)
            color = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(4))
            draw.line((0, y, width, y), fill=color)
        return image

    def _paste_scaled(
        self,
        canvas: Image.Image,
        source: Image.Image,
        size: tuple[int, int],
        position: tuple[int, int],
    ) -> None:
        scaled = self._resize(source, size)
        canvas.paste(scaled, position, scaled)

    def _draw_stars(
        self,
        canvas: Image.Image,
        card_x: int,
        card_y: int,
        card_w: int,
        card_h: int,
        rarity: str,
        copies: int,
    ) -> None:
        max_slots = max_detail_slots(rarity)
        stars = min(max(copies, 0), max_slots)
        star_size = max(8, int(card_h * 0.047))
        gap = max(1, int(star_size * 0.10))
        total_width = max_slots * star_size + (max_slots - 1) * gap
        x = card_x + (card_w - total_width) // 2
        y = card_y + int(card_h * 0.75)
        filled = self._asset("star_filled")
        empty = self._asset("star_empty")
        for index in range(max_slots):
            asset = filled if index < stars else empty
            self._paste_scaled(
                canvas,
                asset,
                (star_size, star_size),
                (x + index * (star_size + gap), y),
            )

        if stars >= max_slots:
            max_mark = self._asset("max_mark")
            mark_height = max(8, int(star_size * 0.70))
            mark_width = int(mark_height * 108 / 34)
            self._paste_scaled(
                canvas,
                max_mark,
                (mark_width, mark_height),
                (card_x + (card_w - mark_width) // 2, y + star_size + 2),
            )

    def _draw_growth_mark(
        self,
        canvas: Image.Image,
        card_x: int,
        card_y: int,
        card_w: int,
        card_h: int,
        is_kaika: bool,
        is_cho_kaika: bool,
    ) -> None:
        if not is_kaika:
            return
        if is_cho_kaika:
            mark = self._asset("cho_kaika")
        else:
            mark = self._asset("kaika")
        mark_h = max(10, int(card_h * 0.12))
        mark_w = int(mark_h * 174 / 152)
        self._paste_scaled(
            canvas,
            mark,
            (mark_w, mark_h),
            (card_x + card_w - mark_w - 8, card_y + 8),
        )

    def render(
        self,
        states: list[RenderCard],
        output_path: Path,
        *,
        footer_text: str = "",
    ) -> bytes:
        """将抽卡状态绘制成 PNG，并返回文件字节。"""
        if not states:
            raise ValueError("没有可渲染的卡牌")

        margin = 16
        gap = 12
        header_height = 64
        footer_height = 36
        card_w = 600 if len(states) == 1 else 330
        card_h = round(card_w * 1052 / 768)

        if len(states) == 1:
            rows = [states]
        elif len(states) == 5:
            rows = [states]
        elif len(states) == 11:
            rows = [states[:4], states[4:8], states[8:]]
        else:
            rows = [states[index : index + 4] for index in range(0, len(states), 4)]

        columns = max(len(row) for row in rows)
        canvas_width = margin * 2 + columns * card_w + (columns - 1) * gap
        canvas_height = (
            header_height
            + len(rows) * card_h
            + (len(rows) - 1) * gap
            + footer_height
            + margin * 2
        )
        canvas = self._vertical_gradient(canvas_width, canvas_height)
        draw = ImageDraw.Draw(canvas)

        title_font = self._font(34)
        draw.text((margin + 4, 18), "ONGEKI 抽卡结果", fill="#ffffff", font=title_font)
        if footer_text:
            footer_font = self._font(20)
            draw.text((margin + 4, canvas_height - footer_height + 6), footer_text, fill="#d7d7e8", font=footer_font)

        row_start_y = header_height + margin
        for row_index, row in enumerate(rows):
            row_width = len(row) * card_w + (len(row) - 1) * gap
            row_x = margin + (canvas_width - margin * 2 - row_width) // 2
            for card_index, state in enumerate(row):
                card_x = row_x + card_index * (card_w + gap)
                card_y = row_start_y + row_index * (card_h + gap)
                card_image = self._resize(self._load_card(state.card), (card_w, card_h))
                canvas.paste(card_image, (card_x, card_y), card_image)
                self._draw_growth_mark(
                    canvas,
                    card_x,
                    card_y,
                    card_w,
                    card_h,
                    state.is_kaika,
                    state.is_cho_kaika,
                )
                self._draw_stars(
                    canvas,
                    card_x,
                    card_y,
                    card_w,
                    card_h,
                    state.card.rarity,
                    state.copies,
                )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.convert("RGB").save(output_path, format="PNG", optimize=True)
        return output_path.read_bytes()
