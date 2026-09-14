#!/usr/bin/env python3
"""storage_advanced.py — Persistência SQLAlchemy ORM: schema completo, queries
analíticas, retenção automática, cifra de campos sensíveis (NIF, morada)."""

from __future__ import annotations

import json
import os
import secrets
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

DB_URL = os.environ.get("DATABASE_URL", "sqlite:///./carewear.db")

if DB_URL.startswith("sqlite"):
    engine = create_engine(
        DB_URL,
        connect_args={"check_same_thread": False} if "sqlite" in DB_URL else {},
        poolclass=StaticPool if "sqlite:///:memory:" in DB_URL else None,
    )
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        # WAL + synchronous=NORMAL: evita fsync por commit (bloquearia o event loop asyncio do bridge)
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()
else:
    engine = create_engine(DB_URL, echo=False, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# tabela de associação muitos-para-muitos cuidadores<->pacientes
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
    """Utilizador (família, clínico, admin de sistema, admin clínico).

    `admin` = Admin de Sistema (sem acesso livre a dados clínicos); acesso
    clínico privilegiado é o papel `admin_clinical`, sujeito a motivo
    obrigatório, concessão temporal (`privileged_access_expires_at`) e
    auditoria própria — ver `_authorize_patient` em api.py.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(
        String(20),
        CheckConstraint("role IN ('family', 'clinician', 'admin', 'admin_clinical')"),
        nullable=False,
    )
    privileged_access_expires_at = Column(DateTime)  # só para admin_clinical; NULL/passado = sem acesso
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
    # pseudonimização RGPD Art. 4(5): id opaco, reversível só via get_patient_by_pseudonym()
    pseudonym = Column(String(32), unique=True, nullable=False, default=lambda: secrets.token_urlsafe(16))
    name = Column(String(255), nullable=False)
    date_of_birth = Column(DateTime, nullable=False)
    # AES-256-GCM (chave via Argon2id) através das properties nif/address abaixo; nunca atribuir direto
    nif_encrypted = Column(String(512))  # aprovação obrigatória, ver dashboard
    address_encrypted = Column(String(512))
    # phone/emergency_contact_* ainda não cifrados (fixados no schema.sql/migração Alembic; próximo passo)
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
    """Doença/diagnóstico do paciente — uma linha por entrada. Inspirado no
    recurso `Condition` do HL7 FHIR (code_system/code opcionais, ex. ICD-10)."""
    __tablename__ = "patient_conditions"

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), unique=True, nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    display_text_encrypted = Column(String(512), nullable=False)  # cifrado, dado de saúde (categoria especial RGPD)
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
    """Alergia do paciente — tabela separada de PatientCondition (FHIR trata
    `AllergyIntolerance` como recurso próprio; NFC/dashboard precisam listar
    alergias isoladamente de condições crónicas em emergência)."""
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

    # UniqueConstraint (não só Index) — impede duplicados sob concorrência (TOCTOU); ver
    # tratamento de IntegrityError em record_medication_adherence (bridge/api.py)
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
    deleted_at = Column(DateTime)  # soft delete, retenção 8 anos (GDPR-006)

    device = relationship("Device", back_populates="emergency_alerts")

    __table_args__ = (
        Index("idx_emergency_device_timestamp", "device_id", "timestamp_utc"),
        Index("idx_emergency_responded", "responded_at"),
        UniqueConstraint("device_id", "sequence_number", name="uq_emergency_device_seq"),
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
    """Par chave/valor de configuração global do bridge (hoje só `retention_days`)."""
    __tablename__ = "settings"

    key = Column(String(100), primary_key=True)
    value = Column(String(255), nullable=False)


class ActivityCorrection(Base):
    """Correção manual do cuidador/equipa clínica à classificação de atividade da IA."""
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
    # GDPR-001 — quem consentiu: o próprio ('patient') ou um representante legal ('representative')
    given_by = Column(
        String(20),
        CheckConstraint("given_by IN ('patient', 'representative')"),
        nullable=False, default="patient",
    )
    representative_relationship = Column(String(50))  # ex. "filho(a)", só se given_by='representative'
    representative_name = Column(String(255))
    # base legal RGPD: 'consent' (Art. 6(1)(a)) normal; 'vital_interest' (Art. 6(1)(d)) p/ emergência
    legal_basis = Column(String(50), nullable=False, default="consent")

    __table_args__ = (
        UniqueConstraint("patient_id", "scope", "version", name="uq_consent_patient_scope_version"),
    )


# âmbitos de consentimento reconhecidos; só 'sensor_data' tem ponto de aplicação real hoje (orm_persistence._ensure_consent)
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


def get_patient_by_pseudonym(db: Session, pseudonym: str) -> Optional[Patient]:
    return db.query(Patient).filter(Patient.pseudonym == pseudonym).first()


def has_valid_consent(db: Session, patient_id: int, scope: str, now: Optional[datetime] = None) -> bool:
    """A decisão mais recente (por id, não signed_at) é granted=True e não expirou?
    Nota: escolhe a mais recente ENTRE TODAS as decisões, não só as concedidas —
    senão uma revogação (granted=False) posterior nunca teria efeito."""
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
    """Cada mudança grava uma linha nova (histórico auditável). Versão = contador
    sequencial por (patient_id, scope), não o nº de versão do texto legal."""
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
        return f"v-{int(datetime.utcnow().timestamp())}"  # versão antiga não numérica; carimba p/ manter unicidade


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
    """Regista uma decisão de consentimento (conceder ou revogar). Lança ValueError
    se `scope` não for reconhecido em CONSENT_SCOPES."""
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
    """Estado atual de cada âmbito: `{scope: {granted, signed_at, version, given_by} | None}`.
    None = nunca decidido (distinto de granted=False = recusado/revogado)."""
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


# baseline comportamental personalizada — liga PersonalizedThreshold a vital_alerts.py

# valores por omissão, não recomendação clínica validada (mesmo espírito de ACTIVITY_ML_DISCLAIMER)
DEFAULT_THRESHOLDS = {
    "heart_rate_min": 50,
    "heart_rate_max": 100,
    "spo2_min": 92,
    "inactivity_threshold_seconds": 3600,
    "sleep_target_minutes": 420,
    "activity_target_minutes": 60,
    "steps_target_daily": 3000,
}

# limites de sanidade (min, max) por campo, nunca aceitar valor fora disto
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
    """Limiares personalizados, com fallback campo-a-campo para DEFAULT_THRESHOLDS.
    Nunca cria linha só por ser lida; 'is_default' indica se ainda não foi personalizado."""
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
    """Atualização parcial (get-or-create): só os campos passados mudam.
    Lança ValueError se campo desconhecido ou fora de THRESHOLD_BOUNDS."""
    for field, value in fields.items():
        if field not in DEFAULT_THRESHOLDS:
            raise ValueError(f"limiar desconhecido: {field!r} (válidos: {tuple(DEFAULT_THRESHOLDS)})")
        lo, hi = THRESHOLD_BOUNDS[field]
        if not (lo <= value <= hi):
            raise ValueError(f"{field}={value!r} fora do intervalo de sanidade [{lo}, {hi}]")

    # valida heart_rate_min/max contra os valores efetivos finais (não só os desta chamada);
    # busca ANTES de qualquer db.add() para não deixar linha fantasma pendurada na sessão
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


def create_all_tables():
    """Cria todas as tabelas (use só para desenvolvimento — em produção use Alembic)."""
    Base.metadata.create_all(bind=engine)


def get_db_session() -> Session:
    """Factory para criar uma sesão de base de dados."""
    return SessionLocal()


class Analytics:
    """Helper class para queries analíticas complexas."""

    @staticmethod
    def heart_rate_trends(db: Session, device_id: int, days: int = 7) -> dict:
        """Tendência de FC nos últimos N dias."""
        # datetime.now(timezone.utc), não utcnow(): naive.timestamp() seria interpretado como hora local
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
        """Distribuição de atividades num dia específico. Usa intervalo
        [início do dia, início do dia seguinte) — activity_date é DateTime completo,
        comparar direto com date.date() nunca encontrava nada."""
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


# funções usadas por ble_bridge.py (get_history, export_csv, retenção, correções de atividade).
# dicts devolvidos usam as mesmas chaves que storage.py (ax/ay/az, etc.) para o dashboard não mudar.

DEFAULT_RETENTION_DAYS = 30
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 3650  # 10 anos
RETENTION_DAYS_SETTING_KEY = "retention_days"


def get_records_since(db: Session, device_id: int, hours: float) -> list[dict]:
    """Registos das últimas `hours` horas, mais antigos primeiro. Filtra por
    received_at (bridge), não timestamp_utc (dispositivo pode estar dessincronizado)."""
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
    """Nº total de registos de sensores guardados para o dispositivo."""
    return db.query(SensorRecord).filter(SensorRecord.device_id == device_id).count()


def export_records_csv(db: Session, device_id: int, hours: float) -> str:
    """Exporta os registos das últimas `hours` horas como CSV."""
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
    """Agrega os registos de sensores por dia civil (UTC). Agregação em SQL, não Python:
    uma janela de vários dias pode ter dezenas de milhares de registos (IMU ~14-52Hz)."""
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
    """Retenção atualmente configurada (dias), ou DEFAULT_RETENTION_DAYS se nunca alterada."""
    row = db.query(Setting).filter_by(key=RETENTION_DAYS_SETTING_KEY).first()
    if row is None:
        return DEFAULT_RETENTION_DAYS
    try:
        return float(row.value)
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS


def set_retention_days(db: Session, days) -> float:
    """Atualiza a retenção configurada. Lança ValueError se fora dos limites de sanidade."""
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
    """Grava uma correção manual do cuidador/equipa clínica à classificação de atividade da IA."""
    db.add(ActivityCorrection(
        device_id=device_id,
        original_category=original_category,
        corrected_category=corrected_category,
    ))
    db.commit()


class DataRetention:
    """Gestão automática de retenção de dados."""

    RETENTION_POLICIES = {
        "sensor_records": 365,  # 1 ano
        "activity_windows": 1825,  # 5 anos
        "alerts": 2555,  # 7 anos
        "emergency_alerts": 2920,  # 8 anos (decisão da utilizadora, 2026-07-31 — GDPR-006)
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

        # MedicationAdherence (apaga mesmo, não soft delete)
        cutoff = cutoff_date - timedelta(days=DataRetention.RETENTION_POLICIES["medication_adherence"])
        query = db.query(MedicationAdherence).filter(MedicationAdherence.scheduled_datetime < cutoff)
        count = query.count()
        if not dry_run:
            query.delete()
            db.commit()
        results["medication_adherence"] = count

        # EmergencyAlert (soft delete, GDPR-006, 8 anos); base em created_at, não timestamp_utc
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


# perfil de emergência (emergencyProfileChar, ver src/Ble/Ble.cpp) — servido por BLE após
# pairing/bonding; função pura (só lê db), quem grava no dispositivo é ble_bridge.py

EMERGENCY_PROFILE_MAX_LEN = 512  # tem de bater com EMERGENCY_PROFILE_MAX_LEN em include/Storage/Storage.h


def build_emergency_profile_payload(db: Session, patient_id: int) -> bytes:
    """Constrói o JSON (UTF-8, <= EMERGENCY_PROFILE_MAX_LEN bytes) servido em
    emergencyProfileChar. Chaves: name, ec (contacto emergência), cond/alrg
    (condições/alergias), med (medicação ativa). Chaves de listas vazias omitidas.

    Se exceder o limite, corta itens do fim de cond, depois med, depois alrg (por
    esta ordem: alergias são a info mais crítica em emergência, última a perder),
    marcando "trunc": true. Nome e contacto de emergência nunca são cortados.
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

    # medicação "atual": não soft-deletada e end_date NULL ou no futuro
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
        # sem espaços + ensure_ascii=False: minimiza bytes, mantém UTF-8 real (não \uXXXX)
        return json.dumps(p, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    data = _dump(payload)
    if len(data) > EMERGENCY_PROFILE_MAX_LEN:
        # trunc=True ANTES do loop de corte: tem de contar no orçamento, senão o loop
        # podia parar no limite e a flag empurrava de volta para cima dele
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


# timeline correlacionada por episódio: junta SensorRecord/ActivityWindow/EmergencyAlert
# numa timeline única centrada num alerta de emergência, sem inventar correlação estatística


def _activity_window_epoch_range(window: "ActivityWindow") -> tuple[int, int]:
    """Reconstrói (start_ts_approx, end_ts_approx) em epoch a partir de
    ActivityWindow.start_time/end_time (minutos desde meia-noite LOCAL do bridge,
    não epoch — ver insert_activity_window em orm_persistence.py) e activity_date.
    Aproximação via time.mktime() em hora local do servidor, não do dispositivo."""
    day_start_local = datetime.combine(window.activity_date.date(), datetime.min.time())
    start_local = day_start_local + timedelta(minutes=window.start_time or 0)
    end_local = day_start_local + timedelta(minutes=window.end_time or 0)
    start_ts = int(time.mktime(start_local.timetuple()))
    end_ts = int(time.mktime(end_local.timetuple()))
    return start_ts, end_ts


def build_episode_timeline(db: Session, device_id: int, center_ts: int, window_minutes: int = 30) -> dict:
    """Reúne, ordenados no tempo, os dados em torno de `center_ts` (epoch, tipicamente
    timestamp_utc de um EmergencyAlert): sinais vitais downsampled por minuto, blocos
    de atividade sobrepostos, alertas próximos. Janela: [center_ts - window_minutes*60,
    center_ts + window_minutes*60]. Nunca lança exceção por falta de dados (janela vazia é válida)."""
    window_start = int(center_ts - window_minutes * 60)
    window_end = int(center_ts + window_minutes * 60)

    # sensor_summary: downsampling por minuto em SQL (IMU ~14-52Hz, janela de 60min
    # podia ter dezenas de milhares de linhas). func.avg/count ignoram NULL nativamente.
    received_start = datetime.fromtimestamp(window_start, tz=timezone.utc).replace(tzinfo=None)
    received_end = datetime.fromtimestamp(window_end, tz=timezone.utc).replace(tzinfo=None)
    # agrupa por minuto do relógio do dispositivo (timestamp_utc); "//" não "/" mantém inteiros
    # (SQLAlchemy insere CAST NUMERIC em "/" sobre Integer, forçando float cross-dialect)
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

    # activity_blocks: pré-filtro largo (+-2 dias) por activity_date evita carregar todo
    # o histórico; sobreposição exata calculada depois em Python sobre epochs reconstruídos
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

    # nearby_emergency_alerts: exclusão do próprio alerta central fica a cargo de
    # get_episode_timeline_for_alert() abaixo, que é quem sabe o sequence_number
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
    """Timeline centrada num EmergencyAlert (device_id, sequence_number). Lança
    ValueError se o alerta não existir/estiver soft-deletado — aqui "não encontrado"
    é erro do chamador, não uma janela vazia legítima."""
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
    # exclui o alerta central de "nearby" (já vai em result["alert"]); comparação por
    # sequence_number pois nearby_emergency_alerts já são dicts, não instâncias ORM
    result["nearby_emergency_alerts"] = [
        a for a in result["nearby_emergency_alerts"] if a["sequence_number"] != sequence_number
    ]
    result["alert"] = {
        "alert_type": alert.alert_type,
        "timestamp_utc": alert.timestamp_utc,
        "sequence_number": alert.sequence_number,
    }
    return result


# versionamento e rollback do modelo ML: regista versões na BD, permite trocar a
# ativa em runtime — ver activity_inference.py (_load_model/reload_active_model)
# e ble_bridge.py (cmds "list_model_versions"/"activate_model_version")


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
    """Formato comum devolvido pelos helpers abaixo. `metrics` é desserializado de `metrics_json`."""
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
    """Regista uma nova versão do modelo `model_name`. Lança ValueError se já existir
    (model_name, version) — apanha o IntegrityError da UniqueConstraint e relança com
    mensagem clara. Se `activate=True`, ativa a versão via activate_model_version."""
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
    """Todas as versões registadas de `model_name`, mais recentes primeiro (por id)."""
    rows = (
        db.query(MlModelVersion)
        .filter(MlModelVersion.model_name == model_name)
        .order_by(desc(MlModelVersion.id))
        .all()
    )
    return [_ml_model_version_to_dict(r) for r in rows]


def activate_model_version(db: Session, model_name: str, version: str) -> dict:
    """Marca a versão pedida como ativa e todas as outras do mesmo `model_name` como
    inativas — nunca duas ativas em simultâneo. Lança ValueError se a versão não existir."""
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
    """A versão ativa registada para `model_name`, ou None se nenhuma ainda registada
    (arranque a frio — activity_inference.py::_load_model() usa o caminho fixo)."""
    row = (
        db.query(MlModelVersion)
        .filter(MlModelVersion.model_name == model_name, MlModelVersion.is_active.is_(True))
        .first()
    )
    return _ml_model_version_to_dict(row) if row is not None else None


if __name__ == "__main__":
    create_all_tables()

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

    print(Analytics.medication_adherence_summary(db, patient_id=1, days=30))
    print(DataRetention.cleanup(db, dry_run=True))

    db.close()
