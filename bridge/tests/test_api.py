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

    def test_empty_key_rejected(self, client):
        resp = client.get("/api/devices/1/heart-rate-trends", headers={"X-API-Key": ""})
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


def _make_medication(db, patient):
    med = sa.Medication(
        uuid="med-adh-1", patient_id=patient.id, name="Donepezilo",
        dosage="5mg", frequency="1x/dia", start_date=datetime.utcnow(),
    )
    db.add(med)
    db.commit()
    db.refresh(med)
    return med


class TestRecordMedicationAdherence:
    def test_requires_api_key(self, client, db):
        patient, _ = _make_patient_device(db)
        med = _make_medication(db, patient)
        resp = client.post(
            f"/api/medications/{med.id}/adherence",
            json={"scheduled_datetime": "2026-07-08T08:00:00", "taken": True},
        )
        assert resp.status_code == 401

    def test_unknown_medication_404(self, client):
        resp = client.post(
            "/api/medications/9999/adherence",
            json={"scheduled_datetime": "2026-07-08T08:00:00", "taken": True},
            headers={"X-API-Key": API_KEY},
        )
        assert resp.status_code == 404

    def test_creates_record_with_taken_at(self, client, db):
        patient, _ = _make_patient_device(db)
        med = _make_medication(db, patient)
        resp = client.post(
            f"/api/medications/{med.id}/adherence",
            json={"scheduled_datetime": "2026-07-08T08:00:00", "taken": True, "method": "manual_entry"},
            headers={"X-API-Key": API_KEY},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["taken"] is True
        assert body["taken_at"] is not None
        assert body["method"] == "manual_entry"

        rows = db.query(sa.MedicationAdherence).filter(sa.MedicationAdherence.medication_id == med.id).all()
        assert len(rows) == 1

        audit = db.query(sa.AuditLog).filter(sa.AuditLog.action == "medication_adherence.write").all()
        assert len(audit) == 1
        assert audit[0].resource_id == med.id

    def test_repeated_call_updates_instead_of_duplicating(self, client, db):
        patient, _ = _make_patient_device(db)
        med = _make_medication(db, patient)
        payload = {"scheduled_datetime": "2026-07-08T08:00:00", "taken": True}
        client.post(f"/api/medications/{med.id}/adherence", json=payload, headers={"X-API-Key": API_KEY})
        resp2 = client.post(f"/api/medications/{med.id}/adherence", json=payload, headers={"X-API-Key": API_KEY})
        assert resp2.status_code == 200

        rows = db.query(sa.MedicationAdherence).filter(sa.MedicationAdherence.medication_id == med.id).all()
        assert len(rows) == 1

    def test_concurrent_requests_for_same_dose_never_duplicate(self, client, db, monkeypatch):
        """Regressão: antes da UniqueConstraint em MedicationAdherence
        (storage_advanced.py), o SELECT e o INSERT deste endpoint não eram
        atómicos — dois pedidos concorrentes para a MESMA dose podiam ambos
        ver "não existe" e ambos inserir, criando duas linhas (reproduzido
        diretamente contra storage_advanced.py antes da correção). Este
        teste simula essa corrida de forma determinística: injeta, no
        momento exato do primeiro commit deste pedido, uma escrita
        "concorrente" da MESMA dose por outra sessão — que só é possível
        de reproduzir de forma fiável forçando o ponto de interleaving
        (threads reais tornariam o teste inconsistente/flaky). O commit
        deste pedido tem então de falhar por violação da constraint e o
        endpoint deve recuperar sozinho (retry como UPDATE), nunca
        devolver 500 nem deixar duas linhas."""
        patient, _ = _make_patient_device(db)
        med = _make_medication(db, patient)
        scheduled_dt = datetime(2026, 7, 10, 8, 0, 0)
        payload = {"scheduled_datetime": scheduled_dt.isoformat(), "taken": True, "method": "manual_entry"}

        real_commit = sa.Session.commit
        call_count = {"n": 0}

        def flaky_commit(self):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Outra sessão "ganha a corrida": commita a mesma dose antes
                # deste pedido conseguir commitar o seu próprio INSERT.
                other_db = sa.get_db_session()
                other_db.add(sa.MedicationAdherence(
                    medication_id=med.id, scheduled_datetime=scheduled_dt,
                    taken=False, method="wearable_detection",
                ))
                other_db.commit()
                other_db.close()
            return real_commit(self)

        monkeypatch.setattr(sa.Session, "commit", flaky_commit)

        resp = client.post(f"/api/medications/{med.id}/adherence", json=payload, headers={"X-API-Key": API_KEY})

        assert resp.status_code == 200
        assert resp.json()["taken"] is True  # o retry aplicou o UPDATE com os dados deste pedido

        rows = db.query(sa.MedicationAdherence).filter(sa.MedicationAdherence.medication_id == med.id).all()
        assert len(rows) == 1

        # A escrita "concorrente" também gera auditoria — as duas tentativas
        # deste pedido (a que falhou + o retry) não devem, cada uma, deixar
        # o seu próprio registo de auditoria: só a tentativa que teve
        # sucesso é que persiste (rollback descarta tudo o resto da
        # transação falhada, incluindo o AuditLog dessa tentativa).
        audit = db.query(sa.AuditLog).filter(sa.AuditLog.resource_id == med.id).all()
        assert len(audit) == 1

    def test_taken_false_clears_taken_at(self, client, db):
        patient, _ = _make_patient_device(db)
        med = _make_medication(db, patient)
        payload = {"scheduled_datetime": "2026-07-08T08:00:00", "taken": True}
        client.post(f"/api/medications/{med.id}/adherence", json=payload, headers={"X-API-Key": API_KEY})

        resp = client.post(
            f"/api/medications/{med.id}/adherence",
            json={"scheduled_datetime": "2026-07-08T08:00:00", "taken": False},
            headers={"X-API-Key": API_KEY},
        )
        assert resp.status_code == 200
        assert resp.json()["taken"] is False
        assert resp.json()["taken_at"] is None

    def test_invalid_method_rejected(self, client, db):
        patient, _ = _make_patient_device(db)
        med = _make_medication(db, patient)
        resp = client.post(
            f"/api/medications/{med.id}/adherence",
            json={"scheduled_datetime": "2026-07-08T08:00:00", "taken": True, "method": "carrier_pigeon"},
            headers={"X-API-Key": API_KEY},
        )
        assert resp.status_code == 422
