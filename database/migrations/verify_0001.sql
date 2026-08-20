-- Asserts the invariants of 0001. Every check raises on failure and the whole
-- script rolls back, so it leaves no rows behind. Run it against any
-- environment to confirm the schema behaves.

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
