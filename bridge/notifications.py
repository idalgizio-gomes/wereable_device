#!/usr/bin/env python3
"""notifications.py — notificações externas de alertas de emergência (SMS/email via Twilio/SendGrid).
NUNCA contacta o 112 diretamente (proibido por lei/ToS) — só escala com SMS/email mais urgente a um humano.
Env vars: CAREWEAR_TWILIO_ACCOUNT_SID/AUTH_TOKEN/FROM_NUMBER, CAREWEAR_SENDGRID_API_KEY/NOTIFY_FROM_EMAIL,
CAREWEAR_ESCALATION_TIMEOUT_MIN (min até escalar, 0 desativa, default 10)."""

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
    from twilio.rest import Client  # import tardio: só exige o pacote se SMS estiver configurado
    return Client(sid, token)


def send_sms(to_number: str, message: str) -> bool:
    """Envia SMS via Twilio. Devolve False sem credenciais; nunca levanta exceção."""
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
    """Envia email via SendGrid. Devolve False sem credenciais."""
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
    """Destinatário de notificação: cuidador ou contacto de emergência do paciente."""
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None


@dataclass
class ScheduleWindow:
    """Janela semanal de indisponibilidade do cuidador. weekday: 0=segunda...6=domingo.
    Não suporta janela que atravessa a meia-noite — usar duas janelas."""
    weekday: int
    start: dt_time
    end: dt_time

    def contains(self, when: datetime) -> bool:
        return when.weekday() == self.weekday and self.start <= when.time() < self.end


def caregiver_unavailable_now(schedule: Optional[List[ScheduleWindow]], when: Optional[datetime] = None) -> bool:
    """True se `when` (default: agora) cair numa janela de indisponibilidade. Sem horário, assume sempre contactável."""
    if not schedule:
        return False
    when = when or datetime.now()
    return any(w.contains(when) for w in schedule)


class EscalationManager:
    """Gere o ciclo de vida de notificação de um alerta. Alertas pendentes vivem em memória
    (`self._pending`) — perdidos se o bridge reiniciar."""

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
        """Notifica todos os destinatários já (T+0). Escalonamento automático (T+timeout) só é
        agendado se o cuidador estiver indisponível agora, conforme `caregiver_schedule`.
        send_sms/send_email correm em to_thread por serem bloqueantes."""
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
        """Cancela o escalonamento pendente do alerta, se existir. Devolve True se havia um."""
        task = self._pending.pop(alert_id, None)
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    def pending_count(self) -> int:
        return len(self._pending)
