"""unique indexes for AgentEvent sequence and Report.run_id

Revision ID: b7e4c2a19f08
Revises: c18a0e9b4d21
Create Date: 2026-08-18
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b7e4c2a19f08"
down_revision: Union[str, None] = "c18a0e9b4d21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("idx_agent_events_run_seq", table_name="agent_events", if_exists=True)
    op.create_index(
        "idx_agent_events_run_seq",
        "agent_events",
        ["run_id", "sequence"],
        unique=True,
    )
    op.drop_index("uq_reports_run_id", table_name="reports", if_exists=True)
    op.create_index("uq_reports_run_id", "reports", ["run_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_reports_run_id", table_name="reports", if_exists=True)
    op.create_index("uq_reports_run_id", "reports", ["run_id"], unique=False)
    op.drop_index("idx_agent_events_run_seq", table_name="agent_events", if_exists=True)
    op.create_index(
        "idx_agent_events_run_seq",
        "agent_events",
        ["run_id", "sequence"],
        unique=False,
    )
