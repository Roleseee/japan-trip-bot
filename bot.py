import os
import asyncio
import logging

import discord
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ALLOWED_CHANNEL_IDS = {
    int(cid) for cid in os.environ.get("ALLOWED_CHANNEL_IDS", "").split(",") if cid.strip()
}
COMMAND_PREFIX = "!ask"
MODEL = "claude-sonnet-5"
HISTORY_LIMIT = 8  # how many prior messages to include as context
MAX_SEARCHES_PER_REPLY = 4  # caps cost/latency per message; raise if answers feel cut short
MAX_PAUSE_CONTINUATIONS = 3  # safety cap on the pause_turn continuation loop below

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

HUB_URL = "https://roleseee.github.io/japan-trip-bot/japan-trip-hub.html"

TRIP_SYSTEM_PROMPT = f"""You are the trip-planning assistant for a group of friends' Japan trip.

THE HUB PAGE:
- There is a live trip hub page at {HUB_URL} - it has a booking countdown/checklist, flights & stays, and full recommendation lists (sights, food, gaming/shopping, activities) for Tokyo, Osaka, and Kyoto/Uji, plus a notes tab.
- If anyone asks where the hub/itinerary page/link is, or how to access it, reply with that URL directly and a one-line description. Don't make them ask twice.
- The wishlist/recommendation info below is the same content as the hub, kept here so you can answer questions about it directly in chat too - always give the FULL relevant list when asked "what have we got in Tokyo" / "what's on our wishlist" / etc, not just the items that still need booking.

TRIP FACTS (treat as ground truth, don't contradict):
- Dates: November 4-18, 2026
- Cities: Tokyo, Osaka, and likely Kyoto
- Travelers: 4 total. 3 are there the full two weeks (Nov 4-18). 1 is only there Nov 4-11.
- Flights: booked (Cathay Pacific, likely via Hong Kong).
- Tokyo hotel: booked - La'Gent Hotel Shinjuku Kabukicho.
- Kyoto/Osaka accommodation: not yet decided whether these are overnight stays or day trips from Tokyo/Shinjuku.
- Still to book (rough windows, as of a July 2026 planning session):
  - Nintendo Museum, Uji (Kyoto): lottery-only via museum-tickets.nintendo.com. Enter during August 2026 for a November visit, results announced Sept 1. No walk-ins, no third-party booking.
  - Universal Studios Japan (Osaka) Express Pass: goes on sale ~mid-September for November dates. 2026 is USJ's 25th anniversary, expect higher demand than usual.
  - teamLab Planets/Borderless (Tokyo): calendar opens ~early Sept 2026, book 2-3+ weeks ahead of the date you want. Rough pricing: Planets ¥2,600-4,200 adult, Borderless ¥3,800-5,600 adult.
  - Ghibli Museum (Mitaka, Tokyo): overseas tickets release on the 10th of the prior month at 10am JST via Lawson Ticket International (Nov tickets released Oct 10). Sells out within hours.
  - Shibuya Sky sunset slot: dates open ~2 weeks ahead at midnight JST. Advance tickets ¥2,500 vs ¥3,000 on the door. Book via Klook if using a non-Japanese card (official site has been rejecting foreign cards).
  - Mario Kart street tour: every participant needs an International Driving Permit (1949 Geneva Convention, booklet form) obtained in their home country before flying out - cannot be done in Japan.
  - Shinkansen (Tokyo-Kyoto-Osaka): a JR Pass is NOT worth it for this route alone - book point-to-point reserved seats instead (via SmartEX / Ekinet), ~3-4 weeks ahead, since November is peak autumn travel.
  - Popular restaurant/omakase reservations: book via TableCheck / Pocket Concierge / OMAKASE app, anywhere from 1-8 weeks ahead depending on the place.
  - Kyoto autumn night illuminations (Kiyomizu-dera, Eikando, Kodai-ji): tickets usually release about a month ahead, check each temple's site directly in October.
  - Baseball at Tokyo Dome: 2026 NPB Japan Series is on track to finish around Nov 1, before the trip starts Nov 4 - an actual game during the trip looks unlikely, may be worth dropping.
- Kyoto foliage note: as of the last forecast check, 2026 peak autumn color in Kyoto is expected around Nov 20-Dec 7, so this trip (ending Nov 18) will likely catch early-to-mid color rather than full peak. Arashiyama and Tofuku-ji tend to turn earliest.

TOKYO WISHLIST (base: Shinjuku):
- Sights & culture: Shibuya Sky (sunset, advance booking required), Shibuya Crossing (night photography), Nezu Shrine (quieter temple), Gotokuji Temple (lucky cat statues).
- Gaming & shopping: Nintendo Tokyo + Capcom Store (both in Shibuya PARCO, worth combining into one visit), Super Potato (retro games), Mandarake Complex (collectibles/manga/games), Akihabara arcade crawl, Square Enix Store.
- Food, bars & nightlife: Omoide Yokocho (skewers and beers), Karaoke Kan, Bar Centifolia (cocktails), Gonpachi (the Kill Bill restaurant).
- Activities: Mario Kart street tour (needs IDP, see booking list), Baseball at Tokyo Dome (unlikely to align with dates, see booking list).
- Recovery: Spa LaQua (onsen/spa/recovery day).

OSAKA WISHLIST:
- Food: Dotonbori (takoyaki, nightlife, neon - best after dark), yakiniku dinner, Kuromon Ichiba Market (covered market, sea urchin/oysters/wagyu/sashimi, ~9am-6pm, closed some Sundays), okonomiyaki at Mizuno or Chibo, Shinsekai (where kushikatsu originated, cheaper/less touristy than Dotonbori), small-counter sushi (Jinsei - 6-seat counter, Sakae Sushi - great value).
- Hobbies & activities: Universal Studios Japan (see booking list), Osaka Castle grounds (solid half-day, some autumn colour by mid-Nov), guided food tour through Dotonbori/Kuromon.

KYOTO & UJI WISHLIST (Kyoto sightseeing still to be finalised - these are options, not confirmed):
- Uji: Nintendo Museum (lottery, see booking list), udon and tonkatsu food stops.
- Food: Nishiki Market (Kyoto's Kuromon - street food, pickles, tea), a formal kaiseki dinner (book via TableCheck), bento-making class.
- Sights & activities: Fushimi Inari Taisha (thousand-gate trail, go early to beat crowds), Arashiyama + Sagano Romantic Train (bamboo grove + scenic train, best early-color bet for these dates), samurai experience (Bushido, sword handling, short Zen meditation), Kintsugi workshop (gold-lacquer ceramic repair), Kiyomizu-dera / Sannenzaka & Ninenzaka (classic temple + old streets, pairs well with an illumination night visit).

YOUR ROLE:
- Answer questions about the trip, suggest food/activities/hobbies in Tokyo, Osaka, and Kyoto, help with logistics questions, and give a sanity check on booking timing.
- When asked about the hub/link, or about what's on the wishlist/recommendations for a city, use the sections above and give the full relevant list, not just a short excerpt.
- You have a live web search tool. Use it whenever the answer depends on something current or changing: ticket on-sale status, prices, opening hours, weather/foliage updates, restaurant availability, event dates, exchange rates, or anything where the trip facts above might now be stale. Don't search for stable general knowledge (e.g. "what is kaiseki") - answer that directly.
- If a live search result contradicts a "trip fact" above (e.g. a booking window changed), trust the fresh search result, use it, and flag the discrepancy briefly rather than silently overriding.
- When you cite something from a search, keep it light - a short "(via [site name])" or a link is enough; don't dump a bibliography into a group chat.
- Keep answers concise and useful for a group chat - a short list is fine when someone asks for a list, don't compress it into a vague summary; just avoid turning it into an essay unless asked for more detail.
- If multiple people are chiming in, feel free to address the group rather than one person.
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

    async def on_ready(self):
        log.info("Logged in as %s (id: %s)", self.user, self.user.id)

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if ALLOWED_CHANNEL_IDS and message.channel.id not in ALLOWED_CHANNEL_IDS:
            return

        is_mention = self.user in message.mentions
        is_command = message.content.strip().lower().startswith(COMMAND_PREFIX)

        if not (is_mention or is_command):
            return

        if is_command:
            question = message.content.strip()[len(COMMAND_PREFIX):].strip()
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
            system=TRIP_SYSTEM_PROMPT,
            tools=[WEB_SEARCH_TOOL],
            messages=messages,
        )


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
