"""baseline current schema from ORM models

Revision ID: c18a0e9b4d21
Revises:
Create Date: 2026-08-18
"""
from typing import Sequence, Union

from alembic import op

from app.shared.base import Base
from app.shared.models import register_models

revision: str = "c18a0e9b4d21"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    register_models()
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    register_models()
    Base.metadata.drop_all(bind=op.get_bind())
