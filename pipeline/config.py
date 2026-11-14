"""Config loading and tiny helpers shared by every module."""
import json
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def log(*parts):
    print(*parts, flush=True)


_secrets = None


def env(name, default=None):
    """Environment variable, falling back to the SECRETS_JSON blob that the workflow passes in. That is how
    per-channel secrets such as YOUTUBE_REFRESH_TOKEN_SCIENCE reach the pipeline without being listed by hand."""
    global _secrets
    value = os.environ.get(name)
    if value:
        return value
    if _secrets is None:
        try:
            _secrets = json.loads(os.environ.get("SECRETS_JSON") or "{}")
        except ValueError:
            _secrets = {}
    return _secrets.get(name) or default


def load_channel(channel_id):
    path = ROOT / "channels" / f"{channel_id}.yaml"
    if not path.exists():
        raise SystemExit(f"No channel config found at {path}")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if cfg.get("extends"):                      # a channel can inherit everything from another and override parts
        cfg = _merge(load_channel(cfg["extends"]), cfg)
        cfg.pop("extends", None)
    cfg["id"] = channel_id
    return cfg


def _merge(base, over):
    """Deep-merge dicts; lists and scalars in `over` replace those in `base`."""
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def list_channels(enabled_only=True):
    ids = []
    for f in sorted((ROOT / "channels").glob("*.yaml")):
        cfg = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if cfg.get("enabled", True) or not enabled_only:
            ids.append(f.stem)
    return ids
