import main
from player import Player


def make(current_game=None):
    return Player(
        steam_id="76561198098282377",
        name="Kyle",
        avatar_url="http://x/a.jpg",
        current_game=current_game,
    )


def test_started_produces_one_start_message():
    player = make("Helldivers 2")
    result = {"action": "started", "previous_game": None, "duration": None}

    (embed,) = main.embeds_for(result, player)

    assert "started playing" in embed.title
    assert "Helldivers 2" in embed.description


def test_stopped_produces_one_stop_message_with_the_session_length():
    player = make(None)
    result = {"action": "stopped", "previous_game": "Deep Rock Galactic", "duration": 7200}

    (embed,) = main.embeds_for(result, player)

    assert "stopped playing" in embed.title
    assert "Deep Rock Galactic" in embed.description
    assert embed.fields[0].value == "2h 0m"


def test_switched_produces_a_stop_then_a_start_and_no_switch_message():
    player = make("Helldivers 2")
    result = {"action": "switched", "previous_game": "Deep Rock Galactic", "duration": 1800}

    stopped, started = main.embeds_for(result, player)

    assert "stopped playing" in stopped.title
    assert "Deep Rock Galactic" in stopped.description
    assert "started playing" in started.title
    assert "Helldivers 2" in started.description
    assert "switched" not in stopped.title.lower()
    assert "switched" not in started.title.lower()
