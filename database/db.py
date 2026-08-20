import logging
import os

from dotenv import load_dotenv
from supabase import Client, create_client

# Load here rather than relying on config being imported first -- this module
# is usable on its own, and a missing .env should not depend on import order.
load_dotenv()

logger = logging.getLogger("db")


def get_client() -> Client:
    """Service-role Supabase client.

    Bypasses RLS, so this key must never reach a browser or a log line.
    """
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    missing = [
        name
        for name, value in (("SUPABASE_URL", url), ("SUPABASE_SERVICE_ROLE_KEY", key))
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required .env values: {', '.join(missing)}")

    return create_client(url, key)


def upsert_players(client, rows) -> int:
    """Insert or refresh roster rows and return how many were written.

    Goes through the upsert_players SQL function, which only ever touches
    steam_id, name and avatar_url -- game state is left alone.
    """
    if not rows:
        return 0

    response = client.rpc("upsert_players", {"rows": rows}).execute()
    return response.data


def load_player_rows(client) -> list:
    """Every player the bot should be watching."""
    return client.table("players").select("*").eq("is_tracked", True).order("name").execute().data


def load_open_sessions(client) -> list:
    """Sessions with no end time -- someone was playing when we last looked."""
    return (
        client.table("game_sessions")
        .select("id, steam_id, game_name, app_id, started_at")
        .is_("ended_at", "null")
        .execute()
        .data
    )


def open_session(client, steam_id, game_name, app_id, started_at) -> int:
    """Start a session and return its id.

    Raises if the player already has an open one -- the partial unique index
    refuses to let a player be in two games at once.
    """
    response = (
        client.table("game_sessions")
        .insert(
            {
                "steam_id": steam_id,
                "game_name": game_name,
                "app_id": app_id,
                "started_at": started_at.isoformat(),
            }
        )
        .execute()
    )
    return response.data[0]["id"]


def close_session(client, session_id, ended_at, ended_by) -> None:
    """End a session. ended_by is 'stopped', 'switched' or 'reconciled'."""
    client.table("game_sessions").update(
        {"ended_at": ended_at.isoformat(), "ended_by": ended_by}
    ).eq("id", session_id).execute()


def update_player(client, steam_id, patch) -> None:
    """Patch the players cache. Never touches name -- that one is user-owned."""
    if not patch:
        return
    client.table("players").update(patch).eq("steam_id", steam_id).execute()
