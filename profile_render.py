"""个人档案使用已装备奖励；奖励浏览独立分页。"""
from pathlib import Path
from PIL import Image
from . import game_ui,render_theme as t
from .render_components import canvas,contain,footer,save,text_block,wrap

ROOT=Path(__file__).parent/'assets/growth'
KINDS={'Trophy':'称号','NamePlate':'名牌','Attachment':'装饰','ProfileVoice':'语音','NormalCard':'奖励卡'}
COLUMNS={'Trophy':'title_id','NamePlate':'nameplate_id','Attachment':'attachment_id'}
TITLE_RARITY={'Normal':0,'Silver':1,'Gold':2,'Platinum':3,'Rainbow':4}

def title_art(image,row,box):
    """原作以稀有度选择称号底图，再叠加称号文字。"""
    index=TITLE_RARITY.get(row.get('rarity'),0)
    path=ROOT/'images'/f'ui_title_{index}.png'
    if not path.is_file():return False
    with Image.open(path) as source:
        source=source.convert('RGBA')
        scale=min((box[2]-box[0])/source.width,(box[3]-box[1])/source.height)
        source=source.resize((round(source.width*scale),round(source.height*scale)),Image.Resampling.LANCZOS)
        left=box[0]+(box[2]-box[0]-source.width)//2
        top=box[1]+(box[3]-box[1]-source.height)//2
        image.paste(source,(left,top),source)
        return (left,top,left+source.width,top+source.height)

def equipped_rewards(snapshot,rewards):
    profile=next(iter(snapshot.get('player_growth_profile',[])),{})
    owned={(r['cosmetic_type'],str(r['cosmetic_id'])) for r in snapshot.get('player_cosmetics',[])}
    lookup={(r['kind'],str(r['id'])):r for r in rewards}
    return {kind:lookup.get((kind,str(profile.get(column)))) if (kind,str(profile.get(column))) in owned else None for kind,column in COLUMNS.items()}

def art(image,row,box):
    asset=row.get('icon') if row and row.get('kind')=='NamePlate' else row.get('image') if row else None
    if asset:
        with Image.open(ROOT/asset) as source:contain(image,source,box)
    elif row and row.get('card_image'):
        with Image.open(ROOT.parent/row['card_image']) as source:contain(image,source,box)

def reward_label(text, width, height, size=30):
    """整段适配可用区域，省略号也计入宽度，不侵占下一行命令。"""
    lines=wrap(text,width,size,True)
    while size>14 and len(lines)*(size+6)>height:
        size-=1;lines=wrap(text,width,size,True)
    if len(lines)*(size+6)>height:
        lines=lines[:max(1,height//(size+6))]
        while lines[-1] and t.font(size,True).getlength(lines[-1]+'…')>width:
            lines[-1]=lines[-1][:-1]
        lines[-1]+='…'
    return lines,size


def render_reward_pages(catalog,rewards,cid,snapshot,output):
    claims={r['reward_key'] for r in snapshot.get('affection_reward_claims',[])}
    equipped=equipped_rewards(snapshot,rewards)
    voice_numbers={r['reward_key']:i for i,r in enumerate(
        (r for r in rewards if r['kind']=='ProfileVoice'),1)}
    pages=[]
    for offset in range(0,len(rewards),5):
        batch=rewards[offset:offset+5];height=280+len(batch)*224
        image,draw,_=canvas(height,'好感奖励',catalog.characters[cid]['name']+' · 新获得的称号与装饰会自动装备，可随时更换',character=cid)
        for i,r in enumerate(batch):
            y=184+i*224;unlocked=r['reward_key'] in claims
            game_ui.panel(image,(48,y,1032,y+208))
            selected=equipped.get(r['kind'])
            status='已装备' if unlocked and selected and str(selected['id'])==str(r['id']) else '已解锁' if unlocked else '未解锁'
            trophy=r['kind']=='Trophy'
            x=80 if trophy else 284
            draw.text((x,y+20),f"Lv{r['level']} · {KINDS[r['kind']]} · {status}",
                      font=t.font(25,True),fill=t.CYAN if unlocked else t.MUTED,anchor='lt')
            if trophy:
                bounds=title_art(image,r,(76,y+62,1004,y+148))
                if bounds:
                    left,top,right,bottom=bounds
                    lines,size=reward_label(r['name'],right-left-48,bottom-top-12,28)
                    for j,line in enumerate(lines):
                        draw.text(((left+right)/2,(top+bottom)/2+(j-(len(lines)-1)/2)*(size+6)),
                                  line,font=t.font(size,True),fill=t.TEXT,anchor='mm')
                else:
                    text_block(draw,(80,y+66),r['name'],900,size=28,fill=t.TEXT)
            else:
                art(image,r,(76,y+20,260,y+188))
                if r['kind']=='ProfileVoice':
                    number=voice_numbers[r['reward_key']]
                    icons=sorted((ROOT/'images/flavor').glob(f'UI_Item_Flavor_{number-1:02d}_*.png'))
                    if icons:
                        with Image.open(icons[0]) as source:
                            contain(image,source.crop((0,0,source.width,138)),(118,y+30,216,y+116))
                    draw.text((168,y+150),f'语音 {number:02d}',font=t.font(23,True),fill=t.MUTED,anchor='mm')
                lines,size=reward_label(r['name'],716,86)
                for j,line in enumerate(lines):
                    draw.text((284,y+64+j*(size+6)),line,font=t.font(size,True),fill=t.TEXT,anchor='lt')
            command=None
            if unlocked and r['kind'] in ('Trophy','Attachment'):
                command=f"ID {r['id']}｜/装扮 {KINDS[r['kind']]} {r['id']}"
            elif unlocked and r['kind']=='ProfileVoice':
                command=f"/角色语音 {catalog.characters[cid]['name']} {voice_numbers[r['reward_key']]}"
            elif r['kind']=='NamePlate':
                command=f"ID {r['id']}｜名牌仅作收藏"
            elif r['kind']=='NormalCard':
                command=f"ID {r['id']}｜/卡图 {r['id']}"
            if command:
                draw.text((x,y+170),command,font=t.font(24),fill=t.PINK if unlocked else t.MUTED,anchor='lt')
        footer(draw,height,page=offset//5+1,pages=(len(rewards)+4)//5,command='/好感奖励 · /装扮 · /好感')
        pages.append(save(image,output.with_name(f'{output.stem}_{offset//5+1}.png')))
    return pages


