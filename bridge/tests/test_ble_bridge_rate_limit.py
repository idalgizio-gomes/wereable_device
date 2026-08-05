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
def bridge():
    # Isolamento da BD já é feito globalmente por conftest.py
    # (DATABASE_URL=sqlite:///:memory:, forçado antes do primeiro import
    # de storage_advanced).
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
    assert bridge.orm.get_retention_days() == 10


def test_rate_limit_resets_after_interval(bridge, monkeypatch):
    ws = FakeWebSocket()
    fake_now = [1000.0]
    monkeypatch.setattr(ble_bridge.time, "monotonic", lambda: fake_now[0])
    asyncio.run(bridge.send_command(ws, "reset_readings"))
    fake_now[0] += ble_bridge.BleBridge.WRITE_COMMAND_MIN_INTERVAL_S + 0.01
    asyncio.run(bridge.send_command(ws, "reset_readings"))
    assert [m["ok"] for m in ws.sent] == [True, True]
    assert len(bridge.current_client.writes) == 2


class TestVersionamentoDoModeloComandosWS:
    """Comandos "list_model_versions"/"activate_model_version" (2026-08-05,
    ver storage_advanced.py::MlModelVersion e
    activity_inference.py::reload_active_model). A fixture `bridge`
    (topo do ficheiro) já constrói um BleBridge() real, cuja
    OrmPersistence.__init__ chama sa.create_all_tables() -- por isso a
    tabela ml_model_versions já existe e activity_inference.py já
    auto-registou/ativou a versão "1" antes de cada teste começar."""

    def test_list_model_versions_devolve_versoes_registadas(self, bridge):
        ws = FakeWebSocket()
        asyncio.run(bridge.handle_dashboard_command(
            ws, json.dumps({"cmd": "list_model_versions"})
        ))
        results = [m for m in ws.sent if m["kind"] == "model_versions"]
        assert len(results) == 1
        versions = results[0]["versions"]
        assert len(versions) >= 1
        assert any(v["version"] == "1" and v["is_active"] for v in versions)

    def test_activate_model_version_com_sucesso_muda_versao_ativa_e_recarrega(self, bridge):
        ws = FakeWebSocket()
        # Regista uma 2ª versão apontando para o MESMO ficheiro físico já
        # carregado -- confirma o mecanismo de ativação/reload de ponta a
        # ponta via WebSocket, sem precisar de um 2º modelo .joblib real.
        db = ble_bridge.sa.get_db_session()
        try:
            ble_bridge.sa.register_model_version(
                db, ble_bridge.ML_MODEL_NAME, version="2",
                file_path=ble_bridge.activity_inference.DEFAULT_MODEL_FILE_PATH,
                labels_path=ble_bridge.activity_inference.DEFAULT_MODEL_LABELS_PATH,
                notes="versao de teste -- mesmo ficheiro fisico da versao 1",
            )
        finally:
            db.close()

        asyncio.run(bridge.handle_dashboard_command(
            ws, json.dumps({"cmd": "activate_model_version", "version": "2"})
        ))
        results = [m for m in ws.sent if m["kind"] == "model_version_result"]
        assert len(results) == 1
        assert results[0]["ok"] is True
        assert results[0]["version"] == "2"
        assert results[0]["reloaded"] is True

        db = ble_bridge.sa.get_db_session()
        try:
            active = ble_bridge.sa.get_active_model_version(db, ble_bridge.ML_MODEL_NAME)
        finally:
            db.close()
        assert active["version"] == "2"

    def test_activate_model_version_inexistente_devolve_ok_false_sem_rebentar(self, bridge):
        ws = FakeWebSocket()
        asyncio.run(bridge.handle_dashboard_command(
            ws, json.dumps({"cmd": "activate_model_version", "version": "nao-existe-999"})
        ))
        results = [m for m in ws.sent if m["kind"] == "model_version_result"]
        assert len(results) == 1
        assert results[0]["ok"] is False
        assert "error" in results[0] and results[0]["error"]

    def test_activate_model_version_looped_calls_are_rate_limited(self, bridge):
        ws = FakeWebSocket()
        for _ in range(20):
            asyncio.run(bridge.handle_dashboard_command(
                ws, json.dumps({"cmd": "activate_model_version", "version": "1"})
            ))
        results = [m for m in ws.sent if m["kind"] == "model_version_result"]
        assert len(results) == 20
        assert results[0]["ok"] is True
        assert all(r["ok"] is False for r in results[1:])
        assert all("limite de taxa" in r["error"] for r in results[1:])
