"""Find fresh stories in the channel's niche and measure how widely each is being covered."""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape

import feedparser
import requests

from .config import log
from .state import title_tokens, same_story

UA = "Mozilla/5.0 (compatible; TrendPulseBot/1.0; +https://github.com/)"


@dataclass
class Story:
    id: str
    title: str
    url: str
    source: str
    summary: str
    published: datetime
    age_hours: float = 0.0
    coverage: int = 0                      # how many OTHER outlets ran a similar story
    related: list = field(default_factory=list)


def _clean(html):
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _fetch_feed(feed):
    try:
        r = requests.get(feed["url"], headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
        return feedparser.parse(r.content)
    except Exception as e:
        log(f"⚠️ Feed failed ({feed.get('name', feed['url'])}): {e}")
        return None


def fetch_candidates(cfg, max_age_hours=36):
    now = datetime.now(timezone.utc)
    stories = []
    for feed in cfg["sources"]["feeds"]:
        parsed = _fetch_feed(feed)
        if not parsed:
            continue
        for e in parsed.entries[:25]:
            title = _clean(e.get("title", ""))
            url = e.get("link", "")
            if not title or not url:
                continue
            ts = e.get("published_parsed") or e.get("updated_parsed")
            published = datetime(*ts[:6], tzinfo=timezone.utc) if ts else now
            age = (now - published).total_seconds() / 3600
            if age > max_age_hours or age < -1:
                continue
            stories.append(Story(
                id=hashlib.sha1(url.encode()).hexdigest()[:12], title=title, url=url,
                source=feed.get("name", "Unknown"), summary=_clean(e.get("summary", ""))[:600],
                published=published, age_hours=max(age, 0)))

    # de-duplicate identical URLs, then measure cross-outlet coverage
    unique = {s.url: s for s in stories}
    stories = list(unique.values())
    toks = {s.id: title_tokens(s.title) for s in stories}
    for s in stories:
        for o in stories:
            if o.id != s.id and o.source != s.source and same_story(toks[s.id], toks[o.id]):
                s.related.append(o)
        s.coverage = len({o.source for o in s.related})
    log(f"📰 {len(stories)} fresh stories from {len(cfg['sources']['feeds'])} feeds")
    return stories
