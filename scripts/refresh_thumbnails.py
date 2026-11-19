"""Give the channel's already-published LONG videos the new, punchier thumbnail (truthful curiosity text).
Needs the YouTube credentials, so it only runs inside GitHub Actions (workflow 'Refresh thumbnails')."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from googleapiclient.http import MediaFileUpload  # noqa: E402

from pipeline.config import load_channel, log  # noqa: E402
from pipeline.llm import Gemini  # noqa: E402
from pipeline.render import extract_frame  # noqa: E402
from pipeline.state import State  # noqa: E402
from pipeline.thumbnail import make_thumbnail  # noqa: E402
from pipeline.uploader import build_service  # noqa: E402

CHANNEL = sys.argv[1] if len(sys.argv) > 1 else "technology"
WORK = Path("work_thumbs")


def thumb_text(llm, title, description):
    prompt = f"""Write the text for a YouTube thumbnail.

VIDEO TITLE: {title}
VIDEO DESCRIPTION: {description}

Rules: 2 to 4 words, ALL true to the title and description (use only what they say; do not add numbers, claims
or promises that are not in them). It must make people need to click: a curiosity gap, a myth-vs-truth contrast or
a bold question. Style examples: 'YOUR PHONE LIES', 'WHY IT NEVER DROPS', 'NOT WHAT YOU THINK'. No emojis.
Return JSON: {{"thumbnail_text": "..."}}"""
    return (llm.json(prompt, temperature=0.7, max_tokens=256).get("thumbnail_text") or "").strip()


def main():
    cfg = load_channel(CHANNEL)
    state = State.load(CHANNEL)
    done = set(state.data.setdefault("thumb_refreshed", []))
    ids = [u["video_id"] for u in state.data["uploads"] if u["kind"] == "long" and u["video_id"] not in done]
    if not ids:
        log("Nothing to refresh.")
        return 0
    service, llm = build_service(CHANNEL), Gemini()
    WORK.mkdir(exist_ok=True)
    items = service.videos().list(part="snippet,status", id=",".join(ids)).execute().get("items", [])
    for it in items:
        vid, sn = it["id"], it["snippet"]
        if it["status"]["privacyStatus"] != "public":
            log(f"⏭️ {vid}: not public yet, will refresh next time")
            continue
        text = thumb_text(llm, sn["title"], sn.get("description", "")[:600])
        if not text:
            log(f"⚠️ {vid}: no thumbnail text produced")
            continue
        src = WORK / f"{vid}.mp4"
        r = subprocess.run(["yt-dlp", "-f", "best[height<=720]/best", "-o", str(src),
                            f"https://www.youtube.com/watch?v={vid}"], capture_output=True, text=True)
        if r.returncode != 0 or not src.exists():
            log(f"⚠️ {vid}: could not download the video ({(r.stderr or r.stdout)[-200:].strip()})")
            continue
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                    "default=nw=1:nk=1", str(src)], capture_output=True, text=True).stdout or 0)
        frame = WORK / f"{vid}.png"
        extract_frame(src, frame, at=max(5.0, dur * 0.35))
        out = make_thumbnail(frame, text, cfg["name"], WORK / f"{vid}.jpg")
        service.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(out), mimetype="image/jpeg")).execute()
        log(f"🖼️ {vid}: '{sn['title']}' -> thumbnail '{text}'")
        done.add(vid)
        state.data["thumb_refreshed"] = sorted(done)
        state.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
