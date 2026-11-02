-- Usage insights: which client app a session uses, and the caller's country.
--
-- client_name / client_version / protocol_version are the one caller-supplied
-- text in usage_events: the session's initialize clientInfo.name / .version
-- and protocolVersion, sanitized by the server (at most 64 characters of
-- [A-Za-z0-9 ._:/@+()-], anything else stored as 'unknown'). method and tool
-- stay names the server knows. country is a two-letter ISO code looked up
-- from the caller's address, which is never stored (not even hashed).
ALTER TABLE {schema}.usage_events
    ADD COLUMN client_name       text CHECK (char_length(client_name) <= 64),
    ADD COLUMN client_version    text CHECK (char_length(client_version) <= 64),
    ADD COLUMN protocol_version  text CHECK (char_length(protocol_version) <= 64),
    ADD COLUMN country           text CHECK (country ~ '^[A-Z]{2}$');

-- usage_daily gains client_name. Recreated, so security_invoker is set again
-- exactly as 0002 does.
DROP VIEW {schema}.usage_daily;
CREATE VIEW {schema}.usage_daily AS
SELECT date_trunc('day', at) AS day,
       tenant,
       client_name,
       method,
       tool,
       count(*) AS calls,
       count(*) FILTER (WHERE outcome <> 'ok') AS errors,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms
FROM {schema}.usage_events
GROUP BY 1, 2, 3, 4, 5;

DO $$
BEGIN
    IF current_setting('server_version_num')::int >= 150000 THEN
        ALTER VIEW {schema}.usage_daily SET (security_invoker = on);
    END IF;
END $$;

REVOKE ALL ON {schema}.usage_daily FROM PUBLIC;
