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
    nif_encrypted = Column(String(255))  # Encriptado, aprovação obrigatória
    address_encrypted = Column(String(255))  # Encriptado
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

    __table_args__ = (
        Index("idx_patient_uuid", "uuid"),
    )


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

    __table_args__ = (
        Index("idx_adherence_medication_scheduled", "medication_id", "scheduled_datetime"),
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
