"""原作风格卡牌获得/突破揭示图。

还原卡牌揭示的版式：稀有度标题、卡面、NEW CARD/STAR UP 横幅、
角色与卡名、属性、限界突破星级、MAX Lv 和 MAX 攻击力。
技能数据暂不展示。
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from .gacha_core import CardInfo, max_detail_slots, rarity_display

REVEAL_SIZE = (1080, 960)
DEFAULT_REVEAL_ASSETS = Path(__file__).parent / "assets/growth/images/card_reveal"
_REVEAL_ASSETS: dict[tuple[str, str], Image.Image] = {}

RARITY_ACCENT: dict[str, tuple[int, int, int]] = {
    "N": (184, 184, 196),
    "R": (125, 167, 255),
    "SR": (87, 182, 255),
    "SRPlus": (123, 232, 255),
    "SSR": (255, 211, 92),
}

ATTRIBUTE_LABELS = {
    "Fire": "Fire / 炎",
    "Aqua": "Aqua / 水",
    "Leaf": "Leaf / 叶",
    "Wind": "Wind / 风",
    "Ice": "Ice / 冰",
    "Light": "Light / 光",
    "Dark": "Dark / 暗",
    "All": "All / 全",
}

RARITY_ASSETS = {
    "N": "rare_N",
    "R": "rare_R",
    "SR": "rare_SR",
    "SRPlus": "rare_SRPlus",
    "SSR": "rare_SSR",
}

CLASS_ASSETS = {
    "N": "class_N",
    "R": "class_R",
    "SR": "class_SR",
    "SRPlus": "class_SRPlus",
    "SSR": "class_SSR",
}

FRAME_ASSETS = {
    "N": "frame_N",
    "R": "frame_R",
    "SR": "frame_SR",
    "SRPlus": "frame_SRPlus",
    "SSR": "frame_SSR",
}

ATTRIBUTE_ASSETS = {
    "Fire": "attr_Fire",
    "Aqua": "attr_Aqua",
    "Leaf": "attr_Leaf",
}


def _reveal_asset(asset_dir: Path, key: str) -> Image.Image | None:
    cache_key = (str(asset_dir), key)
    if cache_key in _REVEAL_ASSETS:
        return _REVEAL_ASSETS[cache_key]
    path = next((candidate for candidate in (asset_dir / f"{key}.png", asset_dir / f"{key}.webp") if candidate.is_file()), None)
    if path is None:
        return None
    with Image.open(path) as source:
        image = source.convert("RGBA")
    _REVEAL_ASSETS[cache_key] = image
    return image


def _font(renderer, size: int):
    return renderer._font(size)


def _gradient(width: int, height: int) -> Image.Image:
    image = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(image)
    top = (14, 20, 38, 255)
    bottom = (42, 30, 62, 255)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(4))
        draw.line((0, y, width, y), fill=color)
    return image


def _outlined_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font,
    fill,
    *,
    outline=(255, 255, 255, 255),
    width: int = 4,
    anchor: str = "mm",
) -> None:
    for dx in range(-width, width + 1):
        for dy in range(-width, width + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((xy[0] + dx, xy[1] + dy), text, font=font, fill=outline, anchor=anchor)
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def _max_level(card: CardInfo, copies: int, *, is_kaika: bool = False,
               is_cho_kaika: bool = False) -> str:
    """未解花时上限为 Lv10；解花后按持有星数提升至 Lv50–100。"""
    if not (is_kaika or is_cho_kaika):
        return "10"
    slots = max_detail_slots(card.rarity)
    stars = max(1, min(int(copies), slots))
    return str(50 + 5 * (stars - 1))


def _max_attack(card: CardInfo, copies: int, *, is_kaika: bool = False,
                is_cho_kaika: bool = False) -> str:
    """从 LevelParam 的 Lv1/Lv50/突破锚点求当前等级上限的攻击力。"""
    try:
        values = [int(part.strip()) for part in card.level_param.split(',')]
        if len(values) < 6 or any(value <= 0 for value in values[:6]):
            return "-"
    except ValueError:
        return "-"
    if not (is_kaika or is_cho_kaika):
        # Lv1 到 Lv50 线性增长；原作 Lv10 样例 50→222 得到 81。
        return str(values[0] + (values[1] - values[0]) * 9 // 49)
    level = int(_max_level(card, copies, is_kaika=True))
    if is_cho_kaika and len(values) >= 10 and values[9] > 0:
        return str(values[9])
    anchors = {50: values[1], 55: values[2], 60: values[3],
               65: values[4], 70: values[5]}
    if card.rarity == 'N' and len(values) >= 9:
        anchors.update({80: values[6], 90: values[7], 100: values[8]})
    if level in anchors and anchors[level] > 0:
        return str(anchors[level])
    lower = max((key for key in anchors if key < level and anchors[key] > 0), default=None)
    upper = min((key for key in anchors if key > level and anchors[key] > 0), default=None)
    if lower is None or upper is None:
        return "-"
    return str(anchors[lower] + (anchors[upper] - anchors[lower]) *
               (level - lower) // (upper - lower))


def _fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    box_w, box_h = size
    scale = min(box_w / image.width, box_h / image.height)
    shown = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    return shown


def _boost_alpha(image: Image.Image, factor: int = 6) -> Image.Image:
    """原作部分文字/缎带素材是低 alpha 叠加层，合成前提亮。"""
    boosted = image.copy()
    alpha = boosted.getchannel("A").point(lambda value: min(255, value * factor))
    boosted.putalpha(alpha)
    return boosted


def _wrap_name(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, *, max_lines: int = 2) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        probe = current + char
        if draw.textlength(probe, font=font) > max_width and current:
            lines.append(current)
            current = char
        else:
            current = probe
    if current:
        lines.append(current)
    if len(lines) > 1 and len(lines[-1]) <= 2:
        lines[-2] += lines[-1]
        lines.pop()
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        tail = lines[-1]
        while tail and draw.textlength(tail + "…", font=font) > max_width:
            tail = tail[:-1]
        lines[-1] = tail + "…"
    return lines


def render_card_reveal(
    renderer,
    card: CardInfo,
    copies: int,
    output_path: Path,
    *,
    mode: str = "new",
    before_copies: int = 0,
    is_kaika: bool = False,
    is_cho_kaika: bool = False,
    character_name: str = "",
    asset_dir: Path | None = None,
) -> bytes:
    """渲染一张卡牌揭示图并返回 PNG 字节。mode 为 new 或 star_up。"""
    width, height = REVEAL_SIZE
    assets = Path(asset_dir) if asset_dir is not None else DEFAULT_REVEAL_ASSETS
    accent = RARITY_ACCENT.get(card.rarity, (220, 200, 150))
    canvas = Image.new("RGBA", (width, height))
    # 照片中的暖金色透光底，保留几何层次，不复刻机台外壳和底层卡池文字。
    pixels = []
    for y in range(height):
        for x in range(width):
            glow = max(0, 1-(((x-520)/760)**2+((y-470)/650)**2))
            pixels.append((round(99+155*glow), round(76+163*glow), round(45+132*glow), 255))
    canvas.putdata(pixels)
    geometry = Image.new("RGBA", canvas.size)
    gd = ImageDraw.Draw(geometry)
    gd.polygon([(0, 170), (230, 170), (1080, 830), (1080, 960)], fill=(255,255,255,28))
    gd.polygon([(570, 0), (1080, 0), (1080, 520)], fill=(255,235,160,35))
    canvas.alpha_composite(geometry)
    texture = _reveal_asset(assets, "bg_card")
    if texture is not None:
        canvas.alpha_composite(texture.resize(canvas.size, Image.Resampling.LANCZOS))
    draw = ImageDraw.Draw(canvas)

    def paste_asset(key, box):
        source = _reveal_asset(assets, key)
        if source is None:
            return False
        bounds = source.getchannel("A").getbbox()
        if bounds:
            source = source.crop(bounds)
        shown = _fit(source, (box[2]-box[0], box[3]-box[1]))
        canvas.alpha_composite(shown, (box[0]+(box[2]-box[0]-shown.width)//2,
                                      box[1]+(box[3]-box[1]-shown.height)//2))
        return True

    # 横幅的青/黄斜切端部与中间半透明白底。
    draw.rectangle((0, 54, width, 158), fill=(244,244,240,235))
    for polygon, color in [([(0,54),(115,54),(57,158),(0,158)], '#00dcd6'),
                           ([(133,54),(205,54),(147,158),(75,158)], '#ffe000'),
                           ([(949,54),(1021,54),(963,158),(891,158)], '#ffe000'),
                           ([(1039,54),(1080,54),(1080,158),(981,158)], '#00dcd6')]:
        draw.polygon(polygon, fill=color)
    logo = _reveal_asset(assets, CLASS_ASSETS.get(card.rarity, "class_N"))
    word = _reveal_asset(assets, "title_text")
    if logo is not None and word is not None:
        logo = logo.crop(logo.getchannel('A').getbbox())
        word = word.crop(word.getchannel('A').getbbox())
        logo = _fit(logo, (330, 146))
        word = _fit(word, (240, 80))
        x = (width-logo.width-word.width-12)//2
        canvas.alpha_composite(logo, (x, 28))
        canvas.alpha_composite(word, (x+logo.width+12, 83))
    else:
        _outlined_text(draw, (540,108), f"{rarity_display(card.rarity)} CARD",
                       _font(renderer,84), 'white', outline='#25202a')

    # _load_card 已含属性、稀有度和原作卡框，只增加照片中的白边与光晕。
    art = _fit(renderer._load_card(card), (435, 625))
    card_layer = Image.new('RGBA', (art.width+14, art.height+14), 'white')
    card_layer.alpha_composite(art, (7,7))
    card_layer = card_layer.rotate(4, resample=Image.Resampling.BICUBIC, expand=True)
    art_x, art_y = 64, 238
    halo = Image.new('RGBA', canvas.size)
    halo.paste((255,255,221,255), (art_x,art_y), card_layer.getchannel('A'))
    canvas.alpha_composite(halo.filter(ImageFilter.GaussianBlur(15)))
    canvas.alpha_composite(card_layer, (art_x,art_y))

    # NEW CARD 横条，与照片一致向右延伸到画面边缘。
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((558, 254, width, 312), fill='#ff43c4' if mode == 'new' else '#e9b522')
    draw.polygon([(1038,254),(1080,254),(1080,312),(1004,312)], fill='#00dcd6')
    draw.text((574,282), 'NEW CARD !!' if mode == 'new' else 'STAR UP !!',
              font=_font(renderer,44), fill='white', anchor='lm', stroke_width=1)

    # 属性和 class 放在白色斜切名牌左侧，卡名、角色姓名位于右侧。
    draw.polygon([(602,363),(1008,363),(965,446),(559,446)], fill=(45,36,25,85))
    draw.polygon([(602,357),(1008,357),(965,438),(559,438)], fill='#f8f7fc')
    paste_asset(ATTRIBUTE_ASSETS.get(card.attribute, ''), (586,378,628,422))
    paste_asset(CLASS_ASSETS.get(card.rarity, 'class_N'), (627,378,684,422))
    label = re.sub(r'^【[^】]*】', '', card.name).strip()
    if character_name:
        label = label.replace(character_name, '').strip()
    for size in range(20, 11, -1):
        if _font(renderer,size).getlength(label) <= 268:
            break
    lines = _wrap_name(draw,label,_font(renderer,size),268,max_lines=1)
    draw.text((834,378), ''.join(lines), font=_font(renderer,size), fill='#302a34',anchor='mm')
    draw.line((710,393,963,393),fill='#b7aebb',width=1)
    name = character_name or card.name
    name_size = 30
    while name_size > 12 and _font(renderer,name_size).getlength(name) > 266:
        name_size -= 1
    draw.text((834,415), name, font=_font(renderer,name_size), fill='#25222b',anchor='mm')

    # 独立的限界突破缎带和数值底板，不再把整列包进圆角卡片。
    draw.rectangle((590, 542, 990, 721), fill=(61,43,20,65))
    draw.rectangle((584, 534, 984, 713), fill='#f9f7fc')
    draw.polygon([(552,551),(584,551),(584,608),(552,608),(565,579)], fill='#d7a600')
    draw.polygon([(984,551),(1016,551),(1003,579),(1016,608),(984,608)], fill='#d7a600')
    draw.polygon([(574,532),(774,522),(994,532),(994,606),(774,596),(574,606)], fill='#f4cc00')
    draw.line([(578,540),(774,530),(990,540)],fill='white',width=2)
    draw.line([(578,598),(774,588),(990,598)],fill='white',width=2)
    _outlined_text(draw,(784,547),'限界突破',_font(renderer,32),'#3c1723',width=2)
    max_slots = max_detail_slots(card.rarity)
    stars = max(0, min(int(copies), max_slots))
    star_size = 26 if max_slots <= 5 else 22
    gap = 2
    star_x = 784-(max_slots*star_size+(max_slots-1)*gap)//2
    for index in range(max_slots):
        cx = star_x+index*(star_size+gap)+star_size/2
        cy = 568+star_size/2
        points = [(cx+math.sin(j*math.pi/5)*(star_size/2 if j%2==0 else star_size/4.5),
                   cy-math.cos(j*math.pi/5)*(star_size/2 if j%2==0 else star_size/4.5))
                  for j in range(10)]
        draw.polygon(points, fill='#ffe879' if index < stars else '#241c11', outline='#795009')
    draw.polygon([(616,623),(729,623),(739,648),(626,648)],fill='#f3cc00')
    draw.text((677,635),'MAX Lv.',font=_font(renderer,20),fill='#34302b',anchor='mm')
    draw.text((858,635),_max_level(card, copies, is_kaika=is_kaika,
                                  is_cho_kaika=is_cho_kaika),font=_font(renderer,28),fill='#34302b',anchor='mm')
    draw.line((626,654,950,654),fill='#d7c36e',width=1)
    draw.text((677,678),'MAX 攻击力',font=_font(renderer,20),fill='#34302b',anchor='mm')
    draw.text((858,678),_max_attack(card, copies, is_kaika=is_kaika,
                                   is_cho_kaika=is_cho_kaika),font=_font(renderer,28),fill='#34302b',anchor='mm')
    if mode == 'star_up':
        draw.text((784,747),f"{max(0,int(before_copies))} → {int(copies)} 星",
                  font=_font(renderer,23),fill='#604113',anchor='mm')
    footer = f"ID {card.id}" + (f"  {card.card_number}" if card.card_number else '')
    draw.text((width-40,height-28),footer,font=_font(renderer,16),fill='#f2e7cb',anchor='rm')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, format="PNG", optimize=True)
    return output_path.read_bytes()
