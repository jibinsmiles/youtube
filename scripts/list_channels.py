"""Print the enabled channel ids as a JSON list (used by the workflow to build its matrix).

    python scripts/list_channels.py            # all enabled channels
    python scripts/list_channels.py science    # just that one (even if disabled, for manual tests)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.config import list_channels  # noqa: E402

want = sys.argv[1] if len(sys.argv) > 1 else "all"
ids = list_channels(enabled_only=True) if want in ("", "all") else [want]
if want not in ("", "all") and want not in list_channels(enabled_only=False):
    sys.exit(f"Unknown channel '{want}'")
print(json.dumps(ids))
