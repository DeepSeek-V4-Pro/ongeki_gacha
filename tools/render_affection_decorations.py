"""Render decoration integration with mock snapshots, without a player database."""
from pathlib import Path
import html
import json
from PIL import Image
from ..gacha_core import load_cards
from ..growth_catalog import GrowthCatalog
from ..reward_catalog import RewardCatalog
from ..growth_render import render_affection
from ..starter_cards import STARTER_CARDS


def main():
    root=Path(__file__).parents[1]/'assets'
    cards=load_cards(root/'card_data/card_info_merged.json')
    catalog=GrowthCatalog(root/'growth',cards)
    rewards=RewardCatalog(root/'growth')
    output=root.parents[1]/'output/character_affection/visual_samples/affection_reference_20260914'
    output.mkdir(parents=True,exist_ok=True)
    entries=[]
    cases=[(cid,1000,True,f'full_{cid}') for cid in catalog.characters]
    cases += [(1013,0,False,'default'),(1013,200,False,'partial')]
    cases += [(1000,level,False,f'level_{level}') for level in (9,10,99,100,500,999)]
    cases += [(1013,9999,True,'max_99_99')]
    for cid,level,decor,key in cases:
        rows=rewards.rewards(cid)
        selected={kind:next(r for r in reversed(rows) if r['kind']==kind) for kind in ('Trophy','Attachment')}
        claims=[r for r in rows if r['level']<=level]
        snapshot={'affection_reward_claims':claims,
                  'inventory':[{'card_id':STARTER_CARDS[cid],'copies':1+sum(r['kind']=='NormalCard' for r in claims)}],
                  'player_growth_profile':[{'title_id':selected['Trophy']['id'],'attachment_id':selected['Attachment']['id']}] if decor else [],
                  'player_cosmetics':[{'cosmetic_type':k,'cosmetic_id':r['id']} for k,r in selected.items()] if decor else []}
        path=render_affection(catalog,cid,catalog.thresholds[level],None,output/(key+'.png'),partner=True,snapshot=snapshot,rewards=list(rewards.by_key.values()))
        with Image.open(path) as im:im.verify()
        entries.append({'file':path.name,'name':catalog.characters[cid]['name'],'level':level})
    body=''.join(f'<article><h2>{html.escape(e["name"])} · Lv{e["level"]}</h2><a href="{e["file"]}"><img src="{e["file"]}"></a></article>' for e in entries)
    (output/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>好感资料模板</title><style>body{background:#172335;color:white;font:16px system-ui;padding:24px}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:24px}img{width:100%}h2{font-size:20px}</style><h1>好感资料模板预览</h1><p>17名角色完整装饰、新账号、部分解锁与等级边界。全部为模拟数据，点击图片查看原尺寸。</p><main>'+body+'</main>',encoding='utf8')
    (output/'manifest.json').write_text(json.dumps(entries,ensure_ascii=False,indent=2),encoding='utf8')
    print(f'{len(entries)} previews: {output}')


if __name__=='__main__':main()
