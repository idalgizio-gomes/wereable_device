"""Testes para a "Timeline correlacionada por episódio" (2026-08-05,
`bridge/storage_advanced.py::build_episode_timeline`/
`get_episode_timeline_for_alert`).

Corre inteiramente contra SQLite em memória (ver conftest.py), mesmo
padrão/fixtures de `test_storage_advanced.py` (`_fresh_schema`, `db`,
`_make_patient`/`_make_device`). Cobre:

  1. janela sem dados nenhuns -- listas vazias, sem exceção;
  2. sensor_summary agrupado por minuto, ignorando minutos sem hr/spo2;
  3. activity_blocks -- sobreposição parcial incluída, bloco fora excluído;
  4. nearby_emergency_alerts -- dentro/fora da janela, e o alerta central
     nunca aparece como "nearby";
  5. get_episode_timeline_for_alert com sequence_number inexistente lança
     ValueError;
  6. o wrapper OrmPersistence.get_episode_timeline funciona end-to-end.
"""
from datetime import datetime, timedelta, timezone

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


@pytest.fixture
def db():
    session = sa.get_db_session()
    try:
        yield session
    finally:
        session.close()


def _make_patient(db, uuid="pat-1", name="Maria Silva"):
    patient = sa.Patient(uuid=uuid, name=name, date_of_birth=datetime(1945, 3, 1))
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


def _make_device(db, patient, uuid="dev-1", mac="AA:BB:CC:DD:EE:FF"):
    device = sa.Device(uuid=uuid, patient_id=patient.id, mac_address=mac)
    db.add(device)
    db.commit()
    db.refresh(device)
    return device


def _received_at_for(ts: int) -> datetime:
    """Converte um epoch (UTC) para o datetime naive que
    SensorRecord.received_at usaria -- mesma convenção do resto do
    ficheiro (ver get_records_since/build_episode_timeline)."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


CENTER_TS = 1_800_000_000  # instante de referência arbitrário (epoch, segundos)


class TestBuildEpisodeTimelineEmptyWindow:
    def test_empty_window_returns_empty_lists_without_raising(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)

        result = sa.build_episode_timeline(db, device.id, CENTER_TS, window_minutes=30)

        assert result["center_ts"] == CENTER_TS
        assert result["window_minutes"] == 30
        assert result["sensor_summary"] == []
        assert result["activity_blocks"] == []
        assert result["nearby_emergency_alerts"] == []


class TestSensorSummaryDownsampling:
    def test_groups_by_minute_and_averages_non_null_readings(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)

        # Dois registos no MESMO minuto (hr=70 e hr=80 -> média 75); um
        # terceiro registo um minuto depois só com spo2 (sem hr).
        db.add_all([
            sa.SensorRecord(
                device_id=device.id, timestamp_utc=CENTER_TS, heart_rate=70,
                spo2_percent=94, received_at=_received_at_for(CENTER_TS),
            ),
            sa.SensorRecord(
                device_id=device.id, timestamp_utc=CENTER_TS + 10, heart_rate=80,
                spo2_percent=96, received_at=_received_at_for(CENTER_TS + 10),
            ),
            sa.SensorRecord(
                device_id=device.id, timestamp_utc=CENTER_TS + 60, heart_rate=None,
                spo2_percent=93, received_at=_received_at_for(CENTER_TS + 60),
            ),
        ])
        db.commit()

        result = sa.build_episode_timeline(db, device.id, CENTER_TS, window_minutes=5)
        summary = result["sensor_summary"]

        assert len(summary) == 2
        first_minute_ts = (CENTER_TS // 60) * 60
        assert summary[0] == {"ts": first_minute_ts, "hr": 75, "spo2": 95}
        assert summary[1] == {"ts": first_minute_ts + 60, "hr": None, "spo2": 93}

    def test_ignores_minutes_without_any_hr_or_spo2_reading(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)

        # Um minuto com hr/spo2 ambos None (ex.: só dados de acelerómetro)
        # não deve gerar nenhuma entrada em sensor_summary.
        db.add(sa.SensorRecord(
            device_id=device.id, timestamp_utc=CENTER_TS, heart_rate=None,
            spo2_percent=None, accel_x=0.1, received_at=_received_at_for(CENTER_TS),
        ))
        db.commit()

        result = sa.build_episode_timeline(db, device.id, CENTER_TS, window_minutes=5)
        assert result["sensor_summary"] == []

    def test_records_outside_window_are_not_included(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)

        far_ts = CENTER_TS + 3600  # 1h depois, fora de uma janela de 5 min
        db.add(sa.SensorRecord(
            device_id=device.id, timestamp_utc=far_ts, heart_rate=100,
            received_at=_received_at_for(far_ts),
        ))
        db.commit()

        result = sa.build_episode_timeline(db, device.id, CENTER_TS, window_minutes=5)
        assert result["sensor_summary"] == []


class TestActivityBlocksOverlap:
    def _make_block(self, db, device, start_time, end_time, activity_date, category="rest"):
        block = sa.ActivityWindow(
            device_id=device.id,
            activity_date=activity_date,
            activity_category=category,
            start_time=start_time,
            end_time=end_time,
            duration_minutes=end_time - start_time,
            confidence=0.8,
        )
        db.add(block)
        db.commit()
        return block

    def test_partially_overlapping_block_is_included(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)
        # Bloco que cobre o dia inteiro local -- sobrepõe-se seguramente a
        # qualquer janela pequena à volta de CENTER_TS, independentemente
        # do fuso horário do servidor de testes.
        activity_date = datetime.fromtimestamp(CENTER_TS, tz=timezone.utc)
        self._make_block(db, device, start_time=0, end_time=1439, activity_date=activity_date)

        result = sa.build_episode_timeline(db, device.id, CENTER_TS, window_minutes=5)

        assert len(result["activity_blocks"]) == 1
        block = result["activity_blocks"][0]
        assert block["category"] == "rest"
        assert block["duration_minutes"] == 1439
        assert block["confidence"] == pytest.approx(0.8)
        assert block["start_ts_approx"] <= CENTER_TS <= block["end_ts_approx"]

    def test_block_entirely_outside_window_is_excluded(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)
        # Bloco muitos dias antes de CENTER_TS -- não se sobrepõe a uma
        # janela pequena nem ao pré-filtro largo (+-2 dias).
        far_date = datetime.fromtimestamp(CENTER_TS, tz=timezone.utc) - timedelta(days=30)
        self._make_block(db, device, start_time=0, end_time=10, activity_date=far_date)

        result = sa.build_episode_timeline(db, device.id, CENTER_TS, window_minutes=5)
        assert result["activity_blocks"] == []


class TestNearbyEmergencyAlerts:
    def test_other_alert_inside_window_appears(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)
        db.add(sa.EmergencyAlert(
            uuid="em-nearby", device_id=device.id, alert_type="fall_inactivity",
            sequence_number=2, timestamp_utc=CENTER_TS + 60,
        ))
        db.commit()

        result = sa.build_episode_timeline(db, device.id, CENTER_TS, window_minutes=5)
        assert len(result["nearby_emergency_alerts"]) == 1
        assert result["nearby_emergency_alerts"][0]["sequence_number"] == 2

    def test_alert_outside_window_does_not_appear(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)
        db.add(sa.EmergencyAlert(
            uuid="em-far", device_id=device.id, alert_type="sos_manual",
            sequence_number=3, timestamp_utc=CENTER_TS + 3600,
        ))
        db.commit()

        result = sa.build_episode_timeline(db, device.id, CENTER_TS, window_minutes=5)
        assert result["nearby_emergency_alerts"] == []

    def test_central_alert_never_appears_as_nearby(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)
        db.add_all([
            sa.EmergencyAlert(
                uuid="em-central", device_id=device.id, alert_type="sos_manual",
                sequence_number=1, timestamp_utc=CENTER_TS,
            ),
            sa.EmergencyAlert(
                uuid="em-nearby", device_id=device.id, alert_type="fall_inactivity",
                sequence_number=2, timestamp_utc=CENTER_TS + 60,
            ),
        ])
        db.commit()

        result = sa.get_episode_timeline_for_alert(db, device.id, sequence_number=1, window_minutes=5)

        sequence_numbers = [a["sequence_number"] for a in result["nearby_emergency_alerts"]]
        assert 1 not in sequence_numbers
        assert 2 in sequence_numbers
        assert result["alert"]["sequence_number"] == 1
        assert result["alert"]["alert_type"] == "sos_manual"
        assert result["alert"]["timestamp_utc"] == CENTER_TS


class TestGetEpisodeTimelineForAlert:
    def test_unknown_sequence_number_raises_value_error(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)

        with pytest.raises(ValueError):
            sa.get_episode_timeline_for_alert(db, device.id, sequence_number=999)

    def test_uses_alert_timestamp_as_center(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)
        db.add(sa.EmergencyAlert(
            uuid="em-1", device_id=device.id, alert_type="sos_manual",
            sequence_number=5, timestamp_utc=CENTER_TS,
        ))
        db.add(sa.SensorRecord(
            device_id=device.id, timestamp_utc=CENTER_TS, heart_rate=88,
            received_at=_received_at_for(CENTER_TS),
        ))
        db.commit()

        result = sa.get_episode_timeline_for_alert(db, device.id, sequence_number=5, window_minutes=5)

        assert result["center_ts"] == CENTER_TS
        assert result["sensor_summary"][0]["hr"] == 88


class TestOrmPersistenceGetEpisodeTimeline:
    """Wrapper OrmPersistence.get_episode_timeline -- end-to-end contra uma
    instância real (mesmo padrão de test_orm_persistence.py: instancia
    OrmPersistence() diretamente, sem mocks)."""

    def test_wrapper_builds_timeline_for_real_alert(self):
        orm = orm_persistence.OrmPersistence()
        assert not orm.disabled

        orm.insert_emergency_alert({
            "alert_type": 1, "alert_name": "sos_manual", "seq": 42,
            "timestamp_utc": CENTER_TS,
        })

        timeline = orm.get_episode_timeline(sequence_number=42, window_minutes=10)

        assert timeline["alert"]["sequence_number"] == 42
        assert timeline["alert"]["alert_type"] == "sos_manual"
        assert timeline["center_ts"] == CENTER_TS
        assert timeline["sensor_summary"] == []
        assert timeline["nearby_emergency_alerts"] == []

    def test_wrapper_raises_value_error_for_unknown_sequence(self):
        orm = orm_persistence.OrmPersistence()
        assert not orm.disabled

        with pytest.raises(ValueError):
            orm.get_episode_timeline(sequence_number=12345)

    def test_wrapper_raises_runtime_error_when_disabled(self):
        orm = orm_persistence.OrmPersistence()
        orm.disabled = True

        with pytest.raises(RuntimeError):
            orm.get_episode_timeline(sequence_number=1)
