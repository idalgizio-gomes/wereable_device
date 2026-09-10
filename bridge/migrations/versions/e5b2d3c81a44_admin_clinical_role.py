"""dois perfis de administrador: role 'admin_clinical' + concessao temporal

Revision ID: e5b2d3c81a44
Revises: d4a1c9e7f512
Create Date: 2026-09-07 00:00:00.000000

2026-09-07 — separa o antigo papel unico `admin` em dois:

  * `admin`           -> Admin de Sistema (utilizadores, contas,
                         dispositivos, configuracao, firmware, logs,
                         manutencao). SEM acesso a dados clinicos.
  * `admin_clinical`  -> Admin Clinico / Suporte Autorizado. Pode aceder
                         a dados clinicos apenas com motivo registado e
                         enquanto tiver uma concessao temporal ativa
                         (`users.privileged_access_expires_at`).

DECISAO (justificacao pedida): acrescenta-se um VALOR novo ao CHECK de
`users.role` em vez de criar uma coluna `admin_subtype` separada. Uma
coluna separada permitiria estados impossiveis (um `clinician` com
subtipo de admin, um `admin` com subtipo NULL) que so' poderiam ser
proibidos em codigo; um valor a mais no CHECK mantem o papel como uma
dimensao unica e deixa a base de dados a rejeitar os estados invalidos.

Migracao de dados: NAO se converte automaticamente nenhum `admin`
existente em `admin_clinical`. O upgrade e' deliberadamente conservador —
todos os administradores atuais ficam como Admin de Sistema, ou seja
PERDEM o acesso clinico que o codigo antigo lhes dava implicitamente.
Promover alguem a `admin_clinical` e' um ato deliberado de quem gere
contas, nao um efeito colateral de uma migracao.

SQLite nao suporta ALTER de CHECK constraint: usa-se batch_alter_table
(recria a tabela), que e' o mesmo padrao ja' usado nesta arvore de
migracoes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5b2d3c81a44'
down_revision: Union[str, Sequence[str], None] = 'd4a1c9e7f512'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_CHECK = "role IN ('family', 'clinician', 'admin')"
_NEW_CHECK = "role IN ('family', 'clinician', 'admin', 'admin_clinical')"
_CHECK_NAME = 'ck_users_role'


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('privileged_access_expires_at', sa.DateTime(), nullable=True))
        # `batch_alter_table` recria a tabela; o CHECK novo entra na
        # definicao recriada. O drop do antigo e' tolerante porque em
        # bases criadas por `create_all` a constraint pode estar sem nome.
        try:
            batch_op.drop_constraint(_CHECK_NAME, type_='check')
        except Exception:  # pragma: no cover - depende do dialeto/base existente
            pass
        batch_op.create_check_constraint(_CHECK_NAME, _NEW_CHECK)


def downgrade() -> None:
    # Qualquer utilizador que ja' seja `admin_clinical` volta a ser um
    # administrador de sistema simples (perde o acesso clinico) — nunca
    # se apaga a conta.
    bind = op.get_bind()
    users = sa.table('users', sa.column('role', sa.String))
    bind.execute(users.update().where(users.c.role == 'admin_clinical').values(role='admin'))

    with op.batch_alter_table('users', schema=None) as batch_op:
        try:
            batch_op.drop_constraint(_CHECK_NAME, type_='check')
        except Exception:  # pragma: no cover
            pass
        batch_op.create_check_constraint(_CHECK_NAME, _OLD_CHECK)
        batch_op.drop_column('privileged_access_expires_at')
