"""统一收藏界面样稿与好感页；使用业务快照，不读取或修改数据库。"""
import json
from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageDraw
from . import render_theme as t
from .render_components import canvas, contain, footer, save, text_block, wrap
from .growth_core import (
    MAX_AFFECTION_LEVEL,
    REWARD_MAX_LEVEL,
    affection_level,
    affection_progress,
)
from . import game_ui

ASSETS = Path(__file__).parent / 'assets/growth'
FLAVOR = ASSETS / 'images/flavor'


@lru_cache(maxsize=1)
def _details() -> dict:
    path = ASSETS / 'character_detail.json'
    return json.loads(path.read_text(encoding='utf8')).get('characters', {}) if path.is_file() else {}


def _detail_rows(cid: int) -> list[tuple[str, str]]:
    row = _details().get(str(cid), {})
    return [('学年', row.get('grade', '')), ('誕生日', row.get('birthday', '')),
            ('星座', row.get('zodiac', '')), ('血液型', row.get('blood', '')),
            ('身長', row.get('height', ''))]


def draw_detail_panel(image, cid: int, box) -> None:
    """以原图坐标排版基础资料；数值字号与底图标签一致并限制在右侧内腔。"""
    left, top, right, bottom = box
    with Image.open(FLAVOR / 'UI_SLC_Cmu_CharaDetailBase_00_71.png') as source:
        base = source.convert('RGBA').resize((right-left, bottom-top), Image.Resampling.LANCZOS)
    image.paste(base, (left, top), base)
    draw = ImageDraw.Draw(image)
    scale = (bottom-top)/142
    for index, (_, value) in enumerate(_detail_rows(cid)):
        if not value:
            continue
        x = left+round(109*(right-left)/298)
        y = top+round((54,73,91,108,125)[index]*scale)
        max_width = round(147*(right-left)/298)
        size = round(13*scale)
        while size > 10 and t.font(size).getlength(value) > max_width:
            size -= 1
        draw.text((x,y),value,font=t.font(size),fill='white',anchor='lm')


FLAVOR_BOUNDARIES = (8,44,80,116,152,189,225,261,297,333,404)
FLAVOR_SCALE = 920/386
FLAVOR_HEIGHT = round(418*FLAVOR_SCALE)


def flavor_rows(character, claimed):
    """按整张原作模板的真实行槽适配文本，未领取内容保持隐藏。"""
    rows = []
    for index, reward in enumerate(r for r in character['rewards'] if r.get('profile')):
        unlocked = reward['reward_key'] in claimed
        value = reward['profile'] if unlocked else f"Lv{reward['level']} 解锁后查看"
        top = round(FLAVOR_BOUNDARIES[index]*FLAVOR_SCALE)
        bottom = round(FLAVOR_BOUNDARIES[index+1]*FLAVOR_SCALE)
        size = 28
        lines = wrap(value, 514, size)
        while size > 16 and len(lines)*(size+8) > bottom-top-18:
            size -= 1
            lines = wrap(value,514,size)
        rows.append({'index':index,'level':reward['level'],'unlocked':unlocked,
                     'lines':lines,'size':size,'top':top,'height':bottom-top})
    return rows


def draw_flavor_panel(image, cid: int, rows, box) -> int:
    """整张原作CharaFlavorBase等比铺设，保留标签、行线及尾部空间。"""
    left, top, right, bottom = box
    with Image.open(FLAVOR / 'UI_SLC_Cmu_CharaFlavorBase_49.png') as source:
        template = source.convert('RGBA').resize((920,FLAVOR_HEIGHT),Image.Resampling.LANCZOS)
    # 原作半透明标签需要连续的底色，不再裁切标签拼成独立卡片。
    draw = ImageDraw.Draw(image)
    draw.rectangle((left,top,right,top+FLAVOR_HEIGHT),fill=t.theme_for(cid)['light'])
    image.paste(template,(left,top),template)
    for row in rows:
        step = row['size']+8
        y = top+row['top']+row['height']/2-(len(row['lines'])-1)*step/2
        for line_no,line in enumerate(row['lines']):
            draw.text((left+386,y+line_no*step),line,font=t.font(row['size']),
                      fill=t.TEXT if row['unlocked'] else t.MUTED,anchor='lm')
    return top+FLAVOR_HEIGHT


def render_affection(catalog, cid, points, portrait, output, *, partner=False, unlocked=True, portrait_mode="original", snapshot=None, rewards=None):
    """单张好感页整合已装备称号、装饰与已解锁资料。"""
    from .profile_render import equipped_rewards, title_art, art as reward_art
    from .starter_cards import STARTER_CARDS
    snapshot=snapshot or {}
    character=catalog.characters[cid]
    level=affection_level(points,catalog.thresholds)
    equipped=equipped_rewards(snapshot,rewards or [])
    claimed={r['reward_key'] for r in snapshot.get('affection_reward_claims',[])}
    details=flavor_rows(character,claimed)
    detail_height=FLAVOR_HEIGHT+112 if details else 0
    height=1200+detail_height
    image,draw,y=canvas(height,"角色好感",f"{character['name']}" + (" · 当前伙伴" if partner else ""),
                        accent=t.PINK,character=cid)
    attachment=equipped['Attachment']
    if attachment:reward_art(image,attachment,(916,22,1040,154))
    else:original_asset(image,f'character_{cid}',(920,30,1032,150))
    # 称号位于左侧角色卡上方；人物与教室一起下移并保持比例。
    game_ui.panel(image,(48,180,1032,height-112))
    if portrait_mode=="original":
        game_ui.character_stage(image,(64,284,528,1066))
        path=Path(__file__).parent/'assets/growth/images'/f'portrait_{cid}.png'
        with Image.open(path) as source:
            source=source.convert('RGBA');bounds=source.getchannel('A').getbbox()
            if bounds:contain(image,source.crop(bounds),(80,302,512,1044))
    elif portrait_mode=="card" and portrait is not None:
        contain(image,portrait,(72,296,520,1058))
    else:
        raise ValueError('展示方式必须为original或具有卡面的card')
    game_ui.heading(image,'角色好感',(560,202,992,308))
    draw_detail_panel(image,cid,(84,850,510,1053))
    level,ratio,current,need=affection_progress(points,catalog.thresholds)
    game_ui.heart(image,(600,300,930,630),ratio,level=level,tier=level//100,
                  finish=level>=MAX_AFFECTION_LEVEL)
    text_block(draw,(568,606),f"累计 {points} 点",416,size=36,bold=True)
    detail=('好感 99 / 99 已满（累计仍增加）'
            if level>=MAX_AFFECTION_LEVEL else f"本级 {current} / {need}")
    text_block(draw,(568,660),detail,416,size=30,fill=t.PINK)
    game_ui.progress_bar(image,(568,698,984,704),ratio,cid)
    copies=next((r['copies'] for r in snapshot.get('inventory',[]) if r['card_id']==STARTER_CARDS[cid]),1)
    text_block(draw,(568,712),f"搜集进度 · {min(copies,11)} / 11",416,size=28,fill=t.MUTED)
    upcoming=next((r for r in character['rewards'] if int(r['level'])>level),None)
    if upcoming:
        reward_label='下一奖励'
    elif level>=REWARD_MAX_LEVEL:
        reward_label='奖励节点已全部达成'
    else:
        reward_label='全部等级节点已达成'
    text_block(draw,(568,766),reward_label,416,size=28,fill=t.MUTED)
    if upcoming:
        lines=wrap(f"Lv{upcoming['level']} · {upcoming['name']}",416,30,True)
        if len(lines)>2:
            lines=lines[:2]
            while t.font(30,True).getlength(lines[-1]+'…')>416:lines[-1]=lines[-1][:-1]
            lines[-1]+='…'
        bottom=text_block(draw,(568,812),'\n'.join(lines),416,size=30,bold=True)
        text_block(draw,(568,bottom+8),f"还差 {catalog.thresholds[int(upcoming['level'])]-points} 点",416,size=28,fill=t.MUTED)
    elif level>=REWARD_MAX_LEVEL:
        text_block(draw,(568,812),'好感继续累计，心形最多显示 99 / 99',416,size=26,fill=t.MUTED)
    trophy=equipped['Trophy']
    bounds=title_art(image,trophy,(64,204,528,272)) if trophy else None
    if bounds:
        left,top,right,bottom=bounds;size=26
        lines=wrap(trophy['name'],right-left-36,size,True)
        while len(lines)>2 and size>14:
            size-=1;lines=wrap(trophy['name'],right-left-36,size,True)
        line_height=size+4
        for i,line in enumerate(lines):
            draw.text(((left+right)//2,(top+bottom)//2+(i-(len(lines)-1)/2)*line_height),line,font=t.font(size,True),fill='#263047',anchor='mm')
    else:
        draw.text((296,238),'称号 · /装扮',font=t.font(26),fill=t.MUTED,anchor='mm')
    if details:
        draw.line((80,1100,1000,1100),fill='#CCD6DF',width=2)
        text_block(draw,(80,1120),'角色资料',900,size=32,bold=True)
        draw.text((1000,1140),f"已解锁 {sum(row['unlocked'] for row in details)} / {len(details)}",
                  font=t.font(24),fill=t.MUTED,anchor='rm')
        draw_flavor_panel(image,cid,details,(80,1184,1000,height-128))
    footer(draw,height,command=f"/装扮 · /好感奖励 {catalog.characters[cid]['name']} · /角色语音")
    return save(image,output)


def original_asset(image, key, box):
    path=Path(__file__).parent/'assets/growth/images'/f'{key}.png'
    if path.is_file():
        with Image.open(path) as source:
            contain(image,source,box)


def render_gift_inventory(items: dict, output: Path, purchase: dict | None = None):
    purchase = purchase or {}
    cards = []
    for size,label,value in (('small','小礼物',300),('medium','中礼物',1000),('large','大礼物',10000)):
        plan = purchase.get(size)
        notes = ([f"购买 {plan['price']} 点 / 份", f"本周剩余 {plan['left']} / {plan['cap']}"]
                 if plan else ['无点数购买渠道'])
        notes = [line for note in notes for line in wrap(note,264,24)]
        counts = wrap(f"持有 {items.get('gift_'+size,0)}",264,34)
        cards.append((size,label,value,notes,counts))
    card_height = max(342+len(counts)*44+len(notes)*34+28 for _,_,_,notes,counts in cards)
    fragment_lines = wrap(
        f"花之碎片 {items.get('flower_fragment',0)}"
        f"｜解花券 {items.get('bloom_ticket',0)}",
        936,
        40,
        True,
    )
    fragment_height = 104+len(fragment_lines)*54
    height = 182+card_height+24+fragment_height+120
    image,draw,y=canvas(height,'礼物背包','赠送礼物，提升指定角色的好感',accent=t.PINK)
    for index,(size,label,value,notes,counts) in enumerate(cards):
        x=48+index*336
        game_ui.panel(image,(x,y,x+312,y+card_height))
        original_asset(image,f'gift_{size}',(x+42,y+24,x+270,y+224))
        text_block(draw,(x+24,y+240),label,264,size=36,bold=True)
        bottom=text_block(draw,(x+24,y+296),'\n'.join(counts),264,size=34)
        bottom=text_block(draw,(x+24,bottom+6),f"每份 +{value} 好感",264,size=28,fill=t.MUTED)
        draw.line((x+24,bottom+4,x+288,bottom+4),fill='#DCE3EC',width=1)
        for i,line in enumerate(notes):
            draw.text((x+24,bottom+18+i*34),line,font=t.font(24),fill=t.CYAN,anchor='lt')
    top=y+card_height+24
    game_ui.panel(image,(48,top,1032,top+fragment_height))
    bottom=text_block(draw,(72,top+24),'\n'.join(fragment_lines),936,size=40,bold=True)
    text_block(draw,(72,bottom+12),'/礼物 购买 小 1  ·  /送礼 星咲 あかり 小 1',936,size=30,fill=t.PINK)
    footer(draw,height,command='/好感 · /装扮 · /养成 <卡ID>')
    return save(image,output)




