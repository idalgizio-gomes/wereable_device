"""Testes de 'baseline comportamental personalizada' (2026-08-05) do lado
do bridge (não da regra pura, já coberta em test_vital_alerts.py):

  (a) BleBridge._maybe_broadcast_vital_alert — só difunde "vital_alert" em
      MUDANÇA de estado (entrar/sair de alerta), nunca a cada leitura;
  (b) comandos WS "get_thresholds"/"set_thresholds" (handle_dashboard_command).

Corre inteiramente contra SQLite em memória (ver conftest.py), sem
hardware BLE nem rede real — mesmo padrão de test_ble_bridge_rate_limit.py.
"""
import asyncio
import json

import pytest

import ble_bridge


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(json.loads(message))


class FakeBleClient:
    def __init__(self):
        self.is_connected = True
        self.writes = []

    async def write_gatt_char(self, uuid, payload, response=False):
        self.writes.append((uuid, payload))


@pytest.fixture
def bridge():
    b = ble_bridge.BleBridge()
    b.current_client = FakeBleClient()
    return b


def _vital_messages(ws, vital=None):
    msgs = [m for m in ws.sent if m.get("kind") == "vital_alert"]
    return [m for m in msgs if vital is None or m["vital"] == vital] if vital else msgs


class TestMaybeBroadcastVitalAlert:
    def test_first_alert_broadcasts_once(self, bridge):
        ws = FakeWebSocket()
        bridge.ws_clients.add(ws)

        async def run():
            bridge._maybe_broadcast_vital_alert(
                "hr", {"vital": "hr", "level": "high", "value": 130, "limit": 100}, {}
            )
            await asyncio.sleep(0.02)

        asyncio.run(run())

        msgs = _vital_messages(ws, "hr")
        assert len(msgs) == 1
        assert msgs[0]["cleared"] is False
        assert msgs[0]["level"] == "high"
        assert "explanation" in msgs[0]

    def test_repeated_same_alert_does_not_rebroadcast(self, bridge):
        """Vetor concreto: uma FC persistentemente alta chega a cada ~30s
        (cadência do PPG) — sem o debounce por estado, cada leitura gerava
        uma mensagem WS nova, inundando o dashboard sem informação nova."""
        ws = FakeWebSocket()
        bridge.ws_clients.add(ws)
        alert = {"vital": "hr", "level": "high", "value": 130, "limit": 100}

        async def run():
            for _ in range(5):
                bridge._maybe_broadcast_vital_alert("hr", alert, {})
            await asyncio.sleep(0.02)

        asyncio.run(run())

        assert len(_vital_messages(ws, "hr")) == 1

    def test_clearing_after_alert_broadcasts_cleared_true(self, bridge):
        ws = FakeWebSocket()
        bridge.ws_clients.add(ws)
        alert = {"vital": "hr", "level": "high", "value": 130, "limit": 100}

        async def run():
            bridge._maybe_broadcast_vital_alert("hr", alert, {"heart_rate_max": 100})
            bridge._maybe_broadcast_vital_alert("hr", None, {"heart_rate_max": 100})
            await asyncio.sleep(0.02)

        asyncio.run(run())

        msgs = _vital_messages(ws, "hr")
        assert len(msgs) == 2
        assert msgs[0]["cleared"] is False
        assert msgs[1]["cleared"] is True
        assert "explanation" in msgs[1]

    def test_never_in_alert_and_clear_never_broadcasts(self, bridge):
        """Nunca esteve em alerta -> nada para 'limpar'. Uma leitura normal
        não deve gerar mensagem nenhuma."""
        ws = FakeWebSocket()
        bridge.ws_clients.add(ws)

        async def run():
            bridge._maybe_broadcast_vital_alert("hr", None, {})
            await asyncio.sleep(0.02)

        asyncio.run(run())

        assert _vital_messages(ws, "hr") == []

    def test_hr_and_spo2_states_are_independent(self, bridge):
        ws = FakeWebSocket()
        bridge.ws_clients.add(ws)
        hr_alert = {"vital": "hr", "level": "high", "value": 130, "limit": 100}
        spo2_alert = {"vital": "spo2", "level": "low", "value": 85, "limit": 92}

        async def run():
            bridge._maybe_broadcast_vital_alert("hr", hr_alert, {})
            bridge._maybe_broadcast_vital_alert("spo2", spo2_alert, {})
            await asyncio.sleep(0.02)

        asyncio.run(run())

        assert len(_vital_messages(ws, "hr")) == 1
        assert len(_vital_messages(ws, "spo2")) == 1

    def test_transition_from_low_to_high_rebroadcasts(self, bridge):
        """Mudar de nível ('low' -> 'high') É uma mudança de estado real,
        mesmo que ambos sejam 'alerta' — tem de voltar a avisar."""
        ws = FakeWebSocket()
        bridge.ws_clients.add(ws)

        async def run():
            bridge._maybe_broadcast_vital_alert("hr", {"vital": "hr", "level": "low", "value": 40, "limit": 50}, {})
            bridge._maybe_broadcast_vital_alert("hr", {"vital": "hr", "level": "high", "value": 130, "limit": 100}, {})
            await asyncio.sleep(0.02)

        asyncio.run(run())

        msgs = _vital_messages(ws, "hr")
        assert len(msgs) == 2
        assert msgs[0]["level"] == "low"
        assert msgs[1]["level"] == "high"


class TestComandosWsDeLimiares:
    def test_get_thresholds_returns_defaults_for_new_patient(self, bridge):
        ws = FakeWebSocket()
        asyncio.run(bridge.handle_dashboard_command(ws, json.dumps({"cmd": "get_thresholds"})))

        msgs = [m for m in ws.sent if m["kind"] == "thresholds"]
        assert len(msgs) == 1
        assert msgs[0]["thresholds"]["is_default"] is True
        assert msgs[0]["thresholds"]["heart_rate_min"] == 50

    def test_set_thresholds_then_get_reflects_change(self, bridge):
        ws = FakeWebSocket()
        asyncio.run(bridge.handle_dashboard_command(
            ws, json.dumps({"cmd": "set_thresholds", "heart_rate_max": 115})
        ))
        asyncio.run(bridge.handle_dashboard_command(ws, json.dumps({"cmd": "get_thresholds"})))

        save_result = [m for m in ws.sent if m["kind"] == "thresholds_result"][0]
        assert save_result["ok"] is True
        assert save_result["thresholds"]["heart_rate_max"] == 115

        final = [m for m in ws.sent if m["kind"] == "thresholds"][0]
        assert final["thresholds"]["heart_rate_max"] == 115
        assert final["thresholds"]["is_default"] is False

    def test_set_thresholds_invalid_value_returns_error_not_exception(self, bridge):
        ws = FakeWebSocket()
        asyncio.run(bridge.handle_dashboard_command(
            ws, json.dumps({"cmd": "set_thresholds", "heart_rate_max": 5})
        ))
        result = [m for m in ws.sent if m["kind"] == "thresholds_result"][0]
        assert result["ok"] is False
        assert "error" in result

    def test_set_thresholds_looped_calls_are_rate_limited(self, bridge):
        ws = FakeWebSocket()
        for _ in range(20):
            asyncio.run(bridge.handle_dashboard_command(
                ws, json.dumps({"cmd": "set_thresholds", "heart_rate_max": 110})
            ))
        results = [m for m in ws.sent if m["kind"] == "thresholds_result"]
        oks = [r["ok"] for r in results]
        assert oks[0] is True
        assert False in oks[1:]
        assert any("limite de taxa" in r.get("error", "") for r in results if not r["ok"])
