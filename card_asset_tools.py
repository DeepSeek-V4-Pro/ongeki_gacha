#!/usr/bin/env python3
"""音击抽卡插件的卡面素材接入工具箱。

本脚本只处理用户已经取得的本地图片文件：

* 扫描素材目录，识别 6 位卡牌 ID；
* 从角色图层和通用图层合成 768x1052 近似标准卡面；
* 将成品卡面按 `ui_card_<6位ID>.png` 接入插件数据目录；
* 校验接入后的文件、尺寸、JSON 和 manifest。

脚本不进行网络下载、网站抓取、游戏包解包、解密或其他素材获取操作。
用户必须自行确认并取得有权使用的素材。

用法示例:

    python card_asset_tools.py scan --source ./cards --report temp/scan.json
    python card_asset_tools.py compose --source ./layers --layers ./ui --out temp/card_art
    python card_asset_tools.py import --source ./cards
    python card_asset_tools.py verify

更接近上游图层/字体排版的浏览器合成请使用 compose_card_art.py。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont, ImageOps


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = SCRIPT_DIR / "assets" / "card_data"
DEFAULT_INFO = DEFAULT_DATA_DIR / "card_info_merged.json"
DEFAULT_POOLS = DEFAULT_DATA_DIR / "gacha_pools.json"
DEFAULT_STAGING = SCRIPT_DIR / "temp" / "card_art"
CARD_SIZE = (768, 1052)
IMAGE_EXTS = {".png", ".webp", ".jpg", ".jpeg", ".gif", ".bmp"}

ATTR_CODES = {"Fire": "00", "Aqua": "01", "Leaf": "02"}
RARITY_NUMERIC = {"N": "00", "R": "01", "SR": "02", "SRPlus": "05", "SSR": "03"}
GRADE_CODES = {
    "高校1年生": "1",
    "高校2年生": "2",
    "高校3年生": "3",
    "中学1年生": "4",
    "中学2年生": "5",
    "中学3年生": "6",
}


def resolve_path(value: str, base: Path | None = None) -> Path:
    """将命令行路径解析为绝对路径。

    相对路径默认以脚本所在插件目录为基准，从任意工作目录运行都不容易跑错。
    """

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path if base is not None else SCRIPT_DIR / path
    return path.resolve()


def load_json(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(f"JSON 文件不存在: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def sid(card_id: int) -> str:
    return str(int(card_id)).zfill(6)


def parse_ids(values: Iterable[str]) -> list[int]:
    """解析 --ids 参数，支持逗号和 a-b 区间。"""

    result: list[int] = []
    for raw in values:
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                left, right = part.split("-", 1)
                if left.strip().isdigit() and right.strip().isdigit():
                    start = int(left)
                    end = int(right)
                    result.extend(range(min(start, end), max(start, end) + 1))
                    continue
            if part.isdigit():
                result.append(int(part))
            else:
                raise ValueError(f"无法解析卡牌 ID: {part}")
    return sorted(set(result))


def card_id_from_name(path: Path) -> int | None:
    """从文件名识别 6 位卡牌 ID。

    只匹配名称中明显包含卡片内容的文件，例如：

        ui_card_100001.png
        UI_Card_Chara_100001_P.webp
        ui_card_chara_100001_p.png
        100001.png

    icon / holo / mask 等附属图层会被忽略，避免把图标误当成卡面。
    """

    stem = path.stem
    lower = stem.lower()
    if any(keyword in lower for keyword in ("icon", "holo", "mask")):
        return None
    if not (
        lower.startswith("ui_card")
        or lower.startswith("card")
        or lower.isdigit()
        or "chara" in lower
    ):
        return None
    matches = list(re.finditer(r"(?<!\d)(\d{6})(?!\d)", stem))
    if not matches:
        return None
    # 文件名通常会同时包含资源分类和 ID，直接使用首个 6 位数字即可。
    return int(matches[0].group(1))


def classify_image(path: Path) -> str:
    """返回 finished / chara_p / chara / ignore。"""

    lower = path.stem.lower()
    if any(keyword in lower for keyword in ("icon", "holo", "mask")):
        return "ignore"
    if "chara" in lower:
        return "chara_p" if lower.endswith("_p") or "_p." in lower else "chara"
    return "finished"


def scan_source(
    sources: list[Path],
    mapping_path: Path | None = None,
) -> dict[int, dict[str, list[Path]]]:
    """扫描一个或多个素材目录，返回按卡牌 ID 分组的文件路径。"""

    by_id: dict[int, dict[str, list[Path]]] = {}
    for source in sources:
        if not source.is_dir():
            print(f"[WARN] 素材目录不存在，已跳过: {source}", file=sys.stderr)
            continue
        for path in source.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
                continue
            card_id = card_id_from_name(path)
            if card_id is None:
                continue
            kind = classify_image(path)
            if kind == "ignore":
                continue
            by_id.setdefault(card_id, {"finished": [], "chara_p": [], "chara": []})
            by_id[card_id].setdefault(kind, []).append(path)

    if mapping_path is not None:
        mapping_rows = load_mapping(mapping_path)
        for entry in mapping_rows:
            if not entry.get("id") or not entry.get("file"):
                continue
            try:
                card_id = int(entry["id"])
            except (TypeError, ValueError):
                continue
            rel = Path(str(entry["file"]))
            found = resolve_path(str(entry["file"]), sources[0] if sources else None)
            if not found.is_file():
                candidates = [
                    source / rel
                    for source in sources
                    if (source / rel).is_file()
                ]
                found = candidates[0] if candidates else found
            if not found.is_file():
                print(f"[WARN] 映射文件不存在，已跳过: {found}", file=sys.stderr)
                continue
            by_id.setdefault(card_id, {"finished": [], "chara_p": [], "chara": []})
            by_id[card_id]["finished"].append(found)

    for record in by_id.values():
        for kind in ("finished", "chara_p", "chara"):
            record[kind] = sorted(set(record[kind]), key=lambda p: str(p).lower())
    return by_id


def load_mapping(path: Path) -> list[dict[str, Any]]:
    """读取 file -> id 映射；支持 JSON 对象/数组和 CSV。"""

    if path.suffix.lower() == ".csv":
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                rows.append({key: row.get(key, "") for key in row})
        return rows

    payload = load_json(path)
    if isinstance(payload, dict):
        if "mapping" in payload and isinstance(payload["mapping"], list):
            return list(payload["mapping"])
        return [{"id": key, "file": value} for key, value in payload.items()]
    if isinstance(payload, list):
        return list(payload)
    raise ValueError("映射文件必须是 JSON 对象、{mapping: [...]} 或 CSV")


def choose_path(record: dict[str, list[Path]], kind: str) -> Path | None:
    values = record.get(kind) or []
    return values[0] if values else None


def preferred_path(record: dict[str, list[Path]]) -> tuple[str, Path | None]:
    """按成品卡面、角色 _P 图层、普通角色图层的顺序选择。"""

    for kind in ("finished", "chara_p", "chara"):
        path = choose_path(record, kind)
        if path is not None:
            return kind, path
    return "none", None


class LayerSet:
    """通用图层索引。

    同时兼容简化文件名（如 `bg_N_00.webp`）和上游规范文件名
    （如 `UI_Card_BG_N_00.webp`），大小写不敏感。
    """

    def __init__(self, roots: list[Path]) -> None:
        self.aliases: dict[str, Path] = {}
        for root in roots:
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
                    continue
                self.aliases.setdefault(path.name.lower(), path)
                self.aliases.setdefault(path.stem.lower(), path)

    def find(self, candidates: Iterable[str]) -> Path | None:
        for candidate in candidates:
            path = self.aliases.get(candidate.lower())
            if path is not None:
                return path
        return None

    def has_any(self) -> bool:
        return bool(self.aliases)


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
    return [
        f"rare_{rarity}.webp",
        f"UI_Card_Rare_{RARITY_NUMERIC.get(rarity, '00')}_{rarity}.webp",
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


def open_image(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGBA")


def fit_image(image: Image.Image, size: tuple[int, int] = CARD_SIZE) -> Image.Image:
    image = image.convert("RGBA")
    if image.size == size:
        return image
    return ImageOps.fit(
        image,
        size,
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )


def scale_to_width(image: Image.Image, width: int) -> Image.Image:
    image = image.convert("RGBA")
    height = max(1, round(image.height * width / max(image.width, 1)))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def clean_card_name(name: str, rarity: str, nick: str) -> str:
    result = name.replace("【SR+】", "【SRPlus】")
    if rarity:
        result = result.replace(f"【{rarity}】", "")
    if nick and f"[{nick}]" in result:
        result = result.replace(f"[{nick}]", "")
    return result.strip()


def find_font(
    paths: list[Path],
    size: int = 40,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in paths:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    bundled_font = SCRIPT_DIR / "assets" / "ui" / "SEGA_Humming_v2-B.ttf"
    if bundled_font.is_file():
        try:
            return ImageFont.truetype(str(bundled_font), size=size)
        except OSError:
            pass
    windows_dir = os.environ.get("WINDIR", "")
    if windows_dir:
        font_root = Path(windows_dir) / "Fonts"
        for path in (
            font_root / "msyhbd.ttc",
            font_root / "msyh.ttc",
            font_root / "simhei.ttf",
        ):
            if path.is_file():
                try:
                    return ImageFont.truetype(str(path), size=size)
                except OSError:
                    continue
    return ImageFont.load_default()


def draw_card_name(canvas: Image.Image, card: dict[str, Any], font_path: Path | None) -> None:
    """用 Pillow 近似绘制卡名。

    这是本地合成脚本的简化实现，不保证逐像素复刻 RinNET 的前端排版。
    """

    nick = str(card.get("nickName") or "").strip()
    name = clean_card_name(
        str(card.get("name") or ""),
        str(card.get("rarity") or ""),
        nick,
    )
    lines = [line for line in (nick, name) if line]
    if not lines:
        return

    text_canvas = Image.new("RGBA", (760, 160), (0, 0, 0, 0))
    draw = ImageDraw.Draw(text_canvas)
    y = 8
    for line in lines:
        size = 22 if line == nick else 42
        font = find_font([font_path] if font_path else [], size=size)
        draw.text(
            (740, y + 3),
            line,
            font=font,
            fill=(37, 146, 193, 255),
            anchor="rm",
            stroke_width=7,
            stroke_fill=(37, 146, 193, 255),
        )
        draw.text(
            (740, y),
            line,
            font=font,
            fill=(255, 255, 255, 255),
            anchor="rm",
        )
        y += size + 14

    rotated = text_canvas.rotate(
        -6,
        expand=True,
        resample=Image.Resampling.BICUBIC,
    )
    desired_center = (330, 760)
    canvas.alpha_composite(
        rotated,
        (
            round(desired_center[0] - rotated.width / 2),
            round(desired_center[1] - rotated.height / 2),
        ),
    )


def compose_card(
    card: dict[str, Any],
    chara_path: Path,
    layers: LayerSet,
    font_path: Path | None = None,
    *,
    skip_text: bool = False,
) -> Image.Image:
    """将角色图层和可选通用图层合成 768x1052 的近似净卡面。"""

    rarity = str(card.get("rarity") or "R")
    attribute = str(card.get("attribute") or "Fire")
    canvas = Image.new("RGBA", CARD_SIZE, (0, 0, 0, 0))
    chara = fit_image(open_image(chara_path))

    bg_path = layers.find(background_candidates(rarity, attribute))
    if rarity in ("N", "R") and bg_path:
        canvas.alpha_composite(fit_image(open_image(bg_path)))

    frame_path = layers.find(frame_candidates(rarity, attribute))
    frame_before = rarity in ("N", "R")
    if frame_path and frame_before:
        canvas.alpha_composite(fit_image(open_image(frame_path)))

    canvas.alpha_composite(chara)

    if frame_path and not frame_before:
        canvas.alpha_composite(fit_image(open_image(frame_path)))

    attr_path = layers.find(attribute_candidates(attribute))
    if attr_path:
        icon = scale_to_width(open_image(attr_path), 130)
        canvas.alpha_composite(icon, (92 - icon.width // 2, 84 - icon.height // 2))

    rare_path = layers.find(rarity_candidates(rarity))
    if rare_path:
        icon = scale_to_width(open_image(rare_path), 200)
        canvas.alpha_composite(icon, (123, 26))

    grade_path = layers.find(grade_candidates(str(card.get("gakunen") or "")))
    if grade_path:
        icon = scale_to_width(open_image(grade_path), 92)
        canvas.alpha_composite(icon, (768 - 23 - icon.width, 0))

    if not skip_text:
        draw_card_name(canvas, card, font_path)
    return canvas


def load_card_info(path: Path) -> list[dict[str, Any]]:
    rows = load_json(path)
    if not isinstance(rows, list):
        raise ValueError("卡牌信息 JSON 顶层必须是数组")
    return [row for row in rows if isinstance(row, dict)]


def save_prepared_image(source: Path, dest: Path, *, force_normalize: bool = False, dry_run: bool = False) -> str:
    """将一张图准备为标准 PNG。

    已经是 768x1052 PNG 时直接复制，避免无谓重编码；
    WebP/JPEG 等非 PNG 或尺寸不符时转成标准尺寸 PNG。
    """

    if dry_run:
        return "would_save"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() == ".png" and not force_normalize:
        try:
            with Image.open(source) as image:
                if image.size == CARD_SIZE:
                    shutil.copy2(source, dest)
                    return "copied"
        except Exception:
            pass

    with Image.open(source) as image:
        image.load()
        prepared = image.convert("RGBA")
        if prepared.size != CARD_SIZE:
            prepared = ImageOps.fit(
                prepared,
                CARD_SIZE,
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
        prepared.save(dest, format="PNG")
    return "converted"


def stage_card(
    card_id: int,
    card: dict[str, Any],
    index: dict[int, dict[str, list[Path]]],
    layer_set: LayerSet,
    staging: Path,
    *,
    mode: str,
    allow_raw: bool,
    force_normalize: bool,
    dry_run: bool,
) -> tuple[Path | None, str]:
    """为单张卡生成标准 PNG，返回 (预期目标路径, 结果说明)。"""

    record = index.get(card_id)
    if not record:
        return None, "missing"

    kind, source = preferred_path(record)
    if mode == "layers" and kind in ("finished",):
        layer_kind, source = "chara_p", choose_path(record, "chara_p")
        if source is None:
            layer_kind, source = "chara", choose_path(record, "chara")
        kind = layer_kind
    if mode == "finished" and kind != "finished":
        return None, "missing_finished"

    target = staging / f"ui_card_{sid(card_id)}.png"
    if kind == "finished" and source is not None:
        if (
            not force_normalize
            and source.suffix.lower() == ".png"
        ):
            try:
                with Image.open(source) as image:
                    if image.size == CARD_SIZE:
                        return source, "ready"
            except Exception:
                pass
        return target, save_prepared_image(
            source,
            target,
            force_normalize=force_normalize,
            dry_run=dry_run,
        )

    if kind in ("chara_p", "chara") and source is not None:
        if layer_set.has_any():
            if dry_run:
                return target, "would_compose"
            canvas = compose_card(card, source, layer_set)
            target.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(target, format="PNG")
            return target, "composed"
        if allow_raw:
            if (
                not force_normalize
                and source.suffix.lower() == ".png"
            ):
                try:
                    with Image.open(source) as image:
                        if image.size == CARD_SIZE:
                            return source, "ready"
                except Exception:
                    pass
            return target, save_prepared_image(
                source,
                target,
                force_normalize=force_normalize,
                dry_run=dry_run,
            )
    return None, "missing_layers"


def write_scan_report(
    report_path: Path,
    sources: list[Path],
    by_id: dict[int, dict[str, list[Path]]],
    info_rows: list[dict[str, Any]],
) -> None:
    expected = {int(row["id"]) for row in info_rows}
    found_ids = set(by_id)
    finished = {card_id for card_id, record in by_id.items() if record.get("finished")}
    chara_p = {card_id for card_id, record in by_id.items() if record.get("chara_p")}
    chara = {card_id for card_id, record in by_id.items() if record.get("chara")}
    payload = {
        "sources": [str(path) for path in sources],
        "expected_count": len(expected),
        "found_count": len(found_ids),
        "finished_count": len(finished & expected),
        "chara_p_count": len(chara_p & expected),
        "chara_count": len(chara & expected),
        "missing_expected": sorted(expected - found_ids),
        "extra_ids": sorted(found_ids - expected),
        "items": [
            {
                "id": card_id,
                "finished": [str(path) for path in record.get("finished", [])],
                "chara_p": [str(path) for path in record.get("chara_p", [])],
                "chara": [str(path) for path in record.get("chara", [])],
            }
            for card_id, record in sorted(by_id.items())
        ],
    }
    write_json(report_path, payload)
    print(f"已写入扫描报告: {report_path}")
    print(
        f"预期 {len(expected)} 张，发现 {len(found_ids)} 个 ID；"
        f"成品 {len(finished & expected)}，角色 P 图层 {len(chara_p & expected)}"
    )
    if expected - found_ids:
        print(f"缺少 ID 数: {len(expected - found_ids)}")
    if found_ids - expected:
        print(f"卡表外 ID 数: {len(found_ids - expected)}")


def cmd_scan(args: argparse.Namespace) -> int:
    sources = [resolve_path(value) for value in args.source]
    mapping = resolve_path(args.mapping) if args.mapping else None
    info_rows = load_card_info(resolve_path(args.info)) if args.info else []
    by_id = scan_source(sources, mapping)
    expected = {int(row["id"]) for row in info_rows}
    finished = {card_id for card_id, record in by_id.items() if record.get("finished")}
    chara_p = {card_id for card_id, record in by_id.items() if record.get("chara_p")}
    chara = {card_id for card_id, record in by_id.items() if record.get("chara")}
    print(f"扫描目录: {len(sources)} 个")
    print(f"预期卡牌: {len(expected)} 张")
    print(f"识别到 ID: {len(by_id)} 个")
    print(f"成品卡面: {len(finished & expected)} 张")
    print(f"角色 P 图层: {len(chara_p & expected)} 张")
    print(f"普通角色图层: {len(chara & expected)} 张")
    if expected - set(by_id):
        print(f"缺少素材 ID 示例: {sorted(expected - set(by_id))[:10]}")
    if set(by_id) - expected:
        print(f"卡表外 ID 示例: {sorted(set(by_id) - expected)[:10]}")
    if args.report:
        write_scan_report(
            resolve_path(args.report),
            sources,
            by_id,
            info_rows,
        )
    return 0


def cmd_compose(args: argparse.Namespace) -> int:
    sources = [resolve_path(value) for value in args.source]
    layer_roots = [resolve_path(value) for value in args.layers]
    info_path = resolve_path(args.info)
    out_dir = resolve_path(args.out)
    info_rows = load_card_info(info_path)
    by_id = scan_source(sources)
    layer_set = LayerSet(layer_roots)
    selected = parse_ids(args.ids) if args.ids else [int(row["id"]) for row in info_rows]
    selected_set = set(selected)
    rows_by_id = {int(row["id"]): row for row in info_rows}

    if not layer_set.has_any():
        print("[WARN] 未找到通用图层；--allow-raw 可仅导出原角色图层。", file=sys.stderr)
        if not args.allow_raw:
            print("未找到通用图层，无法合成标准卡面。", file=sys.stderr)
            return 1

    completed = 0
    skipped: list[int] = []
    for card_id in selected:
        if card_id not in selected_set:
            continue
        record = by_id.get(card_id)
        if not record:
            skipped.append(card_id)
            continue
        chara = choose_path(record, "chara_p") or choose_path(record, "chara")
        if chara is None:
            skipped.append(card_id)
            continue
        try:
            if args.dry_run:
                completed += 1
                continue
            card = rows_by_id.get(card_id, {"id": card_id})
            target = out_dir / f"ui_card_{sid(card_id)}.png"
            if layer_set.has_any():
                canvas = compose_card(
                    card,
                    chara,
                    layer_set,
                    Path(args.font).resolve() if args.font else None,
                    skip_text=args.skip_text,
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                canvas.save(target, format="PNG")
            else:
                save_prepared_image(chara, target)
            completed += 1
        except Exception as exc:
            print(f"[WARN] 合成失败 {card_id}: {exc}", file=sys.stderr)
            skipped.append(card_id)

    print(f"合成完成 {completed} 张 -> {out_dir}")
    if skipped:
        print(f"缺少角色图层，跳过: {skipped[:20]}")
    return 0


def update_info_presence(
    rows: list[dict[str, Any]],
    available_ids: set[int],
) -> list[dict[str, Any]]:
    for row in rows:
        try:
            card_id = int(row["id"])
        except (TypeError, ValueError):
            continue
        row["imagePresent"] = card_id in available_ids
        if card_id in available_ids:
            row.setdefault("imageFile", f"ui_card_{sid(card_id)}.png")
    return rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(dest: Path, info_path: Path) -> None:
    """按插件现有格式重建 card_data_manifest.json。"""

    rows = load_card_info(info_path)
    names: set[str] = set()
    for row in rows:
        if not bool(row.get("imagePresent", False)):
            continue
        try:
            card_id = int(row["id"])
        except (TypeError, ValueError):
            continue
        name = str(row.get("imageFile") or f"ui_card_{sid(card_id)}.png")
        if name:
            names.add(name)
    files = []
    for name in sorted(names):
        path = dest / name
        if not path.is_file():
            continue
        files.append(
            {
                "name": name,
                "size": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    pool_path = dest / DEFAULT_POOLS.name
    gacha_pool = (
        {
            "name": DEFAULT_POOLS.name,
            "size": pool_path.stat().st_size,
            "sha256": sha256(pool_path),
        }
        if pool_path.is_file()
        else {}
    )
    manifest = {
        "count": len(files),
        "json": {
            "name": "card_info_merged.json",
            "size": info_path.stat().st_size,
            "sha256": sha256(info_path),
        },
        "gacha_pool": gacha_pool,
        "files": files,
    }
    write_json(dest / "card_data_manifest.json", manifest)


def finalize_dest(
    dest: Path,
    staged_targets: dict[int, Path],
    info_path: Path,
    *,
    args: argparse.Namespace,
) -> int:
    """将本次导入的新品接入目标目录并重建校验清单。

    为了避免多次分批导入时丢失已有卡面，这里会合并目标目录中已经存在的卡面，
    再自行重建 manifest，而不要求 staging 一次包含全部卡面。
    """

    if args.dry_run:
        print(
            f"[DRY RUN] 将从临时目录导入 {len(staged_targets)} 张卡面到 {dest}，"
            "并重建 card_data_manifest.json"
        )
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    dest_info = dest / "card_info_merged.json"
    base_info_path = dest_info if dest_info.is_file() else info_path
    if args.update_json:
        rows = load_card_info(base_info_path)
        existing: set[int] = set()
        for row in rows:
            try:
                card_id = int(row["id"])
            except (TypeError, ValueError):
                continue
            if not bool(row.get("imagePresent", False)):
                continue
            image_name = str(row.get("imageFile") or f"ui_card_{sid(card_id)}.png")
            if (dest / image_name).is_file():
                existing.add(card_id)
        rows = update_info_presence(
            rows,
            existing | set(staged_targets),
        )
        write_json(dest_info, rows)
    elif not dest_info.is_file():
        shutil.copy2(info_path, dest_info)

    for card_id, target in staged_targets.items():
        image_name = f"ui_card_{sid(card_id)}.png"
        dest_file = dest / image_name
        if target.is_file() and target.resolve() != dest_file.resolve():
            shutil.copy2(target, dest_file)

    if not args.skip_pools and DEFAULT_POOLS.is_file():
        target_pool = dest / DEFAULT_POOLS.name
        if target_pool.is_file() and not args.overwrite:
            pass
        else:
            shutil.copy2(DEFAULT_POOLS, target_pool)

    write_manifest(dest, dest_info)
    print(f"接入完成: {dest}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    sources = [resolve_path(value) for value in args.source]
    layer_roots = [resolve_path(value) for value in args.layers]
    info_path = resolve_path(args.info)
    dest = resolve_path(args.dest)
    staging = resolve_path(args.staging)
    mapping = resolve_path(args.mapping) if args.mapping else None
    info_rows = load_card_info(info_path)
    rows_by_id = {int(row["id"]): row for row in info_rows}
    by_id = scan_source(sources, mapping)
    layer_set = LayerSet(layer_roots)
    if layer_roots and args.mode != "finished":
        print(
            "[INFO] 检测到通用图层；如需更接近上游字体/排版，"
            "可先运行 compose_card_art.py，再导入其输出目录。",
            file=sys.stderr,
        )
    selected = parse_ids(args.ids) if args.ids else list(rows_by_id)
    selected_set = set(selected)

    staged_ids: set[int] = set()
    staged_targets: dict[int, Path] = {}
    errors: list[str] = []
    for card_id in sorted(selected_set):
        if card_id not in rows_by_id:
            continue
        try:
            target, result = stage_card(
                card_id,
                rows_by_id[card_id],
                by_id,
                layer_set,
                staging,
                mode=args.mode,
                allow_raw=args.allow_raw,
                force_normalize=args.force_normalize,
                dry_run=args.dry_run,
            )
        except Exception as exc:
            errors.append(f"{card_id}: {type(exc).__name__}: {exc}")
            continue
        if result in {"missing", "missing_finished", "missing_layers"}:
            errors.append(f"{card_id}: {result}")
            continue
        if target is not None:
            staged_ids.add(card_id)
            staged_targets[card_id] = target

    if not staged_ids:
        print("没有可导入的素材；请先使用 scan 检查目录名称，"
              "或通过 mapping 指定文件与 ID。", file=sys.stderr)
        return 1

    if args.require_complete and staged_ids != selected_set:
        missing = sorted(selected_set - staged_ids)
        print(f"素材不完整，缺少 {len(missing)} 张，示例: {missing[:20]}", file=sys.stderr)
        return 1

    print(
        f"准备导入 {len(staged_ids)}/{len(selected_set)} 张卡面"
        + ("" if args.dry_run else f" -> {staging}")
    )
    if errors:
        print(f"[WARN] 跳过 {len(errors)} 张，示例: {errors[:20]}", file=sys.stderr)

    result = finalize_dest(
        dest,
        staged_targets,
        info_path,
        args=args,
    )
    return result


def cmd_verify(args: argparse.Namespace) -> int:
    dest = resolve_path(args.dest)
    info_path = resolve_path(args.info)
    if not info_path.is_file():
        print(f"FAIL: 卡牌信息文件不存在: {info_path}", file=sys.stderr)
        return 1
    rows = load_card_info(info_path)
    expected: dict[str, tuple[int, str]] = {}
    for row in rows:
        if not bool(row.get("imagePresent", False)):
            continue
        try:
            card_id = int(row["id"])
        except (TypeError, ValueError):
            continue
        name = str(row.get("imageFile") or f"ui_card_{sid(card_id)}.png")
        expected[name] = (card_id, name)

    actual: dict[str, Path] = {}
    extra: list[Path] = []
    for path in dest.glob("*"):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
            name = path.name
            if name in expected:
                actual[name] = path
            elif path.name.lower().startswith("ui_card"):
                extra.append(path)
            else:
                continue

    missing = sorted(set(expected) - set(actual))
    bad_dimensions: list[tuple[str, tuple[int, int]]] = []
    for name, path in actual.items():
        try:
            with Image.open(path) as image:
                if image.size != CARD_SIZE:
                    bad_dimensions.append((name, image.size))
        except Exception as exc:
            bad_dimensions.append((name, (0, 0)))

    print(f"目标目录: {dest}")
    print(f"JSON 期望卡面: {len(expected)} 张")
    print(f"磁盘实际卡面: {len(actual)} 张")
    print(f"缺失: {len(missing)} 张")
    if missing:
        print(f"缺失示例: {missing[:10]}")
    print(f"尺寸异常: {len(bad_dimensions)} 张")
    if bad_dimensions:
        print(f"尺寸异常示例: {bad_dimensions[:10]}")
    print(f"未登记文件: {len(extra)} 个")
    if extra:
        print(f"未登记示例: {[p.name for p in extra[:10]]}")

    if args.report:
        write_json(
            resolve_path(args.report),
            {
                "dest": str(dest),
                "expected": len(expected),
                "actual": len(actual),
                "missing": missing[:200],
                "missing_count": len(missing),
                "bad_dimensions": [
                    {"file": name, "size": size}
                    for name, size in bad_dimensions[:200]
                ],
                "bad_dimensions_count": len(bad_dimensions),
                "extra": [str(path) for path in extra[:200]],
                "extra_count": len(extra),
            },
        )
        print(f"已写入校验报告: {args.report}")

    if missing or bad_dimensions:
        return 1
    print("OK: 卡面文件与尺寸检查通过")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="音击抽卡插件卡面素材接入工具箱（仅处理本地已有素材）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="扫描本地素材目录，识别可接入的卡面")
    scan.add_argument("--source", action="append", required=True, help="素材目录；可重复")
    scan.add_argument("--info", "--source-json", default=str(DEFAULT_INFO), help="卡牌信息 JSON")
    scan.add_argument("--mapping", help="可选文件名映射 JSON/CSV")
    scan.add_argument("--report", help="扫描报告输出路径")
    scan.set_defaults(func=cmd_scan)

    compose = sub.add_parser("compose", help="从角色图层合成标准尺寸卡面")
    compose.add_argument("--source", action="append", required=True, help="角色图层目录；可重复")
    compose.add_argument("--layers", action="append", default=[], help="通用图层目录；可重复")
    compose.add_argument("--info", "--source-json", default=str(DEFAULT_INFO), help="卡牌信息 JSON")
    compose.add_argument("--out", default=str(DEFAULT_STAGING), help="合成输出目录")
    compose.add_argument("--font", help="可选 SEGA Humming 字体路径")
    compose.add_argument("--ids", action="append", default=[], help="卡 ID；支持逗号和区间")
    compose.add_argument("--skip-text", action="store_true", help="不绘制卡名文字")
    compose.add_argument("--allow-raw", action="store_true", help="无通用图层时直接导出角色图")
    compose.add_argument("--dry-run", action="store_true", help="只显示计划")
    compose.set_defaults(func=cmd_compose)

    importer = sub.add_parser("import", help="把成品/合成卡面接入插件数据目录")
    importer.add_argument("--source", action="append", required=True, help="成品或角色图层目录；可重复")
    importer.add_argument("--layers", action="append", default=[], help="通用图层目录；可重复")
    importer.add_argument("--info", "--source-json", default=str(DEFAULT_INFO), help="卡牌信息 JSON")
    importer.add_argument("--dest", default=str(DEFAULT_DATA_DIR), help="插件数据目标目录")
    importer.add_argument("--staging", default=str(DEFAULT_STAGING), help="临时成品目录")
    importer.add_argument("--mapping", help="可选文件名映射 JSON/CSV")
    importer.add_argument(
        "--mode",
        choices=("auto", "finished", "layers"),
        default="auto",
        help="finished=仅成品卡；layers=只从角色图层合成；auto=自动选择",
    )
    importer.add_argument("--ids", action="append", default=[], help="卡 ID；支持逗号和区间")
    importer.add_argument("--update-json", dest="update_json", action="store_true",
                          default=True, help="更新目标目录中的 imagePresent（默认）")
    importer.add_argument("--keep-json", dest="update_json", action="store_false",
                          help="不更新目标目录中的 imagePresent（仅适配完整导入）")
    importer.add_argument("--allow-raw", action="store_true",
                          help="没有通用图层时允许直接使用角色图层作为卡面")
    importer.add_argument("--force-normalize", action="store_true",
                          help="即使大小正确也重新编码为 PNG")
    importer.add_argument("--require-complete", action="store_true",
                          help="缺少任何指定素材时直接失败")
    importer.add_argument("--skip-pools", action="store_true",
                          help="不复制 gacha_pools.json 到目标目录")
    importer.add_argument("--overwrite", action="store_true",
                          help="覆盖目标目录中的已有 pools/JSON")
    importer.add_argument("--dry-run", action="store_true", help="只显示计划")
    importer.set_defaults(func=cmd_import)

    verify = sub.add_parser("verify", help="校验插件数据目录中的卡面")
    verify.add_argument("--dest", default=str(DEFAULT_DATA_DIR), help="插件数据目录")
    verify.add_argument("--info", "--source-json", default=str(DEFAULT_INFO), help="卡牌信息 JSON")
    verify.add_argument("--report", help="校验报告输出路径")
    verify.set_defaults(func=cmd_verify)

    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    try:
        return int(args.func(args))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
