"""Story ranking, script writing, quality gates and fact-checking."""
import json
import re

from .config import log

MAX_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# 1. Ranking
# ---------------------------------------------------------------------------
def rank_stories(llm, cfg, stories, top_n=6):
    """Return [(story, score, angle)] best first."""
    stories = sorted(stories, key=lambda s: (-s.coverage, s.age_hours))[:30]
    lines = [f"[{i}] ({s.source}, {s.age_hours:.0f}h old, also covered by {s.coverage} other outlets) "
             f"{s.title} — {s.summary[:220]}" for i, s in enumerate(stories)]
    prompt = f"""You are the editor of the YouTube channel "{cfg['name']}" ({cfg['niche']}).
Audience: {cfg['audience']}

Score each candidate story from 1 to 10 for how well it would work as a video for THIS audience. Weigh:
- real newsworthiness and freshness
- relevance to Indian viewers, or broad universal curiosity
- room for ORIGINAL ANALYSIS (a "what this really means" angle), not just restating the headline
- wide coverage by several outlets signals a genuine trend

Score 1-3 for: unconfirmed rumours or leaks, tragedies, partisan political fights, press-release fluff,
listicles, discount or deal posts. If several candidates are the same story, keep only the best one.

Candidates:
{chr(10).join(lines)}

Return JSON exactly like:
{{"ranked": [{{"index": 0, "score": 8, "angle": "one sentence: the specific insight or angle the video should take"}}]}}
Include only the {top_n} best stories, best first."""
    data = llm.json(prompt, temperature=0.3, max_tokens=2048)
    out = []
    for item in data.get("ranked", []):
        try:
            story = stories[int(item["index"])]
            out.append((story, float(item["score"]), str(item.get("angle", ""))))
        except (KeyError, ValueError, IndexError, TypeError):
            continue
    return sorted(out, key=lambda t: -t[1])


# ---------------------------------------------------------------------------
# 2. Script generation
# ---------------------------------------------------------------------------
def _spec(kind, cfg):
    f = cfg["formats"][kind]
    if kind == "short":
        structure = ("HOOK (scene 1: a surprising concrete fact or question, max 12 words) -> what happened -> "
                     "the one detail that matters -> what it means for the viewer in India -> a closing line that "
                     "leaves one open question. Fast, no padding.")
    else:
        structure = ("COLD OPEN hook (1-2 scenes, the most surprising fact, no greeting) -> what happened -> "
                     "background viewers need -> how it works or what is actually new -> why it matters -> "
                     "the India angle (pricing, availability, impact on Indian users or industry, only from the "
                     "sources) -> the counter-argument or what could go wrong -> what to watch next -> one closing "
                     "question for the comments.")
    return (f"Total narration: {f['target']} words. Each scene: {f['scene_words']} words. "
            f"Structure: {structure}")


def _prompt(cfg, kind, story, dossier, angle, avoid_titles, feedback):
    style = cfg["style"]
    rules = "\n".join(f"- {r}" for r in style["rules"])
    banned = ", ".join(f'"{p}"' for p in style["banned_phrases"])
    avoid = ("\nThe channel already published these videos on this story; take a clearly DIFFERENT angle:\n"
             + "\n".join(f"- {t}" for t in avoid_titles)) if avoid_titles else ""
    fix = f"\nYour previous attempt was rejected. Fix these problems:\n- " + "\n- ".join(feedback) if feedback else ""
    return f"""You write scripts for the YouTube channel "{cfg['name']}" ({cfg['niche']}).
Audience: {cfg['audience']}
Narrator persona: {style['persona']}

FORMAT: {'YouTube Short (vertical)' if kind == 'short' else 'Long-form YouTube video (landscape)'}
{_spec(kind, cfg)}
Suggested angle: {angle}
{avoid}
RULES:
{rules}
- Use ONLY facts found in the SOURCE MATERIAL below. Never invent numbers, prices, dates, quotes or specs.
  If something is unknown or not announced, say so plainly.
- Attribute important claims ("according to The Verge", "the company says").
- Clearly separate fact from your own analysis ("my read is...", "here's what this likely means").
- Never use these phrases: {banned}
- Write for the ear: short sentences, plain words, spoken rhythm. No stage directions, no emojis, no hashtags.
- Do not greet the viewer and do not say the channel name in the opening.
- visual_query = 2-4 simple words describing something a stock-footage library could really film
  (e.g. "person typing laptop", "server room", "smartphone close up", "india city night").
  Never use brand names, people's names or logos in visual_query.
{fix}

SOURCE MATERIAL:
{dossier['text']}

Return JSON exactly like:
{{"title": "specific, curiosity-driven, max 65 chars, no clickbait lies, no emojis, no hashtags",
  "thumbnail_text": "max 4 words, punchy",
  "description": "2-3 plain sentences summarising the video",
  "tags": ["8-12 relevant tags"],
  "scenes": [{{"narration": "...", "visual_query": "..."}}]}}"""


def _words(text):
    return len(re.findall(r"\b[\w'’₹$%.-]+\b", text))


def validate(script, kind, cfg):
    """Return a list of problems (empty list = good)."""
    problems = []
    f = cfg["formats"][kind]
    scenes = script.get("scenes") or []
    if not scenes:
        return ["no scenes returned"]
    total = sum(_words(s.get("narration", "")) for s in scenes)
    if total < f["min_words"]:
        problems.append(f"too short: {total} words, need at least {f['min_words']}")
    if total > f["max_words"]:
        problems.append(f"too long: {total} words, max {f['max_words']}")
    full = " ".join(s.get("narration", "") for s in scenes).lower()
    for phrase in cfg["style"]["banned_phrases"]:
        if phrase.lower() in full:
            problems.append(f'contains banned phrase "{phrase}"')
    first = scenes[0].get("narration", "").lower()
    if re.match(r"\s*(welcome|hello|hey|hi |what's up|in today|today we)", first):
        problems.append("opening scene greets the viewer instead of hooking")
    if any(not s.get("visual_query", "").strip() for s in scenes):
        problems.append("a scene is missing visual_query")
    title = script.get("title", "")
    if not title or len(title) > 80:
        problems.append("title missing or longer than 80 characters")
    return problems


def write_script(llm, cfg, kind, story, dossier, angle, avoid_titles=()):
    feedback = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = _prompt(cfg, kind, story, dossier, angle, avoid_titles, feedback)
        script = llm.json(prompt, temperature=0.8, max_tokens=16000)
        problems = validate(script, kind, cfg)
        if not problems:
            return script
        log(f"✍️ Script attempt {attempt} rejected: {problems}")
        feedback = problems
    return None


# ---------------------------------------------------------------------------
# 3. Fact-check
# ---------------------------------------------------------------------------
def fact_check(llm, script, dossier):
    """Return a list of unsupported claims (empty = verified)."""
    narration = "\n".join(s["narration"] for s in script["scenes"])
    prompt = f"""You are a strict fact-checker for a news channel.

SOURCE MATERIAL:
{dossier['text']}

NARRATION TO CHECK:
{narration}

List every FACTUAL claim in the narration (numbers, dates, prices, names, quotes, specs, causal claims)
that is NOT supported by the source material. Claims clearly framed as the narrator's opinion or analysis
("my read", "this likely means") are fine. Be precise and do not nitpick wording.

Return JSON: {{"unsupported": [{{"claim": "...", "reason": "..."}}]}}  (empty list if everything is supported)"""
    data = llm.json(prompt, temperature=0.0, max_tokens=2048)
    return data.get("unsupported", [])


def repair_script(llm, script, unsupported):
    issues = "\n".join(f"- {u.get('claim')} ({u.get('reason')})" for u in unsupported)
    prompt = f"""Here is a video script as JSON:
{json.dumps(script, ensure_ascii=False)}

A fact-checker found these claims are NOT supported by the sources:
{issues}

Rewrite the script so each of these claims is removed or softened to what the sources actually support
(or marked clearly as unknown). Keep the same structure, tone, JSON format and about the same length.
Return the full corrected JSON only."""
    return llm.json(prompt, temperature=0.3, max_tokens=16000)


def verified_script(llm, cfg, kind, story, dossier, angle, avoid_titles=()):
    """Write, fact-check, repair once if needed. Returns None if it cannot be made trustworthy."""
    script = write_script(llm, cfg, kind, story, dossier, angle, avoid_titles)
    if not script:
        return None
    for round_no in range(2):
        unsupported = fact_check(llm, script, dossier)
        if not unsupported:
            log("✅ Fact-check passed")
            return script
        log(f"🔍 Fact-check found {len(unsupported)} unsupported claim(s): {unsupported[:3]}")
        if round_no == 1:
            break
        script = repair_script(llm, script, unsupported)
        if validate(script, kind, cfg):
            return None
    return None
