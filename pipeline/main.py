"""Orchestrator. Each run makes AT MOST ONE video, only if there is a strong story worth making.

    python -m pipeline.main --channel technology [--kind auto|short|long] [--dry-run]
"""
import argparse
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import news, research, scriptwriter
from .captions import build_ass, word_timings
from .config import ROOT, load_channel, log
from .llm import Gemini
from .notify import telegram
from .render import extract_frame, measure_loudness, pick_music, render_video
from .state import State
from .thumbnail import make_thumbnail
from .visuals import Visuals
from .voice import PAD_SECONDS, Narrator


def candidate_kinds(cfg, counts, score, forced):
    """Which video kinds are worth making for a story with this score, in order of preference."""
    sch = cfg["schedule"]
    if forced:
        return [forced]
    kinds = []
    if counts["long"] < sch["long_per_day"] and score >= sch["long_min_score"]:
        kinds.append("long")
    if counts["short"] < sch["shorts_per_day"] and score >= sch["short_min_score"]:
        kinds.append("short")
    if (sch["long_per_day"] <= counts["long"] < sch["max_long_per_day"]
            and score >= sch["extra_long_min_score"]):
        kinds.append("long")
    return kinds


def produce(cfg, kind, story, dossier, script, state, workdir, out_dir):
    """Voice -> visuals -> captions -> render. Returns (video_path, thumbnail_or_None)."""
    fmt = cfg["formats"][kind]
    width, height, portrait = fmt["width"], fmt["height"], kind == "short"
    scenes = script["scenes"]

    t0 = time.time()
    voiced = Narrator(cfg).synthesize(scenes, workdir)
    wavs, durations = [w for w, _ in voiced], [d for _, d in voiced]

    vis = Visuals(cfg, state.data["pexels_used"])
    media = [vis.fetch(s["visual_query"], portrait, workdir, i, width, height) for i, s in enumerate(scenes)]
    state.data["pexels_used"].extend(vis.new_ids)
    log(f"🖼️  Visuals: {vis.stats['video']} stock videos, {vis.stats['photo']} photos, "
        f"{vis.stats['gradient']} gradient fallbacks (by provider: {vis.by_provider or 'none'}) "
        f"[{time.time() - t0:.0f}s so far]")
    if vis.stats["gradient"] > len(scenes) * 0.3:
        log("⚠️  Over 30% of scenes fell back to plain gradients — check the footage API key / queries.")

    timings, t = [], 0.0
    for s, d in zip(scenes, durations):
        timings.append({"start": t, "words": word_timings(s["narration"], t, max(d - PAD_SECONDS, 0.3))})
        t += d
    ass = workdir / "captions.ass"
    ass.write_text(build_ass(timings, kind, width, height), encoding="utf-8")

    out_dir.mkdir(parents=True, exist_ok=True)
    video = out_dir / f"{cfg['id']}_{kind}_{int(time.time())}.mp4"
    render_video(media, durations, wavs, ass, width, height, video, workdir,
                 captions=fmt.get("captions", True), music=pick_music())

    lufs, peak = measure_loudness(video)
    log(f"🔊 Audio loudness: {lufs} LUFS, peak {peak} dBFS (target about -16 LUFS) | "
        f"file {video.stat().st_size / 1e6:.0f} MB | total {time.time() - t0:.0f}s")
    thumb = None
    if kind == "long":
        extract_frame(workdir / "scene_000.mp4", workdir / "frame.png")
        thumb = make_thumbnail(workdir / "frame.png", script.get("thumbnail_text") or script["title"],
                               cfg["name"], out_dir / f"{video.stem}.jpg")
    return video, thumb


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="technology")
    ap.add_argument("--kind", default="auto", choices=["auto", "short", "long"])
    ap.add_argument("--dry-run", action="store_true", help="render but do not upload")
    args = ap.parse_args(argv)

    cfg = load_channel(args.channel)
    tz = ZoneInfo(cfg["timezone"])
    state = State.load()
    forced = None if args.kind == "auto" else args.kind
    sch = cfg["schedule"]
    log(f"⚡ {cfg['name']} — run at {datetime.now(tz):%Y-%m-%d %H:%M %Z}")

    counts = state.counts_today(cfg["id"], tz)
    log(f"📊 Today so far: {counts['short']} shorts, {counts['long']} long")
    manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    if manual:
        log("🖐️ Manual run — skipping the active-hours and minimum-gap checks (daily caps still apply).")
    if not forced and not manual and not args.dry_run:
        hour = datetime.now(tz).hour
        if not (sch["active_hours"][0] <= hour < sch["active_hours"][1]):
            log("🌙 Outside active hours — nothing to do.")
            return 0
        if counts["short"] >= sch["shorts_per_day"] and counts["long"] >= sch["max_long_per_day"]:
            log("✅ Daily targets already met.")
            return 0
        gap = state.minutes_since_last_upload(cfg["id"])
        if gap is not None and gap < sch["min_gap_minutes"]:
            log(f"⏳ Last upload was {gap:.0f} min ago (min gap {sch['min_gap_minutes']}) — skipping.")
            return 0

    llm = Gemini()
    stories = news.fetch_candidates(cfg)
    stories = [s for s in stories if len(state.story_kinds(s.id, s.title)) < 2]
    if not stories:
        log("😴 No fresh unused stories.")
        return 0

    ranked = scriptwriter.rank_stories(llm, cfg, stories)
    for s, score, angle in ranked:
        log(f"   {score:>4.1f}  {s.title[:90]}  [{s.source}, +{s.coverage}]")

    for story, score, angle in ranked[:4]:
        done = state.story_kinds(story.id, story.title)
        for kind in candidate_kinds(cfg, counts, score, forced):
            if kind in done:
                continue
            dossier = research.build_dossier(story, kind, cfg)
            need = cfg["research"]["min_chars_long" if kind == "long" else "min_chars_short"]
            if dossier["chars"] < need:
                log(f"⏭️  '{story.title[:60]}': only {dossier['chars']} chars of source text, "
                    f"need {need} for a {kind}")
                continue

            log(f"🎯 Making a {kind.upper()} about: {story.title}")
            script = scriptwriter.verified_script(llm, cfg, kind, story, dossier, angle,
                                                  state.titles_for_story(story.title))
            if not script:
                log("🚫 Could not produce a script that passes quality + fact checks.")
                continue

            workdir = Path(ROOT / "work" / f"{kind}_{int(time.time())}")
            workdir.mkdir(parents=True, exist_ok=True)
            try:
                video, thumb = produce(cfg, kind, story, dossier, script, state, workdir, ROOT / "out")
            finally:
                shutil.rmtree(workdir, ignore_errors=True)

            if args.dry_run:
                log(f"🧪 Dry run — video saved at {video}\n   Title: {script['title']}")
                return 0

            from .uploader import build_service, upload_video
            vid, privacy, publish_at = upload_video(build_service(cfg["id"]), cfg, kind, video, script,
                                                    dossier["sources"], thumb)
            state.record_upload(cfg["id"], kind, vid, script["title"], privacy)
            state.record_story(story.id, story.title, kind, script["title"])
            state.save()
            studio = f"https://studio.youtube.com/video/{vid}/edit"
            label = {"private": "PRIVATE — needs your approval", "public": "LIVE"}.get(privacy, privacy.upper())
            if publish_at:
                label = f"PRIVATE — auto-publishes {publish_at} unless you delete/edit it"
            watch = f"https://www.youtube.com/shorts/{vid}" if kind == "short" else f"https://youtu.be/{vid}"
            log(f"🎉 Uploaded ({label})")
            log(f"🔗 Watch:  {watch}")
            log(f"🛠️  Studio: {studio}")
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                with open(summary, "a", encoding="utf-8") as fh:
                    fh.write(f"### 🎬 {script['title']}\n\n- **Status:** {label}\n- **Watch:** {watch}\n"
                             f"- **Studio:** {studio}\n- **Type:** {kind}\n")
            telegram(f"🎬 New {kind}: {script['title']}\n{label}\nWatch: {watch}\nStudio: {studio}")
            return 0

    log("🛑 No story passed the quality bar this run. Nothing published (by design).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
