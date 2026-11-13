-- R-204 live (D-153), Supabase security advisor 0011 "function_search_path_mutable":
-- the four trigger functions of 001_core.sql / 002_accounts.sql resolved names
-- through the CALLER's search_path. Pin it, like every other agos function.
-- Bodies are unchanged: they only use pg_catalog (now()) and qualified names
-- (auth.uid()), so the empty-ish path cannot change what they do.
-- Proven by tests/test_r204_accounts_db.py::test_every_agos_function_pins_its_search_path
-- and ::test_trigger_functions_still_work_with_the_pinned_path.
alter function agos.touch_updated_at()        set search_path = pg_catalog, pg_temp;
alter function agos.forbid_update()           set search_path = pg_catalog, pg_temp;
alter function agos.forbid_workspace_change() set search_path = pg_catalog, pg_temp;
alter function agos.stamp_created_by()        set search_path = pg_catalog, pg_temp;
