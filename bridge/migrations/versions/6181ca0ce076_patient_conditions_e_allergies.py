"""patient_conditions e patient_allergies

Revision ID: 6181ca0ce076
Revises: b7c4f1a9e230
Create Date: 2026-07-31 00:00:00.000000

O modelo `Patient` (storage_advanced.py) não tinha nenhum campo para
doenças/diagnósticos nem alergias — só `Medication`, que não é o mesmo
(um medicamento na tabela não diz qual a condição que ele trata). Esta
migração cria duas tabelas novas, uma linha por entrada cada (não uma
coluna de texto livre agregado, já que um paciente pode ter várias
doenças/alergias):

  * `patient_conditions` — doenças/diagnósticos.
  * `patient_allergies`  — alergias.

Tabelas separadas (não uma tabela genérica de "achados de saúde"),
inspiradas nos recursos `Condition`/`AllergyIntolerance` do HL7 FHIR, que
também são recursos distintos entre si — relevante em concreto para o
caso de uso de emergência (NFC/dashboard devem conseguir listar alergias
isoladamente de condições crónicas).

Ambas seguem o mesmo padrão de outras tabelas de entidade do projeto
(`uuid` único, soft delete via `deleted_at`) e cifram `display_text` com
o mesmo padrão de `nif_encrypted`/`address_encrypted` em `patients` — é
dado de saúde, categoria especial RGPD. `code_system`/`code` são
opcionais (ex.: "ICD-10"/"E11", "SNOMED-CT"/<código>), inspirados no
CodeableConcept do FHIR, sem exigir integração com terminology server
neste momento.

Segue o padrão de `daaeabc42ec5_schema_inicial.py` (`op.create_table`) —
tabelas novas, não `ALTER TABLE`, por isso sem necessidade de
`op.batch_alter_table` aqui.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6181ca0ce076'
down_revision: Union[str, Sequence[str], None] = 'b7c4f1a9e230'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'patient_conditions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('patient_id', sa.Integer(), nullable=False),
        sa.Column('display_text_encrypted', sa.String(length=512), nullable=False),
        sa.Column('code_system', sa.String(length=50), nullable=True),
        sa.Column('code', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid'),
    )
    op.create_index(
        'idx_patient_condition_patient_id', 'patient_conditions', ['patient_id'],
    )

    op.create_table(
        'patient_allergies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(length=36), nullable=False),
        sa.Column('patient_id', sa.Integer(), nullable=False),
        sa.Column('display_text_encrypted', sa.String(length=512), nullable=False),
        sa.Column('code_system', sa.String(length=50), nullable=True),
        sa.Column('code', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid'),
    )
    op.create_index(
        'idx_patient_allergy_patient_id', 'patient_allergies', ['patient_id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_patient_allergy_patient_id', table_name='patient_allergies')
    op.drop_table('patient_allergies')
    op.drop_index('idx_patient_condition_patient_id', table_name='patient_conditions')
    op.drop_table('patient_conditions')
