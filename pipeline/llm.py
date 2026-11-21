"""Minimal Gemini REST client with model fallback and automatic model discovery.

Model names change and old ones get shut down. If every configured model fails,
we ask the API which models actually exist and try those, so the pipeline does
not silently degrade to filler content.
"""
import json
import os
import re
import time

import requests

from .config import env, log

API = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODELS = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
_SKIP = ("image", "tts", "live", "audio", "embedding", "robotics", "computer",
         "omni", "veo", "imagen", "lyria", "native")


class LLMError(RuntimeError):
    pass


class Gemini:
    def __init__(self):
        # A channel can have its own key (its own free Google Cloud project = its own daily quota):
        # GEMINI_API_KEY_<CHANNEL>, e.g. GEMINI_API_KEY_CRIME. Otherwise the shared GEMINI_API_KEY is used.
        channel = (os.environ.get("CHANNEL") or "").upper()
        self.key = (env(f"GEMINI_API_KEY_{channel}") if channel else None) or env("GEMINI_API_KEY")
        if not self.key:
            raise LLMError("GEMINI_API_KEY is not set")
        override = env("GEMINI_MODELS")
        self.models = ([m.strip() for m in override.split(",") if m.strip()]
                       if override else list(DEFAULT_MODELS))
        self._discovered = False
        self._spent = set()          # models whose free daily quota is used up: skipped for the rest of this run

    # -- model discovery ---------------------------------------------------
    def _discover(self):
        self._discovered = True
        try:
            r = requests.get(f"{API}/models", params={"key": self.key, "pageSize": 200}, timeout=30)
            r.raise_for_status()
            names = []
            for m in r.json().get("models", []):
                name = m.get("name", "").split("/")[-1]
                if ("generateContent" in m.get("supportedGenerationMethods", [])
                        and "flash" in name and not any(s in name for s in _SKIP)):
                    names.append(name)

            def key(n):
                v = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
                return (-(float(v.group(1)) if v else 0), "lite" in n, "preview" in n or "exp" in n)

            found = [n for n in sorted(set(names), key=key) if n not in self.models]
            log(f"🔎 Discovered Gemini models: {found[:6]}")
            self.models = found[:6] or self.models
        except Exception as e:  # discovery is best-effort
            log(f"⚠️ Model discovery failed: {e}")

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _extract(data):
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError):
            return ""
        return "".join(p.get("text", "") for p in parts if not p.get("thought"))

    @staticmethod
    def _parse_json(text):
        text = text.strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("no JSON object in response")
        return json.loads(text[start:end + 1])

    # -- public API --------------------------------------------------------
    def generate(self, prompt, json_mode=True, temperature=0.7, max_tokens=8192, parts=None):
        """`parts` (optional) replaces the plain prompt with a list of {"text": ...} / {"inline_data": ...} parts."""
        payload = {
            "contents": [{"role": "user", "parts": parts or [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"

        last = "no models tried"
        for round_no in (0, 1):
            for model in list(self.models):
                if model in self._spent:
                    continue
                for retry in range(3):
                    try:
                        r = requests.post(f"{API}/models/{model}:generateContent",
                                          params={"key": self.key}, json=payload, timeout=240)
                    except requests.RequestException as e:
                        last = f"{model}: {e}"
                        time.sleep(3 * (retry + 1))
                        continue
                    if r.status_code == 200:
                        text = self._extract(r.json())
                        if not text:
                            last = f"{model}: empty response"
                            break
                        if json_mode:
                            try:
                                return self._parse_json(text)
                            except (ValueError, json.JSONDecodeError) as e:
                                last = f"{model}: bad JSON ({e})"
                                continue
                        return text
                    if r.status_code == 429 and "PerDay" in r.text:
                        # the free daily allowance of this model is gone: waiting will not help, use the next model
                        self._spent.add(model)
                        last = f"{model}: daily quota used up"
                        log(f"ℹ️ {model}: free daily quota used up; using the next model for the rest of this run")
                        break
                    if r.status_code in (429, 500, 502, 503, 504):
                        last = f"{model}: HTTP {r.status_code}"
                        time.sleep(6 * (retry + 1))
                        continue
                    last = f"{model}: HTTP {r.status_code} {r.text[:160]}"
                    break  # 400/403/404 -> try the next model
            if round_no == 0 and not self._discovered:
                self._discover()
                continue
            break
        raise LLMError(f"All Gemini models failed. Last error: {last}")

    def json(self, prompt, **kw):
        return self.generate(prompt, json_mode=True, **kw)
