-- 0001 created these tables as the postgres role, which leaves service_role
-- with no table privileges -- the bot's first write got 42501. Grant them
-- explicitly. anon and authenticated are deliberately left with nothing.

grant select, insert, update, delete on public.players       to service_role;
grant select, insert, update, delete on public.game_sessions to service_role;

grant execute on function public.upsert_players(jsonb) to service_role;
