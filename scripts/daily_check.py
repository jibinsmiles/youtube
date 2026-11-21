"""Evening check (dispatched by the scheduler at 21:32 IST): did every enabled channel make its videos today?

Emails ALERT_EMAIL_TO only when something is short, so silence means everything was made and scheduled.

    python scripts/daily_check.py            # all enabled channels
    python scripts/daily_check.py --always   # also email when everything is fine (handy as a test)
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.config import list_channels, load_channel  # noqa: E402
from pipeline.notify import send_email  # noqa: E402
from pipeline.state import State  # noqa: E402


def main():
    problems, report = [], []
    for cid in list_channels(enabled_only=True):
        cfg = load_channel(cid)
        sch, tz = cfg["schedule"], ZoneInfo(cfg["timezone"])
        st = State.load(cid)
        c = st.counts_today(cid, tz)
        want_s, want_l = sch["shorts_per_day"], sch["long_per_day"]
        line = f"{cfg['name']}: {c['short']}/{want_s} Shorts, {c['long']}/{want_l} long videos made today"
        report.append(line)
        if c["short"] < want_s or c["long"] < want_l:
            problems.append(line)
        last = st.minutes_since_last_upload(cid)
        if last is None or last > 26 * 60:
            problems.append(f"{cfg['name']}: no upload for {'ever' if last is None else f'{last / 60:.0f} hours'}")
        blocked = st.data.get("upload_blocked_until")
        if blocked and datetime.fromisoformat(blocked) > datetime.now(timezone.utc) - timedelta(hours=12):
            problems.append(f"{cfg['name']}: YouTube's daily upload limit was hit (paused until {blocked})")
    text = "\n".join(report)
    print(text)
    if problems:
        print("\nPROBLEMS:\n" + "\n".join(problems))
        send_email(f"[Video robot] {len(problems)} channel problem(s) today",
                   "These channels fell short today:\n\n" + "\n".join(f"- {p}" for p in problems) +
                   "\n\nFull picture:\n" + text +
                   "\n\nThe run logs (Actions > Daily video robot) say why: usually no topic passed the fact-check, "
                   "the Gemini free quota ran out, or a token stopped working.")
    elif "--always" in sys.argv:
        send_email("[Video robot] All channels on target today", text)


if __name__ == "__main__":
    main()
