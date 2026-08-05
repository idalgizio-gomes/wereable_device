#!/usr/bin/env python3
"""
orm_persistence.py — Camada de ligação entre o bridge BLE e o ORM avançado.

CONTEXTO (migração 2026-07-26 — storage_advanced.py passa a ser a fonte única)
-------------------------------------------------------------------------------
Até 2026-07-25, `ble_bridge.py` persistia primariamente em `storage.py`
(SQLite "cru", sem ORM) e este módulo era um SEGUNDO destino de escrita
transitório ("dual-write", Lote C) — o esquema ORM completo de
`storage_advanced.py` (pacientes, dispositivos, `sensor_records`,
`emergency_alerts`, `audit_log`, retenção, cifra de campos sensíveis)
existia mas nunca era lido em runtime pelo dashboard.

Essa fase de transição terminou: `storage.py` foi removido, e este módulo
é agora a ÚNICA camada de persistência do bridge — escrita E leitura
(get_history, get_daily_trend, export_csv, retenção configurável,
correções de atividade). Diferença prática para quem chama a partir de
`ble_bridge.py`: os métodos de LEITURA abaixo (get_history/get_daily_trend/
export_csv/get_retention_days/set_retention_days) já NÃO degradam em
silêncio como os de escrita — se `self.orm` estiver desativado
(`self.disabled`), lançam `RuntimeError` explícito, porque já não há
nenhum `storage.py` a responder no lugar. Quem chama (ble_bridge.py) tem
de apanhar isto e devolver um erro claro ao dashboard.

Escrita continua tolerante a falha (mesmo padrão de sempre):

  * NUNCA pode derrubar o streaming BLE. Ao PRIMEIRO erro em qualquer
    método, avisa uma vez, marca `self.disabled` e passa a ser um no-op.
  * `SensorRecord` são acumulados num buffer e comprometidos EM LOTE (ver
    `insert_sensor_record`) — ao ritmo do IMU (~14-52 registos/seg), um
    commit por registo bloquearia o event loop asyncio. Alertas de
    emergência e auditoria são raros e importantes, logo escritos de
    imediato.

Uso a partir de `ble_bridge.py`:

    self.orm = OrmPersistence()            # no __init__ (try/except -> None)
                                           #   o bootstrap verifica ainda o
                                           #   consentimento (GDPR-001/003):
                                           #   sem ConsentRecord válido de
                                           #   scope 'sensor_data' regista
                                           #   'consent_missing' em audit_log
                                           #   sem bloquear o arranque.
    self.orm.update_device_mac(addr)       # ao ligar (run_device_loop)
    self.orm.insert_sensor_record(record)  # por registo (_on_dump_data)
    self.orm.insert_emergency_alert(alert) # por alerta (_on_emergency_alert)
    self.orm.insert_activity_window(block) # por bloco fechado (activity_inference.py)
    self.orm.audit(...)                    # acessos a dados de paciente
    self.orm.purge(days)                   # retenção periódica (SensorRecord, configurável)
    self.orm.run_retention_cleanup()       # retenção periódica (RETENTION_POLICIES fixas, GDPR-006)
    self.orm.get_history(hours)            # leitura (cmd "get_history")
    self.orm.get_daily_trend(days)         # leitura (cmd "get_daily_trend")
    self.orm.export_csv(hours)             # leitura (cmd "export_csv")
    self.orm.get_retention_days()          # leitura (cmd "get_retention_days")
    self.orm.set_retention_days(days)      # escrita (cmd "set_retention_days")
    self.orm.insert_activity_correction(orig, corrected)  # escrita (cmd "correct_activity")
    self.orm.get_consent_status()          # leitura (cmd "get_consent_status")
    self.orm.set_consent(scope, granted)   # escrita (cmd "set_consent", 2026-08-05)
    self.orm.check_consent(scope)          # usado internamente (ex.: export_csv)
    self.orm.get_thresholds()              # leitura (cmd "get_thresholds", 2026-08-05)
    self.orm.set_thresholds(**fields)      # escrita (cmd "set_thresholds", 2026-08-05)
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
# Utilizador "local" get-or-create, mesmo padrão de DEFAULT_PATIENT_UUID/
# DEFAULT_DEVICE_UUID acima — existe só para que ConsentRecord.user_id
# (NOT NULL) tenha uma FK válida a apontar quando o consentimento é
# concedido/revogado a partir do WebSocket não-autenticado do dashboard
# (2026-08-05, consentimento granular). Nunca serve para login real: o
# password_hash é um valor fixo que nenhum hash de password real produz.
DEFAULT_USER_UUID = "local-default-user"
DEFAULT_USER_EMAIL = "local@carewear.invalid"
_DEFAULT_USER_PASSWORD_HASH = "!disabled-local-default-user"
# date_of_birth é NOT NULL no esquema (Patient.date_of_birth) mas o bridge
# não conhece a data de nascimento real do utente — placeholder explícito
# e documentado, a corrigir por quem fizer o provisioning/registo real.
PLACEHOLDER_DOB = datetime(1940, 1, 1)
# Scope mínimo de consentimento (GDPR-001/GDPR-003) que o bridge tem de ter
# para sequer gravar dados de sensores. Verificado no _bootstrap.
CONSENT_SCOPE = "sensor_data"


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
        self.user_id: Optional[int] = None
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

        # Utilizador local placeholder (ver DEFAULT_USER_UUID acima) — FK
        # necessária para ConsentRecord.user_id. Get-or-create, idempotente
        # como o resto deste método.
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

        # GDPR-001/GDPR-003 — ponto de aplicação real do consentimento.
        self._ensure_consent()

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
        """GDPR-001/GDPR-003 — ponto de aplicação do consentimento no
        arranque. O scope mínimo para o bridge sequer gravar dados é
        `sensor_data`. Se existir um ConsentRecord válido (granted=True e,
        se `expires_at` estiver preenchido, ainda não expirado) segue o
        fluxo normal, sem mudanças de comportamento. Se NÃO existir, NÃO
        bloqueia o arranque (o streaming BLE/`storage.py` nunca podem parar
        por causa disto — mesmo padrão degradável do resto do módulo), mas
        regista a ausência explicitamente em audit_log em vez de a ignorar
        em silêncio.

        NOTA: a criação automática opcional de um ConsentRecord inicial a
        partir de variáveis de ambiente (CAREWEAR_CONSENT_*) foi ponderada
        e deliberadamente NÃO implementada nesta fase: `ConsentRecord`
        exige `user_id NOT NULL` (FK para `users`) e o bootstrap local não
        tem provisioning real de contas (ver DEFAULT_PATIENT_UUID acima) —
        não há um `user_id` real para atribuir, e inventar um utilizador
        placeholder está fora do âmbito deste item. O consentimento
        propriamente dito passa a ser criado pela UI de consentimento do
        dashboard (com o representante já autenticado) — ver `set_consent()`
        abaixo (2026-08-05): já é possível conceder/revogar por âmbito a
        partir do dashboard, ainda sem essa UI dedicada."""
        if sa.has_valid_consent(self.session, self.patient_id, CONSENT_SCOPE):
            return  # consentimento válido — comportamento inalterado.

        # Ausência de consentimento válido registada explicitamente.
        self.audit(
            "consent_missing",
            resource_type="patient",
            resource_id=self.patient_id,
            details={"scope": CONSENT_SCOPE},
        )

    # ---- consentimento granular por âmbito (2026-08-05) --------------------
    #
    # `_ensure_consent` acima só guarda o âmbito mínimo ('sensor_data') no
    # arranque. Os métodos a seguir dão ao dashboard uma forma de conceder/
    # revogar/consultar consentimento por CADA âmbito de sa.CONSENT_SCOPES
    # separadamente (ex.: aceitar guardar sinais vitais mas recusar
    # exportação/analítica) — o Choi Moon-Jung (KAIST), citado na
    # PRISMA_SCR_SCOPING_REVIEW.md, é a referência da literatura que
    # motivou isto: é o único estudo revisto a tratar a sério o controlo
    # granular de partilha de dados de saúde por idosos.

    def check_consent(self, scope: str) -> bool:
        """Existe consentimento válido para este âmbito? Tolerante a falha
        como o resto do módulo: se o ORM estiver desativado, devolve True
        (não bloqueia por causa de uma falha de infraestrutura — a decisão
        de bloquear é de quem chama, baseada no âmbito, não desta função)."""
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
        """Concede ou revoga consentimento para um âmbito (comando
        'set_consent' do dashboard). Ao contrário da escrita de sensores,
        NÃO degrada em silêncio — uma alteração de consentimento que pareça
        ter funcionado mas não foi gravada é pior que um erro visível
        (mesmo raciocínio das leituras do dashboard, ver `_require_enabled`
        abaixo). Lança ValueError se `scope` for desconhecido (propagado
        de `sa.grant_consent`)."""
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
        1825d, medication_adherence 1095d — emergency_alerts nunca é apagado
        de propósito). Isto é DISTINTO de `purge(days)` acima, que só cobre
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


# Constantes de retenção re-exportadas para quem só importa orm_persistence
# (ble_bridge.py) não precisar de importar storage_advanced diretamente só
# por causa de 3 constantes.
DEFAULT_RETENTION_DAYS = sa.DEFAULT_RETENTION_DAYS
MIN_RETENTION_DAYS = sa.MIN_RETENTION_DAYS
MAX_RETENTION_DAYS = sa.MAX_RETENTION_DAYS
