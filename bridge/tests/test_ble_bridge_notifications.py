"""Testes para a integração de notifications.py (SMS/email de emergência +
EscalationManager) em ble_bridge.py — ver `_on_emergency_alert`,
`_dispatch_emergency_notifications`, `_load_notification_recipients_from_env`
e o comando de dashboard "acknowledge_alert".

Cobre especificamente:
  - um alerta de emergência real (notificação BLE de emergencyAlertChar)
    aciona o EscalationManager.notify_emergency com os destinatários
    carregados do ambiente;
  - uma falha nas notificações (exceção dentro de notify_emergency) NUNCA
    impede o broadcast do alerta ao dashboard via WebSocket — esse é o
    caminho crítico de segurança, notifications.py é só um extra;
  - o carregador de configuração por variáveis de ambiente
    (cuidador/contacto de emergência/horário);
  - o comando "acknowledge_alert" do dashboard cancela o escalonamento
    pendente via EscalationManager.acknowledge.

Nenhum destes testes contacta a Twilio/SendGrid reais nem hardware BLE
real: usa FakeWebSocket/FakeBleClient (mesmo padrão de
test_ble_bridge_rate_limit.py) e substitui `notifications.send_sms`/
`send_email` por mocks quando necessário.
"""
import asyncio
import json
import struct
from unittest.mock import MagicMock, patch

import pytest

import ble_bridge
import notifications
import storage


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(json.loads(message))


class FakeBleClient:
    """Simula um BleakClient já ligado, sem hardware real."""

    def __init__(self):
        self.is_connected = True
        self.writes = []

    async def write_gatt_char(self, uuid, payload, response=False):
        self.writes.append((uuid, payload))


def _emergency_alert_bytes(alert_type=1, seq=42, timestamp_utc=1_700_000_000):
    """Constrói os 8 bytes de EmergencyAlertPacket (ver EMERGENCY_ALERT_STRUCT
    em ble_bridge.py): type, reserved, seq, timestamp_utc."""
    return struct.pack("<BBHI", alert_type, 0, seq, timestamp_utc)


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test_carewear_history.db")
    b = ble_bridge.BleBridge()
    b.current_client = FakeBleClient()
    return b


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "CAREWEAR_TWILIO_ACCOUNT_SID",
        "CAREWEAR_TWILIO_AUTH_TOKEN",
        "CAREWEAR_TWILIO_FROM_NUMBER",
        "CAREWEAR_SENDGRID_API_KEY",
        "CAREWEAR_NOTIFY_FROM_EMAIL",
        "CAREWEAR_ESCALATION_TIMEOUT_MIN",
        "CAREWEAR_CAREGIVER_NAME",
        "CAREWEAR_CAREGIVER_PHONE",
        "CAREWEAR_CAREGIVER_EMAIL",
        "CAREWEAR_EMERGENCY_CONTACT_NAME",
        "CAREWEAR_EMERGENCY_CONTACT_PHONE",
        "CAREWEAR_EMERGENCY_CONTACT_EMAIL",
        "CAREWEAR_CAREGIVER_SCHEDULE_JSON",
    ):
        monkeypatch.delenv(key, raising=False)


class TestCarregadorDeConfiguracao:
    def test_sem_variaveis_devolve_vazio(self):
        caregivers, emergency_contact, schedule = ble_bridge._load_notification_recipients_from_env()
        assert caregivers == []
        assert emergency_contact is None
        assert schedule is None

    def test_carrega_cuidador_e_contacto_de_emergencia(self, monkeypatch):
        monkeypatch.setenv("CAREWEAR_CAREGIVER_NAME", "Filha")
        monkeypatch.setenv("CAREWEAR_CAREGIVER_PHONE", "+351933333333")
        monkeypatch.setenv("CAREWEAR_CAREGIVER_EMAIL", "filha@example.com")
        monkeypatch.setenv("CAREWEAR_EMERGENCY_CONTACT_NAME", "Vizinho")
        monkeypatch.setenv("CAREWEAR_EMERGENCY_CONTACT_PHONE", "+351944444444")

        caregivers, emergency_contact, schedule = ble_bridge._load_notification_recipients_from_env()

        assert len(caregivers) == 1
        assert caregivers[0] == notifications.EmergencyContact(
            name="Filha", phone="+351933333333", email="filha@example.com"
        )
        assert emergency_contact == notifications.EmergencyContact(name="Vizinho", phone="+351944444444")
        assert schedule is None

    def test_carrega_horario_valido(self, monkeypatch):
        monkeypatch.setenv(
            "CAREWEAR_CAREGIVER_SCHEDULE_JSON",
            '[{"weekday": 0, "start": "08:00", "end": "17:00"}]',
        )
        _, _, schedule = ble_bridge._load_notification_recipients_from_env()
        assert schedule == [notifications.ScheduleWindow(
            weekday=0, start=notifications.dt_time(8, 0), end=notifications.dt_time(17, 0)
        )]

    def test_horario_invalido_degrada_para_none_sem_rebentar(self, monkeypatch):
        monkeypatch.setenv("CAREWEAR_CAREGIVER_SCHEDULE_JSON", "isto nao e' json valido")
        _, _, schedule = ble_bridge._load_notification_recipients_from_env()
        assert schedule is None


class TestEmergencyAlertAcionaNotificacoes:
    def _bridge_with_recipients(self, bridge):
        bridge.escalation_manager = notifications.EscalationManager(escalation_timeout_minutes=0)
        bridge.notify_caregivers = [
            notifications.EmergencyContact(name="Filha", phone="+351933333333")
        ]
        bridge.notify_emergency_contact = notifications.EmergencyContact(
            name="Vizinho", phone="+351944444444"
        )
        return bridge

    def test_alerta_real_aciona_notify_emergency(self, bridge):
        bridge = self._bridge_with_recipients(bridge)

        with patch("notifications.send_sms", return_value=True) as mock_sms:
            async def run():
                bridge._on_emergency_alert(None, bytearray(_emergency_alert_bytes(alert_type=1, seq=42)))
                await asyncio.sleep(0.05)  # deixa a task de notificacao correr

            asyncio.run(run())

        assert mock_sms.call_count == 2
        called_numbers = {c.args[0] for c in mock_sms.call_args_list}
        assert called_numbers == {"+351933333333", "+351944444444"}

    def test_falha_nas_notificacoes_nao_impede_broadcast_ao_dashboard(self, bridge):
        """Vetor concreto pedido: mesmo que notify_emergency() rebente com
        uma exceção inesperada, o alerta continua a chegar ao dashboard via
        WebSocket — o broadcast é o caminho crítico de segurança, as
        notificações externas são um extra tolerante a falhas."""
        bridge = self._bridge_with_recipients(bridge)
        ws = FakeWebSocket()

        async def run():
            bridge.ws_clients.add(ws)
            with patch.object(
                bridge.escalation_manager, "notify_emergency",
                side_effect=RuntimeError("Twilio explodiu"),
            ):
                bridge._on_emergency_alert(None, bytearray(_emergency_alert_bytes(alert_type=2, seq=7)))
                await asyncio.sleep(0.05)

        asyncio.run(run())

        alert_messages = [m for m in ws.sent if m.get("kind") == "emergency_alert"]
        assert len(alert_messages) == 1
        assert alert_messages[0]["alert_name"] == "fall_inactivity"
        assert alert_messages[0]["seq"] == 7

    def test_sem_escalation_manager_nao_notifica_nem_rebenta(self, bridge):
        """Se notifications.py estiver indisponível (import falhou) ou o
        EscalationManager não tiver sido construído, o alerta continua a
        ser processado e difundido normalmente — só não notifica ninguém."""
        bridge.escalation_manager = None
        ws = FakeWebSocket()

        async def run():
            bridge.ws_clients.add(ws)
            bridge._on_emergency_alert(None, bytearray(_emergency_alert_bytes(alert_type=1, seq=1)))
            await asyncio.sleep(0.05)

        asyncio.run(run())

        assert any(m.get("kind") == "emergency_alert" for m in ws.sent)

    @pytest.mark.asyncio
    async def test_escalona_ao_contacto_de_emergencia_se_nao_confirmado_e_cuidador_indisponivel(self, bridge):
        """Fim-a-fim: alerta real -> notificação imediata -> (cuidador
        indisponível no horário declarado) -> escalonamento automático ao
        contacto de emergência se não confirmado a tempo. NUNCA contacta o
        112/serviços de emergência reais — só SMS ao contacto humano
        declarado (ver notifications.py, "DECISÃO DELIBERADA SOBRE O 112")."""
        from datetime import datetime, time as dt_time
        now = datetime.now()
        bridge.escalation_manager = notifications.EscalationManager(escalation_timeout_minutes=0)
        bridge.escalation_manager.escalation_timeout_minutes = 0.001 / 60  # quase instantaneo
        bridge.notify_caregivers = [notifications.EmergencyContact(name="Filha", phone="+351933333333")]
        bridge.notify_emergency_contact = notifications.EmergencyContact(name="Vizinho", phone="+351944444444")
        bridge.notify_schedule = [notifications.ScheduleWindow(
            weekday=now.weekday(), start=dt_time(0, 0), end=dt_time(23, 59, 59)
        )]

        with patch("notifications.send_sms", return_value=True) as mock_sms:
            bridge._on_emergency_alert(None, bytearray(_emergency_alert_bytes(alert_type=1, seq=99)))
            await asyncio.sleep(0.2)

        numbers_called = [c.args[0] for c in mock_sms.call_args_list]
        # cuidador + contacto (notificacao imediata) + contacto (escalonamento)
        assert numbers_called.count("+351944444444") == 2
        assert numbers_called.count("+351933333333") == 1
        # nenhuma chamada a um numero de emergencia real (112) foi feita —
        # so' contactos humanos declarados pelo utilizador.
        assert "112" not in numbers_called


class TestAcknowledgeAlertCommand:
    def test_acknowledge_cancela_escalonamento_pendente(self, bridge):
        bridge.escalation_manager = notifications.EscalationManager(escalation_timeout_minutes=10)

        async def run():
            # Task real cancelavel em "_pending", para exercitar o mesmo
            # caminho que notify_emergency usaria (ver EscalationManager
            # em notifications.py).
            async def _never():
                await asyncio.sleep(999)
            bridge.escalation_manager._pending["1-42"] = asyncio.create_task(_never())

            ws = FakeWebSocket()
            await bridge.handle_dashboard_command(ws, json.dumps({"cmd": "acknowledge_alert", "alert_id": "1-42"}))
            assert ws.sent == [{"kind": "command_result", "cmd": "acknowledge_alert", "ok": True, "was_pending": True}]
            assert bridge.escalation_manager.pending_count() == 0

        asyncio.run(run())

    def test_acknowledge_sem_escalation_manager(self, bridge):
        bridge.escalation_manager = None
        ws = FakeWebSocket()
        asyncio.run(bridge.handle_dashboard_command(ws, json.dumps({"cmd": "acknowledge_alert", "alert_id": "1-1"})))
        assert ws.sent[0]["ok"] is False

    def test_acknowledge_sem_alert_id(self, bridge):
        bridge.escalation_manager = notifications.EscalationManager()
        ws = FakeWebSocket()
        asyncio.run(bridge.handle_dashboard_command(ws, json.dumps({"cmd": "acknowledge_alert"})))
        assert ws.sent[0]["ok"] is False
