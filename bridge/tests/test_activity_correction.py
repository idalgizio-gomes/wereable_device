"""Testes para o cmd "correct_activity" (bridge/ble_bridge.py,
`handle_dashboard_command`) — pedido do utilizador 2026-07-22: "falta o
botão para contradizer o que a ia acredita que o utente está a fazer".

Corre inteiramente contra storage_advanced.py em SQLite em memória (ver
conftest.py, que força DATABASE_URL=sqlite:///:memory: antes do primeiro
import de storage_advanced), sem hardware nem rede real.
"""
import asyncio
import json

import pytest

import ble_bridge
import storage_advanced as sa


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(json.loads(message))


@pytest.fixture(autouse=True)
def _fresh_schema():
    """Recria o schema do zero antes/depois de cada teste (isolamento) —
    a BD SQLite em memória (StaticPool, ver conftest.py) é partilhada por
    todos os testes do módulo, sem isto uma correção gravada num teste
    continuaria visível no seguinte."""
    sa.Base.metadata.drop_all(bind=sa.engine)
    sa.Base.metadata.create_all(bind=sa.engine)
    yield
    sa.Base.metadata.drop_all(bind=sa.engine)


@pytest.fixture
def bridge():
    return ble_bridge.BleBridge()


def _corrections_in_db(bridge):
    session = sa.get_db_session()
    try:
        rows = (
            session.query(sa.ActivityCorrection)
            .filter_by(device_id=bridge.orm.device_id)
            .order_by(sa.ActivityCorrection.id)
            .all()
        )
        return [(r.original_category, r.corrected_category) for r in rows]
    finally:
        session.close()


def test_correct_activity_valid_category_persists_and_broadcasts(bridge):
    ws = FakeWebSocket()

    async def run():
        bridge.ws_clients.add(ws)
        await bridge.handle_dashboard_command(ws, json.dumps({"cmd": "correct_activity", "category": "Dormir"}))
        await asyncio.sleep(0.05)  # deixa o broadcast (asyncio.create_task) correr

    asyncio.run(run())

    assert _corrections_in_db(bridge) == [(None, "Dormir")]
    corrections = [m for m in ws.sent if m.get("kind") == "activity_correction"]
    assert len(corrections) == 1
    assert corrections[0]["category"] == "Dormir"
    assert corrections[0]["original_category"] is None


def test_correct_activity_unknown_category_rejected(bridge):
    ws = FakeWebSocket()
    asyncio.run(bridge.handle_dashboard_command(ws, json.dumps({"cmd": "correct_activity", "category": "Dançar"})))

    assert _corrections_in_db(bridge) == []
    results = [m for m in ws.sent if m.get("kind") == "command_result"]
    assert results == [{"kind": "command_result", "cmd": "correct_activity", "ok": False, "error": "categoria desconhecida"}]


def test_correct_activity_missing_category_rejected(bridge):
    ws = FakeWebSocket()
    asyncio.run(bridge.handle_dashboard_command(ws, json.dumps({"cmd": "correct_activity"})))

    assert _corrections_in_db(bridge) == []
    results = [m for m in ws.sent if m.get("kind") == "command_result"]
    assert results[0]["ok"] is False


def test_correct_activity_looped_calls_are_rate_limited(bridge):
    """Mesmo vetor já coberto para reset_readings/set_retention_days (ver
    test_ble_bridge_rate_limit.py, SECURITY_STATUS.md API-001) — canal sem
    autenticação, um cliente em loop apertado não pode escrever sem limite."""
    ws = FakeWebSocket()

    async def run():
        for _ in range(20):
            await bridge.handle_dashboard_command(ws, json.dumps({"cmd": "correct_activity", "category": "Dormir"}))
        await asyncio.sleep(0.05)

    asyncio.run(run())

    assert len(_corrections_in_db(bridge)) == 1
    results = [m for m in ws.sent if m.get("kind") == "command_result"]
    assert [r["ok"] for r in results] == [False] * 19
    assert all("limite de taxa" in r["error"] for r in results)
