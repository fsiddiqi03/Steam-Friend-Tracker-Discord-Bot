# Supabase Player Store & Session History — Design

**Date:** 2026-08-19
**Status:** Approved
**Scope:** Two table schemas + a one-shot seed script. Rewiring the bot to read
and write these tables is separate follow-up work.

## Problem

The bot tracks who to watch in `friends.json` and holds all live state in
process memory (`last_game`, `started_at` in `main.py`). Three consequences:

1. **State dies on restart.** Session timing uses `time.monotonic()`, which
   resets every process start. A restart mid-session loses the start time and
   re-announces everyone as freshly "started playing".
2. **No history.** Nothing is retained after a session ends, so stats,
   leaderboards, and any future dashboard are impossible.
3. **A live bug.** `main.py` branches on `if game and previous is None`. A
   direct switch from game A to game B has both `game` and `previous` truthy,
   so it falls to the `else` branch: it posts "stopped playing A", sets
   `last_game` to B, and never posts a start for B. The `switched_game` embed
   in `embeds.py` exists but is never called.

The Supabase project `Steam Flock Camera` (`kamrvulkfxbudntfthjt`, us-east-2,
Postgres 17) is empty and will hold this data.

## Goals

Support all four of these:

- Stats and leaderboards (total hours, top games, longest session)
- A readable audit trail of what happened
- Restart recovery without duplicate announcements or lost session time
- A future dashboard / charts

## Decisions

### The database owns the roster

`players` is the source of truth for who the bot tracks. `friends.json` becomes
a hand-editable seed file: edit it, re-run the seed script, done. It is not
deleted and it is not read at runtime once the bot migrates.

### History is session rows, not an event log

Rejected: an event log of `(steam_id, game, action, occurred_at)` rows with
`action` in `start` / `stop`.

Chosen: one row per session carrying both `started_at` and `ended_at`.

Rationale:

- Aggregation is a plain `SUM ... GROUP BY` instead of a window function that
  pairs each start with its matching stop.
- An unpaired `start` row in the event model is unpairable forever and silently
  drops a real session from every stat. The session model makes the same
  situation visible as `ended_at IS NULL` and repairable.
- Restart recovery is a single query: `WHERE ended_at IS NULL`.
- A partial unique index can enforce "one open session per player" at the
  database level. The event model has no equivalent invariant.
- Ordering sessions by `started_at` is still a perfectly readable audit trail,
  so the event model's one advantage is not actually given up.

Also rejected: keeping both tables. The event table earns nothing that ordered
session rows do not already provide, at the cost of two things to keep in sync.

### Names are owned by the user, avatars are refreshed

A single `name` column, written by the seed script from `friends.json` and never
touched by the poll loop. The names in `friends.json` are labels Faris chose,
not Steam personanames, and must not be overwritten by Steam data.

`avatar_url` is the deliberate exception: it is refreshed on poll. Steam avatar
URLs are content-hashed, so when someone changes their profile picture the
stored URL starts 404ing and embed thumbnails break. The value is already
present in the `GetPlayerSummaries` response the bot reads for game state, so
refreshing it costs no extra request.

Dropped from consideration: `persona_name` (would fight the user's names),
`persona_state` (not needed by any stated goal), `profile_url` (derivable —
`https://steamcommunity.com/profiles/{steam_id}` always resolves).

### Restart behaviour: resume silently

On restart, an open session row is adopted as-is. Nothing is posted, and the
original `started_at` is kept, so a session spanning a restart reports its true
total length when it finally ends.

If the open row's game does not match what Steam currently reports, the bot
closes it with `ended_at = last_polled_at` and `ended_by = 'reconciled'`, then
opens a new session. `last_polled_at` is the last moment Steam confirmed that
state, which is the most defensible end time available after a crash.

### `players` is a cache; `game_sessions` is the truth

`current_game`, `current_app_id`, and `session_started_at` duplicate what the
open session row already says. This is intentional: one `SELECT * FROM players`
returns complete live state for the whole flock, one row per person, which is
exactly what a "now playing" dashboard panel and bot startup both want.

If the two ever disagree, `game_sessions` wins.

## Schema

### `players`

| Column | Type | Notes |
|---|---|---|
| `steam_id` | `text` PK | SteamID64, 17 digits, `CHECK` enforced |
| `name` | `text` NOT NULL | User-owned. Seed script writes it; poll loop never touches it |
| `avatar_url` | `text` | `avatarfull`; refreshed each poll |
| `current_game` | `text` | NULL = not playing |
| `current_app_id` | `int` | NULL when Steam omits it |
| `session_started_at` | `timestamptz` | Wall clock, not `monotonic()` |
| `last_game` | `text` | Last completed game |
| `last_played_at` | `timestamptz` | When that session ended |
| `last_polled_at` | `timestamptz` | Last Steam confirmation; anchors crash recovery |
| `is_tracked` | `bool` NOT NULL default `true` | Soft-remove, preserves history |
| `created_at` | `timestamptz` NOT NULL default `now()` | |
| `updated_at` | `timestamptz` NOT NULL default `now()` | Maintained by trigger |

`steam_id` is `text` rather than `bigint`: Steam returns it as a string, and a
17-digit integer exceeds `Number.MAX_SAFE_INTEGER`, so any future JS dashboard
doing `JSON.parse` would silently corrupt it.

### `game_sessions`

| Column | Type | Notes |
|---|---|---|
| `id` | `bigint` identity PK | |
| `steam_id` | `text` NOT NULL FK -> `players(steam_id)` | `ON DELETE CASCADE` |
| `game_name` | `text` NOT NULL | Denormalized, no `games` table |
| `app_id` | `int` | Nullable; absent for non-Steam games |
| `started_at` | `timestamptz` NOT NULL default `now()` | |
| `ended_at` | `timestamptz` | **NULL = currently playing** |
| `duration_seconds` | `int` GENERATED STORED | Computed from the timestamps; cannot drift |
| `ended_by` | `text` | `stopped` / `switched` / `reconciled`, CHECK constrained |
| `created_at` | `timestamptz` NOT NULL default `now()` | |

Constraints and indexes:

- `CHECK (ended_at IS NULL OR ended_at >= started_at)`
- `CREATE UNIQUE INDEX ... ON game_sessions (steam_id) WHERE ended_at IS NULL`
  — the load-bearing invariant. A player can only be in one game at a time, so a
  double-fired poll or an accidental second bot instance errors on insert rather
  than corrupting history. This is the structural fix for the A-to-B switch bug.
- `(steam_id, started_at DESC)` for per-player history
- `(started_at DESC)` for the global timeline
- `(app_id)` for per-game aggregation

`game_name` is stored per row rather than referencing a `games` table: a JOIN on
every query is not worth a few hundred KB at this scale, and Steam sometimes
reports a game name with no `app_id` at all, which a foreign key could not
represent.

`ended_by` distinguishes a real quit from an inferred one. Sessions closed by
crash recovery are marked `reconciled`, so approximate durations are labeled
rather than silently poisoning the leaderboard.

### Security

RLS enabled on both tables with **no policies**. The bot connects with the
service_role key, which bypasses RLS; anon and public get nothing. A read policy
gets added deliberately when the dashboard is built.

## Seed script — `seed_players.py`

Standalone one-shot, not part of the bot runtime.

1. Read `friends.json` into a steam_id-to-name mapping
2. One `GetPlayerSummaries` call (5 friends fit in a single 100-ID batch) for
   `avatarfull`
3. Upsert `steam_id`, `name`, `avatar_url`, `is_tracked = true`

**It deliberately writes no game-state column.** `current_game`,
`session_started_at`, and the `last_*` fields stay NULL. Writing `current_game`
at seed time would have `players` claiming a session that `game_sessions` has no
row for. Leaving it NULL means the bot's first poll sees NULL changing to a real
game, announces the start, and opens a proper session row.

**Idempotent.** `ON CONFLICT (steam_id) DO UPDATE` refreshes name and avatar and
never touches game state. Re-running after editing `friends.json` is the
intended way to add people.

**Private or deleted profiles** return no summary. They are still inserted, with
a NULL avatar and a warning. A missing avatar is not a reason to skip a friend.

## Supporting changes

- Add `requirements.txt` — the repo has none, and this introduces `supabase`
- Add `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` to `.env` and `.env.example`
  (service_role, needed to write past RLS; stays local)

## Code style

Python function parameters carry no type annotations. Return type annotations
are kept.

## Out of scope

Rewiring `main.py` / `steam.py` to read the roster from `players`, write
`game_sessions` rows, reconcile on startup, and fix the A-to-B switch
announcement. Tracked as follow-up work with its own plan. The bot continues
running off `friends.json` until then.
