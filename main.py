import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from database import db
import embeds
from Steam import tracker
from config import (
    CHANNEL_ID,
    FRIENDS_FILE,
    LOG_LEVEL,
    POLL_INTERVAL,
    STEAM_API_KEY,
    STEAM_ID,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
    TOKEN,
)
from Steam.steam import SteamClient

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("flock")

bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
steam = SteamClient(STEAM_API_KEY, STEAM_ID, FRIENDS_FILE)

supabase = None
# Every tracked player, loaded from the database once at startup. State lives
# on these objects between polls so a tick only has to diff against Steam.
roster: list = []
# The first poll after a restart reconciles the database instead of announcing.
resuming = True


def embeds_for(result, player) -> list:
    """The messages a transition should produce, in order.

    A switch is announced as a stop followed by a start -- there is no
    'switched games' message, but the channel still reflects reality.
    """
    action = result["action"]
    stopped = embeds.stopped_playing(
        player.name, result["previous_game"], player.avatar_url, result["duration"]
    )
    started = embeds.started_playing(player.name, player.current_game, player.avatar_url)

    if action == "started":
        return [started]
    if action == "stopped":
        return [stopped]
    return [stopped, started]


async def check_friends() -> None:
    global resuming

    ids = [player.steam_id for player in roster]
    if not ids:
        logger.warning("Roster is empty -- run seed_players.py")
        return

    activity = await asyncio.to_thread(steam.get_activity, ids)
    now = datetime.now(timezone.utc)

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None and not resuming:
        logger.error("Channel %s not found -- is the bot in that server?", CHANNEL_ID)
        return

    for player in roster:
        live = activity.get(player.steam_id)
        if live is None:
            logger.debug("No summary for %s (%s)", player.name, player.steam_id)
            continue

        # A session that ended while the bot was down ended somewhere around
        # the last time Steam confirmed it, not now.
        closed_at = player.last_polled_at if resuming else None

        result = await asyncio.to_thread(
            tracker.apply_activity,
            supabase,
            player,
            live["game"],
            live["app_id"],
            live["avatar"],
            now,
            closed_at,
        )
        player.last_polled_at = now

        if result["action"] is None:
            continue

        logger.info(
            "%s: %s -> %s (%s)",
            player.name,
            result["previous_game"],
            player.current_game,
            result["action"],
        )

        if resuming:
            # Caught up the database across the restart; saying so would just
            # replay old news into the channel.
            continue

        for embed in embeds_for(result, player):
            await channel.send(embed=embed)

    resuming = False


@tasks.loop(seconds=POLL_INTERVAL)
async def poll_friends() -> None:
    try:
        await check_friends()
    except Exception as e:
        # Never let one bad poll kill the loop -- log it and try again next tick.
        logger.error("Poll failed: %s", e)


@poll_friends.before_loop
async def before_poll() -> None:
    await bot.wait_until_ready()


@bot.event
async def on_ready() -> None:
    global supabase, roster

    logger.info("Logged in as %s", bot.user)

    # on_ready fires again after a reconnect; the roster is only loaded once.
    if supabase is None:
        supabase = await asyncio.to_thread(db.get_client)
        roster = await asyncio.to_thread(tracker.load_roster, supabase)
        logger.info("Loaded %d players from the database", len(roster))

        resumed = [p.name for p in roster if p.is_playing]
        if resumed:
            logger.info("Resuming open sessions: %s", ", ".join(resumed))

    if not poll_friends.is_running():
        poll_friends.start()
        logger.info("Watching the flock every %d seconds", POLL_INTERVAL)


async def main() -> None:

    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
