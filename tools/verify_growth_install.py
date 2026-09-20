"""离线检查插件养成资源；不打开用户数据库，不发送消息。"""
import hashlib
import json
from pathlib import Path
from ..gacha_core import load_cards
from ..growth_catalog import GrowthCatalog
from ..voice_service import VoiceCatalog


def _verify_audio(assets: Path, rows) -> int:
    checked = 0
    for row in rows:
        path = (assets / row["path"]).resolve()
        if not path.is_relative_to(assets.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError(f"语音不完整: {row['path']}")
        checked += 1
    return checked


def verify(root):
    assets = root / "assets/growth"
    catalog = GrowthCatalog(assets, load_cards(root / "assets/card_data/card_info_merged.json"))
    voices = VoiceCatalog(assets)
    voices.validate_rewards(catalog)
    checked = 0
    for row in json.loads((assets / "visual_asset_manifest.json").read_text(encoding="utf8"))["assets"]:
        path = (assets / row["path"]).resolve()
        if not path.is_relative_to(assets.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError(f"界面素材不完整: {row['key']}")
        checked += 1
    profile_audio = _verify_audio(assets, voices.voices.values())
    event_audio = _verify_audio(assets, voices.events.values())
    for font in ("NotoSansCJKsc-Regular.otf", "NotoSansCJKsc-Bold.otf"):
        if not (root / "assets/fonts" / font).is_file():
            raise ValueError(f"字体缺失: {font}")
    pending = sum(r.get("listening_review") != "verified" for r in voices.voices.values()) + sum(
        r.get("listening_review") != "verified" for r in voices.events.values()
    )
    return {
        "characters": len(catalog.characters),
        "cards": len(catalog.cards.cards),
        "visual_assets_verified": checked,
        "profile_audio_verified": profile_audio,
        "event_audio_verified": event_audio,
        "listening_pending": pending,
        "profile_voice_entries": len(voices.voices),
        "database_modified": False,
        "messages_sent": False,
    }


if __name__ == "__main__":
    print(json.dumps(verify(Path(__file__).parents[1]), ensure_ascii=False, indent=2))
