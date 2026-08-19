# Supabase Player Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the bot's roster and session history from `friends.json` + in-memory dicts into two Supabase tables, and ship a one-shot script that seeds the roster.

**Architecture:** `players` holds one row per tracked friend (roster + a live-state cache). `game_sessions` holds one row per play session with `ended_at IS NULL` meaning "playing right now"; a partial unique index makes "one open session per player" a database guarantee. A Postgres function `upsert_players(jsonb)` is the only write path for roster data, so refreshing names and avatars provably cannot clobber game-state columns. `seed_players.py` reads `friends.json`, fetches avatars from Steam, and calls that function.

**Tech Stack:** Python 3.14, Supabase (Postgres 17), `supabase-py`, `requests`, `pytest 9.1.1`, existing `discord.py` bot.

**Spec:** [docs/superpowers/specs/2026-08-19-supabase-player-store-design.md](../specs/2026-08-19-supabase-player-store-design.md)

## Global Constraints

- **Python style:** function *parameters* carry no type annotations. Return type annotations (`-> None`, `-> list`) are kept. This applies to every function written in this plan.
- **Supabase project id:** `kamrvulkfxbudntfthjt` (name: `Steam Flock Camera`, region us-east-2, Postgres 17).
- **Supabase URL:** `https://kamrvulkfxbudntfthjt.supabase.co`
- **Interpreter:** always `./.venv/Scripts/python.exe` (Windows venv already present at `.venv`). Never bare `python`.
- **Always run pytest as `./.venv/Scripts/python.exe -m pytest` from the repo root.** The modules under test (`db.py`, `seed_players.py`) live at the repo root and `tests/` has no `__init__.py`, so `python -m` is what puts the repo root on `sys.path`. Bare `pytest` will fail with `ModuleNotFoundError`.
- **RLS:** enabled on both tables with zero policies. The bot uses the `service_role` key, which bypasses RLS. Do not add policies in this plan.
- **The seed script must never write a game-state column** (`current_game`, `current_app_id`, `session_started_at`, `last_game`, `last_played_at`). This is the single most important invariant in the plan and is tested twice.
- **Names are user-owned.** `players.name` comes from `friends.json`, never from Steam's `personaname`.
- **Branch:** all work lands on `supabase-player-store`, which already exists and holds the spec commit.

## Prerequisite (human action, blocks Task 2)

Task 1 needs nothing from the user. **Task 2 cannot start** until this is done:

Get the service_role key from
`https://supabase.com/dashboard/project/kamrvulkfxbudntfthjt/settings/api-keys`
(the **`service_role` / secret** key — NOT the publishable/anon key, which RLS
will block), and add to `.env`:

```
SUPABASE_URL=https://kamrvulkfxbudntfthjt.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<paste the service_role key>
```

`.env` is already in `.gitignore`. Do not commit this key, and do not print it
in logs or terminal output.

---

### Task 1: Schema migration

**Files:**
- Create: `migrations/0001_players_and_game_sessions.sql`
- Create: `migrations/verify_0001.sql`
- Applied to Supabase via the `apply_migration` MCP tool (no local DB)

**Interfaces:**
- Consumes: nothing
- Produces: tables `public.players` and `public.game_sessions`; function `public.upsert_players(rows jsonb) returns integer`; function `public.touch_updated_at()`

- [ ] **Step 1: Write the migration SQL**

Create `migrations/0001_players_and_game_sessions.sql`:

```sql
-- Steam Flock Camera: roster + session history.
-- players is a cache of live state; game_sessions is the source of truth.

create table public.players (
    steam_id            text primary key,
    name                text        not null,
    avatar_url          text,
    current_game        text,
    current_app_id      integer,
    session_started_at  timestamptz,
    last_game           text,
    last_played_at      timestamptz,
    last_polled_at      timestamptz,
    is_tracked          boolean     not null default true,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),

    constraint players_steam_id_is_steamid64
        check (steam_id ~ '^[0-9]{17}$'),
    constraint players_name_not_blank
        check (length(btrim(name)) > 0),
    -- current_game and session_started_at are set and cleared together.
    constraint players_session_state_consistent
        check ((current_game is null) = (session_started_at is null))
);

comment on table public.players is
    'Tracked Steam friends. Roster is user-owned; game columns are a cache of game_sessions.';
comment on column public.players.name is
    'User-chosen label from friends.json. Never overwritten by Steam personaname.';
comment on column public.players.last_polled_at is
    'Last time Steam confirmed this state. Anchors ended_at when recovering from a crash.';

create table public.game_sessions (
    id                bigint      generated always as identity primary key,
    steam_id          text        not null
                                  references public.players(steam_id) on delete cascade,
    game_name         text        not null,
    app_id            integer,
    started_at        timestamptz not null default now(),
    ended_at          timestamptz,
    duration_seconds  integer     generated always as (
                          case
                              when ended_at is null then null
                              else extract(epoch from (ended_at - started_at))::integer
                          end
                      ) stored,
    ended_by          text,
    created_at        timestamptz not null default now(),

    constraint game_sessions_ends_after_start
        check (ended_at is null or ended_at >= started_at),
    constraint game_sessions_ended_by_valid
        check (ended_by is null or ended_by in ('stopped', 'switched', 'reconciled')),
    -- A closed session always records how it closed; an open one never does.
    constraint game_sessions_ended_by_tracks_ended_at
        check ((ended_at is null) = (ended_by is null))
);

comment on table public.game_sessions is
    'One row per play session. ended_at IS NULL means the session is live.';
comment on column public.game_sessions.ended_by is
    'stopped = quit, switched = moved straight to another game, reconciled = closed by crash recovery (duration approximate).';

-- The load-bearing invariant: a player can only be in one game at a time.
create unique index game_sessions_one_open_per_player
    on public.game_sessions (steam_id) where ended_at is null;

create index game_sessions_player_recent
    on public.game_sessions (steam_id, started_at desc);
create index game_sessions_timeline
    on public.game_sessions (started_at desc);
create index game_sessions_app
    on public.game_sessions (app_id) where app_id is not null;

create function public.touch_updated_at() returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create trigger players_touch_updated_at
    before update on public.players
    for each row execute function public.touch_updated_at();

-- The only write path for roster data. Explicitly lists the columns it may
-- touch, so refreshing a name or avatar cannot clobber game state.
create function public.upsert_players(rows jsonb) returns integer
language sql
set search_path = public, pg_temp
as $$
    with upserted as (
        insert into public.players (steam_id, name, avatar_url)
        select r->>'steam_id', r->>'name', r->>'avatar_url'
        from jsonb_array_elements(rows) as r
        on conflict (steam_id) do update
            set name       = excluded.name,
                avatar_url = excluded.avatar_url,
                is_tracked = true
        returning 1
    )
    select count(*)::integer from upserted;
$$;

comment on function public.upsert_players(jsonb) is
    'Insert or refresh roster rows (steam_id, name, avatar_url). Never writes game-state columns.';

alter table public.players       enable row level security;
alter table public.game_sessions enable row level security;
-- No policies on purpose: service_role bypasses RLS, everyone else gets nothing.
```

- [ ] **Step 2: Apply the migration**

Call the `apply_migration` MCP tool with:
- `project_id`: `kamrvulkfxbudntfthjt`
- `name`: `players_and_game_sessions`
- `query`: the full contents of `migrations/0001_players_and_game_sessions.sql`

Expected: success, no error.

If it fails on the `duration_seconds` generated column with `generation
expression is not immutable`, that is the only genuinely risky line in the
migration. Replace that column definition with a plain `integer` column and add
this trigger immediately after the `touch_updated_at` trigger, then re-apply:

```sql
create function public.set_session_duration() returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
    new.duration_seconds = case
        when new.ended_at is null then null
        else extract(epoch from (new.ended_at - new.started_at))::integer
    end;
    return new;
end;
$$;

create trigger game_sessions_set_duration
    before insert or update on public.game_sessions
    for each row execute function public.set_session_duration();
```

- [ ] **Step 3: Write the verification script**

Create `migrations/verify_0001.sql`. Every assertion raises on failure, and the
whole script rolls back, so it leaves no rows behind:

```sql
begin;

insert into public.players (steam_id, name) values ('99999999999999999', 'Verify Bot');

-- 1. duration_seconds is computed from the timestamps.
do $$
declare d integer;
begin
    insert into public.game_sessions (steam_id, game_name, started_at, ended_at, ended_by)
    values ('99999999999999999', 'Verify Game',
            now() - interval '90 minutes', now(), 'stopped')
    returning duration_seconds into d;

    if d is null or d not between 5395 and 5405 then
        raise exception 'FAIL: duration_seconds was %, expected ~5400', d;
    end if;
    raise notice 'PASS: duration_seconds computed (%)', d;
end $$;

-- 2. An open session leaves duration NULL.
do $$
declare d integer;
begin
    insert into public.game_sessions (steam_id, game_name)
    values ('99999999999999999', 'Open Game')
    returning duration_seconds into d;

    if d is not null then
        raise exception 'FAIL: open session had duration %', d;
    end if;
    raise notice 'PASS: open session duration is NULL';
end $$;

-- 3. A second open session for the same player is rejected.
do $$
begin
    insert into public.game_sessions (steam_id, game_name)
    values ('99999999999999999', 'Second Open Game');
    raise exception 'FAIL: two open sessions were allowed';
exception
    when unique_violation then
        raise notice 'PASS: second open session rejected';
end $$;

-- 4. A malformed steam_id is rejected.
do $$
begin
    insert into public.players (steam_id, name) values ('not-a-steamid', 'Nope');
    raise exception 'FAIL: bad steam_id accepted';
exception
    when check_violation then
        raise notice 'PASS: bad steam_id rejected';
end $$;

-- 5. A closed session must say how it closed.
do $$
begin
    insert into public.game_sessions (steam_id, game_name, started_at, ended_at)
    values ('99999999999999999', 'No Reason', now() - interval '5 minutes', now());
    raise exception 'FAIL: closed session without ended_by accepted';
exception
    when check_violation then
        raise notice 'PASS: closed session requires ended_by';
end $$;

-- 6. upsert_players refreshes roster fields and leaves game state alone.
do $$
declare
    n        integer;
    got_name text;
    got_av   text;
    got_game text;
begin
    update public.players
       set current_game = 'Deep Rock Galactic',
           current_app_id = 548430,
           session_started_at = now()
     where steam_id = '99999999999999999';

    select public.upsert_players(
        '[{"steam_id":"99999999999999999","name":"Renamed","avatar_url":"http://x/a.jpg"}]'::jsonb
    ) into n;

    select name, avatar_url, current_game
      into got_name, got_av, got_game
      from public.players where steam_id = '99999999999999999';

    if n <> 1 then
        raise exception 'FAIL: upsert_players returned %, expected 1', n;
    end if;
    if got_name <> 'Renamed' or got_av <> 'http://x/a.jpg' then
        raise exception 'FAIL: roster not refreshed (name=%, avatar=%)', got_name, got_av;
    end if;
    if got_game is distinct from 'Deep Rock Galactic' then
        raise exception 'FAIL: upsert_players clobbered current_game (now %)', got_game;
    end if;
    raise notice 'PASS: upsert_players refreshed roster, preserved game state';
end $$;

-- 7. Deleting a player takes their sessions with them.
do $$
declare remaining integer;
begin
    delete from public.players where steam_id = '99999999999999999';
    select count(*) into remaining
      from public.game_sessions where steam_id = '99999999999999999';
    if remaining <> 0 then
        raise exception 'FAIL: % sessions survived player delete', remaining;
    end if;
    raise notice 'PASS: sessions cascade on player delete';
end $$;

rollback;
```

- [ ] **Step 4: Run the verification script**

Call the `execute_sql` MCP tool with `project_id` `kamrvulkfxbudntfthjt` and the
full contents of `migrations/verify_0001.sql`.

Expected: completes without error. Any `FAIL:` exception means the migration is
wrong — fix the migration and re-apply before continuing.

- [ ] **Step 5: Confirm RLS is on and the tables are empty**

Call `execute_sql` with:

```sql
select relname, relrowsecurity,
       (select count(*) from pg_policy p where p.polrelid = c.oid) as policies
from pg_class c
where relname in ('players', 'game_sessions');
```

Expected: both rows show `relrowsecurity = true` and `policies = 0`.

- [ ] **Step 6: Commit**

```bash
git add migrations/
git commit -m "feat: add players and game_sessions schema"
```

---

### Task 2: Database access module

**Prerequisite:** the `.env` values from the "Prerequisite" section above must be in place.

**Files:**
- Create: `db.py`
- Create: `requirements.txt`
- Create: `tests/test_db.py`
- Modify: `config.py` (append Supabase settings)
- Modify: `.env.example` (append a Supabase section)

**Interfaces:**
- Consumes: `public.upsert_players(jsonb)` from Task 1
- Produces:
  - `db.get_client() -> Client`
  - `db.upsert_players(client, rows) -> int` — `rows` is a list of dicts with keys `steam_id`, `name`, `avatar_url`; returns the number of rows written

- [ ] **Step 1: Install the dependency and write requirements.txt**

```bash
./.venv/Scripts/python.exe -m pip install supabase
```

Then create `requirements.txt`, replacing `<installed>` with the version that
`./.venv/Scripts/python.exe -m pip show supabase` reports:

```
discord.py==2.7.1
python-dotenv==1.2.2
requests==2.34.2
supabase==<installed>
```

- [ ] **Step 2: Append the Supabase settings to `config.py`**

Add to the end of `config.py`:

```python
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
```

- [ ] **Step 3: Append the Supabase section to `.env.example`**

```
# --- Supabase --------------------------------------------------------------
# Project URL and the service_role (secret) key from
# https://supabase.com/dashboard/project/kamrvulkfxbudntfthjt/settings/api-keys
# Must be service_role, not the publishable/anon key -- RLS is on with no
# policies, so anon can neither read nor write. Never commit the real value.
SUPABASE_URL=https://kamrvulkfxbudntfthjt.supabase.co
SUPABASE_SERVICE_ROLE_KEY=
```

- [ ] **Step 4: Write the failing tests**

Create `tests/test_db.py`:

```python
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
```

- [ ] **Step 5: Run the tests to verify they fail**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_db.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 6: Write `db.py`**

```python
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
```

Note `get_client` reads `os.getenv` directly rather than importing from
`config`, so the `monkeypatch.delenv` test works without reloading modules.

- [ ] **Step 7: Run the tests to verify they pass**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_db.py -v
```

Expected: 3 passed.

- [ ] **Step 8: Commit**

```bash
git add db.py requirements.txt config.py .env.example tests/test_db.py
git commit -m "feat: add Supabase client and roster upsert"
```

---

### Task 3: Seed script

**Files:**
- Create: `seed_players.py`
- Create: `tests/test_seed_players.py`

**Interfaces:**
- Consumes: `db.get_client()`, `db.upsert_players(client, rows)` from Task 2; `SteamClient.load_friends()` and `SteamClient.get_summaries(ids)` from the existing `steam.py`
- Produces: `seed_players.build_player_rows(friends, summaries) -> list` and a `python seed_players.py [--dry-run]` entry point

- [ ] **Step 1: Write the failing tests**

Create `tests/test_seed_players.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
./.venv/Scripts/python.exe -m pytest tests/test_seed_players.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'seed_players'`

- [ ] **Step 3: Write `seed_players.py`**

```python
"""One-shot: load the friends.json roster into the Supabase players table.

Safe to re-run. Edit friends.json, run this again, and the roster catches up.
Deliberately writes no game state -- the bot owns those columns.
"""

import argparse
import json
import logging

import db
from config import FRIENDS_FILE, LOG_LEVEL, STEAM_API_KEY, STEAM_ID
from steam import SteamClient

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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
./.venv/Scripts/python.exe -m pytest tests/ -v
```

Expected: 8 passed (3 from Task 2, 5 from this task).

- [ ] **Step 5: Dry-run against the real Steam API**

```bash
./.venv/Scripts/python.exe seed_players.py --dry-run
```

Expected: JSON for 5 players. Check by eye that `name` matches `friends.json`
("tARI0", "Zoro", "Soapbox03", "Kronk", "Devops Engineer") and that no key
other than `steam_id`, `name`, `avatar_url` appears.

- [ ] **Step 6: Seed for real**

```bash
./.venv/Scripts/python.exe seed_players.py
```

Expected: `Seeded 5 players`

- [ ] **Step 7: Verify the rows landed and game state is NULL**

Call `execute_sql` with `project_id` `kamrvulkfxbudntfthjt`:

```sql
select steam_id, name, avatar_url is not null as has_avatar,
       current_game, session_started_at, last_game, is_tracked
from public.players
order by name;
```

Expected: 5 rows; `current_game`, `session_started_at` and `last_game` all NULL;
`is_tracked` true for all.

- [ ] **Step 8: Verify re-running is idempotent**

```bash
./.venv/Scripts/python.exe seed_players.py
```

Then `execute_sql`:

```sql
select count(*) as players, count(distinct steam_id) as distinct_ids from public.players;
```

Expected: `players = 5`, `distinct_ids = 5` — no duplicates.

- [ ] **Step 9: Commit**

```bash
git add seed_players.py tests/test_seed_players.py
git commit -m "feat: add seed_players script to backfill roster from friends.json"
```

---

### Task 4: Strip parameter type annotations from existing modules

Independent cleanup applying the project's Python style to code written before
the rule existed. Skippable without affecting Tasks 1-3.

**Heads up:** `main.py` has an uncommitted working-tree change (the removed
`elif game and previous` branch). Confirm with Faris whether that deletion is
intentional before touching the file, and do not commit it as part of this task.

**Files:**
- Modify: `steam.py` (5 functions)
- Modify: `embeds.py` (4 functions)
- Modify: `main.py` (no annotated params — verify only)

**Interfaces:**
- Consumes: nothing
- Produces: no signature changes beyond removing annotations; all call sites keep working

- [ ] **Step 1: Confirm the tests pass before changing anything**

```bash
./.venv/Scripts/python.exe -m pytest tests/ -v
```

Expected: 8 passed. This is the baseline.

- [ ] **Step 2: Edit `steam.py`**

Remove parameter annotations only; keep every `->` return type:

```python
    def __init__(self, api_key, steam_id, friends_file):
    def _get(self, path, params) -> dict:
    def get_friend_ids(self) -> list[str]:
    def get_summaries(self, ids) -> dict[str, dict]:
    def load_friends(self) -> dict[str, str]:
    def get_friend_activity(self) -> list[dict]:
```

- [ ] **Step 3: Edit `embeds.py`**

```python
def _base(title, description, color, avatar) -> discord.Embed:
def format_duration(seconds) -> str:
def started_playing(name, game, avatar) -> discord.Embed:
def switched_game(name, old_game, new_game, avatar) -> discord.Embed:
def stopped_playing(name, game, avatar, duration) -> discord.Embed:
```

- [ ] **Step 4: Check `main.py`**

Its functions (`check_friends`, `poll_friends`, `before_poll`, `on_ready`,
`main`) take no parameters, so only the module-level variable annotations
`last_game: dict[str, str | None]` and `started_at: dict[str, float]` carry
types. Those are variables, not parameters — **leave them alone**.

- [ ] **Step 5: Verify nothing broke**

```bash
./.venv/Scripts/python.exe -m pytest tests/ -v
./.venv/Scripts/python.exe -c "import main, steam, embeds, db, seed_players; print('imports ok')"
```

Expected: 8 passed, then `imports ok`.

- [ ] **Step 6: Commit**

```bash
git add steam.py embeds.py
git commit -m "style: drop parameter type annotations"
```

---

## Done when

- `players` and `game_sessions` exist with RLS on and zero policies
- `migrations/verify_0001.sql` runs clean against the live database
- 8 tests pass
- All 5 friends are in `players` with names from `friends.json` and every
  game-state column NULL
- Re-running `seed_players.py` changes no row count

## Not in this plan

Rewiring `main.py` / `steam.py` to read the roster from `players`, write
`game_sessions` rows, reconcile open sessions on startup, and fix the A-to-B
switch announcement. The bot keeps running off `friends.json` until that work
is planned separately.
