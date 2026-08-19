import seed_players

GAME_STATE_COLUMNS = {
    "current_game",
    "current_app_id",
    "session_started_at",
    "last_game",
    "last_played_at",
    "last_polled_at",
}


def summary(steam_id, **extra):
    base = {"steamid": steam_id, "personaname": "SteamName", "avatarfull": "http://x/full.jpg"}
    base.update(extra)
    return base


def test_builds_one_row_per_friend():
    friends = {"76561198098282377": "tARI0", "76561198097048604": "Zoro"}
    summaries = {sid: summary(sid) for sid in friends}

    rows = seed_players.build_player_rows(friends, summaries)

    assert [r["steam_id"] for r in rows] == list(friends)


def test_name_comes_from_friends_file_not_steam():
    friends = {"76561198199123090": "Devops Engineer"}
    summaries = {"76561198199123090": summary("76561198199123090", personaname="xX_sniper_Xx")}

    rows = seed_players.build_player_rows(friends, summaries)

    assert rows[0]["name"] == "Devops Engineer"


def test_prefers_full_avatar_but_falls_back_to_small():
    friends = {"76561198111510802": "Soapbox03"}
    summaries = {
        "76561198111510802": {
            "steamid": "76561198111510802",
            "avatar": "http://x/small.jpg",
        }
    }

    rows = seed_players.build_player_rows(friends, summaries)

    assert rows[0]["avatar_url"] == "http://x/small.jpg"


def test_friend_with_no_summary_is_still_seeded():
    friends = {"76561198113372399": "Kronk"}

    rows = seed_players.build_player_rows(friends, {})

    assert len(rows) == 1
    assert rows[0]["name"] == "Kronk"
    assert rows[0]["avatar_url"] is None


def test_never_writes_game_state():
    friends = {"76561198098282377": "tARI0"}
    summaries = {
        "76561198098282377": summary("76561198098282377", gameextrainfo="Deep Rock Galactic")
    }

    rows = seed_players.build_player_rows(friends, summaries)

    assert set(rows[0]) == {"steam_id", "name", "avatar_url"}
    assert not GAME_STATE_COLUMNS & set(rows[0])
