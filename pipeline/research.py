"""Pull the actual article text so the script is based on facts, not just a headline."""
import requests
import trafilatura

from .config import log
from .news import UA


def fetch_article(url, limit=9000):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
        r.raise_for_status()
        text = trafilatura.extract(r.text, url=url, include_comments=False, include_tables=False) or ""
        return text.strip()[:limit]
    except Exception as e:
        log(f"⚠️ Could not read {url}: {e}")
        return ""


def build_dossier(story, kind, cfg):
    """Return {'text': combined source text, 'sources': [...], 'chars': int}."""
    limit = 4000 if kind == "short" else 9000
    pieces, sources = [], []
    main_text = fetch_article(story.url, limit) or story.summary
    if main_text:
        pieces.append(f"SOURCE 1 — {story.source}: {story.title}\n{main_text}")
        sources.append({"title": story.title, "url": story.url, "source": story.source})
    if kind == "long":
        for rel in story.related[:3]:
            text = fetch_article(rel.url, 6000)
            if len(text) > 400:
                pieces.append(f"SOURCE {len(sources) + 1} — {rel.source}: {rel.title}\n{text}")
                sources.append({"title": rel.title, "url": rel.url, "source": rel.source})
    text = "\n\n".join(pieces)
    return {"text": text, "sources": sources, "chars": len(text)}
