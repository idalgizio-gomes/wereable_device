"""Testes de `bridge/orm_persistence.py` — o segundo destino de escrita
(ORM) do dual-write transitório do Lote C.

Corre inteiramente contra SQLite em memória (ver conftest.py, que força
DATABASE_URL=sqlite:///:memory: ANTES do primeiro import de
storage_advanced), sem hardware BLE nem rede real. Cobre:

  (a) round-trip de SensorRecord (mapeamento campo-a-campo, incl.
      pacing_index e hr=None quando 0);
  (b) escrita EM LOTE (flush por contagem, não 1 commit por registo);
  (c) dedup de EmergencyAlert por (device, seq) — replay BLE;
  (d) auditoria GDPR-003: um get_history real (via BleBridge) gera uma
      entrada em audit_log com a action correta;
  (e) purge respeita os `days` configuráveis (não os 365d fixos);
  (f) degradação: um erro injetado desativa o dual-write (disabled=True)
      e a chamada seguinte não rebenta.
"""
from datetime import datetime, timedelta

import pytest

import orm_persistence
import storage_advanced as sa


@pytest.fixture(autouse=True)
def _fresh_schema():
    """Recria o schema do zero antes/depois de cada teste (isolamento)."""
    sa.Base.metadata.drop_all(bind=sa.engine)
    sa.Base.metadata.create_all(bind=sa.engine)
    yield
    sa.Base.metadata.drop_all(bind=sa.engine)


def _make_record(ts=1000, hr=None, spo2=98, pacing=42, steps=100):
    """Espelha a saída de decode_full_plain() em ble_bridge.py (hr/spo2 já
    vêm None quando 0; pacing_index é sempre um int, 0 é valor real)."""
    return {
        "ts": ts,
        "ax": 0.1, "ay": -0.2, "az": 9.81,
        "gx": 1.5, "gy": -2.5, "gz": 3.5,
        "steps": steps,
        "freefall": False,
        "inactivity": True,
        "spo2": spo2,
        "hr": hr,
        "pacing_index": pacing,
    }


# ---------------------------------------------------------------------------
# (a) round-trip de sensores
# ---------------------------------------------------------------------------

def test_sensor_round_trip_field_by_field():
    orm = orm_persistence.OrmPersistence()
    assert not orm.disabled

    records = [
        _make_record(ts=1000, hr=None, spo2=97, pacing=0, steps=10),   # hr None (era 0)
        _make_record(ts=1001, hr=72, spo2=98, pacing=55, steps=11),
        _make_record(ts=1002, hr=80, spo2=None, pacing=100, steps=12),
    ]
    for r in records:
        orm.insert_sensor_record(r)
    orm.flush()

    session = sa.get_db_session()
    try:
        rows = (
            session.query(sa.SensorRecord)
            .order_by(sa.SensorRecord.timestamp_utc)
            .all()
        )
        assert len(rows) == 3
        for row, r in zip(rows, records):
            assert row.device_id == orm.device_id
            assert row.timestamp_utc == r["ts"]
            assert row.accel_x == pytest.approx(r["ax"])
            assert row.accel_y == pytest.approx(r["ay"])
            assert row.accel_z == pytest.approx(r["az"])
            assert row.gyro_x == pytest.approx(r["gx"])
            assert row.gyro_y == pytest.approx(r["gy"])
            assert row.gyro_z == pytest.approx(r["gz"])
            assert row.steps_count == r["steps"]
            assert row.freefall_detected == r["freefall"]
            assert row.inactivity_detected == r["inactivity"]
            assert row.heart_rate == r["hr"]
            assert row.spo2_percent == r["spo2"]
            assert row.pacing_index == r["pacing_index"]
        # hr None (era 0) preservado como NULL, não como 0.
        assert rows[0].heart_rate is None
        # pacing_index 0 é um valor real, guardado como 0 (não None).
        assert rows[0].pacing_index == 0
        # spo2 None preservado.
        assert rows[2].spo2_percent is None
    finally:
        session.close()


# ---------------------------------------------------------------------------
# (b) escrita em lote — flush por contagem
# ---------------------------------------------------------------------------

def test_sensor_records_are_flushed_in_batches_by_count():
    orm = orm_persistence.OrmPersistence()
    # Neutraliza o flush por TEMPO para isolar o gatilho por CONTAGEM
    # (senão uma máquina lenta podia disparar o flush de 1.0s a meio).
    orm.BATCH_INTERVAL_S = 10 ** 9

    def _db_count():
        s = sa.get_db_session()
        try:
            return s.query(sa.SensorRecord).count()
        finally:
            s.close()

    # BATCH_SIZE-1 inserts: ainda no buffer, nada comprometido na BD.
    for i in range(orm.BATCH_SIZE - 1):
        orm.insert_sensor_record(_make_record(ts=2000 + i))
    assert _db_count() == 0
    assert len(orm._buffer) == orm.BATCH_SIZE - 1

    # O input que atinge BATCH_SIZE dispara o flush em lote.
    orm.insert_sensor_record(_make_record(ts=9999))
    assert _db_count() == orm.BATCH_SIZE
    assert len(orm._buffer) == 0


# ---------------------------------------------------------------------------
# (c) dedup de emergências por (device, seq)
# ---------------------------------------------------------------------------

def test_emergency_alert_dedup_same_device_seq():
    orm = orm_persistence.OrmPersistence()
    alert = {
        "alert_type": 1,
        "alert_name": "sos_manual",
        "seq": 7,
        "timestamp_utc": 1720000000,
    }
    orm.insert_emergency_alert(alert)
    orm.insert_emergency_alert(alert)  # replay BLE do mesmo alerta

    session = sa.get_db_session()
    try:
        rows = session.query(sa.EmergencyAlert).all()
        assert len(rows) == 1
        assert rows[0].alert_type == "sos_manual"
        assert rows[0].sequence_number == 7
        assert rows[0].timestamp_utc == 1720000000
        assert rows[0].device_id == orm.device_id
    finally:
        session.close()
    # O dedup (IntegrityError -> rollback) não desativa o dual-write.
    assert not orm.disabled


def test_emergency_alert_different_seq_both_stored():
    orm = orm_persistence.OrmPersistence()
    orm.insert_emergency_alert({"alert_type": 1, "alert_name": "sos_manual", "seq": 1, "timestamp_utc": 10})
    orm.insert_emergency_alert({"alert_type": 2, "alert_name": "fall_inactivity", "seq": 2, "timestamp_utc": 20})
    session = sa.get_db_session()
    try:
        assert session.query(sa.EmergencyAlert).count() == 2
    finally:
        session.close()


# ---------------------------------------------------------------------------
# (c.2) classificação de atividade — blocos fechados por activity_inference.py
# ---------------------------------------------------------------------------

def _make_closed_block(cls="Descanso", session="dia", start_wall_clock_s=1_800_000_000.0):
    """Espelha a saída de activity_inference.py::_update_block (closed_block)."""
    return {
        "cls": cls,
        "db_category": {"Dormir": "sleep", "Descanso": "rest", "Atividade": "activity",
                         "Alimentação": "eating", "Higiene": "hygiene"}[cls],
        "session": session,
        "duration_min": 12.5,
        "is_anomaly": False,
        "reason": None,
        "confidence": 0.87,
        "start_wall_clock_s": start_wall_clock_s,
        "start_time_minutes": 600,
        "end_time_minutes": 612,
    }


def test_activity_window_round_trip_uses_english_category_from_mapping():
    orm = orm_persistence.OrmPersistence()
    orm.insert_activity_window(_make_closed_block(cls="Higiene"))

    session = sa.get_db_session()
    try:
        rows = session.query(sa.ActivityWindow).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.device_id == orm.device_id
        assert row.activity_category == "hygiene"  # traduzido de "Higiene" pelo chamador
        assert row.start_time == 600
        assert row.end_time == 612
        # round(12.5) == 12 em Python (banker's rounding, arredonda para o
        # par mais próximo) — não 13 como um arredondamento "escolar" daria.
        assert row.duration_minutes == 12
        assert row.confidence == pytest.approx(0.87)
    finally:
        session.close()


def test_activity_window_disabled_orm_is_a_noop():
    orm = orm_persistence.OrmPersistence()
    orm.disabled = True
    orm.insert_activity_window(_make_closed_block())  # não deve lançar exceção

    session = sa.get_db_session()
    try:
        assert session.query(sa.ActivityWindow).count() == 0
    finally:
        session.close()


# ---------------------------------------------------------------------------
# (d) auditoria GDPR-003 — get_history real via BleBridge gera audit_log
# ---------------------------------------------------------------------------

def test_get_history_generates_audit_log_entry(tmp_path, monkeypatch):
    import asyncio
    import json

    import storage
    import ble_bridge

    # Nunca tocar na carewear_history.db real — storage.py numa BD temporária.
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "hist.db")

    class FakeWebSocket:
        def __init__(self):
            self.sent = []

        async def send(self, message):
            self.sent.append(json.loads(message))

    bridge = ble_bridge.BleBridge()
    assert bridge.orm is not None and not bridge.orm.disabled

    ws = FakeWebSocket()
    asyncio.run(bridge.handle_dashboard_command(ws, json.dumps({"cmd": "get_history", "hours": 12})))

    # Respondeu ao cliente com o histórico...
    assert any(m.get("kind") == "history" for m in ws.sent)

    # ...e deixou uma entrada de auditoria com a action correta.
    session = sa.get_db_session()
    try:
        audits = session.query(sa.AuditLog).filter_by(action="sensor_records.read").all()
        assert len(audits) == 1
        assert audits[0].resource_type == "sensor_records"
        assert audits[0].details == {"hours": 12.0}
        # Canal WS não autenticado -> user_id fica None (decisão documentada).
        assert audits[0].user_id is None
    finally:
        session.close()


# ---------------------------------------------------------------------------
# (d.2) consentimento GDPR-001/GDPR-003 no bootstrap
# ---------------------------------------------------------------------------

def test_bootstrap_without_consent_logs_consent_missing():
    """Sem ConsentRecord válido, o bootstrap não bloqueia (orm ativo) mas
    regista explicitamente a ausência em audit_log (não ignora em silêncio)."""
    orm = orm_persistence.OrmPersistence()
    assert not orm.disabled  # arranque nunca bloqueia por falta de consentimento

    session = sa.get_db_session()
    try:
        audits = session.query(sa.AuditLog).filter_by(action="consent_missing").all()
        assert len(audits) == 1
        assert audits[0].resource_type == "patient"
        assert audits[0].resource_id == orm.patient_id
        assert audits[0].details == {"scope": "sensor_data"}
    finally:
        session.close()


def test_bootstrap_with_valid_representative_consent_is_silent():
    """Com um ConsentRecord válido de um representante legal (given_by=
    'representative'), o bootstrap segue o fluxo normal sem consent_missing."""
    # Pré-cria paciente + consentimento de representante ANTES do bootstrap.
    session = sa.get_db_session()
    try:
        patient = sa.Patient(
            uuid=orm_persistence.DEFAULT_PATIENT_UUID,
            name="Paciente Local",
            date_of_birth=orm_persistence.PLACEHOLDER_DOB,
        )
        session.add(patient)
        session.commit()
        session.refresh(patient)
        user = sa.User(
            uuid="rep-user-1", email="rep@example.com",
            password_hash="x", role="family", name="Filha Cuidadora",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        session.add(sa.ConsentRecord(
            patient_id=patient.id, user_id=user.id, scope="sensor_data",
            granted=True, version="1.0", signed_at=datetime.utcnow(),
            given_by="representative", representative_relationship="filha",
            representative_name="Filha Cuidadora", legal_basis="consent",
        ))
        session.commit()
    finally:
        session.close()

    orm = orm_persistence.OrmPersistence()
    assert not orm.disabled

    session = sa.get_db_session()
    try:
        assert session.query(sa.AuditLog).filter_by(action="consent_missing").count() == 0
    finally:
        session.close()


def test_bootstrap_ignores_expired_consent_and_logs_missing():
    """Um ConsentRecord já expirado (expires_at no passado) não conta como
    válido — o bootstrap regista consent_missing na mesma."""
    session = sa.get_db_session()
    try:
        patient = sa.Patient(
            uuid=orm_persistence.DEFAULT_PATIENT_UUID,
            name="Paciente Local",
            date_of_birth=orm_persistence.PLACEHOLDER_DOB,
        )
        session.add(patient)
        session.commit()
        session.refresh(patient)
        user = sa.User(
            uuid="rep-user-2", email="rep2@example.com",
            password_hash="x", role="family", name="Tutor",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        session.add(sa.ConsentRecord(
            patient_id=patient.id, user_id=user.id, scope="sensor_data",
            granted=True, version="1.0", signed_at=datetime.utcnow() - timedelta(days=400),
            expires_at=datetime.utcnow() - timedelta(days=1),  # já expirado
            given_by="representative", legal_basis="consent",
        ))
        session.commit()
    finally:
        session.close()

    orm_persistence.OrmPersistence()

    session = sa.get_db_session()
    try:
        assert session.query(sa.AuditLog).filter_by(action="consent_missing").count() == 1
    finally:
        session.close()


# ---------------------------------------------------------------------------
# (e) purge respeita os `days` configuráveis
# ---------------------------------------------------------------------------

def test_purge_respects_configurable_days():
    orm = orm_persistence.OrmPersistence()
    session = sa.get_db_session()
    try:
        old = sa.SensorRecord(
            device_id=orm.device_id, timestamp_utc=1,
            received_at=datetime.utcnow() - timedelta(days=40),
        )
        recent = sa.SensorRecord(
            device_id=orm.device_id, timestamp_utc=2,
            received_at=datetime.utcnow() - timedelta(days=5),
        )
        session.add_all([old, recent])
        session.commit()
        assert session.query(sa.SensorRecord).count() == 2
    finally:
        session.close()

    # Retenção de 30 dias -> apaga o de 40 dias, mantém o de 5 dias.
    orm.purge(30)

    session = sa.get_db_session()
    try:
        remaining = session.query(sa.SensorRecord).all()
        assert len(remaining) == 1
        assert remaining[0].timestamp_utc == 2
    finally:
        session.close()


def test_purge_uses_days_not_fixed_365_policy():
    """Paridade com a retenção configurável do storage.py: um registo de
    100 dias tem de ser apagado por purge(30), coisa que os 365 dias fixos
    de DataRetention.RETENTION_POLICIES nunca fariam."""
    orm = orm_persistence.OrmPersistence()
    session = sa.get_db_session()
    try:
        session.add(sa.SensorRecord(
            device_id=orm.device_id, timestamp_utc=1,
            received_at=datetime.utcnow() - timedelta(days=100),
        ))
        session.commit()
    finally:
        session.close()

    orm.purge(30)

    session = sa.get_db_session()
    try:
        assert session.query(sa.SensorRecord).count() == 0
    finally:
        session.close()


# ---------------------------------------------------------------------------
# (f) degradação: erro injetado desativa o dual-write sem rebentar
# ---------------------------------------------------------------------------

def test_injected_commit_failure_disables_dual_write():
    orm = orm_persistence.OrmPersistence()
    assert not orm.disabled

    def _boom():
        raise RuntimeError("commit falhou de propósito")

    monkeypatch_commit(orm, _boom)

    # A auditoria (commit imediato) apanha o erro, avisa e desativa —
    # NUNCA propaga a exceção para o chamador (o streaming não pode cair).
    orm.audit(action="sensor_records.read", resource_type="sensor_records")
    assert orm.disabled is True

    # Chamadas seguintes tornam-se no-ops silenciosos (não rebentam).
    orm.audit(action="qualquer")
    orm.insert_emergency_alert({"alert_type": 1, "alert_name": "sos_manual", "seq": 99, "timestamp_utc": 1})
    orm.insert_sensor_record(_make_record())
    orm.purge(30)  # não deve rebentar


def monkeypatch_commit(orm, func):
    """Substitui session.commit por `func` (usado só no teste de falha)."""
    orm.session.commit = func
