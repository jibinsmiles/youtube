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
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
CLIENT_ID = os.environ.get("YOUTUBE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET")
REFRESH_TOKEN = os.environ.get("YOUTUBE_REFRESH_TOKEN")

if not all([GEMINI_KEY, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
    print("❌ Error: Missing required environment variables in GitHub Secrets.")
    sys.exit(1)

client = genai.Client(api_key=GEMINI_KEY)

# ---------------------------------------------------------
# 2. Fetch Trending Headlines
# ---------------------------------------------------------
def get_trending_tech_news():
    print("🔍 Fetching trending technology headlines from Google News RSS...")
    rss_url = "https://news.google.com/rss/search?q=technology+news&hl=en-IN&gl=IN&ceid=IN:en"
    feed = feedparser.parse(rss_url)
    
    if not feed.entries:
        raise ValueError("Could not retrieve any news entries from RSS feed.")
    
    top_entries = feed.entries[:3]
    headlines = [f"- {entry.title}" for entry in top_entries]
    combined_news = "\n".join(headlines)
    print(f"✅ Retrieved headlines:\n{combined_news}")
    return combined_news

# ---------------------------------------------------------
# 3. Dynamic Model Resolver & Script Generation
# ---------------------------------------------------------
def get_working_model():
    # Priority order for standard models
    candidates = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash-latest"]
    try:
        available_models = [m.name for m in client.models.list()]
        print(f"📋 Available models for this key: {available_models[:4]}...")
        for candidate in candidates:
            for m in available_models:
                if candidate in m:
                    return m
        # If no specific match, use the first model supporting content generation
        if available_models:
            return available_models[0]
    except Exception as e:
        print(f"⚠️ Could not list models: {e}. Falling back to default.")
    return "gemini-2.0-flash"

def generate_content(news_summary):
    selected_model = get_working_model()
    print(f"🤖 Generating content with model: {selected_model}...")
    
    prompt = f"""
You are the lead content creator for the YouTube channel 'Trend Pulse Daily'.
Based on these top trending technology headlines:
{news_summary}

Create a fast-paced, highly engaging 45-60 second YouTube Short narration script.

Output your response EXACTLY in this format with these exact labels:
TITLE: <Write a high-CTR, click-worthy title under 70 chars with hashtags like #Shorts #TechNews #TrendPulseDaily>
DESCRIPTION: <Write a concise description summarizing the story with relevant tech tags>
SCRIPT: <Write pure spoken narration text only. Do not include sound effect cues, scene descriptions, or speaker names. Keep it punchy and conversational.>
"""
    response = client.models.generate_content(
        model=selected_model,
        contents=prompt
    )
    text = response.text

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

    print(f"✅ Generated Title: {title}")
    return title, desc, script

# ---------------------------------------------------------
# 4. Generate Voice Narration (TTS)
# ---------------------------------------------------------
def create_voiceover(script_text, audio_path="voice.mp3"):
    print("🎙️ Generating voiceover narration with gTTS...")
    tts = gTTS(text=script_text, lang="en", tld="com", slow=False)
    tts.save(audio_path)
    print(f"✅ Audio saved to {audio_path}")
    return audio_path

# ---------------------------------------------------------
# 5. Build Video with MoviePy
# ---------------------------------------------------------
def build_short_video(audio_path, script_text, output_path="final_video.mp4"):
    print("🎬 Rendering video with MoviePy...")
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
            method="caption"
        ).set_position(("center", "center")).set_duration(duration)

        header_clip = TextClip(
            "TREND PULSE DAILY",
            fontsize=52,
            color="#00E5FF",
            font="Liberation-Sans-Bold"
        ).set_position(("center", 180)).set_duration(duration)

        final_clip = CompositeVideoClip([bg_clip, header_clip, txt_clip]).set_audio(audio)
    except Exception as e:
        print(f"⚠️ TextClip fallback: {e}")
        final_clip = bg_clip.set_audio(audio)

    final_clip.write_videofile(
        output_path,
        fps=24,
        codec="libx264",
        audio_codec="aac",
        threads=4,
        preset="ultrafast"
    )
    print(f"✅ Video rendered successfully at {output_path}")
    return output_path

# ---------------------------------------------------------
# 6. YouTube API Headless Authentication & Upload
# ---------------------------------------------------------
def upload_to_youtube(video_path, title, description):
    print("🚀 Authenticating with YouTube Data API v3 (Headless)...")
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
            "categoryId": "28"
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    print(f"📤 Uploading '{title}' to @TrendPulseDailyIN...")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Uploaded {int(status.progress() * 100)}%")

    print(f"🎉 SUCCESS! Video published with ID: https://youtu.be/{response.get('id')}")

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
        print("🏁 Workflow completed successfully!")
    except Exception as err:
        print(f"❌ Pipeline failed: {err}")
        sys.exit(1)

if __name__ == "__main__":
    main()
