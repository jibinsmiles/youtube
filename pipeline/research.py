"""Pull the actual article text so the script is based on facts, not just a headline."""
import requests
import trafilatura

from .config import log
from .news import UA

UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")


def _download(url, ua):
    r = requests.get(url, headers={"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"}, timeout=25)
    r.raise_for_status()
    return trafilatura.extract(r.text, url=url, include_comments=False, include_tables=False) or ""


def fetch_article(url, limit=9000):
    """Extract article text. Retries once with a browser-like User-Agent if the first try gives little."""
    best = ""
    for ua in (UA, UA_BROWSER):
        try:
            text = _download(url, ua).strip()
        except Exception as e:
            log(f"⚠️ Could not read {url} ({type(e).__name__}): {e}")
            continue
        if len(text) > len(best):
            best = text
        if len(best) >= 800:
            break
    return best[:limit]


def best_text(story, limit):
    """Pick the longest of: extracted page text, full text from the feed, feed summary."""
    options = {"page": fetch_article(story.url, limit),
               "feed": (getattr(story, "content", "") or "")[:limit],
               "summary": story.summary or ""}
    origin = max(options, key=lambda k: len(options[k]))
    return options[origin], origin


def build_dossier(story, kind, cfg):
    """Return {'text': combined source text, 'sources': [...], 'chars': int}."""
    limit = 4000 if kind == "short" else 9000
    pieces, sources = [], []
    main_text, origin = best_text(story, limit)
    log(f"📄 {story.source}: {len(main_text)} chars of source text (from {origin})")
    if main_text:
        pieces.append(f"SOURCE 1 — {story.source}: {story.title}\n{main_text}")
        sources.append({"title": story.title, "url": story.url, "source": story.source})
    if kind == "long":
        for rel in story.related[:3]:
            text, origin = best_text(rel, 6000)
            if len(text) > 400:
                log(f"📄 {rel.source}: {len(text)} chars (from {origin})")
                pieces.append(f"SOURCE {len(sources) + 1} — {rel.source}: {rel.title}\n{text}")
                sources.append({"title": rel.title, "url": rel.url, "source": rel.source})
    text = "\n\n".join(pieces)
    return {"text": text, "sources": sources, "chars": len(text)}
