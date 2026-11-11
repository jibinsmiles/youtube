import os
import sys
import json
import textwrap
import feedparser
import requests
from gtts import gTTS
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from moviepy.editor import ColorClip, TextClip, CompositeVideoClip, AudioFileClip

# ---------------------------------------------------------
# 1. Environment & Configuration
# ---------------------------------------------------------
print("⚡ Starting Trend Pulse Daily automated pipeline...", flush=True)

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
CLIENT_ID = os.environ.get("YOUTUBE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET")
REFRESH_TOKEN = os.environ.get("YOUTUBE_REFRESH_TOKEN")

if not all([GEMINI_KEY, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
    print("❌ Error: Missing required environment variables in GitHub Secrets.", flush=True)
    sys.exit(1)


# ---------------------------------------------------------
# 2. Fetch Trending Headlines
# ---------------------------------------------------------
def get_trending_tech_news():
    print("🔍 Fetching trending technology headlines from Google News RSS...", flush=True)
    rss_url = "https://news.google.com/rss/search?q=technology+news&hl=en-IN&gl=IN&ceid=IN:en"
    feed = feedparser.parse(rss_url)

    if not feed.entries:
        raise ValueError("Could not retrieve any news entries from RSS feed.")

    top_entries = feed.entries[:3]
    headlines = [f"- {entry.title}" for entry in top_entries]
    combined_news = "\n".join(headlines)
    print(f"✅ Retrieved headlines:\n{combined_news}", flush=True)
    return combined_news, top_entries[0].title


# ---------------------------------------------------------
# 3. Direct REST Call to Google AI Studio with Safe Fallback
# ---------------------------------------------------------
def generate_content(news_summary, top_headline):
    print("🤖 Prompting Gemini to craft video script and metadata...", flush=True)
    prompt_text = f"""
You are the lead content creator for the YouTube channel 'Trend Pulse Daily'.
Based on these top trending technology headlines:
{news_summary}

Create a fast-paced, highly engaging 30-40 second YouTube Short narration script.

Output your response EXACTLY in this format with these exact labels:
TITLE: <Write a high-CTR, click-worthy title under 70 chars with hashtags like #Shorts #TechNews #TrendPulseDaily>
DESCRIPTION: <Write a concise description summarizing the story with relevant tech tags>
SCRIPT: <Write pure spoken narration text only. Do not include sound effect cues, scene descriptions, or speaker names. Keep it punchy and conversational.>
"""

    models_to_try = [
        "gemini-2.0-flash",
        "gemini-2.5-flash",
        "gemini-3.8-flash",
        "gemini-1.5-flash-latest"
    ]

    generated_text = None
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [
            {
                "parts": [{"text": prompt_text}]
            }
        ]
    }

    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_KEY}"
        try:
            print(f"🤖 Attempting API call with {model_name}...", flush=True)
            res = requests.post(url, headers=headers, json=payload, timeout=20)
            data = res.json()
            if res.status_code == 200 and "candidates" in data:
                candidate = data["candidates"][0]
                generated_text = candidate["content"]["parts"][0]["text"]
                print(f"🎉 Successfully generated content using {model_name}!", flush=True)
                break
            else:
                err_msg = data.get("error", {}).get("message", res.text)
                print(f"⚠️ {model_name} responded with status {res.status_code}: {err_msg[:120]}...", flush=True)
        except Exception as err:
            print(f"⚠️ Request to {model_name} failed: {err}", flush=True)

    # Fallback script generation if AI quota is completely exhausted
    if not generated_text:
        print("⚡ Free AI quota limit reached. Using dynamic fallback template so upload succeeds...", flush=True)
        title = f"{top_headline[:50]} #Shorts #TechNews #TrendPulseDaily"
        desc = f"Latest technology news briefing:\n{news_summary}\n\nSubscribe to Trend Pulse Daily for daily updates!"
        script = f"Welcome back to Trend Pulse Daily! Here are today's top tech stories. First up: {top_headline}. Across the tech space, major developments are unfolding rapidly with significant industry impact. Follow and subscribe for your daily dose of technology updates!"
        return title, desc, script

    title = "Top Tech Trends Today #Shorts #TechNews"
    desc = "Daily technology briefing by Trend Pulse Daily."
    script = ""

    for line in generated_text.splitlines():
        if line.startswith("TITLE:"):
            title = line.replace("TITLE:", "").strip()
        elif line.startswith("DESCRIPTION:"):
            desc = line.replace("DESCRIPTION:", "").strip()
        elif line.startswith("SCRIPT:"):
            script = generated_text.split("SCRIPT:", 1)[1].strip()
            break

    if not script:
        script = generated_text.strip()

    print(f"✅ Generated Title: {title}", flush=True)
    return title, desc, script


# ---------------------------------------------------------
# 4. Generate Voice Narration (TTS)
# ---------------------------------------------------------
def create_voiceover(script_text, audio_path="voice.mp3"):
    print("🎙️ Generating voiceover narration with gTTS...", flush=True)
    tts = gTTS(text=script_text, lang="en", tld="com", slow=False)
    tts.save(audio_path)
    print(f"✅ Audio saved to {audio_path}", flush=True)
    return audio_path


# ---------------------------------------------------------
# 5. Build Video with MoviePy
# ---------------------------------------------------------
def build_short_video(audio_path, script_text, output_path="final_video.mp4"):
    print("🎬 Rendering video with MoviePy...", flush=True)
    audio = AudioFileClip(audio_path)
    duration = audio.duration

    bg_clip = ColorClip(size=(1080, 1920), color=(15, 18, 26), duration=duration)
    wrapped_text = "\n\n".join(textwrap.wrap(script_text, width=32))

    try:
        txt_clip = TextClip(
            wrapped_text,
            fontsize=46,
            color="white",
            font="Liberation-Sans-Bold",
            size=(920, 1500),
            method="caption",
        ).set_position(("center", "center")).set_duration(duration)

        header_clip = TextClip(
            "TREND PULSE DAILY",
            fontsize=52,
            color="#00E5FF",
            font="Liberation-Sans-Bold",
        ).set_position(("center", 180)).set_duration(duration)

        final_clip = CompositeVideoClip([bg_clip, header_clip, txt_clip]).set_audio(audio)
    except Exception as e:
        print(f"⚠️ TextClip fallback: {e}", flush=True)
        final_clip = bg_clip.set_audio(audio)

    final_clip.write_videofile(
        output_path,
        fps=24,
        codec="libx264",
        audio_codec="aac",
        threads=2,
        preset="ultrafast",
    )
    print(f"✅ Video rendered successfully at {output_path}", flush=True)
    return output_path


# ---------------------------------------------------------
# 6. YouTube Headless Direct Upload
# ---------------------------------------------------------
def upload_to_youtube(video_path, title, description):
    print("🚀 Authenticating with YouTube Data API v3 (Headless)...", flush=True)
    creds = Credentials(
        None,
        refresh_token=REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
    )
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": ["Technology", "Tech News", "Trend Pulse Daily", "Shorts", "AI"],
            "categoryId": "28",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    print(f"📤 Uploading '{title}' to @TrendPulseDailyIN...", flush=True)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Uploaded {int(status.progress() * 100)}%", flush=True)

    video_id = response.get("id")
    print(f"🎉 SUCCESS! Video published with ID: https://youtu.be/{video_id}", flush=True)


# ---------------------------------------------------------
# Main Execution Pipeline
# ---------------------------------------------------------
def main():
    try:
        news_summary, top_headline = get_trending_tech_news()
        title, desc, script = generate_content(news_summary, top_headline)
        audio_file = create_voiceover(script)
        video_file = build_short_video(audio_file, script)
        upload_to_youtube(video_file, title, desc)
        print("🏁 Workflow completed successfully!", flush=True)
    except Exception as err:
        print(f"❌ Pipeline failed: {err}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
