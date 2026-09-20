"""语音自动校验与验收：解码、时长、响度、散列全部通过后写入验收状态。

自动验收不替代人工听感判断；索引会记录 review_method 供复查。
"""
from __future__ import annotations

import argparse
from array import array
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import wave

MIN_DURATION = 0.2
MIN_RMS_16BIT = 8.0


def inspect_wav(path: Path) -> dict:
    """返回时长/采样参数/响度；参数异常或静音时抛 ValueError。"""
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.getnframes()
        raw = handle.readframes(frames)
    if channels < 1 or width not in (1, 2) or rate < 8000 or frames <= 0:
        raise ValueError(f"音频参数异常: channels={channels} width={width} rate={rate} frames={frames}")
    duration = frames / rate
    if duration < MIN_DURATION:
        raise ValueError(f"时长过短: {duration:.3f}s")
    if width == 2:
        samples = array("h", raw[: len(raw) // 2 * 2])
        rms = (sum(value * value for value in samples) / len(samples)) ** 0.5
    else:
        samples = array("B", raw)
        rms = (sum((value - 128) ** 2 for value in samples) / len(samples)) ** 0.5
        rms *= 256  # 归一到 16bit 量级
    if not samples:
        raise ValueError("无采样数据")
    if rms < MIN_RMS_16BIT:
        raise ValueError(f"疑似静音: rms={rms:.2f}")
    return {"duration": duration, "channels": channels, "sample_width": width, "rate": rate, "rms": round(rms, 2)}


def review_catalog(root: Path, catalog_path: Path, *, approve: bool) -> dict:
    """校验整份语音索引；approve=True 且全部通过时写回验收状态。"""
    root = root.resolve()
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    rows = data["voices"]
    failures = []
    durations = []
    for index, row in enumerate(rows):
        label = row.get("path") or f"row-{index}"
        try:
            path = (root / row["path"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("路径越界")
            if not path.is_file():
                raise ValueError("文件缺失")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != row.get("sha256"):
                raise ValueError("散列不符")
            info = inspect_wav(path)
            durations.append(info["duration"])
        except (ValueError, OSError) as exc:  # noqa: PERF203
            failures.append({"path": label, "error": str(exc)})

    if approve and not failures:
        verified_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for row in rows:
            row["listening_review"] = "verified"
            row["review_method"] = "automated"
            row["verified_at"] = verified_at
        catalog_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "catalog": catalog_path.name,
        "total": len(rows),
        "failed": len(failures),
        "approved": bool(approve and not failures),
        "min_duration": round(min(durations), 3) if durations else None,
        "max_duration": round(max(durations), 3) if durations else None,
        "failures": failures[:20],
    }


def review_all(root: Path, *, approve: bool) -> list[dict]:
    reports = []
    for name in ("voice_catalog.json", "event_voice_catalog.json"):
        path = root / "assets/growth" / name
        if path.is_file():
            reports.append(review_catalog(root / "assets/growth", path, approve=approve))
    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--approve", action="store_true", help="全部通过后写入自动验收状态")
    parser.add_argument("--report", type=Path, help="可选：输出 JSON 报告")
    args = parser.parse_args()
    result = review_all(args.root, approve=args.approve)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    if any(report["failed"] for report in result):
        raise SystemExit(1)
