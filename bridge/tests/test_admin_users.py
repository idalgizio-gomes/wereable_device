"""CRUD de utilizadores/perfis pelo Admin de Sistema (RF: "o sistema deve permitir a
administração de utilizadores e respetivos perfis") — antes só existia leitura (admin-view.js
mostrava, mas não permitia criar/editar/revogar). Ver bridge/api.py::admin_create_user e
segs., e a nota em bridge_status/PROJECT_STATUS.md sobre este item passar de "parcialmente
implementado" para implementado.
"""
import uuid as _uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import api
import api_auth
import auth_sessions
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


def _make_user(db, role="family", name="Utilizador", email=None):
    user = sa.User(
        uuid=str(_uuid.uuid4()),
        email=email or f"{_uuid.uuid4()}@example.com",
        password_hash=auth_sessions.hash_password("senha-valida-123"),
        role=role,
        name=name,
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


class TestApenasAdminSistema:
    """admin_clinical e os perfis clínicos/família nunca gerem contas — só `admin`."""

    def test_admin_clinical_recusado(self, db, client):
        headers = _key(db, _make_user(db, role="admin_clinical", name="Suporte"))
        assert client.get("/api/admin/users", headers=headers).status_code == 403

    def test_family_recusado(self, db, client):
        headers = _key(db, _make_user(db, role="family", name="Familiar"))
        assert client.post("/api/admin/users", headers=headers, json={
            "email": "x@example.com", "password": "12345678", "role": "family", "name": "X",
        }).status_code == 403

    def test_sem_autenticacao_401(self, client):
        assert client.get("/api/admin/users").status_code == 401


class TestCriarEListar:
    def test_criar_e_listar(self, db, client):
        headers = _key(db, _make_user(db, role="admin", name="Admin"))
        resp = client.post("/api/admin/users", headers=headers, json={
            "email": "nova.clinica@example.com", "password": "senha-forte-123",
            "role": "clinician", "name": "Dra. Nova Clínica", "institution": "Hospital X",
        })
        assert resp.status_code == 201
        body = resp.json()
        assert body["email"] == "nova.clinica@example.com"
        assert body["role"] == "clinician"
        assert body["active"] is True

        listing = client.get("/api/admin/users", headers=headers).json()
        emails = [u["email"] for u in listing["users"]]
        assert "nova.clinica@example.com" in emails

    def test_password_curta_rejeitada(self, db, client):
        headers = _key(db, _make_user(db, role="admin", name="Admin"))
        resp = client.post("/api/admin/users", headers=headers, json={
            "email": "y@example.com", "password": "curta", "role": "family", "name": "Y",
        })
        assert resp.status_code == 422

    def test_email_duplicado_rejeitado(self, db, client):
        headers = _key(db, _make_user(db, role="admin", name="Admin"))
        client.post("/api/admin/users", headers=headers, json={
            "email": "dup@example.com", "password": "senha-forte-123", "role": "family", "name": "A",
        })
        resp = client.post("/api/admin/users", headers=headers, json={
            "email": "dup@example.com", "password": "outra-senha-123", "role": "family", "name": "B",
        })
        assert resp.status_code == 409

    def test_password_gravada_com_hash_argon2(self, db, client):
        """A password nunca fica em claro na BD — mesma disciplina de auth_sessions.py."""
        headers = _key(db, _make_user(db, role="admin", name="Admin"))
        client.post("/api/admin/users", headers=headers, json={
            "email": "z@example.com", "password": "senha-forte-123", "role": "family", "name": "Z",
        })
        user = db.query(sa.User).filter(sa.User.email == "z@example.com").first()
        assert user.password_hash != "senha-forte-123"
        assert auth_sessions.verify_password("senha-forte-123", user.password_hash)


class TestEditar:
    def test_alterar_role_e_conceder_acesso_clinico_temporal(self, db, client):
        headers = _key(db, _make_user(db, role="admin", name="Admin"))
        alvo = _make_user(db, role="family", name="Vai virar suporte")
        resp = client.patch(f"/api/admin/users/{alvo.id}", headers=headers, json={
            "role": "admin_clinical", "privileged_access_hours": 8,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "admin_clinical"
        assert body["privileged_access_expires_at"] is not None

        db.refresh(alvo)
        assert alvo.privileged_access_expires_at > datetime.utcnow()
        assert alvo.privileged_access_expires_at < datetime.utcnow() + timedelta(hours=9)

    def test_admin_nao_pode_alterar_o_proprio_role(self, db, client):
        admin = _make_user(db, role="admin", name="Admin")
        headers = _key(db, admin)
        resp = client.patch(f"/api/admin/users/{admin.id}", headers=headers, json={"role": "family"})
        assert resp.status_code == 400

    def test_utilizador_inexistente_404(self, db, client):
        headers = _key(db, _make_user(db, role="admin", name="Admin"))
        resp = client.patch("/api/admin/users/999999", headers=headers, json={"name": "X"})
        assert resp.status_code == 404


class TestRevogar:
    def test_revogar_desativa_e_derruba_sessoes(self, db, client):
        admin = _make_user(db, role="admin", name="Admin")
        admin_headers = _key(db, admin)
        alvo = _make_user(db, role="family", name="Vai ser revogado", email="revoga@example.com")

        token = auth_sessions.login(db, "revoga@example.com", "senha-valida-123")
        assert token is not None
        assert auth_sessions.resolve_session(db, token) is not None

        resp = client.post(f"/api/admin/users/{alvo.id}/revoke", headers=admin_headers)
        assert resp.status_code == 200

        db.refresh(alvo)
        assert alvo.deleted_at is not None
        assert auth_sessions.resolve_session(db, token) is None  # sessão pré-existente cai

    def test_revogar_impede_login_seguinte(self, db, client):
        admin = _make_user(db, role="admin", name="Admin")
        admin_headers = _key(db, admin)
        alvo = _make_user(db, role="family", name="Alvo", email="loginfalha@example.com")
        client.post(f"/api/admin/users/{alvo.id}/revoke", headers=admin_headers)
        assert auth_sessions.login(db, "loginfalha@example.com", "senha-valida-123") is None

    def test_revogar_derruba_chave_de_api_tambem(self, db, client):
        """Ver comentário em api.py::_require_user — revogar a conta tem de bastar, mesmo com
        uma chave de API própria ainda não revogada individualmente."""
        admin = _make_user(db, role="admin", name="Admin")
        admin_headers = _key(db, admin)
        alvo = _make_user(db, role="clinician", name="Alvo com chave")
        alvo_headers = _key(db, alvo)

        assert client.get("/api/patients/directory", headers=alvo_headers).status_code == 200
        client.post(f"/api/admin/users/{alvo.id}/revoke", headers=admin_headers)
        assert client.get("/api/patients/directory", headers=alvo_headers).status_code == 401

    def test_admin_nao_pode_revogar_a_propria_conta(self, db, client):
        admin = _make_user(db, role="admin", name="Admin")
        headers = _key(db, admin)
        resp = client.post(f"/api/admin/users/{admin.id}/revoke", headers=headers)
        assert resp.status_code == 400

    def test_revogar_utilizador_inexistente_404(self, db, client):
        headers = _key(db, _make_user(db, role="admin", name="Admin"))
        assert client.post("/api/admin/users/999999/revoke", headers=headers).status_code == 404
