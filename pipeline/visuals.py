"""Stock footage from Pixabay and/or Pexels, with photo and generated-gradient fallbacks.

Set PIXABAY_API_KEY and/or PEXELS_API_KEY. Providers are tried in the order given by
`visuals.providers` in the channel YAML, skipping any whose key is not set.
"""
import random
from pathlib import Path

import requests
from PIL import Image, ImageDraw

from .config import env, log

PEXELS = "https://api.pexels.com"
PIXABAY = "https://pixabay.com/api"


class Visuals:
    def __init__(self, cfg, used_ids):
        self.cfg = cfg
        self.pexels_key = env("PEXELS_API_KEY")
        self.pixabay_key = env("PIXABAY_API_KEY")
        self.used = set(used_ids)
        self.new_ids = []
        order = cfg["visuals"].get("providers", ["pixabay", "pexels"])
        self.providers = [p for p in order if (p == "pexels" and self.pexels_key)
                          or (p == "pixabay" and self.pixabay_key)]
        if not self.providers:
            log("⚠️ No PIXABAY_API_KEY / PEXELS_API_KEY set — using generated backgrounds only "
                "(free Pixabay key: log in at pixabay.com/api/docs)")

    # -- shared helpers ----------------------------------------------------
    def _mark(self, uid):
        self.used.add(uid)
        self.new_ids.append(uid)

    @staticmethod
    def _download(url, path):
        with requests.get(url, stream=True, timeout=90) as r:
            r.raise_for_status()
            size = 0
            with open(path, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    size += len(chunk)
                    if size > 120 * (1 << 20):
                        raise RuntimeError("download too large")
                    fh.write(chunk)

    @staticmethod
    def _shuffle_top(items, n=4):
        head = items[:n]
        random.shuffle(head)
        return head + items[n:]

    # -- Pexels ------------------------------------------------------------
    def _pexels_get(self, path, params):
        r = requests.get(f"{PEXELS}{path}", headers={"Authorization": self.pexels_key},
                         params=params, timeout=25)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _pexels_file(video, portrait):
        files = [f for f in video.get("video_files", [])
                 if f.get("file_type") == "video/mp4" and f.get("link") and f.get("width") and f.get("height")]
        if portrait:
            ok = [f for f in files if f["height"] > f["width"] and f["height"] >= 1280]
            return min(ok, key=lambda f: abs(f["height"] - 1920)) if ok else None
        ok = [f for f in files if f["width"] >= f["height"] and f["width"] >= 1280]
        return min(ok, key=lambda f: abs(f["width"] - 1920)) if ok else None

    def _pexels_video(self, query, portrait, dest):
        data = self._pexels_get("/videos/search", {"query": query, "per_page": 12, "size": "medium",
                                                   "orientation": "portrait" if portrait else "landscape"})
        options = [v for v in data.get("videos", [])
                   if f"v{v['id']}" not in self.used and v.get("duration", 0) >= 4]
        for video in self._shuffle_top(options)[:6]:
            f = self._pexels_file(video, portrait)
            if f:
                self._download(f["link"], dest)
                self._mark(f"v{video['id']}")
                return True
        return False

    def _pexels_photo(self, query, portrait, dest):
        data = self._pexels_get("/v1/search", {"query": query, "per_page": 10,
                                               "orientation": "portrait" if portrait else "landscape"})
        options = [p for p in data.get("photos", []) if f"p{p['id']}" not in self.used]
        if options:
            photo = random.choice(options[:4])
            self._download(photo["src"].get("large2x") or photo["src"]["original"], dest)
            self._mark(f"p{photo['id']}")
            return True
        return False

    # -- Pixabay -----------------------------------------------------------
    def _pixabay_video(self, query, portrait, dest):
        r = requests.get(f"{PIXABAY}/videos/", params={
            "key": self.pixabay_key, "q": query, "per_page": 20, "safesearch": "true"}, timeout=25)
        r.raise_for_status()
        candidates = []   # (orientation_penalty, id, url)
        for hit in r.json().get("hits", []):
            if f"x{hit['id']}" in self.used or hit.get("duration", 0) < 4:
                continue
            for size in ("large", "medium"):          # prefer HD, skip 4K-sized files
                v = (hit.get("videos") or {}).get(size) or {}
                w, h = v.get("width") or 0, v.get("height") or 0
                if v.get("url") and max(w, h) >= 1280 and max(w, h) <= 2600:
                    fits = (h > w) if portrait else (w >= h)
                    candidates.append((0 if fits else 1, hit["id"], v["url"]))
                    break
        if not candidates:
            return False
        # Shorts: prefer vertical clips, but a landscape clip gets centre-cropped by the renderer.
        # Long videos: only landscape clips (a cropped vertical clip would look terrible).
        if not portrait:
            candidates = [c for c in candidates if c[0] == 0]
        candidates.sort(key=lambda c: c[0])
        # shuffle within each orientation group so vertical clips stay ahead of cropped landscape ones
        ordered = []
        for penalty in sorted({c[0] for c in candidates}):
            group = [c for c in candidates if c[0] == penalty]
            random.shuffle(group)
            ordered += group
        for _, vid, url in ordered[:5]:
            self._download(url, dest)
            self._mark(f"x{vid}")
            return True
        return False

    def _pixabay_photo(self, query, portrait, dest):
        params = {"key": self.pixabay_key, "q": query, "image_type": "photo", "per_page": 20,
                  "safesearch": "true", "orientation": "vertical" if portrait else "horizontal"}
        params["min_height" if portrait else "min_width"] = 1000
        r = requests.get(f"{PIXABAY}/", params=params, timeout=25)
        r.raise_for_status()
        hits = [h for h in r.json().get("hits", [])
                if f"q{h['id']}" not in self.used and h.get("largeImageURL")]
        if hits:
            hit = random.choice(hits[:5])
            self._download(hit["largeImageURL"], dest)
            self._mark(f"q{hit['id']}")
            return True
        return False

    # -- generated fallback ------------------------------------------------
    @staticmethod
    def _gradient(dest, width, height, seed):
        rnd = random.Random(seed)
        palette = [((10, 20, 45), (0, 110, 160)), ((25, 10, 45), (120, 40, 140)),
                   ((8, 30, 35), (20, 130, 110)), ((35, 15, 15), (170, 60, 50))]
        top, bottom = rnd.choice(palette)
        img = Image.new("RGB", (width // 4, height // 4))
        px = ImageDraw.Draw(img)
        for y in range(img.height):
            t = y / img.height
            px.line([(0, y), (img.width, y)],
                    fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
        img.resize((width, height), Image.LANCZOS).save(dest)

    # -- public ------------------------------------------------------------
    def fetch(self, query, portrait, workdir, idx, width, height):
        """Return ('video'|'photo', path). Never raises — falls back to a generated gradient."""
        workdir = Path(workdir)
        words = query.split()
        queries = [query] + ([" ".join(words[:2])] if len(words) > 2 else []) + \
                  self.cfg["visuals"]["fallback_queries"]
        finders = {"pexels": (self._pexels_video, self._pexels_photo),
                   "pixabay": (self._pixabay_video, self._pixabay_photo)}
        for q in queries:
            for provider in self.providers:
                try:
                    if finders[provider][0](q, portrait, workdir / f"media_{idx:03d}.mp4"):
                        return "video", workdir / f"media_{idx:03d}.mp4"
                except Exception as e:
                    log(f"⚠️ {provider} video lookup failed for '{q}': {e}")
            for provider in self.providers:
                try:
                    if finders[provider][1](q, portrait, workdir / f"media_{idx:03d}.jpg"):
                        return "photo", workdir / f"media_{idx:03d}.jpg"
                except Exception as e:
                    log(f"⚠️ {provider} photo lookup failed for '{q}': {e}")
        img = workdir / f"media_{idx:03d}.png"
        self._gradient(img, width, height, seed=idx + sum(map(ord, query)))
        return "photo", img
