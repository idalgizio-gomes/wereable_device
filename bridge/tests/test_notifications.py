"""Testes unitários para `bridge/notifications.py` (SMS/email de emergência,
Fase 5 do roteiro do utilizador -- ver PROJECT_STATUS.md).

Nenhum destes testes contacta a Twilio/SendGrid reais: `send_sms`/
`send_email` são sempre chamados sem as variáveis de ambiente configuradas
(devem degradar para um aviso, nunca lançar exceção), ou com os clientes
Twilio/SendGrid substituídos por mocks via `unittest.mock.patch`.
"""
import asyncio
from datetime import datetime, time as dt_time
from unittest.mock import MagicMock, patch

import pytest

import notifications


def _schedule_covering_now() -> list:
    """Janela que cobre o dia inteiro de hoje -- usada para simular
    'cuidador indisponível agora' de forma estável, independente da hora
    real a que os testes correm."""
    now = datetime.now()
    return [notifications.ScheduleWindow(weekday=now.weekday(), start=dt_time(0, 0), end=dt_time(23, 59, 59))]


def _schedule_not_covering_now() -> list:
    """Janela no dia seguinte (nunca 'agora') -- simula 'cuidador disponível'."""
    now = datetime.now()
    other_weekday = (now.weekday() + 1) % 7
    return [notifications.ScheduleWindow(weekday=other_weekday, start=dt_time(0, 0), end=dt_time(23, 59, 59))]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "CAREWEAR_TWILIO_ACCOUNT_SID",
        "CAREWEAR_TWILIO_AUTH_TOKEN",
        "CAREWEAR_TWILIO_FROM_NUMBER",
        "CAREWEAR_SENDGRID_API_KEY",
        "CAREWEAR_NOTIFY_FROM_EMAIL",
        "CAREWEAR_ESCALATION_TIMEOUT_MIN",
    ):
        monkeypatch.delenv(key, raising=False)


class TestDegradaSemCredenciais:
    """Sem credenciais configuradas, nunca finge enviar nem levanta exceção."""

    def test_send_sms_sem_credenciais(self):
        assert notifications.sms_configured() is False
        assert notifications.send_sms("+351900000000", "teste") is False

    def test_send_email_sem_credenciais(self):
        assert notifications.email_configured() is False
        assert notifications.send_email("a@b.com", "assunto", "corpo") is False


class TestEnvioComCredenciais:
    def test_send_sms_chama_twilio_corretamente(self, monkeypatch):
        monkeypatch.setenv("CAREWEAR_TWILIO_ACCOUNT_SID", "SIDxxx")
        monkeypatch.setenv("CAREWEAR_TWILIO_AUTH_TOKEN", "tokenxxx")
        monkeypatch.setenv("CAREWEAR_TWILIO_FROM_NUMBER", "+351911111111")

        mock_client = MagicMock()
        with patch("notifications._get_twilio_client", return_value=mock_client):
            ok = notifications.send_sms("+351922222222", "alerta de teste")

        assert ok is True
        mock_client.messages.create.assert_called_once_with(
            to="+351922222222", from_="+351911111111", body="alerta de teste"
        )

    def test_send_sms_falha_da_twilio_nao_rebenta(self, monkeypatch):
        monkeypatch.setenv("CAREWEAR_TWILIO_ACCOUNT_SID", "SIDxxx")
        monkeypatch.setenv("CAREWEAR_TWILIO_AUTH_TOKEN", "tokenxxx")
        monkeypatch.setenv("CAREWEAR_TWILIO_FROM_NUMBER", "+351911111111")

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("Twilio indisponível")
        with patch("notifications._get_twilio_client", return_value=mock_client):
            ok = notifications.send_sms("+351922222222", "alerta de teste")
        assert ok is False  # nunca propaga a exceção

    def test_send_email_chama_sendgrid_corretamente(self, monkeypatch):
        monkeypatch.setenv("CAREWEAR_SENDGRID_API_KEY", "SGxxx")
        monkeypatch.setenv("CAREWEAR_NOTIFY_FROM_EMAIL", "alerts@carewear.example")

        mock_sg_instance = MagicMock()
        mock_sg_client_cls = MagicMock(return_value=mock_sg_instance)
        with patch("sendgrid.SendGridAPIClient", mock_sg_client_cls):
            ok = notifications.send_email("familia@example.com", "Assunto", "Corpo")

        assert ok is True
        mock_sg_instance.send.assert_called_once()


class TestEscalationManager:
    """Notificação imediata a todos os destinatários + escalonamento SÓ se
    ninguém confirmar dentro do prazo -- NUNCA um contacto automático a
    serviços de emergência reais, só uma mensagem mais forte a um humano."""

    def _contacts(self):
        caregiver = notifications.EmergencyContact(name="Filha", phone="+351933333333", email="filha@example.com")
        emergency_contact = notifications.EmergencyContact(name="Vizinho", phone="+351944444444")
        return caregiver, emergency_contact

    def test_notifica_todos_os_destinatarios_de_imediato(self, monkeypatch):
        monkeypatch.setenv("CAREWEAR_TWILIO_ACCOUNT_SID", "SIDxxx")
        monkeypatch.setenv("CAREWEAR_TWILIO_AUTH_TOKEN", "tokenxxx")
        monkeypatch.setenv("CAREWEAR_TWILIO_FROM_NUMBER", "+351911111111")
        monkeypatch.setenv("CAREWEAR_SENDGRID_API_KEY", "SGxxx")
        monkeypatch.setenv("CAREWEAR_NOTIFY_FROM_EMAIL", "alerts@carewear.example")
        caregiver, emergency_contact = self._contacts()

        with patch("notifications.send_sms", return_value=True) as mock_sms, \
             patch("notifications.send_email", return_value=True) as mock_email:
            mgr = notifications.EscalationManager(escalation_timeout_minutes=0)  # 0 = sem escalonamento neste teste
            mgr.notify_emergency("alert-1", "queda detetada", [caregiver], emergency_contact)

        # SMS: cuidador + contacto de emergência (2 chamadas)
        assert mock_sms.call_count == 2
        called_numbers = {c.args[0] for c in mock_sms.call_args_list}
        assert called_numbers == {"+351933333333", "+351944444444"}
        # Email: só o cuidador tem email
        mock_email.assert_called_once()
        assert mock_email.call_args.args[0] == "filha@example.com"

    @pytest.mark.asyncio
    async def test_escalona_se_nao_confirmado_dentro_do_prazo_e_cuidador_indisponivel(self):
        caregiver, emergency_contact = self._contacts()
        with patch("notifications.send_sms", return_value=True) as mock_sms:
            # timeout curtíssimo (frações de segundo) só para o teste ser rápido
            mgr = notifications.EscalationManager(escalation_timeout_minutes=0)
            mgr.escalation_timeout_minutes = 0.001 / 60  # ~0.06ms em minutos, arredonda para baixo no sleep
            mgr._pending.clear()
            mgr.notify_emergency(
                "alert-2", "SOS manual", [caregiver], emergency_contact,
                caregiver_schedule=_schedule_covering_now(),
            )
            assert mgr.pending_count() == 1
            await asyncio.sleep(0.2)  # tempo suficiente para o escalonamento disparar

        assert mgr.pending_count() == 0
        # 2 chamadas iniciais (cuidador + contacto) + 1 de escalonamento (só contacto de emergência)
        numbers_called = [c.args[0] for c in mock_sms.call_args_list]
        assert numbers_called.count("+351944444444") == 2  # notificação inicial + escalonamento
        assert numbers_called.count("+351933333333") == 1  # só a notificação inicial

    def test_nao_escala_se_cuidador_disponivel_fora_do_horario_declarado(self):
        """Fora do horário declarado de indisponibilidade, o utilizador
        decidiu explicitamente que só o cuidador pode agir -- o sistema
        NUNCA agenda escalonamento automático, mesmo com contacto de
        emergência definido e timeout > 0."""
        caregiver, emergency_contact = self._contacts()
        with patch("notifications.send_sms", return_value=True):
            mgr = notifications.EscalationManager(escalation_timeout_minutes=10)
            mgr.notify_emergency(
                "alert-2b", "SOS manual", [caregiver], emergency_contact,
                caregiver_schedule=_schedule_not_covering_now(),
            )
        assert mgr.pending_count() == 0

    def test_nao_escala_sem_horario_declarado(self):
        """Sem qualquer horário declarado pelo cuidador, comportamento
        conservador por omissão: nunca escala sozinho."""
        caregiver, emergency_contact = self._contacts()
        with patch("notifications.send_sms", return_value=True):
            mgr = notifications.EscalationManager(escalation_timeout_minutes=10)
            mgr.notify_emergency("alert-2c", "SOS manual", [caregiver], emergency_contact)
        assert mgr.pending_count() == 0

    @pytest.mark.asyncio
    async def test_acknowledge_cancela_escalonamento_pendente(self):
        caregiver, emergency_contact = self._contacts()
        with patch("notifications.send_sms", return_value=True) as mock_sms:
            mgr = notifications.EscalationManager(escalation_timeout_minutes=1)
            mgr.notify_emergency(
                "alert-3", "queda detetada", [caregiver], emergency_contact,
                caregiver_schedule=_schedule_covering_now(),
            )
            assert mgr.pending_count() == 1

            was_pending = mgr.acknowledge("alert-3")
            assert was_pending is True
            assert mgr.pending_count() == 0

            await asyncio.sleep(0.05)  # dar tempo à task cancelada para terminar, se fosse disparar

        # só a notificação inicial (2 chamadas), NUNCA a mensagem de escalonamento
        assert mock_sms.call_count == 2

    def test_acknowledge_sem_alerta_pendente_devolve_false(self):
        mgr = notifications.EscalationManager()
        assert mgr.acknowledge("inexistente") is False

    def test_sem_contacto_de_emergencia_nao_agenda_escalonamento(self, monkeypatch):
        caregiver, _ = self._contacts()
        with patch("notifications.send_sms", return_value=True):
            mgr = notifications.EscalationManager(escalation_timeout_minutes=10)
            mgr.notify_emergency(
                "alert-4", "queda detetada", [caregiver], emergency_contact=None,
                caregiver_schedule=_schedule_covering_now(),
            )
        assert mgr.pending_count() == 0


class TestScheduleWindow:
    """`ScheduleWindow`/`caregiver_unavailable_now()` isolados de Twilio/SendGrid."""

    def test_sem_horario_nunca_indisponivel(self):
        assert notifications.caregiver_unavailable_now(None) is False
        assert notifications.caregiver_unavailable_now([]) is False

    def test_dentro_da_janela_declarada(self):
        segunda_manha = datetime(2026, 7, 20, 9, 30)  # 2026-07-20 é uma segunda-feira
        janela = [notifications.ScheduleWindow(weekday=0, start=dt_time(8, 0), end=dt_time(17, 0))]
        assert notifications.caregiver_unavailable_now(janela, segunda_manha) is True

    def test_fora_da_janela_mesmo_dia(self):
        segunda_noite = datetime(2026, 7, 20, 22, 0)
        janela = [notifications.ScheduleWindow(weekday=0, start=dt_time(8, 0), end=dt_time(17, 0))]
        assert notifications.caregiver_unavailable_now(janela, segunda_noite) is False

    def test_fora_da_janela_dia_errado(self):
        terca_manha = datetime(2026, 7, 21, 9, 30)  # terça-feira
        janela = [notifications.ScheduleWindow(weekday=0, start=dt_time(8, 0), end=dt_time(17, 0))]
        assert notifications.caregiver_unavailable_now(janela, terca_manha) is False

    def test_varias_janelas_qualquer_uma_basta(self):
        seg_manha = datetime(2026, 7, 20, 9, 0)
        janelas = [
            notifications.ScheduleWindow(weekday=1, start=dt_time(8, 0), end=dt_time(17, 0)),  # terça
            notifications.ScheduleWindow(weekday=0, start=dt_time(8, 0), end=dt_time(17, 0)),  # segunda
        ]
        assert notifications.caregiver_unavailable_now(janelas, seg_manha) is True
