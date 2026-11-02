"""一键产出：校验 → Word → PPT → 帧 → 旁白稿 → 配音视频。

用法: pr.sh build_all <content.json> [--skip-video] [--engine auto|edge|piper] [--voice V] [--rate +5%]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import load_content, log, plain, slug_of
import build_docx
import build_pptx
import check
import render_frames


def write_script(content_path: Path, content: dict, timeline=None) -> Path:
    """逐页旁白稿（Markdown），附预计/实际时长。"""
    meta = content.get("meta", {})
    slides = content["slides"]
    tl = {t["slide"]: t for t in (timeline or [])}
    total_chars = sum(len(plain(s.get("narration", ""))) for s in slides)
    lines = [f"# 旁白稿：{meta.get('title', '')}", ""]
    if tl:
        total = sum(t["duration"] for t in tl.values())
        lines.append(f"共 {len(slides)} 页，旁白 {total_chars} 字，视频时长 {total / 60:.1f} 分钟。")
    else:
        cpm = check.chars_per_min(content.get("tts", {}).get("rate"))
        lines.append(f"共 {len(slides)} 页，旁白 {total_chars} 字，按每分钟约 {cpm:.0f} 字估计 "
                     f"{total_chars / cpm:.1f} 分钟。")
    lines.append("")
    for i, s in enumerate(slides, 1):
        title = plain(s.get("title") or s.get("type", ""))
        stamp = ""
        if i in tl:
            st = tl[i]["start"]
            stamp = f"（{int(st // 60):02d}:{int(st % 60):02d}，{tl[i]['duration']:.0f}s）"
        lines.append(f"## 第 {i} 页 · {title}{stamp}")
        lines.append("")
        lines.append(plain(s.get("narration", "")).strip() or "（无旁白）")
        lines.append("")
    out = Path(content_path).resolve().parent / "output" / f"{slug_of(content)}_narration.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    log(f"旁白稿已生成：{out}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("content")
    ap.add_argument("--skip-video", action="store_true")
    ap.add_argument("--engine", default="auto", choices=["auto", "edge", "piper"])
    ap.add_argument("--voice")
    ap.add_argument("--rate")
    a = ap.parse_args()
    cp = Path(a.content).resolve()

    if not check.run(cp):
        raise SystemExit(1)
    content = load_content(cp)
    results = {"docx": str(build_docx.build(cp)), "pptx": str(build_pptx.build(cp))}
    render_frames.render_all(cp, sheet=True)
    results["contact_sheet"] = str(cp.parent / "build" / "contact_sheet.png")
    timeline = None
    if not a.skip_video:
        import tts_video
        mp4, srt, timeline = tts_video.build_video(cp, a.engine, a.voice, a.rate)
        results.update(video=str(mp4), srt=str(srt))
    results["narration"] = str(write_script(cp, content, timeline))
    print(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
