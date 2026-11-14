"""Get a YouTube refresh token for one channel (run once per channel, on your own computer).

    pip install requests
    python scripts/get_refresh_token.py <CLIENT_ID> <CLIENT_SECRET>

It prints a short code and a link. Open the link, sign in with the Google account that OWNS the channel
(pick the right channel/brand account if asked), enter the code, and approve. The script then prints the
refresh token to store as the secret YOUTUBE_REFRESH_TOKEN_<CHANNEL> (e.g. YOUTUBE_REFRESH_TOKEN_SCIENCE).
The OAuth client must be of type "TVs and Limited Input devices" with YouTube Data API v3 enabled, and the
consent screen set to "In production" (otherwise tokens expire after 7 days).
"""
import sys
import time

import requests

SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube"


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    client_id, client_secret = sys.argv[1:3]
    r = requests.post("https://oauth2.googleapis.com/device/code", data={"client_id": client_id, "scope": SCOPES})
    r.raise_for_status()
    d = r.json()
    print(f"\n1) Open: {d['verification_url']}\n2) Enter code: {d['user_code']}\n   (waiting for you to approve...)\n")
    deadline = time.time() + d.get("expires_in", 1800)
    interval = d.get("interval", 5)
    while time.time() < deadline:
        time.sleep(interval)
        t = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": client_id, "client_secret": client_secret, "device_code": d["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        j = t.json()
        if "refresh_token" in j:
            print("Refresh token (keep it secret):\n\n" + j["refresh_token"] + "\n")
            return
        if j.get("error") == "slow_down":
            interval += 5
        elif j.get("error") not in ("authorization_pending", None):
            sys.exit(f"Failed: {j}")
    sys.exit("Timed out waiting for approval.")


if __name__ == "__main__":
    main()
