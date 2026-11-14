"""Persistent state: what we already covered and uploaded. Committed back to the repo."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ROOT

_STOP = set("the a an and or of to in on for with from by at as is are was were be been this that these "
            "those it its into over after before about new will can could may might says say said how why "
            "what who when where which has have had not more less than just your you our their they them "
            "his her he she we vs via up out off all any one two three first latest report reports".split())


def title_tokens(title):
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) >= 3 and w not in _STOP}


def same_story(a, b):
    inter = len(a & b)
    return inter >= 3 or (inter / (len(a | b) or 1)) >= 0.4


def _now():
    return datetime.now(timezone.utc)


class State:
    def __init__(self, data, path=None):
        self.path = Path(path) if path else ROOT / "data" / "state.json"
        self.data = data
        self.data.setdefault("stories", [])
        self.data.setdefault("uploads", [])
        self.data.setdefault("pexels_used", [])

    @classmethod
    def load(cls, channel_id="technology"):
        """Each channel keeps its own file (data/state_<channel>.json) so channels never block each other."""
        path = ROOT / "data" / f"state_{channel_id}.json"
        try:
            return cls(json.loads(path.read_text(encoding="utf-8")) or {}, path)
        except (FileNotFoundError, json.JSONDecodeError):
            return cls({}, path)

    def save(self):
        self.data["stories"] = self.data["stories"][-500:]
        self.data["uploads"] = self.data["uploads"][-1000:]
        self.data["pexels_used"] = self.data["pexels_used"][-400:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")

    # -- stories -----------------------------------------------------------
    def story_kinds(self, story_id, title):
        """Which video kinds ('short'/'long') already exist for this story (any outlet)."""
        cutoff = _now() - timedelta(days=7)
        tokens = title_tokens(title)
        kinds = set()
        for s in self.data["stories"]:
            if datetime.fromisoformat(s["ts"]) < cutoff:
                continue
            if s["id"] == story_id or same_story(tokens, set(s.get("tokens", []))):
                kinds.update(s.get("kinds", []))
        return kinds

    def titles_for_story(self, title):
        tokens = title_tokens(title)
        return [s["video_title"] for s in self.data["stories"]
                if s.get("video_title") and same_story(tokens, set(s.get("tokens", [])))]

    def record_story(self, story_id, title, kind, video_title, summary=""):
        self.data["stories"].append({
            "id": story_id, "tokens": sorted(title_tokens(title)), "kinds": [kind],
            "video_title": video_title, "summary": summary[:400], "ts": _now().isoformat()})

    def used_story_ids(self):
        """Articles we have already made a video from (never reuse the same article)."""
        return {s["id"] for s in self.data["stories"]}

    def recent_videos(self, days=10):
        """Videos published recently: [{'title', 'summary', 'tokens', 'kind', 'ts'}], newest first."""
        cutoff = _now() - timedelta(days=days)
        out = []
        for s in self.data["stories"]:
            if s.get("video_title") and datetime.fromisoformat(s["ts"]) >= cutoff:
                out.append({"title": s["video_title"], "summary": s.get("summary", ""),
                            "tokens": set(s.get("tokens", [])) | title_tokens(s["video_title"]),
                            "kind": (s.get("kinds") or ["short"])[0], "ts": s["ts"]})
        return sorted(out, key=lambda v: v["ts"], reverse=True)

    # -- uploads -----------------------------------------------------------
    def record_upload(self, channel, kind, video_id, title, privacy):
        self.data["uploads"].append({"channel": channel, "kind": kind, "video_id": video_id,
                                     "title": title, "privacy": privacy, "ts": _now().isoformat()})

    def counts_today(self, channel, tz):
        today = _now().astimezone(tz).date()
        counts = {"short": 0, "long": 0}
        for u in self.data["uploads"]:
            if u["channel"] == channel and datetime.fromisoformat(u["ts"]).astimezone(tz).date() == today:
                counts[u["kind"]] = counts.get(u["kind"], 0) + 1
        return counts

    def minutes_since_last_upload(self, channel):
        times = [datetime.fromisoformat(u["ts"]) for u in self.data["uploads"] if u["channel"] == channel]
        return (_now() - max(times)).total_seconds() / 60 if times else None


def closest_recent(title, recent):
    """Most similar recent video to a headline: (video_or_None, jaccard_0_to_1)."""
    tokens, best, best_j = title_tokens(title), None, 0.0
    for v in recent:
        j = len(tokens & v["tokens"]) / (len(tokens | v["tokens"]) or 1)
        if j > best_j:
            best, best_j = v, j
    return best, best_j
