"""用原作解花素材组合阶段结果预览；不执行养成交易。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter
from . import render_theme as t
from .render_components import contain, theme_gradient, save, wrap
from .gacha_render import RenderCard

ASSETS = Path(__file__).parent/'assets/growth/images/bloom'


def asset(image, key, box):
    with Image.open(ASSETS/f'{key}.png') as source:
        contain(image,source,box)


def card_surface(renderer, state, width):
    source=renderer._load_card(state.card).convert('RGBA')
    bounds=source.getchannel('A').getbbox()
    if bounds:
        source=source.crop(bounds)
    height=round(width*source.height/source.width)
    image=source.resize((width,height),Image.Resampling.LANCZOS)
    renderer._draw_growth_mark(image,0,0,width,height,state.is_kaika,state.is_cho_kaika)
    renderer._draw_stars(image,0,0,width,height,state.card.rarity,state.copies)
    renderer._draw_card_info_footer(image,0,0,width,height,state.card)
    return image


def render_bloom_result(renderer, card, copies, stage, output, *, spent, remaining, item_label='解花券'):
    if stage not in (1,2):
        raise ValueError('仅支持解花与超解花')
    image=Image.new('RGB',(1200,1280),'white')
    theme_gradient(image,t.theme_for(card.character_id))
    draw=ImageDraw.Draw(image)
    draw.text((56,36),'O.N.G.E.K.I  /  CARD GROWTH',font=t.font(23,True),fill=t.MUTED)
    title_keys=['kaika_0','kaika_1'] if stage==1 else ['super_0','super_1','super_2']
    title_width=len(title_keys)*94
    for i,key in enumerate(title_keys):
        asset(image,key,(600-title_width//2+i*94,80,600-title_width//2+(i+1)*94,190))
    asset(image,'rainbow' if stage==2 else 'ribbon',(426,200,774,230))
    draw.text((600,216),'阶段提升完成',font=t.font(23,True),fill=t.TEXT,anchor='mm')
    asset(image,'glow',(440,170,1180,1050))
    for i,(x,y,size) in enumerate(((66,220,38),(1100,288,38),(548,322,28),(535,872,36),(1110,948,30))):
        asset(image,'petal',(x,y,x+size,y+size))
    before=RenderCard(card,copies,stage==2,False)
    after=RenderCard(card,copies,True,stage==2)
    for state,width,x,y,label in ((before,338,64,386,'未解花' if stage==1 else '解花'),
                                  (after,506,630,294,'解花' if stage==1 else '超解花')):
        shown=card_surface(renderer,state,width)
        shadow=Image.new('RGBA',image.size)
        shadow.paste((35,31,55,65),(x+8,y+16),shown.getchannel('A'))
        image.paste(Image.alpha_composite(image.convert('RGBA'),shadow.filter(ImageFilter.GaussianBlur(12))).convert('RGB'),(0,0))
        image.paste(shown,(x,y),shown)
        draw.text((x+width/2,y-28),label,font=t.font(28,True),fill=t.MUTED,anchor='mm')
    asset(image,'arrow',(458,564,570,666))
    draw.rounded_rectangle((56,1040,1144,1220),radius=24,fill='white')
    title_size=32
    lines=wrap(card.name,1024,title_size,True)
    while len(lines)>2 and title_size>18:
        title_size-=1;lines=wrap(card.name,1024,title_size,True)
    for i,line in enumerate(lines):
        draw.text((88,1060+i*(title_size+6)),line,font=t.font(title_size,True),fill=t.TEXT,anchor='lt')
    draw.text((88,1160),f'{item_label} −{spent}   ·   剩余 {remaining}',font=t.font(29,True),fill=t.PINK,anchor='lt')
    draw.text((56,1244),f'/养成 {card.id}',font=t.font(23),fill=t.MUTED,anchor='lt')
    draw.text((1144,1244),'阶段结果预览',font=t.font(23),fill=t.MUTED,anchor='rt')
    return save(image,output)
