"""真实卡面与官方曲目信息的新旧对照；输出目录由调用者指定。"""
import argparse
import json
from pathlib import Path
import time
import tomllib
from PIL import Image, ImageDraw

from ..gacha_core import load_cards
from ..gacha_render import GachaRenderer, RenderCard
from ..growth_catalog import GrowthCatalog
from ..growth_render import render_affection, render_gift_inventory
from ..task_render import TaskCardData, render_task_card
from ..render_theme import font


def main(output: Path, source: Path):
    root=Path(__file__).parents[1]
    cards=load_cards(root/'assets/card_data/card_info_merged.json')
    catalog=GrowthCatalog(root/'assets/growth',cards)
    renderer=GachaRenderer(root/'assets/card_data',root/'assets/ui')
    output.mkdir(parents=True,exist_ok=True)
    ids=[100001,102739,103985,104095,104355,104345,104380,104470,104485,104490,104075]
    states=[RenderCard(cards.by_id[cid],11 if cid==100001 else i+1,i in (5,6),i==6) for i,cid in enumerate(ids)]
    metrics=[]
    def record(name, fn):
        start=time.perf_counter();result=fn();elapsed=time.perf_counter()-start
        paths=result if isinstance(result,list) else [result] if isinstance(result,Path) else [output/name]
        metrics.append({'name':name,'seconds':elapsed,'files':[p.name for p in paths],
                        'bytes':sum(p.stat().st_size for p in paths)})
        return paths
    def draw(rows,path):
        renderer.render(rows,path)
        return path
    record('draw_new',lambda:draw(states,output/'draw_new.png'))
    portrait=renderer._load_card(next(c for c in cards.cards if c.character_id==1000 and c.rarity=='SSR'))
    record('affection_new.png',lambda:render_affection(catalog,1000,4800,portrait,output/'affection_new.png',partner=True))
    record('affection_max.png',lambda:render_affection(catalog,1000,catalog.thresholds[-1],portrait,output/'affection_max.png'))
    record('affection_card_option.png',lambda:render_affection(catalog,1000,4800,portrait,output/'affection_card_option.png',portrait_mode='card'))
    record('gifts.png',lambda:render_gift_inventory({'gift_small':17,'gift_medium':3,'gift_large':0,'flower_fragment':29},output/'gifts.png'))
    record('draw_single.png',lambda:draw([RenderCard(cards.by_id[ids[-1]],1,False,False)],output/'draw_single.png'))
    records=json.loads((source/'sega_official_music/music.json').read_text(encoding='utf8'))
    song=next(r for r in records if r['title']=='Oshama Scramble!')
    matches=list((source/'sega_official_music').rglob(song['image_url']))
    task=TaskCardData(task_id=42,kind='challenge',game='ongeki',title=song['title'],artist=song['artist'],
                      level=f"MASTER {song['lev_mas']}",requirement='TECHNICAL SCORE 达到 1,000,000（SSS）',reward=tomllib.loads((root/'config.toml').read_text(encoding='utf8'))['task']['challenge_reward_sss'],
                      cover_path=matches[0] if matches else None)
    record('task_old.png',lambda:render_task_card(task,output/'task_old.png'))
    # 新版任务卡效果不佳，正式版保留 task_old.png 对应的旧版卡面。
    preview_names=['draw_new.png','gifts.png','affection_new.png','task_old.png']
    thumbnails=[]
    for name in preview_names:
        with Image.open(output/name) as image:
            thumbnail=image.convert('RGB').resize((360,round(image.height*360/image.width)),Image.Resampling.LANCZOS)
            thumbnail.save(output/f'{Path(name).stem}_360.png')
            thumbnails.append(thumbnail)
    contact=Image.new('RGB',(360*4,max(im.height for im in thumbnails)+48),'#E5EAF3')
    draw=ImageDraw.Draw(contact)
    for i,(name,image) in enumerate(zip(preview_names,thumbnails)):
        contact.paste(image,(i*360,48));draw.text((i*360+8,10),name,font=font(18),fill='#202A44')
    contact.save(output/'contact_sheet.png')
    (output/'metrics.json').write_text(json.dumps({'metrics':metrics,'notes':[
        '真实卡面和真实曲名谱面；持有量、余额、任务ID为固定展示样本，不是用户记录。',
        '旧版没有好感页面，因此不伪造旧好感对照。',
        '仅记录本机首次渲染时间与体积；部署机器、峰值内存和QQ压缩仍待验证。']},ensure_ascii=False,indent=2),encoding='utf8')
    print(output/'contact_sheet.png')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source',type=Path,default=Path('output'))
    args=parser.parse_args();main(args.output,args.source)
