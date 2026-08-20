from datetime import datetime, timedelta, timezone

import pytest

import tracker
from player import Player

NOW = datetime(2026, 8, 19, 21, 0, tzinfo=timezone.utc)
KYLE = "76561198098282377"


class FakeDB:
    """Stands in for db.py, recording every write the tracker makes."""

    def __init__(self, player_rows=None, open_sessions=None, next_session_id=100):
        self.player_rows = player_rows or []
        self.open_sessions = open_sessions or []
        self.next_session_id = next_session_id
        self.opened = []
        self.closed = []
        self.patches = []

    def load_player_rows(self, client):
        return self.player_rows

    def load_open_sessions(self, client):
        return self.open_sessions

    def open_session(self, client, steam_id, game_name, app_id, started_at):
        self.opened.append((steam_id, game_name, app_id, started_at))
        self.next_session_id += 1
        return self.next_session_id

    def close_session(self, client, session_id, ended_at, ended_by):
        self.closed.append((session_id, ended_at, ended_by))

    def update_player(self, client, steam_id, patch):
        self.patches.append((steam_id, patch))


@pytest.fixture
def fake_db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(tracker, "db", fake)
    return fake


def make(current_game=None, open_session_id=None, started_at=None, avatar_url=None):
    return Player(
        steam_id=KYLE,
        name="Kyle",
        avatar_url=avatar_url,
        current_game=current_game,
        session_started_at=started_at,
        open_session_id=open_session_id,
    )


def last_patch(fake):
    return fake.patches[-1][1]


# --- load_roster -----------------------------------------------------------


def test_load_roster_attaches_an_open_session(fake_db):
    fake_db.player_rows = [{"steam_id": KYLE, "name": "Kyle", "current_game": None}]
    fake_db.open_sessions = [
        {
            "id": 7,
            "steam_id": KYLE,
            "game_name": "Deep Rock Galactic",
            "app_id": 548430,
            "started_at": "2026-08-19 19:00:00+00",
        }
    ]

    (player,) = tracker.load_roster(None)

    assert player.open_session_id == 7
    assert player.current_game == "Deep Rock Galactic"
    assert player.session_started_at == datetime(2026, 8, 19, 19, 0, tzinfo=timezone.utc)


def test_load_roster_corrects_a_stale_cache(fake_db):
    # players says they are mid-game but no session row backs that up.
    fake_db.player_rows = [
        {
            "steam_id": KYLE,
            "name": "Kyle",
            "current_game": "Ghost Game",
            "session_started_at": "2026-08-19 19:00:00+00",
        }
    ]
    fake_db.open_sessions = []

    (player,) = tracker.load_roster(None)

    assert player.current_game is None
    assert player.open_session_id is None
    assert player.session_started_at is None


# --- no change -------------------------------------------------------------


def test_unchanged_state_only_touches_last_polled_at(fake_db):
    player = make("Helldivers 2", open_session_id=5, started_at=NOW)

    result = tracker.apply_activity(None, player, "Helldivers 2", 553850, None, NOW)

    assert result["action"] is None
    assert fake_db.opened == [] and fake_db.closed == []
    assert last_patch(fake_db) == {"last_polled_at": NOW.isoformat()}


def test_a_new_avatar_is_refreshed(fake_db):
    player = make(avatar_url="http://x/old.jpg")

    tracker.apply_activity(None, player, None, None, "http://x/new.jpg", NOW)

    assert last_patch(fake_db)["avatar_url"] == "http://x/new.jpg"
    assert player.avatar_url == "http://x/new.jpg"


def test_an_unchanged_avatar_is_not_rewritten(fake_db):
    player = make(avatar_url="http://x/same.jpg")

    tracker.apply_activity(None, player, None, None, "http://x/same.jpg", NOW)

    assert "avatar_url" not in last_patch(fake_db)


# --- started ---------------------------------------------------------------


def test_started_opens_a_session_and_caches_it(fake_db):
    player = make(None)

    result = tracker.apply_activity(None, player, "Helldivers 2", 553850, None, NOW)

    assert result["action"] == "started"
    assert fake_db.opened == [(KYLE, "Helldivers 2", 553850, NOW)]
    assert fake_db.closed == []
    assert player.open_session_id == 101
    assert last_patch(fake_db)["current_game"] == "Helldivers 2"
    assert last_patch(fake_db)["session_started_at"] == NOW.isoformat()


# --- stopped ---------------------------------------------------------------


def test_stopped_closes_the_session_and_clears_the_cache(fake_db):
    started = NOW - timedelta(hours=2)
    player = make("Deep Rock Galactic", open_session_id=7, started_at=started)

    result = tracker.apply_activity(None, player, None, None, None, NOW)

    assert result["action"] == "stopped"
    assert result["previous_game"] == "Deep Rock Galactic"
    assert result["duration"] == 7200
    assert fake_db.closed == [(7, NOW, "stopped")]
    assert fake_db.opened == []
    assert player.open_session_id is None

    patch = last_patch(fake_db)
    assert patch["current_game"] is None
    assert patch["session_started_at"] is None
    assert patch["last_game"] == "Deep Rock Galactic"


# --- switched --------------------------------------------------------------


def test_switched_closes_the_old_session_then_opens_the_new_one(fake_db):
    started = NOW - timedelta(minutes=30)
    player = make("Deep Rock Galactic", open_session_id=7, started_at=started)

    result = tracker.apply_activity(None, player, "Helldivers 2", 553850, None, NOW)

    assert result["action"] == "switched"
    assert result["previous_game"] == "Deep Rock Galactic"
    assert result["duration"] == 1800
    assert fake_db.closed == [(7, NOW, "switched")]
    assert fake_db.opened == [(KYLE, "Helldivers 2", 553850, NOW)]
    assert player.current_game == "Helldivers 2"
    assert player.open_session_id == 101


def test_switch_records_the_new_game_in_the_cache(fake_db):
    player = make("Deep Rock Galactic", open_session_id=7, started_at=NOW)

    tracker.apply_activity(None, player, "Helldivers 2", 553850, None, NOW)

    patch = last_patch(fake_db)
    assert patch["current_game"] == "Helldivers 2"
    assert patch["last_game"] == "Deep Rock Galactic"


# --- reconciliation --------------------------------------------------------


def test_reconciled_close_uses_the_last_poll_time(fake_db):
    started = NOW - timedelta(hours=3)
    last_seen = NOW - timedelta(hours=1)
    player = make("Deep Rock Galactic", open_session_id=7, started_at=started)

    result = tracker.apply_activity(
        None, player, None, None, None, NOW, closed_at=last_seen
    )

    assert result["action"] == "stopped"
    assert fake_db.closed == [(7, last_seen, "reconciled")]
    # Duration measures to the last confirmed sighting, not to now.
    assert result["duration"] == 7200


def test_stopping_without_a_recorded_session_still_clears_state(fake_db):
    player = make("Deep Rock Galactic", open_session_id=None, started_at=NOW)

    result = tracker.apply_activity(None, player, None, None, None, NOW)

    assert result["action"] == "stopped"
    assert fake_db.closed == []
    assert player.current_game is None
