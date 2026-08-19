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
