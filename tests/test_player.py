from datetime import datetime, timedelta, timezone

import pytest

from player import Player, parse_timestamp, transition_for

NOW = datetime(2026, 8, 19, 21, 0, tzinfo=timezone.utc)


def make(current_game=None, **kw):
    return Player(steam_id="76561198098282377", name="Kyle", current_game=current_game, **kw)


@pytest.mark.parametrize(
    "raw",
    [
        "2026-08-19T21:00:00+00:00",
        "2026-08-19 21:00:00+00",
        "2026-08-19T21:00:00Z",
    ],
)
def test_parse_timestamp_handles_supabase_formats(raw):
    assert parse_timestamp(raw) == NOW


def test_parse_timestamp_passes_through_none_and_datetimes():
    assert parse_timestamp(None) is None
    assert parse_timestamp(NOW) is NOW


def test_naive_timestamps_are_assumed_utc():
    assert parse_timestamp("2026-08-19T21:00:00") == NOW


def test_from_row_hydrates_and_parses():
    player = Player.from_row(
        {
            "steam_id": "76561198098282377",
            "name": "Kyle",
            "avatar_url": "http://x/a.jpg",
            "current_game": "Deep Rock Galactic",
            "current_app_id": 548430,
            "session_started_at": "2026-08-19 21:00:00+00",
        }
    )

    assert player.name == "Kyle"
    assert player.is_playing
    assert player.session_started_at == NOW


def test_from_row_tolerates_a_bare_roster_row():
    player = Player.from_row({"steam_id": "76561198113372399", "name": "Tyler"})

    assert not player.is_playing
    assert player.session_started_at is None


def test_session_length_measures_from_the_start():
    player = make("Deep Rock Galactic", session_started_at=NOW)

    assert player.session_length(NOW + timedelta(hours=2)) == 7200


def test_session_length_is_none_without_a_start():
    assert make("Deep Rock Galactic").session_length(NOW) is None


def test_no_change_when_the_game_is_the_same():
    assert transition_for(make("Helldivers 2"), "Helldivers 2") is None


def test_no_change_when_still_idle():
    assert transition_for(make(None), None) is None


def test_started_when_going_from_idle_to_a_game():
    assert transition_for(make(None), "Helldivers 2") == "started"


def test_stopped_when_going_from_a_game_to_idle():
    assert transition_for(make("Helldivers 2"), None) == "stopped"


def test_switched_when_moving_straight_between_games():
    # The case the old loop mishandled: it announced a stop and lost the start.
    assert transition_for(make("Deep Rock Galactic"), "Helldivers 2") == "switched"
