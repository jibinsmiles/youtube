"""Connect YouTube channels to the robot without anyone handling a token (runs inside GitHub Actions).

    python scripts/connect_channels.py crime,health,finance,debate

For each channel it starts Google's "device sign-in": it emails you (ALERT_EMAIL_TO) one short code per channel and
also prints them in the run log. You open google.com/device, enter a code, pick the matching channel and click
Allow. The script then checks the approval really is for that channel (by name) and saves the refresh token straight
into the repo secret YOUTUBE_REFRESH_TOKEN_<CHANNEL> through the GitHub API (token GH_SECRETS_TOKEN). The refresh token
is never printed, logged or emailed. Codes expire after ~30 minutes; expired ones are replaced with new codes (emailed
again) for up to MAX_ROUNDS rounds.
"""
import base64
import os
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.config import load_channel  # noqa: E402
from pipeline.notify import send_email  # noqa: E402
from pipeline.uploader import oauth_client  # noqa: E402

SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube"
MAX_ROUNDS = 3
REPO = os.environ.get("GITHUB_REPOSITORY", "jibinsmiles/youtube")


def _norm(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def save_secret(name, value):
    """Create/update a repository Actions secret (value is encrypted with the repo's public key first)."""
    from nacl import encoding, public
    token = os.environ.get("GH_SECRETS_TOKEN")
    if not token:
        raise RuntimeError("GH_SECRETS_TOKEN is not set")
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    key = requests.get(f"https://api.github.com/repos/{REPO}/actions/secrets/public-key", headers=h, timeout=30)
    key.raise_for_status()
    k = key.json()
    sealed = public.SealedBox(public.PublicKey(k["key"].encode(), encoding.Base64Encoder())).encrypt(value.encode())
    r = requests.put(f"https://api.github.com/repos/{REPO}/actions/secrets/{name}", headers=h, timeout=30,
                     json={"encrypted_value": base64.b64encode(sealed).decode(), "key_id": k["key_id"]})
    if r.status_code not in (201, 204):
        raise RuntimeError(f"GitHub refused to save {name}: HTTP {r.status_code} {r.text[:200]}")


def channel_titles(client_id, secret, refresh):
    t = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "client_id": client_id, "client_secret": secret, "refresh_token": refresh, "grant_type": "refresh_token"})
    t.raise_for_status()
    r = requests.get("https://www.googleapis.com/youtube/v3/channels", timeout=30,
                     params={"part": "snippet", "mine": "true"},
                     headers={"Authorization": f"Bearer {t.json()['access_token']}"})
    r.raise_for_status()
    return [i["snippet"]["title"] for i in r.json().get("items", [])]


def start(ch):
    cid, _ = oauth_client(ch["id"])
    r = requests.post("https://oauth2.googleapis.com/device/code", data={"client_id": cid, "scope": SCOPES}, timeout=30)
    r.raise_for_status()
    d = r.json()
    ch.update(device=d["device_code"], code=d["user_code"], url=d.get("verification_url", "https://www.google.com/device"),
              interval=d.get("interval", 5), expires=time.time() + d.get("expires_in", 1800) - 30, next_poll=0)


def announce(pending, round_no):
    lines = [f"  {c['code']}   ->  pick the channel  \"{c['want']}\"" for c in pending]
    body = (f"Open {pending[0]['url']} (signed in as the Google account that owns the channels).\n"
            f"Enter each code below; when Google asks, choose the matching channel, then click Allow.\n\n"
            + "\n".join(lines) +
            "\n\nIf Google warns that the app isn't verified, click Advanced > Go to ... (it is your own app)."
            "\nCodes expire in about 30 minutes; you will get new ones automatically if they do.")
    print(f"\n===== Round {round_no}: enter these codes at {pending[0]['url']} =====\n{body}\n", flush=True)
    for c in pending:
        print(f"::notice title=Code for {c['want']}::{c['code']} at {c['url']}", flush=True)
    send_email(f"[Video robot] Connect {len(pending)} channel(s): codes inside", body)


def main():
    wanted = [x.strip() for x in (sys.argv[1] if len(sys.argv) > 1 else "").split(",") if x.strip()]
    if not wanted:
        sys.exit("usage: connect_channels.py crime,health,...")
    chans = []
    for cid in wanted:
        cfg = load_channel(cid)
        want = (cfg.get("publish") or {}).get("expected_channel") or cfg["name"]
        client_id, secret = oauth_client(cid)
        if not client_id:
            sys.exit(f"No YouTube OAuth client found for {cid} (YOUTUBE_CLIENT_ID / _SECRET secrets)")
        chans.append({"id": cid, "want": want, "client_id": client_id, "secret": secret, "done": False})

    for round_no in range(1, MAX_ROUNDS + 1):
        pending = [c for c in chans if not c["done"]]
        if not pending:
            break
        for c in pending:
            start(c)
        announce(pending, round_no)
        while any(not c["done"] and time.time() < c["expires"] for c in pending):
            for c in pending:
                if c["done"] or time.time() >= c["expires"] or time.time() < c["next_poll"]:
                    continue
                c["next_poll"] = time.time() + c["interval"]
                j = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
                    "client_id": c["client_id"], "client_secret": c["secret"], "device_code": c["device"],
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}).json()
                if j.get("error") == "slow_down":
                    c["interval"] += 5
                    continue
                if j.get("error") in ("authorization_pending", None) and "refresh_token" not in j:
                    continue
                if "refresh_token" not in j:
                    print(f"❌ {c['want']}: {j.get('error')} — will retry with a new code", flush=True)
                    c["expires"] = 0
                    continue
                titles = channel_titles(c["client_id"], c["secret"], j["refresh_token"])
                if _norm(c["want"]) not in [_norm(t) for t in titles]:
                    print(f"⚠️ Code {c['code']} was approved for {titles}, not \"{c['want']}\". Not saved — "
                          "a new code will follow; pick the right channel next time.", flush=True)
                    c["expires"] = 0
                    continue
                save_secret(f"YOUTUBE_REFRESH_TOKEN_{c['id'].upper()}", j["refresh_token"])
                c["done"] = True
                print(f"✅ {c['want']} connected — saved YOUTUBE_REFRESH_TOKEN_{c['id'].upper()}", flush=True)
            time.sleep(2)

    ok = [c["want"] for c in chans if c["done"]]
    bad = [c["want"] for c in chans if not c["done"]]
    summary = f"Connected: {', '.join(ok) or 'none'}\nNot connected: {', '.join(bad) or 'none'}"
    print(summary, flush=True)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        Path(path).write_text("### Connect channels\n\n" + summary.replace("\n", "\n\n") + "\n", encoding="utf-8")
    send_email(f"[Video robot] Channel connection: {len(ok)} connected, {len(bad)} not", summary +
               ("\n\nRun the 'Connect channels' workflow again for the ones not connected." if bad else ""))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
