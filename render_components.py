"""Pillow 公共组件；文字实测换行，图片 contain 保留完整卡面。"""
from pathlib import Path
import re
from PIL import Image, ImageDraw, ImageOps
from . import render_theme as theme


def wrap(text: str, width: int, size: int = 36, bold: bool = False) -> list[str]:
    face = theme.font(size, bold)
    lines = []
    for paragraph in str(text).split("\n"):
        line = ""
        for token in re.findall(r"[A-Za-z0-9]+(?:['’_-][A-Za-z0-9]+)*|[^A-Za-z0-9]", paragraph):
            if line and face.getlength(line + token) > width:
                lines.append(line.rstrip())
                line = ""
            if not line and token.isspace():
                continue
            for char in token:
                if line and face.getlength(line + char) > width:
                    lines.append(line)
                    line = ""
                line += char
        lines.append(line)
    return lines


def text_block(draw, xy, text, width, *, size=36, fill=theme.TEXT, bold=False):
    x, y = xy
    for line in wrap(text, width, size, bold):
        draw.text((x,y), line, font=theme.font(size,bold), fill=fill, anchor="lt")
        y += size + 14
    return y


def _hex_color(value):
    value=str(value).lstrip('#')
    return tuple(int(value[index:index+2],16) for index in (0,2,4))


def theme_gradient(image, palette):
    """按角色主题色铺垂直渐变，上方叠一层同色斜带。"""
    top=_hex_color(palette['light']); mid=_hex_color(palette['tint']); bottom=(255,255,255)
    draw=ImageDraw.Draw(image)
    height=image.height
    for y in range(height):
        ratio=y/max(height-1,1)
        if ratio<0.55:
            local=ratio/0.55
            color=[top[i]+(mid[i]-top[i])*local for i in range(3)]
        else:
            local=(ratio-0.55)/0.45
            color=[mid[i]+(bottom[i]-mid[i])*local for i in range(3)]
        draw.line((0,y,image.width,y),fill=tuple(round(v) for v in color))
    band=Image.new('RGBA',image.size,(0,0,0,0))
    ImageDraw.Draw(band).polygon(
        [(0,0),(image.width,0),(image.width,round(height*0.30)),(0,round(height*0.42))],
        fill=_hex_color(palette['primary'])+(58,))
    image.paste(Image.alpha_composite(image.convert('RGBA'),band).convert('RGB'),(0,0))


def canvas(height: int, title: str, subtitle: str = "", *, accent=theme.CYAN, background_key="ui_background", character=None):
    image = Image.new("RGB", (theme.WIDTH, height), theme.BACKGROUND)
    palette=theme.theme_for(character) if character is not None else None
    if palette:
        theme_gradient(image,palette)
    else:
        background=Path(__file__).parent/'assets/growth/images'/f'{background_key}.png'
        if background.is_file():
            with Image.open(background) as source:
                image=ImageOps.fit(source.convert('RGB'),image.size,method=Image.Resampling.LANCZOS)
        else:
            # 未接入游戏素材时用自制渐变，保证兜底版式与 /帮助 一致。
            image=theme.self_made_background(image.size)
        veil=Image.new('RGBA',image.size,(255,255,255,0))
        vd=ImageDraw.Draw(veil)
        vd.rectangle((0,0,theme.WIDTH,164),fill=(255,255,255,220))
        vd.rectangle((0,height-88,theme.WIDTH,height),fill=(255,255,255,230))
        image=Image.alpha_composite(image.convert('RGBA'),veil).convert('RGB')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0,0,theme.WIDTH,10), fill=accent)
    draw.polygon([(924,10),(996,10),(950,100),(878,100)], fill="#E3EEF5")
    y = text_block(draw, (48,42), title, 900, size=52,bold=True)
    if subtitle:
        y = text_block(draw, (48,y+4), subtitle, 984, size=30,fill=theme.MUTED)
    return image, draw, y+24


def surface(draw, box):
    draw.rounded_rectangle(box,radius=24,fill=theme.SURFACE)


def contain(image, source, box):
    left,top,right,bottom=box
    source = ImageOps.contain(source.convert("RGBA"),(right-left,bottom-top),Image.Resampling.LANCZOS)
    image.paste(source,(left+(right-left-source.width)//2,top+(bottom-top-source.height)//2),source)


def footer(draw, height, page=1, pages=1, command=""):
    draw.line((48,height-82,1032,height-82),fill="#DFE5EF",width=2)
    draw.text((48,height-62),command,font=theme.font(30),fill=theme.MUTED,anchor="lt")
    draw.text((1032,height-62),f"{page} / {pages}",font=theme.font(30),fill=theme.MUTED,anchor="rt")


def save(image, path: Path):
    path.parent.mkdir(parents=True,exist_ok=True)
    image.save(path,format="PNG",optimize=True)
    return path
