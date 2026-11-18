"""add education fields to candidate and job

Revision ID: 334b9feb41ce
Revises: 295af3836271
Create Date: 2026-11-18 09:13:24.396739

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '334b9feb41ce'
down_revision: Union[str, None] = '295af3836271'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


degree_level_enum = sa.Enum(
    "high_school", "associate", "bachelor", "master", "doctorate", name="degreelevel"
)


def upgrade() -> None:
    # Created explicitly once, then referenced with create_type=False on
    # both columns below -- two separate sa.Enum(...) objects (what plain
    # autogenerate produces here) would each try to `CREATE TYPE
    # degreelevel` on first use, and the second one fails since it already
    # exists.
    degree_level_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "candidates",
        sa.Column(
            "highest_degree_level",
            sa.Enum(
                "high_school", "associate", "bachelor", "master", "doctorate",
                name="degreelevel", create_type=False,
            ),
            nullable=True,
        ),
    )
    op.add_column("candidates", sa.Column("field_of_study", sa.String(length=150), nullable=True))
    op.add_column(
        "jobs",
        sa.Column(
            "min_degree_level",
            sa.Enum(
                "high_school", "associate", "bachelor", "master", "doctorate",
                name="degreelevel", create_type=False,
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("jobs", "min_degree_level")
    op.drop_column("candidates", "field_of_study")
    op.drop_column("candidates", "highest_degree_level")
    degree_level_enum.drop(op.get_bind(), checkfirst=True)
