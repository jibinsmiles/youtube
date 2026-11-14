"""YouTube Data API upload (headless, using a stored refresh token)."""
import re
import time
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from .config import env, log


def build_service(channel_id):
    client_id = env(f"YOUTUBE_CLIENT_ID_{channel_id.upper()}") or env("YOUTUBE_CLIENT_ID")
    secret = env(f"YOUTUBE_CLIENT_SECRET_{channel_id.upper()}") or env("YOUTUBE_CLIENT_SECRET")
    refresh = env(f"YOUTUBE_REFRESH_TOKEN_{channel_id.upper()}") or env("YOUTUBE_REFRESH_TOKEN")
    if not (client_id and secret and refresh):
        raise RuntimeError("Missing YouTube credentials (YOUTUBE_CLIENT_ID / _SECRET / _REFRESH_TOKEN)")
    creds = Credentials(None, refresh_token=refresh, token_uri="https://oauth2.googleapis.com/token",
                        client_id=client_id, client_secret=secret)
    try:
        creds.refresh(Request())
    except Exception as e:
        raise RuntimeError(
            "Could not refresh the YouTube token. If your Google OAuth app is in 'Testing' mode, refresh "
            f"tokens expire after 7 days — publish the app to 'In production' and re-authorise. ({e})")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _clean(text, limit):
    return re.sub(r"[<>]", "", text).strip()[:limit]


def build_description(cfg, kind, script, sources):
    pub = cfg["publish"]
    parts = [script.get("description", "")]
    if sources:
        parts.append("Sources:\n" + "\n".join(f"- {s['source']}: {s['url']}" for s in sources))
    if pub.get("disclosure"):
        parts.append(pub["disclosure"])
    tags = list(pub.get("hashtags", []))
    if kind == "short":
        tags.insert(0, "#Shorts")
    parts.append(" ".join(tags))
    return _clean("\n\n".join(p for p in parts if p), 4900)


def upload_video(service, cfg, kind, video_path, script, sources, thumbnail=None):
    """Returns (video_id, privacy, publish_at_iso_or_None)."""
    pub = cfg["publish"]
    mode = pub.get("mode", "review")
    privacy, publish_at = "private", None
    if mode == "auto":
        privacy = "public"
    elif mode == "veto":
        publish_at = (datetime.now(timezone.utc) + timedelta(hours=pub.get("veto_hours", 3))
                      ).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    tags, total = [], 0
    for t in script.get("tags", []):
        t = _clean(str(t), 40)
        if t and total + len(t) < 450:
            tags.append(t)
            total += len(t) + 1

    body = {"snippet": {"title": _clean(script["title"], 100),
                        "description": build_description(cfg, kind, script, sources),
                        "tags": tags, "categoryId": str(pub.get("category_id", "28")),
                        "defaultLanguage": pub.get("default_language", "en"),
                        "defaultAudioLanguage": pub.get("default_language", "en")},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    if publish_at:
        body["status"]["publishAt"] = publish_at

    media = MediaFileUpload(str(video_path), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)
    response, retries = None, 0
    log(f"📤 Uploading '{body['snippet']['title']}' as {privacy}{' (auto-publish ' + publish_at + ')' if publish_at else ''}")
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                log(f"   {int(status.progress() * 100)}%")
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and retries < 5:
                retries += 1
                time.sleep(2 ** retries)
                continue
            if "quotaExceeded" in str(e):
                from .notify import alert
                alert(cfg["name"], "YouTube upload quota exceeded", "Uploads default to about 100/day per Google Cloud project.")
                raise RuntimeError("YouTube API daily quota exceeded (uploads default to about 100 per day per project)")
            raise
    video_id = response["id"]
    actual = (response.get("status") or {}).get("privacyStatus", privacy)
    if actual != privacy:
        log(f"⚠️ Requested '{privacy}' but YouTube set the video to '{actual}'. If it stays private, the Google "
            "Cloud project has not passed YouTube's API compliance audit (uploads from unaudited projects are "
            "locked private) — apply for the audit at support.google.com/youtube/contact/yt_api_form")
        from .notify import alert
        alert(cfg["name"], f"video was set to '{actual}', not '{privacy}'",
              f"YouTube changed the privacy of https://studio.youtube.com/video/{video_id}/edit. Unaudited API "
              "projects can be forced private; apply for the YouTube API compliance audit.")
        privacy = actual

    if thumbnail and kind == "long":
        try:
            service.thumbnails().set(videoId=video_id,
                                     media_body=MediaFileUpload(str(thumbnail), mimetype="image/jpeg")).execute()
        except Exception as e:
            log(f"⚠️ Thumbnail not set (channel may need phone verification): {e}")
    return video_id, privacy, publish_at
