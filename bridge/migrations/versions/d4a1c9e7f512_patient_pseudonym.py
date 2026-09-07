"""pseudonimizacao: coluna patients.pseudonym (RGPD Art. 4(5))

Revision ID: d4a1c9e7f512
Revises: 201a7c30d084
Create Date: 2026-09-04 00:00:00.000000

Acrescenta um identificador opaco por paciente, sem relacao com
nome/data de nascimento, para uso em contextos que nao precisam de saber
quem e o paciente (exports, ML, logs). Nao remove nem move nenhuma coluna
existente — e' aditiva, reversivel so' atraves desta tabela (pseudonimizacao,
nao anonimizacao).

Como server_default nao pode gerar um valor ALEATORIO por linha em SQLite,
a coluna nasce nullable, e' preenchida linha a linha via UPDATE em Python
(secrets.token_urlsafe), e so' depois fica NOT NULL + UNIQUE.
"""
import secrets
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4a1c9e7f512'
down_revision: Union[str, Sequence[str], None] = '201a7c30d084'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('patients', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pseudonym', sa.String(length=32), nullable=True))

    bind = op.get_bind()
    patients = sa.table('patients', sa.column('id', sa.Integer), sa.column('pseudonym', sa.String))
    for (patient_id,) in bind.execute(sa.select(patients.c.id)):
        bind.execute(
            patients.update().where(patients.c.id == patient_id).values(pseudonym=secrets.token_urlsafe(16))
        )

    with op.batch_alter_table('patients', schema=None) as batch_op:
        batch_op.alter_column('pseudonym', nullable=False)
        batch_op.create_unique_constraint('uq_patients_pseudonym', ['pseudonym'])


def downgrade() -> None:
    with op.batch_alter_table('patients', schema=None) as batch_op:
        batch_op.drop_constraint('uq_patients_pseudonym', type_='unique')
        batch_op.drop_column('pseudonym')
