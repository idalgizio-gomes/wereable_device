#!/usr/bin/env python3
"""
ble_bridge.py — ponte entre o wearable (BLE) e o dashboard web (WebSocket).

Liga-se ao dispositivo BLE "Wearable", sincroniza a hora (characteristic
0x2A2B) se ainda estiver em provisioning, depois subscreve dumpDataChar/
dumpStatusChar e pede o streaming (0x01 em dumpCtrlChar). Remonta os
fragmentos FullPlain (39 bytes) e reenvia cada registo em JSON via
WebSocket (ws://localhost:8765). Também subscreve emergencyAlertChar e
reencaminha alertas de imediato ao dashboard, disparando notifications.py
(SMS/email + escalonamento) quando as credenciais Twilio/SendGrid e os
contactos CAREWEAR_CAREGIVER_*/CAREWEAR_EMERGENCY_CONTACT_* estão
configurados — nunca contacta o 112 (ver notifications.py).

Cifra: desde 2026-07-07 cada FullPlain vai em AES-CTR (nonce de 32 bits
por registo, ver encryptRecord()/allocateNonce() em src/Ble/Ble.cpp). O
bridge decifra com a mesma chave, passada em CAREWEAR_AES_KEY_HEX (hex de
16/24/32 bytes); sem ela, descarta os registos em vez de os interpretar
como texto simples. Não é troca de chaves segura, só o suficiente para o
protótipo local; não testado com hardware real.

    export CAREWEAR_AES_KEY_HEX=<hex de 16/24/32 bytes>
    pip install bleak websockets pycryptodome
    python ble_bridge.py
    # abrir web/dashboard/index.html; liga-se sozinho a ws://localhost:8765
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import struct
import time
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4  # chave publica de cada alerta persistido

import websockets
from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from Crypto.Cipher import AES

try:
    # storage_advanced.py e' a unica base de dados do bridge; sem ela fica
    # sem persistencia (historico/export/retencao do dashboard falham).
    import orm_persistence
    import storage_advanced as sa
    import auth_sessions
except ImportError as exc:
    print(f"[BRIDGE] AVISO GRAVE: modulo orm_persistence indisponivel ({exc}); "
          f"bridge vai correr SEM PERSISTENCIA NENHUMA (streaming ao vivo "
          f"continua, mas historico/export/retencao do dashboard vao falhar). "
          f"Instale as dependencias em requirements.txt.")
    orm_persistence = None
    sa = None
    auth_sessions = None

# fallback quando orm_persistence nao importa; iguais aos valores reais em storage_advanced.py
_FALLBACK_DEFAULT_RETENTION_DAYS = 30
_FALLBACK_MIN_RETENTION_DAYS = 1
_FALLBACK_MAX_RETENTION_DAYS = 3650

import ws_transport
from ws_transport import (
    WS_HOST, WS_PORT, WS_TLS_ENABLED, WS_TOKEN,
    build_ssl_context as _build_ssl_context,
)

import vital_alerts  # baseline comportamental personalizada (storage_advanced.py::PersonalizedThreshold)

try:
    # notifications.py faz o import tardio de twilio/sendgrid; este try cobre so' o ficheiro em falta
    import notifications
except ImportError as exc:
    print(f"[BRIDGE] AVISO: modulo notifications indisponivel ({exc}); notificacoes de emergencia desativadas")
    notifications = None

try:
    # scikit-learn/joblib/pandas so' em requirements_db.txt, nao no minimo usado por start_carewear.bat
    import activity_inference
except ImportError as exc:
    print(f"[BRIDGE] AVISO: modulo activity_inference indisponivel ({exc}); classificacao de atividade desativada")
    activity_inference = None

try:
    # tensorflow NAO esta' em requirements_db.txt (dependencia pesada, deliberadamente opcional --
    # ver anomaly_inference.py); em falta, deteccao de anomalias fica desativada, resto do bridge normal
    import anomaly_inference
except ImportError as exc:
    print(f"[BRIDGE] AVISO: modulo anomaly_inference indisponivel ({exc}); deteccao de anomalias desativada")
    anomaly_inference = None

# duplicado de activity_inference.CLASS_TO_DB_CATEGORY para a correcao manual (cmd "correct_activity")
# continuar a funcionar mesmo sem activity_inference instalado
ACTIVITY_CORRECTION_CATEGORIES = ("Dormir", "Descanso", "Atividade", "Alimentação", "Higiene")

# duplicado de activity_inference.DEFAULT_MODEL_NAME pelo mesmo motivo: comandos de versao do
# dashboard passam so' por `sa`, por isso funcionam mesmo sem activity_inference (ver
# pandas) — só a troca em tempo real (reload_active_model) é que fica sem
# efeito nesse caso, ver cmd "activate_model_version" abaixo.
ML_MODEL_NAME = "activity_classifier_rf"

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
UUID_EMERGENCY_ALERT = "abcd1234-5678-1234-5678-abcdef200004"
UUID_EMERGENCY_PROFILE_WRITE = "abcd1234-5678-1234-5678-abcdef200005"
# liveSnapshotChar (2026-08-06) — instantâneo ao vivo do registo mais
# recente do ring buffer, independente do atraso do dump histórico em
# UUID_DUMP_DATA (ver Ble.cpp, sendLiveSnapshot()/PROJECT_STATUS.md). Só
# existe em firmwares a partir desta data; subscrever é tolerante a
# falha (ver run_device_loop), tal como Battery Level acima.
UUID_LIVE_SNAPSHOT = "abcd1234-5678-1234-5678-abcdef200007"
# Battery Level (0x2A19) — Battery Service padrão do Bluetooth SIG (0x180F),
# publicada pelo firmware via Ble::updateBatteryLevel()/BLEBas (ver
# Battery.h/Ble.cpp, 2026-07-19). UUID padrão, não um dos "abcd1234..."
# próprios deste projeto — só existe em firmwares atualizados a partir
# dessa data; subscrever é sempre feito em modo tolerante a falha (ver
# abaixo), para não quebrar a ligação com firmware mais antigo.
UUID_BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"

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
# bate certo com o static_assert(sizeof(FullPlain) == 39, ...) do firmware.
# Ultimo campo (B, uint8) e' o pacing_index acrescentado em 2026-07-03 (ver
# PROJECT_STATUS.md, backlog de investigacao item 2) — bump de formato de
# 38 para 39 bytes.
FULL_PLAIN_STRUCT = struct.Struct("<IffffffIBBhhB")
assert FULL_PLAIN_STRUCT.size == 39, "FullPlain deve ter 39 bytes, igual ao firmware"

# EmergencyAlertPacket (8 bytes, ver src/Ble/Ble.cpp): type (uint8),
# reserved (uint8, ignorado), seq (uint16), timestamp_utc (uint32).
EMERGENCY_ALERT_STRUCT = struct.Struct("<BBHI")
assert EMERGENCY_ALERT_STRUCT.size == 8, "EmergencyAlertPacket deve ter 8 bytes, igual ao firmware"

# EmergencyAlertType (ver include/Ble/Ble.h) — os valores têm de
# corresponder exatamente ao enum do firmware.
EMERGENCY_ALERT_TYPE_NAMES = {
    1: "sos_manual",       # kEmergencyAlertSosManual
    2: "fall_inactivity",  # kEmergencyAlertFallInactivity
}

# "Explicação de alerta" (2026-08-05, funcionalidade derivada da revisão
# PRISMA — cuidadores recebem melhor um alerta quando sabem PORQUÊ foi
# disparado, não só o quê). O EmergencyAlertPacket (8 bytes) não traz
# amplitude/waveform nenhuma — só o tipo já decidido pelo firmware (ver
# discussão sobre beatPeakAbsHigh: a decisão "isto é um evento real" só é
# possível com acesso ao sinal bruto, que existe no firmware, não aqui) —
# por isso este texto descreve o MECANISMO de deteção, fixo por tipo, não
# um valor medido. Fonte do mecanismo: Emergency.cpp (gesto SOS) e
# Imu::detectFreefall()/Emergency.cpp (queda + inatividade), ambos citados
# em DOCUMENTACAO_TECNICA_CODIGO.md.
EMERGENCY_ALERT_EXPLANATIONS = {
    "sos_manual": (
        "Confirmado por 3 cliques do botão físico em menos de 1200ms, "
        "seguidos de 2500ms sem um 4º clique a cancelar o gesto "
        "(evita disparo por toque acidental)."
    ),
    "fall_inactivity": (
        "Queda livre detetada pelo IMU (acelerómetro), seguida de um "
        "período de inatividade prolongada sem movimento — padrão "
        "consistente com uma queda sem recuperação imediata."
    ),
}

# RF-07 (2026-09-07): título curto de cada alerta persistido em `alerts`
# (a coluna `title` é NOT NULL). O motivo detalhado com os números reais
# vai para `description`, composto por vital_alerts.explain_vital_alert().
_VITAL_ALERT_TITLES = {
    "hr": "Frequência cardíaca fora do intervalo definido",
    "spo2": "SpO2 abaixo do limiar definido",
}

# RF-08 (2026-09-07) — "registo da ação tomada após alerta". Allowlist
# fechada, validada no bridge e não só no dashboard: o canal WebSocket
# aceita qualquer JSON, e `resolution_note` acaba num relatório clínico —
# uma "ação" livre tornaria o campo impossível de agregar. A NOTA, essa,
# é texto livre de propósito (é o ponto do requisito), limitada só em
# comprimento e escapada por quem a mostra.
ALERT_RESOLUTION_ACTIONS = (
    "contactei_o_utente",
    "verifiquei_presencialmente",
    "contactei_a_equipa_clinica",
    "chamei_emergencia_medica",
    "ajustei_o_dispositivo",
    "falso_alarme",
    "sem_acao_necessaria",
)
ALERT_RESOLUTION_NOTE_MAX_CHARS = 500

EMERGENCY_ALERT_EXPLANATION_UNKNOWN = (
    "Tipo de alerta não reconhecido por esta versão do bridge — sem "
    "explicação disponível do mecanismo de deteção."
)

async def _ws_process_request(connection, request):
    return await ws_transport.process_request(connection, request, sa=sa, auth_sessions=auth_sessions)


# autorizacao por perfil no canal WebSocket. Perfis = users.role da BD ('family', 'clinician',
# 'admin'), nao os nomes do dashboard — traducao em API_ROLE_TO_DASHBOARD_ROLE. Cada comando
# mapeado ao perfil da vista que o invoca; 'admin' nunca abre dossie clinico.
_ROLES_CUIDADOR_E_CLINICO = ("family", "clinician")
_ROLES_SO_CLINICO = ("clinician",)

WS_COMMAND_ROLES = {
    "force_reading":         _ROLES_CUIDADOR_E_CLINICO,
    "set_ble_enabled":       _ROLES_CUIDADOR_E_CLINICO,
    "acknowledge_alert":     _ROLES_CUIDADOR_E_CLINICO,
    "get_alerts":            _ROLES_CUIDADOR_E_CLINICO,
    "get_anomalies":         _ROLES_CUIDADOR_E_CLINICO,
    "confirm_alert":         _ROLES_CUIDADOR_E_CLINICO,
    "get_history":           _ROLES_CUIDADOR_E_CLINICO,
    "get_daily_trend":       _ROLES_CUIDADOR_E_CLINICO,
    "get_episode_timeline":  _ROLES_CUIDADOR_E_CLINICO,
    "get_thresholds":        _ROLES_CUIDADOR_E_CLINICO,
    "set_thresholds":        _ROLES_CUIDADOR_E_CLINICO,
    "correct_activity":      _ROLES_CUIDADOR_E_CLINICO,
    "export_csv":            _ROLES_CUIDADOR_E_CLINICO,
    # exclusivos do perfil clinico: apagam dados, alteram governacao ou trocam o modelo em producao
    "reset_readings":        _ROLES_SO_CLINICO,
    "get_retention_days":    _ROLES_SO_CLINICO,
    "set_retention_days":    _ROLES_SO_CLINICO,
    "get_consent_status":    _ROLES_SO_CLINICO,
    "set_consent":           _ROLES_SO_CLINICO,
    "list_model_versions":   _ROLES_SO_CLINICO,
    "activate_model_version": _ROLES_SO_CLINICO,
}

# chave AES lida uma vez do ambiente; tem de ser EXATAMENTE a chave gravada no dispositivo
# via aesKeyChar durante o provisioning (16/24/32 bytes = 32/48/64 caracteres hex)
_AES_KEY_HEX_ENV = "CAREWEAR_AES_KEY_HEX"

def _load_aes_key_from_env() -> Optional[bytes]:
    raw_hex = os.environ.get(_AES_KEY_HEX_ENV)
    if not raw_hex:
        print(f"[BRIDGE] AVISO: {_AES_KEY_HEX_ENV} nao definida — os registos de "
              f"sensores nao vao poder ser decifrados (ver cabecalho deste ficheiro).")
        return None
    try:
        key = bytes.fromhex(raw_hex.strip())
    except ValueError:
        print(f"[BRIDGE] AVISO: {_AES_KEY_HEX_ENV} nao e' hexadecimal valido — ignorada")
        return None
    if len(key) not in (16, 24, 32):
        print(f"[BRIDGE] AVISO: {_AES_KEY_HEX_ENV} tem {len(key)} bytes — "
              f"tem de ter 16, 24 ou 32 (AES-128/192/256) — ignorada")
        return None
    print(f"[BRIDGE] chave AES carregada do ambiente ({len(key) * 8} bits)")
    return key

# notificacoes externas de emergencia: credenciais Twilio/SendGrid ficam em notifications.py;
# aqui so' QUEM notificar e QUANDO o cuidador esta indisponivel (especifico da instalacao/paciente)
_CAREGIVER_NAME_ENV = "CAREWEAR_CAREGIVER_NAME"
_CAREGIVER_PHONE_ENV = "CAREWEAR_CAREGIVER_PHONE"
_CAREGIVER_EMAIL_ENV = "CAREWEAR_CAREGIVER_EMAIL"
# contacto de emergencia e' sempre uma PESSOA (vizinho/familiar), nunca o 112 (ver notifications.py)
_EMERGENCY_CONTACT_NAME_ENV = "CAREWEAR_EMERGENCY_CONTACT_NAME"
_EMERGENCY_CONTACT_PHONE_ENV = "CAREWEAR_EMERGENCY_CONTACT_PHONE"
_EMERGENCY_CONTACT_EMAIL_ENV = "CAREWEAR_EMERGENCY_CONTACT_EMAIL"
# JSON: lista de {"weekday": 0-6 (0=segunda), "start": "HH:MM", "end": "HH:MM"}
_CAREGIVER_SCHEDULE_ENV = "CAREWEAR_CAREGIVER_SCHEDULE_JSON"


def _load_notification_recipients_from_env():
    """Le' do ambiente quem notificar num alerta real; sem variaveis definidas devolve
    ([], None, None) — nunca levanta excecao, um horario mal formado so' descarta esse horario."""
    caregivers = []
    name = os.environ.get(_CAREGIVER_NAME_ENV)
    if name:
        caregivers.append(notifications.EmergencyContact(
            name=name,
            phone=os.environ.get(_CAREGIVER_PHONE_ENV) or None,
            email=os.environ.get(_CAREGIVER_EMAIL_ENV) or None,
        ))

    emergency_contact = None
    ec_name = os.environ.get(_EMERGENCY_CONTACT_NAME_ENV)
    if ec_name:
        emergency_contact = notifications.EmergencyContact(
            name=ec_name,
            phone=os.environ.get(_EMERGENCY_CONTACT_PHONE_ENV) or None,
            email=os.environ.get(_EMERGENCY_CONTACT_EMAIL_ENV) or None,
        )

    schedule = None
    schedule_raw = os.environ.get(_CAREGIVER_SCHEDULE_ENV)
    if schedule_raw:
        try:
            entries = json.loads(schedule_raw)
            schedule = [
                notifications.ScheduleWindow(
                    weekday=int(entry["weekday"]),
                    start=dt_time.fromisoformat(entry["start"]),
                    end=dt_time.fromisoformat(entry["end"]),
                )
                for entry in entries
            ]
        except (ValueError, KeyError, TypeError) as exc:
            print(f"[BRIDGE] AVISO: {_CAREGIVER_SCHEDULE_ENV} invalido ({exc}) — "
                  f"horario de indisponibilidade do cuidador ignorado (sem horario "
                  f"declarado, o sistema nunca escala sozinho, ver notifications.py)")
            schedule = None

    return caregivers, emergency_contact, schedule

def decrypt_full_plain(key: bytes, nonce: int, ciphertext: bytes) -> bytes:
    """Decifra um FullPlain (39 bytes) cifrado com AES-CTR (ver encryptRecord()
    em src/Ble/Ble.cpp). IV de 16 bytes = nonce (4B big-endian) + zeros (4B)
    + contador de bloco (8B, so' esta parte incrementa). AES-ECB bloco a bloco
    + XOR, replicando exatamente o firmware (decifrar == cifrar em CTR)."""
    aes = AES.new(key, AES.MODE_ECB)
    counter = bytearray(16)
    counter[0] = (nonce >> 24) & 0xFF
    counter[1] = (nonce >> 16) & 0xFF
    counter[2] = (nonce >> 8) & 0xFF
    counter[3] = nonce & 0xFF
    # counter[4:16] comeca a zero (prefixo + contador de bloco)

    out = bytearray(len(ciphertext))
    offset = 0
    while offset < len(ciphertext):
        keystream_block = aes.encrypt(bytes(counter))
        take = min(16, len(ciphertext) - offset)
        for i in range(take):
            out[offset + i] = ciphertext[offset + i] ^ keystream_block[i]
        offset += take

        # incrementa counter[8:16] big-endian sem propagar carry para o prefixo
        idx = 16
        carry = 1
        while idx > 8 and carry:
            idx -= 1
            total = counter[idx] + carry
            counter[idx] = total & 0xFF
            carry = total >> 8
    return bytes(out)

def decode_full_plain(raw: bytes) -> dict:
    """Descodifica os 39 bytes de um registo FullPlain para um dict Python.

    A ordem dos campos tem de corresponder exatamente à struct FullPlain
    em src/Ble/Ble.cpp: ts, ax, ay, az, gx, gy, gz, steps, ff, inact,
    spo2, hr, pacing_index.
    """
    ts, ax, ay, az, gx, gy, gz, steps, ff, inact, spo2, hr, pacing_index = (
        FULL_PLAIN_STRUCT.unpack(raw)
    )
    return {
        "ts": ts,
        "ax": ax, "ay": ay, "az": az,
        "gx": gx, "gy": gy, "gz": gz,
        "steps": steps,
        "freefall": bool(ff),
        "inactivity": bool(inact),
        # spo2/hr chegam a 0 quando nao ha leitura nova (ver storageTask em main.cpp)
        "spo2": spo2 if spo2 != 0 else None,
        "hr": hr if hr != 0 else None,
        # 0-100, "pacing"/curvas apertadas via giroscopio (Imu::detectPacing); 0 e' valor real, nao sentinela
        "pacing_index": pacing_index,
    }

# limites de plausibilidade fisica para rejeitar registos com chave AES errada (ruido reinterpretado
# como floats/ints); folga generosa, nao sao limiares clinicos
_MAX_ACCEL_G = 20.0       # IMU nunca deveria exceder ~16g em uso normal
_MAX_GYRO_DPS = 3000.0    # LSM6DS3 satura bem abaixo disto
_MAX_STEPS = 200_000_000  # contador de passos plausivel (anos de uso)

def is_plausible_full_plain(record: dict) -> bool:
    """True se o registo decifrado parece fisicamente possível. Ver nota
    acima — um "não" aqui quase sempre significa chave/nonce AES errada,
    não um bug de decode em si."""
    accel_ok = all(
        math.isfinite(record[k]) and abs(record[k]) <= _MAX_ACCEL_G
        for k in ("ax", "ay", "az")
    )
    gyro_ok = all(
        math.isfinite(record[k]) and abs(record[k]) <= _MAX_GYRO_DPS
        for k in ("gx", "gy", "gz")
    )
    steps_ok = 0 <= record["steps"] <= _MAX_STEPS
    hr_ok = record["hr"] is None or 0 <= record["hr"] <= 250
    spo2_ok = record["spo2"] is None or 0 <= record["spo2"] <= 100
    return accel_ok and gyro_ok and steps_ok and hr_ok and spo2_ok

def decode_emergency_alert(raw: bytes) -> dict:
    """Descodifica os 8 bytes de EmergencyAlertPacket (ver Ble.cpp):
    type, reserved, seq, timestamp_utc. 'seq' incrementa a cada alerta
    enviado pelo firmware — usado pelo dashboard para não duplicar o
    mesmo alerta se a notificação BLE chegar mais do que uma vez."""
    alert_type, _reserved, seq, timestamp_utc = EMERGENCY_ALERT_STRUCT.unpack(raw)
    alert_name = EMERGENCY_ALERT_TYPE_NAMES.get(alert_type, "desconhecido")
    return {
        "alert_type": alert_type,
        "alert_name": alert_name,
        "seq": seq,
        "timestamp_utc": timestamp_utc,
        "explanation": EMERGENCY_ALERT_EXPLANATIONS.get(alert_name, EMERGENCY_ALERT_EXPLANATION_UNKNOWN),
    }

def _ws_remote_ip(ws) -> Optional[str]:
    """Extrai o IP de origem de uma ligação WebSocket para auditoria
    (GDPR-003). `ws.remote_address` é um tuplo (host, port, ...) nas
    ligações reais do `websockets`; nos testes (FakeWebSocket) o atributo
    pode não existir — devolve None em vez de rebentar."""
    remote = getattr(ws, "remote_address", None)
    if isinstance(remote, (tuple, list)) and remote:
        return str(remote[0])
    if isinstance(remote, str):
        return remote
    return None

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
        self.ws_user_ids: dict[websockets.ServerConnection, Optional[int]] = {}
        # RF-02: perfil resolvido no handshake, usado por WS_COMMAND_ROLES.
        self.ws_user_roles: dict[websockets.ServerConnection, Optional[str]] = {}
        self._pending_fragments: dict[int, dict] = {}
        # dicionario separado do liveSnapshotChar: mesmo rec_seq pode aparecer nos dois canais
        self._live_pending_fragments: dict[int, dict] = {}
        self.connected_device_name: Optional[str] = None
        self.connected_device_mac: Optional[str] = None
        self.last_record_ts: Optional[int] = None
        # limite de taxa para o dashboard: IMU produz ate ~52 registos/seg, enviar ao ritmo total
        # causava desconexoes WebSocket; registos com HR/SpO2 novo sao sempre enviados de imediato
        self._last_broadcast_monotonic = 0.0
        # true apos subscricao a liveSnapshotChar ter sucesso (decide onde avaliar alertas de vitais)
        self._live_snapshot_available = False
        # cliente BLE atualmente ligado, para comandos do dashboard escreverem em dumpCtrlChar
        self.current_client: Optional[BleakClient] = None
        self.orm = None
        if orm_persistence is not None:
            try:
                self.orm = orm_persistence.OrmPersistence()
            except Exception as exc:  # noqa: BLE001 - persistencia nunca derruba o arranque do streaming
                print(f"[BRIDGE] AVISO GRAVE: persistencia (storage_advanced.py) indisponivel: {exc}. "
                      f"Streaming ao vivo continua; historico/export/retencao do dashboard vao falhar.")
                self.orm = None
        self.aes_key = _load_aes_key_from_env()
        self._missing_key_warned = False
        self._implausible_record_warned = False
        # se notifications nao importou, escalation_manager fica None e nunca notifica ninguem,
        # sem impedir o arranque do bridge nem o broadcast do alerta ao dashboard
        self.escalation_manager = None
        self.notify_caregivers: list = []
        self.notify_emergency_contact = None
        self.notify_schedule = None
        if notifications is not None:
            try:
                self.escalation_manager = notifications.EscalationManager()
            except Exception as exc:  # noqa: BLE001 - nunca deve impedir o arranque do bridge
                print(f"[BRIDGE] AVISO: gestor de escalonamento de notificacoes indisponivel: {exc}")
                self.escalation_manager = None
            self.notify_caregivers, self.notify_emergency_contact, self.notify_schedule = (
                _load_notification_recipients_from_env()
            )
            if self.escalation_manager is not None and not self.notify_caregivers and self.notify_emergency_contact is None:
                print(f"[BRIDGE] AVISO: nenhum cuidador/contacto de emergencia configurado "
                      f"({_CAREGIVER_NAME_ENV}/{_EMERGENCY_CONTACT_NAME_ENV}) — alertas de "
                      f"emergencia reais nao vao notificar ninguem fora do dashboard.")
        # ultimo instante de cada comando de escrita aceite, por nome. Global (nao por-cliente):
        # varios separadores do dashboard partilham o mesmo BLE fisico
        self._last_write_command_monotonic: dict[str, float] = {}
        # True = run_device_loop procura/mantem a ligacao BLE normalmente. False (pedido do
        # dashboard) = larga a ligacao e para de reconectar ate' voltar a True; WebSocket continua
        self.ble_enabled = True
        # falha aqui (modelo em falta, scikit-learn nao instalado) nunca impede o arranque
        self.activity_inference = None
        if activity_inference is not None:
            try:
                self.activity_inference = activity_inference.ActivityInference()
                if not self.activity_inference.available:
                    print(f"[BRIDGE] AVISO: classificador de atividade indisponivel "
                          f"({self.activity_inference.load_error}); classificacao desativada")
                    self.activity_inference = None
            except Exception as exc:  # noqa: BLE001 - nunca deve impedir o arranque
                print(f"[BRIDGE] AVISO: falha ao inicializar activity_inference: {exc}")
                self.activity_inference = None

        # idem, mas tensorflow em falta (comum — dependencia opcional) e' esperado, nao um erro grave
        self.anomaly_inference = None
        if anomaly_inference is not None:
            try:
                self.anomaly_inference = anomaly_inference.AnomalyInference()
                if not self.anomaly_inference.available:
                    print(f"[BRIDGE] AVISO: deteccao de anomalias indisponivel "
                          f"({self.anomaly_inference.load_error}); desativada")
                    self.anomaly_inference = None
            except Exception as exc:  # noqa: BLE001 - nunca deve impedir o arranque
                print(f"[BRIDGE] AVISO: falha ao inicializar anomaly_inference: {exc}")
                self.anomaly_inference = None
        # serializa chamadas ao autoencoder (correm em thread pool via asyncio.to_thread) -- evita
        # que duas janelas do mesmo dispositivo sejam pontuadas fora de ordem se uma demorar mais
        self._anomaly_lock = asyncio.Lock()

        # ultimo ESTADO (nao valor) difundido por sinal vital, so' notifica o dashboard numa MUDANCA
        self._vital_alert_state: dict[str, Optional[str]] = {"hr": None, "spo2": None}

        # deteta nao-uso do dispositivo; alimentado por _on_live_snapshot, reposto quando a ligacao cai
        self.wear_detector = vital_alerts.WearDetector()

    RECORD_BROADCAST_MIN_INTERVAL_S = 0.25  # no maximo ~4 atualizacoes/seg
    RETENTION_CHECK_INTERVAL_S = 6 * 3600  # limpeza de sensor_records (orm.purge)
    ORM_RETENTION_INTERVAL_S = 86400  # limpeza GDPR-006 do ORM, politicas em anos
    # limite de taxa para comandos de escrita do dashboard (canal WebSocket nao autenticado); sem isto
    # reset_readings podia ser enviado em loop e apagar o historico do wearable repetidamente
    WRITE_COMMAND_MIN_INTERVAL_S = 2.0
    # fragmentos BLE perdidos (notify() sem confirmacao) ficavam para sempre em _pending_fragments;
    # entradas mais velhas que isto sao consideradas perdidas e descartadas
    PENDING_FRAGMENT_TIMEOUT_S = 5.0

    async def periodic_retention_task(self) -> None:
        """Aplica a politica de retencao configuravel (storage_advanced.get/set_retention_days via
        self.orm) no arranque e a cada RETENTION_CHECK_INTERVAL_S. So' apaga sensor_records."""
        while True:
            try:
                if self.orm:
                    days = self.orm.get_retention_days()
                    deleted = self.orm.purge(days)
                    if deleted:
                        print(f"[BRIDGE] retencao: apagados {deleted} registos de sensores "
                              f"com mais de {days} dias")
                else:
                    print("[BRIDGE] retencao: persistencia indisponivel, nada a limpar")
            except Exception as exc:  # noqa: BLE001 - nunca deve derrubar o bridge
                print(f"[BRIDGE] erro na limpeza de retencao: {exc}")
            await asyncio.sleep(self.RETENTION_CHECK_INTERVAL_S)

    async def periodic_orm_retention_task(self) -> None:
        """GDPR-006: aplica as politicas de retencao FIXAS do ORM (DataRetention.cleanup —
        sensor_records, activity_windows, alerts, medication_adherence;
        emergency_alerts nunca e' apagado). Distinta de periodic_retention_task, que so' cobre
        a retencao CONFIGURAVEL do dashboard."""
        while True:
            try:
                if self.orm:
                    result = await self.orm.run_retention_cleanup()
                    if result:
                        resumo = ", ".join(f"{k}={v}" for k, v in result.items() if v)
                        if resumo:
                            print(f"[BRIDGE] retencao ORM (GDPR-006): apagados/marcados {resumo}")
            except Exception as exc:  # noqa: BLE001 - nunca deve derrubar o bridge
                print(f"[BRIDGE] erro na limpeza de retencao ORM: {exc}")
            await asyncio.sleep(self.ORM_RETENTION_INTERVAL_S)

    async def broadcast(self, payload: dict) -> None:
        if not self.ws_clients:
            return
        message = json.dumps(payload)
        # itera sobre copia da lista: iterar direto sobre o set causava "RuntimeError: Set changed
        # size during iteration" quando ws_handler faz add()/discard() a meio de um broadcast
        dead = set()
        for ws in list(self.ws_clients):
            try:
                await ws.send(message)
            except websockets.exceptions.ConnectionClosed:
                dead.add(ws)
        self.ws_clients -= dead

    def _prune_stale_fragments(self) -> None:
        """Remove entradas orfas de _pending_fragments (fragmentos BLE perdidos, notify() sem
        retransmissao) para evitar fuga de memoria e mistura com rec_seq reciclado."""
        now = time.monotonic()
        stale = [seq for seq, e in self._pending_fragments.items()
                 if now - e["created_at"] > self.PENDING_FRAGMENT_TIMEOUT_S]
        for seq in stale:
            del self._pending_fragments[seq]

    def _prune_stale_live_fragments(self) -> None:
        """Mesma logica de _prune_stale_fragments(), mas sobre _live_pending_fragments."""
        now = time.monotonic()
        stale = [seq for seq, e in self._live_pending_fragments.items()
                 if now - e["created_at"] > self.PENDING_FRAGMENT_TIMEOUT_S]
        for seq in stale:
            del self._live_pending_fragments[seq]

    def _on_live_snapshot(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        """Callback de liveSnapshotChar (Ble.cpp::sendLiveSnapshot()): mesmo formato/cifra do dump
        historico, mas instantaneo independente do registo mais recente, sem consumir o ring buffer.
        Da acesso imediato ao "agora" enquanto o dump historico entrega cronologicamente do mais
        antigo. Nao persiste nem alimenta a classificacao de atividade — so' difunde ao dashboard;
        usa dicionario proprio (_live_pending_fragments) para nao contaminar _pending_fragments."""
        if len(data) < 12:
            return
        _type, frag_idx, frag_total, chunk_len = data[0], data[1], data[2], data[3]
        rec_seq = struct.unpack_from("<I", data, 4)[0]
        nonce = struct.unpack_from("<I", data, 8)[0]
        chunk = bytes(data[12:12 + chunk_len])

        if frag_total == 0 or not (0 <= frag_idx < frag_total):
            print(f"[BRIDGE] instantaneo ao vivo: fragmento com frag_idx={frag_idx} invalido "
                  f"(frag_total={frag_total}, rec_seq={rec_seq}) — descartado")
            return

        entry = self._live_pending_fragments.setdefault(
            rec_seq, {"total": frag_total, "nonce": nonce, "parts": {}, "created_at": time.monotonic()}
        )
        entry["parts"][frag_idx] = chunk

        if len(entry["parts"]) < entry["total"]:
            self._prune_stale_live_fragments()
            return  # ainda faltam fragmentos deste instantâneo

        try:
            cipher_full = b"".join(entry["parts"][i] for i in range(entry["total"]))
        except KeyError:
            print(f"[BRIDGE] instantaneo ao vivo rec_seq={rec_seq}: fragmentos completos em "
                  f"contagem mas com indices em falta — descartado")
            del self._live_pending_fragments[rec_seq]
            return
        record_nonce = entry["nonce"]
        del self._live_pending_fragments[rec_seq]

        if len(cipher_full) != FULL_PLAIN_STRUCT.size:
            print(f"[BRIDGE] instantaneo ao vivo rec_seq={rec_seq} com tamanho inesperado "
                  f"({len(cipher_full)} bytes, esperado {FULL_PLAIN_STRUCT.size}) — ignorado")
            return

        if self.aes_key is None:
            return  # ja avisado pelo caminho historico (_on_dump_data)

        full = decrypt_full_plain(self.aes_key, record_nonce, cipher_full)
        record = decode_full_plain(full)

        if not is_plausible_full_plain(record):
            return  # chave/nonce errada, ja avisado pelo caminho historico

        # avaliado aqui (nao no dump historico) porque este instantaneo e' sempre o mais recente
        if self.orm:
            has_new_vital = record["hr"] is not None or record["spo2"] is not None
            if has_new_vital:
                try:
                    thresholds = self.orm.get_thresholds()
                    if record["hr"] is not None:
                        self._maybe_broadcast_vital_alert("hr", vital_alerts.evaluate_hr(record["hr"], thresholds), thresholds)
                    if record["spo2"] is not None:
                        self._maybe_broadcast_vital_alert("spo2", vital_alerts.evaluate_spo2(record["spo2"], thresholds), thresholds)
                except Exception as exc:  # noqa: BLE001 - nunca deve travar o streaming
                    print(f"[BRIDGE] erro na avaliacao de sinais vitais (instantaneo ao vivo): {exc}")

        # nao-uso do dispositivo: so' aqui, nunca no dump historico (registos gravados sem BLE
        # ligado dariam falso "dispositivo removido" numa reconexao com backlog)
        self._observe_wear_state(record)

        asyncio.create_task(self.broadcast({"kind": "live_record", "rec_seq": rec_seq, **record}))

    def _on_dump_data(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        """Callback de dumpDataChar. Cada notificacao e' um fragmento (DumpDataPacket, 20 bytes):
        type, frag_idx, frag_total, chunk_len, rec_seq (u32), nonce (u32), chunk[8]. Um FullPlain
        (39 bytes) cifrado chega em ate 5 fragmentos; remonta por rec_seq antes de decifrar."""
        if len(data) < 12:
            return
        _type, frag_idx, frag_total, chunk_len = data[0], data[1], data[2], data[3]
        rec_seq = struct.unpack_from("<I", data, 4)[0]
        nonce = struct.unpack_from("<I", data, 8)[0]
        chunk = bytes(data[12:12 + chunk_len])

        # valida frag_idx contra bit-flip BLE, evitando KeyError no join() abaixo
        if frag_total == 0 or not (0 <= frag_idx < frag_total):
            print(f"[BRIDGE] fragmento com frag_idx={frag_idx} invalido "
                  f"(frag_total={frag_total}, rec_seq={rec_seq}) — descartado")
            return

        entry = self._pending_fragments.setdefault(
            rec_seq, {"total": frag_total, "nonce": nonce, "parts": {}, "created_at": time.monotonic()}
        )
        entry["parts"][frag_idx] = chunk

        if len(entry["parts"]) < entry["total"]:
            self._prune_stale_fragments()
            return  # ainda faltam fragmentos deste registo

        # todos os fragmentos chegaram — remonta pela ordem correta
        try:
            cipher_full = b"".join(entry["parts"][i] for i in range(entry["total"]))
        except KeyError:
            # indices nao cobrem 0..total-1 (ex.: frag_idx duplicado); descarta em vez de propagar
            print(f"[BRIDGE] rec_seq={rec_seq}: fragmentos completos em contagem "
                  f"mas com indices em falta — registo descartado")
            del self._pending_fragments[rec_seq]
            return
        record_nonce = entry["nonce"]
        del self._pending_fragments[rec_seq]

        if len(cipher_full) != FULL_PLAIN_STRUCT.size:
            print(f"[BRIDGE] registo rec_seq={rec_seq} com tamanho inesperado "
                  f"({len(cipher_full)} bytes, esperado {FULL_PLAIN_STRUCT.size}) — ignorado")
            return

        if self.aes_key is None:
            if not self._missing_key_warned:
                print(f"[BRIDGE] AVISO: a descartar registos de sensores — "
                      f"{_AES_KEY_HEX_ENV} nao configurada, nao ha' como decifrar "
                      f"(ver cabecalho deste ficheiro). Este aviso so' aparece uma vez.")
                self._missing_key_warned = True
            return

        full = decrypt_full_plain(self.aes_key, record_nonce, cipher_full)

        record = decode_full_plain(full)

        if not is_plausible_full_plain(record):
            # quase sempre chave/nonce AES errada; rejeitar evita mostrar valores absurdos no dashboard
            if not self._implausible_record_warned:
                self._implausible_record_warned = True
                print(f"[BRIDGE] AVISO: registo rec_seq={rec_seq} decifrado com valores "
                      f"fisicamente impossiveis (hr={record['hr']}, spo2={record['spo2']}, "
                      f"steps={record['steps']}) — descartado. Isto quase sempre significa "
                      f"que {_AES_KEY_HEX_ENV} nao bate certo com a chave gravada na flash "
                      f"do dispositivo (ou o nonce dessincronizou). Este aviso so aparece "
                      f"uma vez; ver PROJECT_STATUS.md.")
            return

        self.last_record_ts = record["ts"]

        # persiste TODOS os registos, independente do limite de taxa do broadcast a seguir
        if self.orm:
            self.orm.insert_sensor_record(record)

        # janela de 10s de activity_inference precisa do sinal completo, nao da amostra do broadcast
        if self.activity_inference is not None:
            try:
                activity_result = self.activity_inference.add_sample(record)
            except Exception as exc:  # noqa: BLE001 - nunca deve travar o streaming
                print(f"[BRIDGE] erro na classificacao de atividade: {exc}")
                activity_result = None
            if activity_result:
                asyncio.create_task(self.broadcast(activity_result))
                closed = activity_result.get("closed_block")
                if closed:
                    # so' persiste quando um bloco fecha (classe/sessao mudou), nao a cada ~10s
                    if self.orm:
                        self.orm.insert_activity_window(closed)
                    asyncio.create_task(self.broadcast({
                        "kind": "activity_duration_flag", **closed,
                    }))

        # deteccao de anomalias (LSTM Autoencoder) corre em thread pool -- model.predict() e'
        # mais pesado que o classificador RF, nao deve bloquear este callback nem o loop de eventos
        if self.anomaly_inference is not None:
            asyncio.create_task(self._score_anomaly_async(record))

        has_new_vital = record["hr"] is not None or record["spo2"] is not None

        # Baseline comportamental personalizada (2026-08-05, ver
        # vital_alerts.py) — avalia CADA sinal vital independentemente (só
        # quando este record em concreto trouxe uma leitura NOVA desse
        # sinal; record["hr"]/record["spo2"] vêm None na maioria dos
        # records, não representam "voltou ao normal"). Nunca bloqueia o
        # streaming: qualquer erro é apanhado e só impede este alerta em
        # concreto, nunca o resto de _on_dump_data.
        # Bug real corrigido aqui (2026-08-06, relatado pela utilizadora com
        # captura de ecrã: mostrador ao vivo dizia HR=111bpm, mas o alerta
        # dizia "35bpm — abaixo do limiar"): esta funcao (_on_dump_data)
        # processa o DUMP HISTORICO, que entrega registos em ordem
        # cronologica desde o backlog acumulado (por vezes minutos/horas
        # antigos, ver "armazenamento cheio" no dashboard) — avaliar
        # alertas aqui significa alertar com base numa leitura ANTIGA,
        # nao no estado atual do paciente. Quando o instantaneo ao vivo
        # (liveSnapshotChar) esta disponivel nesta ligacao, os alertas
        # passam a ser avaliados so' a partir dele (ver _on_live_snapshot),
        # que reflete sempre o mais recente. Mantido aqui como reserva
        # apenas para firmware antigo sem liveSnapshotChar (ver
        # self._live_snapshot_available).
        if self.orm and has_new_vital and not self._live_snapshot_available:
            try:
                thresholds = self.orm.get_thresholds()
                if record["hr"] is not None:
                    self._maybe_broadcast_vital_alert("hr", vital_alerts.evaluate_hr(record["hr"], thresholds), thresholds)
                if record["spo2"] is not None:
                    self._maybe_broadcast_vital_alert("spo2", vital_alerts.evaluate_spo2(record["spo2"], thresholds), thresholds)
            except Exception as exc:  # noqa: BLE001 - nunca deve travar o streaming
                print(f"[BRIDGE] erro na avaliacao de sinais vitais: {exc}")

        now = time.monotonic()
        due = (now - self._last_broadcast_monotonic) >= self.RECORD_BROADCAST_MIN_INTERVAL_S
        if not (has_new_vital or due):
            return  # amostra "normal" enviada ha pouco tempo — poupa o browser
        self._last_broadcast_monotonic = now

        asyncio.create_task(self.broadcast({"kind": "record", "rec_seq": rec_seq, **record}))

    async def _score_anomaly_async(self, record: dict) -> None:
        """Corre anomaly_inference.add_sample() em thread pool (model.predict do
        LSTM Autoencoder), serializado por self._anomaly_lock para nao pontuar
        janelas do mesmo dispositivo fora de ordem. Persiste e difunde so' quando
        um episodio de anomalia fecha (nao a cada ~10s -- ver anomaly_inference.py)."""
        async with self._anomaly_lock:
            try:
                result = await asyncio.to_thread(
                    self.anomaly_inference.add_sample, self.orm.device_id if self.orm else 0, record,
                )
            except Exception as exc:  # noqa: BLE001 - nunca deve travar o streaming
                print(f"[BRIDGE] erro na deteccao de anomalias: {exc}")
                return
        if not result:
            return
        asyncio.create_task(self.broadcast({
            "kind": "anomaly_score", "score": result["score"], "threshold": result["threshold"],
            "is_anomaly": result["is_anomaly"],
        }))
        closed = result.get("closed_episode")
        if closed:
            if self.orm:
                self.orm.insert_anomaly_detection(closed)
            asyncio.create_task(self.broadcast({"kind": "anomaly_detected", **closed}))

    def _maybe_broadcast_vital_alert(self, vital_key: str, alert: Optional[dict], thresholds: dict) -> None:
        """Difunde "vital_alert" só numa MUDANÇA de estado para este sinal
        (None<->'low'<->'high'), nunca a cada leitura — sem isto, uma FC
        persistentemente alta gerava uma mensagem WS a cada ~30s (cadência
        do PPG) enquanto se mantivesse fora do limiar, inundando o
        dashboard sem informação nova. 'cleared' distingue explicitamente
        "voltou ao normal" de "primeira leitura, nunca esteve em alerta"
        (o segundo caso não gera mensagem nenhuma — nunca houve nada para
        limpar)."""
        state_key = alert["level"] if alert else None
        if state_key == self._vital_alert_state.get(vital_key):
            return
        was_in_alert = self._vital_alert_state.get(vital_key) is not None
        self._vital_alert_state[vital_key] = state_key
        if alert is None and not was_in_alert:
            return  # nunca esteve em alerta — nada para anunciar como "limpo"
        payload = {"kind": "vital_alert", "vital": vital_key, "cleared": alert is None}
        if alert:
            payload.update(alert)
            payload["explanation"] = vital_alerts.explain_vital_alert(alert)
            # persiste em `alerts` com uuid estavel para confirmacao (confirm_alert) e escalonamento
            severity = vital_alerts.severity_for_vital_alert(alert)
            payload["severity"] = severity
            alert_uuid = self._persist_alert(
                alert_type=f"abnormal_vitals_{vital_key}",
                severity=severity,
                title=_VITAL_ALERT_TITLES.get(vital_key, "Sinal vital fora do esperado"),
                description=payload["explanation"],
                raw_data={k: alert.get(k) for k in ("vital", "level", "value", "limit")},
            )
            if alert_uuid:
                payload["alert_uuid"] = alert_uuid
        else:
            payload["explanation"] = vital_alerts.explain_vital_cleared(vital_key, thresholds)
        asyncio.create_task(self.broadcast(payload))

    # alertas persistidos, confirmacao e escalonamento por tempo. Escritas diretas em
    # self.orm.session (nao em orm_persistence.py) — mesma sessao SQLAlchemy, commit + rollback
    ALERT_ESCALATION_MINUTES = 15  # sem confirmacao ao fim disto, sobe um nivel de severidade
    ALERT_ESCALATION_CHECK_INTERVAL_S = 60  # cadencia de reavaliacao de periodic_alert_escalation_task
    ALERT_LIST_MAX = 100  # get_alerts nao serve os 7 anos de retencao (GDPR-006) a um browser

    def _persist_alert(
        self,
        alert_type: str,
        severity: str,
        title: str,
        description: str,
        raw_data: Optional[dict] = None,
    ) -> Optional[str]:
        """Grava um alerta em `alerts` e devolve o uuid (None se a persistencia falhar; o alerta
        segue na mesma ao dashboard ao vivo, so' nao fica confirmavel/escalavel)."""
        if not self.orm or getattr(self.orm, "session", None) is None or self.orm.disabled:
            return None
        if sa is None:
            return None
        alert_uuid = str(uuid4())
        try:
            row = sa.Alert(
                uuid=alert_uuid,
                device_id=self.orm.device_id,
                alert_type=alert_type,
                severity=severity,
                title=title,
                description=description,
                raw_data=raw_data or {},
            )
            self.orm.session.add(row)
            self.orm.session.commit()
            return alert_uuid
        except Exception as exc:  # noqa: BLE001 - nunca derruba o caminho ao vivo
            print(f"[BRIDGE] erro a persistir alerta ({alert_type}): {exc}")
            try:
                self.orm.session.rollback()
            except Exception:  # noqa: BLE001
                pass
            return None

    @staticmethod
    def _alert_to_dict(row) -> dict:
        """Serializa uma linha de `alerts`. `severity` e' o nivel original, `effective_severity`
        o atual (ja escalado, se foi) — o dashboard precisa dos dois para mostrar a subida."""
        def _iso(value):
            return value.isoformat() if value is not None else None

        effective = row.escalated_to_severity or row.severity
        return {
            "uuid": row.uuid,
            "alert_type": row.alert_type,
            "severity": row.severity,
            "effective_severity": effective,
            "title": row.title,
            # frase composta por vital_alerts/explain_wear_state com os numeros que dispararam o alerta
            "reason": row.description,
            "created_at": _iso(row.created_at),
            "read_at": _iso(row.read_at),
            "read_by_user_id": row.read_by_user_id,
            "escalated_to_severity": row.escalated_to_severity,
            "escalated_at": _iso(row.escalated_at),
            "resolved_at": _iso(row.resolved_at),
            "resolved_by_user_id": row.resolved_by_user_id,
            # acao + nota livre, prefixo estavel (_format_resolution_note) para o dashboard separar
            "resolution_note": row.resolution_note,
        }

    def _list_alerts(self, limit: int) -> list:
        if not self.orm or getattr(self.orm, "session", None) is None or self.orm.disabled or sa is None:
            return []
        rows = (
            self.orm.session.query(sa.Alert)
            .filter(sa.Alert.device_id == self.orm.device_id)
            .filter(sa.Alert.deleted_at.is_(None))
            .order_by(sa.Alert.created_at.desc())
            .limit(limit)
            .all()
        )
        return [self._alert_to_dict(r) for r in rows]

    def _list_anomalies(self, limit: int) -> list:
        """Episódios de anomalia já fechados deste dispositivo, mais recentes primeiro
        (ver storage_advanced.py::AnomalyDetection, anomaly_inference.py)."""
        if not self.orm or getattr(self.orm, "session", None) is None or self.orm.disabled or sa is None:
            return []
        rows = (
            self.orm.session.query(sa.AnomalyDetection)
            .filter(sa.AnomalyDetection.device_id == self.orm.device_id)
            .order_by(sa.AnomalyDetection.window_start.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id, "detector": r.detector, "anomaly_category": r.anomaly_category,
                "score": r.score, "threshold_used": r.threshold_used,
                "window_start": r.window_start.isoformat(), "window_end": r.window_end.isoformat(),
                "description": r.description, "severity": r.severity, "model_version": r.model_version,
            }
            for r in rows
        ]

    @staticmethod
    def _format_resolution_note(action: str, note: str) -> str:
        """Acao e nota livre partilham a coluna resolution_note; prefixo "[acao] " estavel para o
        dashboard separar. Nota NAO e' sanitizada aqui — quem a mostra escapa (ver escapeHtml())."""
        note = (note or "").strip()
        return f"[{action}] {note}" if note else f"[{action}]"

    async def _escalate_overdue_alerts(self, now: Optional[datetime] = None) -> list:
        """Sobe um nivel os alertas sem confirmacao ha' ALERT_ESCALATION_MINUTES desde o ultimo
        marco. Alertas confirmados/lidos nunca escalam; 'critical' nao escala (sem nivel acima);
        escada partilhada com vital_alerts.SEVERITY_LADDER. Devolve os alertas escalados, serializados."""
        if not self.orm or getattr(self.orm, "session", None) is None or self.orm.disabled or sa is None:
            return []
        now = now or datetime.utcnow()
        cutoff = now - timedelta(minutes=self.ALERT_ESCALATION_MINUTES)
        escalated = []
        try:
            candidates = (
                self.orm.session.query(sa.Alert)
                .filter(sa.Alert.device_id == self.orm.device_id)
                .filter(sa.Alert.deleted_at.is_(None))
                .filter(sa.Alert.resolved_at.is_(None))
                .filter(sa.Alert.read_at.is_(None))
                .all()
            )
            for row in candidates:
                marco = row.escalated_at or row.created_at
                if marco is None or marco > cutoff:
                    continue
                atual = row.escalated_to_severity or row.severity
                seguinte = vital_alerts.next_severity(atual)
                if seguinte is None:
                    continue
                row.escalated_to_severity = seguinte
                row.escalated_at = now
                escalated.append(row)
            if escalated:
                self.orm.session.commit()
        except Exception as exc:  # noqa: BLE001 - nunca derruba a task periódica
            print(f"[BRIDGE] erro a escalar alertas: {exc}")
            try:
                self.orm.session.rollback()
            except Exception:  # noqa: BLE001
                pass
            return []
        return [self._alert_to_dict(r) for r in escalated]

    async def _handle_confirm_alert(self, ws, cmd: str, msg: dict) -> None:
        """Confirma um alerta (trava o escalonamento) e regista a acao + nota. Extraido de
        handle_dashboard_command por tamanho; autorizacao ja correu em WS_COMMAND_ROLES."""
        wait_s = self._check_write_rate_limit(cmd)
        if wait_s is not None:
            await ws.send(json.dumps({
                "kind": "confirm_alert_result", "ok": False,
                "error": f"limite de taxa excedido, aguarde {wait_s:.1f}s",
            }))
            return

        alert_uuid = msg.get("alert_uuid")
        action = msg.get("action")
        note = msg.get("note", "")
        if not isinstance(alert_uuid, str) or not alert_uuid:
            await ws.send(json.dumps({
                "kind": "confirm_alert_result", "ok": False, "error": "alert_uuid em falta",
            }))
            return
        if action not in ALERT_RESOLUTION_ACTIONS:
            await ws.send(json.dumps({
                "kind": "confirm_alert_result", "ok": False, "alert_uuid": alert_uuid,
                "error": "acao desconhecida",
            }))
            return
        if not isinstance(note, str):
            note = ""
        if len(note) > ALERT_RESOLUTION_NOTE_MAX_CHARS:
            await ws.send(json.dumps({
                "kind": "confirm_alert_result", "ok": False, "alert_uuid": alert_uuid,
                "error": f"nota acima de {ALERT_RESOLUTION_NOTE_MAX_CHARS} caracteres",
            }))
            return
        if not self.orm or getattr(self.orm, "session", None) is None or self.orm.disabled or sa is None:
            await ws.send(json.dumps({
                "kind": "confirm_alert_result", "ok": False, "alert_uuid": alert_uuid,
                "error": "persistencia indisponivel",
            }))
            return

        user_id = self._ws_user_id(ws)
        agora = datetime.utcnow()
        try:
            row = (
                self.orm.session.query(sa.Alert)
                .filter(sa.Alert.uuid == alert_uuid)
                .filter(sa.Alert.device_id == self.orm.device_id)
                .one_or_none()
            )
            if row is None:
                await ws.send(json.dumps({
                    "kind": "confirm_alert_result", "ok": False, "alert_uuid": alert_uuid,
                    "error": "alerta desconhecido",
                }))
                return
            # so' a primeira confirmacao conta para o escalonamento; a segunda so' atualiza a nota
            if row.read_at is None:
                row.read_at = agora
                row.read_by_user_id = user_id
            if row.resolved_at is None:
                row.resolved_at = agora
                row.resolved_by_user_id = user_id
            row.resolution_note = self._format_resolution_note(action, note)
            self.orm.session.commit()
            alerta = self._alert_to_dict(row)
        except Exception as exc:  # noqa: BLE001
            print(f"[BRIDGE] erro a confirmar alerta {alert_uuid}: {exc}")
            try:
                self.orm.session.rollback()
            except Exception:  # noqa: BLE001
                pass
            await ws.send(json.dumps({
                "kind": "confirm_alert_result", "ok": False, "alert_uuid": alert_uuid,
                "error": str(exc),
            }))
            return

        self.orm.audit(
            action="alert.confirm",
            resource_type="alert",
            details={"alert_uuid": alert_uuid, "resolution_action": action},
            ip=_ws_remote_ip(ws),
            user_id=user_id,
        )
        await ws.send(json.dumps({
            "kind": "confirm_alert_result", "ok": True, "alert_uuid": alert_uuid, "alert": alerta,
        }))
        # difunde a todos: se dois cuidadores veem o mesmo alerta, o segundo perde o botao confirmar
        asyncio.create_task(self.broadcast({"kind": "alert_confirmed", "alert": alerta}))

    async def periodic_alert_escalation_task(self) -> None:
        """Corre _escalate_overdue_alerts() a cada ALERT_ESCALATION_CHECK_INTERVAL_S e difunde
        cada subida de nivel."""
        while True:
            try:
                for alerta in await self._escalate_overdue_alerts():
                    print(f"[BRIDGE] alerta {alerta['uuid']} escalado para "
                          f"'{alerta['escalated_to_severity']}' por falta de confirmacao")
                    await self.broadcast({"kind": "alert_escalated", "alert": alerta})
            except Exception as exc:  # noqa: BLE001 - nunca deve derrubar o bridge
                print(f"[BRIDGE] erro na task de escalonamento de alertas: {exc}")
            await asyncio.sleep(self.ALERT_ESCALATION_CHECK_INTERVAL_S)

    def _observe_wear_state(self, record: dict) -> None:
        """Alimenta o WearDetector; em mudanca de estado difunde "wear_status" e persiste um
        alerta se "removido". Chamado do callback BLE — nunca pode levantar excecao."""
        try:
            evento = self.wear_detector.observe(record)
        except Exception as exc:  # noqa: BLE001
            print(f"[BRIDGE] erro na detecao de nao-uso do dispositivo: {exc}")
            return
        if evento is None:
            return
        self._broadcast_wear_event(evento)

    def _broadcast_wear_event(self, evento: dict) -> None:
        if evento["state"] == vital_alerts.WEAR_STATE_REMOVED:
            print("[BRIDGE] RF-05: dispositivo aparentemente retirado do pulso")
            alert_uuid = self._persist_alert(
                alert_type="device_not_worn",
                severity=vital_alerts.WEAR_REMOVED_SEVERITY,
                title=vital_alerts.WEAR_REMOVED_TITLE,
                description=evento["explanation"],
                raw_data={
                    "seconds_without_skin": evento.get("seconds_without_skin"),
                    "seconds_without_motion": evento.get("seconds_without_motion"),
                },
            )
            if alert_uuid:
                evento = {**evento, "alert_uuid": alert_uuid}
        asyncio.create_task(self.broadcast({"kind": "wear_status", **evento}))

    def _on_dump_status(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        """Callback de dumpStatusChar (DumpStatusPacket, 20 bytes): type, state, reason,
        data_loss_flag (0=normal, 1=ring quase cheio, 2=ja a substituir registos), seq,
        sent_records, acked_records, ring_count (permite calcular % de progresso real)."""
        if len(data) < 20:
            return
        _type, state, reason, data_loss_flag, seq, sent, acked, ring_count = struct.unpack_from(
            "<BBBBIIII", data, 0
        )
        asyncio.create_task(self.broadcast({
            "kind": "status", "state": state, "reason": reason,
            "data_loss_flag": data_loss_flag,
            "seq": seq, "sent_records": sent, "acked_records": acked,
            "ring_count": ring_count,
        }))

    def _on_battery_level(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        """Callback da Battery Level (0x2A19, Battery Service 0x180F) — 1 byte 0-100, publicada
        a cada 60s pelo firmware (Ble::updateBatteryLevel()); percentagem aproximada."""
        if not data:
            return
        percent = data[0]
        if percent > 100:
            return  # valor implausível — ignora em vez de mostrar lixo
        asyncio.create_task(self.broadcast({"kind": "battery", "percent": percent}))

    def _on_emergency_alert(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        """Callback de emergencyAlertChar — SOS manual (3 cliques) ou queda+inatividade
        (Emergency.cpp). Reenvia de imediato, sem o limite de taxa dos registos normais."""
        if len(data) < EMERGENCY_ALERT_STRUCT.size:
            return
        alert = decode_emergency_alert(bytes(data[:EMERGENCY_ALERT_STRUCT.size]))
        print(f"[BRIDGE] ALERTA DE EMERGENCIA recebido: {alert['alert_name']} (seq={alert['seq']})")
        if self.orm:
            self.orm.insert_emergency_alert(alert)  # dedup por (device, seq)
        asyncio.create_task(self.broadcast({"kind": "emergency_alert", **alert}))
        # task separada: notificacoes externas nunca podem atrasar o broadcast acima
        asyncio.create_task(self._dispatch_emergency_notifications(alert))

    async def _dispatch_emergency_notifications(self, alert: dict) -> None:
        """Aciona o EscalationManager (notifications.py): notifica cuidador(es) + contacto de
        emergencia, escala so' se cair no horario de indisponibilidade do cuidador e nao for
        confirmado a tempo. Nunca contacta o 112 (ver notifications.py). alert_id = tipo+seq."""
        if self.escalation_manager is None:
            return
        alert_id = f"{alert['alert_type']}-{alert['seq']}"
        summary = f"{alert['alert_name']} (seq={alert['seq']})"
        try:
            await self.escalation_manager.notify_emergency(
                alert_id,
                summary,
                self.notify_caregivers,
                self.notify_emergency_contact,
                self.notify_schedule,
            )
        except Exception as exc:  # noqa: BLE001 - notificacoes externas nunca podem derrubar o bridge
            print(f"[BRIDGE] erro ao acionar notificacoes de emergencia: {exc}")

    async def _ensure_paired(self, client: BleakClient) -> None:
        """Garante bonding/pairing BLE antes de aceder as characteristics SECMODE_ENC_NO_MITM
        (ver SECURITY_STATUS.md BLE-001/002/004/006). No backend WinRT do bleak, BleakClient nao
        empareia sozinho ao ligar — read/write sem pairing previo devolve ACCESS_DENIED. Na
        primeira ligacao pode acionar o dialogo de emparelhamento do Windows. Tolerante a
        falhas: um erro aqui so' faz os reads/writes protegidos falharem depois, nao o bridge."""
        try:
            await client.pair()
            print("[BRIDGE] pairing/bonding BLE confirmado")
        except Exception as exc:  # noqa: BLE001 - pairing nunca pode derrubar o bridge
            print(f"[BRIDGE] pairing BLE falhou ou nao suportado neste backend "
                  f"(pode exigir confirmacao manual no Windows): {exc}")

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
            if not self.ble_enabled:
                # pedido do dashboard (set_ble_enabled) para largar a ligacao BLE
                while not self.ble_enabled:
                    await asyncio.sleep(1)
                continue

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
                    self.connected_device_mac = str(device.address)
                    self.current_client = client
                    # mac: permite ao dashboard confirmar que o wearable ligado e' o do paciente selecionado
                    await self.broadcast({
                        "kind": "device_status", "connected": True,
                        "mac": self.connected_device_mac,
                    })
                    # reconexao repoe o detetor em 'unknown', nunca 'worn'
                    evento_uso = self.wear_detector.on_link_restored()
                    if evento_uso:
                        await self.broadcast({"kind": "wear_status", **evento_uso})

                    # pairing/bonding antes de qualquer acesso a characteristics SECMODE_ENC_NO_MITM
                    await self._ensure_paired(client)

                    # uma entrada de auditoria por ligacao BLE (nao por registo, a ~52/s inundaria audit_log)
                    if self.orm:
                        self.orm.update_device_mac(device.address)
                        self.orm.audit(
                            action="ingestion.session_start",
                            resource_type="device",
                            resource_id=self.orm.device_id,
                            details={"address": str(device.address)},
                        )

                    await self._maybe_send_time(client)

                    # perfil de emergencia (nome, condicoes, alergias, medicacao, contacto): enviado
                    # antes dos notify() abaixo, response=True porque payload pode exceder o MTU
                    if (
                        self.orm
                        and not self.orm.disabled
                        and self.orm.session is not None
                        and self.orm.patient_id is not None
                    ):
                        try:
                            profile_payload = sa.build_emergency_profile_payload(
                                self.orm.session, self.orm.patient_id
                            )
                            await client.write_gatt_char(
                                UUID_EMERGENCY_PROFILE_WRITE, profile_payload, response=True
                            )
                            print(f"[BRIDGE] perfil de emergencia enviado ({len(profile_payload)} bytes)")
                        except Exception as exc:  # noqa: BLE001 - nao bloqueia o resto da ligacao
                            print(f"[BRIDGE] nao foi possivel enviar emergencyProfileWriteChar: {exc}")

                    # subscreve notificacoes de dados e de estado
                    await client.start_notify(UUID_DUMP_DATA, self._on_dump_data)
                    await client.start_notify(UUID_DUMP_STATUS, self._on_dump_status)
                    try:
                        await client.start_notify(UUID_EMERGENCY_ALERT, self._on_emergency_alert)
                    except Exception as exc:  # noqa: BLE001 - nao bloqueia o resto da ligacao
                        print(f"[BRIDGE] nao foi possivel subscrever emergencyAlertChar: {exc}")
                    # so existe em firmware mais recente; sem isto so' fica sem "instantaneo ao vivo"
                    try:
                        await client.start_notify(UUID_LIVE_SNAPSHOT, self._on_live_snapshot)
                        self._live_snapshot_available = True
                    except Exception as exc:  # noqa: BLE001 - nao bloqueia o resto da ligacao
                        self._live_snapshot_available = False
                        print(f"[BRIDGE] nao foi possivel subscrever liveSnapshotChar "
                              f"(normal em firmware antigo sem esta characteristic): {exc}")
                    # le o valor atual antes de subscrever (1ª notificacao periodica so' vem 60s depois)
                    try:
                        initial_battery = await client.read_gatt_char(UUID_BATTERY_LEVEL)
                        self._on_battery_level(None, initial_battery)
                        await client.start_notify(UUID_BATTERY_LEVEL, self._on_battery_level)
                    except Exception as exc:  # noqa: BLE001 - nao bloqueia o resto da ligacao
                        print(f"[BRIDGE] nivel de bateria indisponivel (normal em firmware antigo): {exc}")

                    # firmware so aceita este comando em modo de dados
                    try:
                        await client.write_gatt_char(UUID_DUMP_CTRL, DUMP_CTRL_START, response=False)
                        print("[BRIDGE] pedido de start enviado (dumpCtrlChar)")
                    except Exception as exc:  # noqa: BLE001
                        print(f"[BRIDGE] nao foi possivel pedir start agora "
                              f"(normal se ainda em provisioning): {exc}")

                    print("[BRIDGE] ligado e a receber dados. Ctrl+C para parar.")
                    while client.is_connected and self.ble_enabled:
                        await asyncio.sleep(1)
                    if not self.ble_enabled and client.is_connected:
                        print("[BRIDGE] desconexao BLE pedida pelo dashboard")
                        await client.disconnect()

            except Exception as exc:  # noqa: BLE001 - queremos reconectar em qualquer erro
                print(f"[BRIDGE] ligacao perdida/erro: {exc}")

            self.connected_device_name = None
            self.connected_device_mac = None
            self.current_client = None
            # flush do buffer pendente e auditoria de fim de sessao (par do session_start acima)
            if self.orm:
                self.orm.flush()
                self.orm.audit(
                    action="ingestion.session_end",
                    resource_type="device",
                    resource_id=self.orm.device_id,
                )
            await self.broadcast({"kind": "device_status", "connected": False, "paused": not self.ble_enabled})
            # ausencia de registos aqui e' falta de ligacao, nao evidencia de dispositivo retirado
            evento_uso = self.wear_detector.on_link_lost()
            if evento_uso:
                await self.broadcast({"kind": "wear_status", **evento_uso})
            if not self.ble_enabled:
                continue  # desligado a pedido do dashboard, nao tenta reconectar
            print("[BRIDGE] desligado — a tentar reconectar em 3s")
            await asyncio.sleep(3)

    def _check_write_rate_limit(self, name: str) -> Optional[float]:
        """None se o comando puder prosseguir (regista o instante), senão os segundos a esperar
        (sem registar nada — um cliente em loop nao pode manter o bloqueio para sempre)."""
        now = time.monotonic()
        last = self._last_write_command_monotonic.get(name, 0.0)
        elapsed = now - last
        if elapsed < self.WRITE_COMMAND_MIN_INTERVAL_S:
            return self.WRITE_COMMAND_MIN_INTERVAL_S - elapsed
        self._last_write_command_monotonic[name] = now
        return None

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

        wait_s = self._check_write_rate_limit(name)
        if wait_s is not None:
            await ws.send(json.dumps({
                "kind": "command_result", "cmd": name, "ok": False,
                "error": f"limite de taxa excedido, aguarde {wait_s:.1f}s",
            }))
            return

        try:
            # response=True (não response=False, ao contrário do START automático acima): sem
            # ACK GATT, um pacote perdido por uma quebra de ligação transitória (observado em
            # hardware real, 2026-09-17 — Windows a cancelar a escrita a meio de uma reconexão)
            # fazia este comando reportar "ok": true ao dashboard mesmo sem chegar à placa —
            # a causa exata do "force_reading por vezes silenciosamente ignorado" documentado
            # em PROJECT_STATUS.md. Com response=True, essa mesma falha levanta exceção aqui
            # (ver o except abaixo) e o dashboard recebe "ok": false em vez de um falso positivo.
            await client.write_gatt_char(UUID_DUMP_CTRL, payload, response=True)
            print(f"[BRIDGE] comando do dashboard enviado: {name}")
            await ws.send(json.dumps({"kind": "command_result", "cmd": name, "ok": True}))
            # GDPR-003 (Lote C): auditar SÓ reset_readings, e só quando
            # ACEITE (passou o rate limit e a escrita BLE teve sucesso) — é
            # a ação destrutiva/irreversível (apaga o ring buffer do
            # dispositivo). force_reading é benigno e não se audita.
            if name == "reset_readings" and self.orm:
                self.orm.audit(
                    action="device.reset_readings",
                    resource_type="device",
                    resource_id=self.orm.device_id,
                    ip=_ws_remote_ip(ws),
                user_id=self._ws_user_id(ws),
                )
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

        # autorizacao por perfil (WS_COMMAND_ROLES), antes de qualquer efeito
        if cmd in WS_COMMAND_ROLES:
            role = self._ws_user_role(ws)
            if role not in WS_COMMAND_ROLES[cmd]:
                print(f"[BRIDGE] comando '{cmd}' recusado — perfil '{role}' sem permissao")
                if self.orm is not None:
                    try:
                        self.orm.audit(
                            action="ws.command_denied",
                            resource_type="ws_command",
                            details={"cmd": cmd, "role": role},
                            ip=_ws_remote_ip(ws),
                            user_id=self._ws_user_id(ws),
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"[BRIDGE] falha a registar recusa em auditoria: {exc}")
                await ws.send(json.dumps({
                    "kind": "command_result",
                    "cmd": cmd,
                    "ok": False,
                    "error": "nao_autorizado",
                }))
                return

        if cmd in ("force_reading", "reset_readings"):
            await self.send_command(ws, cmd)
            return
        if cmd == "set_ble_enabled":
            # flag local que run_device_loop respeita; nao passa pelo rate limit de escrita
            self.ble_enabled = bool(msg.get("enabled", True))
            state = "ativada" if self.ble_enabled else "desativada"
            print(f"[BRIDGE] ligacao BLE {state} pelo dashboard")
            await ws.send(json.dumps({"kind": "command_result", "cmd": cmd, "ok": True, "enabled": self.ble_enabled}))
            return
        if cmd == "acknowledge_alert":
            # cancela o escalonamento automatico pendente; alert_id = "{alert_type}-{seq}"
            alert_id = msg.get("alert_id")
            if self.escalation_manager is None or not alert_id:
                await ws.send(json.dumps({
                    "kind": "command_result", "cmd": cmd, "ok": False,
                    "error": "escalonamento indisponivel ou alert_id em falta",
                }))
                return
            was_pending = self.escalation_manager.acknowledge(str(alert_id))
            await ws.send(json.dumps({
                "kind": "command_result", "cmd": cmd, "ok": True, "was_pending": was_pending,
            }))
            return
        if cmd == "get_alerts":
            # so' responde a quem pediu, nao broadcast (mesmo padrao de get_history)
            try:
                limit = int(msg.get("limit", self.ALERT_LIST_MAX))
            except (TypeError, ValueError):
                limit = self.ALERT_LIST_MAX
            limit = max(1, min(limit, self.ALERT_LIST_MAX))
            if not self.orm:
                await ws.send(json.dumps({
                    "kind": "alerts", "alerts": [], "error": "persistencia indisponivel",
                }))
                return
            try:
                alertas = self._list_alerts(limit)
            except Exception as exc:  # noqa: BLE001
                print(f"[BRIDGE] erro a listar alertas: {exc}")
                await ws.send(json.dumps({"kind": "alerts", "alerts": [], "error": str(exc)}))
                return
            await ws.send(json.dumps({
                "kind": "alerts", "alerts": alertas,
                "escalation_minutes": self.ALERT_ESCALATION_MINUTES,
            }))
            return
        if cmd == "get_anomalies":
            # so' responde a quem pediu, nao broadcast (mesmo padrao de get_alerts)
            try:
                limit = int(msg.get("limit", self.ALERT_LIST_MAX))
            except (TypeError, ValueError):
                limit = self.ALERT_LIST_MAX
            limit = max(1, min(limit, self.ALERT_LIST_MAX))
            if not self.orm:
                await ws.send(json.dumps({
                    "kind": "anomalies", "anomalies": [], "error": "persistencia indisponivel",
                }))
                return
            try:
                anomalias = self._list_anomalies(limit)
            except Exception as exc:  # noqa: BLE001
                print(f"[BRIDGE] erro a listar anomalias: {exc}")
                await ws.send(json.dumps({"kind": "anomalies", "anomalies": [], "error": str(exc)}))
                return
            await ws.send(json.dumps({"kind": "anomalies", "anomalies": anomalias}))
            return
        if cmd == "confirm_alert":
            # confirmar = travar escalonamento (read_at) + fechar com acao (resolved_at + nota)
            await self._handle_confirm_alert(ws, cmd, msg)
            return
        if cmd == "get_history":
            # "hours" opcional, por omissao 24h; responde so' ao cliente que pediu
            hours = msg.get("hours", 24)
            try:
                hours = float(hours)
            except (TypeError, ValueError):
                hours = 24.0
            if not self.orm:
                await ws.send(json.dumps({
                    "kind": "history", "records": [], "total_records": 0,
                    "error": "persistencia indisponivel",
                }))
                return
            try:
                records, total = self.orm.get_history(hours)
            except Exception as exc:  # noqa: BLE001
                print(f"[BRIDGE] erro a consultar historico: {exc}")
                await ws.send(json.dumps({"kind": "history", "records": [], "total_records": 0, "error": str(exc)}))
                return
            await ws.send(json.dumps({"kind": "history", "records": records, "total_records": total, "hours": hours}))
            if self.orm:
                self.orm.audit(
                    action="sensor_records.read",
                    resource_type="sensor_records",
                    details={"hours": hours},
                    ip=_ws_remote_ip(ws),
                user_id=self._ws_user_id(ws),
                )
            return
        if cmd == "get_daily_trend":
            # agregado por dia, para nao sobrecarregar o browser (ao contrario de get_history)
            days = msg.get("days", 7)
            try:
                days = float(days)
            except (TypeError, ValueError):
                days = 7.0
            if not self.orm:
                await ws.send(json.dumps({
                    "kind": "daily_trend", "days_summary": [],
                    "error": "persistencia indisponivel",
                }))
                return
            try:
                summary = self.orm.get_daily_trend(days)
            except Exception as exc:  # noqa: BLE001
                print(f"[BRIDGE] erro a agregar tendencia diaria: {exc}")
                await ws.send(json.dumps({"kind": "daily_trend", "days_summary": [], "error": str(exc)}))
                return
            await ws.send(json.dumps({"kind": "daily_trend", "days_summary": summary, "days": days}))
            if self.orm:
                self.orm.audit(
                    action="sensor_records.read_aggregate",
                    resource_type="sensor_records",
                    details={"days": days},
                    ip=_ws_remote_ip(ws),
                user_id=self._ws_user_id(ws),
                )
            return
        if cmd == "export_csv":
            # devolve o texto CSV direto; o dashboard trata do download
            hours = msg.get("hours", 24)
            try:
                hours = float(hours)
            except (TypeError, ValueError):
                hours = 24.0
            if not self.orm:
                await ws.send(json.dumps({"kind": "csv_export", "csv": "", "error": "persistencia indisponivel"}))
                return
            try:
                csv_text = self.orm.export_csv(hours)
            except Exception as exc:  # noqa: BLE001
                print(f"[BRIDGE] erro a exportar CSV: {exc}")
                await ws.send(json.dumps({"kind": "csv_export", "csv": "", "error": str(exc)}))
                return
            await ws.send(json.dumps({"kind": "csv_export", "csv": csv_text, "hours": hours}))
            if self.orm:
                self.orm.audit(
                    action="sensor_records.export",
                    resource_type="sensor_records",
                    details={"hours": hours},
                    ip=_ws_remote_ip(ws),
                user_id=self._ws_user_id(ws),
                )
            return
        if cmd == "get_retention_days":
            if self.orm:
                days = self.orm.get_retention_days()
            elif orm_persistence is not None:
                days = orm_persistence.DEFAULT_RETENTION_DAYS
            else:
                days = _FALLBACK_DEFAULT_RETENTION_DAYS
            await ws.send(json.dumps({
                "kind": "retention_days",
                "days": days,
                "default_days": orm_persistence.DEFAULT_RETENTION_DAYS if orm_persistence is not None else _FALLBACK_DEFAULT_RETENTION_DAYS,
                "min_days": orm_persistence.MIN_RETENTION_DAYS if orm_persistence is not None else _FALLBACK_MIN_RETENTION_DAYS,
                "max_days": orm_persistence.MAX_RETENTION_DAYS if orm_persistence is not None else _FALLBACK_MAX_RETENTION_DAYS,
            }))
            return
        if cmd == "set_retention_days":
            wait_s = self._check_write_rate_limit(cmd)
            if wait_s is not None:
                await ws.send(json.dumps({
                    "kind": "retention_days_result", "ok": False,
                    "error": f"limite de taxa excedido, aguarde {wait_s:.1f}s",
                }))
                return
            if not self.orm:
                await ws.send(json.dumps({"kind": "retention_days_result", "ok": False, "error": "persistencia indisponivel"}))
                return
            days = msg.get("days")
            try:
                saved = self.orm.set_retention_days(days)
            except (TypeError, ValueError) as exc:
                await ws.send(json.dumps({"kind": "retention_days_result", "ok": False, "error": str(exc)}))
                return
            except Exception as exc:  # noqa: BLE001
                print(f"[BRIDGE] erro a gravar retencao configurada: {exc}")
                await ws.send(json.dumps({"kind": "retention_days_result", "ok": False, "error": str(exc)}))
                return
            print(f"[BRIDGE] retencao configurada pelo dashboard: {saved} dias")
            await ws.send(json.dumps({"kind": "retention_days_result", "ok": True, "days": saved}))
            if self.orm:
                self.orm.audit(
                    action="retention.write",
                    resource_type="settings",
                    details={"days": saved},
                    ip=_ws_remote_ip(ws),
                user_id=self._ws_user_id(ws),
                )
            return
        if cmd == "get_consent_status":
            if not self.orm:
                await ws.send(json.dumps({"kind": "consent_status", "status": {}, "error": "persistencia indisponivel"}))
                return
            try:
                status = self.orm.get_consent_status()
            except Exception as exc:  # noqa: BLE001
                await ws.send(json.dumps({"kind": "consent_status", "status": {}, "error": str(exc)}))
                return
            await ws.send(json.dumps({"kind": "consent_status", "status": status}))
            return
        if cmd == "set_consent":
            wait_s = self._check_write_rate_limit(cmd)
            if wait_s is not None:
                await ws.send(json.dumps({
                    "kind": "consent_result", "ok": False,
                    "error": f"limite de taxa excedido, aguarde {wait_s:.1f}s",
                }))
                return
            if not self.orm:
                await ws.send(json.dumps({"kind": "consent_result", "ok": False, "error": "persistencia indisponivel"}))
                return
            scope = msg.get("scope")
            granted = bool(msg.get("granted"))
            representative_name = msg.get("representative_name")
            representative_relationship = msg.get("representative_relationship")
            try:
                result = self.orm.set_consent(
                    scope, granted,
                    representative_name=representative_name,
                    representative_relationship=representative_relationship,
                )
            except ValueError as exc:
                await ws.send(json.dumps({"kind": "consent_result", "ok": False, "error": str(exc)}))
                return
            except Exception as exc:  # noqa: BLE001
                print(f"[BRIDGE] erro a gravar consentimento: {exc}")
                await ws.send(json.dumps({"kind": "consent_result", "ok": False, "error": str(exc)}))
                return
            print(f"[BRIDGE] consentimento atualizado pelo dashboard: {scope}={granted}")
            await ws.send(json.dumps({"kind": "consent_result", "ok": True, **result}))
            return
        if cmd == "get_thresholds":
            thresholds = self.orm.get_thresholds() if self.orm else dict(
                sa.DEFAULT_THRESHOLDS, is_default=True, updated_at=None
            ) if sa is not None else {"is_default": True, "updated_at": None}
            await ws.send(json.dumps({"kind": "thresholds", "thresholds": thresholds}))
            return
        if cmd == "set_thresholds":  # atualizacao parcial, ex.: {"heart_rate_max": 110}
            wait_s = self._check_write_rate_limit(cmd)
            if wait_s is not None:
                await ws.send(json.dumps({
                    "kind": "thresholds_result", "ok": False,
                    "error": f"limite de taxa excedido, aguarde {wait_s:.1f}s",
                }))
                return
            if not self.orm:
                await ws.send(json.dumps({"kind": "thresholds_result", "ok": False, "error": "persistencia indisponivel"}))
                return
            fields = {k: v for k, v in msg.items() if k not in ("cmd",)}
            try:
                result = self.orm.set_thresholds(**fields)
            except (ValueError, TypeError) as exc:
                await ws.send(json.dumps({"kind": "thresholds_result", "ok": False, "error": str(exc)}))
                return
            except Exception as exc:  # noqa: BLE001
                print(f"[BRIDGE] erro a gravar limiares personalizados: {exc}")
                await ws.send(json.dumps({"kind": "thresholds_result", "ok": False, "error": str(exc)}))
                return
            print(f"[BRIDGE] limiares personalizados atualizados pelo dashboard: {fields}")
            await ws.send(json.dumps({"kind": "thresholds_result", "ok": True, "thresholds": result}))
            return
        if cmd == "list_model_versions":  # sem parametros: sempre sobre ML_MODEL_NAME
            if sa is None:
                await ws.send(json.dumps({
                    "kind": "model_versions", "versions": [],
                    "error": "persistencia indisponivel",
                }))
                return
            db = sa.get_db_session()
            try:
                versions = sa.list_model_versions(db, ML_MODEL_NAME)
            except Exception as exc:  # noqa: BLE001
                print(f"[BRIDGE] erro a listar versoes do modelo: {exc}")
                await ws.send(json.dumps({"kind": "model_versions", "versions": [], "error": str(exc)}))
                return
            finally:
                db.close()
            await ws.send(json.dumps({"kind": "model_versions", "versions": versions}))
            return
        if cmd == "activate_model_version":  # troca a versao ativa em runtime, sem reiniciar o bridge
            wait_s = self._check_write_rate_limit(cmd)
            if wait_s is not None:
                await ws.send(json.dumps({
                    "kind": "model_version_result", "ok": False,
                    "error": f"limite de taxa excedido, aguarde {wait_s:.1f}s",
                }))
                return
            if sa is None:
                await ws.send(json.dumps({"kind": "model_version_result", "ok": False, "error": "persistencia indisponivel"}))
                return
            version = msg.get("version")
            if version is None:
                await ws.send(json.dumps({"kind": "model_version_result", "ok": False, "error": "parametro 'version' em falta"}))
                return
            db = sa.get_db_session()
            try:
                activated = sa.activate_model_version(db, ML_MODEL_NAME, version)
            except ValueError as exc:  # versao inexistente, erro do chamador
                await ws.send(json.dumps({"kind": "model_version_result", "ok": False, "version": version, "error": str(exc)}))
                return
            except Exception as exc:  # noqa: BLE001
                print(f"[BRIDGE] erro a ativar versao do modelo: {exc}")
                await ws.send(json.dumps({"kind": "model_version_result", "ok": False, "version": version, "error": str(exc)}))
                return
            finally:
                db.close()
            # ativacao na BD ja teve sucesso; reload_active_model troca o modelo em memoria so'
            # se activity_inference existir (senao a ativacao na BD fica valida na mesma)
            reloaded = False
            note = None
            if self.activity_inference is not None:
                reloaded = self.activity_inference.reload_active_model()
                if not reloaded:
                    note = ("versao ativada na base de dados, mas falhou o recarregamento "
                            "em tempo real (ver load_error) -- o modelo anterior continua em uso")
            else:
                note = "versao ativada na base de dados; classificacao em tempo real indisponivel nesta instancia"
            print(f"[BRIDGE] versao do modelo ativada pelo dashboard: {ML_MODEL_NAME}={version} (reloaded={reloaded})")
            response = {
                "kind": "model_version_result", "ok": True,
                "version": activated["version"], "reloaded": reloaded,
            }
            if note:
                response["note"] = note
            await ws.send(json.dumps(response))
            if self.orm:
                self.orm.audit(
                    action="ml_model.activate_version",
                    resource_type="ml_model_version",
                    details={"model_name": ML_MODEL_NAME, "version": version, "reloaded": reloaded},
                    ip=_ws_remote_ip(ws),
                user_id=self._ws_user_id(ws),
                )
            return
        if cmd == "get_episode_timeline":
            # junta sinais vitais, blocos de atividade e alertas proximos a um EmergencyAlert
            if not self.orm:
                await ws.send(json.dumps({
                    "kind": "episode_timeline", "timeline": None,
                    "error": "persistencia indisponivel",
                }))
                return
            sequence_number = msg.get("sequence_number")
            window_minutes = msg.get("window_minutes", 30)
            try:
                window_minutes = float(window_minutes)
            except (TypeError, ValueError):
                window_minutes = 30.0
            try:
                timeline = self.orm.get_episode_timeline(sequence_number, window_minutes)
            except ValueError as exc:
                # Alerta (device_id, sequence_number) não encontrado — erro
                # do chamador (sequence_number errado/inexistente), distinto
                # de uma falha de infraestrutura.
                await ws.send(json.dumps({
                    "kind": "episode_timeline", "timeline": None,
                    "error": f"alerta nao encontrado: {exc}",
                }))
                return
            except Exception as exc:  # noqa: BLE001
                print(f"[BRIDGE] erro a construir timeline do episodio: {exc}")
                await ws.send(json.dumps({
                    "kind": "episode_timeline", "timeline": None,
                    "error": str(exc),
                }))
                return
            await ws.send(json.dumps({"kind": "episode_timeline", "timeline": timeline}))
            if self.orm:
                self.orm.audit(
                    action="episode_timeline.read",
                    resource_type="emergency_alerts",
                    details={"sequence_number": sequence_number, "window_minutes": window_minutes},
                    ip=_ws_remote_ip(ws),
                user_id=self._ws_user_id(ws),
                )
            return
        if cmd == "correct_activity":  # correcao manual a classificacao de atividade da IA
            wait_s = self._check_write_rate_limit(cmd)
            if wait_s is not None:
                await ws.send(json.dumps({
                    "kind": "command_result", "cmd": cmd, "ok": False,
                    "error": f"limite de taxa excedido, aguarde {wait_s:.1f}s",
                }))
                return
            category = msg.get("category")
            if category not in ACTIVITY_CORRECTION_CATEGORIES:
                await ws.send(json.dumps({
                    "kind": "command_result", "cmd": cmd, "ok": False,
                    "error": "categoria desconhecida",
                }))
                return
            original_category = (
                self.activity_inference.current_category()
                if self.activity_inference is not None else None
            )
            if not self.orm:
                await ws.send(json.dumps({"kind": "command_result", "cmd": cmd, "ok": False, "error": "persistencia indisponivel"}))
                return
            try:
                self.orm.insert_activity_correction(original_category, category)
            except Exception as exc:  # noqa: BLE001
                print(f"[BRIDGE] erro a gravar correcao de atividade: {exc}")
                await ws.send(json.dumps({"kind": "command_result", "cmd": cmd, "ok": False, "error": str(exc)}))
                return
            print(f"[BRIDGE] atividade corrigida pelo dashboard: {original_category!r} -> {category!r}")
            if self.orm:
                self.orm.audit(
                    action="activity.correct",
                    resource_type="activity_classification",
                    details={"original_category": original_category, "corrected_category": category},
                    ip=_ws_remote_ip(ws),
                user_id=self._ws_user_id(ws),
                )
            # difundido a todos: todas as vistas ao vivo devem refletir a mesma correcao
            asyncio.create_task(self.broadcast({
                "kind": "activity_correction",
                "category": category,
                "original_category": original_category,
                "corrected_at": time.time(),
            }))
            return

    async def ws_handler(self, ws: "websockets.ServerConnection") -> None:
        self.ws_clients.add(ws)
        self.ws_user_ids[ws] = getattr(ws, "_carewear_user_id", None)
        self.ws_user_roles[ws] = getattr(ws, "_carewear_user_role", None)
        print(f"[BRIDGE] dashboard ligado via WebSocket ({len(self.ws_clients)} ativo(s))")
        await ws.send(json.dumps({
            "kind": "device_status",
            "connected": self.connected_device_name is not None,
            "mac": self.connected_device_mac,
        }))
        try:
            async for raw_message in ws:
                await self.handle_dashboard_command(ws, raw_message)
        finally:
            self.ws_clients.discard(ws)
            self.ws_user_ids.pop(ws, None)
            self.ws_user_roles.pop(ws, None)
            print(f"[BRIDGE] dashboard desligado ({len(self.ws_clients)} ativo(s))")

    def _ws_user_id(self, ws) -> Optional[int]:
        # dicionario preenchido por ws_handler(); atributo existe desde o handshake, antes disso
        if ws in self.ws_user_ids:
            return self.ws_user_ids[ws]
        return getattr(ws, "_carewear_user_id", None)

    def _ws_user_role(self, ws) -> Optional[str]:
        # mesma dupla fonte de _ws_user_id() acima
        if ws in self.ws_user_roles:
            return self.ws_user_roles[ws]
        return getattr(ws, "_carewear_user_role", None)

async def main() -> None:
    bridge = BleBridge()
    ssl_context = _build_ssl_context()
    server = await websockets.serve(
        bridge.ws_handler, WS_HOST, WS_PORT, ssl=ssl_context,
        process_request=_ws_process_request,
    )
    scheme = "wss" if ssl_context else "ws"
    print(f"[BRIDGE] WebSocket a ouvir em {scheme}://{WS_HOST}:{WS_PORT}"
          + ("" if ssl_context else " (sem TLS — ver CAREWEAR_WS_TLS em ble_bridge.py)")
          + ("" if WS_TOKEN else " (sem CAREWEAR_WS_TOKEN — WS-001 continua aberto)"))
    async with server:
        asyncio.create_task(bridge.periodic_retention_task())
        asyncio.create_task(bridge.periodic_orm_retention_task())
        # em paralelo com o ciclo BLE: um alerta tem de escalar mesmo com o wearable desligado
        asyncio.create_task(bridge.periodic_alert_escalation_task())
        await bridge.run_device_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[BRIDGE] terminado pelo utilizador")
