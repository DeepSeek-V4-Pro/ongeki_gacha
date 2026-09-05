#!/usr/bin/env python3
"""按上游拼接逻辑合成音击卡面（浏览器渲染版）。

本脚本复用上游验证过的 `compose_standard_cards.py` /
`compose_ver152_new_cards.py` 的 HTML/CSS 叠加方式：

* 通用背景 / 边框 / 属性 / 稀有度 / 学年图标；
* 角色 P 图层；
* SEGA Humming 字体与旋转卡名文字。

脚本只接受用户已经取得的本地图片和字体，不进行网络下载、网站抓取或游戏包解包。

用法示例:

    python compose_card_art.py `
      --chara-dir ./card-chara-p `
      --layers-dir ./general-layers `
      --info assets/card_data/card_info_merged.json `
      --out temp/card_art_browser

需要已安装 Playwright（可选）：

    pip install playwright
    playwright install chromium
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import os
import shutil
import sys
from pathlib import Path

from card_asset_tools import (
    ATTR_CODES,
    DEFAULT_INFO,
    DEFAULT_STAGING,
    LayerSet,
    clean_card_name,
    choose_path,
    load_card_info,
    parse_ids,
    scan_source,
    sid,
)


GRADE_CODES = {
    "高校1年生": "1",
    "高校2年生": "2",
    "高校3年生": "3",
    "中学1年生": "4",
    "中学2年生": "5",
    "中学3年生": "6",
}


def resolve_path(value: str, base: Path | None = None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path if base is not None else Path(__file__).resolve().parent / path
    return path.resolve()


def file_uri(path: Path) -> str:
    return path.as_uri()


def background_candidates(rarity: str, attribute: str) -> list[str]:
    attr = ATTR_CODES.get(attribute, "00")
    return [
        f"bg_{rarity}_{attr}.webp",
        f"UI_Card_BG_{rarity}_{attr}.webp",
    ]


def frame_candidates(rarity: str, attribute: str) -> list[str]:
    attr = ATTR_CODES.get(attribute, "00")
    return [
        f"frame_{rarity}_{attr}.webp",
        f"UI_Card_frame_{rarity}_{attr}.webp",
        f"UI_Card_Frame_{rarity}_{attr}.webp",
    ]


def attribute_candidates(attribute: str) -> list[str]:
    attr = ATTR_CODES.get(attribute, "00")
    return [
        f"attr_{attribute}.webp",
        f"UI_Card_Attribute_{attribute}.webp",
        f"UI_Card_Attribute_{attr}_Red.webp",
        f"UI_Card_Attribute_{attr}_Blue.webp",
        f"UI_Card_Attribute_{attr}_Green.webp",
    ]


def rarity_candidates(rarity: str) -> list[str]:
    numeric = {"N": "00", "R": "01", "SR": "02", "SRPlus": "05", "SSR": "03"}
    return [
        f"rare_{rarity}.webp",
        f"UI_Card_Rare_{numeric.get(rarity, '00')}_{rarity}.webp",
        f"UI_Card_Rare_{rarity}.webp",
    ]


def grade_candidates(grade: str) -> list[str]:
    code = GRADE_CODES.get(grade, "")
    if not code:
        return []
    return [
        f"grade_{code}.webp",
        f"UI_Card_Grade_{int(code):05d}.webp",
    ]


def render_card_html(
    card: dict,
    chara_path: Path,
    layers: LayerSet,
    font_path: Path | None,
) -> str:
    cid = int(card["id"])
    sid_value = sid(cid)
    rarity = str(card.get("rarity") or "R")
    attribute = str(card.get("attribute") or "Fire")
    attr_code = ATTR_CODES.get(attribute, "00")
    grade = GRADE_CODES.get(str(card.get("gakunen") or ""), "")

    bg_path = layers.find(background_candidates(rarity, attribute))
    bg = file_uri(bg_path) if bg_path else ""
    chara = file_uri(chara_path)
    frame_path = layers.find(frame_candidates(rarity, attribute))
    frame = file_uri(frame_path) if frame_path else ""
    attr_path = layers.find(attribute_candidates(attribute))
    attr_img = file_uri(attr_path) if attr_path else ""
    rare_path = layers.find(rarity_candidates(rarity))
    rare_img = file_uri(rare_path) if rare_path else ""
    grade_path = layers.find(grade_candidates(str(card.get("gakunen") or "")))
    grade_img = file_uri(grade_path) if grade_path else ""

    nick = html_mod.escape(str(card.get("nickName") or ""))
    name = html_mod.escape(
        clean_card_name(
            str(card.get("name") or ""),
            rarity,
            str(card.get("nickName") or ""),
        )
    )
    frame_before = (
        f'<div class="layer frame" style="background-image:url({frame})"></div>'
        if rarity in ("N", "R") else ""
    )
    frame_after = (
        f'<div class="layer frame" style="background-image:url({frame})"></div>'
        if rarity not in ("N", "R") else ""
    )
    grade_html = f'<img class="grade" src="{grade_img}">' if grade else ""
    return f"""
    <div class="container" style="background-image:{('url(' + bg + ')') if bg else 'none'}">
      {frame_before}
      <div class="layer chara" style="background-image:url({chara})"></div>
      {frame_after}
      <img class="attribute" src="{attr_img}">
      <img class="rare" src="{rare_img}">
      {grade_html}
      <div class="name">
        <div class="name-shadow">
          <div class="title nick">{nick}</div>
          <div class="title chara">{name}</div>
        </div>
        <div class="name-text">
          <div class="title nick">{nick}</div>
          <div class="title chara">{name}</div>
        </div>
      </div>
    </div>
    """


CSS = """
@font-face {
  font-family: "SEGA Humming";
  src: local("SEGA Humming"), url("FILE_FONT");
}
* { box-sizing: border-box; }
html, body { margin:0; padding:0; width:768px; height:1052px; overflow:hidden; background:transparent; }
.container {
  position: relative; width:768px; height:1052px; overflow:hidden;
  background-size:cover; background-position:center;
  font-family:"SEGA Humming", sans-serif;
}
.layer { position:absolute; inset:0; background-size:cover; background-position:center; }
.chara { z-index:2; }
.frame { z-index:3; }
.attribute { width:130px; top:84px; left:92px; transform:translate(-50%,-50%); position:absolute; z-index:4; }
.rare { width:200px; top:26px; left:123px; position:absolute; z-index:4; }
.grade { width:92px; top:0; right:23px; position:absolute; z-index:4; }
.name {
  color:#fff; width:100%; height:105px; text-align:right; top:705px; right:54px;
  position:absolute; z-index:5; font-family:"SEGA Humming", sans-serif; transform:rotate(-6deg);
}
.name-shadow { position:absolute; top:calc(50% + 3px); right:-3px; color:#2592C1; -webkit-text-stroke:7px #2592C1; transform:translate(0,-50%); }
.name-text { position:absolute; top:50%; right:0; transform:translate(0,-50%); }
.title { white-space:nowrap; line-height:1.05; }
.nick { font-size:21.5px; }
.chara { font-size:41.5px; }
.page { display:none; }
.page.show { display:block; }
"""


def font_path_for(layer_roots: list[Path], explicit: Path | None) -> Path | None:
    if explicit is not None and explicit.is_file():
        return explicit
    for root in layer_roots:
        for name in ("SEGA_Humming_v2-B.ttf", "SEGA_Humming_v2-B.ttc"):
            candidate = root / name
            if candidate.is_file():
                return candidate
    fallback = Path(__file__).resolve().parent / "assets" / "ui" / "SEGA_Humming_v2-B.ttf"
    if fallback.is_file():
        return fallback.resolve()
    return None


def browser_executable(explicit: Path | None) -> str | None:
    if explicit is not None and explicit.is_file():
        return str(explicit)
    for name in ("msedge", "chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", "")
    program_files = os.environ.get("PROGRAMFILES", "")
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidates: list[Path] = []
    if program_files_x86:
        candidates.append(
            Path(program_files_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        )
        candidates.append(
            Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe"
        )
    if program_files:
        candidates.append(
            Path(program_files) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        )
        candidates.append(
            Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe"
        )
    if local_app_data:
        candidates.append(
            Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe"
        )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def build_html(cards: list[dict], path_by_id: dict[int, Path], layers: LayerSet, font_path: Path | None) -> str:
    pages = []
    for card in cards:
        cid = int(card["id"])
        chara_path = path_by_id[cid]
        pages.append(
            f'<div id="card-{cid}" class="page">{render_card_html(card, chara_path, layers, font_path)}</div>'
        )
    css = CSS.replace("FILE_FONT", file_uri(font_path) if font_path else "")
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head>
<body>{''.join(pages)}<script>
function show(id) {{
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('show'));
  const el=document.getElementById('card-'+id);
  if(el) el.classList.add('show');
}}
show({cards[0]['id'] if cards else 0});
</script></body></html>"""


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="按上游图层顺序用浏览器渲染音击近似卡面（仅处理本地素材）",
    )
    parser.add_argument("--chara-dir", "--source", dest="chara_dirs", action="append",
                        required=True, help="角色 P 图层目录；可重复")
    parser.add_argument("--layers-dir", "--layers", dest="layer_dirs", action="append",
                        default=[], help="通用背景/边框/图标目录；可重复")
    parser.add_argument("--info", "--source-json", default=str(DEFAULT_INFO),
                        help="卡牌信息 JSON")
    parser.add_argument("--out", default=str(DEFAULT_STAGING / "browser"),
                        help="输出目录")
    parser.add_argument("--ids", action="append", default=[], help="卡 ID；支持逗号和区间")
    parser.add_argument("--font", help="SEGA Humming 字体路径")
    parser.add_argument("--browser-path", help="Chromium/Edge 可执行文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只显示计划")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "未安装 playwright。可执行 pip install playwright "
            "和 playwright install chromium；"
            "或使用 card_asset_tools.py compose 的 Pillow 近似合成。",
            file=sys.stderr,
        )
        return 2

    chara_dirs = [resolve_path(value) for value in args.chara_dirs]
    layer_dirs = [resolve_path(value) for value in args.layer_dirs]
    info_path = resolve_path(args.info)
    out_dir = resolve_path(args.out)
    rows = load_card_info(info_path)
    rows_by_id = {int(row["id"]): row for row in rows}
    selected = parse_ids(args.ids) if args.ids else list(rows_by_id)
    selected_set = set(selected)
    by_id = scan_source(chara_dirs)
    layers = LayerSet(layer_dirs)
    font_path = font_path_for(layer_dirs, Path(args.font).resolve() if args.font else None)

    selected_rows: list[dict] = []
    path_by_id: dict[int, Path] = {}
    for card_id in sorted(selected_set):
        if card_id not in rows_by_id:
            continue
        record = by_id.get(card_id)
        chara = choose_path(record, "chara_p") if record else None
        chara = chara or (choose_path(record, "chara") if record else None)
        if chara is None:
            print(f"[WARN] 缺少角色图层，跳过: {card_id}", file=sys.stderr)
            continue
        selected_rows.append(rows_by_id[card_id])
        path_by_id[card_id] = chara

    if not selected_rows:
        print("没有可合成的素材。", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"[DRY RUN] 将合成 {len(selected_rows)} 张卡面 -> {out_dir}")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "card_render.html"
    html_path.write_text(
        build_html(selected_rows, path_by_id, layers, font_path),
        encoding="utf-8",
    )

    executable = browser_executable(Path(args.browser_path).resolve() if args.browser_path else None)
    if executable:
        print(f"浏览器: {executable}")
    else:
        print("未找到 Edge/Chrome，将使用 Playwright 自带 Chromium。", file=sys.stderr)

    rendered: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=executable,
            headless=True,
            args=["--disable-gpu", "--allow-file-access-from-files"],
        )
        page = browser.new_page(viewport={"width": 768, "height": 1052}, device_scale_factor=1)
        page.goto(html_path.as_uri())
        page.wait_for_timeout(800)
        for idx, card in enumerate(selected_rows, 1):
            cid = int(card["id"])
            page.evaluate(f"show({cid})")
            page.wait_for_timeout(120)
            dest = out_dir / f"ui_card_{sid(cid)}.png"
            clip = {"x": 0, "y": 0, "width": 768, "height": 1052}
            try:
                page.screenshot(path=str(dest), clip=clip, omit_background=True)
            except TypeError:
                page.screenshot(path=str(dest), clip=clip)
            rendered.append(
                {
                    "id": cid,
                    "name": card.get("name", ""),
                    "output": str(dest),
                }
            )
            if idx % 25 == 0:
                print(f"rendered {idx}/{len(selected_rows)}", flush=True)
        browser.close()

    report = {
        "count": len(rendered),
        "layers": [str(path) for path in layer_dirs],
        "chara_dirs": [str(path) for path in chara_dirs],
        "items": rendered,
    }
    (out_dir / "compose_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"完成: {len(rendered)} 张 -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
