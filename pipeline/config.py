"""Config loading and tiny helpers shared by every module."""
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def log(*parts):
    print(*parts, flush=True)


def env(name, default=None):
    value = os.environ.get(name)
    return value if value else default


def load_channel(channel_id):
    path = ROOT / "channels" / f"{channel_id}.yaml"
    if not path.exists():
        raise SystemExit(f"No channel config found at {path}")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg.setdefault("id", channel_id)
    return cfg
