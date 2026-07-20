"""Testes para o pairing/bonding BLE explícito da Fase A de segurança
(ver SECURITY_STATUS.md BLE-001/002/004/006 e Ble.cpp, setPermission(...,
SECMODE_ENC_NO_MITM, ...)) — cobre BleBridge._ensure_paired
(bridge/ble_bridge.py), chamado em run_device_loop() logo a seguir a
"async with BleakClient(device) as client:".

Confirmado por leitura do código real do bleak 3.0.2 instalado neste
ambiente (backend WinRT, bleak/backends/winrt/client.py): BleakClient não
empareia sozinho ao ligar nem no primeiro acesso GATT no Windows — por
isso o bridge tem de chamar client.pair() explicitamente, e este teste
garante que essa chamada acontece e que qualquer falha degrada sem
derrubar o resto do bridge (mesmo padrão de orm_persistence.py/
notifications.py/activity_inference.py). Usa um FakeBleClient com pair()
simulado — nunca hardware real nem o backend WinRT verdadeiro.
"""
import asyncio

import pytest

import ble_bridge
import storage


class FakeBleClientPairOk:
    def __init__(self):
        self.pair_calls = 0

    async def pair(self):
        self.pair_calls += 1


class FakeBleClientPairFails:
    """Simula uma falha real de pairing (ex.: BleakError('Could not pair
    with device: ...') — ver bleak/backends/winrt/client.py, pair())."""

    def __init__(self, exc):
        self._exc = exc
        self.pair_calls = 0

    async def pair(self):
        self.pair_calls += 1
        raise self._exc


class FakeBleClientPairNotImplemented:
    """Simula o backend CoreBluetooth (macOS), onde pair() nao existe (ver
    bleak/backends/corebluetooth/client.py) — o bridge deve degradar sem
    rebentar mesmo neste backend, nao so' apanhar BleakError."""

    async def pair(self):
        raise NotImplementedError("pairing is not available on macOS")


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test_carewear_history.db")
    return ble_bridge.BleBridge()


class TestEnsurePaired:
    def test_pair_e_chamado_e_sucesso_nao_rebenta(self, bridge):
        client = FakeBleClientPairOk()
        asyncio.run(bridge._ensure_paired(client))
        assert client.pair_calls == 1

    def test_falha_de_pairing_degrada_sem_rebentar(self, bridge):
        from bleak.exc import BleakError
        client = FakeBleClientPairFails(
            BleakError("Could not pair with device: AUTH_FAILURE")
        )
        asyncio.run(bridge._ensure_paired(client))  # nao deve levantar
        assert client.pair_calls == 1

    def test_backend_sem_suporte_a_pair_degrada_sem_rebentar(self, bridge):
        client = FakeBleClientPairNotImplemented()
        asyncio.run(bridge._ensure_paired(client))  # nao deve levantar

    def test_erro_generico_inesperado_tambem_degrada(self, bridge):
        client = FakeBleClientPairFails(RuntimeError("timeout WinRT inesperado"))
        asyncio.run(bridge._ensure_paired(client))
        assert client.pair_calls == 1
