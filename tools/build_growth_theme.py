"""生成本次界面改版需要的资源（可重复执行覆盖）。

产出 character_theme.json（每个主角色的主题色，用于好感页与奖励页的渐变背景）。
好感页右上角的 Q 版直接用 assets/growth/images/character_角色ID.png
（＝原作 ui_friendship_bt_* 的基础帧），满级后换成 reward_attachment_*.png。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..starter_cards import STARTER_CARDS

THEME_VERSION = 'growth-theme-v1'
# 原始 AttachmentModel 的 FX_hy_50color_* 文件名明确标注了角色专属色及 RGB。
# 不再从立绘取色：衣服、肤色和卡面属性都会污染角色主题色。
MANUAL_PRIMARY = {
    1000: '#FF999E', 1001: '#FFEA73', 1002: '#4791FF',
    1003: '#8D5CE0', 1004: '#FF71D9', 1005: '#50BFA3',
    1006: '#D169ED', 1007: '#FFF2F4', 1008: '#484878',
    1009: '#CED1D9', 1010: '#94F453', 1011: '#CC0000',
    1012: '#BAF4FF', 1013: '#FFBAD4', 1014: '#FFD427',
    1015: '#4F9BAB', 1016: '#604AA3',
}


def hex_mix(base, target, ratio: float) -> str:
    values = [round(base[index] + (target[index] - base[index]) * ratio) for index in range(3)]
    return '#%02X%02X%02X' % tuple(max(0, min(255, value)) for value in values)


def main() -> None:
    root = Path(__file__).parents[1]
    themes: dict[str, dict[str, str]] = {}
    for cid in sorted(STARTER_CARDS):
        manual = MANUAL_PRIMARY.get(cid)
        if manual:
            base = [int(manual[index:index + 2], 16) for index in (1, 3, 5)]
            themes[str(cid)] = {
                'primary': manual,
                'light': hex_mix(base, (255, 255, 255), 0.70),
                'tint': hex_mix(base, (255, 255, 255), 0.40),
                'deep': hex_mix(base, (28, 34, 52), 0.55),
            }
        else:
            raise ValueError(f'角色 {cid} 缺少原始专属色，禁止用立绘自动取色')
    (root / 'assets/growth/character_theme.json').write_text(
        json.dumps({'version': THEME_VERSION, 'characters': themes},
                   ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(f'{len(themes)} 组角色主题色 -> {root / "assets/growth"}')
    for cid, colors in list(themes.items())[:3]:
        print(' ', cid, colors)


if __name__ == '__main__':
    main()
