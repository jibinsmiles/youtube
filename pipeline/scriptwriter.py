"""Story ranking, script writing, quality gates and fact-checking."""
import json
import re

from .config import log
from .state import closest_recent

MAX_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# 1. Ranking
# ---------------------------------------------------------------------------
def rank_stories(llm, cfg, stories, recent=(), top_n=6):
    """Return [(story, score, angle)] best first. Stories about a topic we already covered are dropped
    unless they report a materially new development (then the angle says what is new)."""
    stories = sorted(stories, key=lambda s: (-s.coverage, s.age_hours))[:30]
    lines = [f"[{i}] ({s.source}, {s.age_hours:.0f}h old, also covered by {s.coverage} other outlets) "
             f"{s.title} — {s.summary[:220]}" for i, s in enumerate(stories)]
    if recent:
        recent_block = "\n".join(f'- "{v["title"]}" ({v["ts"][:10]}) — {v["summary"][:240]}' for v in recent[:25])
    else:
        recent_block = "(none yet)"
    prompt = f"""You are the editor of the YouTube channel "{cfg['name']}" ({cfg['niche']}).
Audience: {cfg['audience']}

VIDEOS WE PUBLISHED RECENTLY (never repeat a topic we already covered):
{recent_block}

Score each candidate story from 1 to 10 for how well it would work as a video for THIS audience. Weigh:
- real newsworthiness and freshness
- relevance to Indian viewers, or broad universal curiosity
- room for ORIGINAL ANALYSIS (a "what this really means" angle), not just restating the headline
- wide coverage by several outlets signals a genuine trend

Score 1-3 for: unconfirmed rumours or leaks, tragedies, partisan political fights, press-release fluff,
listicles, discount or deal posts. If several candidates are the same story, keep only the best one.

For EACH candidate also set "topic_status" by comparing it with the recent videos above:
- "new": a topic none of our recent videos covered.
- "update": the same topic as a recent video, AND this story reports a materially NEW development (a new
  decision, number, announcement, reversal, official response or outcome). Say in "what_is_new" what exactly is
  new, in one sentence. Be strict: opinion pieces, reactions, explainers, or another outlet re-reporting the same
  facts are NOT updates.
- "duplicate": same topic as a recent video with nothing materially new.

Candidates:
{chr(10).join(lines)}

Return JSON exactly like:
{{"ranked": [{{"index": 0, "score": 8, "topic_status": "new", "what_is_new": "", "angle": "one sentence: the specific insight or angle the video should take"}}]}}
Include only the {top_n} best stories, best first."""
    data = llm.json(prompt, temperature=0.3, max_tokens=3072)
    out = []
    for item in data.get("ranked", []):
        try:
            story = stories[int(item["index"])]
            score = float(item["score"])
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        angle = str(item.get("angle", ""))
        status = str(item.get("topic_status", "new")).lower()
        what_is_new = str(item.get("what_is_new", "")).strip()
        prev, sim = closest_recent(story.title, recent)
        if status == "new" and prev and sim >= 0.5:         # safety net: the model missed an obvious repeat
            log(f"♻️  Skipping (looks like a repeat of \"{prev['title'][:60]}\"): {story.title[:70]}")
            continue
        if status == "duplicate" or (status == "update" and not what_is_new):
            log(f"♻️  Skipping (already covered, nothing new): {story.title[:80]}")
            continue
        if status == "update":
            prev_title = prev["title"] if prev else "an earlier video"
            log(f"🆕 Update to \"{prev_title[:60]}\": {what_is_new[:100]}")
            angle = (f'UPDATE to our earlier video "{prev_title}". Cover ONLY what is new: {what_is_new} '
                     f"Give the background in one sentence at most, and make the title clearly about the new development.")
        out.append((story, score, angle))
    return sorted(out, key=lambda t: -t[1])


# ---------------------------------------------------------------------------
# 2. Script generation
# ---------------------------------------------------------------------------
def _spec(kind, cfg):
    f = cfg["formats"][kind]
    if kind == "short":
        structure = ("HOOK (scene 1: a surprising concrete fact or question, max 12 words) -> what happened -> "
                     "the one detail that matters -> what it means for viewers (an India angle ONLY if the sources "
                     "support one, otherwise the wider impact) -> a closing line that "
                     "leaves one open question. Fast, no padding.")
    else:
        structure = ("COLD OPEN hook (1-2 scenes, the most surprising fact, no greeting) -> what happened -> "
                     "background viewers need -> how it works or what is actually new -> why it matters -> "
                     "the India angle (ONLY if the sources provide one; if they do not, use this part for the wider "
                     "impact on users or the industry instead) -> the counter-argument or what could go wrong -> what to watch next -> one closing "
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
    shots = "1-2" if kind == "short" else "3-4"
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
- Never say that something "has not been announced", "is not available" or "is unknown" unless the sources
  say exactly that. If the sources are silent on something (for example India pricing or availability), leave it
  out, or say "the report doesn't mention ...". Do not fill gaps with statements about what is or isn't known.
- Attribute important claims ("according to The Verge", "the company says").
- Clearly separate fact from your own analysis ("my read is...", "here's what this likely means").
- Never use these phrases: {banned}
- Write for the ear: short sentences, plain words, spoken rhythm. No stage directions, no emojis, no hashtags.
- Do not greet the viewer and do not say the channel name in the opening.
- "shots" = {shots} DIFFERENT stock-footage search queries per scene (2-4 plain words each), in the order they
  should appear while the scene is spoken. Think like a documentary editor: show concrete, filmable things that
  match what is being said at that moment (e.g. "data center servers", "engineer typing laptop", "stock market
  screen", "crowded indian street"). Be literal and specific, vary the shots, never repeat a query across the video,
  and never use brand names, people's names or logos.
- "callout" = a 1-4 word on-screen label shown at the start of the scene: a key number, name or short phrase that
  is actually in that scene's narration (e.g. "$5 BILLION", "44,077 CHIPS", "NVIDIA CEO"). Use "" if nothing
  stands out. The FIRST scene's callout is the hook: the single most striking fact, max 4 words.
{fix}

SOURCE MATERIAL:
{dossier['text']}

Return JSON exactly like:
{{"title": "specific, curiosity-driven, max 65 chars, no clickbait lies, no emojis, no hashtags",
  "thumbnail_text": "max 4 words, punchy",
  "description": "2-3 plain sentences summarising the video",
  "tags": ["8-12 relevant tags"],
  "scenes": [{{"narration": "...", "shots": ["...", "..."], "callout": "..."}}]}}"""


def _words(text):
    return len(re.findall(r"\b[\w'’₹$%.-]+\b", text))



def normalize_scenes(script):
    """Make every scene carry a clean list of unique 'shots' and a callout that is grounded in its narration."""
    seen_queries = set()
    for sc in script.get("scenes") or []:
        raw = sc.get("shots") or ([sc["visual_query"]] if sc.get("visual_query") else [])
        shots = []
        for q in raw:
            q = " ".join(str(q).split())
            if q and q.lower() not in seen_queries:
                shots.append(q)
                seen_queries.add(q.lower())
        if not shots and raw:
            shots = [" ".join(str(raw[0]).split())]       # reuse a repeated query rather than reject the script
        sc["shots"] = shots[:4]
        sc["visual_query"] = shots[0] if shots else ""
        callout = " ".join(str(sc.get("callout", "") or "").split())
        narr = sc.get("narration", "").lower().replace(",", "")
        if len(callout.split()) > 5 or len(callout) > 28:
            callout = ""
        digits = re.findall(r"\d+(?:\.\d+)?", callout.replace(",", ""))
        words = [w for w in re.findall(r"[a-z]{3,}", callout.lower())]
        if digits and not all(d in narr for d in digits):
            callout = ""                               # never show a number the narrator did not say
        elif not digits and words and not any(w in narr for w in words):
            callout = ""                               # label must come from the narration
        sc["callout"] = callout
    return script


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
    if any(not s.get("shots") for s in scenes):
        problems.append("a scene has no shots (stock-footage queries)")
    title = script.get("title", "")
    if not title or len(title) > 80:
        problems.append("title missing or longer than 80 characters")
    return problems


def write_script(llm, cfg, kind, story, dossier, angle, avoid_titles=()):
    feedback = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = _prompt(cfg, kind, story, dossier, angle, avoid_titles, feedback)
        script = normalize_scenes(llm.json(prompt, temperature=0.8, max_tokens=16000))
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
    packaging = (f"VIDEO TITLE: {script.get('title', '')}\n"
                 f"THUMBNAIL TEXT: {script.get('thumbnail_text', '')}\n"
                 f"DESCRIPTION: {script.get('description', '')}")
    prompt = f"""You are a strict fact-checker for a news channel.

SOURCE MATERIAL:
{dossier['text']}

TITLE, THUMBNAIL AND DESCRIPTION TO CHECK (a headline must not state or imply more than the sources support,
including any number, amount, name or cause it contains):
{packaging}

NARRATION TO CHECK:
{narration}

List every FACTUAL claim in the title, thumbnail text, description and narration (numbers, dates, prices, names, quotes, specs, causal claims)
that is NOT supported by the source material. Claims clearly framed as the narrator's opinion or analysis
("my read", "this likely means") are fine. Be precise and do not nitpick wording.
Questions put to the audience (for example a closing question inviting comments) are not factual claims; ignore them.
A statement about what the source material itself does or does not mention (for example "the report doesn't
say when it reaches India") is acceptable if it is true. A claim that something has NOT been announced, is
unavailable or does not exist, when the sources are silent on it, IS unsupported.

Return JSON: {{"unsupported": [{{"claim": "...", "reason": "..."}}]}}  (empty list if everything is supported)"""
    data = llm.json(prompt, temperature=0.0, max_tokens=2048)
    return data.get("unsupported", [])


def repair_script(llm, script, unsupported):
    issues = "\n".join(f"- {u.get('claim')} ({u.get('reason')})" for u in unsupported)
    prompt = f"""Here is a video script as JSON:
{json.dumps(script, ensure_ascii=False)}

A fact-checker found these claims are NOT supported by the sources:
{issues}

Rewrite the script so each of these claims is removed or softened to what the sources actually support.
Prefer deleting the claim over replacing it with another statement about availability, pricing or plans,
and do not add any new factual claims. Keep the same structure, tone, JSON format and about the same length.
Return the full corrected JSON only."""
    return normalize_scenes(llm.json(prompt, temperature=0.3, max_tokens=16000))


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
