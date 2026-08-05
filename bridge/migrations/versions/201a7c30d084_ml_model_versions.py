"""versionamento e rollback do modelo ML (ml_model_versions)

Revision ID: 201a7c30d084
Revises: c3e7a1f9b4d2
Create Date: 2026-08-05 00:00:00.000000

Acrescenta a tabela `ml_model_versions` (ver storage_advanced.py --
MlModelVersion/register_model_version/list_model_versions/
activate_model_version/get_active_model_version). Até agora,
`activity_inference.py::_load_model()` carregava sempre o mesmo caminho
fixo (`ml/models/activity_classifier_rf.joblib` +
`activity_classifier_rf_labels.json`) -- sem histórico de versões nem
forma de reverter um modelo retreinado que se revele pior, sem reiniciar
o bridge. Esta tabela guarda esse histórico NA BASE DE DADOS (não num
ficheiro solto ao lado do .joblib) e permite trocar a versão ativa em
runtime.

Sem dados de seed nesta migração: o get-or-create da versão "1" (a que já
existia antes deste sistema de versionamento) é feito em runtime por
`activity_inference.py::_resolve_active_model_paths()` na primeira vez
que corre contra uma BD sem nenhuma versão registada -- não é
responsabilidade desta migração popular essa linha.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '201a7c30d084'
down_revision: Union[str, Sequence[str], None] = 'c3e7a1f9b4d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'ml_model_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('model_name', sa.String(length=50), nullable=False),
        sa.Column('version', sa.String(length=50), nullable=False),
        sa.Column('file_path', sa.String(length=500), nullable=False),
        sa.Column('labels_path', sa.String(length=500), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('trained_at', sa.DateTime(), nullable=True),
        sa.Column('metrics_json', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('model_name', 'version', name='uq_ml_model_name_version'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('ml_model_versions')
