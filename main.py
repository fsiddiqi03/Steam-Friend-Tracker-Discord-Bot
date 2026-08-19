import asyncio
import logging
import time

import discord
from discord.ext import commands, tasks

import embeds
from config import (
    CHANNEL_ID,
    FRIENDS_FILE,
    LOG_LEVEL,
    POLL_INTERVAL,
    STEAM_API_KEY,
    STEAM_ID,
    TOKEN,
)
from steam import SteamClient

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("flock")

bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
steam = SteamClient(STEAM_API_KEY, STEAM_ID, FRIENDS_FILE)

# steamid -> the game we last saw them in (None means they were idle)
last_game: dict[str, str | None] = {}
# steamid -> monotonic timestamp of when the current game started
started_at: dict[str, float] = {}


async def check_friends() -> None:
    activity = await asyncio.to_thread(steam.get_friend_activity)

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        logger.error("Channel %s not found -- is the bot in that server?", CHANNEL_ID)
        return

    now = time.monotonic()
    for friend in activity:
        steamid, name = friend["steamid"], friend["name"]
        game, avatar = friend["game"], friend["avatar"]
        previous = last_game.get(steamid)

        if game == previous:
            continue

        last_game[steamid] = game

        if game and previous is None:
            started_at[steamid] = now
            embed = embeds.started_playing(name, game, avatar)
        elif game and previous:
            started_at[steamid] = now
            embed = embeds.switched_game(name, previous, game, avatar)
        else:
            start = started_at.pop(steamid, None)
            duration = now - start if start else None
            embed = embeds.stopped_playing(name, previous, avatar, duration)

        logger.info("%s: %s -> %s", name, previous, game)
        await channel.send(embed=embed)


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
    logger.info("Logged in as %s", bot.user)
    if not poll_friends.is_running():
        poll_friends.start()
        logger.info("Watching the flock every %d seconds", POLL_INTERVAL)


async def main() -> None:
    missing = [
        name
        for name, value in (
            ("DISCORD_TOKEN", TOKEN),
            ("DISCORD_CHANNEL_ID", CHANNEL_ID),
            ("STEAM_API_KEY", STEAM_API_KEY),
        )
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing required .env values: {', '.join(missing)}")

    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
