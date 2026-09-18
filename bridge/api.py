#!/usr/bin/env python3
"""api.py — API REST sobre storage_advanced.py: leitura analítica + escrita de aderência a medicação.

Correr localmente:
    pip install -r bridge/requirements_db.txt
    cd bridge && uvicorn api:app --host 127.0.0.1 --port 8766

`import api_auth` regista o modelo `ApiKey` na `Base` partilhada de storage_advanced,
para a tabela `api_keys` ser criada por create_all_tables()/create_all dos testes.
"""
from __future__ import annotations

import heapq
from datetime import datetime, timedelta, timezone
from itertools import islice
from typing import List, Literal, Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import api_auth
import auth_sessions
import fhir_export
import storage_advanced as sa

app = FastAPI(
    title="CareWear API",
    description="API REST somente-leitura para dados analíticos (protótipo, Prioridade 4).",
    version="0.2.0",
)

# Corre antes da autenticação (ASGI), por isso também trava força-bruta à chave de API.
app.add_middleware(api_auth.RateLimitMiddleware)

# O dashboard corre como ficheiro local (index.html aberto via file://, sem servidor
# HTTP à frente) — o browser envia Origin: null nesse caso, e "null" também cobre
# sandboxes/iframes. Localhost é para quando o dashboard passar a ser servido por
# http-server/vite/etc. em vez de aberto diretamente. Sem isto, o browser bloqueia
# o pré-voo OPTIONS de qualquer pedido (login incluído) com 405, antes mesmo de a
# app FastAPI ver o pedido real — não é um problema de autenticação nem de rede,
# é só CORS por faltar este middleware. Adicionado por último para ficar como a
# camada mais externa e responder ao OPTIONS antes do RateLimitMiddleware.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "null",
        "http://localhost",
        "http://127.0.0.1",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_db():
    db = sa.get_db_session()
    try:
        yield db
    finally:
        db.close()


def _require_user(
    x_api_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(_get_db),
) -> sa.User:
    """Autentica por chave de API (por-utilizador, revogável) ou sessão bearer; 401 caso contrário.

    Conta desativada (`User.deleted_at`, ver admin_revoke_user) tem de perder acesso de imediato,
    mesmo com uma chave de API própria ainda não revogada individualmente — sem isto, revogar a
    conta pelo admin não bastava para uma chave de API sobrevivente continuar a autenticar."""
    row = api_auth._resolve_api_key_row(db, x_api_key)
    if row is not None and row.user is not None and row.user.deleted_at is None:
        row.last_used_at = datetime.utcnow()  # persiste no commit do próprio endpoint
        return row.user

    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    user = auth_sessions.resolve_session(db, token)
    if user is not None and user.deleted_at is None:
        return user

    raise HTTPException(status_code=401, detail="Não autenticado")


# Dois perfis de admin: "admin" (sistema, sem acesso clínico) e "admin_clinical"
# (acesso clínico de leitura, com motivo + concessão temporal + auditoria própria).
ROLE_ADMIN_SYSTEM = "admin"
ROLE_ADMIN_CLINICAL = "admin_clinical"
ADMIN_ROLES = (ROLE_ADMIN_SYSTEM, ROLE_ADMIN_CLINICAL)

PRIVILEGED_ACCESS_ACTION = "privileged_clinical_access"
MIN_ACCESS_REASON_LENGTH = 8

# Query param partilhado; obrigatório só para admin_clinical (ver _authorize_patient).
ACCESS_REASON_QUERY = Query(
    default=None,
    description=("Motivo do acesso. Obrigatório para o perfil Admin Clínico/Suporte "
                 "Autorizado; registado em auditoria (privileged_clinical_access)."),
)


def _privileged_grant_is_active(user: sa.User, now: Optional[datetime] = None) -> bool:
    """True se a concessão temporal de acesso clínico ainda é válida."""
    expires_at = getattr(user, "privileged_access_expires_at", None)
    if expires_at is None:
        return False
    if expires_at.tzinfo is not None:
        expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
    return expires_at > (now or datetime.utcnow())


def _audit_privileged_access(
    db: Session,
    user: sa.User,
    request: Optional[Request],
    patient_id: int,
    reason: str,
    write: bool,
) -> None:
    """Regista o acesso clínico privilegiado com commit próprio, antes do endpoint devolver."""
    expires_at = getattr(user, "privileged_access_expires_at", None)
    db.add(sa.AuditLog(
        user_id=user.id,
        action=PRIVILEGED_ACCESS_ACTION,
        resource_type="patient",
        resource_id=patient_id,
        details={
            "reason": reason,
            "role": user.role,
            "mode": "write" if write else "read",
            "grant_expires_at": expires_at.isoformat() if expires_at is not None else None,
            "path": str(request.url.path) if request is not None else None,
        },
        ip_address=request.client.host if request is not None and request.client else None,
    ))
    db.commit()


def _authorize_patient(
    db: Session,
    user: sa.User,
    patient_id: int,
    write: bool = False,
    request: Optional[Request] = None,
    reason: Optional[str] = None,
) -> None:
    """Autoriza `user` a aceder ao paciente `patient_id`; 404 (não 403) para não revelar IDs existentes."""
    if user.role == ROLE_ADMIN_SYSTEM:
        raise HTTPException(status_code=404, detail="Não encontrado")

    if user.role == ROLE_ADMIN_CLINICAL:
        if write:
            raise HTTPException(status_code=404, detail="Não encontrado")
        motivo = (reason or "").strip()
        if len(motivo) < MIN_ACCESS_REASON_LENGTH:
            # 403 aqui: não depende do paciente pedido, não revela IDs.
            raise HTTPException(
                status_code=403,
                detail=("Acesso clínico privilegiado exige um motivo explícito "
                        f"(`reason`, mínimo {MIN_ACCESS_REASON_LENGTH} caracteres)."),
            )
        if not _privileged_grant_is_active(user):
            raise HTTPException(
                status_code=403,
                detail="Acesso clínico privilegiado sem concessão temporal ativa ou expirada.",
            )
        _audit_privileged_access(db, user, request, patient_id, motivo, write)
        return

    row = db.execute(
        sa.patient_caregivers.select().where(
            sa.patient_caregivers.c.patient_id == patient_id,
            sa.patient_caregivers.c.user_id == user.id,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Não encontrado")

    if write:
        allowed = bool(row.can_edit_medications) or user.role == "clinician"
        if not allowed:
            raise HTTPException(status_code=404, detail="Não encontrado")


def _audit_read(db: Session, user: sa.User, request: Request, action: str, resource_type: str, resource_id: int) -> None:
    """Regista cada leitura autorizada de PII de saúde (GDPR-003)."""
    db.add(sa.AuditLog(
        user_id=user.id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=request.client.host if request.client else None,
    ))
    db.commit()


@app.get("/health")
def health():
    return {"status": "ok"}


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/api/auth/login")
def login(body: LoginRequest, db: Session = Depends(_get_db)):
    token = auth_sessions.login(db, body.email, body.password)
    if token is None:
        raise HTTPException(status_code=401, detail="Credenciais inválidas")
    return {"token": token, "token_type": "bearer"}


@app.post("/api/auth/logout")
def logout(authorization: Optional[str] = Header(default=None), db: Session = Depends(_get_db)):
    token = authorization[7:] if authorization and authorization.lower().startswith("bearer ") else None
    auth_sessions.logout(db, token)
    return {"status": "ok"}


@app.get("/api/auth/me")
def me(user: sa.User = Depends(_require_user)):
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "name": user.name,
        "privileged_access_expires_at": (
            user.privileged_access_expires_at.isoformat()
            if getattr(user, "privileged_access_expires_at", None) is not None else None
        ),
    }


# CRUD de utilizadores/perfis (RF: "o sistema deve permitir a administração de utilizadores
# e respetivos perfis") — reservado ao Admin de Sistema. admin_clinical NUNCA gere contas
# (seria a mesma conta a escrever nas suas próprias permissões); ver ROLE_ADMIN_CLINICAL acima.
def _require_admin(user: sa.User = Depends(_require_user)) -> sa.User:
    if user.role != ROLE_ADMIN_SYSTEM:
        raise HTTPException(status_code=403, detail="Reservado ao Admin de Sistema")
    return user


def _user_to_dict(u: sa.User) -> dict:
    return {
        "id": u.id,
        "uuid": u.uuid,
        "email": u.email,
        "role": u.role,
        "name": u.name,
        "phone": u.phone,
        "institution": u.institution,
        "professional_id": u.professional_id,
        "active": u.deleted_at is None,
        "privileged_access_expires_at": (
            u.privileged_access_expires_at.isoformat() if u.privileged_access_expires_at else None
        ),
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


@app.get("/api/admin/users")
def admin_list_users(
    request: Request,
    db: Session = Depends(_get_db),
    admin: sa.User = Depends(_require_admin),
):
    users = db.query(sa.User).order_by(sa.User.id).all()
    db.add(sa.AuditLog(
        user_id=admin.id, action="admin.users.list", resource_type="user", resource_id=0,
        ip_address=request.client.host if request.client else None,
    ))
    db.commit()
    return {"users": [_user_to_dict(u) for u in users]}


class AdminUserCreate(BaseModel):
    email: str
    password: str
    role: Literal["family", "clinician", "admin", "admin_clinical"]
    name: str
    phone: Optional[str] = None
    institution: Optional[str] = None
    professional_id: Optional[str] = None

    @field_validator("password")
    @classmethod
    def _password_min_length(cls, v):
        if len(v) < 8:
            raise ValueError("password deve ter pelo menos 8 caracteres")
        return v


@app.post("/api/admin/users", status_code=201)
def admin_create_user(
    body: AdminUserCreate,
    request: Request,
    db: Session = Depends(_get_db),
    admin: sa.User = Depends(_require_admin),
):
    existing = db.query(sa.User).filter(sa.User.email == body.email).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Já existe um utilizador com este email")
    user = sa.User(
        uuid=str(uuid4()),
        email=body.email,
        password_hash=auth_sessions.hash_password(body.password),
        role=body.role,
        name=body.name,
        phone=body.phone,
        institution=body.institution,
        professional_id=body.professional_id,
    )
    db.add(user)
    db.flush()
    db.add(sa.AuditLog(
        user_id=admin.id, action="admin.users.create", resource_type="user", resource_id=user.id,
        details={"role": body.role, "email": body.email},
        ip_address=request.client.host if request.client else None,
    ))
    db.commit()
    db.refresh(user)
    return _user_to_dict(user)


class AdminUserUpdate(BaseModel):
    role: Optional[Literal["family", "clinician", "admin", "admin_clinical"]] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    institution: Optional[str] = None
    professional_id: Optional[str] = None
    # concessão temporal de acesso clínico privilegiado (só relevante para role=admin_clinical,
    # ver _privileged_grant_is_active acima); horas a somar a partir de agora.
    privileged_access_hours: Optional[float] = Field(default=None, ge=0, le=24 * 30)


@app.patch("/api/admin/users/{user_id}")
def admin_update_user(
    user_id: int,
    body: AdminUserUpdate,
    request: Request,
    db: Session = Depends(_get_db),
    admin: sa.User = Depends(_require_admin),
):
    user = db.get(sa.User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Utilizador não encontrado")
    if user.id == admin.id and body.role is not None and body.role != admin.role:
        raise HTTPException(status_code=400, detail="Não podes alterar o teu próprio perfil")

    changes = {}
    for field in ("role", "name", "phone", "institution", "professional_id"):
        value = getattr(body, field)
        if value is not None:
            setattr(user, field, value)
            changes[field] = value
    if body.privileged_access_hours is not None:
        user.privileged_access_expires_at = datetime.utcnow() + timedelta(hours=body.privileged_access_hours)
        changes["privileged_access_expires_at"] = user.privileged_access_expires_at.isoformat()

    db.add(sa.AuditLog(
        user_id=admin.id, action="admin.users.update", resource_type="user", resource_id=user.id,
        details=changes, ip_address=request.client.host if request.client else None,
    ))
    db.commit()
    db.refresh(user)
    return _user_to_dict(user)


@app.post("/api/admin/users/{user_id}/revoke")
def admin_revoke_user(
    user_id: int,
    request: Request,
    db: Session = Depends(_get_db),
    admin: sa.User = Depends(_require_admin),
):
    """Desativa a conta (soft delete — nunca apaga o histórico de auditoria associado) e revoga
    toda a sessão ativa, para o efeito ser imediato mesmo com um token de sessão já emitido."""
    user = db.get(sa.User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Utilizador não encontrado")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Não podes revogar a tua própria conta")

    user.deleted_at = datetime.utcnow()
    db.query(auth_sessions.UserSession).filter(
        auth_sessions.UserSession.user_id == user.id,
        auth_sessions.UserSession.revoked_at.is_(None),
    ).update({"revoked_at": datetime.utcnow()})
    db.add(sa.AuditLog(
        user_id=admin.id, action="admin.users.revoke", resource_type="user", resource_id=user.id,
        ip_address=request.client.host if request.client else None,
    ))
    db.commit()
    return {"ok": True}


@app.get("/api/patients/directory")
def patients_directory(
    request: Request,
    db: Session = Depends(_get_db),
    user: sa.User = Depends(_require_user),
):
    """Ponte de identidade dashboard (uuid) <-> PK desta base de dados, para pacientes do utilizador."""
    query = db.query(sa.Patient)
    if user.role not in ADMIN_ROLES:
        associated = db.execute(
            sa.patient_caregivers.select().where(sa.patient_caregivers.c.user_id == user.id)
        ).fetchall()
        ids = [row.patient_id for row in associated]
        if not ids:
            return {"patients": []}
        query = query.filter(sa.Patient.id.in_(ids))

    patients = query.order_by(sa.Patient.id).all()
    _audit_read(db, user, request, "patients_directory.read", "patient", 0)
    return {
        "patients": [
            {"id": p.id, "uuid": p.uuid, "pseudonym": p.pseudonym}
            for p in patients
        ]
    }


@app.get("/api/patients/{patient_id}/devices")
def patient_devices(
    patient_id: int,
    request: Request,
    db: Session = Depends(_get_db),
    reason: Optional[str] = ACCESS_REASON_QUERY,
    user: sa.User = Depends(_require_user),
):
    """Ponte device_id <-> paciente: os endpoints /api/devices/{id}/* (heart-rate-trends,
    anomalies, activity-distribution, fhir/observations) não tinham nenhuma forma de o
    dashboard descobrir o device_id de um paciente — ficavam inalcançáveis do frontend,
    só testados diretamente por id numérico. Endpoint próprio (não junto a
    /api/patients/directory) para manter esse minimal e já auditado."""
    patient = db.get(sa.Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _authorize_patient(db, user, patient_id, request=request, reason=reason)
    _audit_read(db, user, request, "patient_devices.read", "patient", patient_id)
    return {"devices": [{"id": d.id, "uuid": d.uuid} for d in patient.devices]}


@app.get("/api/devices/{device_id}/heart-rate-trends")
def heart_rate_trends(
    device_id: int,
    request: Request,
    days: int = Query(default=7, ge=1, le=3650),
    db: Session = Depends(_get_db),
    reason: Optional[str] = ACCESS_REASON_QUERY,
    user: sa.User = Depends(_require_user),
):
    device = db.get(sa.Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    _authorize_patient(db, user, device.patient_id, request=request, reason=reason)
    _audit_read(db, user, request, "heart_rate.read", "device", device_id)
    return sa.Analytics.heart_rate_trends(db, device_id, days=days)


@app.get("/api/devices/{device_id}/anomalies")
def device_anomalies(
    device_id: int,
    request: Request,
    days: int = Query(default=30, ge=1, le=3650),
    db: Session = Depends(_get_db),
    reason: Optional[str] = ACCESS_REASON_QUERY,
    user: sa.User = Depends(_require_user),
):
    """Episódios de anomalia já fechados (ver anomaly_inference.py/duration_detector), mais recentes primeiro."""
    device = db.get(sa.Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    _authorize_patient(db, user, device.patient_id, request=request, reason=reason)
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(sa.AnomalyDetection)
        .filter(sa.AnomalyDetection.device_id == device_id, sa.AnomalyDetection.window_start >= cutoff)
        .order_by(sa.AnomalyDetection.window_start.desc())
        .all()
    )
    _audit_read(db, user, request, "anomalies.read", "device", device_id)
    return {
        "anomalies": [
            {
                "id": r.id,
                "detector": r.detector,
                "anomaly_category": r.anomaly_category,
                "score": r.score,
                "threshold_used": r.threshold_used,
                "window_start": r.window_start.isoformat(),
                "window_end": r.window_end.isoformat(),
                "description": r.description,
                "severity": r.severity,
                "model_version": r.model_version,
                "investigated": r.investigated,
                "investigation_notes": r.investigation_notes,
            }
            for r in rows
        ]
    }


@app.get("/api/patients/{patient_id}/medication-adherence")
def medication_adherence(
    patient_id: int,
    request: Request,
    days: int = Query(default=30, ge=1, le=3650),
    db: Session = Depends(_get_db),
    reason: Optional[str] = ACCESS_REASON_QUERY,
    user: sa.User = Depends(_require_user),
):
    patient = db.get(sa.Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _authorize_patient(db, user, patient_id, request=request, reason=reason)
    _audit_read(db, user, request, "medication_adherence.read", "patient", patient_id)
    return sa.Analytics.medication_adherence_summary(db, patient_id, days=days)


@app.get("/api/devices/{device_id}/activity-distribution")
def activity_distribution(
    device_id: int,
    request: Request,
    date: str = Query(..., description="Data no formato AAAA-MM-DD"),
    db: Session = Depends(_get_db),
    reason: Optional[str] = ACCESS_REASON_QUERY,
    user: sa.User = Depends(_require_user),
):
    device = db.get(sa.Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    _authorize_patient(db, user, device.patient_id, request=request, reason=reason)
    try:
        parsed_date = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de data inválido, use AAAA-MM-DD")
    _audit_read(db, user, request, "activity.read", "device", device_id)
    return sa.Analytics.daily_activity_distribution(db, device_id, parsed_date)


class MedicationAdherenceIn(BaseModel):
    scheduled_datetime: datetime
    taken: bool
    method: Literal["manual_entry", "wearable_detection", "ai_inference"] = "manual_entry"
    notes: Optional[str] = None

    @field_validator("scheduled_datetime")
    @classmethod
    def _normalize_to_naive_utc(cls, value: datetime) -> datetime:
        """Normaliza para UTC naive — a coluna é DateTime sem fuso e o resto do ficheiro usa utcnow() naive."""
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value


@app.post("/api/medications/{medication_id}/adherence")
def record_medication_adherence(
    medication_id: int,
    body: MedicationAdherenceIn,
    request: Request,
    db: Session = Depends(_get_db),
    reason: Optional[str] = ACCESS_REASON_QUERY,
    user: sa.User = Depends(_require_user),
):
    """Regista/atualiza se uma dose agendada foi tomada. Idempotente por (medication_id, scheduled_datetime)."""
    medication = db.get(sa.Medication, medication_id)
    if medication is None:
        raise HTTPException(status_code=404, detail="Medicamento não encontrado")
    _authorize_patient(db, user, medication.patient_id, write=True, request=request, reason=reason)

    now = datetime.utcnow()
    max_attempts = 2
    for attempt in range(max_attempts):
        record = (
            db.query(sa.MedicationAdherence)
            .filter(
                sa.MedicationAdherence.medication_id == medication_id,
                sa.MedicationAdherence.scheduled_datetime == body.scheduled_datetime,
            )
            .first()
        )
        if record is None:
            record = sa.MedicationAdherence(medication_id=medication_id, scheduled_datetime=body.scheduled_datetime)
            db.add(record)
        record.taken = body.taken
        record.taken_at = now if body.taken else None
        record.method = body.method
        record.notes = body.notes

        db.add(sa.AuditLog(
            user_id=user.id,
            action="medication_adherence.write",
            resource_type="medication_adherence",
            resource_id=medication_id,
            details={
                "taken": body.taken,
                "method": body.method,
                "scheduled_datetime": body.scheduled_datetime.isoformat(),
            },
            ip_address=request.client.host if request.client else None,
        ))
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()
            if attempt == max_attempts - 1:
                raise
            continue  # pedido concorrente venceu a corrida; repete como UPDATE
    db.refresh(record)

    return {
        "id": record.id,
        "medication_id": record.medication_id,
        "scheduled_datetime": record.scheduled_datetime.isoformat(),
        "taken": record.taken,
        "taken_at": record.taken_at.isoformat() if record.taken_at else None,
        "method": record.method,
        "notes": record.notes,
    }


class ConditionIn(BaseModel):
    display_text: str
    code_system: Optional[str] = None
    code: Optional[str] = None


def _serialize_condition_or_allergy(row) -> dict:
    return {
        "id": row.id,
        "display_text": row.display_text,
        "code_system": row.code_system,
        "code": row.code,
    }


@app.get("/api/patients/{patient_id}/conditions")
def list_conditions(
    patient_id: int,
    request: Request,
    db: Session = Depends(_get_db),
    reason: Optional[str] = ACCESS_REASON_QUERY,
    user: sa.User = Depends(_require_user),
):
    patient = db.get(sa.Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _authorize_patient(db, user, patient_id, request=request, reason=reason)
    rows = (
        db.query(sa.PatientCondition)
        .filter(sa.PatientCondition.patient_id == patient_id, sa.PatientCondition.deleted_at.is_(None))
        .order_by(sa.PatientCondition.id)
        .all()
    )
    _audit_read(db, user, request, "conditions.read", "patient", patient_id)
    return {"conditions": [_serialize_condition_or_allergy(r) for r in rows]}


@app.post("/api/patients/{patient_id}/conditions")
def add_condition(
    patient_id: int,
    body: ConditionIn,
    request: Request,
    db: Session = Depends(_get_db),
    reason: Optional[str] = ACCESS_REASON_QUERY,
    user: sa.User = Depends(_require_user),
):
    patient = db.get(sa.Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _authorize_patient(db, user, patient_id, write=True, request=request, reason=reason)
    row = sa.PatientCondition(uuid=str(uuid4()), patient_id=patient_id, code_system=body.code_system, code=body.code)
    row.display_text = body.display_text
    db.add(row)
    db.add(sa.AuditLog(
        user_id=user.id, action="conditions.write", resource_type="patient", resource_id=patient_id,
        ip_address=request.client.host if request.client else None,
    ))
    db.commit()
    db.refresh(row)
    return _serialize_condition_or_allergy(row)


@app.delete("/api/patients/{patient_id}/conditions/{condition_id}")
def delete_condition(
    patient_id: int,
    condition_id: int,
    request: Request,
    db: Session = Depends(_get_db),
    reason: Optional[str] = ACCESS_REASON_QUERY,
    user: sa.User = Depends(_require_user),
):
    row = db.get(sa.PatientCondition, condition_id)
    if row is None or row.patient_id != patient_id or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Não encontrado")
    _authorize_patient(db, user, patient_id, write=True, request=request, reason=reason)
    row.deleted_at = datetime.utcnow()
    db.add(sa.AuditLog(
        user_id=user.id, action="conditions.delete", resource_type="patient", resource_id=patient_id,
        ip_address=request.client.host if request.client else None,
    ))
    db.commit()
    return {"status": "ok"}


class AllergyIn(BaseModel):
    display_text: str
    code_system: Optional[str] = None
    code: Optional[str] = None


@app.get("/api/patients/{patient_id}/allergies")
def list_allergies(
    patient_id: int,
    request: Request,
    db: Session = Depends(_get_db),
    reason: Optional[str] = ACCESS_REASON_QUERY,
    user: sa.User = Depends(_require_user),
):
    patient = db.get(sa.Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _authorize_patient(db, user, patient_id, request=request, reason=reason)
    rows = (
        db.query(sa.PatientAllergy)
        .filter(sa.PatientAllergy.patient_id == patient_id, sa.PatientAllergy.deleted_at.is_(None))
        .order_by(sa.PatientAllergy.id)
        .all()
    )
    _audit_read(db, user, request, "allergies.read", "patient", patient_id)
    return {"allergies": [_serialize_condition_or_allergy(r) for r in rows]}


@app.post("/api/patients/{patient_id}/allergies")
def add_allergy(
    patient_id: int,
    body: AllergyIn,
    request: Request,
    db: Session = Depends(_get_db),
    reason: Optional[str] = ACCESS_REASON_QUERY,
    user: sa.User = Depends(_require_user),
):
    patient = db.get(sa.Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _authorize_patient(db, user, patient_id, write=True, request=request, reason=reason)
    row = sa.PatientAllergy(uuid=str(uuid4()), patient_id=patient_id, code_system=body.code_system, code=body.code)
    row.display_text = body.display_text
    db.add(row)
    db.add(sa.AuditLog(
        user_id=user.id, action="allergies.write", resource_type="patient", resource_id=patient_id,
        ip_address=request.client.host if request.client else None,
    ))
    db.commit()
    db.refresh(row)
    return _serialize_condition_or_allergy(row)


@app.delete("/api/patients/{patient_id}/allergies/{allergy_id}")
def delete_allergy(
    patient_id: int,
    allergy_id: int,
    request: Request,
    db: Session = Depends(_get_db),
    reason: Optional[str] = ACCESS_REASON_QUERY,
    user: sa.User = Depends(_require_user),
):
    row = db.get(sa.PatientAllergy, allergy_id)
    if row is None or row.patient_id != patient_id or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Não encontrado")
    _authorize_patient(db, user, patient_id, write=True, request=request, reason=reason)
    row.deleted_at = datetime.utcnow()
    db.add(sa.AuditLog(
        user_id=user.id, action="allergies.delete", resource_type="patient", resource_id=patient_id,
        ip_address=request.client.host if request.client else None,
    ))
    db.commit()
    return {"status": "ok"}


# RF-11 — exportação FHIR R4. Mapeamento sinal->código vive em fhir_export.py.
MAX_EXPORT_HOURS = 87600  # 10 anos

# Paginação real por Bundle.link (self/next); MAX_FHIR_RESOURCES é o teto de UMA página, não truncagem.
MAX_FHIR_RESOURCES = fhir_export.MAX_PAGE_SIZE
DEFAULT_FHIR_PAGE_SIZE = fhir_export.DEFAULT_PAGE_SIZE
_FHIR_DB_CHUNK = 500  # linhas trazidas do SQLite por vez ao iterar os streams


def _fhir_sensor_stream(db: Session, device_id: int, cutoff: datetime, subject: dict):
    """Observations de SensorRecord, em ordem observation_sort_key crescente (stream)."""
    query = (
        db.query(sa.SensorRecord)
        .filter(sa.SensorRecord.device_id == device_id, sa.SensorRecord.received_at >= cutoff)
        .order_by(sa.SensorRecord.timestamp_utc.asc(), sa.SensorRecord.id.asc())
        .yield_per(_FHIR_DB_CHUNK)
    )
    for record in query:
        for observation in sorted(
            fhir_export.observations_from_sensor_record(record, subject, device_id),
            key=fhir_export.observation_sort_key,
        ):
            yield observation


def _fhir_activity_stream(db: Session, device_id: int, cutoff: datetime, subject: dict):
    """Observations de ActivityWindow, ordenadas por (activity_date, start_time, id)."""
    query = (
        db.query(sa.ActivityWindow)
        .filter(
            sa.ActivityWindow.device_id == device_id,
            sa.ActivityWindow.activity_date >= cutoff,
        )
        .order_by(
            sa.ActivityWindow.activity_date.asc(),
            sa.ActivityWindow.start_time.asc(),
            sa.ActivityWindow.id.asc(),
        )
        .yield_per(_FHIR_DB_CHUNK)
    )
    for window in query:
        observation = fhir_export.observation_from_activity_window(window, subject, device_id)
        if observation is not None:
            yield observation


def _fhir_total(db: Session, device_id: int, cutoff: datetime, include_activity: bool) -> int:
    """Total de recursos da pesquisa inteira (todas as páginas), via count() em SQL."""
    row = (
        db.query(
            func.count(sa.SensorRecord.heart_rate),
            func.count(sa.SensorRecord.spo2_percent),
            func.count(sa.SensorRecord.steps_count),
            func.count(sa.SensorRecord.pacing_index),
        )
        .filter(sa.SensorRecord.device_id == device_id, sa.SensorRecord.received_at >= cutoff)
        .one()
    )
    total = sum(int(c or 0) for c in row)
    if include_activity:
        total += int(
            db.query(func.count(sa.ActivityWindow.duration_minutes))
            .filter(
                sa.ActivityWindow.device_id == device_id,
                sa.ActivityWindow.activity_date >= cutoff,
                sa.ActivityWindow.activity_date.isnot(None),
            )
            .scalar()
            or 0
        )
    return total


@app.get("/api/devices/{device_id}/fhir/observations")
def fhir_observations(
    device_id: int,
    request: Request,
    hours: float = Query(default=24, gt=0, le=MAX_EXPORT_HOURS),
    include_activity: bool = Query(default=True),
    count: int = Query(
        default=DEFAULT_FHIR_PAGE_SIZE,
        ge=1,
        le=MAX_FHIR_RESOURCES,
        alias="_count",
        description="Recursos por página (nome FHIR R4). Teto absoluto de protecção: 5000.",
    ),
    page: int = Query(
        default=1,
        ge=1,
        alias="_page",
        description="Página, começando em 1. Prefira seguir Bundle.link relation='next'.",
    ),
    include: List[str] = Query(
        default=[],
        alias="_include",
        description="FHIR _include padrão: 'Observation:subject' e/ou 'Observation:device' juntam"
        " Patient/Device como entries search.mode=include, fora da contagem de 'total'.",
    ),
    db: Session = Depends(_get_db),
    reason: Optional[str] = ACCESS_REASON_QUERY,
    user: sa.User = Depends(_require_user),
):
    """Observações do dispositivo como Bundle FHIR R4 (searchset), paginado por Bundle.link."""
    device = db.get(sa.Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    _authorize_patient(db, user, device.patient_id, request=request, reason=reason)
    _audit_read(db, user, request, "fhir_observations.read", "device", device_id)

    patient = db.get(sa.Patient, device.patient_id)
    subject = fhir_export.build_subject(
        device.patient_id,
        pseudonym=getattr(patient, "pseudonym", None) if patient else None,
    )

    included_resources = []
    if "Observation:subject" in include and patient is not None:
        included_resources.append(fhir_export.build_patient_resource(patient))
    if "Observation:device" in include:
        included_resources.append(fhir_export.build_device_resource(device))

    cutoff = datetime.utcnow() - timedelta(hours=hours)

    # heapq.merge funde os dois streams já ordenados sem materializar nada.
    streams = [_fhir_sensor_stream(db, device_id, cutoff, subject)]
    if include_activity:
        streams.append(_fhir_activity_stream(db, device_id, cutoff, subject))
    merged = heapq.merge(*streams, key=fhir_export.observation_sort_key)

    offset = (page - 1) * count
    window = list(islice(merged, offset, offset + count + 1))  # +1 para detetar next sem 2ª query
    has_next = len(window) > count
    observations = window[:count]

    bundle = fhir_export.build_observation_bundle(
        observations,
        base_url=str(request.base_url).rstrip("/") + "/fhir",
        total=_fhir_total(db, device_id, cutoff, include_activity),
        self_url=str(request.url.include_query_params(_count=count, _page=page)),
        next_url=(
            str(request.url.include_query_params(_count=count, _page=page + 1))
            if has_next
            else None
        ),
        included=included_resources,
    )
    return bundle


@app.get("/api/fhir/observation-mappings")
def fhir_observation_mappings(user: sa.User = Depends(_require_user)):
    """Metadados do mapeamento FHIR (sem dados de paciente): quais sinais têm código LOINC confirmado."""
    return {
        "confirmed": [
            {
                "signal": m.key,
                "text": m.text,
                "system": fhir_export.LOINC_SYSTEM,
                "codes": [{"code": c, "display": d} for c, d in m.codings],
                "unit_ucum": m.unit_ucum,
            }
            for m in fhir_export.SIGNAL_MAPPINGS.values()
            if m.confirmed
        ],
        "unconfirmed": fhir_export.unconfirmed_signals(),
    }


# RF-12 — relatório semanal (dados apenas; PDF é feito pelo dashboard). Pensado para ser
# chamado por um GET periódico do agendador Cron do projeto.

WEEKLY_REPORT_DAYS = 7
WEEKLY_REPORT_MAX_ALERTS = 50  # lista truncada; contagem por severidade vai sempre completa


def _weekly_period(end: Optional[str]) -> tuple[datetime, datetime]:
    """[início, fim) da semana do relatório, em UTC naive. `end` (AAAA-MM-DD) fixa o último dia."""
    if end is None:
        period_end = datetime.utcnow()
    else:
        try:
            parsed = datetime.strptime(end, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de data inválido, use AAAA-MM-DD")
        period_end = parsed + timedelta(days=1)  # fim exclusivo, dia indicado entra inteiro
    return period_end - timedelta(days=WEEKLY_REPORT_DAYS), period_end


@app.get("/api/patients/{patient_id}/weekly-report")
def weekly_report(
    patient_id: int,
    request: Request,
    end: Optional[str] = Query(default=None, description="Último dia da semana (AAAA-MM-DD); por omissão, agora"),
    db: Session = Depends(_get_db),
    reason: Optional[str] = ACCESS_REASON_QUERY,
    user: sa.User = Depends(_require_user),
):
    """Relatório semanal do paciente: rotina, sinais vitais, alertas e adesão à medicação."""
    patient = db.get(sa.Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _authorize_patient(db, user, patient_id, request=request, reason=reason)
    _audit_read(db, user, request, "weekly_report.read", "patient", patient_id)

    period_start, period_end = _weekly_period(end)

    device_ids = [d.id for d in db.query(sa.Device).filter(sa.Device.patient_id == patient_id).all()]

    # Rotina
    rotina: dict[str, dict] = {}
    if device_ids:
        rows = (
            db.query(
                sa.ActivityWindow.activity_category,
                func.sum(sa.ActivityWindow.duration_minutes),
                func.count(sa.ActivityWindow.id),
            )
            .filter(
                sa.ActivityWindow.device_id.in_(device_ids),
                sa.ActivityWindow.activity_date >= period_start,
                sa.ActivityWindow.activity_date < period_end,
            )
            .group_by(sa.ActivityWindow.activity_category)
            .all()
        )
        rotina = {
            category: {
                "total_minutes": int(total or 0),
                "windows_count": int(count or 0),
                "daily_average_minutes": round((total or 0) / WEEKLY_REPORT_DAYS, 1),
            }
            for category, total, count in rows
        }
    # categorias sem janelas aparecem a zero, não desaparecem
    for category in ("sleep", "rest", "activity", "eating", "hygiene"):
        rotina.setdefault(category, {"total_minutes": 0, "windows_count": 0, "daily_average_minutes": 0.0})

    # Sinais vitais
    sinais_vitais: dict[str, Optional[dict]] = {"heart_rate": None, "spo2_percent": None, "steps": None}
    if device_ids:
        # timestamp_utc é epoch; marcar period_start/end como UTC antes de .timestamp() evita desvio pelo fuso local
        start_epoch = int(period_start.replace(tzinfo=timezone.utc).timestamp())
        end_epoch = int(period_end.replace(tzinfo=timezone.utc).timestamp())
        for key, column in (
            ("heart_rate", sa.SensorRecord.heart_rate),
            ("spo2_percent", sa.SensorRecord.spo2_percent),
            ("steps", sa.SensorRecord.steps_count),
        ):
            avg_value, min_value, max_value, count = (
                db.query(func.avg(column), func.min(column), func.max(column), func.count(column))
                .filter(
                    sa.SensorRecord.device_id.in_(device_ids),
                    sa.SensorRecord.timestamp_utc >= start_epoch,
                    sa.SensorRecord.timestamp_utc < end_epoch,
                    column.isnot(None),
                )
                .one()
            )
            if count:
                sinais_vitais[key] = {
                    "count": int(count),
                    "avg": round(float(avg_value), 1),
                    "min": min_value,
                    "max": max_value,
                }

    # Alertas
    severities = {"info": 0, "warning": 0, "serious": 0, "critical": 0}
    alert_list: list[dict] = []
    if device_ids:
        alert_rows = (
            db.query(sa.Alert)
            .filter(
                sa.Alert.device_id.in_(device_ids),
                sa.Alert.created_at >= period_start,
                sa.Alert.created_at < period_end,
                sa.Alert.deleted_at.is_(None),  # soft delete: não conta alertas já apagados
            )
            .order_by(sa.Alert.created_at.desc())
            .all()
        )
        for row in alert_rows:
            severities[row.severity] = severities.get(row.severity, 0) + 1
        for row in alert_rows[:WEEKLY_REPORT_MAX_ALERTS]:
            alert_list.append({
                "id": row.id,
                "type": row.alert_type,
                "severity": row.severity,
                "title": row.title,
                "description": row.description,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "resolved": row.resolved_at is not None,
            })

    # Adesão à medicação
    adesao = sa.Analytics.medication_adherence_summary(db, patient_id, days=WEEKLY_REPORT_DAYS)

    return {
        "patient_id": patient_id,
        "patient_pseudonym": getattr(patient, "pseudonym", None),
        "period": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
            "days": WEEKLY_REPORT_DAYS,
        },
        "generated_at": datetime.utcnow().isoformat(),
        "device_ids": device_ids,
        "rotina": rotina,
        "sinais_vitais": sinais_vitais,
        "alertas": {
            "total": sum(severities.values()),
            "por_severidade": severities,
            "recentes": alert_list,
            "truncado": sum(severities.values()) > WEEKLY_REPORT_MAX_ALERTS,
        },
        "adesao_medicacao": adesao,
    }
