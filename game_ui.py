"""复用原作界面素材；窄面板保留四角和上下端帽。"""
from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageOps
from . import render_theme as theme
from .render_components import contain

ROOT = Path(__file__).parent / 'assets/growth/images'
INTIMATE = ROOT / 'intimate'
GLYPH_INDEX = {str(digit): digit for digit in range(10)}
GLYPH_INDEX.update({'+': 10, '-': 11, '.': 12, '/': 13})


def asset(image, key, box):
    with Image.open(ROOT / f'{key}.png') as source:
        contain(image, source, box)


def panel(image, box):
    """干净的白底卡片：原来 9-slice 原作边框在上缘会留下缺口，这里改为自绘。"""
    left, top, right, bottom = box
    shadow = Image.new('RGBA', image.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (left + 4, top + 8, right + 4, bottom + 8), radius=26, fill=(28, 36, 56, 46))
    image.paste(Image.alpha_composite(image.convert('RGBA'), shadow).convert('RGB'), (0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((left, top, right, bottom), radius=24, fill='#FFFFFF')
    draw.rounded_rectangle((left, top, right, bottom), radius=24, outline='#D9E2EF', width=2)


def heading(image, label, box):
    asset(image, 'ui_category', box)
    left, top, right, bottom = box
    ImageDraw.Draw(image).text(((left + right) // 2, (top + bottom) // 2 - 3), label,
        font=theme.font(34, True), fill='white', anchor='mm')


def character_stage(image, box):
    """教室仅用于人物区；等比 cover、居中裁切，直角贴合卡片内沿。"""
    left, top, right, bottom = box
    with Image.open(ROOT / 'ui_character_background.png') as source:
        stage = ImageOps.fit(source.convert('RGB'), (right-left, bottom-top),
                             method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    image.paste(stage, (left, top))


def _intimate(name):
    path = INTIMATE / f'{name}.png'
    return Image.open(path).convert('RGBA') if path.is_file() else None


@lru_cache(maxsize=4)
def glyphs(atlas):
    """按透明间隔切出原作数字图集；顺序为 0-9 与 + - . /"""
    path = INTIMATE / f'{atlas}.png'
    if not path.is_file():
        return []

    def spans(mask, axis):
        length = mask.size[0] if axis == 0 else mask.size[1]
        flags = []
        for index in range(length):
            line = ([mask.getpixel((index, y)) for y in range(mask.height)] if axis == 0
                    else [mask.getpixel((x, index)) for x in range(mask.width)])
            flags.append(any(line))
        out, start = [], None
        for index, filled in enumerate(flags):
            if filled and start is None:
                start = index
            elif not filled and start is not None:
                out.append((start, index)); start = None
        if start is not None:
            out.append((start, length))
        return out

    source = Image.open(path).convert('RGBA')
    alpha = source.getchannel('A')
    tiles = []
    for top, bottom in spans(alpha, 1):
        band = alpha.crop((0, top, source.width, bottom))
        for left, right in spans(band, 0):
            tile = source.crop((left, top, right, bottom))
            tiles.append(tile.crop(tile.getchannel('A').getbbox()))
    return tiles


def draw_digits(image, xy, text, atlas='57pt_Friendship_Level', height=60, gap=2):
    """用原作图集拼数字，返回绘制后的右边界。"""
    tiles = glyphs(atlas)
    x, y = xy
    for char in str(text):
        index = GLYPH_INDEX.get(char)
        if index is None or index >= len(tiles):
            continue
        tile = tiles[index]
        scale = height / tile.height
        shown = tile.resize((max(1, round(tile.width * scale)), height), Image.Resampling.LANCZOS)
        image.paste(shown, (x, y), shown)
        x += shown.width + gap
    return x


def heart(image, box, ratio, *, level=0, tier=0, meter=None, finish=False):
    """按原作坐标拼装后整体缩放，数字始终位于心内，挡位贴合左上缘。

    量表分档：普通档 GaugeBase，10 档 GaugeBase_10，1000 级后叠加
    GaugeBase_Rebirth_10 挡位底图；填充使用原作 Pink/Yellow 心形素材。
    """
    local_level = level % 100
    decorated = local_level >= 10 or tier >= 10
    base = _intimate('GaugeBase_10' if decorated else 'GaugeBase') or _intimate('GaugeBase')
    if base is None:
        return
    base = base.copy()
    red, green, blue, alpha = base.split()
    interior = ImageChops.lighter(ImageChops.lighter(red, green), blue).point(
        lambda value: 255 if value < 190 else 0)
    region = Image.new('L', base.size)
    ImageDraw.Draw(region).rectangle((140, 150, 286, 276), fill=255)
    interior = ImageChops.multiply(ImageChops.multiply(interior, alpha), region)
    # 原作心形彩底：1000 级前用 Pink，1000 级后大小两个心都用 Rebirth_10 彩底；
    # 不使用 Yellow 满档素材。按实际进度裁切出水平液面。
    meter = meter or ('GaugeBase_Rebirth_10' if tier >= 10 else 'Gauge_Meter_Pink')
    meter_image = _intimate(meter)
    fill = Image.new('RGBA', base.size)
    # 以主心内腔的实际边界定位；Rebirth 自带透明边距，不能直接拉伸整图。
    cavity = interior.getbbox() or (140, 150, 286, 276)
    left, top, right, bottom = cavity
    if meter_image is not None:
        pattern = meter_image.convert('RGBA')
        bounds = pattern.getchannel('A').getbbox()
        if bounds:
            pattern = pattern.crop(bounds)
        pattern = pattern.resize((right-left, bottom-top), Image.Resampling.LANCZOS)
        fill.alpha_composite(pattern, (left, top))
    else:
        ImageDraw.Draw(fill).rectangle(cavity, fill=(249, 90, 217, 255))
    liquid = Image.new('L', base.size, 0)
    level_height = round((bottom-top) * max(0.0, min(float(ratio), 1.0)))
    if level_height:
        ImageDraw.Draw(liquid).rectangle(
            (left, bottom-level_height, right-1, bottom-1), fill=255)
    fill.putalpha(ImageChops.multiply(fill.getchannel('A'), ImageChops.multiply(interior, liquid)))
    base = Image.alpha_composite(base, fill)

    def number(text, atlas, bounds, desired_height):
        tiles = glyphs(atlas)
        def measure(height):
            return sum(round(tiles[GLYPH_INDEX[c]].width*height/tiles[GLYPH_INDEX[c]].height)
                       for c in text) + 2*(len(text)-1)
        left, top, right, bottom = bounds
        height = min(desired_height, bottom-top)
        while height > 8 and measure(height) > right-left:
            height -= 1
        draw_digits(base, (left+(right-left-measure(height))//2, top+(bottom-top-height)//2),
                    text, atlas=atlas, height=height)

    # 内腔的宽处（避开下方尖角），四周留白；不再按带光晕的整图尺寸计算字号。
    number(f'{level % 100:02d}', '57pt_Friendship_Level', (170,181,254,231), 44)
    if tier > 0:
        plate = _intimate('GaugeBase_Rebirth_10' if tier >= 10 else 'GaugeBase_Rebirth')
        if plate is not None:
            plate = plate.crop(plate.getchannel('A').getbbox()).resize((56,53), Image.Resampling.LANCZOS)
            base.alpha_composite(plate, (116,132))
        number(str(tier), '24pt_Friendship_Level', (122,141,166,165), 20)
    if finish:
        banner = _intimate('Gauge_Finish')
        if banner is not None:
            banner = banner.convert('RGBA').resize((196, 37), Image.Resampling.LANCZOS)
            base.alpha_composite(banner, (114, 300))
    # 径向淡出光晕；方形边缘淡出会把粉色背景变成明显的方块。
    shown = base.crop((76, 76, 348, 348))
    feather = Image.new('L', shown.size)
    feather.putdata([
        round(255 * max(0, min(1, (136-((x-136)**2+(y-129)**2)**0.5)/40)))
        for y in range(272) for x in range(272)
    ])
    shown.putalpha(ImageChops.multiply(shown.getchannel('A'), feather))
    contain(image, shown, box)



def progress_bar(image, box, ratio, cid):
    """细进度条补足心形小面积填充的辨识度。"""
    left, top, right, bottom = box
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(box, radius=3, fill='#E5EAF3')
    width = round((right-left) * max(0, min(ratio, 1)))
    if width:
        draw.rounded_rectangle((left, top, left+width, bottom), radius=3,
                               fill=theme.theme_for(cid)['primary'])
