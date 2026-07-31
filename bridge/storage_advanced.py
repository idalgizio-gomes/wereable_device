#!/usr/bin/env python3
"""
storage_advanced.py — Serviço de persistência avançado com SQLAlchemy ORM.

Refatoração do storage.py original com:
  - SQLAlchemy ORM (segurança contra SQL injection, migrations, type hints)
  - Schema completo (users, patients, devices, medications, etc.)
  - Queries analíticas (trends, aggregations)
  - Políticas de retenção automática
  - Cifra de campos sensíveis (NIF, morada)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean, DateTime,
    ForeignKey, Index, Text, JSON, CheckConstraint, UniqueConstraint,
    Table, desc, and_, or_, func, event
)
from sqlalchemy.orm import sessionmaker, relationship, Session, declarative_base
from sqlalchemy.pool import StaticPool

from crypto_utils import decrypt_field, encrypt_field

# ============================================================
# CONFIGURAÇÃO
# ============================================================

DB_URL = os.environ.get(
    "DATABASE_URL",
    "sqlite:///./carewear.db"  # Local development
)

# Para SQLite em-memória em testes:
# DB_URL = "sqlite:///:memory:"

if DB_URL.startswith("sqlite"):
    # SQLite requer configurações especiais para foreign keys
    engine = create_engine(
        DB_URL,
        connect_args={"check_same_thread": False} if "sqlite" in DB_URL else {},
        poolclass=StaticPool if "sqlite:///:memory:" in DB_URL else None,
    )
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        # OTIMIZAÇÃO (Lote C): mesma justificação documentada em
        # storage.py get_connection() — com o dual-write, os SensorRecord
        # também são escritos aqui (em lote, ver orm_persistence.py). WAL +
        # synchronous=NORMAL evita o fsync por commit do modo por omissão
        # (rollback journal + FULL), que bloquearia o event loop asyncio do
        # bridge. Num protótipo local de uso pessoal (sem requisitos de
        # durabilidade contra corte de energia) a troca é adequada. Em
        # SQLite :memory: (testes) o PRAGMA é inócuo — não há ficheiro -wal.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()
else:
    # PostgreSQL em produção
    engine = create_engine(DB_URL, echo=False, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ============================================================
# MODELOS ORM
# ============================================================

# Tabela de associação muitos-para-muitos entre utilizadores (cuidadores) e
# pacientes — suporta "múltiplos cuidadores com permissões por papel"
# (item 10 do backlog do dashboard). Faltava por completo (só era
# referenciada por nome em User.patients via secondary=, sem nenhuma
# Table/model a definir) — sem isto, configurar qualquer mapper deste
# ficheiro (User, Patient, ou qualquer outro modelo, porque o SQLAlchemy
# configura o registo de mappers em conjunto) falha com
# InvalidRequestError ("patient_caregivers... failed to locate a name").
patient_caregivers = Table(
    "patient_caregivers",
    Base.metadata,
    Column("patient_id", Integer, ForeignKey("patients.id"), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("can_view_alerts", Boolean, default=True),
    Column("can_edit_notes", Boolean, default=True),
    Column("can_edit_medications", Boolean, default=False),
    Column("created_at", DateTime, default=datetime.utcnow),
)


class User(Base):
    """Utilizador (família, clínico, admin)."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), CheckConstraint("role IN ('family', 'clinician', 'admin')"), nullable=False)
    name = Column(String(255), nullable=False)
    phone = Column(String(20))
    institution = Column(String(255))
    professional_id = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime)

    # Relationships
    patients = relationship("Patient", secondary="patient_caregivers")
    audit_log = relationship("AuditLog", back_populates="user")


class Patient(Base):
    """Paciente monitorizado."""
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    date_of_birth = Column(DateTime, nullable=False)
    # Cifrados com AES-256-GCM (chave derivada via Argon2id, ver crypto_utils.py)
    # através das propriedades nif/address abaixo — nunca atribuir estas duas
    # colunas *_encrypted diretamente. 512 bytes (não 255) para acomodar o
    # overhead da cifra (nonce + tag + base64) em moradas mais longas.
    nif_encrypted = Column(String(512))  # aprovação obrigatória, ver dashboard
    address_encrypted = Column(String(512))
    # GDPR-005 (Lote C) — DECISÃO DOCUMENTADA: NÃO estender a cifra de
    # campo (padrão nif/address, ver propriedades abaixo) a `phone` e aos
    # `emergency_contact_*` NESTE lote. Motivos concretos:
    #   1. Ao contrário de nif_encrypted/address_encrypted, estas colunas
    #      são fixadas por nome no esquema SQL canónico (bridge/schema.sql)
    #      e por uma migração Alembic já aplicada
    #      (migrations/versions/daaeabc42ec5_schema_inicial.py) — ficheiros
    #      FORA do âmbito deste lote. Renomeá-las para *_encrypted aqui
    #      dessincronizaria o ORM do esquema/migração sem uma migração nova
    #      correspondente (risco real numa base de dados existente).
    #   2. Não é a "extensão direta e óbvia" do padrão que o cifrar de
    #      Strings via propriedade seria em isolamento — arrasta alterações
    #      coordenadas em 3 ficheiros de esquema para ser correto.
    # A extensão fica registada como próximo passo a fazer em conjunto com
    # uma migração Alembic dedicada (rename coluna + backfill cifrado),
    # não como uma alteração pontual do modelo.
    phone = Column(String(20))
    emergency_contact_name = Column(String(255))
    emergency_contact_phone = Column(String(20))
    emergency_contact_relation = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime)

    # Relationships
    devices = relationship("Device", back_populates="patient")
    medications = relationship("Medication", back_populates="patient")
    thresholds = relationship("PersonalizedThreshold", back_populates="patient", uselist=False)
    conditions = relationship("PatientCondition", back_populates="patient", cascade="all, delete-orphan")
    allergies = relationship("PatientAllergy", back_populates="patient", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_patient_uuid", "uuid"),
    )

    @property
    def nif(self) -> Optional[str]:
        """NIF em texto simples (decifrado sob pedido, nunca guardado assim)."""
        return decrypt_field(self.nif_encrypted)

    @nif.setter
    def nif(self, value: Optional[str]) -> None:
        self.nif_encrypted = encrypt_field(value)

    @property
    def address(self) -> Optional[str]:
        """Morada em texto simples (decifrada sob pedido, nunca guardada assim)."""
        return decrypt_field(self.address_encrypted)

    @address.setter
    def address(self, value: Optional[str]) -> None:
        self.address_encrypted = encrypt_field(value)


class PatientCondition(Base):
    """Doença/diagnóstico do paciente — uma linha por entrada (não texto livre agregado).

    Inspirado no recurso `Condition` do HL7 FHIR: `display_text` é o que se vê
    no dashboard (obrigatório), `code_system`/`code` são opcionais
    (ex.: "ICD-10"/"E11" para diabetes tipo 2) para permitir cruzamento
    automático mais tarde sem obrigar o cuidador a conhecer códigos clínicos
    hoje. Ver PatientAllergy para o motivo de ser uma tabela separada.
    """
    __tablename__ = "patient_conditions"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), unique=True, nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    # Cifrado com o mesmo padrão de nif_encrypted/address_encrypted — é dado
    # de saúde, categoria especial RGPD.
    display_text_encrypted = Column(String(512), nullable=False)
    code_system = Column(String(50))  # ex.: "ICD-10", "SNOMED-CT"
    code = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime)

    patient = relationship("Patient", back_populates="conditions")

    __table_args__ = (
        Index("idx_patient_condition_patient_id", "patient_id"),
    )

    @property
    def display_text(self) -> Optional[str]:
        return decrypt_field(self.display_text_encrypted)

    @display_text.setter
    def display_text(self, value: Optional[str]) -> None:
        self.display_text_encrypted = encrypt_field(value)


class PatientAllergy(Base):
    """Alergia do paciente — uma linha por entrada.

    Tabela separada de PatientCondition (não uma só tabela genérica "achados
    de saúde"): o FHIR trata `AllergyIntolerance` como recurso próprio porque
    uma alergia é semanticamente distinta de um diagnóstico — no CareWear
    isso importa em concreto para o caso de uso de emergência (NFC/dashboard
    devem conseguir listar alergias isoladamente de condições crónicas).
    """
    __tablename__ = "patient_allergies"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), unique=True, nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    display_text_encrypted = Column(String(512), nullable=False)
    code_system = Column(String(50))  # ex.: "SNOMED-CT"
    code = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime)

    patient = relationship("Patient", back_populates="allergies")

    __table_args__ = (
        Index("idx_patient_allergy_patient_id", "patient_id"),
    )

    @property
    def display_text(self) -> Optional[str]:
        return decrypt_field(self.display_text_encrypted)

    @display_text.setter
    def display_text(self, value: Optional[str]) -> None:
        self.display_text_encrypted = encrypt_field(value)


class Device(Base):
    """Dispositivo wearable."""
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), unique=True, nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    mac_address = Column(String(17), unique=True, nullable=False)
    firmware_version = Column(String(50))
    hardware_variant = Column(String(100))
    battery_percent = Column(Integer)
    last_sync = Column(DateTime)
    storage_used_bytes = Column(Integer)
    storage_total_bytes = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    patient = relationship("Patient", back_populates="devices")
    sensor_records = relationship("SensorRecord", back_populates="device", cascade="all, delete-orphan")
    emergency_alerts = relationship("EmergencyAlert", back_populates="device", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_device_patient_id", "patient_id"),
    )


class SensorRecord(Base):
    """Registo de sensores em tempo real."""
    __tablename__ = "sensor_records"

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    timestamp_utc = Column(Integer, nullable=False)  # Unix timestamp
    accel_x = Column(Float)
    accel_y = Column(Float)
    accel_z = Column(Float)
    gyro_x = Column(Float)
    gyro_y = Column(Float)
    gyro_z = Column(Float)
    steps_count = Column(Integer)
    freefall_detected = Column(Boolean)
    inactivity_detected = Column(Boolean)
    heart_rate = Column(Integer)  # BPM
    spo2_percent = Column(Integer)  # %
    pacing_index = Column(Integer)  # 0-100
    received_at = Column(DateTime, default=datetime.utcnow)

    device = relationship("Device", back_populates="sensor_records")

    __table_args__ = (
        Index("idx_sensor_device_timestamp", "device_id", "timestamp_utc"),
        Index("idx_sensor_received_at", "received_at"),
    )


class ActivityWindow(Base):
    """Janela de atividade (agregação diária por tipo)."""
    __tablename__ = "activity_windows"

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    activity_date = Column(DateTime, nullable=False)
    activity_category = Column(
        String(20),
        CheckConstraint("activity_category IN ('sleep', 'rest', 'activity', 'eating', 'hygiene')"),
        nullable=False
    )
    start_time = Column(Integer)  # Minutos desde início do dia
    end_time = Column(Integer)
    duration_minutes = Column(Integer)
    confidence = Column(Float)  # 0.0-1.0

    __table_args__ = (
        Index("idx_activity_device_date", "device_id", "activity_date"),
    )


class Medication(Base):
    """Medicamento prescrito."""
    __tablename__ = "medications"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), unique=True, nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    name = Column(String(255), nullable=False)
    dosage = Column(String(100), nullable=False)
    frequency = Column(String(100), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)
    prescribed_by_user_id = Column(Integer, ForeignKey("users.id"))
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime)

    patient = relationship("Patient", back_populates="medications")
    adherence = relationship("MedicationAdherence", back_populates="medication", cascade="all, delete-orphan")


class MedicationAdherence(Base):
    """Registro de aderência a medicação."""
    __tablename__ = "medication_adherence"

    id = Column(Integer, primary_key=True)
    medication_id = Column(Integer, ForeignKey("medications.id"), nullable=False)
    scheduled_datetime = Column(DateTime, nullable=False)
    taken = Column(Boolean)
    taken_at = Column(DateTime)
    method = Column(String(50))  # 'manual_entry', 'wearable_detection', 'ai_inference'
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    medication = relationship("Medication", back_populates="adherence")

    # BUG CORRIGIDO: era um Index não-único — nada na base de dados impedia
    # duas linhas para a mesma (medication_id, scheduled_datetime). A
    # idempotência "por desenho" documentada em `record_medication_adherence`
    # (bridge/api.py) era garantida SÓ pela leitura-antes-de-escrever nesse
    # endpoint (SELECT a verificar se já existe, depois INSERT ou UPDATE) —
    # uma janela clássica de TOCTOU: dois pedidos concorrentes para a MESMA
    # dose (ex.: um retry por timeout de rede a coincidir com o pedido
    # original, ou dois cuidadores a marcar a mesma dose quase ao mesmo
    # tempo, uma vez que o dashboard/`ble_bridge.py` venham a chamar este
    # endpoint) podem ambos fazer o SELECT antes de qualquer um dos dois
    # fazer o INSERT, resultando em duas linhas para a mesma dose (confirmado
    # por reprodução direta contra `storage_advanced.py`, ver
    # bridge/tests/test_storage_advanced.py::TestMedicationAdherenceUniqueness).
    # Isto duplicava entradas de auditoria e inflacionava as métricas de
    # aderência (`Analytics.medication_adherence_summary` conta `total` por
    # número de linhas). Só uma constraint a nível da BD impede isto de
    # forma real sob concorrência — ver também o tratamento de
    # IntegrityError em `record_medication_adherence` (bridge/api.py), que
    # converge para um UPDATE quando perde a corrida em vez de deixar a BD
    # rejeitar o pedido com um erro 500.
    __table_args__ = (
        UniqueConstraint(
            "medication_id", "scheduled_datetime",
            name="uq_adherence_medication_scheduled",
        ),
    )


class Alert(Base):
    """Alerta (anomalia, vital anormal, queda, etc.)."""
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), unique=True, nullable=False)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    alert_type = Column(String(100), nullable=False)
    severity = Column(
        String(20),
        CheckConstraint("severity IN ('info', 'warning', 'serious', 'critical')"),
        nullable=False
    )
    title = Column(String(255), nullable=False)
    description = Column(Text)
    raw_data = Column(JSON)
    read_by_user_id = Column(Integer, ForeignKey("users.id"))
    read_at = Column(DateTime)
    silenced = Column(Boolean, default=False)
    silenced_until = Column(DateTime)
    escalated_to_severity = Column(String(20))
    escalated_at = Column(DateTime)
    resolved_by_user_id = Column(Integer, ForeignKey("users.id"))
    resolved_at = Column(DateTime)
    resolution_note = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    deleted_at = Column(DateTime)  # soft delete (política de retenção, 7 anos)

    __table_args__ = (
        Index("idx_alert_device_created", "device_id", "created_at"),
        Index("idx_alert_severity_read", "severity", "read_at"),
    )


class EmergencyAlert(Base):
    """Alerta de emergência (SOS, queda)."""
    __tablename__ = "emergency_alerts"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), unique=True, nullable=False)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    alert_type = Column(String(50), nullable=False)  # 'sos_manual', 'fall_inactivity'
    sequence_number = Column(Integer)
    timestamp_utc = Column(Integer, nullable=False)
    responded_at = Column(DateTime)
    response_user_id = Column(Integer, ForeignKey("users.id"))
    response_action = Column(String(50))  # 'confirmed', 'false_positive', 'no_response'
    confirmation_code = Column(String(6))  # OTP
    confirmation_attempts = Column(Integer, default=0)
    confirmation_blocked_until = Column(DateTime)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    device = relationship("Device", back_populates="emergency_alerts")

    __table_args__ = (
        Index("idx_emergency_device_timestamp", "device_id", "timestamp_utc"),
        Index("idx_emergency_responded", "responded_at"),
        UniqueConstraint("device_id", "sequence_number", name="uq_emergency_device_seq"),
    )


class AnomalyDetection(Base):
    """Anomalia de rotina detectada (LSTM Autoencoder)."""
    __tablename__ = "anomaly_detections"

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    anomaly_type = Column(String(100), nullable=False)
    score = Column(Float)  # 0.0-1.0
    start_datetime = Column(DateTime, nullable=False)
    end_datetime = Column(DateTime)
    description = Column(Text)
    potential_cause = Column(Text)
    severity = Column(String(20))  # 'minor', 'moderate', 'severe'
    investigated = Column(Boolean, default=False)
    investigation_notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_anomaly_device_datetime", "device_id", "start_datetime"),
    )


class PersonalizedThreshold(Base):
    """Limiares personalizados por paciente."""
    __tablename__ = "personalized_thresholds"

    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), unique=True, nullable=False)
    heart_rate_min = Column(Integer)
    heart_rate_max = Column(Integer)
    spo2_min = Column(Integer)
    inactivity_threshold_seconds = Column(Integer)
    sleep_target_minutes = Column(Integer)
    activity_target_minutes = Column(Integer)
    steps_target_daily = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    patient = relationship("Patient", back_populates="thresholds")


class DailyStatistics(Base):
    """Cache de estatísticas diárias (para dashboards rápidos)."""
    __tablename__ = "daily_statistics"

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    stat_date = Column(DateTime, nullable=False)
    total_steps = Column(Integer)
    avg_heart_rate = Column(Integer)
    min_heart_rate = Column(Integer)
    max_heart_rate = Column(Integer)
    avg_spo2 = Column(Integer)
    sleep_duration_minutes = Column(Integer)
    activity_duration_minutes = Column(Integer)
    rest_duration_minutes = Column(Integer)
    eating_duration_minutes = Column(Integer)
    hygiene_duration_minutes = Column(Integer)
    alerts_count = Column(Integer)
    anomalies_count = Column(Integer)
    medication_adherence_percent = Column(Float)
    computed_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("device_id", "stat_date", name="uq_daily_stats_device_date"),
        Index("idx_daily_stats_device_date", "device_id", "stat_date"),
    )


class AuditLog(Base):
    """Auditoria de ações sensíveis."""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    action = Column(String(100), nullable=False)
    resource_type = Column(String(50))
    resource_id = Column(Integer)
    details = Column(JSON)
    ip_address = Column(String(45))
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="audit_log")

    __table_args__ = (
        Index("idx_audit_user_created", "user_id", "created_at"),
        Index("idx_audit_resource", "resource_type", "resource_id"),
    )


class Setting(Base):
    """Par chave/valor de configuração global do bridge (2026-07-26,
    migração — equivalente à tabela `settings` de storage.py). Hoje só
    guarda `retention_days` (ver get_retention_days/set_retention_days
    abaixo), mas fica pensada para outras opções futuras sem precisar de
    nova tabela."""
    __tablename__ = "settings"

    key = Column(String(100), primary_key=True)
    value = Column(String(255), nullable=False)


class ActivityCorrection(Base):
    """Correção manual do cuidador/equipa clínica à classificação de
    atividade da IA (2026-07-26, migração — equivalente à tabela
    `activity_corrections` de storage.py). Ligada a `device_id` (ao
    contrário da versão original em storage.py, que não distinguia
    dispositivo — irrelevante numa instalação de um só dispositivo, mas
    correto agora que a fonte de verdade é o esquema multi-dispositivo do
    ORM)."""
    __tablename__ = "activity_corrections"

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    received_at = Column(DateTime, default=datetime.utcnow)
    original_category = Column(String(50))
    corrected_category = Column(String(50), nullable=False)

    __table_args__ = (
        Index("idx_activity_correction_device_received", "device_id", "received_at"),
    )


class ConsentRecord(Base):
    """Registro de consentimento GDPR/HIPAA."""
    __tablename__ = "consent_records"

    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    scope = Column(String(100), nullable=False)  # 'sensor_data', 'analytics', 'export', 'research'
    granted = Column(Boolean, nullable=False)
    version = Column(String(50), nullable=False)
    signed_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    # GDPR-001 — quem consentiu. O público-alvo tem demência e pode não
    # poder consentir sozinho, por isso distingue-se o próprio utente
    # ('patient') de um representante legal/procurador ('representative').
    # CheckConstraint restringe os valores, no mesmo estilo de User.role.
    given_by = Column(
        String(20),
        CheckConstraint("given_by IN ('patient', 'representative')"),
        nullable=False, default="patient",
    )
    # Relação do representante com o utente (ex.: "filho(a)", "tutor(a)",
    # "procurador(a)"). Só preenchido quando given_by == 'representative'.
    # Texto livre de propósito (sem CheckConstraint rígido): a lista de
    # relações possíveis é um problema de UX/UI que ainda não existe.
    representative_relationship = Column(String(50))
    # Nome do representante que assinou o consentimento. Guardado à parte
    # de user_id porque nesta fase não há provisioning real de contas
    # (ver DEFAULT_PATIENT_UUID/comentário em orm_persistence.py) — o
    # user_id pode não corresponder a uma conta de utilizador real.
    representative_name = Column(String(255))
    # Base legal RGPD do tratamento. 'consent' (Art. 6(1)(a)) é o caso
    # normal (o próprio ou o representante consentem). 'vital_interest'
    # (Art. 6(1)(d)) cobre o alerta de emergência/queda em que aguardar
    # consentimento explícito atrasaria a resposta a um risco de vida.
    # Não introduzir mais valores sem um caso de uso concreto.
    legal_basis = Column(String(50), nullable=False, default="consent")

    __table_args__ = (
        UniqueConstraint("patient_id", "scope", "version", name="uq_consent_patient_scope_version"),
    )


# ============================================================
# INICIALIZAÇÃO
# ============================================================

def create_all_tables():
    """Cria todas as tabelas (use só para desenvolvimento — em produção use Alembic)."""
    Base.metadata.create_all(bind=engine)


def get_db_session() -> Session:
    """Factory para criar uma sesão de base de dados."""
    return SessionLocal()


# ============================================================
# QUERIES ANALÍTICAS
# ============================================================

class Analytics:
    """Helper class para queries analíticas complexas."""

    @staticmethod
    def heart_rate_trends(db: Session, device_id: int, days: int = 7) -> dict:
        """Tendência de FC nos últimos N dias."""
        # datetime.utcnow() é "naive" (sem fuso) mas representa UTC; chamar
        # .timestamp() nele fá-lo-ia ser interpretado como hora LOCAL do
        # servidor, desviando o corte por exatamente o offset do fuso —
        # usa-se datetime.now(timezone.utc), que é "aware" e converte para
        # epoch corretamente em qualquer servidor.
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        records = db.query(SensorRecord).filter(
            and_(
                SensorRecord.device_id == device_id,
                SensorRecord.timestamp_utc >= int(cutoff.timestamp()),
                SensorRecord.heart_rate.isnot(None)
            )
        ).order_by(SensorRecord.timestamp_utc).all()

        return {
            "count": len(records),
            "avg": sum(r.heart_rate for r in records) / len(records) if records else 0,
            "min": min((r.heart_rate for r in records), default=0),
            "max": max((r.heart_rate for r in records), default=0),
            "records": [{"ts": r.timestamp_utc, "hr": r.heart_rate} for r in records],
        }

    @staticmethod
    def medication_adherence_summary(db: Session, patient_id: int, days: int = 30) -> dict:
        """Sumário de aderência a medicação no período."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        medications = db.query(Medication).filter(Medication.patient_id == patient_id).all()

        adherence_list = []
        for med in medications:
            adherences = db.query(MedicationAdherence).filter(
                and_(
                    MedicationAdherence.medication_id == med.id,
                    MedicationAdherence.scheduled_datetime >= cutoff
                )
            ).all()
            taken = sum(1 for a in adherences if a.taken)
            total = len(adherences)
            adherence_list.append({
                "medication_name": med.name,
                "taken": taken,
                "total": total,
                "percent": (taken / total * 100) if total > 0 else 0,
            })

        return {
            "period_days": days,
            "medications": adherence_list,
            "overall_percent": sum(m["percent"] for m in adherence_list) / len(adherence_list) if adherence_list else 0,
        }

    @staticmethod
    def daily_activity_distribution(db: Session, device_id: int, date: datetime) -> dict:
        """Distribuição de atividades num dia específico.

        `activity_date` é uma coluna DateTime (guarda também a hora); comparar
        diretamente com `date.date()` nunca encontrava nada (comparação
        datetime-completo vs. data-nua, mismatch de tipo/formato em SQLite) —
        usa-se antes um intervalo [início do dia, início do dia seguinte).
        """
        day_start = datetime(date.year, date.month, date.day)
        day_end = day_start + timedelta(days=1)
        activities = db.query(ActivityWindow).filter(
            and_(
                ActivityWindow.device_id == device_id,
                ActivityWindow.activity_date >= day_start,
                ActivityWindow.activity_date < day_end,
            )
        ).all()

        result = {}
        for category in ["sleep", "rest", "activity", "eating", "hygiene"]:
            windows = [a for a in activities if a.activity_category == category]
            total_minutes = sum(a.duration_minutes for a in windows if a.duration_minutes)
            result[category] = {
                "duration_minutes": total_minutes,
                "windows_count": len(windows),
                "average_window_minutes": total_minutes / len(windows) if windows else 0,
            }

        return result


# ============================================================
# LEITURA/ESCRITA DO CAMINHO DO DASHBOARD (2026-07-26, migração)
# ============================================================
#
# Funções abaixo substituem storage.py como fonte única de leitura/escrita
# usada por `ble_bridge.py` (get_history, get_daily_trend, export_csv,
# retenção configurável, correções de atividade). Antes desta migração,
# storage.py (SQLite cru) era o caminho primário e storage_advanced.py só
# recebia uma cópia em segundo plano (dual-write, ver orm_persistence.py).
# Os dicts devolvidos por get_records_since/export_records_csv usam
# deliberadamente as MESMAS chaves que storage.py devolvia (ax/ay/az em vez
# de accel_x/accel_y/accel_z, etc.) para o dashboard não precisar de
# nenhuma alteração — só a fonte dos dados muda, o formato na rede não.

DEFAULT_RETENTION_DAYS = 30
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 3650  # 10 anos
RETENTION_DAYS_SETTING_KEY = "retention_days"


def get_records_since(db: Session, device_id: int, hours: float) -> list[dict]:
    """Devolve os registos de sensores das últimas `hours` horas para o
    dispositivo indicado, mais antigos primeiro — equivalente a
    storage.get_records_since(). Filtra por `received_at` (instante em que
    o bridge recebeu o registo), não por `timestamp_utc` (relógio do
    dispositivo, que pode estar dessincronizado) — mesmo critério que
    storage.py já usava."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    rows = (
        db.query(SensorRecord)
        .filter(SensorRecord.device_id == device_id, SensorRecord.received_at >= cutoff)
        .order_by(SensorRecord.received_at.asc())
        .all()
    )
    return [_sensor_record_to_dict(r) for r in rows]


def _sensor_record_to_dict(r: "SensorRecord") -> dict:
    return {
        "id": r.id,
        "received_at": r.received_at.replace(tzinfo=timezone.utc).timestamp() if r.received_at else None,
        "device_timestamp": r.timestamp_utc,
        "ax": r.accel_x, "ay": r.accel_y, "az": r.accel_z,
        "gx": r.gyro_x, "gy": r.gyro_y, "gz": r.gyro_z,
        "steps": r.steps_count,
        "freefall": int(bool(r.freefall_detected)),
        "inactivity": int(bool(r.inactivity_detected)),
        "spo2": r.spo2_percent,
        "hr": r.heart_rate,
    }


def count_records(db: Session, device_id: int) -> int:
    """Nº total de registos de sensores guardados para o dispositivo —
    equivalente a storage.count_records()."""
    return db.query(SensorRecord).filter(SensorRecord.device_id == device_id).count()


def export_records_csv(db: Session, device_id: int, hours: float) -> str:
    """Exporta os registos das últimas `hours` horas como texto CSV —
    equivalente a storage.export_records_csv(), mesmas colunas."""
    import csv
    import io

    records = get_records_since(db, device_id, hours)
    buffer = io.StringIO()
    fieldnames = [
        "id", "received_at", "device_timestamp",
        "ax", "ay", "az", "gx", "gy", "gz",
        "steps", "freefall", "inactivity", "spo2", "hr",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for record in records:
        writer.writerow(record)
    return buffer.getvalue()


def get_daily_summary(db: Session, device_id: int, days: float = 7) -> list[dict]:
    """Agrega os registos de sensores por dia civil (UTC) — equivalente a
    storage.get_daily_summary(). Agregação feita em SQL (não em Python)
    pela mesma razão documentada em storage.py: uma janela de vários dias
    pode ter dezenas de milhares de registos (IMU a ~14-52/seg)."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    day_expr = func.date(SensorRecord.timestamp_utc, "unixepoch").label("day")
    rows = (
        db.query(
            day_expr,
            func.count().label("record_count"),
            func.avg(SensorRecord.heart_rate).label("avg_hr"),
            func.count(SensorRecord.heart_rate).label("hr_samples"),
            func.min(SensorRecord.steps_count).label("min_steps"),
            func.max(SensorRecord.steps_count).label("max_steps"),
        )
        .filter(SensorRecord.device_id == device_id, SensorRecord.received_at >= cutoff)
        .group_by(day_expr)
        .order_by(day_expr.asc())
        .all()
    )
    return [
        {
            "day": r.day,
            "record_count": r.record_count,
            "avg_hr": r.avg_hr,
            "hr_samples": r.hr_samples,
            "min_steps": r.min_steps,
            "max_steps": r.max_steps,
        }
        for r in rows
    ]


def get_retention_days(db: Session) -> float:
    """Devolve a retenção atualmente configurada (dias), ou
    DEFAULT_RETENTION_DAYS se nunca tiver sido alterada — equivalente a
    storage.get_retention_days()."""
    row = db.query(Setting).filter_by(key=RETENTION_DAYS_SETTING_KEY).first()
    if row is None:
        return DEFAULT_RETENTION_DAYS
    try:
        return float(row.value)
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS


def set_retention_days(db: Session, days) -> float:
    """Atualiza a retenção configurada — equivalente a
    storage.set_retention_days(). Lança ValueError se `days` estiver fora
    dos limites de sanidade."""
    days = float(days)
    if not (MIN_RETENTION_DAYS <= days <= MAX_RETENTION_DAYS):
        raise ValueError(
            f"retenção tem de estar entre {MIN_RETENTION_DAYS} e {MAX_RETENTION_DAYS} dias"
        )
    row = db.query(Setting).filter_by(key=RETENTION_DAYS_SETTING_KEY).first()
    if row is None:
        db.add(Setting(key=RETENTION_DAYS_SETTING_KEY, value=str(days)))
    else:
        row.value = str(days)
    db.commit()
    return days


def insert_activity_correction(
    db: Session, device_id: int, original_category: Optional[str], corrected_category: str
) -> None:
    """Grava uma correção manual do cuidador/equipa clínica à classificação
    de atividade da IA — equivalente a storage.insert_activity_correction()."""
    db.add(ActivityCorrection(
        device_id=device_id,
        original_category=original_category,
        corrected_category=corrected_category,
    ))
    db.commit()


# ============================================================
# POLÍTICAS DE RETENÇÃO
# ============================================================

class DataRetention:
    """Gestão automática de retenção de dados."""

    RETENTION_POLICIES = {
        "sensor_records": 365,  # 1 ano
        "activity_windows": 1825,  # 5 anos
        "alerts": 2555,  # 7 anos
        "emergency_alerts": 3650,  # 10 anos
        "anomaly_detections": 1825,  # 5 anos
        "medication_adherence": 1095,  # 3 anos
    }

    @staticmethod
    def cleanup(db: Session, dry_run: bool = False) -> dict:
        """Executa limpeza de dados antigos conforme políticas."""
        results = {}
        cutoff_date = datetime.utcnow()

        # SensorRecord (apaga mesmo, não soft delete)
        cutoff = cutoff_date - timedelta(days=DataRetention.RETENTION_POLICIES["sensor_records"])
        query = db.query(SensorRecord).filter(SensorRecord.received_at < cutoff)
        count = query.count()
        if not dry_run:
            query.delete()
            db.commit()
        results["sensor_records"] = count

        # ActivityWindow
        cutoff = cutoff_date - timedelta(days=DataRetention.RETENTION_POLICIES["activity_windows"])
        query = db.query(ActivityWindow).filter(ActivityWindow.activity_date < cutoff)
        count = query.count()
        if not dry_run:
            query.delete()
            db.commit()
        results["activity_windows"] = count

        # Alerts (soft delete, marca deleted_at)
        cutoff = cutoff_date - timedelta(days=DataRetention.RETENTION_POLICIES["alerts"])
        query = db.query(Alert).filter(
            and_(Alert.created_at < cutoff, Alert.deleted_at.is_(None))
        )
        count = query.count()
        if not dry_run:
            query.update({"deleted_at": datetime.utcnow()})
            db.commit()
        results["alerts"] = count

        # AnomalyDetection (apaga mesmo, não soft delete)
        cutoff = cutoff_date - timedelta(days=DataRetention.RETENTION_POLICIES["anomaly_detections"])
        query = db.query(AnomalyDetection).filter(AnomalyDetection.created_at < cutoff)
        count = query.count()
        if not dry_run:
            query.delete()
            db.commit()
        results["anomaly_detections"] = count

        # MedicationAdherence (apaga mesmo, não soft delete)
        cutoff = cutoff_date - timedelta(days=DataRetention.RETENTION_POLICIES["medication_adherence"])
        query = db.query(MedicationAdherence).filter(MedicationAdherence.scheduled_datetime < cutoff)
        count = query.count()
        if not dry_run:
            query.delete()
            db.commit()
        results["medication_adherence"] = count

        # emergency_alerts: presente em RETENTION_POLICIES só como referência
        # documental (10 anos) -- nunca processado aqui de propósito, é
        # histórico de segurança mantido para sempre (ver PROJECT_STATUS.md).

        return results


# ============================================================
# EXEMPLO DE USO
# ============================================================

if __name__ == "__main__":
    # Criar tabelas
    create_all_tables()

    # Exemplo: inserir um utilizador
    db = get_db_session()
    new_user = User(
        uuid="usr-001",
        email="joao@example.com",
        password_hash="(seria bcrypt em produção)",
        role="family",
        name="João Silva",
    )
    db.add(new_user)
    db.commit()

    # Exemplo: analytics
    print(Analytics.medication_adherence_summary(db, patient_id=1, days=30))

    # Exemplo: data retention (dry-run)
    print(DataRetention.cleanup(db, dry_run=True))

    db.close()
