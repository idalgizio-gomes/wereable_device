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

import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean, DateTime,
    ForeignKey, Index, Text, JSON, CheckConstraint, UniqueConstraint,
    Table, desc, and_, or_, func, event
)
from sqlalchemy.orm import sessionmaker, relationship, Session, declarative_base
from sqlalchemy.pool import StaticPool
from sqlalchemy.exc import IntegrityError

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
    # GDPR-006 (decisão da utilizadora, 2026-07-31): retenção de 8 anos,
    # soft delete — ver DataRetention.RETENTION_POLICIES["emergency_alerts"]
    # e SECURITY_STATUS.md. Antes desta coluna existir, a política estava
    # só documentada, nunca aplicada (ver comentário histórico em
    # DataRetention.cleanup()).
    deleted_at = Column(DateTime)

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


# Âmbitos de consentimento reconhecidos (mesma lista do comentário de
# `ConsentRecord.scope`, agora como constante para não haver 2 fontes de
# verdade). 'sensor_data' é o único com um ponto de aplicação real hoje
# (bootstrap do bridge, `orm_persistence._ensure_consent`); os restantes
# existem para o dashboard poder gerir consentimento granular por
# categoria de dado (2026-08-05) — cada operação que os usa decide se
# bloqueia ou só regista, não há uma regra única para todos os âmbitos.
CONSENT_SCOPE_SENSOR_DATA = "sensor_data"
CONSENT_SCOPE_ANALYTICS = "analytics"
CONSENT_SCOPE_EXPORT = "export"
CONSENT_SCOPE_RESEARCH = "research"
CONSENT_SCOPES = (
    CONSENT_SCOPE_SENSOR_DATA,
    CONSENT_SCOPE_ANALYTICS,
    CONSENT_SCOPE_EXPORT,
    CONSENT_SCOPE_RESEARCH,
)


def has_valid_consent(db: Session, patient_id: int, scope: str, now: Optional[datetime] = None) -> bool:
    """A decisão MAIS RECENTE (por id, mesmo critério de desempate de
    `_next_consent_version`/`get_consent_status` abaixo — não `signed_at`,
    que pode empatar entre chamadas rápidas) para este paciente+âmbito é
    granted=True e não está expirada?

    CORREÇÃO 2026-08-05: a versão anterior filtrava `granted.is_(True)`
    NA PRÓPRIA QUERY antes de escolher "a mais recente" — ou seja,
    procurava a concessão mais recente entre as concedidas, ignorando por
    completo qualquer revogação (`granted=False`) posterior. Resultado:
    depois de UMA concessão, `grant_consent(..., granted=False)` nunca
    conseguia realmente revogar nada — esta função continuava a devolver
    True para sempre (a não ser que a linha concedida expirasse por
    `expires_at`). Só não foi apanhado antes porque o único chamador
    existente (`_ensure_consent`, scope 'sensor_data') nunca é exercido
    com um cenário de revogação nos testes. Apanhado ao escrever os
    testes da revogação por âmbito (2026-08-05) — ver
    test_has_valid_consent_respects_most_recent_decision."""
    now = now or datetime.utcnow()
    latest = (
        db.query(ConsentRecord)
        .filter(ConsentRecord.patient_id == patient_id, ConsentRecord.scope == scope)
        .order_by(desc(ConsentRecord.id))
        .first()
    )
    if latest is None or not latest.granted:
        return False
    if latest.expires_at is not None and latest.expires_at <= now:
        return False
    return True


def _next_consent_version(db: Session, patient_id: int, scope: str) -> str:
    """Cada mudança de consentimento (conceder ou revogar) grava uma LINHA
    NOVA, nunca reescreve uma anterior — mantém histórico auditável (quem
    consentiu o quê e quando), no espírito do resto do GDPR-001. A versão
    é só um contador sequencial por (patient_id, scope), não um número de
    versão do texto legal do consentimento."""
    last = (
        db.query(ConsentRecord)
        .filter(ConsentRecord.patient_id == patient_id, ConsentRecord.scope == scope)
        .order_by(desc(ConsentRecord.id))
        .first()
    )
    if last is None:
        return "1"
    try:
        return str(int(last.version) + 1)
    except (TypeError, ValueError):
        # Versão antiga não numérica (ex.: consentimento inicial gravado
        # manualmente com outro esquema de versão) — não rebenta, só deixa
        # de conseguir incrementar; carimba com timestamp para garantir
        # unicidade face à UniqueConstraint(patient_id, scope, version).
        return f"v-{int(datetime.utcnow().timestamp())}"


def grant_consent(
    db: Session,
    patient_id: int,
    user_id: int,
    scope: str,
    granted: bool,
    given_by: str = "representative",
    representative_name: Optional[str] = None,
    representative_relationship: Optional[str] = None,
    legal_basis: str = "consent",
    notes: Optional[str] = None,
) -> ConsentRecord:
    """Regista uma decisão de consentimento (conceder OU revogar) para um
    âmbito. Lança ValueError se `scope` não for um dos CONSENT_SCOPES
    reconhecidos — evita âmbitos escritos à mão com erro de ortografia que
    nunca seriam lidos por `has_valid_consent`."""
    if scope not in CONSENT_SCOPES:
        raise ValueError(f"âmbito de consentimento desconhecido: {scope!r} (válidos: {CONSENT_SCOPES})")
    row = ConsentRecord(
        patient_id=patient_id,
        user_id=user_id,
        scope=scope,
        granted=granted,
        version=_next_consent_version(db, patient_id, scope),
        signed_at=datetime.utcnow(),
        given_by=given_by,
        representative_name=representative_name,
        representative_relationship=representative_relationship,
        legal_basis=legal_basis,
        notes=notes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_consent_status(db: Session, patient_id: int) -> dict:
    """Estado atual (mais recente) de cada âmbito reconhecido, para a
    utilização do dashboard: `{scope: {granted, signed_at, version,
    given_by} | None}`. `None` significa "nunca decidido para este
    âmbito" — distinto de `granted=False` ("decidido, e negado/revogado"),
    porque o dashboard trata os dois casos de forma diferente (pedir
    consentimento vs. mostrar que foi recusado)."""
    status = {}
    for scope in CONSENT_SCOPES:
        latest = (
            db.query(ConsentRecord)
            .filter(ConsentRecord.patient_id == patient_id, ConsentRecord.scope == scope)
            .order_by(desc(ConsentRecord.id))
            .first()
        )
        if latest is None:
            status[scope] = None
        else:
            status[scope] = {
                "granted": latest.granted,
                "signed_at": latest.signed_at.replace(tzinfo=timezone.utc).timestamp() if latest.signed_at else None,
                "version": latest.version,
                "given_by": latest.given_by,
                "expires_at": latest.expires_at.replace(tzinfo=timezone.utc).timestamp() if latest.expires_at else None,
            }
    return status


# ============================================================
# BASELINE COMPORTAMENTAL PERSONALIZADA (2026-08-05)
# ------------------------------------------------------------
# `PersonalizedThreshold` já existia no esquema (ver classe acima) mas sem
# NENHUMA lógica a lê-la ou escrevê-la em lado nenhum do bridge — os
# alertas de sinais vitais (FC/SpO2 fora do esperado) que aparecem no
# dashboard eram só dados de demonstração fixos (EMERGENCY_LOG/alerts em
# web/dashboard/index.html), nunca calculados a partir de leituras reais.
# Esta secção liga o esquema já existente a uma avaliação real (ver
# bridge/vital_alerts.py) e dá ao dashboard forma de o consultar/editar.
# ============================================================

# Valores por omissão — ponto de partida genérico enquanto o cuidador não
# definir limiares próprios para este paciente (nunca uma recomendação
# clínica validada; documentado como tal, mesmo espírito de
# ACTIVITY_ML_DISCLAIMER em activity_inference.py). FC de repouso adulto
# típica 60-100bpm (alargada para 50-100 para reduzir falsos positivos
# num protótipo sem validação clínica); SpO2 normal >=95%, alerta comum a
# partir de <92% (linha usada em oximetria de pulso doméstica).
DEFAULT_THRESHOLDS = {
    "heart_rate_min": 50,
    "heart_rate_max": 100,
    "spo2_min": 92,
    "inactivity_threshold_seconds": 3600,
    "sleep_target_minutes": 420,
    "activity_target_minutes": 60,
    "steps_target_daily": 3000,
}

# Limites de sanidade (min, max) por campo — nunca aceitar um valor fora
# disto, venha do dashboard ou de onde vier (mesmo raciocínio de
# MIN_RETENTION_DAYS/MAX_RETENTION_DAYS acima).
THRESHOLD_BOUNDS = {
    "heart_rate_min": (20, 150),
    "heart_rate_max": (40, 220),
    "spo2_min": (70, 100),
    "inactivity_threshold_seconds": (60, 24 * 3600),
    "sleep_target_minutes": (60, 900),
    "activity_target_minutes": (0, 900),
    "steps_target_daily": (0, 50000),
}


def get_thresholds(db: Session, patient_id: int) -> dict:
    """Limiares personalizados do paciente, com fallback campo-a-campo para
    DEFAULT_THRESHOLDS (não só quando a linha inteira não existe — também
    quando existe mas um campo em concreto nunca foi definido, NULL).
    Nunca cria uma linha só por ser lida (get, não get-or-create) —
    'is_default' distingue os dois casos para o dashboard poder mostrar
    "ainda não personalizado" em vez de fingir que foi uma escolha."""
    row = db.query(PersonalizedThreshold).filter_by(patient_id=patient_id).first()
    if row is None:
        return {**DEFAULT_THRESHOLDS, "is_default": True, "updated_at": None}
    values = {}
    for field, default in DEFAULT_THRESHOLDS.items():
        value = getattr(row, field)
        values[field] = value if value is not None else default
    values["is_default"] = False
    values["updated_at"] = row.updated_at.replace(tzinfo=timezone.utc).timestamp() if row.updated_at else None
    return values


def set_thresholds(db: Session, patient_id: int, **fields) -> dict:
    """Atualização PARCIAL (get-or-create) — só os campos passados mudam,
    os restantes mantêm o que já lá estava (ou continuam a usar o
    fallback por omissão de get_thresholds, se nunca tiverem sido
    definidos). Lança ValueError se um campo for desconhecido ou estiver
    fora de THRESHOLD_BOUNDS — nunca grava um limiar fisiologicamente
    absurdo (ex.: heart_rate_max=5) só porque veio de um formulário."""
    for field, value in fields.items():
        if field not in DEFAULT_THRESHOLDS:
            raise ValueError(f"limiar desconhecido: {field!r} (válidos: {tuple(DEFAULT_THRESHOLDS)})")
        lo, hi = THRESHOLD_BOUNDS[field]
        if not (lo <= value <= hi):
            raise ValueError(f"{field}={value!r} fora do intervalo de sanidade [{lo}, {hi}]")

    # Busca (sem criar ainda) para poder validar heart_rate_min/max contra
    # os valores EFETIVOS finais (novo valor se vier nesta chamada, senão o
    # já gravado, senão o padrão) — não só os dois campos desta chamada.
    # Sem isto, mudar só heart_rate_min para acima de um heart_rate_max já
    # gravado passava despercebido. A busca fica ANTES de qualquer
    # db.add(): uma validação que falha aqui não pode deixar uma linha
    # NOVA pendurada na sessão (add() sem commit ainda conta para
    # autoflush — um commit futuro e não relacionado, na mesma sessão de
    # longa duração do bridge, arrastaria essa linha fantasma).
    existing = db.query(PersonalizedThreshold).filter_by(patient_id=patient_id).first()
    effective_hr_min = fields.get(
        "heart_rate_min",
        existing.heart_rate_min if existing and existing.heart_rate_min is not None else DEFAULT_THRESHOLDS["heart_rate_min"],
    )
    effective_hr_max = fields.get(
        "heart_rate_max",
        existing.heart_rate_max if existing and existing.heart_rate_max is not None else DEFAULT_THRESHOLDS["heart_rate_max"],
    )
    if effective_hr_min >= effective_hr_max:
        raise ValueError(
            f"heart_rate_min ({effective_hr_min}) tem de ser menor que heart_rate_max ({effective_hr_max})"
        )

    row = existing
    if row is None:
        row = PersonalizedThreshold(patient_id=patient_id)
        db.add(row)
    for field, value in fields.items():
        setattr(row, field, int(value))
    db.commit()
    db.refresh(row)
    return get_thresholds(db, patient_id)


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
        "emergency_alerts": 2920,  # 8 anos (decisão da utilizadora, 2026-07-31 — GDPR-006)
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

        # EmergencyAlert (soft delete, mesmo padrão de Alert) — GDPR-006
        # (decisão da utilizadora, 2026-07-31): 8 anos, já não "para sempre".
        # Base em created_at (data de ingestão pelo bridge), não em
        # timestamp_utc (hora do evento no firmware, inteiro epoch) — mesma
        # base temporal usada por "alerts", suficientemente próxima da hora
        # real do evento para uma janela de retenção de anos.
        cutoff = cutoff_date - timedelta(days=DataRetention.RETENTION_POLICIES["emergency_alerts"])
        query = db.query(EmergencyAlert).filter(
            and_(EmergencyAlert.created_at < cutoff, EmergencyAlert.deleted_at.is_(None))
        )
        count = query.count()
        if not dry_run:
            query.update({"deleted_at": datetime.utcnow()})
            db.commit()
        results["emergency_alerts"] = count

        return results


# ============================================================
# PERFIL DE EMERGÊNCIA (emergencyProfileChar, ver src/Ble/Ble.cpp) —
# subconjunto curado de dados de saúde do paciente, servido pelo firmware
# por leitura BLE só depois de pairing/bonding (ver PROJECT_STATUS.md,
# 2026-07-31, "Nova characteristic GATT de leitura de emergência"). Função
# pura (só lê `db`, nunca escreve nada) para ser fácil de testar isolada —
# quem grava o resultado no dispositivo é `ble_bridge.py`.
# ============================================================

# Tem de bater certo com EMERGENCY_PROFILE_MAX_LEN em
# include/Storage/Storage.h (teto do SoftDevice para um atributo GATT de
# tamanho variável, BLE_GATTS_VAR_ATTR_LEN_MAX) — é o orçamento de bytes
# usado pela política de corte abaixo.
EMERGENCY_PROFILE_MAX_LEN = 512


def build_emergency_profile_payload(db: Session, patient_id: int) -> bytes:
    """Constrói o JSON (UTF-8, <= EMERGENCY_PROFILE_MAX_LEN bytes) escrito
    em emergencyProfileWriteChar e depois servido por leitura em
    emergencyProfileChar.

    Chaves: "name" (Patient.name), "ec" (contacto de emergência —
    Patient.emergency_contact_*, omitido se nenhum dos três campos estiver
    preenchido), "cond"/"alrg" (PatientCondition/PatientAllergy ativos —
    `.display_text` já vem decifrado pela property do modelo), "med"
    (Medication ativa: não soft-deletada e sem end_date já passado).
    Chaves de listas vazias são omitidas (poupa bytes, e é o que o
    dashboard/app já vai assumir ao verificar "cond" in perfil).

    Se o JSON completo exceder EMERGENCY_PROFILE_MAX_LEN bytes (paciente
    com muitas condições/alergias/medicações — não há precedente disto no
    codebase, política nova), corta-se itens do FIM de "cond", depois
    "med", depois "alrg" — por esta ordem porque alergias são a informação
    mais crítica em emergência (risco real de reação a algo administrado
    pelo socorrista), por isso são a última coisa a perder — até caber, e
    marca-se "trunc": true para o dashboard/app poder avisar que a lista
    pode estar incompleta. Nome e contacto de emergência NUNCA são
    cortados, mesmo que o payload continue a exceder o limite depois de
    esvaziar as três listas (caso extremo, só possível com um nome/
    contacto muito longos) — quem escreve isto por BLE (ble_bridge.py)
    fica sujeito aos limites físicos do protocolo nesse cenário.
    """
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.deleted_at.is_(None))
        .first()
    )
    if patient is None:
        raise ValueError(f"paciente {patient_id} nao encontrado (ou soft-deleted)")

    payload: dict = {"name": patient.name}

    ec = {}
    if patient.emergency_contact_name:
        ec["name"] = patient.emergency_contact_name
    if patient.emergency_contact_phone:
        ec["phone"] = patient.emergency_contact_phone
    if patient.emergency_contact_relation:
        ec["relation"] = patient.emergency_contact_relation
    if ec:
        payload["ec"] = ec

    conditions = (
        db.query(PatientCondition)
        .filter(PatientCondition.patient_id == patient_id, PatientCondition.deleted_at.is_(None))
        .order_by(PatientCondition.id)
        .all()
    )
    if conditions:
        payload["cond"] = [c.display_text for c in conditions]

    allergies = (
        db.query(PatientAllergy)
        .filter(PatientAllergy.patient_id == patient_id, PatientAllergy.deleted_at.is_(None))
        .order_by(PatientAllergy.id)
        .all()
    )
    if allergies:
        payload["alrg"] = [a.display_text for a in allergies]

    # Medicação "atual": não soft-deletada e ainda não terminada (end_date
    # NULL = sem data de fim prevista, ou end_date no futuro/agora).
    # datetime.utcnow() (naive), não datetime.now(timezone.utc) — mesmo
    # precedente do resto deste ficheiro (ver Analytics.
    # medication_adherence_summary/get_records_since acima): só é preciso
    # a variante "aware" quando se chama .timestamp() a seguir para
    # comparar contra uma coluna inteira de epoch, o que não é o caso aqui
    # (Medication.end_date é DateTime nativo).
    now = datetime.utcnow()
    medications = (
        db.query(Medication)
        .filter(
            Medication.patient_id == patient_id,
            Medication.deleted_at.is_(None),
            or_(Medication.end_date.is_(None), Medication.end_date >= now),
        )
        .order_by(Medication.id)
        .all()
    )
    if medications:
        payload["med"] = [
            {"name": m.name, "dosage": m.dosage, "frequency": m.frequency}
            for m in medications
        ]

    def _dump(p: dict) -> bytes:
        # separators sem espaço + ensure_ascii=False: minimiza bytes (o
        # orçamento é apertado) e mantém acentuação em UTF-8 real em vez de
        # escapes \uXXXX (que gastariam 6 bytes por carácter acentuado em
        # vez de 2).
        return json.dumps(p, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    data = _dump(payload)
    if len(data) > EMERGENCY_PROFILE_MAX_LEN:
        # "trunc": true e' adicionado ANTES do loop de corte (nao depois),
        # de proposito: e' preciso contar os bytes do proprio marcador no
        # orcamento enquanto se decide quantos itens cortar, senao o loop
        # podia parar exatamente no limite e a flag acrescentada a seguir
        # empurrava o payload de volta para cima de EMERGENCY_PROFILE_MAX_LEN
        # (bug real, apanhado por
        # tests/test_storage_advanced.py::TestBuildEmergencyProfilePayload::
        # test_payload_never_exceeds_max_len_and_sets_trunc_flag).
        payload["trunc"] = True
        data = _dump(payload)
        for key in ("cond", "med", "alrg"):
            while key in payload and payload[key] and len(data) > EMERGENCY_PROFILE_MAX_LEN:
                payload[key].pop()
                if not payload[key]:
                    del payload[key]
                data = _dump(payload)
            if len(data) <= EMERGENCY_PROFILE_MAX_LEN:
                break
        print(f"[STORAGE] AVISO: perfil de emergencia do paciente {patient_id} excedia "
              f"{EMERGENCY_PROFILE_MAX_LEN} bytes -- itens de condicoes/alergias/medicacao "
              f"foram cortados (nome/contacto de emergencia nunca sao cortados)")
        if len(data) > EMERGENCY_PROFILE_MAX_LEN:
            print(f"[STORAGE] AVISO GRAVE: perfil de emergencia do paciente {patient_id} "
                  f"excede {EMERGENCY_PROFILE_MAX_LEN} bytes mesmo so' com nome+contacto de "
                  f"emergencia -- nao ha mais nada que se possa cortar sem violar a regra de "
                  f"nunca cortar nome/contacto")

    return data


# ============================================================
# TIMELINE CORRELACIONADA POR EPISÓDIO (2026-08-05)
# ------------------------------------------------------------
# Motivação: hoje, quando surge um alerta de emergência (SOS/queda), o
# cuidador só vê o alerta isolado no dashboard -- não vê o que estava a
# acontecer nos minutos antes/depois (FC a subir? o paciente estava
# classificado como "Atividade" ou "Descanso"? houve outro alerta
# próximo?). Esta secção junta esses dados dispersos (SensorRecord,
# ActivityWindow, EmergencyAlert) numa timeline única, ordenada no tempo,
# centrada num alerta de emergência concreto -- sem inventar nenhuma
# correlação estatística, só reunir o que já existe em tabelas separadas.
# ============================================================


def _activity_window_epoch_range(window: "ActivityWindow") -> tuple[int, int]:
    """Reconstrói (start_ts_approx, end_ts_approx), em epoch, a partir de
    `ActivityWindow.start_time`/`end_time` (MINUTOS DESDE A MEIA-NOITE
    LOCAL do bridge -- não epoch, ver `insert_activity_window` em
    orm_persistence.py, que os grava a partir de `time.localtime()` do
    relógio do bridge) e `activity_date` (a data desse dia).

    APROXIMAÇÃO, não a hora real do dispositivo -- mesmo tipo de limitação
    já assumida em DAY_SESSION_START_HOUR (bridge/activity_inference.py):
    os dois campos nasceram em rotinas diferentes e nunca houve unificação
    de representação de tempo, tal como o comentário de CLASS_TO_DB_CATEGORY
    (mesmo ficheiro) documenta para o vocabulário de categorias de
    atividade -- este é o mesmo tipo de inconsistência já assumida no
    projeto, desta vez de REPRESENTAÇÃO DE TEMPO, não de vocabulário.
    `time.mktime()` interpreta a hora reconstruída como hora LOCAL do
    servidor onde o bridge corre (não do dispositivo, que pode estar
    noutro fuso) -- a melhor aproximação disponível sem alterar o esquema
    (ver PersonalizedThreshold/ActivityWindow acima, sem coluna de fuso
    horário nenhuma)."""
    day_start_local = datetime.combine(window.activity_date.date(), datetime.min.time())
    start_local = day_start_local + timedelta(minutes=window.start_time or 0)
    end_local = day_start_local + timedelta(minutes=window.end_time or 0)
    start_ts = int(time.mktime(start_local.timetuple()))
    end_ts = int(time.mktime(end_local.timetuple()))
    return start_ts, end_ts


def build_episode_timeline(db: Session, device_id: int, center_ts: int, window_minutes: int = 30) -> dict:
    """Reúne, numa única estrutura ordenada no tempo, os dados dispersos à
    volta de um instante `center_ts` (Unix epoch, segundos -- tipicamente o
    `timestamp_utc` de um `EmergencyAlert`): sinais vitais (downsampled por
    minuto), blocos de classificação de atividade que se sobrepõem à
    janela, e outros alertas de emergência próximos. Janela aplicada para
    AMBOS os lados: [center_ts - window_minutes*60, center_ts +
    window_minutes*60].

    Nunca lança exceção por "não haver dados" -- uma janela vazia devolve
    listas vazias, é um resultado válido (ex.: dispositivo sem sensores
    ligados nesse período, ou sem outros alertas por perto)."""
    window_start = int(center_ts - window_minutes * 60)
    window_end = int(center_ts + window_minutes * 60)

    # ---- sensor_summary: downsampling por minuto, feito em SQL (não em
    # Python) pela mesma razão documentada em get_daily_summary() acima --
    # o IMU grava a ~14-52Hz, uma janela de 60 min podia ter dezenas de
    # milhares de linhas em bruto. func.avg()/func.count() ignoram NULL
    # nativamente (semântica SQL padrão), o que dá exatamente "média entre
    # os registos não-nulos desse minuto" e "ignora minutos sem nenhuma
    # leitura" sem lógica extra em Python. Filtra por `received_at` (não
    # `timestamp_utc`) pelo mesmo critério de get_records_since() -- ver o
    # comentário lá para o porquê.
    received_start = datetime.fromtimestamp(window_start, tz=timezone.utc).replace(tzinfo=None)
    received_end = datetime.fromtimestamp(window_end, tz=timezone.utc).replace(tzinfo=None)
    # Agrupa por minuto do relógio do DISPOSITIVO (timestamp_utc truncado),
    # não do bridge (received_at) -- é o timestamp_utc que é diretamente
    # comparável a center_ts (também um timestamp_utc de EmergencyAlert).
    # Divisão inteira com "//" (não "/"): o SQLAlchemy insere um CAST para
    # NUMERIC em "/" sobre colunas Integer (força divisão em vírgula
    # flutuante, cross-dialect) -- "//" mantém os dois operandos como
    # inteiros e trunca ao minuto como pretendido (confirmado a correr
    # contra SQLite real, ver bridge/tests/test_episode_timeline.py).
    minute_expr = ((SensorRecord.timestamp_utc // 60) * 60).label("minute_ts")
    sensor_rows = (
        db.query(
            minute_expr,
            func.avg(SensorRecord.heart_rate).label("avg_hr"),
            func.count(SensorRecord.heart_rate).label("hr_n"),
            func.avg(SensorRecord.spo2_percent).label("avg_spo2"),
            func.count(SensorRecord.spo2_percent).label("spo2_n"),
        )
        .filter(
            SensorRecord.device_id == device_id,
            SensorRecord.received_at >= received_start,
            SensorRecord.received_at <= received_end,
        )
        .group_by(minute_expr)
        .having(or_(func.count(SensorRecord.heart_rate) > 0, func.count(SensorRecord.spo2_percent) > 0))
        .order_by(minute_expr.asc())
        .all()
    )
    sensor_summary = [
        {
            "ts": int(r.minute_ts),
            "hr": int(round(r.avg_hr)) if r.avg_hr is not None else None,
            "spo2": int(round(r.avg_spo2)) if r.avg_spo2 is not None else None,
        }
        for r in sensor_rows
    ]

    # ---- activity_blocks: ActivityWindow acumula muito mais devagar que
    # SensorRecord (poucos blocos por dia, não dezenas por segundo) -- um
    # pré-filtro largo (+-2 dias) por activity_date evita carregar todo o
    # histórico do dispositivo, e o filtro exato de sobreposição acontece
    # depois em Python, sobre os epochs reconstruídos por
    # _activity_window_epoch_range (só é possível calcular a sobreposição
    # depois de reconstruir, porque start_time/end_time não são epoch --
    # ver essa função).
    coarse_start = received_start - timedelta(days=2)
    coarse_end = received_end + timedelta(days=2)
    candidate_windows = (
        db.query(ActivityWindow)
        .filter(
            ActivityWindow.device_id == device_id,
            ActivityWindow.activity_date >= coarse_start,
            ActivityWindow.activity_date <= coarse_end,
        )
        .all()
    )
    activity_blocks = []
    for w in candidate_windows:
        if w.start_time is None or w.end_time is None:
            continue  # dados incompletos -- sem epoch fiável, não se inclui
        start_ts_approx, end_ts_approx = _activity_window_epoch_range(w)
        if end_ts_approx >= window_start and start_ts_approx <= window_end:
            activity_blocks.append({
                "category": w.activity_category,
                "start_ts_approx": start_ts_approx,
                "end_ts_approx": end_ts_approx,
                "duration_minutes": w.duration_minutes,
                "confidence": w.confidence,
            })
    activity_blocks.sort(key=lambda b: b["start_ts_approx"])

    # ---- nearby_emergency_alerts: todos os EmergencyAlert do mesmo
    # dispositivo dentro da janela (excluindo soft-deletados, GDPR-006).
    # Esta função não sabe qual é "o alerta central" (só recebe center_ts,
    # um número simples) -- a exclusão do próprio alerta central é feita
    # por get_episode_timeline_for_alert() abaixo, que É quem sabe o
    # sequence_number a excluir.
    alert_rows = (
        db.query(EmergencyAlert)
        .filter(
            EmergencyAlert.device_id == device_id,
            EmergencyAlert.timestamp_utc >= window_start,
            EmergencyAlert.timestamp_utc <= window_end,
            EmergencyAlert.deleted_at.is_(None),
        )
        .order_by(EmergencyAlert.timestamp_utc.asc())
        .all()
    )
    nearby_emergency_alerts = [
        {
            "alert_type": a.alert_type,
            "timestamp_utc": a.timestamp_utc,
            "sequence_number": a.sequence_number,
        }
        for a in alert_rows
    ]

    return {
        "center_ts": int(center_ts),
        "window_minutes": window_minutes,
        "sensor_summary": sensor_summary,
        "activity_blocks": activity_blocks,
        "nearby_emergency_alerts": nearby_emergency_alerts,
    }


def get_episode_timeline_for_alert(
    db: Session, device_id: int, sequence_number: int, window_minutes: int = 30
) -> dict:
    """Timeline centrada num EmergencyAlert concreto, identificado por
    (device_id, sequence_number) -- a mesma chave da UniqueConstraint
    uq_emergency_device_seq. Lança ValueError se o alerta não existir (ou
    estiver soft-deletado, GDPR-006) -- ao contrário de
    build_episode_timeline(), aqui "não encontrado" é um erro do chamador
    (sequence_number errado), não uma janela vazia legítima."""
    alert = (
        db.query(EmergencyAlert)
        .filter(
            EmergencyAlert.device_id == device_id,
            EmergencyAlert.sequence_number == sequence_number,
            EmergencyAlert.deleted_at.is_(None),
        )
        .first()
    )
    if alert is None:
        raise ValueError(
            f"alerta de emergencia nao encontrado: device_id={device_id} sequence_number={sequence_number}"
        )
    result = build_episode_timeline(db, device_id, alert.timestamp_utc, window_minutes)
    # Exclui o próprio alerta central de "nearby" -- já vai à parte em
    # result["alert"]. Comparação por sequence_number (não por identidade
    # de objeto ORM -- nearby_emergency_alerts já são dicts simples nesta
    # altura, não instâncias de EmergencyAlert).
    result["nearby_emergency_alerts"] = [
        a for a in result["nearby_emergency_alerts"] if a["sequence_number"] != sequence_number
    ]
    result["alert"] = {
        "alert_type": alert.alert_type,
        "timestamp_utc": alert.timestamp_utc,
        "sequence_number": alert.sequence_number,
    }
    return result


# ============================================================
# VERSIONAMENTO E ROLLBACK DO MODELO ML (2026-08-05)
# ------------------------------------------------------------
# Motivação: `activity_inference.py::_load_model()` carregava sempre o
# mesmo caminho fixo (ml/models/activity_classifier_rf.joblib +
# _labels.json), sem histórico de versões nem forma de voltar atrás se um
# modelo retreinado se revelar pior. Esta secção guarda o registo de
# versões NA BASE DE DADOS (não num ficheiro solto ao lado do .joblib) e
# dá ao bridge/dashboard forma de trocar a versão ativa em runtime — ver
# activity_inference.py (_load_model/reload_active_model) e ble_bridge.py
# (cmds "list_model_versions"/"activate_model_version").
# ============================================================


class MlModelVersion(Base):
    """Registo de versões do modelo de classificação de atividade (ML) —
    permite trocar a versão ativa em runtime e reverter para uma anterior
    sem reiniciar o bridge."""
    __tablename__ = "ml_model_versions"

    id = Column(Integer, primary_key=True)
    model_name = Column(String(50), nullable=False)  # ex. "activity_classifier_rf"
    version = Column(String(50), nullable=False)      # ex. "1", "2", timestamp, etc.
    file_path = Column(String(500), nullable=False)   # caminho relativo a ml/, ex. "models/activity_classifier_rf_v2.joblib"
    labels_path = Column(String(500), nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)
    trained_at = Column(DateTime)
    metrics_json = Column(Text)   # JSON livre: accuracy, etc. — sem obrigar a um esquema fixo
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("model_name", "version", name="uq_ml_model_name_version"),
    )


def _ml_model_version_to_dict(row: "MlModelVersion") -> dict:
    """Formato comum devolvido pelos 4 helpers abaixo — um único sítio a
    decidir o shape exposto ao bridge/dashboard, mesmo raciocínio do resto
    do ficheiro (ex. _sensor_record_to_dict). `metrics` é desserializado
    de volta de `metrics_json` (None se nunca foi passado nenhum)."""
    return {
        "id": row.id,
        "model_name": row.model_name,
        "version": row.version,
        "file_path": row.file_path,
        "labels_path": row.labels_path,
        "is_active": row.is_active,
        "trained_at": row.trained_at.replace(tzinfo=timezone.utc).timestamp() if row.trained_at else None,
        "metrics": json.loads(row.metrics_json) if row.metrics_json is not None else None,
        "notes": row.notes,
        "created_at": row.created_at.replace(tzinfo=timezone.utc).timestamp() if row.created_at else None,
    }


def register_model_version(
    db: Session,
    model_name: str,
    version: str,
    file_path: str,
    labels_path: str,
    trained_at: Optional[datetime] = None,
    metrics: Optional[dict] = None,
    notes: Optional[str] = None,
    activate: bool = False,
) -> dict:
    """Regista uma nova versão do modelo `model_name`. Lança ValueError se
    já existir uma versão com o mesmo (model_name, version) — apanha o
    IntegrityError da UniqueConstraint uq_ml_model_name_version, faz
    rollback e relança com mensagem clara; nunca deixa o IntegrityError cru
    do SQLAlchemy propagar até a quem chamou (mesmo padrão de
    insert_emergency_alert em orm_persistence.py, mas aqui o duplicado É
    um erro do chamador, não um replay legítimo a ignorar).

    Se `activate=True`, ativa esta versão logo a seguir, reutilizando
    `activate_model_version` (não duplica a lógica de "só uma ativa de
    cada vez")."""
    row = MlModelVersion(
        model_name=model_name,
        version=str(version),
        file_path=file_path,
        labels_path=labels_path,
        trained_at=trained_at,
        metrics_json=json.dumps(metrics) if metrics is not None else None,
        notes=notes,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError(
            f"já existe uma versão {version!r} registada para o modelo {model_name!r}"
        )
    db.refresh(row)
    if activate:
        return activate_model_version(db, model_name, row.version)
    return _ml_model_version_to_dict(row)


def list_model_versions(db: Session, model_name: str) -> list[dict]:
    """Todas as versões registadas de `model_name`, mais recentes primeiro
    (por id — mesmo critério de desempate do resto do ficheiro, ver
    has_valid_consent/_next_consent_version acima)."""
    rows = (
        db.query(MlModelVersion)
        .filter(MlModelVersion.model_name == model_name)
        .order_by(desc(MlModelVersion.id))
        .all()
    )
    return [_ml_model_version_to_dict(r) for r in rows]


def activate_model_version(db: Session, model_name: str, version: str) -> dict:
    """Marca a versão pedida como ativa e TODAS as outras do mesmo
    `model_name` como inativas — nunca podem ficar duas ativas em
    simultâneo. Feito numa única transação (sem lock explícito — SQLite/o
    padrão do resto do ficheiro já não usa). Lança ValueError se a versão
    não existir para este modelo."""
    target = (
        db.query(MlModelVersion)
        .filter(MlModelVersion.model_name == model_name, MlModelVersion.version == str(version))
        .first()
    )
    if target is None:
        raise ValueError(f"versão {version!r} não encontrada para o modelo {model_name!r}")
    db.query(MlModelVersion).filter(
        MlModelVersion.model_name == model_name, MlModelVersion.id != target.id
    ).update({"is_active": False}, synchronize_session=False)
    target.is_active = True
    db.commit()
    db.refresh(target)
    return _ml_model_version_to_dict(target)


def get_active_model_version(db: Session, model_name: str) -> Optional[dict]:
    """A versão ativa registada para `model_name`, ou None se nenhuma
    estiver registada ainda — caso do arranque a frio contra uma BD nova,
    tratado por activity_inference.py::_load_model() como "usa o caminho
    fixo de sempre e regista essa carga como a versão inicial"."""
    row = (
        db.query(MlModelVersion)
        .filter(MlModelVersion.model_name == model_name, MlModelVersion.is_active.is_(True))
        .first()
    )
    return _ml_model_version_to_dict(row) if row is not None else None


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
