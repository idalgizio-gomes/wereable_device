#!/usr/bin/env python3
"""orm_persistence.py — camada de ligação entre o bridge BLE e storage_advanced.py
(única fonte de persistência: escrita e leitura). Escrita é tolerante a falha (ao
primeiro erro, avisa uma vez, marca self.disabled e vira no-op — nunca derruba o
streaming BLE); leitura lança RuntimeError se desativado, pois não há fallback.
SensorRecord é acumulado em buffer e commitado em lote (insert_sensor_record);
alertas de emergência e auditoria são escritos de imediato."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import storage_advanced as sa

# UUIDs fixos do paciente/dispositivo "local" únicos deste bridge (sem multi-tenancy);
# get-or-create no arranque. Provisioning real com vários dispositivos resolveria por MAC/uuid.
DEFAULT_PATIENT_UUID = "local-default-patient"
DEFAULT_DEVICE_UUID = "local-default-device"
DEFAULT_DEVICE_MAC = "00:00:00:00:00:00"  # placeholder até update_device_mac() com o MAC real
# utilizador local get-or-create, só para ConsentRecord.user_id (NOT NULL) ter FK válida;
# nunca serve para login real (password_hash fixo, nenhum hash real produz este valor)
DEFAULT_USER_UUID = "local-default-user"
DEFAULT_USER_EMAIL = "local@carewear.invalid"
_DEFAULT_USER_PASSWORD_HASH = "!disabled-local-default-user"
PLACEHOLDER_DOB = datetime(1940, 1, 1)  # date_of_birth é NOT NULL mas o bridge não a conhece
CONSENT_SCOPE = "sensor_data"  # scope mínimo (GDPR-001/003) para o bridge gravar dados; ver _bootstrap


class OrmPersistence:
    """Camada de persistência ORM. Todos os métodos de escrita são tolerantes a
    falha: ao primeiro erro, avisam uma vez, marcam self.disabled=True e viram no-op."""

    BATCH_SIZE = 50  # compromete o buffer de SensorRecord ao atingir este tamanho...
    BATCH_INTERVAL_S = 1.0  # ...ou após este tempo desde o último flush, o que vier primeiro

    def __init__(self) -> None:
        self.disabled = False
        self._warned = False
        self._buffer: list = []
        self._last_flush = time.monotonic()
        self.session = None
        self.patient_id: Optional[int] = None
        self.device_id: Optional[int] = None
        self.user_id: Optional[int] = None
        try:
            sa.create_all_tables()
            self.session = sa.get_db_session()
            self._bootstrap()
        except Exception as exc:  # noqa: BLE001 - nunca derruba o arranque
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

        # utilizador local placeholder (FK necessária para ConsentRecord.user_id)
        user = (
            self.session.query(sa.User)
            .filter_by(uuid=DEFAULT_USER_UUID)
            .first()
        )
        if user is None:
            user = sa.User(
                uuid=DEFAULT_USER_UUID,
                email=DEFAULT_USER_EMAIL,
                password_hash=_DEFAULT_USER_PASSWORD_HASH,
                role="family",
                name=os.environ.get("CAREWEAR_PATIENT_NAME", "Utilizador Local"),
            )
            self.session.add(user)
            self.session.commit()
            self.session.refresh(user)
        self.user_id = user.id

        self._ensure_consent()  # GDPR-001/003 — ponto de aplicação real do consentimento

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

    def _ensure_consent(self) -> None:
        """Ponto de aplicação do consentimento no arranque (scope mínimo 'sensor_data').
        Se não existir consentimento válido, não bloqueia o arranque (mesmo padrão
        degradável do módulo) mas regista a ausência em audit_log."""
        if sa.has_valid_consent(self.session, self.patient_id, CONSENT_SCOPE):
            return

        self.audit(
            "consent_missing",
            resource_type="patient",
            resource_id=self.patient_id,
            details={"scope": CONSENT_SCOPE},
        )

    # consentimento granular por âmbito: permite conceder/revogar/consultar por
    # cada scope de sa.CONSENT_SCOPES separadamente (ex.: sensores sim, export não)

    def check_consent(self, scope: str) -> bool:
        """Existe consentimento válido para este âmbito? Se o ORM estiver desativado,
        devolve True — não bloqueia por falha de infraestrutura não relacionada."""
        if self.disabled or self.session is None:
            return True
        try:
            return sa.has_valid_consent(self.session, self.patient_id, scope)
        except Exception as exc:  # noqa: BLE001
            self._degrade("check_consent", exc)
            return True

    def set_consent(
        self,
        scope: str,
        granted: bool,
        given_by: str = "representative",
        representative_name: Optional[str] = None,
        representative_relationship: Optional[str] = None,
    ) -> dict:
        """Concede ou revoga consentimento (cmd 'set_consent'). Não degrada em
        silêncio — uma alteração que pareça ter funcionado mas não gravou é pior
        que um erro visível. Lança ValueError se scope desconhecido."""
        self._require_enabled()
        row = sa.grant_consent(
            self.session,
            patient_id=self.patient_id,
            user_id=self.user_id,
            scope=scope,
            granted=granted,
            given_by=given_by,
            representative_name=representative_name,
            representative_relationship=representative_relationship,
        )
        self.audit(
            "consent_changed",
            resource_type="patient",
            resource_id=self.patient_id,
            details={"scope": scope, "granted": granted, "version": row.version},
        )
        return {"scope": scope, "granted": row.granted, "version": row.version}

    def get_consent_status(self) -> dict:
        """Estado atual de todos os âmbitos de consentimento reconhecidos,
        para o dashboard mostrar (comando 'get_consent_status')."""
        self._require_enabled()
        return sa.get_consent_status(self.session, self.patient_id)

    # ---- baseline comportamental personalizada (2026-08-05) ---------------

    def get_thresholds(self) -> dict:
        """Limiares personalizados do paciente (ou DEFAULT_THRESHOLDS se
        ainda não definidos). Ao contrário da maioria das leituras deste
        módulo, NÃO lança quando o ORM está desativado — devolve os
        valores por omissão, para a avaliação de sinais vitais em tempo
        real (ver ble_bridge.py::_on_dump_data) nunca ficar sem limiar
        nenhum só por uma falha de infraestrutura não relacionada."""
        if self.disabled or self.session is None:
            return dict(sa.DEFAULT_THRESHOLDS, is_default=True, updated_at=None)
        try:
            return sa.get_thresholds(self.session, self.patient_id)
        except Exception as exc:  # noqa: BLE001
            self._degrade("get_thresholds", exc)
            return dict(sa.DEFAULT_THRESHOLDS, is_default=True, updated_at=None)

    def set_thresholds(self, **fields) -> dict:
        """Grava uma alteração PARCIAL aos limiares (comando
        'set_thresholds' do dashboard). Como set_consent, não degrada em
        silêncio — lança RuntimeError/ValueError explícitos (ver
        _require_enabled/sa.set_thresholds)."""
        self._require_enabled()
        result = sa.set_thresholds(self.session, self.patient_id, **fields)
        self.audit(
            "thresholds_changed",
            resource_type="patient",
            resource_id=self.patient_id,
            details=fields,
        )
        return result

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
        user_id: Optional[int] = None,
    ) -> None:
        """Grava uma entrada em audit_log (commit imediato). `user_id` é
        opcional: o canal WebSocket agora pode identificar o utilizador via
        token de sessão (?session= na ligação, ver ble_bridge._ws_process_request
        e auth_sessions.py), mas continua a aceitar None para ligações sem
        sessão associada."""
        if self.disabled or self.session is None:
            return
        try:
            row = sa.AuditLog(
                user_id=user_id,
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
                # BUG CORRIGIDO: `activity_date` era gravado com
                # `tz=timezone.utc` (data/hora UTC), mas `start_time`/
                # `end_time` logo abaixo são MINUTOS DESDE A MEIA-NOITE
                # LOCAL (activity_inference.py::_update_block usa
                # `time.localtime()`). Os dois campos referiam-se a
                # relógios diferentes, e quem os volta a juntar
                # (`storage_advanced._activity_window_epoch_range`, que faz
                # `datetime.combine(activity_date.date(), ...)` +
                # `time.mktime()`, ou seja LOCAL) reconstruía o instante
                # errado. Num fuso a leste de UTC (Portugal em horário de
                # verão, UTC+1) qualquer bloco fechado entre as 00:00 e as
                # 01:00 locais era gravado com a data do dia ANTERIOR:
                # local 2026-08-15 00:30 -> UTC 2026-08-14 23:30, e a
                # reconstrução dava 2026-08-14 00:30, exatamente 24h de
                # erro. Consequências reais: o bloco nunca aparecia na
                # timeline do episódio (`get_episode_timeline`) e era
                # contado no dia errado por
                # `Analytics.daily_activity_distribution` (que também
                # compara contra um `datetime(ano, mês, dia)` local-naive).
                # `fromtimestamp()` sem `tz` devolve a hora LOCAL naive —
                # o mesmo relógio de `start_time`/`end_time`.
                activity_date=datetime.fromtimestamp(
                    closed_block["start_wall_clock_s"]
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

    def purge(self, days: float) -> int:
        """Apaga SensorRecord com received_at < (agora - days) — retenção
        CONFIGURÁVEL (não os 365 dias fixos de
        DataRetention.RETENTION_POLICIES). Devolve o nº de linhas apagadas
        (0 se desativado ou erro), para quem chama poder registar/reportar
        — mesmo contrato que storage.purge_old_sensor_records() tinha."""
        if self.disabled or self.session is None:
            return 0
        try:
            from datetime import timedelta
            cutoff = datetime.utcnow() - timedelta(days=days)
            deleted = self.session.query(sa.SensorRecord).filter(
                sa.SensorRecord.received_at < cutoff
            ).delete(synchronize_session=False)
            self.session.commit()
            return deleted
        except Exception as exc:  # noqa: BLE001
            self._degrade("purge", exc)
            return 0

    async def run_retention_cleanup(self, dry_run: bool = False) -> Optional[dict]:
        """Aplica as políticas de retenção FIXAS de `DataRetention.cleanup`
        (RETENTION_POLICIES em storage_advanced.py: sensor_records 365d,
        activity_windows 1825d, alerts 2555d [soft delete], anomaly_detections
        1825d, medication_adherence 1095d, emergency_alerts 2920d [soft
        delete]). CORRIGIDO: esta docstring afirmava que "emergency_alerts
        nunca é apagado de propósito", o que deixou de ser verdade em
        2026-07-31 (GDPR-006, decisão da utilizadora) — `DataRetention.
        cleanup` passou nessa data a fazer soft delete dos EmergencyAlert
        com mais de 8 anos (ver o bloco "EmergencyAlert (soft delete...)"
        em storage_advanced.py). Isto é DISTINTO de `purge(days)` acima, que só cobre
        SensorRecord com a retenção CONFIGURÁVEL do dashboard (paridade com
        storage.py); este método cobre as restantes 4 tabelas do ORM que
        antes de existir este método nunca eram limpas em runtime (GDPR-006).
        Devolve o dict de contagens por tabela ou None se o ORM estiver
        desativado."""
        if self.disabled or self.session is None:
            return None
        try:
            return sa.DataRetention.cleanup(self.session, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            self._degrade("run_retention_cleanup", exc)
            return None

    # ---- leitura do dashboard (2026-07-26 — storage_advanced.py é a fonte única) --
    #
    # Ao contrário dos métodos de escrita acima, estes NÃO engolem falhas em
    # silêncio: sem storage.py como caminho alternativo, um erro aqui tem de
    # chegar a quem chama (ble_bridge.py) para virar um erro explícito no
    # dashboard, não uma lista vazia sem explicação.

    def _require_enabled(self) -> None:
        if self.disabled or self.session is None:
            raise RuntimeError(
                "persistencia ORM indisponivel (storage_advanced.py e a unica "
                "base de dados do bridge desde 2026-07-26; ver aviso de "
                "arranque para a causa raiz)"
            )

    def get_history(self, hours: float) -> tuple[list[dict], int]:
        """Devolve (records, total_records) das últimas `hours` horas —
        substitui storage.get_records_since() + storage.count_records()."""
        self._require_enabled()
        records = sa.get_records_since(self.session, self.device_id, hours)
        total = sa.count_records(self.session, self.device_id)
        return records, total

    def get_daily_trend(self, days: float) -> list[dict]:
        """Substitui storage.get_daily_summary()."""
        self._require_enabled()
        return sa.get_daily_summary(self.session, self.device_id, days)

    def export_csv(self, hours: float) -> str:
        """Substitui storage.export_records_csv(). Exportar dados é uma
        partilha explícita para fora do sistema (download local, mas ainda
        assim "sair" dos dados só usados internamente) — por isso, ao
        contrário de get_history/get_daily_trend (leituras internas do
        próprio dashboard), esta operação exige consentimento do âmbito
        'export' (2026-08-05). Lança PermissionError (não RuntimeError, para
        o chamador poder distinguir "sem consentimento" de "ORM em baixo")
        se não houver consentimento válido — ble_bridge.py já apanha
        qualquer Exception aqui e devolve {"error": str(exc)} ao dashboard,
        por isso não precisa de nenhuma alteração para este caso funcionar."""
        self._require_enabled()
        if not sa.has_valid_consent(self.session, self.patient_id, sa.CONSENT_SCOPE_EXPORT):
            raise PermissionError(
                "exportação de dados requer consentimento do âmbito 'export' "
                "(ainda não concedido para este paciente)"
            )
        return sa.export_records_csv(self.session, self.device_id, hours)

    def get_retention_days(self) -> float:
        """Substitui storage.get_retention_days(). Ao contrário dos outros
        métodos de leitura, não lança em caso de ORM desativado — devolve o
        valor por omissão, porque um pedido de LEITURA da retenção
        configurada não tem por onde falhar de forma útil ao utilizador
        (não há nada para escrever/gravar aqui)."""
        if self.disabled or self.session is None:
            return sa.DEFAULT_RETENTION_DAYS
        return sa.get_retention_days(self.session)

    def set_retention_days(self, days) -> float:
        """Substitui storage.set_retention_days()."""
        self._require_enabled()
        return sa.set_retention_days(self.session, days)

    def insert_activity_correction(self, original_category: Optional[str], corrected_category: str) -> None:
        """Substitui storage.insert_activity_correction()."""
        self._require_enabled()
        sa.insert_activity_correction(self.session, self.device_id, original_category, corrected_category)

    # ---- timeline correlacionada por episódio (2026-08-05) -----------------

    def get_episode_timeline(self, sequence_number: int, window_minutes: float = 30) -> dict:
        """Timeline correlacionada (sinais vitais + blocos de atividade +
        outros alertas próximos) centrada no EmergencyAlert
        `sequence_number` deste dispositivo (comando 'get_episode_timeline'
        do dashboard). Mesmo padrão de get_history/get_daily_trend: leitura,
        sem storage.py como caminho alternativo, por isso não degrada em
        silêncio -- lança RuntimeError se o ORM estiver desativado (via
        _require_enabled) ou ValueError se o alerta não existir (propagado
        de sa.get_episode_timeline_for_alert)."""
        self._require_enabled()
        return sa.get_episode_timeline_for_alert(self.session, self.device_id, sequence_number, window_minutes)


# Constantes de retenção re-exportadas para quem só importa orm_persistence
# (ble_bridge.py) não precisar de importar storage_advanced diretamente só
# por causa de 3 constantes.
DEFAULT_RETENTION_DAYS = sa.DEFAULT_RETENTION_DAYS
MIN_RETENTION_DAYS = sa.MIN_RETENTION_DAYS
MAX_RETENTION_DAYS = sa.MAX_RETENTION_DAYS
