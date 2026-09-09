"""Testes do RF-11 — exportação interoperável HL7 FHIR R4 (2026-09-07).

Cobre três coisas distintas, deliberadamente separadas:

  1. `bridge/fhir_export.py` produz recursos `Observation` estruturalmente
     válidos (validação de esquema).
  2. O validador NÃO é vazio — cada regra que ele diz verificar é
     exercitada com um recurso inválido que TEM de ser rejeitado. Sem
     isto, "o meu output passa no meu validador" não provaria nada.
  3. O endpoint `/api/devices/{id}/fhir/observations` respeita o trio de
     segurança obrigatório desta API (autenticação, autorização por
     paciente, auditoria) — RF-02.

A validação de esquema é feita por DUAS vias independentes: o validador
próprio de `fhir_export` e a biblioteca externa `jsonschema` contra
`OBSERVATION_JSON_SCHEMA` (saltada se a biblioteca não estiver
instalada, porque não é dependência declarada do projeto).

Estrutura de fixtures igual à de test_api.py (chaves de API reais, SQLite
em memória via conftest.py) — não se duplica lógica de autenticação.
"""
import uuid as _uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api
import api_auth
import fhir_export
import storage_advanced as sa

# `jsonschema` NÃO é dependência declarada do projeto (regra: não engordar
# requirements.txt sem necessidade real). Quando existe no ambiente, os
# testes marcados abaixo usam-na como validador externo independente;
# quando não existe, são saltados e fica só o validador próprio.
try:  # pragma: no cover - depende do ambiente
    import jsonschema as _jsonschema
except ImportError:  # pragma: no cover
    _jsonschema = None


# ------------------------------------------------------------------
# Fixtures (mesmo padrão de test_api.py)
# ------------------------------------------------------------------

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


def _issue_key(db, user):
    plaintext, key_hash = api_auth.generate_api_key()
    db.add(api_auth.ApiKey(user_id=user.id, key_hash=key_hash, label="test"))
    db.commit()
    return plaintext


class _Auth:
    def __init__(self, user, key):
        self.user = user
        self.key = key
        self.headers = {"X-API-Key": key}


@pytest.fixture
def primary(db):
    user = _make_user(db, email="familia@example.com", role="family", name="Família")
    return _Auth(user, _issue_key(db, user))


@pytest.fixture
def intruder(db):
    """Chave VÁLIDA mas sem associação a paciente nenhum."""
    user = _make_user(db, email="intruso@example.com", role="family", name="Intruso")
    return _Auth(user, _issue_key(db, user))


PATIENT_NAME = "Maria Silva"


def _make_patient_device(db, caregiver=None):
    patient = sa.Patient(
        uuid="pat-fhir-1",
        pseudonym="pseudo-fhir-1",
        name=PATIENT_NAME,
        date_of_birth=datetime(1945, 3, 1),
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)
    device = sa.Device(uuid="dev-fhir-1", patient_id=patient.id, mac_address="AA:BB:CC:DD:EE:F1")
    db.add(device)
    db.commit()
    db.refresh(device)
    if caregiver is not None:
        db.execute(sa.patient_caregivers.insert().values(
            patient_id=patient.id,
            user_id=caregiver.id,
            can_view_alerts=True,
            can_edit_notes=True,
            can_edit_medications=True,
        ))
        db.commit()
    return patient, device


def _add_sensor_record(db, device, **overrides):
    now = datetime.utcnow()
    values = dict(
        device_id=device.id,
        timestamp_utc=int(datetime.now(timezone.utc).timestamp()) - 60,
        heart_rate=72,
        spo2_percent=97,
        steps_count=1234,
        pacing_index=55,
        received_at=now,
    )
    values.update(overrides)
    record = sa.SensorRecord(**values)
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


# ------------------------------------------------------------------
# 1. Estrutura dos recursos produzidos
# ------------------------------------------------------------------

class _FakeRecord:
    """Registo mínimo com a mesma forma de `SensorRecord` (sem tocar na BD)."""

    def __init__(self, **kwargs):
        self.id = 1
        self.timestamp_utc = 1757260800
        self.heart_rate = None
        self.spo2_percent = None
        self.steps_count = None
        self.pacing_index = None
        for k, v in kwargs.items():
            setattr(self, k, v)


def _subject():
    return fhir_export.build_subject(7, pseudonym="pseudo-x")


class TestEstruturaObservation:
    def test_registo_completo_gera_uma_observation_por_sinal(self):
        record = _FakeRecord(heart_rate=72, spo2_percent=97, steps_count=900, pacing_index=40)
        observations = fhir_export.observations_from_sensor_record(record, _subject(), device_id=3)
        assert len(observations) == 4
        assert [o["id"] for o in observations] == ["hr-1", "spo2-1", "steps-1", "pacing-1"]

    def test_todas_as_observations_passam_validacao_de_esquema(self):
        record = _FakeRecord(heart_rate=72, spo2_percent=97, steps_count=900, pacing_index=40)
        for observation in fhir_export.observations_from_sensor_record(record, _subject(), device_id=3):
            assert fhir_export.validate_observation(observation) == []

    @pytest.mark.skipif(_jsonschema is None, reason="jsonschema não instalada (não é dependência do projeto)")
    def test_observations_validam_contra_json_schema_externo(self):
        """Validação por biblioteca INDEPENDENTE — evita a circularidade de
        o próprio módulo ser juiz do seu output."""
        _jsonschema.Draft202012Validator.check_schema(fhir_export.OBSERVATION_JSON_SCHEMA)
        validator = _jsonschema.Draft202012Validator(fhir_export.OBSERVATION_JSON_SCHEMA)
        record = _FakeRecord(heart_rate=72, spo2_percent=97, steps_count=900, pacing_index=40)
        for observation in fhir_export.observations_from_sensor_record(record, _subject(), device_id=3):
            validator.validate(observation)

    def test_sinais_a_none_sao_omitidos_e_nao_geram_recurso_vazio(self):
        record = _FakeRecord(heart_rate=72)  # os outros três ficam a None
        observations = fhir_export.observations_from_sensor_record(record, _subject(), device_id=3)
        assert [o["id"] for o in observations] == ["hr-1"]

    def test_janela_de_atividade_usa_start_time_no_effective_datetime(self):
        class _Window:
            id = 5
            device_id = 3
            activity_date = datetime(2026, 9, 7)
            activity_category = "sleep"
            start_time = 90  # 01:30
            duration_minutes = 420

        observation = fhir_export.observation_from_activity_window(_Window(), _subject(), device_id=3)
        assert observation["effectiveDateTime"] == "2026-09-07T01:30:00Z"
        assert observation["valueQuantity"]["value"] == 420
        assert observation["valueQuantity"]["code"] == "min"
        assert "sleep" in observation["code"]["text"]
        assert fhir_export.validate_observation(observation) == []

    def test_bundle_e_valido_e_total_coincide_com_as_entradas(self):
        record = _FakeRecord(heart_rate=72, spo2_percent=97)
        observations = fhir_export.observations_from_sensor_record(record, _subject(), device_id=3)
        bundle = fhir_export.build_observation_bundle(observations, base_url="http://127.0.0.1:8766/fhir")
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "searchset"
        assert bundle["total"] == 2
        assert fhir_export.validate_bundle(bundle) == []
        assert bundle["entry"][0]["fullUrl"].endswith("/Observation/hr-1")


# ------------------------------------------------------------------
# 2. Códigos clínicos: confirmados vs. por confirmar
# ------------------------------------------------------------------

class TestCodigosClinicos:
    def test_frequencia_cardiaca_usa_loinc_8867_4(self):
        record = _FakeRecord(heart_rate=72)
        observation = fhir_export.observations_from_sensor_record(record, _subject(), device_id=3)[0]
        codings = observation["code"]["coding"]
        assert [(c["system"], c["code"]) for c in codings] == [("http://loinc.org", "8867-4")]
        assert observation["valueQuantity"]["system"] == "http://unitsofmeasure.org"
        assert observation["valueQuantity"]["code"] == "/min"

    def test_spo2_leva_os_dois_codings_do_perfil_oxygensat(self):
        record = _FakeRecord(spo2_percent=97)
        observation = fhir_export.observations_from_sensor_record(record, _subject(), device_id=3)[0]
        codes = {c["code"] for c in observation["code"]["coding"]}
        assert codes == {"2708-6", "59408-5"}
        assert observation["valueQuantity"]["code"] == "%"

    @pytest.mark.parametrize("attr,mapping_key", [
        ("steps_count", "steps_count"),
        ("pacing_index", "pacing_index"),
    ])
    def test_sinais_por_confirmar_saem_sem_coding(self, attr, mapping_key):
        """REGRA CENTRAL DO RF-11: melhor ficar por confirmar do que
        inventar um código clínico errado. Um sinal não confirmado sai com
        `text` e SEM `coding` — o recetor percebe que não pode processar
        aquilo automaticamente."""
        record = _FakeRecord(**{attr: 10})
        observation = fhir_export.observations_from_sensor_record(record, _subject(), device_id=3)[0]
        assert "coding" not in observation["code"]
        assert observation["code"]["text"]
        assert fhir_export.SIGNAL_MAPPINGS[mapping_key].confirmed is False
        # Continua a ser um recurso FHIR válido: `text` sozinho é legal.
        assert fhir_export.validate_observation(observation) == []

    def test_lista_de_codigos_por_confirmar_e_explicita(self):
        pendentes = {u["signal"] for u in fhir_export.unconfirmed_signals()}
        assert pendentes == {"steps_count", "activity_duration", "pacing_index"}
        for item in fhir_export.unconfirmed_signals():
            assert item["note"], "cada código por confirmar tem de dizer PORQUÊ"

    def test_nenhum_coding_emitido_vem_de_um_mapeamento_nao_confirmado(self):
        """Guarda contra regressões: se alguém puser `confirmed=True` sem
        preencher `codings` (ou o contrário), isto apanha."""
        for mapping in fhir_export.SIGNAL_MAPPINGS.values():
            assert bool(mapping.confirmed) == bool(mapping.codings)


# ------------------------------------------------------------------
# 3. O validador rejeita mesmo o que diz rejeitar (testes negativos)
# ------------------------------------------------------------------

def _valid_observation():
    return fhir_export.build_observation(
        resource_id="hr-1",
        mapping=fhir_export.SIGNAL_MAPPINGS["heart_rate"],
        value=72,
        effective=1757260800,
        subject=_subject(),
        device_id=3,
    )


class TestValidadorNaoEVazio:
    def test_recurso_de_referencia_e_valido(self):
        assert fhir_export.validate_observation(_valid_observation()) == []

    def test_resource_type_errado(self):
        obs = _valid_observation()
        obs["resourceType"] = "Patient"
        assert any("resourceType" in e for e in fhir_export.validate_observation(obs))

    def test_status_fora_do_value_set(self):
        obs = _valid_observation()
        obs["status"] = "concluido"  # não pertence ao ObservationStatus do FHIR
        assert any("value set" in e for e in fhir_export.validate_observation(obs))

    def test_status_em_falta(self):
        obs = _valid_observation()
        del obs["status"]
        assert any("status" in e for e in fhir_export.validate_observation(obs))

    def test_code_em_falta(self):
        obs = _valid_observation()
        del obs["code"]
        assert any("code" in e for e in fhir_export.validate_observation(obs))

    def test_codeable_concept_sem_coding_nem_text(self):
        obs = _valid_observation()
        obs["code"] = {}
        assert any("'coding' ou 'text'" in e for e in fhir_export.validate_observation(obs))

    def test_coding_sem_system_e_ambiguo(self):
        obs = _valid_observation()
        obs["code"] = {"coding": [{"code": "8867-4"}]}
        assert any("system" in e for e in fhir_export.validate_observation(obs))

    def test_referencia_mal_formada(self):
        obs = _valid_observation()
        obs["subject"] = {"reference": "7"}
        assert any("TipoDeRecurso/id" in e for e in fhir_export.validate_observation(obs))

    def test_id_fora_do_formato_fhir(self):
        obs = _valid_observation()
        obs["id"] = "hr 1/&"
        assert any("formato FHIR id" in e for e in fhir_export.validate_observation(obs))

    def test_quantity_com_code_ucum_mas_sem_system(self):
        obs = _valid_observation()
        del obs["valueQuantity"]["system"]
        assert any("UCUM" in e for e in fhir_export.validate_observation(obs))

    def test_obs6_data_absent_reason_com_valor(self):
        """Invariante obs-6 do FHIR R4: `dataAbsentReason` só pode existir
        se NÃO houver value[x]."""
        obs = _valid_observation()
        obs["dataAbsentReason"] = {"text": "não medido"}
        assert any("obs-6" in e for e in fhir_export.validate_observation(obs))

    def test_obs7_component_com_o_mesmo_codigo_do_observation(self):
        """Invariante obs-7 do FHIR R4."""
        obs = _valid_observation()
        obs["component"] = [{"code": obs["code"], "valueQuantity": {"value": 1}}]
        assert any("obs-7" in e for e in fhir_export.validate_observation(obs))

    def test_dois_value_x_ao_mesmo_tempo(self):
        obs = _valid_observation()
        obs["valueString"] = "72"
        assert any("value[x]" in e for e in fhir_export.validate_observation(obs))

    def test_bundle_com_total_errado(self):
        bundle = fhir_export.build_observation_bundle([_valid_observation()])
        bundle["total"] = 99
        assert any("total" in e for e in fhir_export.validate_bundle(bundle))

    def test_bundle_propaga_erro_do_recurso_interior(self):
        obs = _valid_observation()
        del obs["status"]
        bundle = fhir_export.build_observation_bundle([obs])
        errors = fhir_export.validate_bundle(bundle)
        assert any(e.startswith("entry[0].resource:") for e in errors)


# ------------------------------------------------------------------
# 4. Endpoint HTTP: segurança (RF-02) e conteúdo
# ------------------------------------------------------------------

class TestEndpointFhir:
    def test_sem_autenticacao_devolve_401(self, db, client, primary):
        _, device = _make_patient_device(db, caregiver=primary.user)
        response = client.get(f"/api/devices/{device.id}/fhir/observations")
        assert response.status_code == 401

    def test_utilizador_sem_associacao_devolve_404(self, db, client, primary, intruder):
        _, device = _make_patient_device(db, caregiver=primary.user)
        _add_sensor_record(db, device)
        response = client.get(
            f"/api/devices/{device.id}/fhir/observations", headers=intruder.headers
        )
        # 404 (não 403) por decisão documentada em `_authorize_patient`:
        # um 403 distinto revelaria que o ID existe.
        assert response.status_code == 404

    def test_dispositivo_inexistente_devolve_404(self, db, client, primary):
        response = client.get("/api/devices/9999/fhir/observations", headers=primary.headers)
        assert response.status_code == 404

    def test_bundle_devolvido_e_valido(self, db, client, primary):
        _, device = _make_patient_device(db, caregiver=primary.user)
        _add_sensor_record(db, device)
        response = client.get(
            f"/api/devices/{device.id}/fhir/observations", headers=primary.headers
        )
        assert response.status_code == 200
        bundle = response.json()
        assert fhir_export.validate_bundle(bundle) == []
        assert bundle["total"] == 4  # FC + SpO2 + passos + pacing
        codes = {
            c["code"]
            for entry in bundle["entry"]
            for c in entry["resource"]["code"].get("coding", [])
        }
        assert "8867-4" in codes

    @pytest.mark.skipif(_jsonschema is None, reason="jsonschema não instalada (não é dependência do projeto)")
    def test_bundle_do_endpoint_valida_contra_json_schema_externo(self, db, client, primary):
        _, device = _make_patient_device(db, caregiver=primary.user)
        _add_sensor_record(db, device)
        bundle = client.get(
            f"/api/devices/{device.id}/fhir/observations", headers=primary.headers
        ).json()
        _jsonschema.Draft202012Validator.check_schema(fhir_export.BUNDLE_JSON_SCHEMA)
        _jsonschema.Draft202012Validator(fhir_export.BUNDLE_JSON_SCHEMA).validate(bundle)

    def test_bundle_nao_contem_o_nome_do_paciente(self, db, client, primary):
        """RGPD: a exportação identifica o paciente por id lógico +
        pseudónimo, nunca por nome."""
        _, device = _make_patient_device(db, caregiver=primary.user)
        _add_sensor_record(db, device)
        raw = client.get(
            f"/api/devices/{device.id}/fhir/observations", headers=primary.headers
        ).text
        assert PATIENT_NAME not in raw
        assert "pseudo-fhir-1" in raw

    def test_janelas_de_atividade_entram_no_bundle(self, db, client, primary):
        _, device = _make_patient_device(db, caregiver=primary.user)
        db.add(sa.ActivityWindow(
            device_id=device.id,
            activity_date=datetime.utcnow(),
            activity_category="sleep",
            start_time=0,
            end_time=420,
            duration_minutes=420,
            confidence=0.9,
        ))
        db.commit()
        bundle = client.get(
            f"/api/devices/{device.id}/fhir/observations", headers=primary.headers
        ).json()
        assert any(e["resource"]["id"].startswith("activity-") for e in bundle["entry"])

        semi = client.get(
            f"/api/devices/{device.id}/fhir/observations?include_activity=false",
            headers=primary.headers,
        ).json()
        assert not any(e["resource"]["id"].startswith("activity-") for e in semi["entry"])

    def test_janela_temporal_e_respeitada(self, db, client, primary):
        _, device = _make_patient_device(db, caregiver=primary.user)
        _add_sensor_record(db, device, received_at=datetime.utcnow() - timedelta(days=30))
        bundle = client.get(
            f"/api/devices/{device.id}/fhir/observations?hours=1", headers=primary.headers
        ).json()
        assert bundle["total"] == 0
        largo = client.get(
            f"/api/devices/{device.id}/fhir/observations?hours=2000", headers=primary.headers
        ).json()
        assert largo["total"] == 4

    def test_leitura_fica_registada_na_auditoria(self, db, client, primary):
        """GDPR-003: uma exportação clínica completa TEM de deixar rasto."""
        _, device = _make_patient_device(db, caregiver=primary.user)
        _add_sensor_record(db, device)
        client.get(f"/api/devices/{device.id}/fhir/observations", headers=primary.headers)
        entries = db.query(sa.AuditLog).filter(
            sa.AuditLog.action == "fhir_observations.read"
        ).all()
        assert len(entries) == 1
        assert entries[0].user_id == primary.user.id
        assert entries[0].resource_type == "device"
        assert entries[0].resource_id == device.id

    def test_acesso_negado_nao_gera_registo_de_auditoria_de_leitura(self, db, client, intruder, primary):
        _, device = _make_patient_device(db, caregiver=primary.user)
        client.get(f"/api/devices/{device.id}/fhir/observations", headers=intruder.headers)
        assert db.query(sa.AuditLog).filter(
            sa.AuditLog.action == "fhir_observations.read"
        ).count() == 0

    def test_endpoint_de_mapeamentos_exige_autenticacao(self, client):
        assert client.get("/api/fhir/observation-mappings").status_code == 401

    def test_endpoint_de_mapeamentos_declara_o_que_esta_por_confirmar(self, client, primary):
        response = client.get("/api/fhir/observation-mappings", headers=primary.headers)
        assert response.status_code == 200
        body = response.json()
        assert {s["signal"] for s in body["confirmed"]} == {"heart_rate", "spo2_percent"}
        assert {s["signal"] for s in body["unconfirmed"]} == {
            "steps_count", "activity_duration", "pacing_index"
        }
