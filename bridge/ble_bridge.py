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
  4. Remonta os fragmentos de cada registo (FullPlain, 39 bytes) e reenvia
     cada registo já descodificado, em JSON, a todos os clientes WebSocket
     ligados a este script (por omissão, ws://localhost:8765).
  5. Subscreve também emergencyAlertChar (módulo firmware Emergency — SOS
     manual ou queda+inatividade confirmada) e reencaminha o alerta de
     imediato para o dashboard, sem passar pelo limite de taxa dos
     registos normais. Desde 2026-07-17 também aciona `notifications.py`
     (ver `_dispatch_emergency_notifications`/`EscalationManager`) para
     notificar o(s) cuidador(es) + o contacto de emergência do paciente
     por SMS/email (Twilio/SendGrid), e escalar ao contacto de emergência
     se o alerta não for confirmado dentro do prazo E cair dentro do
     horário declarado de indisponibilidade do cuidador — NUNCA contacta
     o 112 ou qualquer serviço de emergência real (ver a "DECISÃO
     DELIBERADA SOBRE O 112" no cabeçalho de notifications.py). Precisa de
     `CAREWEAR_TWILIO_*`/`CAREWEAR_SENDGRID_*` + `CAREWEAR_CAREGIVER_*`/
     `CAREWEAR_EMERGENCY_CONTACT_*` no ambiente (ver
     `_load_notification_recipients_from_env` abaixo); sem isso, degrada
     para um aviso no log, nunca finge notificar nem bloqueia o alerta ao
     dashboard.

CIFRA AES-CTR DO "MODO DE DADOS" (2026-07-07)
----------------------------------------------
Até 2026-07-07 o registo transmitido no "modo de dados" ia em texto
simples pelo ar, apesar de o dispositivo já trocar e guardar uma chave
AES — ver PROJECT_STATUS.md para o histórico. Isso deixou de ser verdade:
o firmware (src/Ble/Ble.cpp, `encryptRecord()`) cifra agora cada FullPlain
com AES-CTR (128/192/256 bits, conforme o comprimento da chave) antes de
fragmentar, usando um nonce de 32 bits por registo (campo novo "nonce" em
DumpDataPacket) derivado de um contador persistente dedicado no firmware
(ver allocateNonce()/reserveNonceBatch() em src/Ble/Ble.cpp) — nunca
reutilizado enquanto a chave não mudar.

Este script decifra usando a MESMA chave, mas **NÃO existe (ainda) nenhuma
app de provisioning que entregue essa chave ao bridge de forma automática
e segura** — só o dispositivo a recebe hoje (via nRF Connect/app manual,
characteristic aesKeyChar, escrita única). Solução honesta desta fase,
adequada a um protótipo local (o bridge já assume confiança total do
ambiente onde corre — "Canal não autenticado — só deve ser exposto em
localhost", ver PROJECT_STATUS.md): quem faz o provisioning do dispositivo
configura o bridge com a MESMA chave (em hexadecimal) através da variável
de ambiente `CAREWEAR_AES_KEY_HEX`. Sem essa variável definida, o bridge
não consegue decifrar os registos — regista um aviso (uma vez) e
descarta-os em vez de os interpretar como texto simples (o que produziria
valores de sensores fabricados/sem sentido, ver `_on_dump_data`).

    export CAREWEAR_AES_KEY_HEX=<32/48/64 caracteres hex = 16/24/32 bytes>
    python ble_bridge.py

**Limitação honesta, por resolver numa fase futura**: isto não é uma troca
de chaves segura (Diffie-Hellman ou semelhante) nem uma app de
provisioning real — é a forma mais simples e honesta de fechar o ciclo
com o que já existe hoje neste protótipo. Não testado com o firmware real
em hardware (bloqueado pela indisponibilidade atual da placa — ver
PROJECT_STATUS.md, "Riscos/bloqueios ativos"); o protocolo de
cifra/decifra foi validado byte a byte com um script Python à parte
(round-trip determinístico), não com o par firmware↔bridge real.

DEPENDÊNCIAS
-------------
    pip install bleak websockets pycryptodome

UTILIZAÇÃO
----------
    python ble_bridge.py
    # depois abrir web/dashboard/index.html num browser; a página tenta
    # ligar-se sozinha a ws://localhost:8765.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import os
import ssl
import struct
import time
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Optional

import websockets
from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from Crypto.Cipher import AES

import storage

try:
    # Dual-write transitório do Lote C (ver comentário em __init__ abaixo).
    # sqlalchemy/argon2-cffi (arrastados por orm_persistence -> storage_advanced
    # -> crypto_utils) só estão em requirements_db.txt, não no requirements.txt
    # mínimo usado por start_carewear.bat — por isso este import nunca pode
    # impedir `import ble_bridge` de suceder numa instalação mínima.
    import orm_persistence
except ImportError as exc:
    print(f"[BRIDGE] AVISO: modulo orm_persistence indisponivel ({exc}); dual-write desativado")
    orm_persistence = None

try:
    # Notificações externas de alertas de emergência (SMS/email + escalonamento,
    # ver notifications.py) — não deve exigir twilio/sendgrid instalados só
    # para importar ble_bridge.py (notifications.py já faz esse import tardio
    # lá dentro, só quando as credenciais estão de facto configuradas); este
    # try/except cobre só o caso (improvável) de o próprio ficheiro faltar.
    import notifications
except ImportError as exc:
    print(f"[BRIDGE] AVISO: modulo notifications indisponivel ({exc}); notificacoes de emergencia desativadas")
    notifications = None

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

WS_HOST = "localhost"
WS_PORT = 8765

# ============================================================
# TLS OPCIONAL NO WEBSOCKET (GDPR-004, ver SECURITY_STATUS.md)
# ------------------------------------------------------------
# Por omissão o canal bridge<->dashboard continua em ws:// (texto
# simples) — WS_HOST está fixado em "localhost", o que já limita o
# risco a outros processos/utilizadores da mesma máquina (ver
# SECURITY_STATUS.md). Ativar TLS aqui exige TAMBÉM mudar
# `WS_URL` para "wss://localhost:8765" em web/dashboard/index.html
# E aceitar manualmente o certificado autoassinado no browser uma vez
# (visitar https://localhost:8765 diretamente e confirmar o aviso de
# segurança) — sem isso a ligação WSS falha silenciosamente e o
# dashboard mostra "sem ligação ao bridge". Por esta fricção de UX não
# documentada como "resolvida sozinha", TLS fica opt-in por agora, não
# ligado por omissão.
# ============================================================
WS_TLS_ENABLED = os.environ.get("CAREWEAR_WS_TLS", "0") == "1"
WS_TLS_CERT_PATH = Path(__file__).parent / "tls_cert.pem"
WS_TLS_KEY_PATH = Path(__file__).parent / "tls_key.pem"


def _ensure_tls_cert() -> None:
    """Gera um certificado autoassinado para localhost/127.0.0.1 se ainda
    não existir (nunca reescreve um já gerado — evita invalidar um
    certificado já aceite manualmente no browser). Válido 10 anos porque
    serve só para cifrar o transporte num canal já restrito a localhost,
    não para provar identidade a terceiros."""
    if WS_TLS_CERT_PATH.exists() and WS_TLS_KEY_PATH.exists():
        return
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "carewear-bridge-local")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                x509.IPAddress(ipaddress.ip_address("::1")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    WS_TLS_KEY_PATH.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    WS_TLS_CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"[BRIDGE] certificado TLS autoassinado gerado em {WS_TLS_CERT_PATH}")


def _build_ssl_context() -> Optional[ssl.SSLContext]:
    if not WS_TLS_ENABLED:
        return None
    _ensure_tls_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(WS_TLS_CERT_PATH), keyfile=str(WS_TLS_KEY_PATH))
    return ctx

# ============================================================
# CIFRA AES-CTR (ver cabecalho do ficheiro, "CIFRA AES-CTR DO MODO DE
# DADOS") — chave lida uma unica vez do ambiente, em hexadecimal, tem de
# ser EXATAMENTE a mesma chave escrita no dispositivo via aesKeyChar
# durante o provisioning (16/24/32 bytes = 32/48/64 caracteres hex).
# ============================================================
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


# ============================================================
# NOTIFICAÇÕES EXTERNAS DE EMERGÊNCIA (SMS/email, ver notifications.py) —
# mesma convenção de configuração por variável de ambiente usada acima para
# a chave AES e o TLS opcional. As credenciais Twilio/SendGrid propriamente
# ditas (CAREWEAR_TWILIO_*, CAREWEAR_SENDGRID_*, CAREWEAR_NOTIFY_FROM_EMAIL,
# CAREWEAR_ESCALATION_TIMEOUT_MIN) são lidas diretamente por
# notifications.py — aqui só carregamos QUEM notificar (cuidador(es) +
# contacto de emergência) e QUANDO o cuidador está tipicamente indisponível
# (ScheduleWindow), porque isso é específico de cada instalação/paciente,
# não do provedor de SMS/email. Deliberadamente por variáveis de ambiente
# (não um novo ficheiro de configuração) para não introduzir mais uma forma
# de configurar o bridge além da já existente.
# ============================================================
_CAREGIVER_NAME_ENV = "CAREWEAR_CAREGIVER_NAME"
_CAREGIVER_PHONE_ENV = "CAREWEAR_CAREGIVER_PHONE"
_CAREGIVER_EMAIL_ENV = "CAREWEAR_CAREGIVER_EMAIL"
# NOTA: o contacto de emergência é sempre uma PESSOA (ex.: vizinho/familiar),
# nunca um número de emergência real — ver a "DECISÃO DELIBERADA SOBRE O
# 112" no cabeçalho de notifications.py. Só tem telefone no esquema atual
# (Patient.emergency_contact_*), sem coluna de email, mas aceitamos
# CAREWEAR_EMERGENCY_CONTACT_EMAIL na mesma para não bloquear instalações
# futuras que queiram configurar um email também.
_EMERGENCY_CONTACT_NAME_ENV = "CAREWEAR_EMERGENCY_CONTACT_NAME"
_EMERGENCY_CONTACT_PHONE_ENV = "CAREWEAR_EMERGENCY_CONTACT_PHONE"
_EMERGENCY_CONTACT_EMAIL_ENV = "CAREWEAR_EMERGENCY_CONTACT_EMAIL"
# JSON: lista de {"weekday": 0-6 (0=segunda, ver datetime.weekday()),
# "start": "HH:MM", "end": "HH:MM"}. Ex.:
#   CAREWEAR_CAREGIVER_SCHEDULE_JSON='[{"weekday":0,"start":"08:00","end":"17:00"}]'
_CAREGIVER_SCHEDULE_ENV = "CAREWEAR_CAREGIVER_SCHEDULE_JSON"


def _load_notification_recipients_from_env():
    """Lê do ambiente quem notificar num alerta de emergência real. Sem
    NENHUMA variável definida, devolve ([], None, None) — o bridge continua
    a funcionar normalmente, só que `_dispatch_emergency_notifications` não
    tem ninguém para notificar (regista um aviso uma vez, ver __init__).
    Nunca levanta exceção: um horário mal formado (JSON inválido, chave em
    falta) só descarta esse horário especificamente — nunca impede o resto
    da configuração de carregar nem o arranque do bridge."""
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
    """Decifra um registo FullPlain (39 bytes) cifrado pelo firmware com
    AES-CTR (ver encryptRecord() em src/Ble/Ble.cpp) — reproduz o MESMO
    desenho de contador ali usado, byte a byte:

      IV de 16 bytes = [nonce de 32 bits, big-endian (4 bytes)]
                     + [0x00000000 (4 bytes)]
                     + [contador de bloco de 8 bytes, comeca em 0]

    so' os ultimos 8 bytes do IV incrementam entre blocos de 16 bytes
    (equivalente a CTR.setCounterSize(8) no firmware); os primeiros 8
    bytes ficam fixos como prefixo desta mensagem. Cada bloco de
    keystream e' AES_ECB(chave, bloco_contador); a cifra e' um XOR simples
    entre o texto cifrado e o keystream — por isso decifrar e' a MESMA
    operacao que cifrar (propriedade do modo CTR).

    Implementado com AES em modo ECB "cru" (bloco a bloco), em vez de um
    modo CTR de alto nivel de alguma biblioteca Python, precisamente para
    controlar byte a byte a construcao do bloco de contador e garantir que
    bate certo com o firmware sem depender de convencoes de
    endianness/prefixo que podem diferir entre bibliotecas.
    """
    aes = AES.new(key, AES.MODE_ECB)
    counter = bytearray(16)
    counter[0] = (nonce >> 24) & 0xFF
    counter[1] = (nonce >> 16) & 0xFF
    counter[2] = (nonce >> 8) & 0xFF
    counter[3] = nonce & 0xFF
    # counter[4:8] fica a zero (resto do prefixo fixo); counter[8:16]
    # (contador de bloco) tambem comeca a zero.

    out = bytearray(len(ciphertext))
    offset = 0
    while offset < len(ciphertext):
        keystream_block = aes.encrypt(bytes(counter))
        take = min(16, len(ciphertext) - offset)
        for i in range(take):
            out[offset + i] = ciphertext[offset + i] ^ keystream_block[i]
        offset += take

        # Incrementa counter[8:16] como um inteiro big-endian (byte 15 e'
        # o menos significativo), sem propagar carry para o prefixo
        # (counter[0:8]) — mesma logica do CTR.cpp do firmware.
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
        # spo2/hr chegam como 0 quando não há leitura nova nesse instante
        # (ver storageTask em main.cpp) — o dashboard deve ignorar zeros.
        "spo2": spo2 if spo2 != 0 else None,
        "hr": hr if hr != 0 else None,
        # Indice 0-100 de "pacing"/curvas apertadas via giroscopio (ver
        # Imu::detectPacing em Imu.cpp) — 0 e' um valor real possivel (sem
        # curvas apertadas na ultima janela), nao um sentinela de "sem
        # leitura" como spo2/hr, por isso nao e' convertido para None aqui.
        "pacing_index": pacing_index,
    }


# Limites de plausibilidade física para um registo FullPlain decifrado
# (2026-07-07, correcao de bug reportado pelo utilizador: dashboard a
# mostrar FC/SpO2/aceleracao "malucos", a variar em loop). Causa raiz
# confirmada: a chave AES em device_key.env ja nao bate certo com a
# chave gravada na flash do dispositivo (aesKeyChar so aceita a
# primeira escrita — um reprovisionamento nao teve efeito porque a
# flash ja tinha uma chave guardada), por isso decrypt_full_plain()
# produz, na pratica, ruido aleatorio reinterpretado como floats/ints.
# Nao ha' forma de corrigir a chave remotamente sem apagar a flash do
# dispositivo (acao destrutiva, decisao do utilizador) — por isso este
# filtro so' pode REJEITAR o lixo antes de chegar ao dashboard/BD, nunca
# "corrigir" os valores. Limites com folga generosa acima de qualquer
# leitura humana plausivel (não são limiares clínicos).
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
    return {
        "alert_type": alert_type,
        "alert_name": EMERGENCY_ALERT_TYPE_NAMES.get(alert_type, "desconhecido"),
        "seq": seq,
        "timestamp_utc": timestamp_utc,
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
        # Ligação à base de dados local (SQLite, ver storage.py) — aberta
        # uma única vez no arranque do bridge, reutilizada para todos os
        # inserts/queries desta execução.
        self.db = storage.init_db()
        # Segundo destino de escrita (ORM, storage_advanced.py) do
        # dual-write transitório do Lote C — ver orm_persistence.py. É
        # SECUNDÁRIO: storage.py (self.db acima) continua a ser o caminho
        # primário de TODAS as leituras do dashboard. Construído dentro de
        # try/except porque uma falha aqui (BD ORM indisponível, esquema em
        # migração, etc.) nunca deve impedir o bridge de arrancar nem de
        # persistir via storage.py — degrada para None e continua.
        self.orm = None
        if orm_persistence is not None:
            try:
                self.orm = orm_persistence.OrmPersistence()
            except Exception as exc:  # noqa: BLE001 - dual-write nunca derruba o arranque
                print(f"[BRIDGE] AVISO: persistencia ORM (dual-write) indisponivel: {exc}")
                self.orm = None
        # Chave AES para decifrar o "modo de dados" (ver
        # "CIFRA AES-CTR DO MODO DE DADOS" no cabeçalho deste ficheiro) —
        # None se CAREWEAR_AES_KEY_HEX não estiver configurada.
        self.aes_key = _load_aes_key_from_env()
        self._missing_key_warned = False
        self._implausible_record_warned = False
        # Notificações externas de alertas de emergência REAIS (SMS/email +
        # escalonamento condicional ao contacto de emergência — ver
        # notifications.py e _dispatch_emergency_notifications abaixo).
        # Mesma lógica de degradação do self.orm acima: uma instância por
        # processo do bridge; se notifications.py não estiver disponível
        # (import falhou, ver topo do ficheiro) ou a configuração do
        # timeout de escalonamento for inválida, self.escalation_manager
        # fica None e _on_emergency_alert simplesmente não notifica
        # ninguém — nunca impede o arranque do bridge nem o broadcast do
        # alerta ao dashboard, que é o caminho crítico de segurança.
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
        # Ver WRITE_COMMAND_MIN_INTERVAL_S abaixo — ultimo instante
        # (time.monotonic()) em que cada comando de escrita foi aceite,
        # por nome de comando. Global (nao por-cliente WebSocket) de
        # proposito: varios separadores do dashboard ligados ao mesmo
        # bridge partilham o mesmo dispositivo BLE fisico, por isso um
        # limite por-cliente seria trivial de contornar abrindo outra
        # ligacao WebSocket.
        self._last_write_command_monotonic: dict[str, float] = {}
        # Controlo manual da ligacao BLE (pedido do dashboard, ver
        # handle_dashboard_command "set_ble_enabled"). True = run_device_loop
        # procura/mantem a ligacao normalmente (comportamento de sempre).
        # False = o dashboard pediu para largar a ligacao ao wearable (ex.:
        # libertar o radio/porta serie para outra ferramenta, gravar
        # firmware novo, ou so' parar de transmitir sinais vitais por
        # privacidade) — run_device_loop desliga o cliente atual se estiver
        # ligado e para de tentar reconectar ate' voltar a True. Nao afeta o
        # WebSocket dashboard<->bridge, que continua ligado normalmente.
        self.ble_enabled = True

    RECORD_BROADCAST_MIN_INTERVAL_S = 0.25  # no maximo ~4 atualizacoes/seg
    # Intervalo entre limpezas automaticas de sensor_records (ver
    # storage.purge_old_sensor_records) - nao precisa de ser frequente,
    # e' so' para o ficheiro .db nao crescer sem limite num bridge deixado
    # a correr por muito tempo.
    RETENTION_CHECK_INTERVAL_S = 6 * 3600
    # *** LIMITE DE TAXA PARA COMANDOS DE ESCRITA DO DASHBOARD ***
    # (2026-07-08, rotina de auditoria de segurança). O canal WebSocket nao
    # e' autenticado (ver docstring do modulo/handle_dashboard_command) —
    # ate agora so' o broadcast de leitura tinha um limite de taxa
    # (RECORD_BROADCAST_MIN_INTERVAL_S); os comandos de escrita
    # (force_reading, reset_readings, set_retention_days) podiam ser
    # enviados em loop sem nenhuma limitacao. O caso mais grave e'
    # "reset_readings": destroi de forma irreversivel os registos guardados
    # no ring buffer do dispositivo (ver DUMP_CTRL_RESET_READINGS acima) —
    # qualquer processo local com acesso ao WebSocket podia apagar o
    # historico do wearable repetidamente, sem limite, so' por enviar a
    # mesma mensagem JSON em loop. Isto nao substitui autenticacao (fora do
    # ambito desta correcao — ver PROJECT_STATUS.md/SECURITY_STATUS.md),
    # mas reduz o dano de um cliente descontrolado/malicioso na rede local.
    WRITE_COMMAND_MIN_INTERVAL_S = 2.0
    # BUG CORRIGIDO (2026-07-07, rotina cloud): um registo cujos fragmentos
    # BLE se percam (notify() nao e' um transporte com confirmacao) ficava
    # para sempre em _pending_fragments — nunca recebia todos os
    # fragmentos, por isso nunca era removido em _on_dump_data. A ~14-52
    # registos/seg, mesmo uma perda de pacotes pequena acumula milhares de
    # entradas orfas numa sessao de varias horas (fuga de memoria real num
    # processo pensado para correr continuamente). Qualquer entrada mais
    # velha do que isto e' considerada perdida e descartada.
    PENDING_FRAGMENT_TIMEOUT_S = 5.0

    async def periodic_retention_task(self) -> None:
        """Aplica a politica de retencao (ver storage.py,
        get_retention_days/set_retention_days) uma vez no arranque e
        depois a cada RETENTION_CHECK_INTERVAL_S enquanto o bridge estiver
        a correr. So' apaga sensor_records - o registo de emergencias
        nunca e' apagado automaticamente. Le' o valor configurado a cada
        ciclo (em vez de o guardar numa variavel) para uma alteracao feita
        pelo dashboard a meio da execucao (ver handle_dashboard_command,
        comando set_retention_days) ter efeito no proximo ciclo sem
        precisar de reiniciar o bridge."""
        while True:
            try:
                days = storage.get_retention_days(self.db)
                deleted = storage.purge_old_sensor_records(self.db, days=days)
                if deleted:
                    print(f"[BRIDGE] retencao: apagados {deleted} registos de sensores "
                          f"com mais de {days} dias")
                # Dual-write (Lote C): aplica a MESMA retenção configurável
                # ao ORM (usa `days`, não os 365d fixos das RETENTION_POLICIES).
                if self.orm:
                    self.orm.purge(days)
            except Exception as exc:  # noqa: BLE001 - nunca deve derrubar o bridge
                print(f"[BRIDGE] erro na limpeza de retencao: {exc}")
            await asyncio.sleep(self.RETENTION_CHECK_INTERVAL_S)

    async def broadcast(self, payload: dict) -> None:
        if not self.ws_clients:
            return
        message = json.dumps(payload)
        # Envia a todos os clientes ligados; remove os que já desligaram.
        # BUG CORRIGIDO (2026-07-07, rotina cloud): iterar diretamente sobre
        # self.ws_clients (um set partilhado) enquanto este método está
        # suspenso num `await ws.send(...)` corria a par de ws_handler a
        # fazer add()/discard() no mesmo set (ligação/desligação de outro
        # separador do dashboard a meio de um broadcast) — "RuntimeError:
        # Set changed size during iteration", reproduzido diretamente.
        # Iterar sobre uma cópia (`list(...)`) torna o broadcast imune a
        # mutações concorrentes do set original.
        dead = set()
        for ws in list(self.ws_clients):
            try:
                await ws.send(message)
            except websockets.exceptions.ConnectionClosed:
                dead.add(ws)
        self.ws_clients -= dead

    def _prune_stale_fragments(self) -> None:
        """BUG CORRIGIDO (2026-07-07, rotina cloud): entradas de
        _pending_fragments para registos com um ou mais fragmentos BLE
        perdidos (notify() não tem confirmação/retransmissão) nunca eram
        removidas — só o eram quando TODOS os fragmentos chegavam. A
        ~14-52 registos/seg, mesmo uma perda de pacotes pequena acumulava
        milhares de entradas órfãs numa sessão de várias horas (fuga de
        memória real). Além disso, uma entrada antiga ainda pendente podia
        um dia ser reaproveitada por um rec_seq reciclado (o mesmo
        problema de ordem de grandeza do desgaste do nonce de 32 bits, já
        documentado), misturando fragmentos de dois registos distintos.
        Chamado a cada fragmento incompleto recebido; custo desprezável
        (o dicionário fica sempre pequeno na prática)."""
        now = time.monotonic()
        stale = [seq for seq, e in self._pending_fragments.items()
                 if now - e["created_at"] > self.PENDING_FRAGMENT_TIMEOUT_S]
        for seq in stale:
            del self._pending_fragments[seq]

    def _on_dump_data(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        """Callback de notificação da characteristic dumpDataChar.

        Cada notificação é um fragmento (DumpDataPacket, 20 bytes, ver
        Ble.cpp): type, frag_idx, frag_total, chunk_len, rec_seq (uint32),
        nonce (uint32, 2026-07-07 — ver "CIFRA AES-CTR DO MODO DE DADOS"),
        chunk[8]. Um FullPlain (39 bytes), CIFRADO, chega dividido em até 5
        fragmentos; aqui remontamos por rec_seq até termos todos os bytes,
        depois decifra-se o registo completo antes de o descodificar.
        """
        if len(data) < 12:
            return
        _type, frag_idx, frag_total, chunk_len = data[0], data[1], data[2], data[3]
        rec_seq = struct.unpack_from("<I", data, 4)[0]
        nonce = struct.unpack_from("<I", data, 8)[0]
        chunk = bytes(data[12:12 + chunk_len])

        # frag_idx tem de ser um índice válido dentro de [0, frag_total) —
        # um único byte corrompido no ar (bit-flip de BLE, já visto noutras
        # partes deste projeto, ex.: o nonce AES-CTR) podia produzir um
        # frag_idx fora deste intervalo; sem esta validação, len(parts)
        # podia atingir "total" com um índice em falta (ex.: 0,1,5 para
        # total=3), e o join() abaixo rebentava com KeyError não tratado
        # dentro do callback de notificação BLE.
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

        # Todos os fragmentos chegaram — remonta pela ordem correta.
        try:
            cipher_full = b"".join(entry["parts"][i] for i in range(entry["total"]))
        except KeyError:
            # len(parts) == total mas os índices não cobrem 0..total-1
            # (ex.: um frag_idx duplicado ocupou o lugar de outro) — descarta
            # este registo em vez de deixar a exceção subir para o callback
            # de notificação do bleak (que pararia o processamento).
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
            # Ver is_plausible_full_plain() para o porquê: quase sempre
            # chave/nonce AES errada, nunca um valor real de sensor.
            # Rejeitar aqui em vez de deixar passar evita mostrar no
            # dashboard "FC=-18019, passos=2955050482" etc. (bug
            # reportado pelo utilizador) — mas NAO resolve a causa raiz.
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

        # Persiste TODOS os registos na base de dados local (ver
        # storage.py), independentemente do limite de taxa aplicado ao
        # broadcast por WebSocket logo a seguir — o histórico real não
        # deve perder amostras só porque o browser não precisa de as ver
        # todas em tempo real.
        try:
            storage.insert_record(self.db, record)
        except Exception as exc:  # noqa: BLE001 - nao deve travar o streaming
            print(f"[BRIDGE] erro a gravar registo na base de dados local: {exc}")

        # Dual-write (Lote C): segundo destino de escrita no ORM. Vem DEPOIS
        # de storage.insert_record de propósito — o caminho primário nunca
        # espera pelo ORM. insert_sensor_record acumula em buffer e faz
        # flush em lote (não 1 commit/registo) e é tolerante a falha por
        # dentro; não precisa de try/except aqui.
        if self.orm:
            self.orm.insert_sensor_record(record)

        has_new_vital = record["hr"] is not None or record["spo2"] is not None
        now = time.monotonic()
        due = (now - self._last_broadcast_monotonic) >= self.RECORD_BROADCAST_MIN_INTERVAL_S
        if not (has_new_vital or due):
            return  # amostra "normal" enviada ha pouco tempo — poupa o browser
        self._last_broadcast_monotonic = now

        asyncio.create_task(self.broadcast({"kind": "record", "rec_seq": rec_seq, **record}))

    def _on_dump_status(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        """Callback de notificação da characteristic dumpStatusChar
        (DumpStatusPacket, 16 bytes): type, state, reason, data_loss_flag,
        seq, sent_records, acked_records — ver Ble.cpp para o significado
        de cada "reason". 'data_loss_flag' (2026-07-03): 0=normal,
        1=ring buffer quase cheio (aviso antecipado, ainda sem perdas),
        2=já a substituir registos antigos não consumidos.
        """
        if len(data) < 16:
            return
        _type, state, reason, data_loss_flag, seq, sent, acked = struct.unpack_from(
            "<BBBBIII", data, 0
        )
        asyncio.create_task(self.broadcast({
            "kind": "status", "state": state, "reason": reason,
            "data_loss_flag": data_loss_flag,
            "seq": seq, "sent_records": sent, "acked_records": acked,
        }))

    def _on_emergency_alert(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        """Callback de notificação de emergencyAlertChar — disparada pelo
        módulo firmware Emergency ao confirmar um SOS manual (3 cliques)
        ou uma queda + inatividade prolongada (ver Emergency.cpp). Reenvia
        de imediato ao dashboard, sem o limite de taxa usado para os
        registos normais de sensores (é raro e crítico)."""
        if len(data) < EMERGENCY_ALERT_STRUCT.size:
            return
        alert = decode_emergency_alert(bytes(data[:EMERGENCY_ALERT_STRUCT.size]))
        print(f"[BRIDGE] ALERTA DE EMERGENCIA recebido: {alert['alert_name']} (seq={alert['seq']})")
        try:
            storage.insert_emergency_alert(self.db, alert)
        except Exception as exc:  # noqa: BLE001 - a gravacao nunca deve bloquear o alerta
            print(f"[BRIDGE] erro a gravar alerta de emergencia na base de dados local: {exc}")
        # Dual-write (Lote C): escrita imediata do alerta no ORM, com dedup
        # por (device, seq) igual ao INSERT OR IGNORE do storage.py.
        # Tolerante a falha por dentro; não bloqueia o alerta.
        if self.orm:
            self.orm.insert_emergency_alert(alert)
        asyncio.create_task(self.broadcast({"kind": "emergency_alert", **alert}))
        # Notificações externas (SMS/email ao(s) cuidador(es) + escalonamento
        # condicional ao contacto de emergência — ver notifications.py) NUNCA
        # podem atrasar/bloquear o broadcast acima, que é o caminho crítico
        # de segurança: o dashboard tem de ver o alerta de imediato, mesmo
        # que a Twilio/SendGrid estejam lentas, em baixo, ou nem configuradas.
        # Por isso corre como uma task asyncio SEPARADA (não um await direto
        # aqui), criada DEPOIS da task de broadcast, com qualquer erro
        # apanhado por dentro de _dispatch_emergency_notifications em vez de
        # poder propagar para este callback de notificação BLE.
        asyncio.create_task(self._dispatch_emergency_notifications(alert))

    async def _dispatch_emergency_notifications(self, alert: dict) -> None:
        """Aciona o EscalationManager (ver notifications.py) para um alerta
        de emergência REAL vindo do wearable: notifica de imediato o(s)
        cuidador(es) + o contacto de emergência, e agenda um escalonamento
        automático SÓ se o alerta cair dentro do horário declarado de
        indisponibilidade do cuidador e não for confirmado dentro do prazo
        (`acknowledge_alert`, ver handle_dashboard_command) — nunca contacta
        o 112 ou qualquer serviço de emergência real (ver a "DECISÃO
        DELIBERADA SOBRE O 112" no cabeçalho de notifications.py; o
        escalonamento é sempre uma mensagem mais urgente a um HUMANO, nunca
        uma chamada automatizada). `alert_id` combina tipo+seq para ficar
        estável o suficiente para `acknowledge_alert` cancelar o
        escalonamento certo, mesmo que 'seq' (uint16) eventualmente dê a
        volta numa sessão muito longa."""
        if self.escalation_manager is None:
            return
        alert_id = f"{alert['alert_type']}-{alert['seq']}"
        summary = f"{alert['alert_name']} (seq={alert['seq']})"
        try:
            self.escalation_manager.notify_emergency(
                alert_id,
                summary,
                self.notify_caregivers,
                self.notify_emergency_contact,
                self.notify_schedule,
            )
        except Exception as exc:  # noqa: BLE001 - notificacoes externas nunca podem derrubar o bridge
            print(f"[BRIDGE] erro ao acionar notificacoes de emergencia: {exc}")

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
                # Pedido do dashboard (ver handle_dashboard_command,
                # "set_ble_enabled") para largar a ligacao BLE — nao
                # procura nem liga enquanto isto nao voltar a True.
                # broadcast ja' foi feito no ponto onde ble_enabled passou
                # a False (ver abaixo); aqui so' aguardamos, sem repetir.
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
                    self.current_client = client
                    await self.broadcast({"kind": "device_status", "connected": True})

                    # Dual-write (Lote C): regista o MAC real do dispositivo
                    # (device.address do bleak) e audita o INÍCIO da sessão
                    # de ingestão. DECISÃO DOCUMENTADA (GDPR-003): audita-se
                    # UMA entrada por ligação BLE (session_start/session_end),
                    # nunca por registo de sensor — a ~52 registos/s um audit
                    # por registo inundaria audit_log e tornaria a auditoria
                    # inútil. O que importa registar é que uma sessão de
                    # ingestão de dados de saúde começou/terminou.
                    if self.orm:
                        self.orm.update_device_mac(device.address)
                        self.orm.audit(
                            action="ingestion.session_start",
                            resource_type="device",
                            resource_id=self.orm.device_id,
                            details={"address": str(device.address)},
                        )

                    await self._maybe_send_time(client)

                    # Subscreve notificacoes de dados e de estado.
                    await client.start_notify(UUID_DUMP_DATA, self._on_dump_data)
                    await client.start_notify(UUID_DUMP_STATUS, self._on_dump_status)
                    try:
                        await client.start_notify(UUID_EMERGENCY_ALERT, self._on_emergency_alert)
                    except Exception as exc:  # noqa: BLE001 - nao bloqueia o resto da ligacao
                        print(f"[BRIDGE] nao foi possivel subscrever emergencyAlertChar: {exc}")

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
                    # Mantem a ligacao viva ate ela cair sozinha OU o
                    # dashboard pedir para desligar (ble_enabled -> False).
                    while client.is_connected and self.ble_enabled:
                        await asyncio.sleep(1)
                    if not self.ble_enabled and client.is_connected:
                        print("[BRIDGE] desconexao BLE pedida pelo dashboard")
                        await client.disconnect()

            except Exception as exc:  # noqa: BLE001 - queremos reconectar em qualquer erro
                print(f"[BRIDGE] ligacao perdida/erro: {exc}")

            self.connected_device_name = None
            self.current_client = None
            # Dual-write (Lote C): garante que o buffer de sensores pendente
            # é comprometido ao fim da sessão (não fica perdido à espera do
            # próximo flush por tamanho/tempo) e audita o FIM da sessão de
            # ingestão (par do session_start acima).
            if self.orm:
                self.orm.flush()
                self.orm.audit(
                    action="ingestion.session_end",
                    resource_type="device",
                    resource_id=self.orm.device_id,
                )
            await self.broadcast({"kind": "device_status", "connected": False, "paused": not self.ble_enabled})
            if not self.ble_enabled:
                # Desligado a pedido do dashboard — nao ha' motivo para
                # tentar reconectar, o topo do loop vai ficar a aguardar
                # ble_enabled voltar a True (ver inicio de run_device_loop).
                continue
            print("[BRIDGE] desligado — a tentar reconectar em 3s")
            await asyncio.sleep(3)

    def _check_write_rate_limit(self, name: str) -> Optional[float]:
        """Ver WRITE_COMMAND_MIN_INTERVAL_S. Devolve None e regista o
        instante atual se o comando `name` puder prosseguir agora, ou o
        nº de segundos que falta esperar caso contrário (sem registar
        nada — uma tentativa rejeitada não deve empurrar a janela de
        limite mais para a frente, senão um cliente em loop apertado
        conseguiria manter o comando bloqueado para sempre)."""
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
            await client.write_gatt_char(UUID_DUMP_CTRL, payload, response=False)
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
        if cmd in ("force_reading", "reset_readings"):
            await self.send_command(ws, cmd)
            return
        if cmd == "set_ble_enabled":
            # Ligar/desligar manualmente a ligacao BLE ao wearable (botao
            # do dashboard) — nao e' uma escrita ao dispositivo (so' um
            # flag local que run_device_loop respeita), por isso nao passa
            # pelo rate limit de comandos de escrita (_check_write_rate_limit)
            # nem precisa de payload BLE. Ver self.ble_enabled no __init__.
            self.ble_enabled = bool(msg.get("enabled", True))
            state = "ativada" if self.ble_enabled else "desativada"
            print(f"[BRIDGE] ligacao BLE {state} pelo dashboard")
            # A desconexao real (se estava ligado) e' tratada por
            # run_device_loop, que ve' ble_enabled cair a False no seu
            # proximo ciclo (ate' 1s depois) e chama client.disconnect().
            await ws.send(json.dumps({"kind": "command_result", "cmd": cmd, "ok": True, "enabled": self.ble_enabled}))
            return
        if cmd == "acknowledge_alert":
            # Confirmação manual de um alerta de emergência (ver
            # notifications.py, EscalationManager.acknowledge) — cancela o
            # escalonamento automático pendente ao contacto de emergência,
            # se houver um agendado para este alert_id. 'alert_id' usa o
            # mesmo formato "{alert_type}-{seq}" produzido em
            # _dispatch_emergency_notifications; o dashboard já recebe
            # 'alert_type' e 'seq' no payload "emergency_alert" e pode
            # construir o mesmo id. Sem escalation_manager disponível ou
            # sem alert_id, devolve ok=False sem rebentar — canal não
            # autenticado, mesmo aviso de sempre (ver docstring deste
            # método).
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
        if cmd == "get_history":
            # Pedido de histórico real (ver storage.py) — "hours" é
            # opcional, por omissão 24h. Responde só ao cliente que
            # pediu, não a todos os ligados (ao contrário de broadcast()).
            hours = msg.get("hours", 24)
            try:
                hours = float(hours)
            except (TypeError, ValueError):
                hours = 24.0
            try:
                records = storage.get_records_since(self.db, hours)
                total = storage.count_records(self.db)
            except Exception as exc:  # noqa: BLE001
                print(f"[BRIDGE] erro a consultar historico: {exc}")
                await ws.send(json.dumps({"kind": "history", "records": [], "total_records": 0, "error": str(exc)}))
                return
            await ws.send(json.dumps({"kind": "history", "records": records, "total_records": total, "hours": hours}))
            # GDPR-003 (Lote C, lado bridge): auditar o acesso a dados de
            # paciente. O lado API pertence ao Lote B (api.py) — não mexido.
            if self.orm:
                self.orm.audit(
                    action="sensor_records.read",
                    resource_type="sensor_records",
                    details={"hours": hours},
                    ip=_ws_remote_ip(ws),
                )
            return
        if cmd == "get_daily_trend":
            # Histórico REAL agregado por dia (ver storage.get_daily_summary)
            # para a vista "Tendência semanal" do dashboard — leve o
            # suficiente para não sobrecarregar o WebSocket/browser, ao
            # contrário de "get_history" (registos em bruto).
            days = msg.get("days", 7)
            try:
                days = float(days)
            except (TypeError, ValueError):
                days = 7.0
            try:
                summary = storage.get_daily_summary(self.db, days)
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
                )
            return
        if cmd == "export_csv":
            # Exportação CSV (2026-07-03, pedido do utilizador) — devolve
            # o texto CSV diretamente, o dashboard trata de o transformar
            # num download no browser (mesma técnica já usada para o FHIR
            # JSON, ver exportFhirSummary() em web/dashboard/index.html).
            hours = msg.get("hours", 24)
            try:
                hours = float(hours)
            except (TypeError, ValueError):
                hours = 24.0
            try:
                csv_text = storage.export_records_csv(self.db, hours)
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
                )
            return
        if cmd == "get_retention_days":
            # Item pendente do backlog (PROJECT_STATUS.md, Prioridade 4):
            # expor a retenção como opção configurável pelo utilizador em
            # vez de constante fixa no código (ver storage.py).
            days = storage.get_retention_days(self.db)
            await ws.send(json.dumps({
                "kind": "retention_days",
                "days": days,
                "default_days": storage.DEFAULT_RETENTION_DAYS,
                "min_days": storage.MIN_RETENTION_DAYS,
                "max_days": storage.MAX_RETENTION_DAYS,
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
            days = msg.get("days")
            try:
                saved = storage.set_retention_days(self.db, days)
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
                )

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
    ssl_context = _build_ssl_context()
    server = await websockets.serve(bridge.ws_handler, WS_HOST, WS_PORT, ssl=ssl_context)
    scheme = "wss" if ssl_context else "ws"
    print(f"[BRIDGE] WebSocket a ouvir em {scheme}://{WS_HOST}:{WS_PORT}"
          + ("" if ssl_context else " (sem TLS — ver CAREWEAR_WS_TLS em ble_bridge.py)"))
    async with server:
        asyncio.create_task(bridge.periodic_retention_task())
        await bridge.run_device_loop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[BRIDGE] terminado pelo utilizador")
