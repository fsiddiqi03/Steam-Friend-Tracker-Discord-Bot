from datetime import datetime, timezone

import discord

FOOTER = "Steam Flock"


def _base(title: str, description: str, color: discord.Color, avatar: str | None) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    if avatar:
        embed.set_thumbnail(url=avatar)
    embed.set_footer(text=FOOTER)
    return embed


def format_duration(seconds: float) -> str:
    """Session length as '2h 14m', '43m', or '58s'."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def started_playing(name: str, game: str, avatar: str | None) -> discord.Embed:
    return _base(
        title=f"🎮 {name} started playing",
        description=f"**{game}**",
        color=discord.Color.green(),
        avatar=avatar,
    )


def switched_game(name: str, old_game: str, new_game: str, avatar: str | None) -> discord.Embed:
    embed = _base(
        title=f"🔀 {name} switched games",
        description=f"**{new_game}**",
        color=discord.Color.blurple(),
        avatar=avatar,
    )
    embed.add_field(name="Ditched", value=old_game, inline=True)
    return embed


def stopped_playing(
    name: str, game: str, avatar: str | None, duration: float | None
) -> discord.Embed:
    embed = _base(
        title=f"💤 {name} stopped playing",
        description=f"**{game}**",
        color=discord.Color.greyple(),
        avatar=avatar,
    )
    if duration is not None:
        embed.add_field(name="Session", value=format_duration(duration), inline=True)
    return embed
