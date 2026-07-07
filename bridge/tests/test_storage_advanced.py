"""Testes unitários para `bridge/storage_advanced.py` (ORM SQLAlchemy).

Primeira suite de testes deste módulo (item pendente em PROJECT_STATUS.md,
secção "Base de Dados SQL Completa" -> "Próximas fases": "Testes unitários
com pytest"). Corre inteiramente contra SQLite em memória (ver conftest.py),
nunca contra `carewear.db` real.

Nota: este ficheiro também serve de regressão para bugs reais encontrados
ao tentar correr o módulo pela primeira vez (nunca tinha sido importado
com sucesso). Uma sessão paralela corrigiu, na mesma janela de tempo, o
`ImportError` de `JSONB` (só existe em `sqlalchemy.dialects.postgresql`),
a tabela de associação `patient_caregivers` em falta e um bug de fuso
horário em `heart_rate_trends` -- não repetidos aqui. Esta suite cobre
adicionalmente:
1. `DataRetention.cleanup()` assumia `Alert.deleted_at`, coluna que não
   existia no modelo `Alert` (a documentação já descrevia soft delete de
   alertas, o código não implementava a coluna).
2. `Analytics.daily_activity_distribution()` comparava uma coluna DateTime
   diretamente com `date.date()` -- nunca encontrava nada.
3. `DataRetention.RETENTION_POLICIES` declarava 6 políticas mas
   `cleanup()` só aplicava 3 -- `anomaly_detections` e
   `medication_adherence` nunca eram purgados apesar de a documentação
   já afirmar que o eram.
"""
from datetime import datetime, timedelta

import pytest

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


def _make_user(db, uuid="usr-1", email="joao@example.com", role="family"):
    user = sa.User(
        uuid=uuid, email=email, password_hash="hash", role=role, name="Joao",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


class TestSchemaCreation:
    """Regressão: o módulo tem de importar e criar todas as tabelas sem erro."""

    def test_create_all_tables_succeeds(self):
        # A fixture _fresh_schema já corre create_all; se o import ou o
        # mapeamento ORM estiver partido, já teria rebentado antes de
        # chegarmos aqui.
        table_names = set(sa.Base.metadata.tables.keys())
        assert "patient_caregivers" in table_names
        assert "users" in table_names
        assert "alerts" in table_names


class TestPatientCaregiverAssociation:
    """`patient_caregivers` liga User<->Patient (backlog "Equipa de cuidadores")."""

    def test_link_user_to_patient_via_secondary_table(self, db):
        patient = _make_patient(db)
        user = _make_user(db)

        db.execute(
            sa.patient_caregivers.insert().values(
                patient_id=patient.id, user_id=user.id,
                can_view_alerts=True, can_edit_notes=True, can_edit_medications=False,
            )
        )
        db.commit()

        reloaded = db.query(sa.User).filter_by(id=user.id).one()
        assert [p.id for p in reloaded.patients] == [patient.id]


class TestAlertSoftDelete:
    def test_alert_has_deleted_at_column(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)
        alert = sa.Alert(
            uuid="alert-1", device_id=device.id, alert_type="fall",
            severity="critical", title="Queda detetada",
        )
        db.add(alert)
        db.commit()
        assert alert.deleted_at is None


class TestAnalyticsHeartRateTrends:
    def test_empty_when_no_records(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)
        result = sa.Analytics.heart_rate_trends(db, device.id, days=7)
        assert result == {"count": 0, "avg": 0, "min": 0, "max": 0, "records": []}

    def test_computes_avg_min_max_over_recent_records(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)
        now = datetime.utcnow()
        for hr, hours_ago in [(60, 1), (80, 2), (100, 3)]:
            db.add(sa.SensorRecord(
                device_id=device.id,
                timestamp_utc=int((now - timedelta(hours=hours_ago)).timestamp()),
                heart_rate=hr,
            ))
        db.commit()

        result = sa.Analytics.heart_rate_trends(db, device.id, days=7)
        assert result["count"] == 3
        assert result["avg"] == 80
        assert result["min"] == 60
        assert result["max"] == 100

    def test_ignores_records_outside_the_day_window(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)
        now = datetime.utcnow()
        db.add(sa.SensorRecord(
            device_id=device.id,
            timestamp_utc=int((now - timedelta(days=30)).timestamp()),
            heart_rate=200,
        ))
        db.commit()

        result = sa.Analytics.heart_rate_trends(db, device.id, days=7)
        assert result["count"] == 0

    def test_ignores_records_without_heart_rate(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)
        db.add(sa.SensorRecord(
            device_id=device.id,
            timestamp_utc=int(datetime.utcnow().timestamp()),
            heart_rate=None,
        ))
        db.commit()

        result = sa.Analytics.heart_rate_trends(db, device.id, days=7)
        assert result["count"] == 0


class TestAnalyticsMedicationAdherence:
    def test_summary_with_no_medications(self, db):
        patient = _make_patient(db)
        result = sa.Analytics.medication_adherence_summary(db, patient.id, days=30)
        assert result == {"period_days": 30, "medications": [], "overall_percent": 0}

    def test_summary_computes_percent_taken(self, db):
        patient = _make_patient(db)
        med = sa.Medication(
            uuid="med-1", patient_id=patient.id, name="Donepezilo",
            dosage="5mg", frequency="1x/dia", start_date=datetime.utcnow(),
        )
        db.add(med)
        db.commit()
        db.refresh(med)

        now = datetime.utcnow()
        db.add_all([
            sa.MedicationAdherence(medication_id=med.id, scheduled_datetime=now, taken=True),
            sa.MedicationAdherence(medication_id=med.id, scheduled_datetime=now, taken=False),
        ])
        db.commit()

        result = sa.Analytics.medication_adherence_summary(db, patient.id, days=30)
        assert result["medications"][0]["taken"] == 1
        assert result["medications"][0]["total"] == 2
        assert result["medications"][0]["percent"] == 50.0
        assert result["overall_percent"] == 50.0


class TestAnalyticsDailyActivityDistribution:
    def test_distribution_groups_by_category(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)
        day = datetime(2026, 7, 1)
        db.add_all([
            sa.ActivityWindow(
                device_id=device.id, activity_date=day, activity_category="sleep",
                start_time=0, end_time=300, duration_minutes=300,
            ),
            sa.ActivityWindow(
                device_id=device.id, activity_date=day, activity_category="sleep",
                start_time=400, end_time=460, duration_minutes=60,
            ),
        ])
        db.commit()

        result = sa.Analytics.daily_activity_distribution(db, device.id, day)
        assert result["sleep"]["duration_minutes"] == 360
        assert result["sleep"]["windows_count"] == 2
        assert result["sleep"]["average_window_minutes"] == 180
        assert result["rest"]["windows_count"] == 0


class TestDataRetention:
    def test_dry_run_reports_but_does_not_delete(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)
        old_ts = int((datetime.utcnow() - timedelta(days=400)).timestamp())
        record = sa.SensorRecord(device_id=device.id, timestamp_utc=old_ts, heart_rate=70)
        record.received_at = datetime.utcnow() - timedelta(days=400)
        db.add(record)
        db.commit()

        result = sa.DataRetention.cleanup(db, dry_run=True)
        assert result["sensor_records"] == 1
        assert db.query(sa.SensorRecord).count() == 1

    def test_deletes_sensor_records_older_than_retention(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)
        old_record = sa.SensorRecord(
            device_id=device.id, timestamp_utc=0, heart_rate=70,
        )
        old_record.received_at = datetime.utcnow() - timedelta(days=400)
        recent_record = sa.SensorRecord(
            device_id=device.id, timestamp_utc=0, heart_rate=75,
        )
        recent_record.received_at = datetime.utcnow() - timedelta(days=1)
        db.add_all([old_record, recent_record])
        db.commit()

        result = sa.DataRetention.cleanup(db, dry_run=False)
        assert result["sensor_records"] == 1
        remaining = db.query(sa.SensorRecord).all()
        assert len(remaining) == 1
        assert remaining[0].heart_rate == 75

    def test_alerts_are_soft_deleted_not_removed(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)
        old_alert = sa.Alert(
            uuid="alert-old", device_id=device.id, alert_type="anomaly",
            severity="warning", title="velho",
        )
        old_alert.created_at = datetime.utcnow() - timedelta(days=3000)
        db.add(old_alert)
        db.commit()

        sa.DataRetention.cleanup(db, dry_run=False)

        reloaded = db.query(sa.Alert).filter_by(uuid="alert-old").one()
        assert reloaded.deleted_at is not None

    def test_anomaly_detections_purged_after_five_years(self, db):
        patient = _make_patient(db)
        device = _make_device(db, patient)
        old = sa.AnomalyDetection(
            device_id=device.id, anomaly_type="wandering",
            start_datetime=datetime.utcnow(),
        )
        old.created_at = datetime.utcnow() - timedelta(days=1826)
        db.add(old)
        db.commit()

        result = sa.DataRetention.cleanup(db, dry_run=False)
        assert result["anomaly_detections"] == 1
        assert db.query(sa.AnomalyDetection).count() == 0

    def test_medication_adherence_purged_after_three_years(self, db):
        patient = _make_patient(db)
        med = sa.Medication(
            uuid="med-old", patient_id=patient.id, name="Donepezilo",
            dosage="5mg", frequency="1x/dia", start_date=datetime.utcnow(),
        )
        db.add(med)
        db.commit()
        db.refresh(med)

        db.add(sa.MedicationAdherence(
            medication_id=med.id,
            scheduled_datetime=datetime.utcnow() - timedelta(days=1096),
            taken=True,
        ))
        db.commit()

        result = sa.DataRetention.cleanup(db, dry_run=False)
        assert result["medication_adherence"] == 1
        assert db.query(sa.MedicationAdherence).count() == 0

    def test_emergency_alerts_are_never_purged(self, db):
        """`emergency_alerts` está em RETENTION_POLICIES só como referência
        documental (10 anos) -- `cleanup()` nunca a processa de facto,
        histórico de segurança mantido para sempre."""
        patient = _make_patient(db)
        device = _make_device(db, patient)
        old_emergency = sa.EmergencyAlert(
            uuid="em-1", device_id=device.id, alert_type="sos_manual",
            timestamp_utc=0,
        )
        old_emergency.created_at = datetime.utcnow() - timedelta(days=9000)
        db.add(old_emergency)
        db.commit()

        sa.DataRetention.cleanup(db, dry_run=False)

        assert db.query(sa.EmergencyAlert).count() == 1
