"""emergency_alerts deleted_at (GDPR-006 fecho: retencao 8 anos)

Revision ID: c3e7a1f9b4d2
Revises: 6181ca0ce076
Create Date: 2026-07-31 00:00:00.000000

GDPR-006 (`SECURITY_STATUS.md`) documentava a retenção "para sempre" de
`emergency_alerts` sem justificação — a política já estava referenciada
em `DataRetention.RETENTION_POLICIES` (10 anos) mas nunca era aplicada,
porque a tabela não tinha `deleted_at` (soft delete, mesmo padrão de
`alerts`). A utilizadora decidiu (2026-07-31) fechar o requisito com um
prazo de 8 anos, substituindo o valor de referência de 10 anos. Esta
migração acrescenta a coluna que faltava para `DataRetention.cleanup()`
poder aplicar essa política de facto, em vez de só a documentar.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3e7a1f9b4d2'
down_revision: Union[str, Sequence[str], None] = '6181ca0ce076'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('emergency_alerts') as batch_op:
        batch_op.add_column(sa.Column('deleted_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('emergency_alerts') as batch_op:
        batch_op.drop_column('deleted_at')
