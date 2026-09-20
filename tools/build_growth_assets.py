"""按白名单打包少量原作素材，并保存来源、哈希、尺寸和用途。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import unicodedata
from PIL import Image

from ..growth_core import MAIN_CHARACTER_IDS


UI_KIT = {
    **{f"ui_title_{i}": rf"UI_CMN_Signage_UserTitle_0{i}_" for i in range(5)},
}

INTIMATE_KIT = {
    "GaugeBase": r"UI_SLC_Cmu_Friendship_GaugeBase_942",
    "GaugeBase_10": r"UI_SLC_Cmu_Friendship_GaugeBase_10_357",
    "GaugeBase_Eff": r"UI_SLC_Cmu_Friendship_GaugeBase_Eff_655",
    "GaugeBase_Rebirth": r"UI_SLC_Cmu_Friendship_GaugeBase_Rebirth_732",
    "GaugeBase_Rebirth_10": r"UI_SLC_Cmu_Friendship_GaugeBase_Rebirth_10_992",
    "Gauge_Meter_Pink": r"UI_SLC_Cmu_Friendship_Gauge_Meter_Pink_810",
    "Gauge_Meter_Yellow": r"UI_SLC_Cmu_Friendship_Gauge_Meter_Yellow_939",
    "Gauge_Finish": r"UI_SLC_Cmu_Friendship_Gauge_Finish_54",
    "Gauge_NameBase": r"UI_SLC_Cmu_Friendship_Gauge_NameBase_46",
    "TextLevel": r"UI_SLC_Cmu_Friendship_TextLevel_912",
    "24pt_Friendship_Level": r"UI_NUM_24pt_Friendship_Level_",
    "57pt_Friendship_Level": r"UI_NUM_57pt_Friendship_Level_",
}

CARD_REVEAL_KIT = {
    "rare_N": "gameUi/UI_Card_Rare_00_N.webp",
    "rare_R": "gameUi/UI_Card_Rare_01_R.webp",
    "rare_SR": "gameUi/UI_Card_Rare_02_SR.webp",
    "rare_SSR": "gameUi/UI_Card_Rare_03_SSR.webp",
    "rare_SRPlus": "gameUi/UI_Card_Rare_05_SRPlus.webp",
    "attr_Fire": "gameUi/UI_Card_Attribute_00_Red.webp",
    "attr_Aqua": "gameUi/UI_Card_Attribute_01_Bule.webp",
    "attr_Leaf": "gameUi/UI_Card_Attribute_02_Green.webp",
    "frame_N": "card-frame/UI_Card_frame_N_00.webp",
    "frame_R": "card-frame/UI_Card_frame_R_00.webp",
    "frame_SR": "card-frame/UI_Card_frame_SR_00.webp",
    "frame_SRPlus": "card-frame/UI_Card_frame_SRPlus_00.webp",
    "frame_SSR": "card-frame/UI_Card_frame_SSR_00.webp",
}

CARD_GET_KIT = {
    "title_bg": "SB_CMN_CardGet_Title_BG",
    "title_text": "SB_CMN_CardGet_Title",
    "class_N": "SB_CMN_CardGet_class_N",
    "class_R": "SB_CMN_CardGet_class_R",
    "class_SR": "SB_CMN_CardGet_class_SR",
    "class_SRPlus": "SB_CMN_CardGet_class_SRP_00",
    "class_SSR": "SB_CMN_CardGet_class_SSR",
    "new_banner": "UI_CMN_CardGet_NEW",
    "limit_bar": "UI_CMN_CardGet_LimitBreak_Right",
    "limit_ribbon": "UI_CMN_CardGet_LimitBreak_Ribbon",
    "bg_card": "SB_CMN_CardGet_BG_Corridir",
    "line_N": "SB_CMN_CardGet_BG_N_Line_00",
    "line_R": "SB_CMN_CardGet_BG_R_Line_00",
    "line_SR": "SB_CMN_CardGet_BG_SR_Line_00",
    "line_SSR": "SB_CMN_CardGet_BG_SSR_Line_00",
}


def _find_ui(roots, pattern):
    regex = re.compile(pattern)
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.png")):
            if regex.search(path.name):
                return path
    return None


def build(source: Path, destination: Path, official: Path | None = None):
    extracted=json.loads((source/'asset_manifest.json').read_text(encoding='utf8'))
    by_name={row['name']:row for row in extracted}
    selected=[(f'character_{cid}',f'ui_friendship_bt_{cid:06d}','主角色列表与伙伴头像',cid) for cid in sorted(MAIN_CHARACTER_IDS)]
    selected += [(f'gift_{size}',f'ui_intimateup_{i:06d}','礼物背包、赠礼回执',None)
                 for i,size in enumerate(('small','medium','large'),1)]
    manifest=[]
    for key,bundle,purpose,cid in selected:
        entry=by_name[bundle]
        images=[p for p in entry['outputs'] if p.lower().endswith('.png')]
        if len(images)!=1:raise ValueError(f'素材不唯一: {bundle}')
        original=source/Path(images[0].replace('\\','/'))
        with Image.open(original) as image:
            image.load();dimensions=list(image.size);alpha='A' in image.getbands()
        relative=f'images/{key}.png';target=destination/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(original,target)
        manifest.append({'key':key,'path':relative,'game_source':entry['source'].replace('\\','/'),
                         'extracted_source':images[0].replace('\\','/'),'purpose':purpose,
                         'dimensions':dimensions,'alpha':alpha,'character_id':cid,
                         'crop':'contain/full_image','fallback':'文字及统一占位图',
                         'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                         'rights':'SEGA 或对应权利人；不属于插件代码开源许可'})
    official = official or source.parent/'sega_official_characters'
    official_manifest=json.loads((official/'manifest.json').read_text(encoding='utf8'))
    characters=json.loads((source/'character_affection.json').read_text(encoding='utf8'))
    def normalized(name):return ''.join(unicodedata.normalize('NFKC',name).split())
    names={normalized(c['name']):int(c['id']) for c in characters}
    seen=set()
    for entry in official_manifest['items']:
        cid=names[normalized(entry['info']['nameJp'])]
        if cid in seen:raise ValueError('官网角色立绘重复归属')
        seen.add(cid)
        if 'asset_id' not in entry:
            raise ValueError('官网清单缺少 asset_id，请用修复后的抓取脚本重建')
        asset_id=int(entry['asset_id'])
        asset=next(a for a in entry['assets'] if a['asset']=='image_normal.png')
        original=official/str(asset_id)/'image_normal.png'
        relative=f'images/portrait_{cid}.png';target=destination/relative
        shutil.copyfile(original,target)
        with Image.open(target) as image:
            rgba=image.convert('RGBA');bounds=rgba.getchannel('A').getbbox()
            if not bounds:raise ValueError('官网立绘为空')
            dimensions=list(image.size)
        manifest.append({'key':f'portrait_{cid}','path':relative,'source_url':asset['url'],
                         'official_character_id':entry['id'],'official_asset_id':asset_id,
                         'character_id':cid,'name':entry['info']['nameJp'],
                         'purpose':'好感页默认原始立绘',
                         'dimensions':dimensions,'alpha':True,'content_bounds':list(bounds),
                         'crop':'仅去透明边距，完整保留角色内容','fallback':'文字占位；不自动切换卡面',
                         'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                         'rights':'SEGA 或对应权利人；不属于插件代码开源许可'})
    if seen!=MAIN_CHARACTER_IDS:raise ValueError('默认立绘必须覆盖全部17名主角色')
    ui_files = {
        'ui_category': 'ui/sharedassets14/UI_SLC_Cmu_CategoryBase_00_58.png',
        'bloom_ticket': 'ui_extra/resources/UI_Item_OpenFlower_Ticket_00_946.png',
    }
    for key, relative_source in ui_files.items():
        original=source/relative_source
        relative=f'images/{key}.png';target=destination/relative
        shutil.copyfile(original,target)
        with Image.open(target) as image: dimensions=list(image.size)
        manifest.append({'key':key,'path':relative,'extracted_source':relative_source,
                         'game_source':f"mu3_Data/{relative_source.split('/')[1]}.assets",
                         'purpose':'原作好感界面组件：栏目、面板、量表、礼物按钮',
                         'dimensions':dimensions,'crop':'完整图像；窄面板使用保留端帽的拉伸',
                         'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                         'rights':'SEGA 或对应权利人；不属于插件代码开源许可'})
    original=source.parent/'all_ui_assets/adventuremap_bg/ui_adventuremap_bg_60001.png'
    target=destination/'images/ui_background.png'
    shutil.copyfile(original,target)
    with Image.open(target) as image: dimensions=list(image.size)
    manifest.append({'key':'ui_background','path':'images/ui_background.png',
                     'extracted_source':'../all_ui_assets/adventuremap_bg/ui_adventuremap_bg_60001.png',
                     'game_source':'ui_adventuremap_bg_60001','purpose':'非抽卡页面原作背景',
                     'dimensions':dimensions,'crop':'cover，居中裁切背景以适配页面',
                     'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                     'rights':'SEGA 或对应权利人；不属于插件代码开源许可'})

    original=source.parent/'upstream_ongeki_generic/assets/ongeki/back_character.webp'
    target=destination/'images/ui_character_background.png'
    with Image.open(original) as image:
        dimensions=list(image.size)
        image.convert('RGB').save(target)
    manifest.append({'key':'ui_character_background','path':'images/ui_character_background.png',
                     'extracted_source':str(original.relative_to(source.parent)),
                     'purpose':'用户指定的游戏角色背景：仅好感和档案的立绘展示区域',
                     'dimensions':dimensions,'crop':'人物区等比 cover，居中裁切；不作为整页背景',
                     'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                     'rights':'SEGA 或对应权利人；不属于插件代码开源许可'})

    reward_assets = source / 'assets'
    plate_owner, attachment_owner = {}, {}
    for character in characters:
        for reward in character['rewards']:
            if reward['kind'] == 'NamePlate':
                plate_owner[int(reward['id'])] = int(character['id'])
            elif reward['kind'] == 'Attachment':
                attachment_owner[int(reward['id'])] = int(character['id'])
    reward_specs = []
    for rid in sorted(plate_owner):
        reward_specs.append((f'reward_plate_{rid}', f'ui_userplate_{rid:06d}', '名牌奖励图', plate_owner[rid]))
        reward_specs.append((f'reward_plate_icon_{rid}', f'ui_userplate_icon_{rid:06d}', '名牌奖励图标', plate_owner[rid]))
    for rid in sorted(attachment_owner):
        reward_specs.append((f'reward_attachment_{rid}', f'ui_attachment_{rid:06d}', '装饰奖励图', attachment_owner[rid]))
    for key, folder, purpose, owner in reward_specs:
        images = sorted(p for p in (reward_assets / folder).rglob('*.png') if p.is_file())
        if len(images) != 1:
            raise ValueError(f'奖励素材不唯一: {folder}')
        relative = f'images/{key}.png'
        target = destination / relative
        shutil.copyfile(images[0], target)
        with Image.open(target) as image:
            dimensions = list(image.size); alpha = 'A' in image.getbands()
        manifest.append({'key': key, 'path': relative,
                         'extracted_source': str(images[0].relative_to(source.parent.parent)),
                         'purpose': purpose, 'character_id': owner,
                         'dimensions': dimensions, 'alpha': alpha, 'crop': 'contain/full_image',
                         'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                         'rights': 'SEGA 或对应权利人；不属于插件代码开源许可'})

    ui_roots = [source / 'ui', source / 'ui_extra', source / 'profile_rewards/ui']
    for key, pattern in UI_KIT.items():
        found = _find_ui(ui_roots, pattern)
        if found is None:
            raise ValueError(f'游戏UI素材缺失: {key} ({pattern})')
        relative = f'images/{key}.png'
        target = destination / relative
        shutil.copyfile(found, target)
        with Image.open(target) as image:
            dimensions = list(image.size); alpha = 'A' in image.getbands()
        manifest.append({'key': key, 'path': relative,
                         'extracted_source': str(found.relative_to(source.parent.parent)),
                         'game_source': f"{found.parent.name}.assets",
                         'purpose': '游戏UI套件：面板、名牌条、等级、按键、量表',
                         'dimensions': dimensions, 'alpha': alpha, 'crop': '完整图像，面板按端帽拉伸',
                         'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                         'rights': 'SEGA 或对应权利人；不属于插件代码开源许可'})

    for key, pattern in INTIMATE_KIT.items():
        found = _find_ui(ui_roots, pattern)
        if found is None:
            raise ValueError(f'好感量表素材缺失: {key} ({pattern})')
        relative = f'images/intimate/{key}.png'
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(found, target)
        with Image.open(target) as image:
            dimensions = list(image.size); alpha = 'A' in image.getbands()
        manifest.append({'key': f'intimate_{key}', 'path': relative,
                         'extracted_source': str(found.relative_to(source.parent.parent)),
                         'game_source': f"{found.parent.name}.assets",
                         'purpose': '好感量表分档：低档、10档、1000+挡位、填充、上限',
                         'dimensions': dimensions, 'alpha': alpha,
                         'crop': '完整图像；填充按原作心形内腔裁切',
                         'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                         'rights': 'SEGA 或对应权利人；不属于插件代码开源许可'})

    upstream = source.parent / 'upstream_ongeki_generic/assets/ongeki'
    for key, relative_source in CARD_REVEAL_KIT.items():
        original = upstream / relative_source
        if not original.is_file():
            raise ValueError(f'卡牌揭示素材缺失: {key} ({relative_source})')
        relative = f'images/card_reveal/{key}.webp'
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        with Image.open(target) as image:
            dimensions = list(image.size); alpha = 'A' in image.getbands()
        manifest.append({'key': f'card_reveal_{key}', 'path': relative,
                         'extracted_source': f"../upstream_ongeki_generic/assets/ongeki/{relative_source}",
                         'purpose': '卡牌揭示：稀有度Logo、属性图标、卡框',
                         'dimensions': dimensions, 'alpha': alpha,
                         'crop': '完整图像',
                         'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                         'rights': 'SEGA 或对应权利人；不属于插件代码开源许可'})

    for key, source_name in CARD_GET_KIT.items():
        original = source / 'card_get_ui' / f'{source_name}.png'
        if not original.is_file():
            raise ValueError(f'CardGet 素材缺失: {source_name}，请先运行 archive/scripts/extract_card_get_ui.py')
        relative = f'images/card_reveal/{key}.png'
        target = destination / relative
        shutil.copyfile(original, target)
        with Image.open(target) as image:
            dimensions = list(image.size); alpha = 'A' in image.getbands()
        manifest.append({'key': f'card_get_{key}', 'path': relative,
                         'extracted_source': f'card_get_ui/{source_name}.png',
                         'purpose': '原作卡牌揭示：标题、class、NEW/限界突破横幅、背景线',
                         'dimensions': dimensions, 'alpha': alpha,
                         'crop': '完整图像',
                         'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                         'rights': 'SEGA 或对应权利人；不属于插件代码开源许可'})

    (destination/'visual_asset_manifest.json').write_text(json.dumps({'version':'growth-assets-v2','assets':manifest},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(f'Packaged {len(manifest)} referenced original assets')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=Path(__file__).parents[1]/'assets/growth')
    parser.add_argument('--official',type=Path)
    args=parser.parse_args();build(args.source,args.output,args.official)
