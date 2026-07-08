"""Testes para o limite de taxa de comandos de escrita do dashboard
(bridge/ble_bridge.py, `_check_write_rate_limit`/`send_command`/
`handle_dashboard_command` "set_retention_days") — ver SECURITY_STATUS.md,
risco API-001: o WebSocket não autenticado aceitava `{"cmd":"reset_readings"}`
em loop sem qualquer limite, apagando repetidamente o histórico do
dispositivo (comando destrutivo/irreversível).

Corre inteiramente contra uma base de dados SQLite temporária (nunca a
`carewear_history.db` real de desenvolvimento — ver fixture `bridge`
abaixo) e um cliente BLE + WebSocket falsos, sem hardware nem rede real.
"""
import asyncio
import json

import pytest

import ble_bridge
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


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test_carewear_history.db")
    b = ble_bridge.BleBridge()
    b.current_client = FakeBleClient()
    return b


def test_reset_readings_first_call_succeeds(bridge):
    ws = FakeWebSocket()
    asyncio.run(bridge.send_command(ws, "reset_readings"))
    assert ws.sent == [{"kind": "command_result", "cmd": "reset_readings", "ok": True}]
    assert len(bridge.current_client.writes) == 1


def test_reset_readings_looped_calls_are_rate_limited(bridge):
    """Vetor concreto: um cliente WebSocket (canal sem autenticação, ver
    handle_dashboard_command) a enviar {"cmd":"reset_readings"} em loop
    apertado. Antes desta correção, cada mensagem produzia de imediato uma
    escrita BLE destrutiva — aqui só a primeira das 20 tentativas deve
    chegar a `write_gatt_char`."""
    ws = FakeWebSocket()
    for _ in range(20):
        asyncio.run(bridge.send_command(ws, "reset_readings"))
    assert len(bridge.current_client.writes) == 1
    oks = [m["ok"] for m in ws.sent]
    assert oks == [True] + [False] * 19
    assert all("limite de taxa" in m["error"] for m in ws.sent[1:])


def test_force_reading_and_reset_readings_have_independent_limits(bridge):
    ws = FakeWebSocket()
    asyncio.run(bridge.send_command(ws, "reset_readings"))
    asyncio.run(bridge.send_command(ws, "force_reading"))
    assert [m["ok"] for m in ws.sent] == [True, True]
    assert len(bridge.current_client.writes) == 2


def test_set_retention_days_looped_calls_are_rate_limited(bridge):
    ws = FakeWebSocket()
    asyncio.run(bridge.handle_dashboard_command(
        ws, json.dumps({"cmd": "set_retention_days", "days": 10})
    ))
    asyncio.run(bridge.handle_dashboard_command(
        ws, json.dumps({"cmd": "set_retention_days", "days": 20})
    ))
    results = [m for m in ws.sent if m["kind"] == "retention_days_result"]
    assert results[0]["ok"] is True and results[0]["days"] == 10
    assert results[1]["ok"] is False
    assert "limite de taxa" in results[1]["error"]
    # O segundo pedido (bloqueado) não deve ter alterado o valor persistido.
    assert storage.get_retention_days(bridge.db) == 10


def test_rate_limit_resets_after_interval(bridge, monkeypatch):
    ws = FakeWebSocket()
    fake_now = [1000.0]
    monkeypatch.setattr(ble_bridge.time, "monotonic", lambda: fake_now[0])
    asyncio.run(bridge.send_command(ws, "reset_readings"))
    fake_now[0] += ble_bridge.BleBridge.WRITE_COMMAND_MIN_INTERVAL_S + 0.01
    asyncio.run(bridge.send_command(ws, "reset_readings"))
    assert [m["ok"] for m in ws.sent] == [True, True]
    assert len(bridge.current_client.writes) == 2
