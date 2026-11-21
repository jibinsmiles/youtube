"""Self-renewing scheduler: GitHub's own cron is unreliable (it drops ticks for hours), so this job sleeps until
exact wall-clock times (India time), dispatches the video robot, and near its 6-hour job limit starts the next copy
of itself. Run with --dry-run to print the plan without sleeping or dispatching."""
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Kolkata")
# Morning batch needs to start early and may need retries; later ticks only top up anything that failed.
TARGETS = ["05:02", "05:32", "06:32", "08:02", "10:02", "13:02", "16:02", "19:02", "22:02"]
CHECK_AT = "21:32"                      # daily email check: did every channel make its videos today?
REPO = os.environ.get("GITHUB_REPOSITORY", "jibinsmiles/youtube")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGGER = 45                            # seconds between channel dispatches (spreads load on shared footage API)
LIFETIME = 5 * 3600 + 20 * 60          # hand over to a fresh copy before the 6 h job limit


def other_channels(dry):
    """Enabled channels besides technology, read fresh from the repo each time (enabling one needs no restart)."""
    if not dry:
        subprocess.run(["git", "-C", ROOT, "pull", "-q", "--ff-only"], capture_output=True)
    out = []
    folder = os.path.join(ROOT, "channels")
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".yaml") or name == "technology.yaml":
            continue
        with open(os.path.join(folder, name), encoding="utf-8") as fh:
            m = re.search(r"^enabled:\s*(\w+)", fh.read(), re.M)
        if m is None or m.group(1).lower() == "true":
            out.append(name[:-5])
    return out


def next_target(now):
    for day in (0, 1):
        for t in sorted(TARGETS + [CHECK_AT]):
            h, m = (int(x) for x in t.split(":"))
            cand = (now + timedelta(days=day)).astimezone(TZ).replace(hour=h, minute=m, second=0, microsecond=0)
            if cand > now:
                return cand
    raise RuntimeError("no target")


def gh(*args, dry):
    cmd = ["gh", "workflow", "run", *args, "--repo", REPO, "--ref", "main"]
    print("▶", " ".join(cmd), flush=True)
    if dry:
        return
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout.strip(), r.stderr.strip(), flush=True)


def main():
    dry = "--dry-run" in sys.argv
    start = datetime.now(timezone.utc)
    deadline = start + timedelta(seconds=LIFETIME)
    now = start
    while True:
        nxt = next_target(now)
        print(f"⏰ next dispatch {nxt:%a %d %b %H:%M} IST", flush=True)
        if nxt > deadline:
            break
        if not dry:
            time.sleep(max(0, (nxt - datetime.now(timezone.utc)).total_seconds()))
        if nxt.strftime("%H:%M") == CHECK_AT:
            gh("daily_check.yml", dry=dry)
        else:
            # Tech Tadka exactly as before, then every other enabled channel a little later.
            gh("daily_robot.yml", "-f", "channel=technology", "-f", "kind=auto", "-f", "mode=auto",
               "-f", "visibility=public", dry=dry)
            for ch in other_channels(dry):
                if not dry:
                    time.sleep(STAGGER)
                gh("daily_robot.yml", "-f", f"channel={ch}", "-f", "kind=auto", "-f", "mode=auto",
                   "-f", "visibility=public", dry=dry)
        now = nxt + timedelta(seconds=1)
        if dry and nxt > start + timedelta(days=1):
            return
    if not dry:
        time.sleep(max(0, (deadline - datetime.now(timezone.utc)).total_seconds()))
    gh("scheduler.yml", dry=dry)


if __name__ == "__main__":
    main()
