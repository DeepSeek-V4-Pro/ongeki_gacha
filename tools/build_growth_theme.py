"""生成本次界面改版需要的资源（可重复执行覆盖）。

产出 character_theme.json（每个主角色的主题色，用于好感页与奖励页的渐变背景）。
好感页右上角的 Q 版直接用 assets/growth/images/character_角色ID.png
（＝原作 ui_friendship_bt_* 的基础帧），满级后换成 reward_attachment_*.png。
"""
from __future__ import annotations

import json
import colorsys
from pathlib import Path

from PIL import Image

from ..starter_cards import STARTER_CARDS

THEME_VERSION = 'growth-theme-v1'
# 手工校验过的角色主色：以立绘高饱和簇为依据，按角色印象色定稿
MANUAL_PRIMARY = {
    1000: '#E2738F',   # 星咲あかり 粉
    1001: '#C9C24F',   # 藤沢柚子 柚黄
    1002: '#C4668C',   # 三角葵 洋红
    1003: '#8A6FB5',   # 高瀬梨緒 紫
    1004: '#C06F93',   # 結城莉玖 桃
    1005: '#2E7F9A',   # 藍原椿 蓝绿
    1006: '#4E9A62',   # 早乙女彩華 绿
    1007: '#E0899B',   # 桜井春菜 浅粉
    1008: '#6F76B5',   # 九條楓 蓝紫
    1009: '#DE8FA8',   # 柏木咲姫 玫瑰
    1010: '#4E9BC9',   # 井之原小星 天蓝
    1011: '#C2455F',   # 逢坂茜 绯红
    1012: '#6FB4DD',   # 珠洲島有栖 水蓝
    1013: '#DE7F9D',   # 柏木美亜 粉
    1014: '#D8A32F',   # 日向千夏 金
    1015: '#4A4A85',   # 東雲つむぎ 深蓝紫
    1016: '#9B87D8',   # 皇城セナ 淡紫
}


def hex_mix(base, target, ratio: float) -> str:
    values = [round(base[index] + (target[index] - base[index]) * ratio) for index in range(3)]
    return '#%02X%02X%02X' % tuple(max(0, min(255, value)) for value in values)


def theme_colors(source: Image.Image) -> dict[str, str]:
    """按色相直方图取主色，再提升饱和度，避免平均值发灰。"""
    portrait = source.convert('RGBA')
    bounds = portrait.getchannel('A').getbbox()
    if bounds:
        left, top, right, bottom = bounds
        height = bottom - top
        # 服装区域更能代表角色配色，避开脸部与头发的肤色偏置
        crop = portrait.crop((left, top + round(height * 0.30), right, top + round(height * 0.95)))
    else:
        crop = portrait
    small = crop.resize((96, 96), Image.Resampling.LANCZOS)
    hsv = small.convert('HSV')
    pixels = list(small.getdata())
    hsv_pixels = list(hsv.getdata())
    bins = [0.0] * 36
    for (_, _, _, alpha), (hue, saturation, value) in zip(pixels, hsv_pixels):
        if alpha < 128 or saturation < 45 or value < 45:
            continue
        # 过滤肤色（低饱和的橙黄区间），避免整张图被肤色拉灰
        if 6 <= hue <= 34 and saturation < 110 and value > 120:
            continue
        bins[hue * 36 // 256] += (saturation / 255) * (value / 255)
    if any(bins):
        top = max(range(36), key=lambda index: bins[index])
        chosen = [pixel[:3] for pixel, (hue, saturation, value), (_, _, _, alpha) in
                  zip(pixels, hsv_pixels, pixels)
                  if alpha >= 128 and saturation >= 45 and value >= 45
                  and not (6 <= hue <= 34 and saturation < 110 and value > 120)
                  and abs(hue * 36 // 256 - top) <= 1]
    else:
        chosen = [pixel[:3] for pixel in pixels if pixel[3] > 128]
    chosen = chosen or [(180, 190, 205)]
    base = [sum(pixel[index] for pixel in chosen) / len(chosen) / 255 for index in range(3)]
    hue, saturation, value = colorsys.rgb_to_hsv(*base)
    base = list(colorsys.hsv_to_rgb(hue, min(1.0, saturation * 1.35), min(1.0, max(value, 0.72))))
    base = [channel * 255 for channel in base]

    def hex_of(rgb) -> str:
        return '#%02X%02X%02X' % tuple(max(0, min(255, round(value))) for value in rgb)

    def mix(target, ratio):
        return [base[index] + (target[index] - base[index]) * ratio for index in range(3)]

    return {'primary': hex_of(base),
            'light': hex_of(mix((255, 255, 255), 0.70)),
            'tint': hex_of(mix((255, 255, 255), 0.40)),
            'deep': hex_of(mix((28, 34, 52), 0.55))}


def main() -> None:
    root = Path(__file__).parents[1]
    images = root / 'assets/growth/images'
    themes: dict[str, dict[str, str]] = {}
    for cid in sorted(STARTER_CARDS):
        with Image.open(images / f'portrait_{cid}.png') as source:
            portrait = source.convert('RGBA')
            colors = theme_colors(portrait)
            manual = MANUAL_PRIMARY.get(cid)
            if manual:
                base = [int(manual[index:index + 2], 16) for index in (1, 3, 5)]
                colors['primary'] = manual
                colors['light'] = hex_mix(base, (255, 255, 255), 0.70)
                colors['tint'] = hex_mix(base, (255, 255, 255), 0.40)
                colors['deep'] = hex_mix(base, (28, 34, 52), 0.55)
            themes[str(cid)] = colors
    (root / 'assets/growth/character_theme.json').write_text(
        json.dumps({'version': THEME_VERSION, 'characters': themes},
                   ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(f'{len(themes)} 组角色主题色 -> {root / "assets/growth"}')
    for cid, colors in list(themes.items())[:3]:
        print(' ', cid, colors)


if __name__ == '__main__':
    main()
