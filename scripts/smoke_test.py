"""Offline smoke test: renders a Short and a long video from synthetic inputs (no API keys needed).

    python scripts/smoke_test.py

Checks ffmpeg filters, captions, ducked music mix, thumbnail and script validation.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.captions import build_ass, word_timings          # noqa: E402
from pipeline.config import load_channel                         # noqa: E402
from pipeline.render import extract_frame, render_video, snap_to_frames          # noqa: E402
from pipeline.scriptwriter import validate                       # noqa: E402
from pipeline.thumbnail import make_thumbnail                    # noqa: E402
from pipeline.visuals import Visuals                             # noqa: E402
from pipeline.voice import PAD_SECONDS, probe_duration           # noqa: E402

TEXTS = [
    "Apple just changed how iPhones get repaired, and it affects your wallet.",
    "According to The Verge, the company will now allow used parts in official repairs, which is a big shift.",
    "Here's my read: cheaper repairs, but only if Indian service centres actually adopt the program.",
    "Pricing for India hasn't been announced yet.",
]


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *map(str, args)], check=True)


def make_case(kind, width, height, work):
    work.mkdir(parents=True, exist_ok=True)
    cfg = load_channel("technology")
    vis = Visuals(cfg, [])
    media, durations, wavs, timings, t = [], [], [], [], 0.0
    callouts = ["$5 BILLION", "", "NVIDIA CEO", "44,077 chips on the list"]
    shots, shot_secs = [], []
    for i, text in enumerate(TEXTS):
        dur = 2.5 + i * 0.4
        wav = work / f"voice_{i:03d}.wav"
        ff("-f", "lavfi", "-i", f"sine=frequency={180 + 40 * i}:duration={dur}", "-ar", 44100, "-ac", 1, wav)
        d = probe_duration(wav) + PAD_SECONDS
        wav2 = work / f"v2_{i:03d}.wav"
        ff("-i", wav, "-af", f"apad=pad_dur={PAD_SECONDS}", wav2)
        wavs.append(wav2)
        durations.append(probe_duration(wav2))
        timings.append({"start": t, "dur": durations[-1], "callout": callouts[i],
                        "words": word_timings(text, t, durations[-1] - PAD_SECONDS)})
        t += durations[-1]
        if i % 2 == 0:       # a test-pattern "stock video"
            p = work / f"media_{i:03d}.mp4"
            ff("-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate=30:duration=2", "-pix_fmt", "yuv420p", p)
            media.append(("video", p))
        else:                # generated gradient "photo"
            p = work / f"media_{i:03d}.png"
            vis._gradient(p, width, height, i)
            media.append(("photo", p))
    for m, d in zip(media, durations):            # two shots per scene, like the real pipeline
        shots += [m, m]
        shot_secs += [d / 2, d / 2]
    shot_secs = snap_to_frames(shot_secs)
    music = work / "music.wav"
    ff("-f", "lavfi", "-i", "sine=frequency=110:duration=5", "-ar", 44100, "-ac", 2, music)
    ass = work / "captions.ass"
    ass.write_text(build_ass(timings, kind, width, height, show_captions=(kind == "short")))
    out = work / f"{kind}.mp4"
    render_video(shots, shot_secs, wavs, ass, width, height, out, work, captions=True, music=music)
    assert out.exists() and out.stat().st_size > 20_000, "output missing or tiny"
    dur = probe_duration(out)
    assert abs(dur - sum(durations)) < 0.6, f"duration mismatch {dur} vs {sum(durations)}"
    print(f"  ✅ {kind}: {out.stat().st_size // 1024} KB, {dur:.1f}s")
    return work, out


def main():
    base = Path(tempfile.mkdtemp(prefix="tp_smoke_"))
    print("Rendering vertical Short…")
    make_case("short", 1080, 1920, base / "short")
    print("Rendering landscape long-form…")
    work, _ = make_case("long", 1920, 1080, base / "long")

    print("Thumbnail…")
    extract_frame(work / "scene_000.mp4", work / "frame.png")
    thumb = make_thumbnail(work / "frame.png", "Apple's repair U-turn", "Trend Pulse Daily", work / "thumb.jpg")
    assert Path(thumb).stat().st_size < 2_000_000
    print(f"  ✅ thumbnail {Path(thumb).stat().st_size // 1024} KB")

    print("Script validation gate…")
    cfg = load_channel("technology")
    bad = {"title": "x", "scenes": [{"narration": "Welcome back to the channel, this is a game-changer.",
                                      "shots": ["laptop"]}]}
    problems = validate(bad, "short", cfg)
    assert problems, "validator should reject filler"
    print(f"  ✅ rejected filler: {problems[:2]}")
    print(f"\nAll good. Outputs in {base}")


if __name__ == "__main__":
    main()
