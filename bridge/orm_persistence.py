#!/usr/bin/env python3
"""
orm_persistence.py — Camada de ligação entre o bridge BLE e o ORM avançado.

CONTEXTO (Lote C — dual-write transitório)
------------------------------------------
Até agora `ble_bridge.py` só persistia em `storage.py` (SQLite "cru", sem
ORM). O esquema ORM completo de `storage_advanced.py` (pacientes,
dispositivos, `sensor_records`, `emergency_alerts`, `audit_log`, retenção,
cifra de campos sensíveis) existia mas NUNCA era escrito em runtime — só
era exercitado por testes. Este módulo fecha essa lacuna sem trocar o
caminho primário: `storage.py` continua a ser a fonte de verdade de TODAS
as leituras do dashboard (get_history, get_daily_trend, export_csv,
retenção). O ORM entra como SEGUNDO destino de escrita ("dual-write").

Porquê dual-write e não substituição direta: a troca definitiva só deve
acontecer depois de o dual-write ter corrido com hardware real, hoje
bloqueado (placa indisponível, ver PROJECT_STATUS.md). Até lá, esta
segunda escrita:

  * NUNCA pode derrubar o streaming BLE nem o caminho `storage.py`. Ao
    PRIMEIRO erro em qualquer método, avisa uma vez, marca `self.disabled`
    e passa a ser um no-op — exatamente o mesmo padrão degradável dos
    try/except já existentes em `_on_dump_data`/`_on_emergency_alert`.
  * Não paga 1 commit por registo de sensor. `storage.py` já paga 1
    commit/registo (em WAL) até ~52 registos/s; duplicar isso com o
    overhead do ORM bloquearia o event loop. Por isso os `SensorRecord`
    são acumulados num buffer e comprometidos EM LOTE (ver
    `insert_sensor_record`). Alertas de emergência e auditoria são raros e
    importantes, logo são escritos de imediato.

Uso a partir de `ble_bridge.py` (todas as chamadas guardadas por
`if self.orm:` do lado do bridge):

    self.orm = OrmPersistence()            # no __init__ (try/except -> None)
    self.orm.update_device_mac(addr)       # ao ligar (run_device_loop)
    self.orm.insert_sensor_record(record)  # por registo (_on_dump_data)
    self.orm.insert_emergency_alert(alert) # por alerta (_on_emergency_alert)
    self.orm.insert_activity_window(block) # por bloco fechado (activity_inference.py)
    self.orm.audit(...)                    # acessos a dados de paciente
    self.orm.purge(days)                   # retenção periódica
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import storage_advanced as sa

# UUIDs fixos do paciente/dispositivo "local" únicos deste bridge. O
# dual-write local não tem multi-tenancy — há um único paciente e um único
# dispositivo por instalação, criados por get-or-create no arranque. Se e
# quando existir provisioning real com vários dispositivos, isto passa a
# ser resolvido pelo MAC/uuid reais entregues por essa app.
DEFAULT_PATIENT_UUID = "local-default-patient"
DEFAULT_DEVICE_UUID = "local-default-device"
# Placeholder até haver um MAC real (atualizado por update_device_mac()
# quando o bridge liga — device.address do bleak). "00:00:00:00:00:00"
# nunca colide com um MAC real de hardware.
DEFAULT_DEVICE_MAC = "00:00:00:00:00:00"
# date_of_birth é NOT NULL no esquema (Patient.date_of_birth) mas o bridge
# não conhece a data de nascimento real do utente — placeholder explícito
# e documentado, a corrigir por quem fizer o provisioning/registo real.
PLACEHOLDER_DOB = datetime(1940, 1, 1)


class OrmPersistence:
    """Segundo destino de escrita (ORM) do dual-write transitório.

    Todos os métodos são tolerantes a falha: ao primeiro erro, avisam uma
    vez, marcam `self.disabled = True` e tornam-se no-ops. A persistência
    nova degrada em silêncio; o streaming e o `storage.py` continuam.
    """

    # Compromete o buffer de SensorRecord quando atinge este tamanho...
    BATCH_SIZE = 50
    # ...ou quando passou este tempo desde o último flush (o que vier
    # primeiro), verificado dentro do próprio insert (sem task extra).
    BATCH_INTERVAL_S = 1.0

    def __init__(self) -> None:
        self.disabled = False
        self._warned = False
        self._buffer: list = []
        self._last_flush = time.monotonic()
        self.session = None
        self.patient_id: Optional[int] = None
        self.device_id: Optional[int] = None
        try:
            sa.create_all_tables()
            self.session = sa.get_db_session()
            self._bootstrap()
        except Exception as exc:  # noqa: BLE001 - dual-write nunca derruba o arranque
            self._degrade("bootstrap do ORM", exc)

    # ---- infraestrutura interna -------------------------------------------

    def _degrade(self, where: str, exc: Exception) -> None:
        """Marca o dual-write como desativado e avisa (uma única vez)."""
        self.disabled = True
        if not self._warned:
            self._warned = True
            print(f"[BRIDGE] AVISO: persistencia ORM (dual-write) desativada apos "
                  f"erro em {where}: {exc}. O streaming e o storage.py continuam; "
                  f"este aviso so aparece uma vez.")
        # Tenta limpar qualquer transacao meia-feita para nao contaminar
        # uma sessao que possa ainda vir a ser lida por um teste.
        try:
            if self.session is not None:
                self.session.rollback()
        except Exception:  # noqa: BLE001
            pass

    def _bootstrap(self) -> None:
        """get-or-create do paciente/dispositivo local. Idempotente — pode
        correr várias vezes contra a mesma BD (ex.: vários BleBridge() nos
        testes) sem duplicar linhas."""
        patient = (
            self.session.query(sa.Patient)
            .filter_by(uuid=DEFAULT_PATIENT_UUID)
            .first()
        )
        if patient is None:
            patient = sa.Patient(
                uuid=DEFAULT_PATIENT_UUID,
                name=os.environ.get("CAREWEAR_PATIENT_NAME", "Paciente Local"),
                date_of_birth=PLACEHOLDER_DOB,
            )
            self.session.add(patient)
            self.session.commit()
            self.session.refresh(patient)
        self.patient_id = patient.id

        device = (
            self.session.query(sa.Device)
            .filter_by(uuid=DEFAULT_DEVICE_UUID)
            .first()
        )
        if device is None:
            device = sa.Device(
                uuid=DEFAULT_DEVICE_UUID,
                patient_id=self.patient_id,
                mac_address=DEFAULT_DEVICE_MAC,
            )
            self.session.add(device)
            self.session.commit()
            self.session.refresh(device)
        self.device_id = device.id

    def _flush(self) -> None:
        """Compromete o buffer de SensorRecord acumulado (add_all + commit).
        O buffer é uma lista de objetos AINDA NÃO adicionados à sessão, por
        isso um commit de emergência/auditoria/purge no meio nunca os
        arrasta prematuramente nem os deixa presos numa transação alheia."""
        if not self._buffer:
            return
        self.session.add_all(self._buffer)
        self.session.commit()
        self._buffer = []
        self._last_flush = time.monotonic()

    def flush(self) -> None:
        """Força o flush do buffer de sensores (usado no encerramento
        ordenado e pelos testes). Tolerante a falha como os restantes."""
        if self.disabled or self.session is None:
            return
        try:
            self._flush()
        except Exception as exc:  # noqa: BLE001
            self._degrade("flush do buffer de sensores", exc)

    # ---- escrita de sensores (EM LOTE) ------------------------------------

    def insert_sensor_record(self, record: dict) -> None:
        """Acrescenta um registo de sensor ao buffer e faz flush em lote
        quando `len(buffer) >= BATCH_SIZE` ou passou `BATCH_INTERVAL_S`
        desde o último flush. Mapeamento exato dict->SensorRecord (ver
        decode_full_plain em ble_bridge.py): hr já vem None quando 0."""
        if self.disabled or self.session is None:
            return
        try:
            rec = sa.SensorRecord(
                device_id=self.device_id,
                timestamp_utc=record["ts"],
                accel_x=record["ax"],
                accel_y=record["ay"],
                accel_z=record["az"],
                gyro_x=record["gx"],
                gyro_y=record["gy"],
                gyro_z=record["gz"],
                steps_count=record["steps"],
                freefall_detected=record["freefall"],
                inactivity_detected=record["inactivity"],
                heart_rate=record["hr"],
                spo2_percent=record["spo2"],
                pacing_index=record["pacing_index"],
            )
            self._buffer.append(rec)
            now = time.monotonic()
            if (len(self._buffer) >= self.BATCH_SIZE
                    or (now - self._last_flush) >= self.BATCH_INTERVAL_S):
                self._flush()
        except Exception as exc:  # noqa: BLE001
            self._degrade("insert_sensor_record", exc)

    # ---- escrita de emergências (IMEDIATA) --------------------------------

    def insert_emergency_alert(self, alert: dict) -> None:
        """Escrita imediata (nunca em lote) de um alerta de emergência.
        A UniqueConstraint uq_emergency_device_seq (device_id,
        sequence_number) faz de dedup de replay BLE — equivalente ao
        INSERT OR IGNORE do storage.py: um IntegrityError aqui é rollback +
        ignorar, não um erro que desative o dual-write."""
        if self.disabled or self.session is None:
            return
        try:
            row = sa.EmergencyAlert(
                uuid=str(uuid4()),
                device_id=self.device_id,
                alert_type=alert["alert_name"],
                sequence_number=alert["seq"],
                timestamp_utc=alert["timestamp_utc"],
            )
            self.session.add(row)
            self.session.commit()
        except IntegrityError:
            # Replay do mesmo (device, seq) — dedup, não é falha real.
            self.session.rollback()
        except Exception as exc:  # noqa: BLE001
            self._degrade("insert_emergency_alert", exc)

    # ---- auditoria (GDPR-003, IMEDIATA) -----------------------------------

    def audit(
        self,
        action: str,
        resource_type: Optional[str] = None,
        resource_id: Optional[int] = None,
        details: Optional[dict] = None,
        ip: Optional[str] = None,
    ) -> None:
        """Grava uma entrada em audit_log (commit imediato). user_id fica
        None de propósito: o canal WebSocket bridge<->dashboard NÃO é
        autenticado (ver handle_dashboard_command/docstring de ble_bridge),
        por isso não há utilizador conhecido a atribuir ao acesso. O que
        interessa registar é a AÇÃO sobre dados de paciente e a origem
        (ip), não uma identidade que não existe nesta fase."""
        if self.disabled or self.session is None:
            return
        try:
            row = sa.AuditLog(
                user_id=None,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details=details,
                ip_address=ip,
            )
            self.session.add(row)
            self.session.commit()
        except Exception as exc:  # noqa: BLE001
            self._degrade("audit", exc)

    # ---- dispositivo -------------------------------------------------------

    def update_device_mac(self, mac: Optional[str]) -> None:
        """Atualiza mac_address/last_sync do dispositivo local quando o
        bridge liga (device.address do bleak). O MAC é UNIQUE: se já
        existir uma linha Device com esse MAC (ex.: reprovisionamento),
        apanha o IntegrityError, faz rollback e passa a usar essa linha
        (lookup por MAC) em vez de duplicar."""
        if self.disabled or self.session is None or not mac:
            return
        try:
            device = self.session.get(sa.Device, self.device_id)
            if device is None:
                return
            device.mac_address = mac
            device.last_sync = datetime.now(timezone.utc)
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            try:
                existing = (
                    self.session.query(sa.Device)
                    .filter_by(mac_address=mac)
                    .first()
                )
                if existing is not None:
                    self.device_id = existing.id
                    existing.last_sync = datetime.now(timezone.utc)
                    self.session.commit()
            except Exception as exc:  # noqa: BLE001
                self._degrade("update_device_mac (lookup por MAC)", exc)
        except Exception as exc:  # noqa: BLE001
            self._degrade("update_device_mac", exc)

    # ---- classificação de atividade (IMEDIATA, 2026-07-20) -----------------

    def insert_activity_window(self, closed_block: dict) -> None:
        """Escrita imediata (não em lote — blocos fecham a cada poucos
        minutos, não a ~52/s como sensor_records) de um bloco de atividade
        já FECHADO pelo classificador em tempo real (ver
        activity_inference.py::_update_block). `activity_category` usa o
        vocabulário em inglês do esquema (CheckConstraint em
        storage_advanced.py), já traduzido pelo chamador via
        CLASS_TO_DB_CATEGORY (closed_block["db_category"]).

        NOTA: `is_anomaly`/`reason` (veredito do duration_detector) não têm
        ainda uma coluna própria neste esquema — são transmitidos ao
        dashboard em tempo real (kind "activity_duration_flag") mas não
        persistidos aqui. Ficaria natural futuramente popular
        `anomaly_detections` a partir daqui quando `is_anomaly` for True;
        não feito nesta rotina (âmbito: ligar a classificação em si, não
        todo o pipeline de alertas de rotina)."""
        if self.disabled or self.session is None:
            return
        try:
            row = sa.ActivityWindow(
                device_id=self.device_id,
                activity_date=datetime.fromtimestamp(
                    closed_block["start_wall_clock_s"], tz=timezone.utc
                ),
                activity_category=closed_block["db_category"],
                start_time=closed_block["start_time_minutes"],
                end_time=closed_block["end_time_minutes"],
                duration_minutes=round(closed_block["duration_min"]),
                confidence=closed_block["confidence"],
            )
            self.session.add(row)
            self.session.commit()
        except Exception as exc:  # noqa: BLE001
            self._degrade("insert_activity_window", exc)

    # ---- retenção ----------------------------------------------------------

    def purge(self, days: float) -> None:
        """Apaga SensorRecord com received_at < (agora - days), em paridade
        com a retenção CONFIGURÁVEL do storage.py. NÃO usa os 365 dias
        fixos de DataRetention.RETENTION_POLICIES — usa os `days` passados
        (o mesmo valor efetivo que o bridge passa a storage.purge_old_...)."""
        if self.disabled or self.session is None:
            return
        try:
            from datetime import timedelta
            cutoff = datetime.utcnow() - timedelta(days=days)
            self.session.query(sa.SensorRecord).filter(
                sa.SensorRecord.received_at < cutoff
            ).delete(synchronize_session=False)
            self.session.commit()
        except Exception as exc:  # noqa: BLE001
            self._degrade("purge", exc)
