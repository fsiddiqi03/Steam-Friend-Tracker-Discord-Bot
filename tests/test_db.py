import pytest

import db


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeClient:
    """Records rpc calls so tests can assert on what db.py sent."""

    def __init__(self, data=0):
        self.data = data
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return self

    def execute(self) -> FakeResponse:
        return FakeResponse(self.data)


def test_upsert_players_calls_the_upsert_function():
    client = FakeClient(data=2)
    rows = [
        {"steam_id": "76561198098282377", "name": "tARI0", "avatar_url": "http://x/a.jpg"},
        {"steam_id": "76561198097048604", "name": "Zoro", "avatar_url": None},
    ]

    written = db.upsert_players(client, rows)

    assert written == 2
    assert client.calls == [("upsert_players", {"rows": rows})]


def test_upsert_players_short_circuits_on_empty_input():
    client = FakeClient()

    written = db.upsert_players(client, [])

    assert written == 0
    assert client.calls == []


def test_get_client_requires_configuration(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        db.get_client()
