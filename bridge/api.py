#!/usr/bin/env python3
"""
api.py — API REST sobre storage_advanced.py: leitura (queries analíticas) +
um primeiro endpoint de escrita (aderência a medicação).

Primeiro passo do "próximo item concreto da Prioridade 4" registado em
PROJECT_STATUS.md (secção "Cifra real dos campos sensíveis (NIF, morada) +
Alembic", 2026-07-07): ligar as queries analíticas (`Analytics.*`) a um
serviço HTTP, para que o dashboard possa um dia consumir histórico real via
rede em vez de depender só do bridge WebSocket local (`ble_bridge.py`,
`ws://localhost:8765`).

Âmbito da primeira versão (2026-07-07): só leitura (GET). Correr localmente:

    pip install -r bridge/requirements_db.txt
    cd bridge && uvicorn api:app --host 127.0.0.1 --port 8766

**2026-07-08**: adicionado o primeiro endpoint de escrita — POST de
aderência a medicação (ver `record_medication_adherence` abaixo).

**2026-07-17 (API-002 + API-003)**: a autenticação passou de uma chave
estática partilhada (`CAREWEAR_API_KEY`) para chaves por-utilizador
revogáveis (`api_auth.ApiKey`), com autorização por paciente em cada
endpoint e rate limiting por janela deslizante. A chave estática foi
REMOVIDA — era exatamente o vetor do API-002 (uma só chave, sem rotação,
partilhada por todos). O provisionamento passou para o CLI de `api_auth.py`.
Ver SECURITY_STATUS.md (API-002, API-003) e os docstrings abaixo.

Nota importante: `import api_auth` (abaixo) regista o modelo `ApiKey` na
`Base` partilhada de `storage_advanced` — é isso que faz a tabela `api_keys`
ser criada por `create_all_tables()` e pelo `create_all` dos testes sem
tocar em `storage_advanced.py`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import api_auth
import storage_advanced as sa

app = FastAPI(
    title="CareWear API",
    description="API REST somente-leitura para dados analíticos (protótipo, Prioridade 4).",
    version="0.2.0",
)

# Rate limiting (API-003) — middleware ASGI próprio, sem dependência nova.
# Corre ANTES da autenticação por natureza ASGI, portanto também trava
# força-bruta à chave de API (a preocupação explícita do API-003).
app.add_middleware(api_auth.RateLimitMiddleware)


def _get_db():
    db = sa.get_db_session()
    try:
        yield db
    finally:
        db.close()


def _require_user(
    x_api_key: Optional[str] = Header(default=None),
    db: Session = Depends(_get_db),
) -> sa.User:
    """Autenticação por chave por-utilizador (API-002).

    Substitui a antiga chave estática partilhada `CAREWEAR_API_KEY` (removida):
    cada cuidador/clínico tem a sua própria chave (`api_auth.ApiKey`),
    revogável por linha. 401 se ausente, desconhecida ou revogada.

    **Fail-closed**: sem nenhuma `ApiKey` na base de dados, todos os pedidos
    autenticados são 401 — não há bootstrap partilhado nem chave por omissão.
    O provisionamento faz-se pelo CLI de `api_auth.py` (`create`/`revoke`).

    Atualiza `last_used_at` da chave em cada uso (auditoria de utilização) —
    a alteração é apenas marcada na sessão e persiste com o primeiro commit
    do próprio endpoint (o `AuditLog` de leitura ou a escrita de aderência).
    Não commitamos aqui de propósito: um commit extra nesta dependência
    mudaria a ordenação de commits que o endpoint de escrita usa para
    recuperar de corridas por dose (ver `record_medication_adherence`).
    """
    row = api_auth._resolve_api_key_row(db, x_api_key)
    if row is None:
        raise HTTPException(status_code=401, detail="Chave de API inválida ou ausente")
    row.last_used_at = datetime.utcnow()
    return row.user


def _authorize_patient(db: Session, user: sa.User, patient_id: int, write: bool = False) -> None:
    """Autoriza `user` a aceder ao paciente `patient_id`; caso contrário 404.

    Modelo de acesso real (ver storage_advanced.py): `patient_caregivers` é a
    ÚNICA associação utilizador↔paciente e serve para família E clínicos — o
    `User.role` é que os distingue. Não existe nenhuma associação
    clínico-paciente separada no ORM, e `Medication.prescribed_by_user_id`
    NÃO é um grant de acesso. Decisão: clínicos têm de estar associados via
    `patient_caregivers` como qualquer cuidador.

      * admin  -> acesso a tudo.
      * outros -> tem de existir a linha (patient_id, user.id) em
        `patient_caregivers`.
      * escrita (`write=True`) exige adicionalmente `can_edit_medications=True`
        na linha da associação OU `role in ("clinician", "admin")`.

    **Devolve 404 (não 403) quando não autorizado** — deliberadamente igual ao
    "não encontrado". Com IDs sequenciais, um 403 distinto de um 404 revelaria
    a um atacante QUAIS os IDs que existem (enumeração); respondendo sempre 404
    não se distingue "não existe" de "existe mas não é teu".
    """
    if user.role == "admin":
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
        allowed = bool(row.can_edit_medications) or user.role in ("clinician", "admin")
        if not allowed:
            raise HTTPException(status_code=404, detail="Não encontrado")


def _audit_read(db: Session, user: sa.User, request: Request, action: str, resource_type: str, resource_id: int) -> None:
    """GDPR-003 (lado API): regista cada leitura autorizada de PII de saúde."""
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
    """Sem autenticação — não expõe dados, só confirma que o serviço está de pé."""
    return {"status": "ok"}


@app.get("/api/devices/{device_id}/heart-rate-trends")
def heart_rate_trends(
    device_id: int,
    request: Request,
    days: int = Query(default=7, ge=1, le=3650),
    db: Session = Depends(_get_db),
    user: sa.User = Depends(_require_user),
):
    device = db.get(sa.Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    _authorize_patient(db, user, device.patient_id)
    _audit_read(db, user, request, "heart_rate.read", "device", device_id)
    return sa.Analytics.heart_rate_trends(db, device_id, days=days)


@app.get("/api/patients/{patient_id}/medication-adherence")
def medication_adherence(
    patient_id: int,
    request: Request,
    days: int = Query(default=30, ge=1, le=3650),
    db: Session = Depends(_get_db),
    user: sa.User = Depends(_require_user),
):
    patient = db.get(sa.Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _authorize_patient(db, user, patient_id)
    _audit_read(db, user, request, "medication_adherence.read", "patient", patient_id)
    return sa.Analytics.medication_adherence_summary(db, patient_id, days=days)


@app.get("/api/devices/{device_id}/activity-distribution")
def activity_distribution(
    device_id: int,
    request: Request,
    date: str = Query(..., description="Data no formato AAAA-MM-DD"),
    db: Session = Depends(_get_db),
    user: sa.User = Depends(_require_user),
):
    device = db.get(sa.Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    _authorize_patient(db, user, device.patient_id)
    try:
        parsed_date = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de data inválido, use AAAA-MM-DD")
    _audit_read(db, user, request, "activity.read", "device", device_id)
    return sa.Analytics.daily_activity_distribution(db, device_id, parsed_date)


class MedicationAdherenceIn(BaseModel):
    """Corpo do POST de aderência — ver `record_medication_adherence` abaixo."""

    scheduled_datetime: datetime
    taken: bool
    method: Literal["manual_entry", "wearable_detection", "ai_inference"] = "manual_entry"
    notes: Optional[str] = None


@app.post("/api/medications/{medication_id}/adherence")
def record_medication_adherence(
    medication_id: int,
    body: MedicationAdherenceIn,
    request: Request,
    db: Session = Depends(_get_db),
    user: sa.User = Depends(_require_user),
):
    """Regista (ou atualiza) se uma dose agendada foi tomada.

    Idempotente por desenho: `(medication_id, scheduled_datetime)` identifica
    uma dose agendada — um pedido repetido para a mesma dose atualiza o
    registo existente em vez de criar duplicados (mesmo comportamento que
    `markDoseTaken()` já tem no dashboard via localStorage, só que aqui
    persistido). Cada escrita fica registada em `AuditLog` (ação sensível,
    mesmo padrão já documentado para o resto do schema).

    Autorização (API-002): exige estar associado ao paciente do medicamento
    com permissão de escrita — ver `_authorize_patient(..., write=True)`.

    BUG CORRIGIDO: o SELECT abaixo (verifica se já existe registo) e o
    INSERT/UPDATE seguinte não são atómicos — dois pedidos concorrentes para
    a MESMA dose podiam ambos ver "não existe" e ambos inserir, criando duas
    linhas (só a `UniqueConstraint` nova em `MedicationAdherence.__table_args__`,
    storage_advanced.py, impede isto de facto; ver o comentário lá para a
    reprodução concreta). Em vez de deixar esse conflito rebentar como um
    500 para o pedido que perde a corrida, tenta-se aqui uma segunda vez:
    se o commit falhar por violação da constraint, descarta-se a tentativa de
    INSERT (rollback) e repete-se como UPDATE puro sobre a linha que já lá
    está — o resultado observável pelo cliente continua a ser sempre "a dose
    ficou registada", nunca um erro por causa de outro pedido legítimo para
    a mesma dose.
    """
    medication = db.get(sa.Medication, medication_id)
    if medication is None:
        raise HTTPException(status_code=404, detail="Medicamento não encontrado")
    _authorize_patient(db, user, medication.patient_id, write=True)

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
            # Outro pedido concorrente para a mesma dose venceu a corrida
            # entre este SELECT e este COMMIT — repete o ciclo, que agora
            # vai encontrar a linha dele no SELECT e fazer um UPDATE puro.
            continue
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
