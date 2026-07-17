"""Testes para `bridge/api.py` (API REST sobre Analytics) — autenticação
por-utilizador (API-002), autorização por paciente (IDOR) e rate limiting
(API-003).

Corre inteiramente contra SQLite em memória (ver conftest.py), com
`starlette.testclient.TestClient` — não sobe um servidor HTTP real. As
fixtures de autenticação vivem AQUI (não em conftest.py, que é partilhado):
criam `User` + `api_auth.ApiKey` reais e usam a chave gerada nos headers,
em vez do antigo monkeypatch a uma variável de ambiente.
"""
import uuid as _uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import api
import api_auth
import storage_advanced as sa


@pytest.fixture(autouse=True)
def _fresh_schema():
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


@pytest.fixture
def client():
    return TestClient(api.app)


# ------------------------------------------------------------------
# Helpers de utilizadores / chaves / associações
# ------------------------------------------------------------------

def _make_user(db, email=None, role="family", name="Utilizador"):
    user = sa.User(
        uuid=str(_uuid.uuid4()),
        email=email or f"{_uuid.uuid4()}@example.com",
        password_hash="(bcrypt em produção)",
        role=role,
        name=name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _issue_key(db, user, label="test"):
    plaintext, key_hash = api_auth.generate_api_key()
    row = api_auth.ApiKey(user_id=user.id, key_hash=key_hash, label=label)
    db.add(row)
    db.commit()
    db.refresh(row)
    return plaintext, row


def _associate(db, patient, user, can_edit_medications=True):
    db.execute(sa.patient_caregivers.insert().values(
        patient_id=patient.id,
        user_id=user.id,
        can_view_alerts=True,
        can_edit_notes=True,
        can_edit_medications=can_edit_medications,
    ))
    db.commit()


class _Auth:
    def __init__(self, user, key):
        self.user = user
        self.key = key
        self.headers = {"X-API-Key": key}


@pytest.fixture
def primary(db):
    """Utilizador principal (família) com chave própria — o "dono" nos testes."""
    user = _make_user(db, email="familia@example.com", role="family", name="Família")
    key, _ = _issue_key(db, user)
    return _Auth(user, key)


@pytest.fixture
def intruder(db):
    """Segundo utilizador com chave VÁLIDA mas sem associação a paciente algum."""
    user = _make_user(db, email="intruso@example.com", role="family", name="Intruso")
    key, _ = _issue_key(db, user)
    return _Auth(user, key)


def _make_patient_device(db, caregiver=None, can_edit=True, uuid_suffix="1", mac="AA:BB:CC:DD:EE:01"):
    patient = sa.Patient(uuid=f"pat-{uuid_suffix}", name="Maria Silva", date_of_birth=datetime(1945, 3, 1))
    db.add(patient)
    db.commit()
    db.refresh(patient)
    device = sa.Device(uuid=f"dev-{uuid_suffix}", patient_id=patient.id, mac_address=mac)
    db.add(device)
    db.commit()
    db.refresh(device)
    if caregiver is not None:
        _associate(db, patient, caregiver, can_edit_medications=can_edit)
    return patient, device


def _make_medication(db, patient, suffix="1"):
    med = sa.Medication(
        uuid=f"med-adh-{suffix}", patient_id=patient.id, name="Donepezilo",
        dosage="5mg", frequency="1x/dia", start_date=datetime.utcnow(),
    )
    db.add(med)
    db.commit()
    db.refresh(med)
    return med


# ==================================================================
# HEALTH
# ==================================================================

class TestHealth:
    def test_health_no_auth_required(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ==================================================================
# AUTENTICAÇÃO (API-002)
# ==================================================================

class TestAuth:
    def test_no_keys_in_db_fails_closed(self, client):
        # Sem nenhuma ApiKey na BD, qualquer chave apresentada é 401.
        resp = client.get("/api/devices/1/heart-rate-trends", headers={"X-API-Key": "cw_qualquer"})
        assert resp.status_code == 401

    def test_missing_header_rejected(self, client, primary):
        resp = client.get("/api/devices/1/heart-rate-trends")
        assert resp.status_code == 401

    def test_unknown_key_rejected(self, client, primary):
        resp = client.get("/api/devices/1/heart-rate-trends", headers={"X-API-Key": "cw_naoexiste"})
        assert resp.status_code == 401

    def test_empty_key_rejected(self, client, primary):
        resp = client.get("/api/devices/1/heart-rate-trends", headers={"X-API-Key": ""})
        assert resp.status_code == 401

    def test_valid_key_accepted(self, client, db, primary):
        _, device = _make_patient_device(db, caregiver=primary.user)
        resp = client.get(
            f"/api/devices/{device.id}/heart-rate-trends",
            headers=primary.headers,
        )
        assert resp.status_code == 200

    def test_revoked_key_rejected(self, client, db):
        user = _make_user(db, role="family")
        key, row = _issue_key(db, user)
        _, device = _make_patient_device(db, caregiver=user)
        # Revogar por linha (o requisito de rotação do API-002).
        row.revoked_at = datetime.utcnow()
        db.commit()
        resp = client.get(f"/api/devices/{device.id}/heart-rate-trends", headers={"X-API-Key": key})
        assert resp.status_code == 401

    def test_last_used_at_updated_on_use(self, client, db, primary):
        _, device = _make_patient_device(db, caregiver=primary.user)
        resp = client.get(f"/api/devices/{device.id}/heart-rate-trends", headers=primary.headers)
        assert resp.status_code == 200
        row = db.query(api_auth.ApiKey).filter(api_auth.ApiKey.user_id == primary.user.id).first()
        db.refresh(row)
        assert row.last_used_at is not None


# ==================================================================
# AUTORIZAÇÃO POR PACIENTE / IDOR (API-002)
# ==================================================================

class TestIDOR:
    def test_caregiver_reads_own_patient_ok(self, client, db, primary):
        _, device = _make_patient_device(db, caregiver=primary.user)
        resp = client.get(f"/api/devices/{device.id}/heart-rate-trends", headers=primary.headers)
        assert resp.status_code == 200

    def test_intruder_cannot_read_other_patient_device_404(self, client, db, primary, intruder):
        # Paciente do `primary`; o `intruder` tem chave válida mas nenhuma associação.
        _, device = _make_patient_device(db, caregiver=primary.user)
        resp = client.get(f"/api/devices/{device.id}/heart-rate-trends", headers=intruder.headers)
        # 404 (não 403) — não distingue "não existe" de "existe mas não é teu".
        assert resp.status_code == 404

    def test_caregiver_of_p1_cannot_read_p2_medication_adherence(self, client, db, primary):
        patient1, _ = _make_patient_device(db, caregiver=primary.user, uuid_suffix="1", mac="AA:BB:CC:DD:EE:01")
        patient2, _ = _make_patient_device(db, caregiver=None, uuid_suffix="2", mac="AA:BB:CC:DD:EE:02")
        ok = client.get(f"/api/patients/{patient1.id}/medication-adherence", headers=primary.headers)
        assert ok.status_code == 200
        forbidden = client.get(f"/api/patients/{patient2.id}/medication-adherence", headers=primary.headers)
        assert forbidden.status_code == 404

    def test_caregiver_of_p1_cannot_read_p2_activity(self, client, db, primary):
        _, device1 = _make_patient_device(db, caregiver=primary.user, uuid_suffix="1", mac="AA:BB:CC:DD:EE:01")
        _, device2 = _make_patient_device(db, caregiver=None, uuid_suffix="2", mac="AA:BB:CC:DD:EE:02")
        ok = client.get(f"/api/devices/{device1.id}/activity-distribution", params={"date": "2026-07-07"}, headers=primary.headers)
        assert ok.status_code == 200
        forbidden = client.get(f"/api/devices/{device2.id}/activity-distribution", params={"date": "2026-07-07"}, headers=primary.headers)
        assert forbidden.status_code == 404

    def test_intruder_cannot_post_adherence_to_other_patient_med(self, client, db, primary, intruder):
        patient, _ = _make_patient_device(db, caregiver=primary.user)
        med = _make_medication(db, patient)
        resp = client.post(
            f"/api/medications/{med.id}/adherence",
            json={"scheduled_datetime": "2026-07-08T08:00:00", "taken": True},
            headers=intruder.headers,
        )
        assert resp.status_code == 404

    def test_family_without_edit_permission_cannot_write_404(self, client, db):
        user = _make_user(db, role="family")
        key, _ = _issue_key(db, user)
        patient, _ = _make_patient_device(db, caregiver=user, can_edit=False)
        med = _make_medication(db, patient)
        resp = client.post(
            f"/api/medications/{med.id}/adherence",
            json={"scheduled_datetime": "2026-07-08T08:00:00", "taken": True},
            headers={"X-API-Key": key},
        )
        assert resp.status_code == 404

    def test_clinician_associated_can_write_even_without_edit_flag(self, client, db):
        clinician = _make_user(db, role="clinician", name="Dra. Ana")
        key, _ = _issue_key(db, clinician)
        # Associado mas can_edit_medications=False — o papel clínico é que autoriza a escrita.
        patient, _ = _make_patient_device(db, caregiver=clinician, can_edit=False)
        med = _make_medication(db, patient)
        resp = client.post(
            f"/api/medications/{med.id}/adherence",
            json={"scheduled_datetime": "2026-07-08T08:00:00", "taken": True},
            headers={"X-API-Key": key},
        )
        assert resp.status_code == 200

    def test_admin_sees_everything_without_association(self, client, db):
        admin = _make_user(db, role="admin", name="Root")
        key, _ = _issue_key(db, admin)
        patient, device = _make_patient_device(db, caregiver=None)
        med = _make_medication(db, patient)
        # GET FC, GET aderência, GET atividade, POST — todos 200 sem associação.
        assert client.get(f"/api/devices/{device.id}/heart-rate-trends", headers={"X-API-Key": key}).status_code == 200
        assert client.get(f"/api/patients/{patient.id}/medication-adherence", headers={"X-API-Key": key}).status_code == 200
        assert client.get(f"/api/devices/{device.id}/activity-distribution", params={"date": "2026-07-07"}, headers={"X-API-Key": key}).status_code == 200
        post = client.post(
            f"/api/medications/{med.id}/adherence",
            json={"scheduled_datetime": "2026-07-08T08:00:00", "taken": True},
            headers={"X-API-Key": key},
        )
        assert post.status_code == 200


# ==================================================================
# AUDITORIA (GDPR-003, lado API)
# ==================================================================

class TestAudit:
    def test_authorized_read_creates_audit_with_user_id(self, client, db, primary):
        _, device = _make_patient_device(db, caregiver=primary.user)
        resp = client.get(f"/api/devices/{device.id}/heart-rate-trends", headers=primary.headers)
        assert resp.status_code == 200
        rows = db.query(sa.AuditLog).filter(sa.AuditLog.action == "heart_rate.read").all()
        assert len(rows) == 1
        assert rows[0].user_id == primary.user.id
        assert rows[0].resource_id == device.id


# ==================================================================
# ENDPOINTS DE LEITURA (regressão do comportamento existente)
# ==================================================================

class TestHeartRateTrends:
    def test_unknown_device_404(self, client, primary):
        resp = client.get("/api/devices/9999/heart-rate-trends", headers=primary.headers)
        assert resp.status_code == 404

    def test_returns_records_within_window(self, client, db, primary):
        _, device = _make_patient_device(db, caregiver=primary.user)
        now = datetime.utcnow()
        recent = sa.SensorRecord(device_id=device.id, timestamp_utc=int(now.timestamp()), heart_rate=72)
        old = sa.SensorRecord(
            device_id=device.id,
            timestamp_utc=int((now - timedelta(days=30)).timestamp()),
            heart_rate=200,  # fora da janela de 7 dias por omissão
        )
        db.add_all([recent, old])
        db.commit()

        resp = client.get(f"/api/devices/{device.id}/heart-rate-trends", headers=primary.headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["avg"] == 72


class TestMedicationAdherence:
    def test_unknown_patient_404(self, client, primary):
        resp = client.get("/api/patients/9999/medication-adherence", headers=primary.headers)
        assert resp.status_code == 404

    def test_computes_percent_taken(self, client, db, primary):
        patient, _ = _make_patient_device(db, caregiver=primary.user)
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

        resp = client.get(f"/api/patients/{patient.id}/medication-adherence", headers=primary.headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["medications"][0]["taken"] == 1
        assert body["medications"][0]["total"] == 2
        assert body["medications"][0]["percent"] == 50.0


class TestActivityDistribution:
    def test_invalid_date_format_400(self, client, db, primary):
        _, device = _make_patient_device(db, caregiver=primary.user)
        resp = client.get(
            f"/api/devices/{device.id}/activity-distribution",
            params={"date": "07-07-2026"},
            headers=primary.headers,
        )
        assert resp.status_code == 400

    def test_unknown_device_404(self, client, primary):
        resp = client.get(
            "/api/devices/9999/activity-distribution",
            params={"date": "2026-07-07"},
            headers=primary.headers,
        )
        assert resp.status_code == 404

    def test_aggregates_by_category(self, client, db, primary):
        _, device = _make_patient_device(db, caregiver=primary.user)
        day = datetime(2026, 7, 7, 22, 0)
        db.add(sa.ActivityWindow(
            device_id=device.id, activity_date=day, activity_category="sleep",
            start_time=1320, end_time=1440, duration_minutes=120, confidence=0.9,
        ))
        db.commit()

        resp = client.get(
            f"/api/devices/{device.id}/activity-distribution",
            params={"date": "2026-07-07"},
            headers=primary.headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["sleep"]["duration_minutes"] == 120
        assert body["sleep"]["windows_count"] == 1
        assert body["rest"]["duration_minutes"] == 0


# ==================================================================
# ENDPOINT DE ESCRITA (regressão)
# ==================================================================

class TestRecordMedicationAdherence:
    def test_requires_api_key(self, client, db, primary):
        patient, _ = _make_patient_device(db, caregiver=primary.user)
        med = _make_medication(db, patient)
        resp = client.post(
            f"/api/medications/{med.id}/adherence",
            json={"scheduled_datetime": "2026-07-08T08:00:00", "taken": True},
        )
        assert resp.status_code == 401

    def test_unknown_medication_404(self, client, primary):
        resp = client.post(
            "/api/medications/9999/adherence",
            json={"scheduled_datetime": "2026-07-08T08:00:00", "taken": True},
            headers=primary.headers,
        )
        assert resp.status_code == 404

    def test_creates_record_with_taken_at(self, client, db, primary):
        patient, _ = _make_patient_device(db, caregiver=primary.user)
        med = _make_medication(db, patient)
        resp = client.post(
            f"/api/medications/{med.id}/adherence",
            json={"scheduled_datetime": "2026-07-08T08:00:00", "taken": True, "method": "manual_entry"},
            headers=primary.headers,
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
        assert audit[0].user_id == primary.user.id  # agora preenchido (antes ia a None)

    def test_repeated_call_updates_instead_of_duplicating(self, client, db, primary):
        patient, _ = _make_patient_device(db, caregiver=primary.user)
        med = _make_medication(db, patient)
        payload = {"scheduled_datetime": "2026-07-08T08:00:00", "taken": True}
        client.post(f"/api/medications/{med.id}/adherence", json=payload, headers=primary.headers)
        resp2 = client.post(f"/api/medications/{med.id}/adherence", json=payload, headers=primary.headers)
        assert resp2.status_code == 200

        rows = db.query(sa.MedicationAdherence).filter(sa.MedicationAdherence.medication_id == med.id).all()
        assert len(rows) == 1

    def test_concurrent_requests_for_same_dose_never_duplicate(self, client, db, primary, monkeypatch):
        """Regressão: antes da UniqueConstraint em MedicationAdherence
        (storage_advanced.py), o SELECT e o INSERT deste endpoint não eram
        atómicos — dois pedidos concorrentes para a MESMA dose podiam ambos
        ver "não existe" e ambos inserir. Simula essa corrida de forma
        determinística injetando, no momento exato do primeiro commit deste
        pedido, uma escrita "concorrente" da MESMA dose por outra sessão. O
        commit deste pedido tem então de falhar por violação da constraint e
        o endpoint deve recuperar sozinho (retry como UPDATE), nunca 500 nem
        duas linhas."""
        patient, _ = _make_patient_device(db, caregiver=primary.user)
        med = _make_medication(db, patient)
        scheduled_dt = datetime(2026, 7, 10, 8, 0, 0)
        payload = {"scheduled_datetime": scheduled_dt.isoformat(), "taken": True, "method": "manual_entry"}

        real_commit = sa.Session.commit
        call_count = {"n": 0}

        def flaky_commit(self):
            call_count["n"] += 1
            if call_count["n"] == 1:
                other_db = sa.get_db_session()
                other_db.add(sa.MedicationAdherence(
                    medication_id=med.id, scheduled_datetime=scheduled_dt,
                    taken=False, method="wearable_detection",
                ))
                other_db.commit()
                other_db.close()
            return real_commit(self)

        monkeypatch.setattr(sa.Session, "commit", flaky_commit)

        resp = client.post(f"/api/medications/{med.id}/adherence", json=payload, headers=primary.headers)

        assert resp.status_code == 200
        assert resp.json()["taken"] is True  # o retry aplicou o UPDATE com os dados deste pedido

        rows = db.query(sa.MedicationAdherence).filter(sa.MedicationAdherence.medication_id == med.id).all()
        assert len(rows) == 1

        # Só a tentativa bem-sucedida persiste auditoria (o rollback descarta
        # o AuditLog da tentativa falhada).
        audit = db.query(sa.AuditLog).filter(
            sa.AuditLog.resource_id == med.id,
            sa.AuditLog.action == "medication_adherence.write",
        ).all()
        assert len(audit) == 1

    def test_taken_false_clears_taken_at(self, client, db, primary):
        patient, _ = _make_patient_device(db, caregiver=primary.user)
        med = _make_medication(db, patient)
        payload = {"scheduled_datetime": "2026-07-08T08:00:00", "taken": True}
        client.post(f"/api/medications/{med.id}/adherence", json=payload, headers=primary.headers)

        resp = client.post(
            f"/api/medications/{med.id}/adherence",
            json={"scheduled_datetime": "2026-07-08T08:00:00", "taken": False},
            headers=primary.headers,
        )
        assert resp.status_code == 200
        assert resp.json()["taken"] is False
        assert resp.json()["taken_at"] is None

    def test_invalid_method_rejected(self, client, db, primary):
        patient, _ = _make_patient_device(db, caregiver=primary.user)
        med = _make_medication(db, patient)
        resp = client.post(
            f"/api/medications/{med.id}/adherence",
            json={"scheduled_datetime": "2026-07-08T08:00:00", "taken": True, "method": "carrier_pigeon"},
            headers=primary.headers,
        )
        assert resp.status_code == 422


# ==================================================================
# RATE LIMITING (API-003) — middleware isolado, clock falso
# ==================================================================

class _FakeClock:
    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t

    def tick(self, seconds):
        self.t += seconds


async def _echo_app(scope, receive, send):
    """ASGI mínimo: 200 para tudo (também drena o corpo e trata lifespan)."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    assert scope["type"] == "http"
    more_body = True
    while more_body:
        message = await receive()
        more_body = message.get("more_body", False)
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})


def _rl_client(clock, mw=None, **kwargs):
    if mw is None:
        mw = api_auth.RateLimitMiddleware(_echo_app, clock=clock)
    return TestClient(mw, **kwargs), mw


class TestRateLimit:
    def test_read_limit_60_then_429(self):
        clock = _FakeClock()
        client, _ = _rl_client(clock)
        headers = {"X-API-Key": "cw_abcdefgh1234"}
        for _ in range(60):
            assert client.get("/api/x", headers=headers).status_code == 200
        resp = client.get("/api/x", headers=headers)
        assert resp.status_code == 429
        assert resp.json() == {"detail": "Demasiados pedidos"}
        assert int(resp.headers["retry-after"]) >= 1

    def test_write_limit_10_then_429(self):
        clock = _FakeClock()
        client, _ = _rl_client(clock)
        headers = {"X-API-Key": "cw_abcdefgh1234"}
        for _ in range(10):
            assert client.post("/api/x", headers=headers, json={}).status_code == 200
        resp = client.post("/api/x", headers=headers, json={})
        assert resp.status_code == 429

    def test_read_and_write_counted_separately(self):
        clock = _FakeClock()
        client, _ = _rl_client(clock)
        headers = {"X-API-Key": "cw_abcdefgh1234"}
        # Esgota a escrita (10) mas a leitura continua livre.
        for _ in range(10):
            assert client.post("/api/x", headers=headers, json={}).status_code == 200
        assert client.post("/api/x", headers=headers, json={}).status_code == 429
        assert client.get("/api/x", headers=headers).status_code == 200

    def test_window_slides(self):
        clock = _FakeClock()
        client, _ = _rl_client(clock)
        headers = {"X-API-Key": "cw_abcdefgh1234"}
        for _ in range(60):
            assert client.get("/api/x", headers=headers).status_code == 200
        assert client.get("/api/x", headers=headers).status_code == 429
        # Avança 61s: todos os timestamps saem da janela.
        clock.tick(61)
        assert client.get("/api/x", headers=headers).status_code == 200

    def test_rejected_request_does_not_push_window(self):
        clock = _FakeClock()
        client, _ = _rl_client(clock)
        headers = {"X-API-Key": "cw_abcdefgh1234"}
        for _ in range(60):
            client.get("/api/x", headers=headers)
        # Vários 429 seguidos — não empurram a janela.
        for _ in range(5):
            assert client.get("/api/x", headers=headers).status_code == 429
        # O timestamp mais antigo continua a ser o do 1.º pedido: passados 60s
        # exatos desde ele, volta a haver espaço.
        clock.tick(60)
        assert client.get("/api/x", headers=headers).status_code == 200

    def test_different_ips_independent(self):
        clock = _FakeClock()
        mw = api_auth.RateLimitMiddleware(_echo_app, clock=clock)
        c1, _ = _rl_client(clock, mw=mw, client=("10.0.0.1", 1))
        c2, _ = _rl_client(clock, mw=mw, client=("10.0.0.2", 2))
        headers = {"X-API-Key": "cw_abcdefgh1234"}
        for _ in range(60):
            assert c1.get("/api/x", headers=headers).status_code == 200
        assert c1.get("/api/x", headers=headers).status_code == 429
        # IP diferente tem contador próprio.
        assert c2.get("/api/x", headers=headers).status_code == 200

    def test_different_keys_independent(self):
        clock = _FakeClock()
        client, _ = _rl_client(clock)
        for _ in range(60):
            assert client.get("/api/x", headers={"X-API-Key": "cw_aaaaaaaa1"}).status_code == 200
        assert client.get("/api/x", headers={"X-API-Key": "cw_aaaaaaaa1"}).status_code == 429
        # Prefixo de chave diferente => bucket diferente.
        assert client.get("/api/x", headers={"X-API-Key": "cw_bbbbbbbb2"}).status_code == 200

    def test_health_never_limited(self):
        clock = _FakeClock()
        client, _ = _rl_client(clock)
        for _ in range(200):
            assert client.get("/health").status_code == 200
