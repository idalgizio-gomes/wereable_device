"""Testes do RF-12 — relatório semanal por paciente (2026-09-07).

Critério de aceitação: relatório semanal por paciente com rotina, sinais
vitais, alertas e adesão à medicação. O endpoint
`/api/patients/{id}/weekly-report` devolve os DADOS dessas quatro secções;
a apresentação (PDF) é do dashboard, que reutiliza a folha de impressão
já existente (`#clinicalPrintSheet`, web/dashboard/export-clinico.js).

Testa-se aqui, além do conteúdo:
  * o trio de segurança obrigatório da API (autenticação, autorização por
    paciente, auditoria) — RF-02;
  * a janela temporal (`end`), que é o que permite ao Cron pedir "a
    semana que terminou no dia X" em vez de depender da hora a que a
    tarefa agendada correu;
  * idempotência de leitura, porque o endpoint é para ser chamado por um
    agendador externo.
"""
import uuid as _uuid
from datetime import datetime, timedelta, timezone

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
    user = _make_user(db, email="clinico@example.com", role="clinician", name="Clínico")
    return _Auth(user, _issue_key(db, user))


@pytest.fixture
def intruder(db):
    user = _make_user(db, email="intruso@example.com", role="family", name="Intruso")
    return _Auth(user, _issue_key(db, user))


@pytest.fixture
def cenario(db, primary):
    """Paciente com uma semana de dados nas quatro secções do relatório."""
    patient = sa.Patient(
        uuid="pat-sem-1",
        pseudonym="pseudo-sem-1",
        name="Maria Silva",
        date_of_birth=datetime(1945, 3, 1),
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)

    device = sa.Device(uuid="dev-sem-1", patient_id=patient.id, mac_address="AA:BB:CC:DD:EE:11")
    db.add(device)
    db.commit()
    db.refresh(device)

    db.execute(sa.patient_caregivers.insert().values(
        patient_id=patient.id,
        user_id=primary.user.id,
        can_view_alerts=True,
        can_edit_notes=True,
        can_edit_medications=True,
    ))
    db.commit()

    now = datetime.utcnow()
    now_epoch = int(datetime.now(timezone.utc).timestamp())

    # --- Rotina: 2 janelas de sono + 1 de atividade, dentro da semana ---
    for days_ago, category, minutes in ((1, "sleep", 400), (2, "sleep", 440), (3, "activity", 60)):
        db.add(sa.ActivityWindow(
            device_id=device.id,
            activity_date=now - timedelta(days=days_ago),
            activity_category=category,
            start_time=0,
            end_time=minutes,
            duration_minutes=minutes,
            confidence=0.9,
        ))
    # Uma janela FORA da semana — não pode entrar nos totais.
    db.add(sa.ActivityWindow(
        device_id=device.id,
        activity_date=now - timedelta(days=40),
        activity_category="sleep",
        start_time=0, end_time=999, duration_minutes=999, confidence=0.9,
    ))

    # --- Sinais vitais: 3 amostras dentro da semana + 1 fora ---
    for hours_ago, hr, spo2, steps in ((2, 70, 96, 100), (24, 80, 98, 200), (48, 60, 94, 300)):
        db.add(sa.SensorRecord(
            device_id=device.id,
            timestamp_utc=now_epoch - hours_ago * 3600,
            heart_rate=hr, spo2_percent=spo2, steps_count=steps,
            received_at=now - timedelta(hours=hours_ago),
        ))
    db.add(sa.SensorRecord(
        device_id=device.id,
        timestamp_utc=now_epoch - 40 * 24 * 3600,
        heart_rate=999, spo2_percent=10, steps_count=99999,
        received_at=now - timedelta(days=40),
    ))

    # --- Alertas: 3 dentro da semana (um deles soft-deleted) + 1 fora ---
    db.add(sa.Alert(uuid="al-1", device_id=device.id, alert_type="hr_high", severity="critical",
                    title="FC elevada", description="92 bpm", created_at=now - timedelta(hours=3)))
    db.add(sa.Alert(uuid="al-2", device_id=device.id, alert_type="inactivity", severity="warning",
                    title="Inatividade", description="3h12", created_at=now - timedelta(days=2)))
    db.add(sa.Alert(uuid="al-3", device_id=device.id, alert_type="spo2_low", severity="warning",
                    title="SpO2 baixa", description="93%", created_at=now - timedelta(days=1),
                    deleted_at=now))  # apagado pela retenção — não conta
    db.add(sa.Alert(uuid="al-4", device_id=device.id, alert_type="hr_high", severity="serious",
                    title="Antigo", description="fora da semana",
                    created_at=now - timedelta(days=40)))

    # --- Medicação: 2 doses agendadas, 1 tomada ---
    med = sa.Medication(
        uuid="med-1", patient_id=patient.id, name="Donepezilo",
        dosage="5 mg", frequency="1x/dia", start_date=now - timedelta(days=30),
    )
    db.add(med)
    db.commit()
    db.refresh(med)
    db.add(sa.MedicationAdherence(medication_id=med.id,
                                  scheduled_datetime=now - timedelta(days=1), taken=True))
    db.add(sa.MedicationAdherence(medication_id=med.id,
                                  scheduled_datetime=now - timedelta(days=2), taken=False))
    db.commit()
    return patient, device, med


class TestSegurancaRelatorioSemanal:
    def test_sem_autenticacao_devolve_401(self, client, cenario):
        patient, _, _ = cenario
        assert client.get(f"/api/patients/{patient.id}/weekly-report").status_code == 401

    def test_utilizador_sem_associacao_devolve_404(self, client, cenario, intruder):
        patient, _, _ = cenario
        # 404 e não 403, por decisão documentada em `_authorize_patient`.
        response = client.get(f"/api/patients/{patient.id}/weekly-report", headers=intruder.headers)
        assert response.status_code == 404

    def test_paciente_inexistente_devolve_404(self, client, primary):
        assert client.get("/api/patients/9999/weekly-report", headers=primary.headers).status_code == 404

    def test_leitura_fica_registada_na_auditoria(self, db, client, cenario, primary):
        patient, _, _ = cenario
        client.get(f"/api/patients/{patient.id}/weekly-report", headers=primary.headers)
        entries = db.query(sa.AuditLog).filter(sa.AuditLog.action == "weekly_report.read").all()
        assert len(entries) == 1
        assert entries[0].user_id == primary.user.id
        assert entries[0].resource_type == "patient"
        assert entries[0].resource_id == patient.id

    def test_acesso_negado_nao_gera_auditoria_de_leitura(self, db, client, cenario, intruder):
        patient, _, _ = cenario
        client.get(f"/api/patients/{patient.id}/weekly-report", headers=intruder.headers)
        assert db.query(sa.AuditLog).filter(sa.AuditLog.action == "weekly_report.read").count() == 0

    def test_admin_nao_precisa_de_associacao(self, db, client, cenario):
        """`_authorize_patient` dá acesso total ao papel 'admin'. Este teste
        documenta esse comportamento no endpoint novo — a decisão de o
        dashboard NUNCA mostrar dados clínicos a um administrador é do lado
        do cliente (ver cabeçalho de web/dashboard/admin-view.js), não desta
        camada."""
        patient, _, _ = cenario
        admin = _make_user(db, email="admin@example.com", role="admin", name="Admin")
        key = _issue_key(db, admin)
        response = client.get(f"/api/patients/{patient.id}/weekly-report",
                              headers={"X-API-Key": key})
        assert response.status_code == 200


class TestConteudoRelatorioSemanal:
    def test_tem_as_quatro_seccoes_do_criterio_de_aceitacao(self, client, cenario, primary):
        patient, _, _ = cenario
        body = client.get(f"/api/patients/{patient.id}/weekly-report",
                          headers=primary.headers).json()
        for seccao in ("rotina", "sinais_vitais", "alertas", "adesao_medicacao"):
            assert seccao in body, f"secção '{seccao}' em falta no relatório"
        assert body["period"]["days"] == 7

    def test_identifica_o_paciente_por_pseudonimo(self, client, cenario, primary):
        patient, _, _ = cenario
        body = client.get(f"/api/patients/{patient.id}/weekly-report",
                          headers=primary.headers).json()
        assert body["patient_pseudonym"] == "pseudo-sem-1"
        assert "Maria Silva" not in client.get(
            f"/api/patients/{patient.id}/weekly-report", headers=primary.headers
        ).text

    def test_rotina_soma_minutos_por_categoria_e_exclui_fora_da_semana(self, client, cenario, primary):
        patient, _, _ = cenario
        rotina = client.get(f"/api/patients/{patient.id}/weekly-report",
                            headers=primary.headers).json()["rotina"]
        assert rotina["sleep"]["total_minutes"] == 840  # 400 + 440; os 999 antigos ficam de fora
        assert rotina["sleep"]["windows_count"] == 2
        assert rotina["sleep"]["daily_average_minutes"] == 120.0
        assert rotina["activity"]["total_minutes"] == 60

    def test_categorias_sem_dados_aparecem_a_zero(self, client, cenario, primary):
        """Uma linha em falta seria ambígua (não medido? não houve?)."""
        patient, _, _ = cenario
        rotina = client.get(f"/api/patients/{patient.id}/weekly-report",
                            headers=primary.headers).json()["rotina"]
        assert set(rotina) == {"sleep", "rest", "activity", "eating", "hygiene"}
        assert rotina["hygiene"] == {"total_minutes": 0, "windows_count": 0, "daily_average_minutes": 0.0}

    def test_sinais_vitais_agregados_ignoram_amostras_fora_da_semana(self, client, cenario, primary):
        patient, _, _ = cenario
        vitais = client.get(f"/api/patients/{patient.id}/weekly-report",
                            headers=primary.headers).json()["sinais_vitais"]
        assert vitais["heart_rate"] == {"count": 3, "avg": 70.0, "min": 60, "max": 80}
        assert vitais["spo2_percent"]["min"] == 94  # o 10 antigo não entra
        assert vitais["steps"]["max"] == 300        # os 99999 antigos não entram

    def test_sem_amostras_o_sinal_vem_a_none_em_vez_de_zero(self, db, client, primary):
        """Zero seria uma leitura falsa ("FC média 0"); None diz "não medido"."""
        patient = sa.Patient(uuid="pat-vazio", pseudonym="pseudo-vazio",
                             name="Sem dados", date_of_birth=datetime(1950, 1, 1))
        db.add(patient)
        db.commit()
        db.refresh(patient)
        db.execute(sa.patient_caregivers.insert().values(
            patient_id=patient.id, user_id=primary.user.id,
            can_view_alerts=True, can_edit_notes=True, can_edit_medications=True,
        ))
        db.commit()
        body = client.get(f"/api/patients/{patient.id}/weekly-report",
                          headers=primary.headers).json()
        assert body["sinais_vitais"] == {"heart_rate": None, "spo2_percent": None, "steps": None}
        assert body["alertas"]["total"] == 0
        assert body["device_ids"] == []

    def test_alertas_contados_por_severidade_sem_os_apagados(self, client, cenario, primary):
        patient, _, _ = cenario
        alertas = client.get(f"/api/patients/{patient.id}/weekly-report",
                             headers=primary.headers).json()["alertas"]
        # al-1 (critical) + al-2 (warning); al-3 está soft-deleted e al-4 é antigo.
        assert alertas["total"] == 2
        assert alertas["por_severidade"] == {"info": 0, "warning": 1, "serious": 0, "critical": 1}
        assert [a["title"] for a in alertas["recentes"]] == ["FC elevada", "Inatividade"]
        assert alertas["truncado"] is False

    def test_adesao_a_medicacao_no_periodo(self, client, cenario, primary):
        patient, _, _ = cenario
        adesao = client.get(f"/api/patients/{patient.id}/weekly-report",
                            headers=primary.headers).json()["adesao_medicacao"]
        assert adesao["period_days"] == 7
        assert len(adesao["medications"]) == 1
        assert adesao["medications"][0]["medication_name"] == "Donepezilo"
        assert adesao["medications"][0]["taken"] == 1
        assert adesao["medications"][0]["total"] == 2
        assert adesao["overall_percent"] == 50.0


class TestJanelaTemporalECron:
    def test_parametro_end_desloca_a_janela(self, client, cenario, primary):
        """É isto que permite ao Cron pedir "a semana que terminou no dia X"
        em vez de depender da hora a que a tarefa agendada correu."""
        patient, _, _ = cenario
        antigo = (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d")
        body = client.get(f"/api/patients/{patient.id}/weekly-report?end={antigo}",
                          headers=primary.headers).json()
        assert body["alertas"]["total"] == 0
        assert body["rotina"]["sleep"]["total_minutes"] == 0
        assert body["period"]["end"].startswith(
            (datetime.utcnow() - timedelta(days=59)).strftime("%Y-%m-%d")
        )

    def test_end_inclui_o_dia_indicado_por_inteiro(self, client, cenario, primary):
        patient, _, _ = cenario
        hoje = datetime.utcnow().strftime("%Y-%m-%d")
        body = client.get(f"/api/patients/{patient.id}/weekly-report?end={hoje}",
                          headers=primary.headers).json()
        # Fim exclusivo no início do dia seguinte -> tudo o de hoje entra.
        assert body["period"]["end"].startswith(
            (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
        )
        assert body["alertas"]["total"] == 2

    def test_end_com_formato_invalido_devolve_400(self, client, cenario, primary):
        patient, _, _ = cenario
        response = client.get(f"/api/patients/{patient.id}/weekly-report?end=07-09-2026",
                              headers=primary.headers)
        assert response.status_code == 400

    def test_chamadas_repetidas_dao_o_mesmo_resultado(self, client, cenario, primary):
        """O endpoint tem de ser idempotente para poder ser chamado por um
        agendador externo sem efeitos colaterais (a única escrita é a linha
        de auditoria, que é o comportamento desejado)."""
        patient, _, _ = cenario
        hoje = datetime.utcnow().strftime("%Y-%m-%d")
        url = f"/api/patients/{patient.id}/weekly-report?end={hoje}"
        primeiro = client.get(url, headers=primary.headers).json()
        segundo = client.get(url, headers=primary.headers).json()
        primeiro.pop("generated_at")
        segundo.pop("generated_at")
        assert primeiro == segundo

    def test_cada_chamada_deixa_a_sua_propria_linha_de_auditoria(self, db, client, cenario, primary):
        patient, _, _ = cenario
        url = f"/api/patients/{patient.id}/weekly-report"
        client.get(url, headers=primary.headers)
        client.get(url, headers=primary.headers)
        assert db.query(sa.AuditLog).filter(sa.AuditLog.action == "weekly_report.read").count() == 2
