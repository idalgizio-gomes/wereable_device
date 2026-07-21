#!/usr/bin/env python3
"""
notifications.py — Notificações externas de alertas de emergência (SMS/email).

CONTEXTO
--------
`ble_bridge.py` já deteta e regista alertas de emergência (SOS manual ou
queda+inatividade confirmada, ver `_on_emergency_alert()`), mas nunca
notificou ninguém fora do dashboard (ver PROJECT_STATUS.md, "precisa de um
provedor real com credenciais do utilizador, decisão pendente"). Este
módulo fecha essa lacuna com Twilio (SMS) + SendGrid (email, também da
Twilio) — provedor confirmado pelo utilizador.

DECISÃO DELIBERADA SOBRE O 112/SERVIÇOS DE EMERGÊNCIA (pedido explícito do
utilizador para contacto automático e direto ao 112, recusado — ver
PROJECT_STATUS.md para a justificação completa: uso indevido de linha de
emergência é contraordenação/crime em Portugal independente da intenção,
viola os termos de serviço da Twilio para chamadas automatizadas a
números de emergência, e nenhum sistema real de teleassistência
automatiza essa chamada). Este módulo NUNCA contacta o 112 ou qualquer
serviço de emergência real. O que faz:
  1. Notifica imediatamente os cuidadores + o contacto de emergência do
     paciente (SMS/email) com os detalhes do alerta.
  2. Se o alerta acontecer dentro do horário declarado de indisponibilidade
     do cuidador (`ScheduleWindow`/`caregiver_unavailable_now()` — ex.:
     horário de trabalho) e ninguém o confirmar
     (`EscalationManager.acknowledge()`) dentro de
     `escalation_timeout_minutes`, envia UMA mensagem de escalonamento ao
     contacto de emergência a sugerir explicitamente contactar o 112 — só
     um SMS/email mais urgente a um humano, nunca uma chamada automatizada
     real. Fora do horário declarado (cuidador presumivelmente
     contactável), NÃO escala sozinho — decisão do utilizador: só o
     cuidador pode agir nesse caso.

CONFIGURAÇÃO (variáveis de ambiente, mesmo padrão de `crypto_utils.py` —
nunca no código-fonte; sem configurar, degrada para um aviso no log, nunca
falha silenciosamente nem finge enviar):
  CAREWEAR_TWILIO_ACCOUNT_SID / CAREWEAR_TWILIO_AUTH_TOKEN — credenciais Twilio
  CAREWEAR_TWILIO_FROM_NUMBER — número Twilio de origem dos SMS (formato E.164)
  CAREWEAR_SENDGRID_API_KEY / CAREWEAR_NOTIFY_FROM_EMAIL — email via SendGrid
  CAREWEAR_ESCALATION_TIMEOUT_MIN — minutos até escalar (por omissão 10; 0 desativa)
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from typing import Dict, List, Optional

_TWILIO_SID_ENV = "CAREWEAR_TWILIO_ACCOUNT_SID"
_TWILIO_TOKEN_ENV = "CAREWEAR_TWILIO_AUTH_TOKEN"
_TWILIO_FROM_SMS_ENV = "CAREWEAR_TWILIO_FROM_NUMBER"
_SENDGRID_KEY_ENV = "CAREWEAR_SENDGRID_API_KEY"
_NOTIFY_FROM_EMAIL_ENV = "CAREWEAR_NOTIFY_FROM_EMAIL"
_ESCALATION_TIMEOUT_ENV = "CAREWEAR_ESCALATION_TIMEOUT_MIN"

DEFAULT_ESCALATION_TIMEOUT_MINUTES = 10


def sms_configured() -> bool:
    return bool(os.environ.get(_TWILIO_SID_ENV) and os.environ.get(_TWILIO_TOKEN_ENV) and os.environ.get(_TWILIO_FROM_SMS_ENV))


def email_configured() -> bool:
    return bool(os.environ.get(_SENDGRID_KEY_ENV) and os.environ.get(_NOTIFY_FROM_EMAIL_ENV))


def _get_twilio_client():
    sid = os.environ.get(_TWILIO_SID_ENV)
    token = os.environ.get(_TWILIO_TOKEN_ENV)
    if not sid or not token:
        return None
    from twilio.rest import Client  # import tardio: só exige o pacote instalado se TLS/SMS estiver configurado
    return Client(sid, token)


def send_sms(to_number: str, message: str) -> bool:
    """Envia um SMS via Twilio. Devolve False (e regista aviso) sem
    credenciais configuradas — nunca levanta exceção para não bloquear o
    resto do fluxo de emergência por causa de notificações."""
    if not sms_configured():
        print(f"[NOTIF] AVISO: Twilio (SMS) nao configurado — mensagem NAO enviada para {to_number}: {message!r}")
        return False
    from_number = os.environ[_TWILIO_FROM_SMS_ENV]
    try:
        client = _get_twilio_client()
        client.messages.create(to=to_number, from_=from_number, body=message)
        print(f"[NOTIF] SMS enviado para {to_number}")
        return True
    except Exception as exc:  # noqa: BLE001 - notificacao nunca deve derrubar o bridge
        print(f"[NOTIF] erro a enviar SMS para {to_number}: {exc}")
        return False


def send_email(to_email: str, subject: str, body: str) -> bool:
    """Envia um email via SendGrid (Twilio). Devolve False (e regista
    aviso) sem credenciais configuradas."""
    if not email_configured():
        print(f"[NOTIF] AVISO: SendGrid (email) nao configurado — email NAO enviado para {to_email}: {subject!r}")
        return False
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        mail = Mail(
            from_email=os.environ[_NOTIFY_FROM_EMAIL_ENV],
            to_emails=to_email,
            subject=subject,
            plain_text_content=body,
        )
        SendGridAPIClient(os.environ[_SENDGRID_KEY_ENV]).send(mail)
        print(f"[NOTIF] email enviado para {to_email}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[NOTIF] erro a enviar email para {to_email}: {exc}")
        return False


@dataclass
class EmergencyContact:
    """Um destinatário de notificação de emergência — cuidador (User) ou o
    'contacto de emergência' designado do paciente (Patient.emergency_contact_*).
    `phone`/`email` em falta são simplesmente ignorados (ex.: o contacto de
    emergência só tem telefone no esquema atual, sem coluna de email)."""
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None


@dataclass
class ScheduleWindow:
    """Uma janela semanal em que o CUIDADOR (não o paciente) está
    tipicamente indisponível/incontactável (ex.: horário de trabalho) —
    definida pelo próprio cuidador no dashboard (ver PROJECT_STATUS.md).
    `weekday` segue a convenção de `datetime.weekday()`: 0=segunda ...
    6=domingo. Uma janela que atravessa a meia-noite (ex.: turno noturno)
    não é suportada aqui — definir como duas janelas separadas."""
    weekday: int
    start: dt_time
    end: dt_time

    def contains(self, when: datetime) -> bool:
        return when.weekday() == self.weekday and self.start <= when.time() < self.end


def caregiver_unavailable_now(schedule: Optional[List[ScheduleWindow]], when: Optional[datetime] = None) -> bool:
    """True se `when` (por omissão, agora) cair dentro de alguma janela do
    horário declarado do cuidador. Sem horário declarado, assume-se que o
    cuidador está sempre contactável (comportamento anterior, sem
    escalonamento automático)."""
    if not schedule:
        return False
    when = when or datetime.now()
    return any(w.contains(when) for w in schedule)


class EscalationManager:
    """Gere o ciclo de vida de notificação de um alerta de emergência.
    Uma instância por processo do bridge — os alertas pendentes vivem em
    memória (`self._pending`), perdidos se o bridge reiniciar; aceitável
    para um protótipo single-process, documentado como limitação."""

    def __init__(self, escalation_timeout_minutes: Optional[int] = None):
        if escalation_timeout_minutes is None:
            escalation_timeout_minutes = int(os.environ.get(_ESCALATION_TIMEOUT_ENV, DEFAULT_ESCALATION_TIMEOUT_MINUTES))
        self.escalation_timeout_minutes = escalation_timeout_minutes
        self._pending: Dict[str, "asyncio.Task[None]"] = {}

    async def notify_emergency(
        self,
        alert_id: str,
        alert_summary: str,
        caregivers: List[EmergencyContact],
        emergency_contact: Optional[EmergencyContact] = None,
        caregiver_schedule: Optional[List[ScheduleWindow]] = None,
        now: Optional[datetime] = None,
    ) -> None:
        """Notifica de imediato todos os destinatários (T+0). O escalonamento
        automático (T+timeout, mensagem urgente ao contacto de emergência)
        só é agendado quando `caregiver_schedule` diz que o cuidador está
        tipicamente indisponível agora (ex.: no trabalho) — decisão do
        utilizador: fora dessa janela, só o cuidador pode agir, o sistema
        não escala sozinho. Sem horário declarado, nunca escala automático
        (comportamento conservador por omissão).

        `send_sms`/`send_email` são chamadas de rede síncronas/bloqueantes
        (Twilio/SendGrid) — corridas em `asyncio.to_thread` para não
        bloquear o event loop partilhado com BLE/WebSocket."""
        recipients = list(caregivers)
        if emergency_contact is not None:
            recipients.append(emergency_contact)
        message = f"[CareWear] Alerta de emergencia: {alert_summary}"
        for r in recipients:
            if r.phone:
                await asyncio.to_thread(send_sms, r.phone, message)
            if r.email:
                await asyncio.to_thread(send_email, r.email, "CareWear - Alerta de emergencia", message)

        should_escalate = (
            emergency_contact is not None
            and self.escalation_timeout_minutes > 0
            and caregiver_unavailable_now(caregiver_schedule, now)
        )
        if should_escalate:
            old = self._pending.pop(alert_id, None)
            if old and not old.done():
                old.cancel()
            self._pending[alert_id] = asyncio.create_task(
                self._escalate_after_timeout(alert_id, alert_summary, emergency_contact)
            )

    async def _escalate_after_timeout(self, alert_id: str, alert_summary: str, emergency_contact: EmergencyContact) -> None:
        try:
            await asyncio.sleep(self.escalation_timeout_minutes * 60)
        except asyncio.CancelledError:
            return
        message = (
            f"[CareWear] O alerta de emergencia AINDA NAO foi confirmado "
            f"{self.escalation_timeout_minutes} min depois: {alert_summary}. "
            f"Se nao conseguires contactar {emergency_contact.name} de outra forma, "
            f"considera ligar ja para o 112."
        )
        if emergency_contact.phone:
            await asyncio.to_thread(send_sms, emergency_contact.phone, message)
        if emergency_contact.email:
            await asyncio.to_thread(send_email, emergency_contact.email, "CareWear - Alerta de emergencia NAO confirmado", message)
        self._pending.pop(alert_id, None)

    def acknowledge(self, alert_id: str) -> bool:
        """Chamado quando um cuidador confirma o alerta no dashboard.
        Cancela o escalonamento pendente, se existir. Devolve True se havia
        de facto um escalonamento pendente para este alerta."""
        task = self._pending.pop(alert_id, None)
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    def pending_count(self) -> int:
        return len(self._pending)
