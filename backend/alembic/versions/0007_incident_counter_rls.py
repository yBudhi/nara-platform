"""Incident counter row-level security

Revision ID: 0007_incident_counter_rls
Revises: 0006_incidents
Create Date: 2026-11-01 00:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_incident_counter_rls"
down_revision: str | None = "0006_incidents"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE incident_counters ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE incident_counters FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY incident_counters_company_select ON incident_counters
        FOR SELECT
        USING (
            company_id = NULLIF(current_setting('app.current_company_id', true), '')::uuid
        )
        """
    )
    op.execute(
        """
        CREATE POLICY incident_counters_company_insert ON incident_counters
        FOR INSERT
        WITH CHECK (
            company_id = NULLIF(current_setting('app.current_company_id', true), '')::uuid
        )
        """
    )
    op.execute(
        """
        CREATE POLICY incident_counters_company_update ON incident_counters
        FOR UPDATE
        USING (
            company_id = NULLIF(current_setting('app.current_company_id', true), '')::uuid
        )
        WITH CHECK (
            company_id = NULLIF(current_setting('app.current_company_id', true), '')::uuid
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS incident_counters_company_update "
        "ON incident_counters"
    )
    op.execute(
        "DROP POLICY IF EXISTS incident_counters_company_insert "
        "ON incident_counters"
    )
    op.execute(
        "DROP POLICY IF EXISTS incident_counters_company_select "
        "ON incident_counters"
    )
    op.execute("ALTER TABLE incident_counters NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE incident_counters DISABLE ROW LEVEL SECURITY")
