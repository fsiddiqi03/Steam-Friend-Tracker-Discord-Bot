"""One-shot: load the friends.json roster into the Supabase players table.

Run from the repo root as a module, not as a script -- it imports sibling
packages, which needs the root on sys.path:

    python -m database.seed_players [--dry-run]

Safe to re-run. Edit friends.json, run this again, and the roster catches up.
Deliberately writes no game state -- the bot owns those columns.
"""

import argparse
import json
import logging

from database import db
from config import FRIENDS_FILE, LOG_LEVEL, STEAM_API_KEY, STEAM_ID
from Steam.steam import SteamClient

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("seed")


def build_player_rows(friends, summaries) -> list:
    """Roster rows for the players table, one per entry in friends.json.

    Names come from friends.json, never from Steam. Friends whose profile is
    private or deleted still get a row -- we just have no avatar for them.
    """
    rows = []
    for steam_id, name in friends.items():
        player = summaries.get(steam_id)
        if player is None:
            logger.warning(
                "No Steam summary for %s (%s) -- private or deleted profile", name, steam_id
            )
            player = {}

        rows.append(
            {
                "steam_id": steam_id,
                "name": name,
                "avatar_url": player.get("avatarfull") or player.get("avatar"),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the rows that would be written and exit without touching the database.",
    )
    args = parser.parse_args()

    if not STEAM_API_KEY:
        raise SystemExit("Missing required .env value: STEAM_API_KEY")

    steam = SteamClient(STEAM_API_KEY, STEAM_ID, FRIENDS_FILE)
    friends = steam.load_friends()
    if not friends:
        raise SystemExit(f"{FRIENDS_FILE} is empty -- nothing to seed")

    logger.info("Fetching Steam summaries for %d friends", len(friends))
    summaries = steam.get_summaries(list(friends))
    rows = build_player_rows(friends, summaries)

    if args.dry_run:
        print(json.dumps(rows, indent=2))
        logger.info("Dry run -- %d rows not written", len(rows))
        return

    written = db.upsert_players(db.get_client(), rows)
    logger.info("Seeded %d players", written)


if __name__ == "__main__":
    main()
