# Japan Trip Bot — Setup Guide

Three files work together:

1. **`itinerary_data.json`** — the single source of truth for the trip: booking checklist, flights/stays, and the Tokyo/Osaka/Kyoto wishlists.
2. **`japan-trip-hub.html`** — a static page that fetches `itinerary_data.json` and renders it. Hosted on GitHub Pages: https://roleseee.github.io/japan-trip-bot/japan-trip-hub.html
3. **`bot.py`** — a Discord bot. `@mention` or `!ask` it for live trip Q&A (Claude + web search, so answers on prices/on-sale dates stay current). `!update` lets 4 authorized people edit the itinerary from Discord, which publishes straight to the hub.

This is already deployed (Railway + GitHub Pages), so most of this doc is for reference if you need to redeploy from scratch or add another authorized editor.

## 1. Discord bot — create it

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application** → name it (e.g. "Japan Trip Bot").
2. **Bot** tab → **Add Bot**. Under **Privileged Gateway Intents**, turn on **Message Content Intent** (required — the bot reads message text to answer questions).
3. Copy the **bot token** (Reset Token if needed) — this is your `DISCORD_BOT_TOKEN`.
4. **Installation** tab → set Guild Install scopes to `bot` (+ `applications.commands` if you want) with permissions `Send Messages`, `Read Message History`, `View Channels`. Use the Discord-provided install link (or the OAuth2 URL Generator) to invite it to a server.

## 2. Anthropic API key

Get one from [platform.claude.com](https://platform.claude.com/settings/keys) → **API Keys**. This is your `ANTHROPIC_API_KEY`. The bot calls the API per question/update asked, so it's pay-as-you-go against your account's credit balance, not part of a Claude subscription.

## 3. GitHub token (for `!update`)

The `!update` command needs write access to this repo so it can commit itinerary changes:

1. [GitHub → Settings → Developer settings → Fine-grained tokens](https://github.com/settings/personal-access-tokens/new).
2. Resource owner: your account. Repository access: **Only select repositories** → this repo.
3. Permissions → **Contents: Read and write**.
4. Generate, copy the token — this is `GITHUB_TOKEN`. It won't be shown again.

## 4. Authorized editors

Only these Discord user IDs can request/confirm `!update` edits (anyone else gets told to ask one of them). To get a user ID: enable Developer Mode (User Settings → Advanced), then right-click someone → **Copy User ID**. Comma-separate them for `AUTHORIZED_USER_IDS`.

## 5. Configure

Copy `env.example` to `.env` in the same folder as `bot.py`, and fill in:

```
DISCORD_BOT_TOKEN=<from step 1>
ANTHROPIC_API_KEY=<from step 2>
ALLOWED_CHANNEL_IDS=          # optional — comma-separated channel IDs to restrict the bot to
GITHUB_TOKEN=<from step 3>
GITHUB_REPO=Roleseee/japan-trip-bot
AUTHORIZED_USER_IDS=<from step 4, comma-separated>
```

To get a channel ID: enable Discord's Developer Mode (User Settings → Advanced), then right-click a channel → **Copy Channel ID**.

## 6. Run it

**Locally (for testing):**

```bash
pip install -r requirements.txt
python bot.py
```

**Hosted 24/7** — this bot runs on Railway, connected to this GitHub repo:

1. Push these files to a GitHub repo.
2. [railway.app](https://railway.app) → **New Project → Deploy from GitHub repo**.
3. In the Railway project's **Variables** tab, add all the variables from step 5.
4. Railway auto-detects `requirements.txt` and runs `python bot.py` as the start command. Every push to `main` (including `!update` commits) triggers an automatic redeploy — check the logs for `Logged in as ...` to confirm it's live.

Any other always-on host works too (a Raspberry Pi, a small VPS, Fly.io, etc.) — the only requirements are Python 3.10+ and the process staying alive.

## 7. Using the bot

In the Discord server:

- `@Japan Trip Bot best ramen near Shinjuku`
- `!ask has the teamLab calendar opened yet?`
- `!ask where's the hub?`

It pulls the last few messages in the channel as context and searches the web when the answer depends on something current (prices, on-sale dates, hours, weather). All itinerary content (checklist, wishlist) comes from `itinerary_data.json`, so `!update` edits show up in `!ask` answers immediately, even before the hub page finishes rebuilding.

**Updating the itinerary** (authorized users only):

```
!update add Kinkaku-ji to the Kyoto wishlist
!update mark the USJ Express Pass as booked
```

The bot proposes the change with a plain-English summary and posts ✅/❌ reactions. Only a reaction from one of the 4 authorized users confirms it (5 minute timeout). Once confirmed, it commits `itinerary_data.json` to GitHub, which auto-publishes the hub page and updates the bot's own answers.

## Sharing the hub page

Live at https://roleseee.github.io/japan-trip-bot/japan-trip-hub.html — hosted for free via GitHub Pages from this repo (repo is public; no secrets are stored in it, those all live in Railway's environment variables).

The booking checklist's checkbox state saves to each person's own browser (`localStorage`), not shared across the group. The itinerary content itself (wishlist, checklist text) is shared and lives in `itinerary_data.json`, editable via `!update`.
