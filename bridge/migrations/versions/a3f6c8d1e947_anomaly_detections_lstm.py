"""anomaly_detections redesenhada + ml_model_versions.extra_path (LSTM Autoencoder)

Revision ID: a3f6c8d1e947
Revises: e5b2d3c81a44
Create Date: 2026-09-15 00:00:00.000000

Recria `anomaly_detections` com um desenho novo, ligado de facto ao LSTM
Autoencoder já treinado em ml/ (ver storage_advanced.py::AnomalyDetection,
bridge/anomaly_inference.py). A tabela antiga (schema exists, nunca
escrita — comentário original dizia "LSTM Autoencoder — futura") foi
apagada numa sessão anterior por estar morta; esta é uma tabela NOVA, não
uma alteração da antiga, daí `create_table` e não `alter_table`.

Uma linha por EPISÓDIO já fechado (não por janela de 10s — ver
anomaly_inference.py::_update_episode), de duas fontes possíveis
(`detector`): 'lstm_autoencoder' (padrão temporal atípico numa sequência
de janelas) ou 'duration_rule' (bloco de atividade fora da duração
esperada, ml/duration_detector.py — antes só transmitido ao vivo, nunca
persistido).

`ml_model_versions.extra_path` (nullable, ADD COLUMN não recria a
tabela): artefacto adicional opcional além de file_path/labels_path —
usado pelo autoencoder para o caminho do StandardScaler
(lstm_autoencoder_scaler.joblib), significado depende de `model_name`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f6c8d1e947'
down_revision: Union[str, Sequence[str], None] = 'e5b2d3c81a44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('ml_model_versions', sa.Column('extra_path', sa.String(length=500), nullable=True))

    op.create_table(
        'anomaly_detections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('device_id', sa.Integer(), nullable=False),
        sa.Column('detector', sa.String(length=30), nullable=False),
        sa.Column('anomaly_category', sa.String(length=50), nullable=False),
        sa.Column('score', sa.Float(), nullable=True),
        sa.Column('threshold_used', sa.Float(), nullable=True),
        sa.Column('window_start', sa.DateTime(), nullable=False),
        sa.Column('window_end', sa.DateTime(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('severity', sa.String(length=20), nullable=True),
        sa.Column('model_version', sa.String(length=50), nullable=True),
        sa.Column('investigated', sa.Boolean(), nullable=True),
        sa.Column('investigation_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid'),
    )
    op.create_index(
        'idx_anomaly_device_window', 'anomaly_detections', ['device_id', 'window_start'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_anomaly_device_window', table_name='anomaly_detections')
    op.drop_table('anomaly_detections')
    op.drop_column('ml_model_versions', 'extra_path')
