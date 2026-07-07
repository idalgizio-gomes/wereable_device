#!/usr/bin/env python3
"""
api.py — API REST somente-leitura sobre as queries analíticas de storage_advanced.py.

Primeiro passo do "próximo item concreto da Prioridade 4" registado em
PROJECT_STATUS.md (secção "Cifra real dos campos sensíveis (NIF, morada) +
Alembic", 2026-07-07): ligar as queries analíticas (`Analytics.*`) a um
serviço HTTP, para que o dashboard possa um dia consumir histórico real via
rede em vez de depender só do bridge WebSocket local (`ble_bridge.py`,
`ws://localhost:8765`).

Âmbito deliberadamente pequeno para uma execução: só leitura (GET), sem
escrita/mutações. Correr localmente:

    pip install -r bridge/requirements_db.txt
    export CAREWEAR_API_KEY=<chave escolhida>
    cd bridge && uvicorn api:app --host 127.0.0.1 --port 8766

**Ainda por fazer** (fora do âmbito desta primeira versão, ver
PROJECT_STATUS.md): integração com `web/dashboard/index.html` (que hoje só
fala com o bridge por WebSocket), integração com `ble_bridge.py`
(`storage_advanced.py` continua sem ligação ao streaming BLE real — só
`storage.py`, o módulo SQLite mais simples, está integrado), autenticação
de produção (por-utilizador, rotação de chave, rate-limiting — a chave
estática única aqui é só um protótipo, mesmo nível de honestidade já usado
para a cifra AES-CTR/AES-GCM do projeto).
"""
from __future__ import annotations

import hmac
import os
from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from sqlalchemy.orm import Session

import storage_advanced as sa

app = FastAPI(
    title="CareWear API",
    description="API REST somente-leitura para dados analíticos (protótipo, Prioridade 4).",
    version="0.1.0",
)

API_KEY_ENV_VAR = "CAREWEAR_API_KEY"
_warned_no_api_key = False


def _get_db():
    db = sa.get_db_session()
    try:
        yield db
    finally:
        db.close()


def _require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """Autenticação mínima por chave estática partilhada (protótipo).

    Ao contrário da cifra de campos sensíveis (`crypto_utils.py`) e da cifra
    AES-CTR do streaming BLE — que "degradam de forma visível" (avisam mas
    continuam a funcionar sem cifrar) — esta API **falha fechada** sem
    `CAREWEAR_API_KEY` configurada: os dados aqui expostos (FC, aderência a
    medicação, rotina diária) são PII de saúde servidos por rede, não um
    stream local; deixar passar pedidos sem chave por omissão seria o pior
    comportamento possível. Não é autenticação de produção (sem
    por-utilizador, sem rotação, sem rate-limiting) — decisão pendente,
    registada em PROJECT_STATUS.md.
    """
    global _warned_no_api_key
    configured = os.environ.get(API_KEY_ENV_VAR)
    if not configured:
        if not _warned_no_api_key:
            print(
                f"[API] AVISO: {API_KEY_ENV_VAR} não configurada — todos os "
                "pedidos autenticados serão REJEITADOS (falha fechada). "
                "Defina esta variável de ambiente para ativar a API."
            )
            _warned_no_api_key = True
        raise HTTPException(status_code=503, detail=f"{API_KEY_ENV_VAR} não configurada no servidor")
    # Comparação em tempo constante — "!=" numa string compara byte a byte e
    # sai assim que encontra a primeira diferença, o que teoricamente permite
    # a um atacante reconstruir a chave certa por temporização (mede-se
    # quanto tempo demora a rejeitar cada tentativa). `hmac.compare_digest`
    # evita esse atalho.
    if not hmac.compare_digest(x_api_key or "", configured):
        raise HTTPException(status_code=401, detail="Chave de API inválida ou ausente")


@app.get("/health")
def health():
    """Sem autenticação — não expõe dados, só confirma que o serviço está de pé."""
    return {"status": "ok"}


@app.get(
    "/api/devices/{device_id}/heart-rate-trends",
    dependencies=[Depends(_require_api_key)],
)
def heart_rate_trends(
    device_id: int,
    days: int = Query(default=7, ge=1, le=3650),
    db: Session = Depends(_get_db),
):
    device = db.get(sa.Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    return sa.Analytics.heart_rate_trends(db, device_id, days=days)


@app.get(
    "/api/patients/{patient_id}/medication-adherence",
    dependencies=[Depends(_require_api_key)],
)
def medication_adherence(
    patient_id: int,
    days: int = Query(default=30, ge=1, le=3650),
    db: Session = Depends(_get_db),
):
    patient = db.get(sa.Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    return sa.Analytics.medication_adherence_summary(db, patient_id, days=days)


@app.get(
    "/api/devices/{device_id}/activity-distribution",
    dependencies=[Depends(_require_api_key)],
)
def activity_distribution(
    device_id: int,
    date: str = Query(..., description="Data no formato AAAA-MM-DD"),
    db: Session = Depends(_get_db),
):
    device = db.get(sa.Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    try:
        parsed_date = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de data inválido, use AAAA-MM-DD")
    return sa.Analytics.daily_activity_distribution(db, device_id, parsed_date)
