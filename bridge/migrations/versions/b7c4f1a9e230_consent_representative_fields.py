"""campos de representante/base legal em consent_records (GDPR-001)

Revision ID: b7c4f1a9e230
Revises: 09f7da2ef011
Create Date: 2026-07-21 00:00:00.000000

GDPR-001 — o modelo `ConsentRecord` (storage_advanced.py) só sabia SE houve
consentimento, não QUEM o deu. O público-alvo do CareWear tem demência e
pode não poder consentir sozinho, por isso é preciso distinguir o próprio
utente de um representante legal/procurador e registar a base legal RGPD do
tratamento. Esta migração acrescenta à tabela `consent_records`:

  * `given_by`    ('patient' | 'representative', NOT NULL, default 'patient')
  * `representative_relationship` (texto livre, nullable)
  * `representative_name`         (nullable)
  * `legal_basis` ('consent' Art.6(1)(a) | 'vital_interest' Art.6(1)(d),
                   NOT NULL, default 'consent')

Todas as colunas novas têm `server_default` para não rebentar linhas já
existentes na tabela (paridade com o `default=` do modelo ORM). O
CheckConstraint de `given_by` espelha o estilo de `User.role`.

Segue o padrão da migração inicial (`daaeabc42ec5_schema_inicial.py`):
`op.batch_alter_table` porque o backend é SQLite (ALTER limitado).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c4f1a9e230'
down_revision: Union[str, Sequence[str], None] = '09f7da2ef011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('consent_records', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'given_by', sa.String(length=20),
            nullable=False, server_default='patient',
        ))
        batch_op.add_column(sa.Column(
            'representative_relationship', sa.String(length=50), nullable=True,
        ))
        batch_op.add_column(sa.Column(
            'representative_name', sa.String(length=255), nullable=True,
        ))
        batch_op.add_column(sa.Column(
            'legal_basis', sa.String(length=50),
            nullable=False, server_default='consent',
        ))
        batch_op.create_check_constraint(
            'ck_consent_given_by',
            "given_by IN ('patient', 'representative')",
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('consent_records', schema=None) as batch_op:
        batch_op.drop_constraint('ck_consent_given_by', type_='check')
        batch_op.drop_column('legal_basis')
        batch_op.drop_column('representative_name')
        batch_op.drop_column('representative_relationship')
        batch_op.drop_column('given_by')
