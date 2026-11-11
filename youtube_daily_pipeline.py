import os
import sys
import textwrap
import feedparser
import requests
from gtts import gTTS
from google import genai
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

client = genai.Client(api_key=GEMINI_KEY)


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
    return combined_news


# ---------------------------------------------------------
# 3. Script Generation
# ---------------------------------------------------------
def generate_content(news_summary):
    print("🤖 Prompting Gemini to craft video script and metadata...", flush=True)
    prompt = f"""
You are the lead content creator for the YouTube channel 'Trend Pulse Daily'.
Based on these top trending technology headlines:
{news_summary}

Create a fast-paced, highly engaging 30-45 second YouTube Short narration script.

Output your response EXACTLY in this format with these exact labels:
TITLE: <Write a high-CTR, click-worthy title under 70 chars with hashtags like #Shorts #TechNews #TrendPulseDaily>
DESCRIPTION: <Write a concise description summarizing the story with relevant tech tags>
SCRIPT: <Write pure spoken narration text only. Do not include sound effect cues, scene descriptions, or speaker names. Keep it punchy and conversational.>
"""
    models_to_try = [
            "gemini-3.1-pro-preview",
            "models/gemini-3.1-pro-preview",
            "gemini-2.5-flash",
            "models/gemini-2.5-flash"
    ]

    text = None
    for m in models_to_try:
        try:
            print(f"🤖 Calling model: {m}...", flush=True)
            response = client.models.generate_content(
                model=m,
                contents=prompt,
            )
            if response and response.text:
                text = response.text
                print(f"🎉 Successfully generated content using: {m}", flush=True)
                break
        except Exception as err:
            print(f"⚠️ {m} failed: {err}", flush=True)

    if not text:
        raise RuntimeError("❌ Could not generate content with available Gemini models.")

    title = "Top Tech Trends Today #Shorts #TechNews"
    desc = "Daily technology briefing by Trend Pulse Daily."
    script = ""

    for line in text.splitlines():
        if line.startswith("TITLE:"):
            title = line.replace("TITLE:", "").strip()
        elif line.startswith("DESCRIPTION:"):
            desc = line.replace("DESCRIPTION:", "").strip()
        elif line.startswith("SCRIPT:"):
            script = text.split("SCRIPT:", 1)[1].strip()
            break

    if not script:
        script = text.strip()

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
        news_summary = get_trending_tech_news()
        title, desc, script = generate_content(news_summary)
        audio_file = create_voiceover(script)
        video_file = build_short_video(audio_file, script)
        upload_to_youtube(video_file, title, desc)
        print("🏁 Workflow completed successfully!", flush=True)
    except Exception as err:
        print(f"❌ Pipeline failed: {err}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
