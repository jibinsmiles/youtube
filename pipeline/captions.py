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


_FONT_FILES = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"]
_fonts = {}


def text_width(text, size):
    """Pixel width of bold DejaVu Sans text at `size` (falls back to a conservative estimate)."""
    try:
        from PIL import ImageFont
        if size not in _fonts:
            for f in _FONT_FILES:
                try:
                    _fonts[size] = ImageFont.truetype(f, size)
                    break
                except OSError:
                    continue
            else:
                _fonts[size] = None
        if _fonts[size] is not None:
            return _fonts[size].getlength(text)
    except ImportError:
        pass
    return len(text) * size * 0.78


def _chunks(words, max_words, fits=None):
    """Group words into on-screen chunks: at most max_words, ending at punctuation, and (if `fits` is given)
    never wider than the screen allows."""
    chunk = []
    for w in words:
        if chunk and fits and not fits([x[0] for x in chunk] + [w[0]]):
            yield chunk
            chunk = []
        chunk.append(w)
        if len(chunk) >= max_words or w[0].endswith((".", "?", "!", ",", ";", ":")):
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _safe(word, upper):
    word = word.replace("\\", "").replace("{", "").replace("}", "")
    return word.upper() if upper else word


def _callout_text(text, size, max_width):
    """Return (ass_text, font_size): label split over balanced lines, shrunk if still too wide for the frame."""
    text = text.upper().replace("\\", "").replace("{", "").replace("}", "").strip()
    words = text.split()
    lines = [" ".join(words)]
    if len(text) > 14 and len(words) > 1:
        for n in (2, 3):                                    # prefer 2 lines, then 3
            n = min(n, len(words))
            # balanced split by character count
            per = len(text) / n
            lines, cur = [], []
            for w in words:
                if cur and len(" ".join(cur + [w])) > per and len(lines) < n - 1:
                    lines.append(" ".join(cur))
                    cur = []
                cur.append(w)
            lines.append(" ".join(cur))
            if max(text_width(l, size) for l in lines) <= max_width:
                break
    widest = max(text_width(l, size) for l in lines)
    if widest > max_width:
        size = max(36, int(size * max_width / widest))
    return "\\N".join(lines), size


def build_ass(scene_timings, kind, width, height, font="DejaVu Sans", show_captions=True):
    """scene_timings: [{'start': float, 'dur': float, 'words': [(word, start, end), ...], 'callout': str}].

    Word-highlight captions (Shorts) plus an animated 'callout' label at the start of scenes that have one.
    """
    short = kind == "short"
    size, outline, marginv = (80, 7, 600) if short else (52, 4, 70)
    max_words = 3 if short else 7
    # Keep text inside the frame and clear of the Shorts side buttons: widest allowed line in pixels.
    line_max = 760 if short else int(width * 0.8)
    # Callout: white bold text in a red box. Shorts: top third, centred. Long: lower-left.
    c_size, c_pad, c_align, c_marginv, c_marginl = (96, 24, 8, 330, 70) if short else (60, 16, 1, 120, 90)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{WHITE},&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},2,2,70,70,{marginv},1
Style: Callout,{font},{c_size},&H00FFFFFF,&H000000FF,&H000A0ADC,&H00000000,-1,0,0,0,100,100,2,0,3,{c_pad},0,{c_align},{c_marginl},70,{c_marginv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for scene in scene_timings:
        if show_captions:
            fits = lambda ws_: text_width(" ".join(_safe(x, short) for x in ws_), size) <= line_max
            for chunk in _chunks(scene["words"], max_words, fits):
                # a single very long word is shrunk to fit instead of running off the screen
                one = " ".join(_safe(c[0], short) for c in chunk)
                w_full = text_width(one, size)
                fs = f"{{\\fs{max(36, int(size * line_max / w_full))}}}" if w_full > line_max else ""
                for idx, (w, ws, we) in enumerate(chunk):
                    # show the whole chunk while this word is spoken, highlight the active word
                    parts = []
                    for j, (cw, _, _) in enumerate(chunk):
                        txt = _safe(cw, short)
                        parts.append(f"{{\\c{YELLOW}}}{txt}{{\\c{WHITE}}}" if j == idx else txt)
                    end = chunk[idx + 1][1] if idx + 1 < len(chunk) else we
                    lines.append(f"Dialogue: 0,{_ts(ws)},{_ts(end)},Default,,0,0,0,,{fs}{' '.join(parts)}")
        callout = (scene.get("callout") or "").strip()
        if callout:
            start = scene["start"] + 0.2
            end = start + min(3.4, max(scene.get("dur", 3.0) - 0.5, 1.2))
            # box = text + padding both sides; at 1.08x pop scale it must still fit: keep ~15% margin
            avail = (width - 2 * 70 if short else width - 90 - 70) * 0.84 - 2 * c_pad
            ctext, csize = _callout_text(callout, c_size, avail)
            size_tag = f"\\fs{csize}" if csize != c_size else ""
            pop = "{" + size_tag + "\\fad(140,200)\\fscx55\\fscy55\\t(0,220,\\fscx108\\fscy108)\\t(220,360,\\fscx100\\fscy100)}"
            lines.append(f"Dialogue: 1,{_ts(start)},{_ts(end)},Callout,,0,0,0,,{pop}{ctext}")
    return header + "\n".join(lines) + "\n"
