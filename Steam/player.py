from datetime import datetime, timezone


def parse_timestamp(value) -> datetime | None:
    """Postgres timestamptz as Python datetime, always timezone-aware.

    Supabase hands these back as ISO strings, sometimes space-separated and
    sometimes with a bare '+00' offset that fromisoformat won't take.
    """
    if value is None or isinstance(value, datetime):
        return value

    text = value.strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    elif len(text) > 3 and text[-3] in "+-":
        text = text + ":00"

    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class Player:
    """One tracked friend, hydrated from the database at startup.

    Holds the last state we saw so the poll loop can diff against it without
    re-reading the database every tick.
    """

    def __init__(
        self,
        steam_id,
        name,
        avatar_url=None,
        current_game=None,
        current_app_id=None,
        session_started_at=None,
        open_session_id=None,
        last_polled_at=None,
    ):
        self.steam_id = steam_id
        self.name = name
        self.avatar_url = avatar_url
        self.current_game = current_game
        self.current_app_id = current_app_id
        self.session_started_at = parse_timestamp(session_started_at)
        self.open_session_id = open_session_id
        # Last time Steam confirmed this state. Anchors ended_at when a session
        # turns out to have finished while the bot was down.
        self.last_polled_at = parse_timestamp(last_polled_at)

    @classmethod
    def from_row(cls, row) -> "Player":
        return cls(
            steam_id=row["steam_id"],
            name=row["name"],
            avatar_url=row.get("avatar_url"),
            current_game=row.get("current_game"),
            current_app_id=row.get("current_app_id"),
            session_started_at=row.get("session_started_at"),
            last_polled_at=row.get("last_polled_at"),
        )

    @property
    def is_playing(self) -> bool:
        return self.current_game is not None

    def session_length(self, until) -> float | None:
        """Seconds in the current game, or None if we never saw it start."""
        if self.session_started_at is None:
            return None
        return (until - self.session_started_at).total_seconds()

    def __repr__(self) -> str:
        game = self.current_game or "idle"
        return f"<Player {self.name} ({self.steam_id}) {game}>"


def transition_for(player, live_game) -> str | None:
    """What changed between what we last saw and what Steam reports now.

    Returns 'started', 'stopped', 'switched', or None for no change. The
    switched case is the one the old in-memory loop got wrong: it treated a
    direct A -> B move as a plain stop and never announced B.
    """
    if live_game == player.current_game:
        return None
    if player.current_game is None:
        return "started"
    if live_game is None:
        return "stopped"
    return "switched"
