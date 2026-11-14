"""Word-highlighted captions (ASS format) burned in by ffmpeg."""
import re

YELLOW, WHITE = "&H0000FFFF&", "&H00FFFFFF&"


def _ts(seconds):
    seconds = max(seconds, 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def word_timings(narration, start, speech_seconds):
    """Spread the scene's speech time over its words, weighting by length and punctuation pauses."""
    words = narration.split()
    if not words:
        return []
    weights = []
    for w in words:
        wt = len(re.sub(r"\W", "", w)) + 1.5
        if w.endswith((",", ";", ":")):
            wt += 2
        elif w.endswith((".", "?", "!", "…")):
            wt += 4
        weights.append(wt)
    total, t, out = sum(weights), start, []
    for w, wt in zip(words, weights):
        dur = speech_seconds * wt / total
        out.append((w, t, t + dur))
        t += dur
    return out


def _chunks(words, max_words):
    chunk = []
    for w in words:
        chunk.append(w)
        if len(chunk) >= max_words or w[0].endswith((".", "?", "!", ",", ";", ":")):
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _safe(word, upper):
    word = word.replace("\\", "").replace("{", "").replace("}", "")
    return word.upper() if upper else word


def build_ass(scene_timings, kind, width, height, font="DejaVu Sans"):
    """scene_timings: [{'start': float, 'words': [(word, start, end), ...]}]."""
    short = kind == "short"
    size, outline, marginv = (92, 8, 600) if short else (52, 4, 70)
    max_words = 3 if short else 7
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{WHITE},&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},2,2,70,70,{marginv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for scene in scene_timings:
        words = scene["words"]
        for chunk in _chunks(words, max_words):
            for idx, (w, ws, we) in enumerate(chunk):
                # show the whole chunk while this word is spoken, highlight the active word
                parts = []
                for j, (cw, _, _) in enumerate(chunk):
                    txt = _safe(cw, short)
                    parts.append(f"{{\\c{YELLOW}}}{txt}{{\\c{WHITE}}}" if j == idx else txt)
                end = chunk[idx + 1][1] if idx + 1 < len(chunk) else we
                lines.append(f"Dialogue: 0,{_ts(ws)},{_ts(end)},Default,,0,0,0,,{' '.join(parts)}")
    return header + "\n".join(lines) + "\n"
