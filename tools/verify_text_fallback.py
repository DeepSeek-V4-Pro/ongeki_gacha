"""离线验证“无素材”发布包：只带代码与索引时插件仍能运行。

做法是把插件目录复制成一份不含任何二进制素材的临时副本（JSON 与代码用硬链接，
不重复拷贝大文件），导入该副本，用 Mock 发送器执行两组命令：

1. **缺素材**：确认插件能加载、每个命令都有回复、卡面相关输出改为文字清单；
2. **缺字体**：强制关闭图片渲染，确认全部命令都退化为纯文字且不发送任何图片。

需要安装 maibot_sdk 的解释器（通常是 MaiBot 实例的 Python）。
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
from pathlib import Path
import re
import shutil
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

BINARY_SUFFIXES = {
    ".png", ".webp", ".jpg", ".jpeg", ".gif", ".bmp", ".otf", ".ttf", ".ttc",
    ".wav", ".mp3", ".ogg", ".m4a", ".flac", ".db", ".zip", ".7z", ".rar",
}
SKIP_DIRS = {"__pycache__", ".git", "data", "artifacts", "dist", "temp"}

# 覆盖各条主路径：文字回执、抽卡、卡池、卡册、养成、任务与规则。
COMMANDS = (
    "/帮助",
    "/点数",
    "/签到",
    "/概率",
    "/卡池 列表",
    "/抽卡 11",
    "/卡册",
    "/卡册 星咲 あかり 1",
    "/好感 列表",
    "/伙伴 星咲 あかり",
    "/陪伴",
    "/礼物",
    "/好感 星咲 あかり",
    "/规则",
    "/任务列表",
    "/角色语音 分类",
)
# 连字体都没有时，只验证最关键的四条路径。
PLAIN_COMMANDS = ("/帮助", "/规则", "/卡册", "/抽卡 1")
DRAW_TEXT_MARKER = "【抽卡结果】"

COPY_NAME = "ongeki_gacha_assetless"


def build_assetless_copy(source: Path, target: Path) -> int:
    """复制代码与 JSON 索引，跳过全部二进制素材；返回复制文件数。"""
    copied = 0
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.is_dir() or path.suffix.lower() in BINARY_SUFFIXES:
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.hardlink_to(path)
        except OSError:
            shutil.copy2(path, destination)
        copied += 1
    return copied


def _leftover_binaries(root: Path) -> list[str]:
    """副本里残留的二进制文件；正常情况下应为空。"""
    return [
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in BINARY_SUFFIXES
    ]


def _dispatch(plugin, text: str, message_id: str):
    """按插件注册的命令组件匹配并执行，和插件运行时一致。"""
    for component in plugin.get_components():
        if component.get("type") != "COMMAND":
            continue
        metadata = component["metadata"]
        match = re.fullmatch(metadata["command_pattern"], text)
        if match is None:
            continue
        handler = getattr(plugin, metadata["handler_name"])
        return handler(
            stream_id="mock-group",
            user_id="user",
            message_id=message_id,
            matched_groups=match.groupdict(),
        )
    raise RuntimeError(f"命令未注册: {text}")


async def _reply(plugin, command: str, message_id: str) -> dict:
    reply = await _dispatch(plugin, command, message_id)
    text = str(reply[1]) if reply else ""
    return {"ok": bool(reply and reply[0]), "chars": len(text), "text": text}


async def _exercise(module, data_dir: Path, runtime_dir: Path) -> dict:
    sender = SimpleNamespace(
        text=AsyncMock(return_value=True),
        image=AsyncMock(return_value={"success": True}),
        custom=AsyncMock(return_value=True),
    )
    plugin = module.OngekiGachaPlugin()
    plugin._set_context(
        SimpleNamespace(
            paths=SimpleNamespace(data_dir=data_dir, runtime_dir=runtime_dir),
            send=sender,
            logger=logging.getLogger("text-fallback"),
        )
    )
    config = plugin.build_default_config()
    config["growth"]["voice_enabled"] = False
    config["admin"]["admin_ids"] = ["user"]
    plugin.set_plugin_config(config)
    await plugin.on_load()
    try:
        assetless: dict[str, dict] = {}
        for index, command in enumerate(COMMANDS, start=1):
            assetless[command] = await _reply(plugin, command, f"assetless-{index}")
        draw_text = assetless["/抽卡 11"]["text"]
        render_ready_assetless = bool(plugin._render_ready)

        sender.image.reset_mock()
        sender.text.reset_mock()
        plugin._render_ready = False
        plain: dict[str, dict] = {}
        for index, command in enumerate(PLAIN_COMMANDS, start=1):
            plain[command] = await _reply(plugin, command, f"plain-{index}")
        plain_images = int(sender.image.await_count)
        return {
            "card_images_ready": bool(plugin._card_images_ready),
            "render_ready": render_ready_assetless,
            "assetless": assetless,
            "draw_text_fallback": DRAW_TEXT_MARKER in draw_text,
            "plain": plain,
            "plain_images_sent": plain_images,
        }
    finally:
        await plugin.on_unload()


def verify(plugin_root: Path | None = None) -> dict:
    """在临时目录里跑一遍“无素材”流程，返回结构化结果。"""
    source = Path(plugin_root) if plugin_root else Path(__file__).parents[1]
    source = source.resolve()
    workspace = Path(tempfile.mkdtemp(prefix="ongeki-text-fallback-"))
    package_root = workspace / COPY_NAME
    package_root.mkdir(parents=True, exist_ok=True)
    copied = build_assetless_copy(source, package_root)
    leftovers = _leftover_binaries(package_root)
    data_dir = workspace / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir = workspace / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(workspace))
    module = None
    try:
        module = importlib.import_module(f"{COPY_NAME}.plugin")
        outcome = asyncio.run(_exercise(module, data_dir, runtime_dir))
        failed = sorted(
            {f"缺素材 {command}" for command, row in outcome["assetless"].items()
             if not row["ok"] or not row["chars"]}
            | {f"缺字体 {command}" for command, row in outcome["plain"].items()
               if not row["ok"] or not row["chars"]}
        )
        ok = (
            not leftovers
            and not failed
            and outcome["draw_text_fallback"]
            and outcome["plain_images_sent"] == 0
        )
        return {
            "ok": ok,
            "copied_files": copied,
            "leftover_binaries": leftovers,
            "excluded_suffixes": sorted(BINARY_SUFFIXES),
            "assetless_commands": len(outcome["assetless"]),
            "plain_commands": len(outcome["plain"]),
            "failed_commands": failed,
            "draw_text_fallback": outcome["draw_text_fallback"],
            "plain_images_sent": outcome["plain_images_sent"],
            "card_images_ready": outcome["card_images_ready"],
            "database_modified": False,
            "messages_sent": False,
        }
    finally:
        if module is not None:
            sys.modules.pop(COPY_NAME, None)
        for name in [name for name in sys.modules if name.startswith(COPY_NAME + ".")]:
            sys.modules.pop(name, None)
        if str(workspace) in sys.path:
            sys.path.remove(str(workspace))
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    try:
        summary = verify()
    except ModuleNotFoundError as exc:
        if exc.name != "maibot_sdk":
            raise
        print(json.dumps({
            "ok": False,
            "error": "需要安装 maibot_sdk 的解释器才能验证插件加载",
        }, ensure_ascii=False, indent=2))
        raise SystemExit(1) from exc
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(0 if summary["ok"] else 1)
