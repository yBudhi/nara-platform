# PostgreSQL roles and ownership

NARA uses three roles:

| Role | Login | Purpose |
| --- | --- | --- |
| `nara_owner` | no | Owns the database, `public` schema, and all application objects. No password. |
| `nara_migrator` | yes | Non-superuser migration login. May `SET ROLE nara_owner`; has no direct schema `CREATE` privilege. |
| `nara_runtime` | yes | Restricted FastAPI login. DML on application tables/sequences only. |

Alembic connects as `nara_migrator`, verifies the connection user, then runs every migration inside a transaction with `SET LOCAL ROLE nara_owner`. Objects created by migrations are therefore owned by `nara_owner`, and default privileges for `nara_owner` grant `nara_runtime` DML on future objects.

PostgreSQL 16 or newer is required for `GRANT ... WITH INHERIT FALSE, SET TRUE`.

## Fresh volume

Docker runs `docker/postgres/init/01-provision-roles.sh` only when the data directory is empty. The script is idempotent and:

1. Creates/enforces the three roles.
2. Makes `nara_owner` the database and `public` schema owner.
3. Grants `nara_migrator` membership in `nara_owner` with `SET TRUE` and `INHERIT FALSE`.
4. Grants `nara_runtime` DML on existing tables/sequences.
5. Sets default privileges for future objects created by `nara_owner`.

Procedure:

```bash
docker compose up -d db
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\du"'
docker compose up -d migrate
docker compose logs migrate
docker compose up -d api
```

`api` is configured to wait for `migrate` to complete successfully when started with `docker compose up`.

## Existing volume (for example VM104)

**Init scripts do not run on an existing volume.** The repair script must be run manually, before starting `migrate`.

Current VM104 state differs from a fresh volume: `nara_app` is the bootstrap superuser, `nara_owner` and `nara_migrator` are `NOLOGIN`, and `nara_runtime` is a login role. The repair script fixes role attributes, passwords, membership, ownership, grants, and default privileges without deleting data.

1. Stop the application services (not the database) and take a backup first.

   ```bash
   docker compose stop migrate api
   docker compose exec -T db sh -c 'pg_dump -U nara_app -Fc "$POSTGRES_DB"' > nara-pre-role-repair.dump
   ```

2. Inspect roles, database owner, schema owner, and table owners.

   ```sql
   SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole
   FROM pg_roles
   WHERE rolname IN ('nara_app', 'nara_owner', 'nara_migrator', 'nara_runtime');

   SELECT datname, pg_get_userbyid(datdba) AS owner
   FROM pg_database WHERE datname = current_database();

   SELECT nspname, pg_get_userbyid(nspowner) AS owner
   FROM pg_namespace WHERE nspname = 'public';

   SELECT schemaname, tablename, tableowner
   FROM pg_tables WHERE schemaname = 'public';
   ```

3. Run the manual repair as the existing superuser (`nara_app`). The db container must have real `NARA_MIGRATOR_PASSWORD` and `NARA_RUNTIME_PASSWORD` values from the deployment `.env`; the scripts refuse example `CHANGE_ME` values.

   ```bash
   docker compose up -d db
   docker compose exec -e NARA_SUPERUSER=nara_app db /opt/nara/repair/align-existing-volume.sh
   ```

   `docker compose up -d db` may recreate the database container if its configuration changed, but it keeps the `nara_pgdata` volume. The repair script is idempotent. It does not drop objects, revoke `PUBLIC` database/schema access, or change table data.

4. If any existing table or sequence in `public` is not owned by `nara_owner`, reassign it before migrating. Review the list from step 2 first.

   ```sql
   SELECT format('ALTER TABLE public.%I OWNER TO %I', tablename, 'nara_owner')
   FROM pg_tables
   WHERE schemaname = 'public' AND tableowner <> 'nara_owner'\gexec

   SELECT format('ALTER SEQUENCE public.%I OWNER TO %I', sequence_name, 'nara_owner')
   FROM information_schema.sequences
   WHERE sequence_schema = 'public' AND sequence_name IS NOT NULL\gexec
   ```

5. Verify role membership and grants.

   ```sql
   SELECT r.rolname AS member, g.rolname AS role, m.inherit_option, m.set_option
   FROM pg_auth_members m
   JOIN pg_roles r ON r.oid = m.member
   JOIN pg_roles g ON g.oid = m.roleid
   WHERE r.rolname = 'nara_migrator' AND g.rolname = 'nara_owner';

   SELECT grantee, table_name, privilege_type
   FROM information_schema.role_table_grants
   WHERE table_schema = 'public' AND grantee = 'nara_runtime'
   ORDER BY table_name, privilege_type;
   ```

6. Run migrations, then start the API.

   ```bash
   docker compose up -d migrate
   docker compose logs migrate
   docker compose up -d api
   ```

## Safety

- Never run `docker compose down -v`; it deletes `nara_pgdata`.
- Do not recreate the volume to apply role changes.
- Do not run the fresh-init script expecting it to repair an existing volume.
- Do not run `alembic downgrade` as a deployment rollback. Roll back the application image instead unless a database downgrade has been explicitly planned.
- Keep passwords in environment/secret storage; the scripts read them via `\getenv` and do not print them.
