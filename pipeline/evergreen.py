"""Evergreen videos: timeless explainers that keep earning views through search and suggested videos.

Topic ideas come from the LLM (kept inside the channel's niche, never repeating recent videos); the facts come from
Wikipedia article text, so the same fact-check used for news still applies (everything must be in the sources).
"""
import hashlib
import re
from datetime import datetime, timezone

import requests

from .config import log
from .news import Story
from .state import closest_recent, title_tokens

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_UA = "EvergreenVideoBot/1.0 (https://github.com/jibinsmiles/youtube; automated educational explainers)"


def wiki_extracts(titles, limit=9000):
    """Plain-text extracts of existing Wikipedia articles: [(title, url, text)] (missing pages are dropped)."""
    out = []
    for title in titles[:4]:
        try:
            r = requests.get(WIKI_API, headers={"User-Agent": WIKI_UA}, timeout=25, params={
                "action": "query", "format": "json", "prop": "extracts", "explaintext": 1, "redirects": 1,
                "exsectionformat": "plain", "titles": title})
            r.raise_for_status()
            pages = (r.json().get("query") or {}).get("pages") or {}
        except Exception as e:
            log(f"⚠️ Wikipedia lookup failed for '{title}' ({type(e).__name__}): {e}")
            continue
        for page in pages.values():
            text = re.sub(r"\n{2,}", "\n", (page.get("extract") or "")).strip()
            if "missing" in page or len(text) < 600:
                continue
            real = page.get("title", title)
            out.append((real, "https://en.wikipedia.org/wiki/" + real.replace(" ", "_"), text))
    return out


def propose_topics(llm, cfg, recent_titles, n=8):
    """Ask for timeless explainer topics in the niche: [{'topic', 'angle', 'wiki': [exact article titles]}]."""
    hint = (cfg.get("evergreen") or {}).get("hint", "")
    topic_rules = (cfg.get("evergreen") or {}).get("rules") or []
    rules = ("TOPIC RULES (never suggest a topic that breaks one):\n" + "\n".join(f"- {r}" for r in topic_rules) + "\n"
             if topic_rules else "")
    avoid = "\n".join(f"- {t}" for t in recent_titles[:40]) or "- (none yet)"
    prompt = f"""You plan evergreen YouTube explainers for the channel "{cfg['name']}" ({cfg['niche']}).
Audience: {cfg['audience']}
{('Topic ideas to draw from: ' + hint) if hint else ''}

Suggest {n} timeless topics people search for years later: "how X works", "why Y happens", "X vs Y explained",
"what is Z and why it matters", "the history of Z". They must be accurate, concrete, interesting to this audience, and
NOT tied to a specific news event or date.
Prefer topics with a built-in curiosity gap that make people stop scrolling: something they use every day but
have never understood, a surprising number or myth, "why does X do Y", "what really happens when you ...",
"X vs Y: which is actually better". Skip dull, generic overviews ("What is technology")
{rules}Do not repeat or closely resemble these recent videos:
{avoid}

For each topic give 1-3 EXACT English Wikipedia article titles that contain the facts needed (real articles only).
Return JSON: {{"topics": [{{"topic": "video topic as a short phrase", "angle": "what makes it interesting, one sentence",
"wiki": ["Exact Wikipedia Title"]}}]}}"""
    data = llm.json(prompt, temperature=0.9, max_tokens=4096)
    return [t for t in data.get("topics", []) if t.get("topic") and t.get("wiki")]


def make_story(topic, wiki_url):
    sid = "evergreen-" + hashlib.sha1(topic.lower().encode()).hexdigest()[:12]
    return Story(id=sid, title=topic, url=wiki_url, source="Wikipedia", summary="",
                 published=datetime.now(timezone.utc), evergreen=True)


def candidates(llm, cfg, state, kind):
    """Yield (story, dossier, angle) for fresh evergreen topics, best first."""
    recent = state.recent_videos(days=int((cfg.get("evergreen") or {}).get("memory_days", 60)))
    used = state.used_story_ids()
    limit = 4000 if kind == "short" else 9000
    seen = 0
    for t in propose_topics(llm, cfg, [v["title"] for v in recent]):
        story = make_story(t["topic"], "")
        _, jac = closest_recent(t["topic"], recent)
        if story.id in used or jac >= 0.5:
            log(f"♻️ Evergreen topic already covered, skipping: {t['topic']}")
            continue
        pages = wiki_extracts(t["wiki"], limit)
        if not pages:
            log(f"⏭️ No usable Wikipedia text for: {t['topic']}")
            continue
        pieces, sources, budget = [], [], limit
        for i, (title, url, text) in enumerate(pages, 1):
            chunk = text[:max(budget // max(len(pages) - i + 1, 1), 1500)]
            pieces.append(f"SOURCE {i} — Wikipedia: {title}\n{chunk}")
            sources.append({"title": title, "url": url, "source": "Wikipedia"})
            budget -= len(chunk)
        story.url = sources[0]["url"]
        text = "\n\n".join(pieces)
        yield story, {"text": text, "sources": sources, "chars": len(text)}, t.get("angle", "")
        seen += 1
        if seen >= 4:
            return
