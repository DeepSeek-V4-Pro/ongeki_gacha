"""仅主角色的姓名解析；内部仍使用稳定ID，拒绝模糊猜测。"""
import unicodedata

ALIASES = {
    1000: ('星咲明','星咲あかり'), 1001: ('藤泽柚子','藤澤柚子'),
    1002: ('三角葵',), 1003: ('高濑梨绪','高瀬梨緒'),
    1004: ('结城莉玖','結城莉玖'), 1005: ('蓝原椿','藍原椿'),
    1006: ('早乙女彩华','早乙女彩華'), 1007: ('樱井春菜','桜井春菜'),
    1008: ('九条枫','九條楓'), 1009: ('柏木咲姬','柏木咲姫'),
    1010: ('井之原小星',), 1011: ('逢坂茜',), 1012: ('珠洲岛有栖','珠洲島有栖'),
    1013: ('柏木美亚','柏木美亜'), 1014: ('日向千夏',),
    1015: ('东云纺','東雲紬'), 1016: ('皇城刹那','皇城セツナ'),
}


def normalize(value):
    return ''.join(unicodedata.normalize('NFKC',str(value)).split()).casefold()


def name(catalog, cid):
    return normalize(catalog.characters[cid]['name'])


def resolve(catalog, value):
    value=normalize(value)
    if value.isdecimal() and int(value) in catalog.characters:return int(value)
    matches=[cid for cid,character in catalog.characters.items()
             if value in {normalize(character['name']),*(normalize(n) for n in ALIASES.get(cid,()))}]
    if len(matches)!=1:raise ValueError('未找到主角色姓名，请用 /好感 列表 查看完整姓名')
    return matches[0]


def arguments(catalog, action, raw):
    parts=str(raw or '').split()
    if not parts:return []
    if action=='好感':
        if len(parts)==1 and parts[0] in {'列表','总览'}:return ['列表']
        index=parts.index('卡面') if '卡面' in parts else len(parts)
        return [str(resolve(catalog,''.join(parts[:index]))),*parts[index:]]
    if action=='角色语音':
        if len(parts)==1 and parts[0] in {'分类','类别'}:return ['分类']
        if parts[-1].isdigit() and len(parts)>1:
            return [str(resolve(catalog,''.join(parts[:-1]))),parts[-1]]
        return [str(resolve(catalog,''.join(parts)))]
    if action in {'好感奖励','伙伴'}:
        return [str(resolve(catalog,''.join(parts)))]
    if action=='送礼':
        index=next((i for i,p in enumerate(parts) if p in {'小','中','大'}),None)
        if index is None:raise ValueError('用法：/送礼 <角色姓名> 小/中/大 [数量]')
        return [str(resolve(catalog,''.join(parts[:index]))),*parts[index:]]
    return parts
