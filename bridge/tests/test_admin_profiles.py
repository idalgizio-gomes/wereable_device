"""Dois perfis de administrador (2026-09-07).

O papel único `admin` foi dividido em dois, com a separação IMPOSTA na
API (não escondida no dashboard):

  * `admin`          — Admin de Sistema: contas, dispositivos,
    configuração, firmware, logs, manutenção. Sem acesso a dados clínicos.
  * `admin_clinical` — Admin Clínico / Suporte Autorizado: lê dados
    clínicos só com motivo explícito, concessão temporal por expirar e
    auditoria reforçada (`privileged_clinical_access`).

Cada uma destas três condições tem aqui um teste que a falha
isoladamente — se qualquer delas deixar de ser exigida, um teste parte.
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


def _make_user(db, role="family", name="Utilizador", privileged_until=None):
    user = sa.User(
        uuid=str(_uuid.uuid4()),
        email=f"{_uuid.uuid4()}@example.com",
        password_hash="(bcrypt em produção)",
        role=role,
        name=name,
        privileged_access_expires_at=privileged_until,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _key(db, user):
    plaintext, key_hash = api_auth.generate_api_key()
    db.add(api_auth.ApiKey(user_id=user.id, key_hash=key_hash, label="test"))
    db.commit()
    return {"X-API-Key": plaintext}


@pytest.fixture
def patient(db):
    p = sa.Patient(uuid="pat-admin-1", name="Maria Silva", date_of_birth=datetime(1945, 3, 1),
                   pseudonym="pseudo-admin-1")
    db.add(p)
    db.commit()
    db.refresh(p)
    device = sa.Device(uuid="dev-admin-1", patient_id=p.id, mac_address="AA:BB:CC:DD:EE:AA")
    db.add(device)
    db.commit()
    return p


MOTIVO = "Incidente INC-4711: relatorio semanal vazio reportado pela equipa clinica"


class TestAdminSistema:
    """O Admin de Sistema nunca vê dados clínicos — nem com motivo."""

    def test_sem_acesso_ao_relatorio_semanal(self, db, client, patient):
        headers = _key(db, _make_user(db, role="admin", name="Admin Sistema"))
        assert client.get(f"/api/patients/{patient.id}/weekly-report",
                          headers=headers).status_code == 404

    def test_motivo_nao_lhe_abre_a_porta(self, db, client, patient):
        """Dar um motivo não transforma um Admin de Sistema num Admin
        Clínico — a distinção é o papel, não o texto enviado."""
        headers = _key(db, _make_user(db, role="admin", name="Admin Sistema"))
        resp = client.get(f"/api/patients/{patient.id}/weekly-report",
                          params={"reason": MOTIVO}, headers=headers)
        assert resp.status_code == 404

    def test_acesso_negado_nao_gera_auditoria_de_leitura(self, db, client, patient):
        headers = _key(db, _make_user(db, role="admin", name="Admin Sistema"))
        client.get(f"/api/patients/{patient.id}/weekly-report", headers=headers)
        assert db.query(sa.AuditLog).filter(
            sa.AuditLog.action.in_(["weekly_report.read", api.PRIVILEGED_ACCESS_ACTION])
        ).count() == 0

    def test_pode_ler_a_correspondencia_de_identidades(self, db, client, patient):
        """O directory é metadados de identificação (id/uuid/pseudónimo),
        não dados clínicos — o Admin de Sistema precisa dele para gerir
        dispositivos e contas."""
        headers = _key(db, _make_user(db, role="admin", name="Admin Sistema"))
        resp = client.get("/api/patients/directory", headers=headers)
        assert resp.status_code == 200
        assert [row["uuid"] for row in resp.json()["patients"]] == ["pat-admin-1"]


class TestAdminClinico:
    def _admin(self, db, horas=1):
        expira = datetime.utcnow() + timedelta(hours=horas) if horas is not None else None
        return _make_user(db, role="admin_clinical", name="Suporte", privileged_until=expira)

    def test_acesso_com_motivo_e_concessao_ativa(self, db, client, patient):
        headers = _key(db, self._admin(db))
        resp = client.get(f"/api/patients/{patient.id}/weekly-report",
                          params={"reason": MOTIVO}, headers=headers)
        assert resp.status_code == 200

    def test_sem_motivo_e_recusado(self, db, client, patient):
        headers = _key(db, self._admin(db))
        resp = client.get(f"/api/patients/{patient.id}/weekly-report", headers=headers)
        assert resp.status_code == 403
        assert "reason" in resp.json()["detail"]

    def test_motivo_vazio_ou_trivial_e_recusado(self, db, client, patient):
        headers = _key(db, self._admin(db))
        for motivo in ("", "   ", "x", "teste"):
            resp = client.get(f"/api/patients/{patient.id}/weekly-report",
                              params={"reason": motivo}, headers=headers)
            assert resp.status_code == 403, motivo

    def test_concessao_expirada_e_recusada(self, db, client, patient):
        headers = _key(db, self._admin(db, horas=-1))  # expirou há uma hora
        resp = client.get(f"/api/patients/{patient.id}/weekly-report",
                          params={"reason": MOTIVO}, headers=headers)
        assert resp.status_code == 403
        assert "concessão" in resp.json()["detail"].lower()

    def test_sem_concessao_nenhuma_e_recusado(self, db, client, patient):
        headers = _key(db, _make_user(db, role="admin_clinical", name="Suporte"))
        resp = client.get(f"/api/patients/{patient.id}/weekly-report",
                          params={"reason": MOTIVO}, headers=headers)
        assert resp.status_code == 403

    def test_nao_pode_escrever_no_registo_clinico(self, db, client, patient):
        """Suporte investiga, não altera. Mesmo com motivo e concessão."""
        med = sa.Medication(uuid="med-admin-1", patient_id=patient.id, name="Donepezilo",
                            dosage="5 mg", frequency="1x/dia", start_date=datetime(2026, 1, 1))
        db.add(med)
        db.commit()
        db.refresh(med)
        headers = _key(db, self._admin(db))
        resp = client.post(
            f"/api/medications/{med.id}/adherence",
            params={"reason": MOTIVO},
            json={"scheduled_datetime": "2026-07-08T08:00:00", "taken": True},
            headers=headers,
        )
        assert resp.status_code == 404

    def test_acesso_fica_auditado_com_motivo_e_accao_propria(self, db, client, patient):
        admin = self._admin(db)
        headers = _key(db, admin)
        client.get(f"/api/patients/{patient.id}/weekly-report",
                   params={"reason": MOTIVO}, headers=headers)
        entradas = db.query(sa.AuditLog).filter(
            sa.AuditLog.action == api.PRIVILEGED_ACCESS_ACTION
        ).all()
        assert len(entradas) == 1
        entrada = entradas[0]
        assert entrada.user_id == admin.id
        assert entrada.resource_type == "patient"
        assert entrada.resource_id == patient.id
        assert entrada.details["reason"] == MOTIVO
        assert entrada.details["role"] == "admin_clinical"
        assert entrada.details["mode"] == "read"
        assert entrada.details["grant_expires_at"] is not None

    def test_recusa_nao_gera_auditoria_de_acesso(self, db, client, patient):
        """Uma tentativa sem motivo não pode aparecer no relatório de
        auditoria como se tivesse havido acesso a dados."""
        headers = _key(db, self._admin(db))
        client.get(f"/api/patients/{patient.id}/weekly-report", headers=headers)
        assert db.query(sa.AuditLog).filter(
            sa.AuditLog.action == api.PRIVILEGED_ACCESS_ACTION).count() == 0

    def test_auditoria_privilegiada_e_filtravel_separadamente(self, db, client, patient):
        """O objetivo prático da ação distinta: um relatório de auditoria
        consegue isolar 'o que é que o suporte viu' de todas as leituras
        normais, com um único filtro."""
        caregiver = _make_user(db, role="family", name="Filha")
        db.execute(sa.patient_caregivers.insert().values(
            patient_id=patient.id, user_id=caregiver.id,
            can_view_alerts=True, can_edit_notes=True, can_edit_medications=True))
        db.commit()
        client.get(f"/api/patients/{patient.id}/weekly-report", headers=_key(db, caregiver))
        client.get(f"/api/patients/{patient.id}/weekly-report",
                   params={"reason": MOTIVO}, headers=_key(db, self._admin(db)))

        privilegiados = db.query(sa.AuditLog).filter(
            sa.AuditLog.action == api.PRIVILEGED_ACCESS_ACTION).all()
        normais = db.query(sa.AuditLog).filter(
            sa.AuditLog.action == "weekly_report.read").all()
        assert len(privilegiados) == 1
        assert len(normais) == 2  # a leitura normal e a do suporte, ambas registadas
        assert all(e.details and e.details.get("reason") for e in privilegiados)

    def test_aplica_se_tambem_a_exportacao_fhir(self, db, client, patient):
        """A regra é do `_authorize_patient`, portanto vale para TODOS os
        endpoints clínicos — não só para o relatório semanal."""
        device = db.query(sa.Device).filter(sa.Device.patient_id == patient.id).first()
        headers = _key(db, self._admin(db))
        sem_motivo = client.get(f"/api/devices/{device.id}/fhir/observations", headers=headers)
        assert sem_motivo.status_code == 403
        com_motivo = client.get(f"/api/devices/{device.id}/fhir/observations",
                                params={"reason": MOTIVO}, headers=headers)
        assert com_motivo.status_code == 200


class TestPapeisNaoAdministrativos:
    """Os perfis existentes não podem ter mudado de comportamento."""

    def test_familia_associada_continua_a_ler_sem_motivo(self, db, client, patient):
        user = _make_user(db, role="family", name="Filha")
        db.execute(sa.patient_caregivers.insert().values(
            patient_id=patient.id, user_id=user.id,
            can_view_alerts=True, can_edit_notes=True, can_edit_medications=True))
        db.commit()
        resp = client.get(f"/api/patients/{patient.id}/weekly-report", headers=_key(db, user))
        assert resp.status_code == 200

    def test_familia_nao_associada_continua_a_levar_404(self, db, client, patient):
        user = _make_user(db, role="family", name="Estranho")
        resp = client.get(f"/api/patients/{patient.id}/weekly-report", headers=_key(db, user))
        assert resp.status_code == 404

    def test_utilizador_normal_nao_ganha_acesso_por_enviar_motivo(self, db, client, patient):
        """O `reason` não é um bypass: é ignorado para quem não é
        `admin_clinical`."""
        user = _make_user(db, role="clinician", name="Dr. Intruso")
        resp = client.get(f"/api/patients/{patient.id}/weekly-report",
                          params={"reason": MOTIVO}, headers=_key(db, user))
        assert resp.status_code == 404
        assert db.query(sa.AuditLog).filter(
            sa.AuditLog.action == api.PRIVILEGED_ACCESS_ACTION).count() == 0
