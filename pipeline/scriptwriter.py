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
{_extra_rules(cfg.get("ranking_rules"), "Channel rules (score 1 for anything that breaks them)")}

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
def _extra_rules(rules, heading):
    """Optional per-channel rule list rendered as a prompt block ('' when the channel has none)."""
    if not rules:
        return ""
    return f"\n{heading}:\n" + "\n".join(f"- {r}" for r in rules) + "\n"


def _spec(kind, cfg, evergreen=False):
    f = cfg["formats"][kind]
    custom = (((cfg.get("evergreen") or {}).get("structure") or {}).get(kind)) if evergreen else None
    if custom:          # a channel can define its own storytelling shape (e.g. a case story or a both-sides debate)
        return (f"Total narration: {f['target']} words. Each scene: {f['scene_words']} words. "
                f"Structure: {custom}")
    if evergreen:
        structure = ("HOOK (scene 1: a surprising fact, a bold claim or a question the viewer has always wondered about; "
                     "it must make them need the answer, and promise a payoff that comes LATER, for example 'and the "
                     "last detail is the strangest') -> the simple answer in one line -> how it works, step by step, "
                     "every scene adding a NEW fact or a concrete example/analogy, nothing repeated -> the common "
                     "misconception or the surprising detail (the promised payoff) -> why it matters to the viewer -> "
                     "a last line that echoes the opening so the video loops, plus one open question. Keep a surprise, "
                     "a number or a twist in every scene so there is never a reason to swipe. Spoken like a great explainer."
                     if kind == "short" else
                     "COLD OPEN hook (1-2 scenes, a surprising fact or the question everyone wonders about, no greeting) -> "
                     "the short answer -> promise what the viewer will know by the end -> background viewers need -> how it "
                     "works, step by step, with concrete examples and analogies -> a mini-cliffhanger before each new "
                     "section ('but here is where it gets interesting') -> the common misconceptions -> a real-world "
                     "example or the history -> why it matters -> what to explore next -> one closing question for the "
                     "comments. Every section must teach something new and specific. Do NOT stop early: the script must reach the full word count, so keep going with more sections, examples and details until it does.")
        return (f"Total narration: {f['target']} words. Each scene: {f['scene_words']} words. "
                f"Structure: {structure}")
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
    if getattr(story, "evergreen", False):
        attribution = ("- This is an EVERGREEN explainer: write it so it is still true and makes sense a year from now. "
                       "No 'today', 'this week', 'recently', 'just announced' or dated references. State well-established "
                       "facts plainly (no need to cite the encyclopedia on every line); attribute only disputed claims. "
                       "The title should be search-friendly, like 'How UPI Actually Works' or 'Why Phones Get Hot'.")
    else:
        attribution = '- Attribute important claims ("according to The Verge", "the company says").'
    return f"""You write scripts for the YouTube channel "{cfg['name']}" ({cfg['niche']}).
Audience: {cfg['audience']}
Narrator persona: {style['persona']}

FORMAT: {'YouTube Short (vertical)' if kind == 'short' else 'Long-form YouTube video (landscape)'}
{_spec(kind, cfg, getattr(story, 'evergreen', False))}
Suggested angle: {angle}
{avoid}
RULES:
{rules}
- Use ONLY facts found in the SOURCE MATERIAL below. Never invent numbers, prices, dates, quotes or specs.
  If something is unknown or not announced, say so plainly.
- Never say that something "has not been announced", "is not available" or "is unknown" unless the sources
  say exactly that. If the sources are silent on something (for example India pricing or availability), leave it
  out, or say "the report doesn't mention ...". Do not fill gaps with statements about what is or isn't known.
{attribution}
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
  "thumbnail_text": "2-4 words that make people NEED to click, yet stay 100% true to the video: a curiosity gap, a surprising real number from the sources, a myth-vs-truth contrast or a bold question (examples of the style: 'YOUR PHONE LIES', 'WHY IT NEVER DROPS', '99% DON'T KNOW', 'NOT WHAT YOU THINK'). Never promise or imply anything the video does not deliver; no made-up numbers, no fake urgency{_thumb_rules(cfg)}",
  "description": "2-3 plain sentences summarising the video",
  "tags": ["8-12 relevant tags"],
  "scenes": [{{"narration": "...", "shots": ["...", "..."], "callout": "..."}}]}}"""


def _thumb_rules(cfg):
    extra = (cfg.get("style") or {}).get("thumbnail_rules") or []
    return ("; " + "; ".join(r.rstrip(".").replace('"', "'") for r in extra)) if extra else ""


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
    style = cfg["style"]
    for phrase in style.get("required_phrases") or []:          # e.g. a "not medical advice" line
        if phrase.lower() not in full:
            problems.append(f'the narration must include the line "{phrase}" (say it naturally in the last scene)')
    packaging = " ".join([script.get("title", ""), script.get("thumbnail_text", ""), script.get("description", ""), full])
    for pat in style.get("forbidden_patterns") or []:            # hard safety gates, regex, case-insensitive
        m = re.search(pat, packaging, re.I)
        if m:
            problems.append(f'contains forbidden content "{m.group(0)}" (channel safety rule); remove it')
    if any(not s.get("shots") for s in scenes):
        problems.append("a scene has no shots (stock-footage queries)")
    title = script.get("title", "")
    if not title or len(title) > 80:
        problems.append("title missing or longer than 80 characters")
    return problems


def expand_script(llm, cfg, kind, script, dossier):
    """The model often writes a long script too short. Ask it to deepen the script it already wrote."""
    f = cfg["formats"][kind]
    have = sum(_words(sc.get("narration", "")) for sc in script["scenes"])
    prompt = f"""Here is a video script as JSON ({have} words of narration, far too short):
{json.dumps(script, ensure_ascii=False)}

It must reach {f['target']} words of narration in total (never below {f['min_words']}), each scene {f['scene_words']} words.
Expand it: add more scenes and make thin scenes fuller. Every added scene must teach something NEW and specific from
the source material below (a concrete number, an example, an analogy, a misconception, a little history, a
"here is where it gets interesting" turn). Do not repeat points and do not pad with filler. Keep the existing hook,
the order of the story, the tone and the closing question at the end. Keep the exact same JSON keys; every scene
needs its narration and its own "shots" list in the same format as the existing scenes.

{_keep_lines(cfg)}SOURCE MATERIAL (only state facts that are supported by it):
{dossier['text']}

Return the full expanded JSON only."""
    return normalize_scenes(llm.json(prompt, temperature=0.5, max_tokens=16000))


def _keep_lines(cfg):
    req = (cfg.get("style") or {}).get("required_phrases") or []
    return ("The last scene must keep these exact words: " + "; ".join(f'"{r}"' for r in req) + "\n\n") if req else ""


def write_script(llm, cfg, kind, story, dossier, angle, avoid_titles=()):
    feedback = []
    best = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = _prompt(cfg, kind, story, dossier, angle, avoid_titles, feedback)
        script = normalize_scenes(llm.json(prompt, temperature=0.8, max_tokens=16000))
        problems = validate(script, kind, cfg)
        if not problems:
            return script
        log(f"✍️ Script attempt {attempt} rejected: {problems}")
        feedback = problems
        if script.get("scenes") and (best is None or _total_words(script) > _total_words(best)):
            best = script
    # Long scripts usually fail only because they are too short: deepen the best draft instead of starting over.
    if kind == "long" and best:
        for round_no in range(int(cfg["formats"][kind].get("expand_rounds", 2))):
            best = expand_script(llm, cfg, kind, best, dossier)
            problems = validate(best, kind, cfg)
            if not problems:
                log(f"✅ Expanded the draft to {_total_words(best)} words")
                return best
            log(f"✍️ Expansion {round_no + 1} still rejected: {problems}")
    return None


def _total_words(script):
    return sum(_words(sc.get("narration", "")) for sc in script.get("scenes") or [])


# ---------------------------------------------------------------------------
# 3. Fact-check
# ---------------------------------------------------------------------------
def fact_check(llm, script, dossier, cfg=None):
    """Return a list of unsupported claims and safety-rule violations (empty = verified)."""
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

{_extra_rules((cfg or {}).get("fact_check_notes"), "Notes for this check")}{_compliance_block(cfg)}
Return JSON: {{"unsupported": [{{"claim": "...", "reason": "..."}}]}}  (empty list if everything is supported)"""
    data = llm.json(prompt, temperature=0.0, max_tokens=2048)
    return data.get("unsupported", [])


def _compliance_block(cfg):
    rules = (cfg or {}).get("compliance") or []
    if not rules:
        return ""
    return ("ALSO list, in the same JSON list, every line of the title, thumbnail text, description or narration that "
            "breaks one of these channel SAFETY RULES (put the offending text in \"claim\" and \"breaks rule: ...\" in "
            "\"reason\"). Be strict about these:\n" + "\n".join(f"- {r}" for r in rules) + "\n")


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
    rounds = int((cfg.get("research") or {}).get("fact_check_rounds", 2))     # checks; repairs = rounds - 1
    for round_no in range(rounds):
        unsupported = fact_check(llm, script, dossier, cfg)
        if not unsupported:
            log("✅ Fact-check passed")
            return script
        log(f"🔍 Fact-check found {len(unsupported)} unsupported claim(s): {unsupported[:3]}")
        if round_no == rounds - 1:
            break
        script = repair_script(llm, script, unsupported)
        if validate(script, kind, cfg):
            return None
    return None
