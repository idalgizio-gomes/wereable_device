#!/usr/bin/env python3
"""
ble_bridge.py — Ponte entre o wearable (BLE) e o dashboard web (WebSocket).

CONTEXTO
--------
O dashboard web (web/dashboard/index.html), quando corrido localmente num
browser, não consegue ligar-se diretamente ao dispositivo Bluetooth sem
depender da API "Web Bluetooth" (que só existe no Chrome/Edge e nunca vai
existir no Safari/iOS — limitação da Apple). Para funcionar em mais
browsers, e para já termos uma base reutilizável quando houver apps móveis
nativas, este script faz de intermediário:

    Wearable (BLE, GATT) <--> este script (Python, bleak) <--> WebSocket
                                                                    |
                                                          dashboard web (JS)

Este script:
  1. Procura e liga-se ao dispositivo BLE chamado "Wearable" (ver
     Bluefruit.setName("Wearable") em src/main.cpp).
  2. Se ainda estiver na fase de "provisioning" (à espera de hora — ver
     Ble::ensureTimeSync() em src/Ble/Ble.cpp), escreve automaticamente a
     hora atual (UTC) na characteristic padrão "Current Time" (0x2A2B).
     Isto substitui o que antes só era possível fazer manualmente pelo
     nRF Connect ou pelo bypass de depuração WAKE/SLEEP na porta série.
  3. Depois de o dispositivo entrar em "modo de dados", subscreve as
     notificações de dumpDataChar (registos de sensores fragmentados) e
     dumpStatusChar (estado da transmissão), e pede o início do streaming
     escrevendo 0x01 em dumpCtrlChar.
  4. Remonta os fragmentos de cada registo (FullPlain, 38 bytes) e reenvia
     cada registo já descodificado, em JSON, a todos os clientes WebSocket
     ligados a este script (por omissão, ws://localhost:8765).

IMPORTANTE — SEM CIFRA NESTA FASE
----------------------------------
Apesar de o dispositivo trocar e guardar uma chave AES, o registo
transmitido no "modo de dados" chama-se "FullPlain" no firmware
(src/Ble/Ble.cpp) porque ainda vai em texto simples — a cifra AES está
prevista mas ainda não implementada nesse caminho. Este script não faz
nem precisa de fazer decifra; se/quando a cifra for adicionada ao
firmware, este ficheiro terá de ser atualizado.

DEPENDÊNCIAS
-------------
    pip install bleak websockets

UTILIZAÇÃO
----------
    python ble_bridge.py
    # depois abrir web/dashboard/index.html num browser; a página tenta
    # ligar-se sozinha a ws://localhost:8765.
"""

from __future__ import annotations

import asyncio
import json
import struct
import time
from datetime import datetime, timezone
from typing import Optional

import websockets
from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic

# ============================================================
# IDENTIFICADORES BLE — têm de corresponder exatamente aos definidos
# em src/Ble/Ble.cpp. Se algum UUID mudar no firmware, tem de mudar aqui
# também.
# ============================================================
DEVICE_NAME = "Wearable"

UUID_CURRENT_TIME = "00002a2b-0000-1000-8000-00805f9b34fb"  # 0x2A2B padrão do Bluetooth SIG
UUID_DUMP_CTRL = "abcd1234-5678-1234-5678-abcdef200001"
UUID_DUMP_DATA = "abcd1234-5678-1234-5678-abcdef200002"
UUID_DUMP_STATUS = "abcd1234-5678-1234-5678-abcdef200003"

DUMP_CTRL_START = bytes([0x01])
DUMP_CTRL_STOP = bytes([0x02])
# Pede FC forcada (streaming durante N segundos) + SpO2 imediato num so
# comando (ver kDumpCtrlForceHr em Ble.cpp). Bytes 1-2: segundos, uint16
# little-endian.
DUMP_CTRL_FORCE_READING_SECONDS = 15
DUMP_CTRL_FORCE_READING = bytes([0x03]) + struct.pack("<H", DUMP_CTRL_FORCE_READING_SECONDS)
# Apaga os registos guardados no ring buffer do dispositivo (destrutivo,
# irreversivel — ver kDumpCtrlResetReadings em Ble.cpp). Nao apaga
# calibracao nem chave AES.
DUMP_CTRL_RESET_READINGS = bytes([0x04])

# Tamanho de um registo completo (FullPlain, ver Ble.cpp) e o layout dos
# seus campos, na mesma ordem em que o firmware os escreve. "<" = little-
# endian (nativo do Cortex-M4 do nRF52840); struct.calcsize confirma que
# bate certo com o static_assert(sizeof(FullPlain) == 38, ...) do firmware.
FULL_PLAIN_STRUCT = struct.Struct("<IffffffIBBhh")
assert FULL_PLAIN_STRUCT.size == 38, "FullPlain deve ter 38 bytes, igual ao firmware"

WS_HOST = "localhost"
WS_PORT = 8765


def decode_full_plain(raw: bytes) -> dict:
    """Descodifica os 38 bytes de um registo FullPlain para um dict Python.

    A ordem dos campos tem de corresponder exatamente à struct FullPlain
    em src/Ble/Ble.cpp: ts, ax, ay, az, gx, gy, gz, steps, ff, inact,
    spo2, hr.
    """
    ts, ax, ay, az, gx, gy, gz, steps, ff, inact, spo2, hr = (
        FULL_PLAIN_STRUCT.unpack(raw)
    )
    return {
        "ts": ts,
        "ax": ax, "ay": ay, "az": az,
        "gx": gx, "gy": gy, "gz": gz,
        "steps": steps,
        "freefall": bool(ff),
        "inactivity": bool(inact),
        # spo2/hr chegam como 0 quando não há leitura nova nesse instante
        # (ver storageTask em main.cpp) — o dashboard deve ignorar zeros.
        "spo2": spo2 if spo2 != 0 else None,
        "hr": hr if hr != 0 else None,
    }


def build_current_time_payload(dt: Optional[datetime] = None) -> bytes:
    """Constrói os 10 bytes esperados pela characteristic Current Time
    (0x2A2B), no mesmo formato que Ble::ctsToEpochUtc() descodifica em
    src/Ble/Ble.cpp: ano (uint16 little-endian), mes, dia, hora, min, seg,
    + 3 bytes finais (dia da semana/fracoes/motivo de ajuste) que o
    firmware ignora mas exige que estejam presentes (len == 10).
    """
    dt = dt or datetime.now(timezone.utc)
    payload = struct.pack(
        "<HBBBBB", dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second
    ) + bytes(3)  # dia_semana=0, fracoes256=0, motivo=0 -> total 10 bytes
    assert len(payload) == 10, "payload da hora tem de ter exatamente 10 bytes"
    return payload


class BleBridge:
    """Liga-se ao wearable, mantém-se ligado (com reconexão automática) e
    difunde os registos descodificados a todos os clientes WebSocket."""

    def __init__(self):
        self.ws_clients: set[websockets.ServerConnection] = set()
        self._pending_fragments: dict[int, dict] = {}
        self.connected_device_name: Optional[str] = None
        self.last_record_ts: Optional[int] = None
        # *** LIMITE DE TAXA PARA O DASHBOARD ***: o IMU produz ate ~52
        # registos/seg, mas a interface web nao precisa de redesenhar a
        # essa velocidade — e, em testes reais, enviar ao ritmo total
        # (~14 msgs/seg observadas ja fragmentadas/remontadas) causava
        # desconexoes repetidas da ligacao WebSocket no browser. Registos
        # "normais" (sem leitura nova de HR/SpO2) sao amostrados para no
        # maximo RECORD_BROADCAST_MIN_INTERVAL_S; registos com HR/SpO2
        # novos sao sempre enviados de imediato (sao raros e importantes).
        self._last_broadcast_monotonic = 0.0
        # Referencia ao cliente BLE atualmente ligado (ou None), para que
        # comandos vindos do dashboard (ver ws_handler) possam escrever em
        # dumpCtrlChar sem precisar de re-ligar. So e' valida enquanto
        # run_device_loop() estiver dentro do "async with BleakClient(...)".
        self.current_client: Optional[BleakClient] = None

    RECORD_BROADCAST_MIN_INTERVAL_S = 0.25  # no maximo ~4 atualizacoes/seg

    async def broadcast(self, payload: dict) -> None:
        if not self.ws_clients:
            return
        message = json.dumps(payload)
        # Envia a todos os clientes ligados; remove os que já desligaram.
        dead = set()
        for ws in self.ws_clients:
            try:
                await ws.send(message)
            except websockets.exceptions.ConnectionClosed:
                dead.add(ws)
        self.ws_clients -= dead

    def _on_dump_data(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        """Callback de notificação da characteristic dumpDataChar.

        Cada notificação é um fragmento (DumpDataPacket, 20 bytes, ver
        Ble.cpp): type, frag_idx, frag_total, chunk_len, rec_seq (uint32),
        chunk[12]. Um FullPlain (38 bytes) chega dividido em até 4
        fragmentos; aqui remontamos por rec_seq até termos todos os bytes.
        """
        if len(data) < 8:
            return
        _type, frag_idx, frag_total, chunk_len = data[0], data[1], data[2], data[3]
        rec_seq = struct.unpack_from("<I", data, 4)[0]
        chunk = bytes(data[8:8 + chunk_len])

        entry = self._pending_fragments.setdefault(
            rec_seq, {"total": frag_total, "parts": {}}
        )
        entry["parts"][frag_idx] = chunk

        if len(entry["parts"]) < entry["total"]:
            return  # ainda faltam fragmentos deste registo

        # Todos os fragmentos chegaram — remonta pela ordem correta.
        full = b"".join(entry["parts"][i] for i in range(entry["total"]))
        del self._pending_fragments[rec_seq]

        if len(full) != FULL_PLAIN_STRUCT.size:
            print(f"[BRIDGE] registo rec_seq={rec_seq} com tamanho inesperado "
                  f"({len(full)} bytes, esperado {FULL_PLAIN_STRUCT.size}) — ignorado")
            return

        record = decode_full_plain(full)
        self.last_record_ts = record["ts"]

        has_new_vital = record["hr"] is not None or record["spo2"] is not None
        now = time.monotonic()
        due = (now - self._last_broadcast_monotonic) >= self.RECORD_BROADCAST_MIN_INTERVAL_S
        if not (has_new_vital or due):
            return  # amostra "normal" enviada ha pouco tempo — poupa o browser
        self._last_broadcast_monotonic = now

        asyncio.create_task(self.broadcast({"kind": "record", "rec_seq": rec_seq, **record}))

    def _on_dump_status(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        """Callback de notificação da characteristic dumpStatusChar
        (DumpStatusPacket, 16 bytes): type, state, reason, reserved,
        seq, sent_records, acked_records — ver Ble.cpp para o significado
        de cada "reason".
        """
        if len(data) < 16:
            return
        _type, state, reason, _reserved, seq, sent, acked = struct.unpack_from(
            "<BBBBIII", data, 0
        )
        asyncio.create_task(self.broadcast({
            "kind": "status", "state": state, "reason": reason,
            "seq": seq, "sent_records": sent, "acked_records": acked,
        }))

    async def _maybe_send_time(self, client: BleakClient) -> None:
        """Se a characteristic Current Time existir e for escrevível
        (dispositivo ainda em provisioning, à espera de hora — ver
        Ble::ensureTimeSync()), escreve a hora UTC atual para desbloquear
        o arranque automaticamente, sem precisar do nRF Connect."""
        services = client.services
        char = services.get_characteristic(UUID_CURRENT_TIME) if services else None
        if char is None:
            return
        try:
            await client.write_gatt_char(char, build_current_time_payload())
            print("[BRIDGE] hora atual (UTC) enviada via Current Time (0x2A2B)")
        except Exception as exc:  # noqa: BLE001 - so' um passo best-effort
            print(f"[BRIDGE] nao foi possivel escrever a hora (normal se ja sincronizada): {exc}")

    async def run_device_loop(self) -> None:
        """Ciclo principal: procura o dispositivo, liga-se, mantém a
        ligação, e volta a tentar automaticamente se cair."""
        while True:
            print(f"[BRIDGE] a procurar dispositivo \"{DEVICE_NAME}\"...")
            device = await BleakScanner.find_device_by_filter(
                lambda d, adv: d.name == DEVICE_NAME or (adv.local_name == DEVICE_NAME),
                timeout=15.0,
            )
            if device is None:
                print(f"[BRIDGE] \"{DEVICE_NAME}\" nao encontrado — a tentar novamente em 5s")
                await asyncio.sleep(5)
                continue

            print(f"[BRIDGE] encontrado {device.address} — a ligar...")
            try:
                async with BleakClient(device) as client:
                    self.connected_device_name = DEVICE_NAME
                    self.current_client = client
                    await self.broadcast({"kind": "device_status", "connected": True})

                    await self._maybe_send_time(client)

                    # Subscreve notificacoes de dados e de estado.
                    await client.start_notify(UUID_DUMP_DATA, self._on_dump_data)
                    await client.start_notify(UUID_DUMP_STATUS, self._on_dump_status)

                    # Pede explicitamente o inicio do streaming (o
                    # firmware so aceita este comando em modo de dados —
                    # ver dumpCtrlCallback em Ble.cpp).
                    try:
                        await client.write_gatt_char(UUID_DUMP_CTRL, DUMP_CTRL_START, response=False)
                        print("[BRIDGE] pedido de start enviado (dumpCtrlChar)")
                    except Exception as exc:  # noqa: BLE001
                        print(f"[BRIDGE] nao foi possivel pedir start agora "
                              f"(normal se ainda em provisioning): {exc}")

                    print("[BRIDGE] ligado e a receber dados. Ctrl+C para parar.")
                    # Mantem a ligacao viva ate ela cair sozinha.
                    while client.is_connected:
                        await asyncio.sleep(1)

            except Exception as exc:  # noqa: BLE001 - queremos reconectar em qualquer erro
                print(f"[BRIDGE] ligacao perdida/erro: {exc}")

            self.connected_device_name = None
            self.current_client = None
            await self.broadcast({"kind": "device_status", "connected": False})
            print("[BRIDGE] desligado — a tentar reconectar em 3s")
            await asyncio.sleep(3)

    async def send_command(self, ws, name: str) -> None:
        """Escreve um comando em dumpCtrlChar, pedido pelo dashboard
        (ver handle_dashboard_command). Responde ao mesmo cliente WS com
        o resultado, para a interface poder mostrar sucesso/erro."""
        client = self.current_client
        if client is None or not client.is_connected:
            await ws.send(json.dumps({"kind": "command_result", "cmd": name, "ok": False, "error": "dispositivo nao ligado"}))
            return

        payload_by_name = {
            "force_reading": DUMP_CTRL_FORCE_READING,
            "reset_readings": DUMP_CTRL_RESET_READINGS,
        }
        payload = payload_by_name.get(name)
        if payload is None:
            await ws.send(json.dumps({"kind": "command_result", "cmd": name, "ok": False, "error": "comando desconhecido"}))
            return

        try:
            await client.write_gatt_char(UUID_DUMP_CTRL, payload, response=False)
            print(f"[BRIDGE] comando do dashboard enviado: {name}")
            await ws.send(json.dumps({"kind": "command_result", "cmd": name, "ok": True}))
        except Exception as exc:  # noqa: BLE001
            print(f"[BRIDGE] falha a enviar comando {name}: {exc}")
            await ws.send(json.dumps({"kind": "command_result", "cmd": name, "ok": False, "error": str(exc)}))

    async def handle_dashboard_command(self, ws, raw_message: str) -> None:
        """Descodifica uma mensagem JSON vinda do dashboard (ex.:
        {"cmd":"force_reading"}) e traduz para uma escrita BLE. Comandos
        desconhecidos ou mal formados sao ignorados silenciosamente —
        este canal nao e' autenticado, pelo que so deve ser exposto em
        localhost (ver README do bridge)."""
        try:
            msg = json.loads(raw_message)
        except (ValueError, TypeError):
            return
        cmd = msg.get("cmd") if isinstance(msg, dict) else None
        if cmd in ("force_reading", "reset_readings"):
            await self.send_command(ws, cmd)

    async def ws_handler(self, ws: "websockets.ServerConnection") -> None:
        self.ws_clients.add(ws)
        print(f"[BRIDGE] dashboard ligado via WebSocket ({len(self.ws_clients)} ativo(s))")
        await ws.send(json.dumps({
            "kind": "device_status",
            "connected": self.connected_device_name is not None,
        }))
        try:
            async for raw_message in ws:
                await self.handle_dashboard_command(ws, raw_message)
        finally:
            self.ws_clients.discard(ws)
            print(f"[BRIDGE] dashboard desligado ({len(self.ws_clients)} ativo(s))")


async def main() -> None:
    bridge = BleBridge()
    server = await websockets.serve(bridge.ws_handler, WS_HOST, WS_PORT)
    print(f"[BRIDGE] WebSocket a ouvir em ws://{WS_HOST}:{WS_PORT}")
    async with server:
        await bridge.run_device_loop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[BRIDGE] terminado pelo utilizador")
