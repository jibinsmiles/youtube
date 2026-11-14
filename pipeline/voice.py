"""Text-to-speech, one clip per scene so audio and visuals stay perfectly in sync.

Providers are tried in order (config: voice.providers). A provider is used for the WHOLE video: if it fails
part-way (quota, outage) the video is re-voiced from scratch with the next provider, so one video never
mixes two different voices.

  elevenlabs  paid, very natural            (needs ELEVENLABS_API_KEY + elevenlabs.voice_id)
  gemini      natural, uses GEMINI_API_KEY  (Gemini speech models)
  edge        free Microsoft voices, last-resort fallback (always available, sounds the most synthetic)
"""
import asyncio
import base64
import re
import subprocess
import time
from pathlib import Path

import requests

from .config import env, log

PAD_SECONDS = 0.18   # breathing room added after every scene
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"

DEFAULT_GEMINI_MODELS = ["gemini-3.8-flash-tts", "gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts"]
DEFAULT_STYLE = ("Warm, confident and conversational, like a friendly tech journalist explaining the news to a "
                 "smart friend. Natural pace with small pauses, light emphasis on the key facts, never like a "
                 "news announcer reading a teleprompter.")


def normalize_for_speech(text):
    t = text.replace("&", " and ").replace("%", " percent")
    t = re.sub(r"₹\s?([\d,\.]+)", r"\1 rupees", t)
    t = re.sub(r"\$\s?([\d,\.]+)", r"\1 dollars", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def probe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


# Trim the silence TTS engines leave at both ends (keeps natural pauses inside the sentence), roll off rumble,
# apply gentle compression for a more even, "broadcast" voice, then add a fixed breathing gap.
_CLEAN = ("silenceremove=start_periods=1:start_duration=0:start_threshold=-50dB,"
          "areverse,silenceremove=start_periods=1:start_duration=0:start_threshold=-50dB,areverse,"
          "silenceremove=stop_periods=-1:stop_duration=0.6:stop_threshold=-45dB:stop_silence=0.3,"   # squeeze long dead gaps inside a clip

          "highpass=f=70,acompressor=threshold=0.125:ratio=2.5:attack=8:release=120:makeup=1.5,"
          f"apad=pad_dur={PAD_SECONDS}")


def _to_wav(src, dst, input_args=()):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *input_args, "-i", str(src), "-ar", "44100", "-ac", "1",
                    "-af", _CLEAN, str(dst)], check=True)


def analyze_speech(path):
    """Return (total_seconds, speech_seconds, longest_internal_silence) using ffmpeg silencedetect."""
    total = probe_duration(path)
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
                          "silencedetect=n=-45dB:d=0.3", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", out)]
    durs = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", out)]
    silent = sum(durs)
    # a trailing silence (our padding) is not an internal gap
    internal = [d for st, d in zip(starts, durs) if st + d < total - 0.25]
    return total, max(0.0, total - silent), max(internal, default=0.0)


class _Quota(RuntimeError):
    """Provider is rate-limited/out of quota for good — stop and fall back."""


class Narrator:
    def __init__(self, cfg, kind=None):
        self.cfg = cfg
        v = cfg["voice"]
        # providers_by_kind lets a channel spend its limited natural-voice quota on Shorts and use the free
        # Edge voice for long videos (a long video needs ~35 Gemini calls, a Short only ~7).
        order = (v.get("providers_by_kind") or {}).get(kind) or v.get("providers") or [v.get("provider", "edge")]
        usable = []
        for p in order:
            if p == "elevenlabs" and not (env("ELEVENLABS_API_KEY") and cfg.get("elevenlabs", {}).get("voice_id")):
                continue
            if p == "gemini" and not env("GEMINI_API_KEY"):
                continue
            if p not in usable:
                usable.append(p)
        if "edge" not in usable:
            usable.append("edge")          # always keep a free last resort
        self.providers = usable
        self.used = None
        self.voice = None                   # edge: sticky voice
        self._gem_model = None              # gemini: sticky working model + request shape
        self._gem_shape = None

    # -- edge ----------------------------------------------------------------
    def _edge(self, text, out_mp3):
        import edge_tts
        voices = [self.voice] if self.voice else self.cfg["voice"]["voices"]
        rate = self.cfg["voice"].get("rate", "+0%")
        last = None
        for voice in voices:
            for attempt in range(3):
                try:
                    asyncio.run(asyncio.wait_for(edge_tts.Communicate(text, voice, rate=rate).save(str(out_mp3)), 90))
                    if Path(out_mp3).exists() and Path(out_mp3).stat().st_size > 1000:
                        self.voice = voice
                        return
                except Exception as e:
                    last = e
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"edge-tts failed for every voice: {last}")

    # -- elevenlabs ----------------------------------------------------------
    def _elevenlabs(self, text, out_mp3, prev_text="", next_text=""):
        el = self.cfg.get("elevenlabs", {})
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{el['voice_id']}",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": env("ELEVENLABS_API_KEY")},
            json={"text": text, "model_id": el.get("model_id", "eleven_multilingual_v2"),
                  "previous_text": prev_text, "next_text": next_text,
                  "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.25}},
            timeout=120)
        r.raise_for_status()
        Path(out_mp3).write_bytes(r.content)

    # -- gemini --------------------------------------------------------------
    def _gem_models(self):
        g = self.cfg["voice"].get("gemini", {})
        return list(g.get("models") or DEFAULT_GEMINI_MODELS)

    def _gem_payload(self, shape, text, style, voice):
        if shape == "new":      # current API: style travels as metadata, text stays a verbatim transcript
            return {"contents": [{"role": "user", "parts": [{"text": text, "speech_metadata": {"style": style}}]}],
                    "generationConfig": {"responseModalities": ["AUDIO"],
                                         "speechConfig": {"voiceConfig": {"voice": voice}}}}
        # older preview models: style is a spoken instruction in front of the text
        return {"contents": [{"role": "user", "parts": [{"text": f"Say this in a style that is {style}: {text}"}]}],
                "generationConfig": {"responseModalities": ["AUDIO"],
                                     "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}

    @staticmethod
    def _gem_audio(data):
        """Return (bytes, ffmpeg input args) for the audio in a generateContent response, or None."""
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError):
            return None
        for part in parts:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                raw = base64.b64decode(inline["data"])
                if raw[:4] == b"RIFF":
                    return raw, ()
                mime = inline.get("mimeType") or inline.get("mime_type") or ""
                rate = re.search(r"rate=(\d+)", mime)
                return raw, ("-f", "s16le", "-ar", rate.group(1) if rate else "24000", "-ac", "1")
        return None

    def _gemini(self, text, out_wav, workdir):
        g = self.cfg["voice"].get("gemini", {})
        voice, style = g.get("voice", "Charon"), g.get("style", DEFAULT_STYLE)
        models = [self._gem_model] if self._gem_model else self._gem_models()
        shapes = [self._gem_shape] if self._gem_shape else ["new", "old"]
        min_seconds = max(0.4, len(text.split()) / 6.0)       # shorter than this = truncated output
        last, quota_hits = "no attempt", 0

        for model in models:
            for shape in shapes:
                for attempt in range(6):
                    try:
                        r = requests.post(f"{GEMINI_API}/models/{model}:generateContent",
                                          headers={"x-goog-api-key": env("GEMINI_API_KEY")},
                                          json=self._gem_payload(shape, text, style, voice), timeout=180)
                    except requests.RequestException as e:
                        last = f"{model}: {e}"
                        time.sleep(3 * (attempt + 1))
                        continue
                    if r.status_code == 429:
                        quota_hits += 1
                        wait = min(int(r.headers.get("Retry-After", 0) or 0) or 10 * (attempt + 1), 65)
                        last = f"{model}: rate limited (429)"
                        if quota_hits >= 6:
                            raise _Quota(last)
                        time.sleep(wait)
                        continue
                    if r.status_code in (500, 502, 503, 504):
                        last = f"{model}: HTTP {r.status_code}"
                        time.sleep(4 * (attempt + 1))
                        continue
                    if r.status_code in (400, 404):      # wrong request shape for this model, or no such model
                        last = f"{model}/{shape}: HTTP {r.status_code} {r.text[:140]}"
                        break
                    if r.status_code != 200:
                        last = f"{model}: HTTP {r.status_code} {r.text[:140]}"
                        break
                    audio = self._gem_audio(r.json())
                    if not audio:
                        last = f"{model}: response had no audio"
                        time.sleep(2)
                        continue
                    raw, in_args = audio
                    tmp = Path(workdir) / "gem_raw.bin"
                    tmp.write_bytes(raw)
                    _to_wav(tmp, out_wav, in_args)
                    total, speech, gap = analyze_speech(out_wav)
                    words = len(text.split())
                    if total < min_seconds:
                        last = f"{model}: audio too short (truncated)"
                        continue
                    if speech < max(0.4, words / 5.5) or gap > 1.2:      # mostly silent / dropout inside the clip
                        last = f"{model}: audio has a dropout (speech {speech:.1f}s of {total:.1f}s, gap {gap:.1f}s)"
                        log(f"⚠️ {last}; regenerating this scene")
                        continue
                    self._gem_model, self._gem_shape = model, shape
                    return
        raise RuntimeError(f"Gemini TTS failed: {last}")

    # -- public ----------------------------------------------------------------
    def _run(self, provider, scenes, workdir):
        results = []
        for i, scene in enumerate(scenes):
            text = normalize_for_speech(scene["narration"])
            mp3, wav = workdir / f"voice_{i:03d}.mp3", workdir / f"voice_{i:03d}.wav"
            if provider == "gemini":
                self._gemini(text, wav, workdir)
            elif provider == "elevenlabs":
                prev_t = scenes[i - 1]["narration"] if i else ""
                next_t = scenes[i + 1]["narration"] if i + 1 < len(scenes) else ""
                self._elevenlabs(text, mp3, prev_t, next_t)
                _to_wav(mp3, wav)
            else:
                self._edge(text, mp3)
                _to_wav(mp3, wav)
            results.append((wav, probe_duration(wav)))
        return results

    def synthesize(self, scenes, workdir):
        """Create voice_XXX.wav for each scene. Returns [(wav_path, duration_seconds)]."""
        workdir = Path(workdir)
        last_error = None
        for provider in self.providers:
            try:
                results = self._run(provider, scenes, workdir)
            except Exception as e:                       # includes _Quota
                last_error = e
                log(f"⚠️ Voice provider '{provider}' failed ({e}); trying the next one")
                from .notify import alert
                if self.cfg.get("alerts", {}).get("voice_fallback", False):
                  alert(self.cfg["name"], f"voice provider '{provider}' failed, falling back",
                        f"{e}\nVideos may sound more robotic until this is fixed (quota/billing or an outage).")
                for f in workdir.glob("voice_*"):
                    f.unlink(missing_ok=True)
                continue
            self.used = provider
            detail = {"edge": self.voice, "gemini": f"{self._gem_model}/{self.cfg['voice'].get('gemini', {}).get('voice')}"
                      }.get(provider, provider)
            log(f"🎙️ Voiced {len(scenes)} scenes ({sum(d for _, d in results):.0f}s) using {provider} [{detail}]")
            return results
        raise RuntimeError(f"Every voice provider failed. Last error: {last_error}")
