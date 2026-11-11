import os
import sys
import json
import textwrap
import feedparser
import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from gtts import gTTS
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from moviepy.editor import ImageClip, AudioFileClip

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
        "gemini-1.5-flash-latest"
    ]

    generated_text = None
    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": prompt_text}]}]}

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
                print(f"⚠️ {model_name} error: {err_msg[:100]}...", flush=True)
        except Exception as err:
            print(f"⚠️ Request to {model_name} failed: {err}", flush=True)

    if not generated_text:
        print("⚡ Free AI quota limit reached. Using dynamic fallback template...", flush=True)
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
# 4. Voice Narration (TTS)
# ---------------------------------------------------------
def create_voiceover(script_text, audio_path="voice.mp3"):
    print("🎙️ Generating voiceover narration with gTTS...", flush=True)
    tts = gTTS(text=script_text, lang="en", tld="com", slow=False)
    tts.save(audio_path)
    print(f"✅ Audio saved to {audio_path}", flush=True)
    return audio_path


# ---------------------------------------------------------
# 5. Visual Frame Generation with Pillow (No ImageMagick Needed)
# ---------------------------------------------------------
def create_card_image(headline, script_text, output_image_path="card.png"):
    width, height = 1080, 1920
    # Gradient/Dark tech background
    img = Image.new("RGB", (width, height), color=(15, 23, 42))
    draw = ImageDraw.Draw(img)

    # Load system font or fall back to default
    try:
        font_header = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 60)
        font_sub = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 44)
        font_body = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 38)
    except Exception:
        font_header = font_sub = font_body = ImageFont.load_default()

    # Brand badge banner
    draw.rounded_rectangle([(80, 140), (1000, 260)], radius=20, fill=(30, 41, 59))
    draw.text((width // 2, 200), "TREND PULSE DAILY", fill=(0, 229, 255), font=font_header, anchor="mm")

    # Category chip
    draw.rounded_rectangle([(80, 310), (360, 370)], radius=15, fill=(239, 68, 68))
    draw.text((220, 340), "BREAKING TECH", fill="white", font=font_sub, anchor="mm")

    # Headline block
    wrapped_headline = textwrap.fill(headline, width=28)
    draw.text((80, 420), wrapped_headline, fill=(255, 255, 255), font=font_header, spacing=20)

    # Narrative preview card
    draw.rounded_rectangle([(80, 850), (1000, 1600)], radius=25, fill=(30, 41, 59))
    wrapped_body = textwrap.fill(script_text[:350] + ("..." if len(script_text) > 350 else ""), width=36)
    draw.text((120, 920), wrapped_body, fill=(203, 213, 225), font=font_body, spacing=18)

    # Footer call to action
    draw.text((width // 2, 1750), "SUBSCRIBE FOR DAILY UPDATES", fill=(148, 163, 184), font=font_sub, anchor="mm")

    img.save(output_image_path)
    print(f"✅ Generated visual frame card at {output_image_path}", flush=True)
    return output_image_path


def build_short_video(audio_path, headline, script_text, output_path="final_video.mp4"):
    print("🎬 Rendering video with MoviePy and Pillow...", flush=True)
    audio = AudioFileClip(audio_path)
    duration = audio.duration

    card_image_path = create_card_image(headline, script_text)
    video_clip = ImageClip(card_image_path).set_duration(duration).set_audio(audio)

    video_clip.write_videofile(
        output_path,
        fps=24,
        codec="libx264",
        audio_codec="aac",
        threads=2,
        preset="ultrafast"
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
    youtube
