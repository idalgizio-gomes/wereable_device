"""Testes para `bridge/api.py` (API REST somente-leitura sobre Analytics).

Corre inteiramente contra SQLite em memória (ver conftest.py), com
`starlette.testclient.TestClient` — não sobe um servidor HTTP real.
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import api
import storage_advanced as sa

API_KEY = "chave-de-teste"


@pytest.fixture(autouse=True)
def _fresh_schema():
    sa.Base.metadata.drop_all(bind=sa.engine)
    sa.Base.metadata.create_all(bind=sa.engine)
    yield
    sa.Base.metadata.drop_all(bind=sa.engine)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv(api.API_KEY_ENV_VAR, API_KEY)
    yield


@pytest.fixture
def db():
    session = sa.get_db_session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    return TestClient(api.app)


def _make_patient_device(db, uuid_suffix="1", mac="AA:BB:CC:DD:EE:01"):
    patient = sa.Patient(uuid=f"pat-{uuid_suffix}", name="Maria Silva", date_of_birth=datetime(1945, 3, 1))
    db.add(patient)
    db.commit()
    db.refresh(patient)
    device = sa.Device(uuid=f"dev-{uuid_suffix}", patient_id=patient.id, mac_address=mac)
    db.add(device)
    db.commit()
    db.refresh(device)
    return patient, device


class TestHealth:
    def test_health_no_auth_required(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestAuth:
    def test_missing_api_key_env_var_fails_closed(self, client, monkeypatch):
        monkeypatch.delenv(api.API_KEY_ENV_VAR, raising=False)
        resp = client.get("/api/devices/1/heart-rate-trends", headers={"X-API-Key": "qualquer"})
        assert resp.status_code == 503

    def test_missing_header_rejected(self, client):
        resp = client.get("/api/devices/1/heart-rate-trends")
        assert resp.status_code == 401

    def test_wrong_key_rejected(self, client):
        resp = client.get("/api/devices/1/heart-rate-trends", headers={"X-API-Key": "errada"})
        assert resp.status_code == 401

    def test_correct_key_accepted(self, client, db):
        _, device = _make_patient_device(db)
        resp = client.get(
            f"/api/devices/{device.id}/heart-rate-trends",
            headers={"X-API-Key": API_KEY},
        )
        assert resp.status_code == 200


class TestHeartRateTrends:
    def test_unknown_device_404(self, client):
        resp = client.get("/api/devices/9999/heart-rate-trends", headers={"X-API-Key": API_KEY})
        assert resp.status_code == 404

    def test_returns_records_within_window(self, client, db):
        _, device = _make_patient_device(db)
        now = datetime.utcnow()
        recent = sa.SensorRecord(
            device_id=device.id,
            timestamp_utc=int(now.timestamp()),
            heart_rate=72,
        )
        old = sa.SensorRecord(
            device_id=device.id,
            timestamp_utc=int((now - timedelta(days=30)).timestamp()),
            heart_rate=200,  # fora da janela de 7 dias por omissão, não deve contar
        )
        db.add_all([recent, old])
        db.commit()

        resp = client.get(f"/api/devices/{device.id}/heart-rate-trends", headers={"X-API-Key": API_KEY})
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["avg"] == 72


class TestMedicationAdherence:
    def test_unknown_patient_404(self, client):
        resp = client.get("/api/patients/9999/medication-adherence", headers={"X-API-Key": API_KEY})
        assert resp.status_code == 404

    def test_computes_percent_taken(self, client, db):
        patient, _ = _make_patient_device(db)
        med = sa.Medication(
            uuid="med-1", patient_id=patient.id, name="Donepezilo",
            dosage="5mg", frequency="1x/dia", start_date=datetime.utcnow(),
        )
        db.add(med)
        db.commit()
        db.refresh(med)
        db.add_all([
            sa.MedicationAdherence(medication_id=med.id, scheduled_datetime=datetime.utcnow(), taken=True),
            sa.MedicationAdherence(medication_id=med.id, scheduled_datetime=datetime.utcnow(), taken=False),
        ])
        db.commit()

        resp = client.get(f"/api/patients/{patient.id}/medication-adherence", headers={"X-API-Key": API_KEY})
        assert resp.status_code == 200
        body = resp.json()
        assert body["medications"][0]["taken"] == 1
        assert body["medications"][0]["total"] == 2
        assert body["medications"][0]["percent"] == 50.0


class TestActivityDistribution:
    def test_invalid_date_format_400(self, client, db):
        _, device = _make_patient_device(db)
        resp = client.get(
            f"/api/devices/{device.id}/activity-distribution",
            params={"date": "07-07-2026"},
            headers={"X-API-Key": API_KEY},
        )
        assert resp.status_code == 400

    def test_unknown_device_404(self, client):
        resp = client.get(
            "/api/devices/9999/activity-distribution",
            params={"date": "2026-07-07"},
            headers={"X-API-Key": API_KEY},
        )
        assert resp.status_code == 404

    def test_aggregates_by_category(self, client, db):
        _, device = _make_patient_device(db)
        day = datetime(2026, 7, 7, 22, 0)
        db.add(sa.ActivityWindow(
            device_id=device.id, activity_date=day, activity_category="sleep",
            start_time=1320, end_time=1440, duration_minutes=120, confidence=0.9,
        ))
        db.commit()

        resp = client.get(
            f"/api/devices/{device.id}/activity-distribution",
            params={"date": "2026-07-07"},
            headers={"X-API-Key": API_KEY},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["sleep"]["duration_minutes"] == 120
        assert body["sleep"]["windows_count"] == 1
        assert body["rest"]["duration_minutes"] == 0
