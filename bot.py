import os
import re
import json
import base64
import asyncio
import logging

import discord
import aiohttp
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ALLOWED_CHANNEL_IDS = {
    int(cid) for cid in os.environ.get("ALLOWED_CHANNEL_IDS", "").split(",") if cid.strip()
}

# GitHub write access, used only by the !update command to publish itinerary edits.
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Roleseee/japan-trip-bot")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
ITINERARY_FILE = "itinerary_data.json"

# Only these Discord user IDs may request/confirm hub updates.
AUTHORIZED_USER_IDS = {
    int(uid) for uid in os.environ.get("AUTHORIZED_USER_IDS", "").split(",") if uid.strip()
}

COMMAND_PREFIX = "!ask"
UPDATE_PREFIX = "!update"
MODEL = "claude-sonnet-5"
HISTORY_LIMIT = 8  # how many prior messages to include as context
MAX_SEARCHES_PER_REPLY = 4  # caps cost/latency per message; raise if answers feel cut short
MAX_PAUSE_CONTINUATIONS = 3  # safety cap on the pause_turn continuation loop below
UPDATE_CONFIRM_TIMEOUT = 300  # seconds to wait for a reaction before cancelling an update

HUB_URL = "https://roleseee.github.io/japan-trip-bot/japan-trip-hub.html"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("japan-trip-bot")

anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)

WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": MAX_SEARCHES_PER_REPLY,
    "user_location": {
        "type": "approximate",
        "country": "JP",
        "timezone": "Asia/Tokyo",
    },
}

TRIP_FACTS = """TRIP FACTS (treat as ground truth, don't contradict):
- Dates: November 4-18, 2026
- Cities: Tokyo, Osaka, and likely Kyoto
- Travelers: 4 total. 3 are there the full two weeks (Nov 4-18). 1 is only there Nov 4-11.
- Kyoto foliage note: as of the last forecast check, 2026 peak autumn color in Kyoto is expected around Nov 20-Dec 7, so this trip (ending Nov 18) will likely catch early-to-mid color rather than full peak. Arashiyama and Tofuku-ji tend to turn earliest."""

ROLE_INSTRUCTIONS = """YOUR ROLE:
- Answer questions about the trip, suggest food/activities/hobbies in Tokyo, Osaka, and Kyoto, help with logistics questions, and give a sanity check on booking timing.
- If anyone asks where the hub/itinerary page/link is, or how to access it, reply with the hub URL above and a one-line description. Don't make them ask twice.
- When asked about the hub/link, or about what's on the wishlist/recommendations for a city, use the sections above and give the full relevant list, not just a short excerpt.
- You have a live web search tool. Use it whenever the answer depends on something current or changing: ticket on-sale status, prices, opening hours, weather/foliage updates, restaurant availability, event dates, exchange rates, or anything where the trip facts above might now be stale. Don't search for stable general knowledge (e.g. "what is kaiseki") - answer that directly.
- If a live search result contradicts a "trip fact" above (e.g. a booking window changed), trust the fresh search result, use it, and flag the discrepancy briefly rather than silently overriding.
- When you cite something from a search, keep it light - a short "(via [site name])" or a link is enough; don't dump a bibliography into a group chat.
- Keep answers concise and useful for a group chat - a short list is fine when someone asks for a list, don't compress it into a vague summary; just avoid turning it into an essay unless asked for more detail.
- If multiple people are chiming in, feel free to address the group rather than one person.
- If someone asks to change/add/remove something on the hub, tell them to use `!update <what they want changed>` instead of trying to do it via `!ask` - you can't edit the hub yourself through this command."""


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def load_itinerary_data() -> dict:
    with open(ITINERARY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def build_trip_system_prompt(data: dict) -> str:
    """Turn the structured itinerary data into the system prompt text."""
    parts = [
        "You are the trip-planning assistant for a group of friends' Japan trip.",
        "",
        f"THE HUB PAGE:\n- There is a live trip hub page at {HUB_URL} - it has a booking countdown/checklist, "
        "flights & stays, and full recommendation lists (sights, food, gaming/shopping, activities) for Tokyo, "
        "Osaka, and Kyoto/Uji, plus a notes tab.\n"
        "- The wishlist/recommendation info below is generated from the same data as the hub, kept in sync - "
        "always give the FULL relevant list when asked \"what have we got in Tokyo\" / \"what's on our "
        "wishlist\" / etc, not just the items that still need booking.",
        "",
        TRIP_FACTS,
    ]

    stays = data.get("stays", [])
    if stays:
        parts.append("")
        parts.append("FLIGHTS & STAYS:")
        for s in stays:
            parts.append(f"- {s.get('title', '')} ({s.get('meta', '')}): {_strip_html(s.get('body', ''))}")

    checklist = data.get("booking_checklist", [])
    if checklist:
        parts.append("")
        parts.append("STILL TO BOOK (rough windows, check with web search if this might be stale):")
        for item in checklist:
            parts.append(
                f"- {item.get('title', '')} [{item.get('meta', '')}]: {_strip_html(item.get('body', ''))}"
            )

    if data.get("foliage_note"):
        parts.append("")
        parts.append(_strip_html(data["foliage_note"]))

    wishlist = data.get("wishlist", {})
    for city_key, label in (("tokyo", "TOKYO WISHLIST"), ("osaka", "OSAKA WISHLIST"), ("kyoto", "KYOTO & UJI WISHLIST")):
        city = wishlist.get(city_key)
        if not city:
            continue
        parts.append("")
        header = label
        if city.get("subhead"):
            header += f" ({city['subhead']})"
        parts.append(header + ":")
        if city.get("callout"):
            parts.append(f"({_strip_html(city['callout'])})")
        for col in city.get("columns", []):
            items = "; ".join(
                f"{it.get('title', '')}" + (f" - {it.get('detail')}" if it.get("detail") else "")
                for it in col.get("items", [])
            )
            parts.append(f"- {col.get('heading', '')}: {items}")

    parts.append("")
    parts.append(ROLE_INSTRUCTIONS)
    return "\n".join(parts)


UPDATE_EDITOR_PROMPT = """You maintain a Japan trip's structured itinerary data, stored as JSON.

You will be given the CURRENT itinerary JSON and a natural-language REQUEST describing a change someone wants.

Apply the requested change to produce an UPDATED version of the JSON, preserving the exact same schema and all
unrelated fields/content unchanged. Follow the existing style of entries you see (e.g. wishlist items have a
"title" and short "detail"; booking_checklist items have id/urgent/meta/title/body).

- If asked to add something, add it to the most sensible existing section/column, inventing a short reasonable
  "detail"/"body" if none was given, in the same tone as neighboring entries.
- If asked to mark something as booked/done, update the relevant checklist item's "meta"/"body" (or "stays" entry)
  to reflect that rather than deleting it, unless told to remove it.
- If asked to remove something, remove just that entry.
- Never invent a completely new top-level schema field. Only use the fields already present in the current JSON.
- ids in booking_checklist should be short lowercase_snake_case slugs, unique.

Respond with ONLY a single JSON object (no markdown code fences, no commentary before or after) with exactly two
top-level keys:
{
  "summary": "<one or two short sentences describing what you changed, for a human to review before it's published>",
  "updated_data": { ...the full updated itinerary JSON, matching the original schema exactly... }
}
"""


def strip_mentions(content: str, client_user: discord.ClientUser) -> str:
    return (
        content.replace(f"<@{client_user.id}>", "")
        .replace(f"<@!{client_user.id}>", "")
        .strip()
    )


def chunk_message(text: str, limit: int = 1900):
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks


class TripBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.itinerary_data = load_itinerary_data()
        self.trip_system_prompt = build_trip_system_prompt(self.itinerary_data)
        self.http_session: aiohttp.ClientSession | None = None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()

    async def on_ready(self):
        log.info("Logged in as %s (id: %s)", self.user, self.user.id)

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if ALLOWED_CHANNEL_IDS and message.channel.id not in ALLOWED_CHANNEL_IDS:
            return

        stripped = message.content.strip()

        if stripped.lower().startswith(UPDATE_PREFIX):
            await self.handle_update(message, stripped[len(UPDATE_PREFIX):].strip())
            return

        is_mention = self.user in message.mentions
        is_command = stripped.lower().startswith(COMMAND_PREFIX)

        if not (is_mention or is_command):
            return

        if is_command:
            question = stripped[len(COMMAND_PREFIX):].strip()
        else:
            question = strip_mentions(message.content, self.user)

        if not question:
            await message.channel.send(
                "What do you want to ask? e.g. `!ask best ramen near Dotonbori`"
            )
            return

        async with message.channel.typing():
            history = await self._recent_history(message.channel, exclude_id=message.id)
            reply = await self._ask_claude(question, history)

        for chunk in chunk_message(reply):
            await message.channel.send(chunk)

    # ---------- !ask ----------

    async def _recent_history(self, channel, exclude_id: int):
        history = []
        async for msg in channel.history(limit=HISTORY_LIMIT + 1):
            if msg.id == exclude_id or msg.author.bot:
                continue
            history.append(f"{msg.author.display_name}: {msg.content}")
        history.reverse()
        return history

    async def _ask_claude(self, question: str, history) -> str:
        context_block = "\n".join(history) if history else "(no prior context)"
        user_message = (
            f"Recent chat context:\n{context_block}\n\nQuestion to answer:\n{question}"
        )
        messages = [{"role": "user", "content": user_message}]

        try:
            response = await self._create_message(messages)

            # A search-heavy turn can pause partway through; resubmitting the
            # assistant message unchanged lets the API pick back up where it left off.
            continuations = 0
            while response.stop_reason == "pause_turn" and continuations < MAX_PAUSE_CONTINUATIONS:
                messages.append({"role": "assistant", "content": response.content})
                response = await self._create_message(messages)
                continuations += 1

            return _format_reply(response)
        except Exception as exc:  # noqa: BLE001
            log.exception("Claude API call failed")
            return f"Hit an error talking to Claude: `{exc}`"

    async def _create_message(self, messages):
        return await asyncio.to_thread(
            anthropic_client.messages.create,
            model=MODEL,
            max_tokens=1400,
            system=self.trip_system_prompt,
            tools=[WEB_SEARCH_TOOL],
            messages=messages,
        )

    # ---------- !update ----------

    async def handle_update(self, message: discord.Message, request_text: str):
        if message.author.id not in AUTHORIZED_USER_IDS:
            await message.channel.send(
                "Only the trip organizers can update the hub. Ask one of them to run this, "
                "or ask me a question instead with `!ask`."
            )
            return

        if not request_text:
            await message.channel.send(
                "What should I update? e.g. `!update add Kinkaku-ji to the Kyoto wishlist` "
                "or `!update mark the USJ Express Pass as booked`."
            )
            return

        if not GITHUB_TOKEN:
            await message.channel.send(
                "Updates aren't configured yet - the bot is missing a `GITHUB_TOKEN` with write "
                "access to the repo. Ask whoever deployed the bot to add one."
            )
            return

        async with message.channel.typing():
            try:
                summary, new_data = await self._propose_update(request_text)
            except Exception as exc:  # noqa: BLE001
                log.exception("Failed to propose update")
                await message.channel.send(f"Couldn't work out that edit: `{exc}`. Try rephrasing it.")
                return

        preview = await message.channel.send(
            f"**Proposed hub update:** {summary}\n\n"
            f"React with ✅ to publish this, or ❌ to cancel. "
            f"(only a trip organizer's reaction counts, expires in {UPDATE_CONFIRM_TIMEOUT // 60} min)"
        )
        await preview.add_reaction("✅")
        await preview.add_reaction("❌")

        def check(reaction: discord.Reaction, user: discord.User) -> bool:
            return (
                reaction.message.id == preview.id
                and user.id in AUTHORIZED_USER_IDS
                and str(reaction.emoji) in ("✅", "❌")
            )

        try:
            reaction, user = await self.wait_for(
                "reaction_add", timeout=UPDATE_CONFIRM_TIMEOUT, check=check
            )
        except asyncio.TimeoutError:
            await message.channel.send("Update timed out with no confirmation - nothing was changed.")
            return

        if str(reaction.emoji) == "❌":
            await message.channel.send(f"Cancelled by {user.display_name}. Nothing was changed.")
            return

        async with message.channel.typing():
            try:
                await self._commit_itinerary(new_data, summary, message.author.display_name)
            except Exception as exc:  # noqa: BLE001
                log.exception("Failed to commit itinerary update")
                await message.channel.send(f"Confirmed, but publishing failed: `{exc}`. Nothing went live.")
                return

            self.itinerary_data = new_data
            self.trip_system_prompt = build_trip_system_prompt(new_data)

        await message.channel.send(
            f"Published by {user.display_name} ✅ The hub will update in a minute or two: {HUB_URL}"
        )

    async def _propose_update(self, request_text: str):
        current_json = json.dumps(self.itinerary_data, ensure_ascii=False)
        user_message = f"CURRENT itinerary JSON:\n{current_json}\n\nREQUEST:\n{request_text}"

        response = await asyncio.to_thread(
            anthropic_client.messages.create,
            model=MODEL,
            max_tokens=4096,
            system=UPDATE_EDITOR_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        text = "".join(block.text for block in response.content if block.type == "text").strip()
        text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()

        parsed = json.loads(text)
        summary = parsed["summary"]
        new_data = parsed["updated_data"]

        if not isinstance(new_data, dict) or "booking_checklist" not in new_data or "wishlist" not in new_data:
            raise ValueError("edited JSON is missing expected sections")

        return summary, new_data

    async def _commit_itinerary(self, new_data: dict, summary: str, editor_name: str):
        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{ITINERARY_FILE}"
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        }

        async with self.http_session.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH}) as resp:
            if resp.status != 200:
                raise RuntimeError(f"couldn't read current file (HTTP {resp.status})")
            current = await resp.json()
            sha = current["sha"]

        new_content = json.dumps(new_data, indent=2, ensure_ascii=False) + "\n"
        payload = {
            "message": f"Update itinerary via Discord ({editor_name}): {summary}",
            "content": base64.b64encode(new_content.encode("utf-8")).decode("ascii"),
            "sha": sha,
            "branch": GITHUB_BRANCH,
        }

        async with self.http_session.put(api_url, headers=headers, json=payload) as resp:
            if resp.status not in (200, 201):
                body = await resp.text()
                raise RuntimeError(f"GitHub commit failed (HTTP {resp.status}): {body[:200]}")


def _format_reply(response) -> str:
    """Pull the plain-text answer out of the response, plus a short source list
    if the reply cites any web search results."""
    text_parts = []
    sources = []  # list of (title, url), de-duplicated, in order seen

    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
            for citation in getattr(block, "citations", None) or []:
                url = getattr(citation, "url", None)
                title = getattr(citation, "title", None) or url
                if url and (title, url) not in sources:
                    sources.append((title, url))

    answer = "\n".join(part.strip() for part in text_parts if part.strip())
    if not answer:
        answer = "I couldn't put together an answer for that one - try rephrasing?"

    if sources:
        footer = "\n".join(f"- [{title}]({url})" for title, url in sources[:4])
        answer = f"{answer}\n\n**Sources:**\n{footer}"

    return answer


if __name__ == "__main__":
    client = TripBot()
    client.run(DISCORD_TOKEN)
