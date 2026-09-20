"""统一图片主题。

字体优先使用随包 Noto Sans CJK；发布包不含素材时回退到系统自带的中日文字体，
两者都没有才判定为“无法渲染”，由命令层改为纯文字回复。
"""
import json
from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

VERSION = "collection-v1-preview"
WIDTH = 1080
MARGIN = 48
BACKGROUND = "#F5F7FC"
SURFACE = "#FFFFFF"
TEXT = "#202A44"
MUTED = "#596780"
CYAN = "#168EAF"
PINK = "#D94D88"
SUCCESS = "#247A5A"
WARNING = "#946100"
FAILURE = "#BC354B"
RARITY = {"N": "#707887", "R": "#3876BE", "SR": "#168EAF", "SRPlus": "#8463B4", "SSR": "#946100"}
DEFAULT_THEME = {"primary": "#168EAF", "light": "#EAF4F8", "tint": "#D6E8F0", "deep": "#1E3A4A"}
FONT_FILES = ("NotoSansCJKsc-Regular.otf", "NotoSansCJKsc-Bold.otf")
# 只做查找，不打包：没有随包字体时用系统里的中日文字体兜底。
SYSTEM_FONT_FILES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)
# 自制背景：与 /帮助 的文字卡片同一套浅色渐变，完全不依赖游戏素材。
BACKGROUND_STOPS = (
    (0.00, (157, 216, 244)),
    (0.42, (247, 207, 239)),
    (0.76, (232, 215, 249)),
    (1.00, (250, 241, 250)),
)


def _font_path(bold: bool = False) -> Path | None:
    """随包字体优先，其次系统字体；都找不到返回 None。"""
    bundled = Path(__file__).parent / "assets" / "fonts" / FONT_FILES[1 if bold else 0]
    if bundled.is_file():
        return bundled
    for candidate in SYSTEM_FONT_FILES:
        path = Path(candidate)
        if path.is_file():
            return path
    return None


@lru_cache(maxsize=32)
def font(size: int, bold: bool = False):
    path = _font_path(bold)
    if path is None:
        return ImageFont.load_default()
    return ImageFont.truetype(str(path), size)


def fonts_available() -> bool:
    """是否存在可用的中日文字体（随包或系统）。

    发布包不含素材，缺失时所有图片渲染都应跳过并回退纯文字；
这里不缓存结果，用户补上素材后不需要重启即可生效。
    """
    return _font_path(False) is not None


def self_made_background(size: tuple[int, int]) -> Image.Image:
    """代码绘制的浅色渐变背景，不读取任何素材文件。"""
    width, height = size
    image = Image.new("RGB", size, BACKGROUND)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        color = BACKGROUND_STOPS[-1][1]
        for index in range(len(BACKGROUND_STOPS) - 1):
            start_pos, start_color = BACKGROUND_STOPS[index]
            end_pos, end_color = BACKGROUND_STOPS[index + 1]
            if start_pos <= ratio <= end_pos:
                local = (ratio - start_pos) / max(end_pos - start_pos, 1e-6)
                color = tuple(
                    round(start_color[channel] + (end_color[channel] - start_color[channel]) * local)
                    for channel in range(3)
                )
                break
        draw.line((0, y, width, y), fill=color)
    return image


@lru_cache(maxsize=1)
def character_themes() -> dict:
    """角色主题色（build_growth_theme.py 生成）；缺失时回退到默认青色。"""
    path = Path(__file__).parent / "assets/growth/character_theme.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf8")).get("characters", {})


def theme_for(cid: int | str | None) -> dict:
    return character_themes().get(str(cid), DEFAULT_THEME)
