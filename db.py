import logging
import os

from supabase import Client, create_client

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
