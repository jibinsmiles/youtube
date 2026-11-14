"""Stock footage from Pixabay and/or Pexels, with photo and generated-gradient fallbacks.

Set PIXABAY_API_KEY and/or PEXELS_API_KEY. Providers are tried in the order given by
`visuals.providers` in the channel YAML, skipping any whose key is not set.
"""
import base64
import io
import json
import random
import re
import time
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
        self._last_pixabay = 0.0
        self._cache = {}
        self.stats = {"video": 0, "photo": 0, "gradient": 0, "card": 0}
        self.by_provider = {}
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
    def _pixabay_get(self, path, params):
        """Rate-limited (Pixabay allows ~100 requests/minute) and cached for the duration of one run."""
        key = (path, tuple(sorted(params.items())))
        if key in self._cache:
            return self._cache[key]
        wait = 0.7 - (time.time() - self._last_pixabay)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            self._last_pixabay = time.time()
            r = requests.get(f"{PIXABAY}{path}", params={"key": self.pixabay_key, **params}, timeout=25)
            if r.status_code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            r.raise_for_status()
            self._cache[key] = r.json()
            return self._cache[key]
        raise RuntimeError("Pixabay rate limit")

    @staticmethod
    def _relevance(query, hit):
        """How many words of the search query appear in the clip's own tags (higher = more on-topic)."""
        words = {w for w in re.findall(r"[a-z]{3,}", query.lower())} - {"the", "and", "with", "for"}
        tags = (hit.get("tags") or "").lower()
        return sum(1 for w in words if w in tags)

    def _pixabay_video(self, query, portrait, dest, strict=False):
        data = self._pixabay_get("/videos/", {"q": query, "per_page": 30, "safesearch": "true"})
        candidates = []   # (orientation_penalty, -relevance, id, url)
        for hit in data.get("hits", []):
            if f"x{hit['id']}" in self.used or hit.get("duration", 0) < 4:
                continue
            for size in ("large", "medium"):          # prefer HD, skip 4K-sized files
                v = (hit.get("videos") or {}).get(size) or {}
                w, h = v.get("width") or 0, v.get("height") or 0
                if v.get("url") and 1280 <= max(w, h) <= 2600:
                    fits = (h > w) if portrait else (w >= h)
                    candidates.append((0 if fits else 1, -self._relevance(query, hit), hit["id"], v["url"]))
                    break
        # Shorts prefer vertical clips (a landscape clip gets centre-cropped); long videos only use landscape.
        if not portrait:
            candidates = [c for c in candidates if c[0] == 0]
        if not candidates:
            return False
        candidates.sort(key=lambda c: (c[0], c[1]))
        best = candidates[0]
        if strict and best[1] == 0:                    # nothing on-topic for this query -> try a different query
            return False
        group = [c for c in candidates if c[:2] == best[:2]]
        random.shuffle(group)
        for _, _, vid, url in group[:4]:
            self._download(url, dest)
            self._mark(f"x{vid}")
            return True
        return False

    def _pixabay_photo(self, query, portrait, dest, strict=False):
        params = {"q": query, "image_type": "photo", "per_page": 30, "safesearch": "true",
                  "orientation": "vertical" if portrait else "horizontal"}
        params["min_height" if portrait else "min_width"] = 1000
        data = self._pixabay_get("/", params)
        hits = [h for h in data.get("hits", []) if f"q{h['id']}" not in self.used and h.get("largeImageURL")]
        if not hits:
            return False
        hits.sort(key=lambda h: -self._relevance(query, h))
        if strict and self._relevance(query, hits[0]) == 0:
            return False
        top = self._relevance(query, hits[0])
        group = [h for h in hits if self._relevance(query, h) == top][:4]
        hit = random.choice(group)
        self._download(hit["largeImageURL"], dest)
        self._mark(f"q{hit['id']}")
        return True

    # -- vision-checked selection -----------------------------------------
    def _candidates(self, query, portrait, n_video=3):
        """On-topic-ish options for one shot WITHOUT downloading: [{'kind','id','url','thumb','tags'}]."""
        out, seen = [], set()
        words = query.split()
        for q in [query] + ([" ".join(words[:2])] if len(words) > 2 else []):
            try:
                data = self._pixabay_get("/videos/", {"q": q, "per_page": 30, "safesearch": "true"})
            except Exception as e:
                log(f"⚠️ pixabay video search failed for '{q}': {e}")
                continue
            vids = []
            for hit in data.get("hits", []):
                if f"x{hit['id']}" in self.used or hit["id"] in seen or hit.get("duration", 0) < 4:
                    continue
                for size in ("large", "medium"):
                    v = (hit.get("videos") or {}).get(size) or {}
                    w, h = v.get("width") or 0, v.get("height") or 0
                    if v.get("url") and 1280 <= max(w, h) <= 2600 and v.get("thumbnail"):
                        fits = (h > w) if portrait else (w >= h)
                        if portrait or fits:
                            vids.append(((0 if fits else 1), -self._relevance(q, hit), hit["id"], v, hit))
                        break
            vids.sort(key=lambda t: (t[0], t[1]))
            for _, _, vid, v, hit in vids[:n_video]:
                if vid not in seen:
                    seen.add(vid)
                    out.append({"kind": "video", "id": f"x{vid}", "url": v["url"], "thumb": v["thumbnail"],
                                "tags": hit.get("tags", "")})
            if len(out) >= n_video:
                break
        try:
            params = {"q": query, "image_type": "photo", "per_page": 20, "safesearch": "true",
                      "orientation": "vertical" if portrait else "horizontal",
                      "min_height" if portrait else "min_width": 1000}
            hits = [h for h in self._pixabay_get("/", params).get("hits", [])
                    if f"q{h['id']}" not in self.used and h.get("largeImageURL")]
            hits.sort(key=lambda h: -self._relevance(query, h))
            for h in hits[:1]:
                out.append({"kind": "photo", "id": f"q{h['id']}", "url": h["largeImageURL"],
                            "thumb": h.get("webformatURL") or h.get("previewURL"), "tags": h.get("tags", "")})
        except Exception as e:
            log(f"⚠️ pixabay photo search failed for '{query}': {e}")
        return out

    @staticmethod
    def _thumb_part(url):
        """Small JPEG of a preview image as a Gemini inline part (None if it cannot be fetched)."""
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            im = Image.open(io.BytesIO(r.content)).convert("RGB")
            im.thumbnail((320, 320))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=70)
            return {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}}
        except Exception:
            return None

    def _judge(self, llm, shots, cands):
        """One vision call per batch: for each shot, which option (A, B, ...) fits what is said, or None."""
        parts = [{"text": (
            "You pick stock footage for a YouTube video, like a documentary editor. For each SHOT you get the line "
            "being spoken and a search idea, then several candidate pictures (A, B, C...). Choose the candidate that "
            "most clearly shows what the narration is about (concrete, literal, relevant to the subject). Reject "
            "pictures that are unrelated, generic filler, nature or random people that do not fit the topic, or that "
            "contain readable text/logos of the wrong brand. If no candidate fits well, answer \"none\". It is much "
            "better to answer none than to pick something irrelevant.\n")}]
        index = {}
        for i, ((query, narration), options) in enumerate(zip(shots, cands), 1):
            shot_parts, labels = [], []
            for opt in options:
                part = self._thumb_part(opt["thumb"]) if opt.get("thumb") else None
                if part:
                    label = "ABCDEFGH"[len(labels)]
                    labels.append((label, options.index(opt)))
                    shot_parts += [{"text": f"SHOT {i} option {label}:"}, part]
            if not labels:
                continue
            index[i] = dict(labels)
            parts.append({"text": f"\nSHOT {i} — narration: \"{narration[:220]}\" | search idea: {query}"})
            parts += shot_parts
        parts.append({"text": 'Return JSON: {"picks": [{"shot": 1, "choice": "B"}, {"shot": 2, "choice": "none"}]}'})
        try:
            data = llm.generate("", json_mode=True, temperature=0.0, max_tokens=2048, parts=parts)
        except Exception as e:
            log(f"⚠️ Visual check unavailable ({type(e).__name__}: {str(e)[:120]}); using tag matching instead")
            return None
        picks = {}
        for p in data.get("picks", []):
            try:
                shot, choice = int(p["shot"]), str(p["choice"]).strip().upper()
            except (KeyError, ValueError, TypeError):
                continue
            if shot in index and choice in index[shot]:
                picks[shot] = index[shot][choice]
        return picks

    def fetch_all(self, llm, shots, portrait, workdir, width, height, batch=12):
        """shots: [(search_query, narration)]. Returns [(kind, path)] in the same order. Footage is chosen by looking
        at candidate thumbnails; shots with no good candidate fall back to the tag-matching search."""
        workdir = Path(workdir)
        results = [None] * len(shots)
        if llm is not None and "pixabay" in self.providers:
            cands = [self._candidates(q, portrait) for q, _ in shots]
            accepted = rejected = 0
            for start in range(0, len(shots), batch):
                chunk = range(start, min(start + batch, len(shots)))
                picks = self._judge(llm, [shots[i] for i in chunk], [cands[i] for i in chunk])
                if picks is None:                         # judge unavailable: leave these to the tag-matching search
                    continue
                for local, i in enumerate(chunk, 1):
                    choice = picks.get(local)
                    if choice is None:
                        rejected += 1
                        dest = workdir / f"media_{i:03d}.png"
                        self._card(dest, width, height, shots[i][0], seed=i + sum(map(ord, shots[i][0])))
                        self.stats["card"] += 1
                        results[i] = ("photo", dest)
                        continue
                    opt = cands[i][choice]
                    if opt["id"] in self.used:               # another shot already took this clip
                        continue
                    ext = "mp4" if opt["kind"] == "video" else "jpg"
                    dest = workdir / f"media_{i:03d}.{ext}"
                    try:
                        self._download(opt["url"], dest)
                    except Exception as e:
                        log(f"⚠️ download failed for shot {i + 1}: {e}")
                        continue
                    self._mark(opt["id"])
                    self.stats[opt["kind"]] += 1
                    self.by_provider["pixabay"] = self.by_provider.get("pixabay", 0) + 1
                    results[i] = (opt["kind"], dest)
                    accepted += 1
                    log(f"   🖼️ shot {i + 1}: '{shots[i][0]}' -> {opt['kind']} [{opt['tags'][:60]}]")
            log(f"🔍 Visual check: {accepted}/{len(shots)} shots matched by looking at the footage, "
                f"{rejected} had no fitting footage and show a branded text card")
        for i, (q, _) in enumerate(shots):
            if results[i] is None:
                results[i] = self.fetch(q, portrait, workdir, i, width, height)
        return results

    # -- generated fallback ------------------------------------------------
    @staticmethod
    def _card(dest, width, height, text, seed):
        """Branded text card (dark gradient + accent bar + big phrase) for shots with no fitting footage."""
        from PIL import ImageFont
        Visuals._gradient(dest, width, height, seed)
        img = Image.open(dest).convert("RGB")
        d = ImageDraw.Draw(img)
        text = re.sub(r"[^\w\s$%₹&'-]", "", text).upper().strip() or "TECH TADKA"
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        size = int(width * (0.11 if height > width else 0.07))
        max_w = int(width * 0.78)
        while True:
            try:
                font = ImageFont.truetype(font_path, size)
            except OSError:
                font = ImageFont.load_default()
                break
            lines, cur = [], ""
            for w in text.split():
                trial = (cur + " " + w).strip()
                if cur and d.textlength(trial, font=font) > max_w:
                    lines.append(cur)
                    cur = w
                else:
                    cur = trial
            lines.append(cur)
            if max(d.textlength(l, font=font) for l in lines) <= max_w or size <= 40:
                break
            size = int(size * 0.9)
        line_h = int(size * 1.25)
        top = (height - line_h * len(lines)) // 2
        bar_w = int(width * 0.18)
        d.rounded_rectangle(((width - bar_w) // 2, top - int(size * 0.7), (width + bar_w) // 2,
                             top - int(size * 0.7) + 10), 5, fill=(255, 138, 0))
        for k, l in enumerate(lines):
            d.text(((width - d.textlength(l, font=font)) / 2, top + k * line_h), l, font=font, fill=(255, 255, 255))
        img.save(dest)

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
        """Return ('video'|'photo', path). Never raises — falls back to a generated gradient.

        Order: on-topic videos for the scene's own queries, then on-topic photos, then generic fallback
        videos/photos. The scene's own queries must match a clip's tags to be accepted, so footage stays on-topic;
        only the generic fallback queries accept anything."""
        workdir = Path(workdir)
        words = query.split()
        specific = [query] + ([" ".join(words[:2])] if len(words) > 2 else [])
        fallback = self.cfg["visuals"]["fallback_queries"]
        attempts = ([(q, True, "video") for q in specific] + [(q, True, "photo") for q in specific] +
                    [(q, False, "video") for q in fallback] + [(q, False, "photo") for q in fallback])
        finders = {"pexels": {"video": self._pexels_video, "photo": self._pexels_photo},
                   "pixabay": {"video": self._pixabay_video, "photo": self._pixabay_photo}}
        for q, strict, media_kind in attempts:
            dest = workdir / f"media_{idx:03d}.{'mp4' if media_kind == 'video' else 'jpg'}"
            for provider in self.providers:
                try:
                    fn = finders[provider][media_kind]
                    ok = fn(q, portrait, dest, strict) if provider == "pixabay" else fn(q, portrait, dest)
                except Exception as e:
                    log(f"⚠️ {provider} {media_kind} lookup failed for '{q}': {e}")
                    continue
                if ok:
                    self.stats[media_kind] += 1
                    self.by_provider[provider] = self.by_provider.get(provider, 0) + 1
                    return media_kind, dest
        img = workdir / f"media_{idx:03d}.png"
        self._gradient(img, width, height, seed=idx + sum(map(ord, query)))
        self.stats["gradient"] += 1
        return "photo", img
