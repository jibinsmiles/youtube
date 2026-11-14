"""Assemble the final video with ffmpeg: motion visuals + voice + ducked music + burned captions."""
import random
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import ROOT, log

FPS = 30


def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(map(str, cmd[:6]))} ...\n{r.stderr[-1500:]}")


def snap_to_frames(durations):
    """Round shot lengths to whole frames using cumulative rounding, so cuts land on exact frames and the
    video length never drifts away from the audio no matter how many shots there are."""
    out, cum, prev = [], 0.0, 0
    for d in durations:
        cum += d
        end = int(round(cum * FPS))
        out.append(max(end - prev, 1) / FPS)
        prev = max(end, prev + 1)
    return out


def build_scene_clip(kind_media, media_path, duration, width, height, out_path, idx):
    base = ["ffmpeg", "-y", "-loglevel", "error"]
    enc = ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p", "-r", str(FPS)]
    frames = max(int(round(duration * FPS)), 2)
    if kind_media == "video":
        vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
              f"setsar=1,fps={FPS},eq=contrast=1.04:saturation=1.08,format=yuv420p")
        run(base + ["-stream_loop", "-1", "-i", str(media_path), "-vf", vf, "-frames:v", str(frames)] + enc
            + [str(out_path)])
    else:
        up_w, up_h = int(width * 1.5) // 2 * 2, int(height * 1.5) // 2 * 2
        zoom = (f"1+0.18*on/{frames}" if idx % 2 == 0 else f"1.18-0.18*on/{frames}")   # alternate in / out
        vf = (f"scale={up_w}:{up_h}:force_original_aspect_ratio=increase,crop={up_w}:{up_h},"
              f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={width}x{height}:fps={FPS},"
              f"format=yuv420p")
        run(base + ["-i", str(media_path), "-vf", vf, "-frames:v", str(frames)] + enc + [str(out_path)])


def pick_music():
    tracks = [p for ext in ("*.mp3", "*.wav", "*.m4a", "*.ogg") for p in (ROOT / "assets" / "music").glob(ext)]
    return random.choice(tracks) if tracks else None


def render_video(media, durations, voice_wavs, ass_path, width, height, out_path, workdir,
                 captions=True, music=None):
    """media: [(kind, path)], durations: [seconds], voice_wavs: [Path]. Returns out_path."""
    workdir = Path(workdir)

    # 1) one motion clip per scene (parallel)
    clips = [workdir / f"scene_{i:03d}.mp4" for i in range(len(media))]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda a: build_scene_clip(media[a][0], media[a][1], durations[a], width, height,
                                                 clips[a], a), range(len(media))))
    (workdir / "scenes.txt").write_text("".join(f"file '{c.name}'\n" for c in clips))
    (workdir / "voice.txt").write_text("".join(f"file '{Path(w).name}'\n" for w in voice_wavs))
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "voice.txt",
         "-c", "copy", "voice.wav"], cwd=workdir)

    # 2) final pass: captions + audio mix + loudness
    total = sum(durations)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "scenes.txt",
           "-i", "voice.wav"]
    vchain = f"[0:v]ass={Path(ass_path).name}[v]" if captions else "[0:v]null[v]"
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        achain = ("[1:a]aresample=44100,asplit=2[vo][sc];"
                  "[2:a]aresample=44100,volume=0.14,afade=t=in:d=1.5[m0];"
                  "[m0][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=300[m];"
                  "[vo][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix];"
                  "[mix]loudnorm=I=-16:TP=-1.5:LRA=11[a]")
    else:
        achain = "[1:a]aresample=44100,loudnorm=I=-16:TP=-1.5:LRA=11[a]"
    cmd += ["-filter_complex", f"{vchain};{achain}", "-map", "[v]", "-map", "[a]",
            "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-ac", "2", "-b:a", "192k",
            "-movflags", "+faststart", str(Path(out_path).resolve())]
    run(cmd, cwd=workdir)
    log(f"🎬 Rendered {Path(out_path).name} ({total:.0f}s, {width}x{height})")
    return out_path


def extract_frame(clip_path, out_png, at=0.8):
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(at), "-i", str(clip_path),
         "-frames:v", "1", str(out_png)])


def measure_loudness(path):
    """Integrated loudness (LUFS) and true peak (dBTP) of the final file, for the run log."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
                        "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    import re
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    peak = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", r.stderr)
    return (float(lufs[-1]) if lufs else None, float(peak[-1]) if peak else None)


def find_silences(path, min_dur=1.0, threshold="-45dB"):
    """[(start, duration)] of silent stretches in the final audio, so a dropout shows up in the run log."""
    import re
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
                        f"silencedetect=n={threshold}:d={min_dur}", "-f", "null", "-"], capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
    durs = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", r.stderr)]
    return list(zip(starts, durs))
