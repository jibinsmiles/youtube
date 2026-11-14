# Trend Pulse Daily — automated tech channel (India angle)

Finds fresh tech stories, researches the actual articles, writes a fact-checked script,
voices it, builds a multi-scene video with captions and music, and uploads it to YouTube.
Targets per day: **3 Shorts + 1 long video** (a 2nd long only for an exceptional story).

**Design principle: skip rather than publish filler.** If no story is strong enough, there isn't enough
source text, the script fails the quality checks, or the fact-check finds unsupported claims,
the run publishes nothing.

## How a run works
1. Pull stories from the feeds in `channels/technology.yaml` (last 36 h), measure how many outlets cover each.
2. Gemini ranks them (newsworthiness, India relevance, room for original analysis). Rumours/listicles score low.
3. Fetch the full article text (long videos also pull 2-3 related articles).
4. Write the script from the sources only → reject if too short/long, filler phrases, greeting opener.
5. Fact-check pass; unsupported claims are repaired once, otherwise the story is dropped.
6. Voice each scene → stock footage per scene (Pexels) with motion → word-highlight captions (Shorts) →
   ducked background music → loudness-normalised audio → thumbnail (long videos).
7. Upload according to `publish.mode`, save state, optional Telegram ping.

## Setup
Replace your old `youtube_daily_pipeline.py` and `.github/workflows/daily_robot.yml` with these files.

**GitHub → Settings → Secrets and variables → Actions:**

| Secret | Needed | Notes |
|---|---|---|
| `GEMINI_API_KEY` | yes | you already have this |
| `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` | yes | you already have these |
| `PIXABAY_API_KEY` | strongly recommended | free: sign up at pixabay.com, then open pixabay.com/api/docs while logged in — your key is shown on that page. Without a footage key you only get gradient backgrounds |
| `PEXELS_API_KEY` | optional | Pexels has paused new key issuance; only useful if you already have a key |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | optional | get a ping with a Studio link for each upload |
| `ELEVENLABS_API_KEY` | optional | set `voice.provider: elevenlabs` and `elevenlabs.voice_id` in the YAML |

**Background music (optional but recommended):** drop royalty-free `.mp3` files into `assets/music/`
(e.g. from YouTube Audio Library). One is picked at random and automatically lowered under the voice.

## First run
1. Actions → *Daily video robot* → **Run workflow** → `kind = short`, `dry_run = true`.
2. Download the `dry-run-video` artifact and watch it. Do the same with `kind = long`.
3. Happy? Leave `publish.mode: review` for the first week. Uploads arrive **private** with a Studio link;
   you watch and press Publish. Later you can switch to `veto` (auto-publishes after 3 h unless you delete it)
   or `auto`.

Local offline check (no API keys): `python scripts/smoke_test.py`

## Tuning (all in `channels/technology.yaml`)
- `schedule` — videos per day, minimum gap, active hours, minimum story scores.
- `style` — persona, rules, banned phrases. Most of the "feel" of the channel lives here.
- `sources.feeds` — add/remove feeds. Dead feeds are skipped automatically.
- `formats.*.target` — script lengths.

## Adding another channel later
Copy `channels/technology.yaml` to `channels/<name>.yaml`, change niche / sources / style / voice, add a
`YOUTUBE_REFRESH_TOKEN_<NAME>` secret (same client id/secret works), and add a workflow step with
`--channel <name>`. Start with one channel and add more only when its videos earn real views.

## Things that will bite you
- **Gemini model names.** Models get shut down. The client auto-discovers working models if the defaults
  fail; override with a `GEMINI_MODELS` env var (comma separated) if needed.
- **OAuth "Testing" mode** makes refresh tokens expire after 7 days. Set the OAuth consent screen to
  *In production*.
- **API audit.** Uploads from an unaudited Google Cloud project can be forced to private. If videos stay
  private in `auto` mode, apply for the YouTube API compliance audit.
- **Quota.** Default 10,000 units/day; an upload costs ~1,600. 4-5 uploads/day per project is fine;
  many channels need a quota extension.
- **edge-tts** is a free but unofficial route to Microsoft's voices and can break. ElevenLabs is the
  stable upgrade.
- **Monetization.** YouTube demonetizes generic, templated, mass-produced channels. The `review` step,
  original analysis, source credits in every description and a narrow niche are what protect you.
