"""Turns Steam's live view of a player into database writes.

Knows nothing about Discord. The poll loop calls apply_activity and decides
what, if anything, to say about the result.
"""

import logging

from database import db
from Steam.player import Player, parse_timestamp, transition_for

logger = logging.getLogger("tracker")


def load_roster(client) -> list:
    """Every tracked player, with any still-open session attached.

    game_sessions is the source of truth: a player counts as playing only if
    they have an open session row. Anything stale in the players cache gets
    corrected here rather than trusted.
    """
    open_by_player = {s["steam_id"]: s for s in db.load_open_sessions(client)}

    roster = []
    for row in db.load_player_rows(client):
        player = Player.from_row(row)
        session = open_by_player.get(player.steam_id)

        if session is None:
            player.current_game = None
            player.current_app_id = None
            player.session_started_at = None
            player.open_session_id = None
        else:
            player.open_session_id = session["id"]
            player.current_game = session["game_name"]
            player.current_app_id = session["app_id"]
            player.session_started_at = parse_timestamp(session["started_at"])

        roster.append(player)

    return roster


def apply_activity(client, player, live_game, live_app_id, avatar_url, now, closed_at=None) -> dict:
    """Record whatever changed for one player and report what happened.

    `closed_at` overrides when a session that ended while the bot was down is
    treated as having ended -- pass the player's last_polled_at during startup
    reconciliation. Its presence is what marks a close as 'reconciled'.

    Returns {"action", "previous_game", "duration"}; action is None when
    nothing changed.
    """
    action = transition_for(player, live_game)

    patch = {"last_polled_at": now.isoformat()}
    if avatar_url and avatar_url != player.avatar_url:
        patch["avatar_url"] = avatar_url
        player.avatar_url = avatar_url

    if action is None:
        db.update_player(client, player.steam_id, patch)
        return {"action": None, "previous_game": player.current_game, "duration": None}

    previous_game = player.current_game
    ended_at = closed_at or now
    duration = None

    if action in ("stopped", "switched"):
        duration = player.session_length(ended_at)
        if player.open_session_id is not None:
            reason = "reconciled" if closed_at else action
            db.close_session(client, player.open_session_id, ended_at, reason)
        else:
            # No row to close: the bot saw them playing but never recorded it.
            logger.warning("%s had no open session to close for %s", player.name, previous_game)
        patch["last_game"] = previous_game
        patch["last_played_at"] = ended_at.isoformat()

    if action in ("started", "switched"):
        player.open_session_id = db.open_session(
            client, player.steam_id, live_game, live_app_id, now
        )
        player.current_game = live_game
        player.current_app_id = live_app_id
        player.session_started_at = now
        patch["current_game"] = live_game
        patch["current_app_id"] = live_app_id
        patch["session_started_at"] = now.isoformat()
    else:
        player.open_session_id = None
        player.current_game = None
        player.current_app_id = None
        player.session_started_at = None
        patch["current_game"] = None
        patch["current_app_id"] = None
        patch["session_started_at"] = None

    db.update_player(client, player.steam_id, patch)
    return {"action": action, "previous_game": previous_game, "duration": duration}
