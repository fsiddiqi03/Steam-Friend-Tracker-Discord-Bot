import json
import logging

import requests

logger = logging.getLogger("steam")

BASE_URL = "https://api.steampowered.com"
MAX_IDS_PER_REQUEST = 100


class SteamClient:
    """Thin wrapper around the Steam Web API endpoints the bot needs."""

    def __init__(self, api_key: str, steam_id: str, friends_file: str):
        self.api_key = api_key
        self.steam_id = steam_id
        self.friends_file = friends_file
        self.session = requests.Session()

    def _get(self, path: str, params: dict) -> dict:
        params = {"key": self.api_key, **params}
        r = self.session.get(f"{BASE_URL}/{path}", params=params, timeout=10)
        r.raise_for_status()
        return r.json()

    def get_friend_ids(self) -> list[str]:
        """Every steamid on the configured account's friends list.

        Not used by the poll loop -- this is for regenerating friends.json.
        """
        data = self._get(
            "ISteamUser/GetFriendList/v1/",
            {"steamid": self.steam_id, "relationship": "friend"},
        )
        friends = data.get("friendslist", {}).get("friends", [])
        return [f["steamid"] for f in friends]

    def get_summaries(self, ids: list[str]) -> dict[str, dict]:
        """Player summaries keyed by steamid, in chunks the API will accept."""
        summaries = {}
        for i in range(0, len(ids), MAX_IDS_PER_REQUEST):
            chunk = ids[i : i + MAX_IDS_PER_REQUEST]
            data = self._get(
                "ISteamUser/GetPlayerSummaries/v2/",
                {"steamids": ",".join(chunk)},
            )
            for player in data["response"]["players"]:
                summaries[player["steamid"]] = player
        return summaries

    def load_friends(self) -> dict[str, str]:
        """The tracked steamid -> display name map, re-read every poll so the
        file can be edited without restarting the bot."""
        with open(self.friends_file, encoding="utf-8") as f:
            return json.load(f)

    def get_friend_activity(self) -> list[dict]:
        """What everyone in friends.json is up to right now.

        Returns one dict per friend Steam gave us data for. `game` is None when
        they are not in a game. Private profiles are skipped.
        """
        friends = self.load_friends()
        summaries = self.get_summaries(list(friends))

        activity = []
        for steamid, name in friends.items():
            player = summaries.get(steamid)
            if player is None:
                logger.debug("No summary returned for %s (%s)", name, steamid)
                continue
            activity.append(
                {
                    "steamid": steamid,
                    "name": name or player.get("personaname", "Unknown"),
                    "game": player.get("gameextrainfo"),
                    "avatar": player.get("avatarfull") or player.get("avatar"),
                }
            )
        return activity
