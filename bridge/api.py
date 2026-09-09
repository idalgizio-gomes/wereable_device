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

**2026-09-07 (RF-11 + RF-12)**: dois endpoints de leitura novos —
`/api/devices/{id}/fhir/observations` (exportação HL7 FHIR R4, ver
`fhir_export.py`) e `/api/patients/{id}/weekly-report` (relatório semanal
agregado). Ambos seguem obrigatoriamente o mesmo trio de segurança dos
endpoints anteriores: `_require_user` -> `_authorize_patient` ->
`_audit_read`. Não há exceções a este trio nesta API.

Nota importante: `import api_auth` (abaixo) regista o modelo `ApiKey` na
`Base` partilhada de `storage_advanced` — é isso que faz a tabela `api_keys`
ser criada por `create_all_tables()` e pelo `create_all` dos testes sem
tocar em `storage_advanced.py`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, field_validator
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
    authorization: Optional[str] = Header(default=None),
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
    if row is not None:
        row.last_used_at = datetime.utcnow()
        return row.user

    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    user = auth_sessions.resolve_session(db, token)
    if user is not None:
        return user

    raise HTTPException(status_code=401, detail="Não autenticado")


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
    return {"id": user.id, "email": user.email, "role": user.role, "name": user.name}


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

    @field_validator("scheduled_datetime")
    @classmethod
    def _normalize_to_naive_utc(cls, value: datetime) -> datetime:
        """BUG CORRIGIDO: normaliza para UTC "naive" (sem fuso).

        O pydantic aceita ISO-8601 com fuso e devolve um datetime AWARE
        ("2026-09-07T10:00:00+01:00" -> 10:00+01:00), mas
        `MedicationAdherence.scheduled_datetime` é uma coluna `DateTime`
        sem fuso e todo o resto do ficheiro trabalha em UTC naive
        (`datetime.utcnow()`, tal como `storage_advanced.py`). O dialeto
        SQLite descarta o offset sem converter, por isso a hora LOCAL do
        cliente era gravada como se fosse UTC.

        Duas consequências reais, ambas reproduzidas:
          1. Quebra a idempotência documentada abaixo e a
             `UniqueConstraint(medication_id, scheduled_datetime)` de que
             a recuperação de corridas depende — o MESMO instante enviado
             como "10:00+01:00" e como "09:00Z" gravava DUAS linhas
             (10:00 e 09:00), em vez de atualizar a mesma dose.
          2. Desvia a dose do instante real, o que enviesa o corte
             `scheduled_datetime >= cutoff` de
             `Analytics.medication_adherence_summary` (que compara contra
             um `datetime.utcnow()` naive).

        Converter para UTC e retirar o fuso deixa os dois clientes acima a
        produzir exatamente o mesmo valor. Entradas já naive (o formato
        que o dashboard envia hoje) ficam inalteradas.
        """
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value


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


# ======================================================================
# RF-11 — API DE EXPORTAÇÃO INTEROPERÁVEL (HL7 FHIR R4), 2026-09-07
# ======================================================================
# Justificação (revisão de literatura deste projeto): a dimensão
# "Interoperabilidade" está a 0/20 estudos — nenhum trabalho do corpus
# implementa API, FHIR, HL7 ou integração com registo clínico eletrónico.
# O mapeamento sinal->código clínico vive todo em `fhir_export.py`
# (incluindo a decisão de NÃO emitir códigos LOINC não confirmados); aqui
# fica só o HTTP, a autorização e a auditoria.

# 87600h = 10 anos. Mesmo teto já usado no export CSV do dashboard
# (EXPORT_ALL_HOURS em web/dashboard/export-clinico.js) — não há "sem
# limite" real, `hours` é sempre um corte.
MAX_EXPORT_HOURS = 87600
# Teto duro de recursos por resposta. Um Bundle FHIR é JSON expandido
# (cada amostra vira até 4 Observations com codings e unidades), por isso
# uma janela larga sobre uma tabela com milhões de linhas construiria
# centenas de MB em memória antes de sair um único byte. FHIR resolve isto
# com paginação por `Bundle.link` (relation "next"); enquanto essa não
# existir, trunca-se explicitamente e diz-se no próprio Bundle que foi
# truncado (ver `carewear-truncated` abaixo) — nunca silenciosamente.
MAX_FHIR_RESOURCES = 5000


@app.get("/api/devices/{device_id}/fhir/observations")
def fhir_observations(
    device_id: int,
    request: Request,
    hours: float = Query(default=24, gt=0, le=MAX_EXPORT_HOURS),
    include_activity: bool = Query(default=True),
    limit: int = Query(default=1000, ge=1, le=MAX_FHIR_RESOURCES),
    db: Session = Depends(_get_db),
    user: sa.User = Depends(_require_user),
):
    """Devolve as observações do dispositivo como `Bundle` FHIR R4 (searchset).

    RF-11. Mapeia para `Observation` os sinais que o sistema já guarda:
    frequência cardíaca e SpO2 (`SensorRecord`, códigos LOINC confirmados),
    passos e índice de pacing (`SensorRecord`, por confirmar) e duração dos
    blocos de rotina (`ActivityWindow`, por confirmar). Ver
    `fhir_export.SIGNAL_MAPPINGS` para a decisão código-a-código e
    `fhir_export.unconfirmed_signals()` para o que ficou por confirmar.

    Autorização: trio obrigatório desta API — `_require_user` (dependência),
    `_authorize_patient` (o dispositivo pertence a um paciente; quem não
    está associado leva 404, não 403, para não permitir enumeração de IDs) e
    `_audit_read` (RGPD/GDPR-003 — uma exportação clínica completa é
    precisamente o tipo de leitura que TEM de deixar rasto).

    O filtro usa `received_at` (instante em que o bridge recebeu), não
    `timestamp_utc` (relógio do dispositivo, que pode estar
    dessincronizado) — mesmo critério de `sa.get_records_since()`. Mas o
    `effectiveDateTime` de cada Observation usa `timestamp_utc`, porque é
    esse o instante em que a medição foi FEITA, que é o que
    `Observation.effective[x]` significa em FHIR.
    """
    device = db.get(sa.Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    _authorize_patient(db, user, device.patient_id)
    _audit_read(db, user, request, "fhir_observations.read", "device", device_id)

    patient = db.get(sa.Patient, device.patient_id)
    subject = fhir_export.build_subject(
        device.patient_id,
        pseudonym=getattr(patient, "pseudonym", None) if patient else None,
    )

    cutoff = datetime.utcnow() - timedelta(hours=hours)
    records = (
        db.query(sa.SensorRecord)
        .filter(sa.SensorRecord.device_id == device_id, sa.SensorRecord.received_at >= cutoff)
        .order_by(sa.SensorRecord.received_at.asc())
        .limit(limit)
        .all()
    )

    observations: list[dict] = []
    for record in records:
        observations.extend(fhir_export.observations_from_sensor_record(record, subject, device_id))

    if include_activity:
        windows = (
            db.query(sa.ActivityWindow)
            .filter(
                sa.ActivityWindow.device_id == device_id,
                sa.ActivityWindow.activity_date >= cutoff,
            )
            .order_by(sa.ActivityWindow.activity_date.asc())
            .limit(limit)
            .all()
        )
        for window in windows:
            observation = fhir_export.observation_from_activity_window(window, subject, device_id)
            if observation is not None:
                observations.append(observation)

    truncated = len(observations) > MAX_FHIR_RESOURCES
    if truncated:
        observations = observations[:MAX_FHIR_RESOURCES]

    bundle = fhir_export.build_observation_bundle(
        observations,
        base_url=str(request.base_url).rstrip("/") + "/fhir",
    )
    if truncated:
        # `Bundle.meta.tag` é o sítio previsto em FHIR para marcar
        # propriedades da própria resposta. Um recetor que ignore a tag
        # continua a ler um Bundle válido; um que a leia sabe que a
        # exportação está incompleta e tem de reduzir a janela.
        bundle["meta"] = {"tag": [{
            "system": "urn:carewear:bundle-flags",
            "code": "carewear-truncated",
            "display": (
                f"Resposta truncada em {MAX_FHIR_RESOURCES} recursos — "
                "reduza 'hours' ou 'limit' para obter o resto."
            ),
        }]}
    return bundle


@app.get("/api/fhir/observation-mappings")
def fhir_observation_mappings(user: sa.User = Depends(_require_user)):
    """Metadados do mapeamento FHIR: que sinais têm código confirmado e quais não.

    Não devolve dados clínicos de paciente nenhum — só o dicionário de
    mapeamento do próprio sistema. Por isso exige autenticação
    (`_require_user`) mas NÃO chama `_authorize_patient`/`_audit_read`:
    não há paciente a autorizar nem leitura de PII a auditar. É a única
    exceção ao trio nesta API, e é-o por não tocar em dados de saúde.

    Serve para um integrador saber, ANTES de consumir o Bundle, o que
    pode processar automaticamente (coding LOINC) e o que só tem `text`.
    """
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


# ======================================================================
# RF-12 — RELATÓRIO PERIÓDICO (SEMANAL) POR PACIENTE, 2026-09-07
# ======================================================================
# Este endpoint devolve os DADOS do relatório; a apresentação (PDF) é
# feita pelo dashboard, reutilizando a folha de impressão que já existia
# (`#clinicalPrintSheet`, ver web/dashboard/export-clinico.js). Não se
# criou um segundo mecanismo de exportação.
#
# AGENDAMENTO: o projeto já tem um mecanismo Cron para tarefas periódicas
# (relatório do projeto, cap. 6; usado hoje para limpeza de registos
# antigos — ver `sa.DataRetention`). A geração periódica LIGA-SE A ESSE
# Cron em vez de trazer um agendador novo: a tarefa semanal faz um GET
# autenticado a este endpoint por paciente e arquiva/envia o resultado.
# Nada aqui guarda estado nem agenda nada — o endpoint é puro e
# idempotente exatamente para poder ser chamado por um agendador externo
# tantas vezes quantas forem precisas sem efeitos colaterais (a única
# escrita é a linha de auditoria, que é o comportamento desejado).

WEEKLY_REPORT_DAYS = 7
# Teto de alertas listados no relatório. O relatório é um resumo semanal
# para leitura humana (e depois PDF), não um dump — a contagem POR
# SEVERIDADE vai sempre completa; só a lista detalhada é truncada.
WEEKLY_REPORT_MAX_ALERTS = 50


def _weekly_period(end: Optional[str]) -> tuple[datetime, datetime]:
    """Calcula [início, fim) da semana do relatório, em UTC naive.

    `end` opcional (AAAA-MM-DD) permite ao Cron pedir explicitamente "a
    semana que terminou no dia X" em vez de depender da hora a que a
    tarefa agendada correu — sem isto, um atraso do agendador deslocava
    silenciosamente a janela do relatório.
    """
    if end is None:
        period_end = datetime.utcnow()
    else:
        try:
            parsed = datetime.strptime(end, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de data inválido, use AAAA-MM-DD")
        # Fim EXCLUSIVO no início do dia seguinte, para o dia indicado
        # entrar inteiro no relatório.
        period_end = parsed + timedelta(days=1)
    return period_end - timedelta(days=WEEKLY_REPORT_DAYS), period_end


@app.get("/api/patients/{patient_id}/weekly-report")
def weekly_report(
    patient_id: int,
    request: Request,
    end: Optional[str] = Query(default=None, description="Último dia da semana (AAAA-MM-DD); por omissão, agora"),
    db: Session = Depends(_get_db),
    user: sa.User = Depends(_require_user),
):
    """Relatório semanal de um paciente: rotina, sinais vitais, alertas e adesão.

    RF-12. As quatro secções do critério de aceitação, agregadas a partir
    do que já está persistido — nenhuma delas é dado novo:

      * `rotina`            -> `ActivityWindow` (minutos por categoria)
      * `sinais_vitais`     -> `SensorRecord` (FC, SpO2, passos), agregado em SQL
      * `alertas`           -> `Alert` dos dispositivos do paciente, por severidade
      * `adesao_medicacao`  -> `Analytics.medication_adherence_summary`

    Autorização: trio obrigatório (`_require_user` + `_authorize_patient` +
    `_audit_read`). Um relatório semanal completo é a leitura mais
    abrangente que esta API oferece, portanto é a que mais precisa de
    ficar auditada.

    Os agregados de sinais vitais são calculados em SQL (`func.avg/min/max`)
    e NÃO por `Analytics.heart_rate_trends`: essa devolve também a série
    completa de amostras (`records`), o que numa semana de dados reais são
    dezenas de milhares de pontos que o relatório não usa para nada.
    """
    patient = db.get(sa.Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    _authorize_patient(db, user, patient_id)
    _audit_read(db, user, request, "weekly_report.read", "patient", patient_id)

    period_start, period_end = _weekly_period(end)

    device_ids = [d.id for d in db.query(sa.Device).filter(sa.Device.patient_id == patient_id).all()]

    # ---------------- Rotina ----------------
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
                # Média por DIA da semana (não por janela): é o número que
                # um clínico compara com o dia anterior.
                "daily_average_minutes": round((total or 0) / WEEKLY_REPORT_DAYS, 1),
            }
            for category, total, count in rows
        }
    # Categorias sem qualquer janela aparecem a zero em vez de
    # desaparecerem — "0 minutos registados" é informação clínica; uma
    # linha em falta no relatório seria ambígua (não medido? não houve?).
    for category in ("sleep", "rest", "activity", "eating", "hygiene"):
        rotina.setdefault(category, {"total_minutes": 0, "windows_count": 0, "daily_average_minutes": 0.0})

    # ---------------- Sinais vitais ----------------
    sinais_vitais: dict[str, Optional[dict]] = {"heart_rate": None, "spo2_percent": None, "steps": None}
    if device_ids:
        # `timestamp_utc` é epoch; `period_start/end` são datetimes UTC
        # naive. Marcar como UTC antes de .timestamp() é obrigatório —
        # sem isso o Python interpreta-os como hora LOCAL do servidor e o
        # corte desvia-se pelo offset do fuso (mesmo cuidado já
        # documentado em Analytics.heart_rate_trends).
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

    # ---------------- Alertas ----------------
    severities = {"info": 0, "warning": 0, "serious": 0, "critical": 0}
    alert_list: list[dict] = []
    if device_ids:
        alert_rows = (
            db.query(sa.Alert)
            .filter(
                sa.Alert.device_id.in_(device_ids),
                sa.Alert.created_at >= period_start,
                sa.Alert.created_at < period_end,
                # Soft delete: alertas já apagados pela política de
                # retenção não contam para o relatório.
                sa.Alert.deleted_at.is_(None),
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

    # ---------------- Adesão à medicação ----------------
    adesao = sa.Analytics.medication_adherence_summary(db, patient_id, days=WEEKLY_REPORT_DAYS)

    return {
        "patient_id": patient_id,
        # Pseudónimo em vez do nome: um relatório arquivado pelo Cron não
        # precisa de conter PII para ser útil a quem já sabe de quem é.
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
