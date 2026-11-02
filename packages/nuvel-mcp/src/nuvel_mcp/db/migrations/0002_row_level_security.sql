-- Row-level security on every table in the schema, forced so it binds the
-- owner too. The one policy per table lets the owning role (the role
-- nuvel-mcp connects as) do everything; any other role -- Neon's Data API, a
-- read-only analyst login, a leaked low-privilege credential -- reads and
-- writes nothing, even if someone grants it table privileges later.
-- Superusers and BYPASSRLS roles still bypass RLS, as Postgres always allows.
-- A later migration that creates a table must enable and force RLS on it too
-- (test_every_table_forces_row_level_security fails otherwise).
DO $$
DECLARE
    t record;
BEGIN
    FOR t IN
        SELECT c.oid::regclass AS rel, pg_get_userbyid(c.relowner) AS owner
        FROM pg_class c
        WHERE c.relnamespace = '{schema}'::regnamespace AND c.relkind IN ('r', 'p')
    LOOP
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t.rel);
        EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', t.rel);
        EXECUTE format('CREATE POLICY owner_all ON %s TO %I USING (true) WITH CHECK (true)',
                       t.rel, t.owner);
    END LOOP;

    -- A view runs with its owner's rights by default, which would read
    -- straight past the policies above. security_invoker needs Postgres 15+.
    IF current_setting('server_version_num')::int >= 150000 THEN
        ALTER VIEW {schema}.usage_daily SET (security_invoker = on);
        ALTER VIEW {schema}.skill_runs_daily SET (security_invoker = on);
    END IF;
END $$;

REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM PUBLIC;
REVOKE ALL ON SCHEMA {schema} FROM PUBLIC;
