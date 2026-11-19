"""Self-renewing scheduler: GitHub's own cron is unreliable (it drops ticks for hours), so this job sleeps until
exact wall-clock times (India time), dispatches the video robot, and near its 6-hour job limit starts the next copy
of itself. Run with --dry-run to print the plan without sleeping or dispatching."""
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Kolkata")
# Morning batch needs to start early and may need retries; later ticks only top up anything that failed.
TARGETS = ["05:02", "05:32", "06:32", "08:02", "10:02", "13:02", "16:02", "19:02", "22:02"]
REPO = os.environ.get("GITHUB_REPOSITORY", "jibinsmiles/youtube")
LIFETIME = 5 * 3600 + 20 * 60          # hand over to a fresh copy before the 6 h job limit


def next_target(now):
    for day in (0, 1):
        for t in TARGETS:
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
        gh("daily_robot.yml", "-f", "channel=technology", "-f", "kind=auto", "-f", "mode=auto",
           "-f", "visibility=public", dry=dry)
        now = nxt + timedelta(seconds=1)
        if dry and nxt > start + timedelta(days=1):
            return
    if not dry:
        time.sleep(max(0, (deadline - datetime.now(timezone.utc)).total_seconds()))
    gh("scheduler.yml", dry=dry)


if __name__ == "__main__":
    main()
