"""Text-to-speech, one clip per scene so audio and visuals stay perfectly in sync."""
import asyncio
import re
import subprocess
import time
from pathlib import Path

import requests

from .config import env, log

PAD_SECONDS = 0.18   # breathing room added after every scene


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


def _to_wav(src, dst):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ar", "44100", "-ac", "1",
                    "-af", f"apad=pad_dur={PAD_SECONDS}", str(dst)], check=True)


class Narrator:
    def __init__(self, cfg):
        self.cfg = cfg
        self.provider = cfg["voice"].get("provider", "edge")
        if self.provider == "elevenlabs" and not env("ELEVENLABS_API_KEY"):
            log("⚠️ ELEVENLABS_API_KEY missing — falling back to edge-tts")
            self.provider = "edge"
        self.voice = None   # sticky: once a voice works we keep it for the whole video

    # -- providers ---------------------------------------------------------
    def _edge(self, text, out_mp3):
        import edge_tts
        voices = [self.voice] if self.voice else self.cfg["voice"]["voices"]
        rate = self.cfg["voice"].get("rate", "+0%")
        last = None
        for voice in voices:
            for attempt in range(3):
                try:
                    asyncio.run(edge_tts.Communicate(text, voice, rate=rate).save(str(out_mp3)))
                    if Path(out_mp3).exists() and Path(out_mp3).stat().st_size > 1000:
                        self.voice = voice
                        return
                except Exception as e:
                    last = e
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"edge-tts failed for every voice: {last}")

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

    # -- public ------------------------------------------------------------
    def synthesize(self, scenes, workdir):
        """Create scene_XXX.wav for each scene. Returns [(wav_path, duration_seconds)]."""
        workdir = Path(workdir)
        results = []
        for i, scene in enumerate(scenes):
            text = normalize_for_speech(scene["narration"])
            mp3, wav = workdir / f"voice_{i:03d}.mp3", workdir / f"voice_{i:03d}.wav"
            if self.provider == "elevenlabs":
                prev_t = scenes[i - 1]["narration"] if i else ""
                next_t = scenes[i + 1]["narration"] if i + 1 < len(scenes) else ""
                self._elevenlabs(text, mp3, prev_t, next_t)
            else:
                self._edge(text, mp3)
            _to_wav(mp3, wav)
            results.append((wav, probe_duration(wav)))
        log(f"🎙️ Voiced {len(scenes)} scenes ({sum(d for _, d in results):.0f}s) using "
            f"{self.voice or self.provider}")
        return results
