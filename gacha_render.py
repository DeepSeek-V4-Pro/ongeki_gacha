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

BRAND_TEXT = "O.N.G.E.K.I"
BRAND_COLOR = (30, 60, 110, 255)
POWERED_TEXT = "MaiBot"
POWERED_COLOR = (75, 85, 110, 255)

INFO_FOOTER_HEIGHT_RATIO = 0.045
INFO_FOOTER_FONT_RATIO = 0.030
INFO_FOOTER_ALPHA = 128

OVERLAY_FILES = {
    "star_filled": "UI_Card_star_00.webp",
    "star_empty": "UI_Card_star_01.webp",
    "max_mark": "UI_Card_max_00.webp",
    "kaika": "UI_CMN_PrintMark_01_kaika.webp",
    "cho_kaika": "UI_CMN_PrintMark_02_tyoukaika.webp",
}

# 网站补齐的 298 张卡没有透明外框；这里按稀有度选择一张同版本正常卡
# 作为透明轮廓参考，避免这些卡在合成结果里比其他卡大一圈。
CARD_ALPHA_REFERENCE_FILES = {
    "N": "ui_card_100001.png",
    "R": "ui_card_102469.png",
    "SR": "ui_card_102415.png",
    "SRPlus": "ui_card_102738.png",
    "SSR": "ui_card_102049.png",
}


@dataclass(frozen=True)
class RenderCard:
    """渲染单张卡牌所需的完整状态。"""

    card: CardInfo
    copies: int
    is_kaika: bool
    is_cho_kaika: bool


class GachaRenderer:
    """用 Pillow 合成 1/5/11 连结果图。"""

    def __init__(self, cards_dir: Path, ui_dir: Path) -> None:
        self._cards_dir = cards_dir
        self._ui_dir = ui_dir
        self._fonts: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
        self._assets: dict[str, Image.Image] = {}
        self._card_alpha_masks: dict[str, Image.Image] = {}

    def _font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        if size in self._fonts:
            return self._fonts[size]
        candidates = (
            self._ui_dir / "SEGA_Humming_v2-B.ttf",
            Path("C:/Windows/Fonts/msyhbd.ttc"),
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
        )
        font = ImageFont.load_default()
        for path in candidates:
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
        image = None
        if path.is_file():
            try:
                with Image.open(path) as image_file:
                    image = image_file.convert("RGBA")
            except (OSError, ValueError):
                image = None
        if image is not None:
            if image.getchannel("A").getextrema() == (255, 255):
                mask = self._card_alpha_masks.get(card.rarity)
                if mask is None:
                    reference_name = CARD_ALPHA_REFERENCE_FILES.get(card.rarity, "ui_card_000001.png")
                    reference_path = self._cards_dir / reference_name
                    if not reference_path.is_file():
                        reference_path = self._cards_dir / "ui_card_000001.png"
                    if reference_path.is_file():
                        with Image.open(reference_path) as reference:
                            mask = reference.convert("RGBA").split()[3]
                        self._card_alpha_masks[card.rarity] = mask
                if mask is not None:
                    if mask.size != image.size:
                        mask = mask.resize(image.size, Image.Resampling.LANCZOS)
                    image.putalpha(mask)
            return image

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
        top = (247, 250, 255, 255)
        bottom = (214, 228, 245, 255)
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

    def _draw_card_info_footer(
        self,
        canvas: Image.Image,
        card_x: int,
        card_y: int,
        card_w: int,
        card_h: int,
        card: CardInfo,
    ) -> None:
        """Draw the RinNET-style ID/card-number footer on one result card."""
        label = str(card.id)
        card_number = str(card.card_number or "").strip()
        if card_number:
            label += " " + card_number
        elif card.version:
            label += " " + str(card.version)

        font_size = max(8, round(card_w * INFO_FOOTER_FONT_RATIO))
        font = self._font(font_size)
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        while font_size > 8 and probe.textlength(label, font=font) > card_w - 8:
            font_size -= 1
            font = self._font(font_size)

        footer_h = max(
            round(card_h * INFO_FOOTER_HEIGHT_RATIO),
            round(font_size * 1.5),
        )
        overlay = Image.new("RGBA", (card_w, footer_h), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rectangle(
            (0, 0, card_w - 1, footer_h - 1),
            fill=(0, 0, 0, INFO_FOOTER_ALPHA),
        )
        overlay_draw.text(
            (card_w / 2, footer_h / 2),
            label,
            font=font,
            fill=(255, 255, 255, 255),
            anchor="mm",
        )
        canvas.alpha_composite(
            overlay,
            (card_x, card_y + card_h - footer_h),
        )

    def render(
        self,
        states: list[RenderCard],
        output_path: Path,
    ) -> bytes:
        """将抽卡状态绘制成 PNG，并返回文件字节。"""
        if not states:
            raise ValueError("没有可渲染的卡牌")

        margin = 16
        gap = 12
        header_height = 64
        card_w = 600 if len(states) == 1 else 330
        card_h = round(card_w * 1052 / 768)

        if len(states) == 1:
            rows = [states]
        elif len(states) == 5:
            rows = [states[:2], states[2:]]
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
            + margin * 2
        )
        canvas = self._vertical_gradient(canvas_width, canvas_height)
        draw = ImageDraw.Draw(canvas)

        brand_font = self._font(26)
        powered_font = self._font(15)
        draw.text(
            (margin + 8, 16),
            BRAND_TEXT,
            font=brand_font,
            fill=BRAND_COLOR,
        )
        powered_width = draw.textlength(POWERED_TEXT, font=powered_font)
        draw.text(
            (canvas_width - margin - powered_width - 8, 24),
            POWERED_TEXT,
            font=powered_font,
            fill=POWERED_COLOR,
        )

        row_start_y = header_height + margin
        for row_index, row in enumerate(rows):
            row_width = len(row) * card_w + (len(row) - 1) * gap
            row_x = margin + (canvas_width - margin * 2 - row_width) // 2
            for card_index, state in enumerate(row):
                card_x = row_x + card_index * (card_w + gap)
                card_y = row_start_y + row_index * (card_h + gap)
                card_image = self._resize(self._load_card(state.card), (card_w, card_h))
                canvas.paste(card_image, (card_x, card_y), card_image)
                draw.rectangle(
                    (card_x - 1, card_y - 1, card_x + card_w, card_y + card_h),
                    outline=(45, 55, 85, 235),
                    width=2,
                )
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
                self._draw_card_info_footer(
                    canvas,
                    card_x,
                    card_y,
                    card_w,
                    card_h,
                    state.card,
                )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.convert("RGB").save(output_path, format="PNG", optimize=True)
        return output_path.read_bytes()
