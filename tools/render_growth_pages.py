"""一次性渲染本次新增的全部养成图片界面，供视觉评审使用。

输出到 output/character_affection/visual_samples/growth_pages_<日期>/：
好感页（默认/满级/中途/卡面）、礼物背包、全部好感奖励分页、列表卡片，并生成 index.html。
"""
from __future__ import annotations

import html
import json
from datetime import date
from pathlib import Path
import time

from ..gacha_core import load_cards
from ..gacha_render import GachaRenderer
from ..growth_catalog import GrowthCatalog
from ..growth_core import affection_level
from ..growth_render import render_affection, render_gift_inventory
from ..profile_render import render_reward_pages
from ..reward_catalog import RewardCatalog
from ..starter_cards import STARTER_CARDS
from ..text_render import render_text_card


def snapshot_for(character: dict, level: int, *, decorated: bool) -> dict:
    """构造评审用快照：按等级给出解锁状态与装备状态。"""
    cid = int(character['id'])
    claims = [r for r in character['rewards'] if int(r['level']) <= level]
    cosmetics = []
    profile = {}
    if decorated:
        for kind, column in (('Trophy', 'title_id'), ('Attachment', 'attachment_id')):
            chosen = next((r for r in reversed(claims) if r['kind'] == kind), None)
            if chosen is None:
                continue
            cosmetics.append({'cosmetic_type': kind, 'cosmetic_id': str(chosen['id'])})
            profile[column] = str(chosen['id'])
    copies = 1 + sum(r['kind'] == 'NormalCard' for r in claims)
    return {
        'affection_reward_claims': claims,
        'inventory': [{'card_id': STARTER_CARDS[cid], 'copies': min(copies, 11)}],
        'player_growth_profile': [profile] if profile else [],
        'player_cosmetics': cosmetics,
    }


def cosmetics_lines(rewards: RewardCatalog, cid: int) -> list[str]:
    rows = [r for r in rewards.rewards(cid) if r['kind'] in ('Trophy', 'Attachment')]
    lines = ['已解锁的好感页装扮']
    for row in rows:
        label = '称号' if row['kind'] == 'Trophy' else '装饰'
        lines += [row['name'], f"/装扮 {label} {row['id']}"]
    lines += ['/装扮 称号 卸下', '/装扮 装饰 卸下', '/好感']
    return lines


def voice_lines(assets: Path, catalog: GrowthCatalog, cid: int) -> list[str]:
    rows = json.loads((assets / 'growth/voice_catalog.json').read_text(encoding='utf8'))['voices']
    picked = sorted((row for row in rows if int(row['character_id']) == cid),
                    key=lambda row: int(row['unlock_level']))
    lines = [f"{catalog.characters[cid]['name']} · 角色语音",
             '养成自动回应：小礼物、中/大礼物、好感升级',
             '实际增益后最多一条，升级优先；需语音开关开启且素材通过试听。',
             '以下为需好感奖励解锁的档案语音：']
    for number, row in enumerate(picked, 1):
        lines += [f"{number:02d} · Lv{row['unlock_level']} · 已解锁",
                  f"/角色语音 {catalog.characters[cid]['name']} {number}"]
    return lines


def affection_list_lines(catalog: GrowthCatalog, partner: int = 1000) -> list[str]:
    """与 /好感 列表 相同的文案，避免为渲染引入 SDK 依赖。"""
    lines = []
    for cid, character in catalog.characters.items():
        level = affection_level(0, catalog.thresholds)
        lines.append(f"{character['name']}｜Lv{level}" + ('｜伙伴' if cid == partner else ''))
    lines.append('/好感 星咲 あかり')
    return lines


def main() -> None:
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--other-only",action="store_true")
    options=parser.parse_args()
    root = Path(__file__).parents[1]
    assets = root / 'assets'
    cards = load_cards(assets / 'card_data/card_info_merged.json')
    catalog = GrowthCatalog(assets / 'growth', cards)
    rewards = RewardCatalog(assets / 'growth')
    renderer = GachaRenderer(assets / 'card_data', assets / 'ui')
    output = (root.parent / 'output/character_affection/visual_samples'
              / f'growth_{"other" if options.other_only else "pages"}_{date.today():%Y%m%d}')
    output.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    timings: list[dict] = []

    def render(section: str, label: str, fn, *args, **kwargs):
        start = time.perf_counter()
        result = fn(*args, **kwargs)
        paths = list(result) if isinstance(result, (list, tuple)) else [result]
        for index, path in enumerate(paths):
            entries.append({'section': section, 'label': label, 'file': path.name,
                            'page': index + 1, 'pages': len(paths)})
        timings.append({'label': label, 'seconds': round(time.perf_counter() - start, 3),
                        'files': [p.name for p in paths]})
        return result

    if not options.other_only:
        for cid, character in catalog.characters.items():
            render('好感页 · 默认', f"{character['name']} · Lv0", render_affection, catalog, cid, 0, None,
                   output / f'affection_default_{cid}.png', partner=cid == 1000,
                   snapshot=snapshot_for(character, 0, decorated=False),
                   rewards=list(rewards.by_key.values()))
            render('好感页 · 满级装扮', f"{character['name']} · Lv1000", render_affection, catalog, cid,
                   catalog.thresholds[1000], None, output / f'affection_full_{cid}.png', partner=cid == 1000,
                   snapshot=snapshot_for(character, 1000, decorated=True),
                   rewards=list(rewards.by_key.values()))

        partial = catalog.characters[1013]
        render('好感页 · 其他形态', '柏木美亜 · Lv300 中途', render_affection, catalog, 1013,
               catalog.thresholds[300], None, output / 'affection_partial_1013.png', partner=True,
               snapshot=snapshot_for(partial, 300, decorated=True), rewards=list(rewards.by_key.values()))
        render('好感页 · 其他形态', '柏木美亜 · 好感 99/99', render_affection, catalog, 1013,
               catalog.thresholds[9999], None, output / 'affection_99_99_1013.png', partner=True,
               snapshot=snapshot_for(partial, 1000, decorated=True), rewards=list(rewards.by_key.values()))
        showcase = next(c for c in cards.cards if c.character_id == 1000 and c.rarity == 'SSR')
        render('好感页 · 其他形态', f'星咲 あかり · 展示卡面 {showcase.id}', render_affection, catalog, 1000,
               catalog.thresholds[500], renderer._load_card(showcase), output / 'affection_card_1000.png',
               partner=True, portrait_mode='card',
               snapshot=snapshot_for(catalog.characters[1000], 500, decorated=True),
               rewards=list(rewards.by_key.values()))

    render('礼物背包', '礼物与碎片（含购买额度）', render_gift_inventory,
           {'gift_small': 4, 'gift_medium': 1, 'gift_large': 1, 'flower_fragment': 76},
           output / 'gift_inventory.png',
           {'small': {'price': 150, 'cap': 10, 'used': 3, 'left': 7},
            'medium': {'price': 500, 'cap': 3, 'used': 1, 'left': 2}})

    render('礼物背包', '空背包与本周额度用尽', render_gift_inventory,
           {}, output / 'gift_empty.png',
           {'small': {'price': 150, 'cap': 10, 'left': 0},
            'medium': {'price': 500, 'cap': 3, 'left': 0}})
    render('礼物背包', '大数值排版检查', render_gift_inventory,
           {'gift_small': 123456789, 'gift_medium': 999999, 'flower_fragment': 1234567890123},
           output / 'gift_large_counts.png',
           {'small': {'price': 123456, 'cap': 9999, 'left': 9999}})

    for cid, character in catalog.characters.items():
        render('好感奖励分页', character['name'], render_reward_pages, catalog, rewards.rewards(cid), cid,
               snapshot_for(character, 300, decorated=True), output / f'rewards_{cid}.png')

    render('列表卡片', '好感列表', render_text_card, '音击收藏模拟器 · 好感列表',
           affection_list_lines(catalog), output / 'text_affection_list.png')
    render('列表卡片', '装扮列表', render_text_card, '音击收藏模拟器 · 装扮',
           cosmetics_lines(rewards, 1013), output / 'text_cosmetics.png')
    render('列表卡片', '角色语音列表', render_text_card, '音击收藏模拟器 · 角色语音',
           voice_lines(assets, catalog, 1013), output / 'text_voice_list.png')

    sections: dict[str, list[dict]] = {}
    for entry in entries:
        sections.setdefault(entry['section'], []).append(entry)
    blocks = []
    for section, rows in sections.items():
        cells = []
        for row in rows:
            suffix = f' · {row["page"]}/{row["pages"]}' if row['pages'] > 1 else ''
            cells.append(f'<article><a href="{html.escape(row["file"])}">'
                         f'<img src="{html.escape(row["file"])}" loading="lazy"></a>'
                         f'<p>{html.escape(row["label"])}{suffix}</p></article>')
        blocks.append(f'<h2>{html.escape(section)}<span>{len(rows)} 张</span></h2>'
                      f'<section>{"".join(cells)}</section>')
    (output / 'index.html').write_text(
        '<!doctype html><meta charset="utf-8"><title>养成界面总览</title><style>'
        'body{background:#172335;color:#EAF2FF;font:15px/1.5 "Microsoft YaHei",sans-serif;margin:0;padding:28px}'
        'h1{font-size:24px;margin:0 0 6px}h2{font-size:19px;margin:32px 0 12px;border-left:4px solid #168EAF;padding-left:10px}'
        'h2 span{font-size:13px;color:#8FA6C0;margin-left:10px}'
        'section{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px}'
        'article{background:#202C42;border-radius:12px;padding:8px}'
        'img{width:100%;border-radius:8px;display:block}'
        'p{margin:6px 2px 2px;font-size:13px;color:#BFD0E4}</style>'
        f'<h1>养成界面总览 · {len(entries)} 张</h1>'
    f'<p>生成时间 {time.strftime("%Y-%m-%d %H:%M")}；全部使用真实素材与评审快照，不含用户库记录。</p>'
        + ''.join(blocks), encoding='utf8')
    (output / 'manifest.json').write_text(
        json.dumps({'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'entries': entries,
                    'timings': timings}, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(f'{len(entries)} pages -> {output}')
    print(f'index: {output / "index.html"}')


if __name__ == '__main__':
    main()
