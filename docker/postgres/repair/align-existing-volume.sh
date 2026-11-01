#!/usr/bin/env bash
set -euo pipefail

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"

export NARA_OWNER_USER="${NARA_OWNER_USER:-nara_owner}"
export NARA_MIGRATOR_USER="${NARA_MIGRATOR_USER:-nara_migrator}"
export NARA_RUNTIME_USER="${NARA_RUNTIME_USER:-nara_runtime}"

: "${NARA_MIGRATOR_PASSWORD:?NARA_MIGRATOR_PASSWORD is required}"
: "${NARA_RUNTIME_PASSWORD:?NARA_RUNTIME_PASSWORD is required}"

for var in NARA_MIGRATOR_PASSWORD NARA_RUNTIME_PASSWORD; do
    case "${!var}" in
        *CHANGE_ME*)
            echo "$var must not use the example placeholder value" >&2
            exit 1
            ;;
    esac
done

SUPERUSER="${NARA_SUPERUSER:-$POSTGRES_USER}"

psql -v ON_ERROR_STOP=1 --username "$SUPERUSER" --dbname "$POSTGRES_DB" <<'EOSQL'
\getenv owner NARA_OWNER_USER
\getenv migrator NARA_MIGRATOR_USER
\getenv migrator_pw NARA_MIGRATOR_PASSWORD
\getenv runtime NARA_RUNTIME_USER
\getenv runtime_pw NARA_RUNTIME_PASSWORD
\getenv dbname POSTGRES_DB

SELECT format(
    'CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE',
    :'owner'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'owner')\gexec

SELECT format(
    'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L',
    :'migrator', :'migrator_pw'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'migrator')\gexec

SELECT format(
    'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L',
    :'runtime', :'runtime_pw'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'runtime')\gexec

ALTER ROLE :"owner" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION PASSWORD NULL;
ALTER ROLE :"migrator" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION PASSWORD :'migrator_pw';
ALTER ROLE :"runtime" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION PASSWORD :'runtime_pw';

ALTER DATABASE :"dbname" OWNER TO :"owner";
ALTER SCHEMA public OWNER TO :"owner";

REVOKE CREATE, TEMPORARY ON DATABASE :"dbname" FROM :"runtime";
GRANT CONNECT ON DATABASE :"dbname" TO :"owner", :"migrator", :"runtime";

REVOKE CREATE ON SCHEMA public FROM :"runtime";
REVOKE CREATE ON SCHEMA public FROM :"migrator";
GRANT USAGE ON SCHEMA public TO :"migrator";
GRANT USAGE ON SCHEMA public TO :"runtime";

GRANT :"owner" TO :"migrator" WITH INHERIT FALSE, SET TRUE;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"runtime";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"runtime";

ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"runtime";
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO :"runtime";
EOSQL
