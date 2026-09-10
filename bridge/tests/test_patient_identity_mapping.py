"""Correspondência de identidade dashboard <-> base de dados (2026-09-07).

O que este ficheiro protege, em uma frase: **nunca associar dados ao
paciente errado**.

O dashboard identifica pacientes por ids de demonstração ('p1'/'p2'/'p3',
web/dashboard/pacientes.js) e a API pela chave primária inteira de
`patients`. A tentação óbvia — e o bug — seria mapear 'p2' -> 2. Numa
base de dados real as PKs são atribuídas por ordem de criação e não têm
nenhuma relação com a ordem em que o dashboard listou os pacientes.

A ponte real é `Patient.uuid` (estável entre bases) resolvido para a PK
em tempo de execução, via `GET /api/patients/directory` (bridge/api.py) +
`resolvePatientDbIds()` (web/dashboard/pacientes.js).

O cenário destes testes é deliberadamente hostil: o paciente que o
dashboard chama 'p2' tem PK **7**, e existe um paciente com PK 2 que é
outra pessoa. Se alguma vez alguém reintroduzir a heurística do número,
`test_p2_nao_le_dados_da_pk_2` parte.
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


def _make_user(db, role="family", name="Filha"):
    user = sa.User(uuid=str(_uuid.uuid4()), email=f"{_uuid.uuid4()}@example.com",
                   password_hash="(bcrypt em produção)", role=role, name=name)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _key(db, user):
    plaintext, key_hash = api_auth.generate_api_key()
    db.add(api_auth.ApiKey(user_id=user.id, key_hash=key_hash, label="test"))
    db.commit()
    return {"X-API-Key": plaintext}


# ------------------------------------------------------------------
# O que o dashboard tem hoje (web/dashboard/pacientes.js): id de
# demonstração + `dbUuid` como campo de ligação. `dbId` NÃO está aqui de
# propósito — é derivado, nunca escrito à mão.
# ------------------------------------------------------------------
DASHBOARD_PATIENTS = [
    {"id": "p1", "dbUuid": "uuid-maria"},
    {"id": "p2", "dbUuid": "uuid-antonio"},
    {"id": "p3", "dbUuid": None},  # paciente só de demonstração, sem par na BD
]


def _resolve_db_ids(directory, dashboard_patients):
    """Equivalente Python de `resolvePatientDbIds()` (pacientes.js).

    Mantido igual à versão JS de propósito: é a lógica que este teste
    tem de exercitar, e duplicá-la aqui é o preço de o dashboard não ter
    runner de testes próprio neste projeto. Qualquer alteração numa das
    duas obriga a alterar a outra — está anotado nos dois lados.
    """
    by_uuid = {row["uuid"]: row for row in directory["patients"]}
    resolved = {}
    for p in dashboard_patients:
        if not p.get("dbUuid"):
            continue                      # sem uuid não há resolução possível
        row = by_uuid.get(p["dbUuid"])
        if row is None:
            continue                      # uuid desconhecido nesta base de dados
        resolved[p["id"]] = row["id"]
    return resolved


@pytest.fixture
def cenario(db):
    """PKs DELIBERADAMENTE não coincidentes com os ids do dashboard.

    Cria-se lixo antes para empurrar as PKs, e depois apaga-se — é assim
    que uma base de dados real fica com buracos na sequência.
    """
    filler = []
    for i in range(1, 7):
        p = sa.Patient(uuid=f"uuid-filler-{i}", name=f"Filler {i}",
                       date_of_birth=datetime(1940, 1, 1), pseudonym=f"pseudo-filler-{i}")
        db.add(p)
        filler.append(p)
    db.commit()

    # PK 7 -> o paciente que o dashboard chama 'p2'
    antonio = sa.Patient(uuid="uuid-antonio", name="António Ferreira",
                         date_of_birth=datetime(1947, 5, 2), pseudonym="pseudo-antonio")
    db.add(antonio)
    db.commit()
    db.refresh(antonio)
    assert antonio.id == 7, "o cenário depende de o António ficar com a PK 7"

    # A PK 2 fica a ser OUTRA pessoa — é a armadilha do 'p2' -> 2.
    intruso_pk2 = filler[1]
    assert intruso_pk2.id == 2
    intruso_pk2.name = "Outra Pessoa"
    intruso_pk2.pseudonym = "pseudo-outra-pessoa"
    db.commit()

    # E a Maria ('p1') fica com a PK 1... por acaso. Não é garantia de nada.
    maria = filler[0]
    maria.uuid = "uuid-maria"
    maria.pseudonym = "pseudo-maria"
    db.commit()

    user = _make_user(db)
    for p in (maria, antonio, intruso_pk2):
        db.execute(sa.patient_caregivers.insert().values(
            patient_id=p.id, user_id=user.id, can_view_alerts=True,
            can_edit_notes=True, can_edit_medications=True))
    db.commit()
    return {"user": user, "maria": maria, "antonio": antonio, "outra": intruso_pk2}


class TestDirectory:
    def test_devolve_uuid_e_pk_dos_pacientes_autorizados(self, db, client, cenario):
        headers = _key(db, cenario["user"])
        body = client.get("/api/patients/directory", headers=headers).json()
        by_uuid = {row["uuid"]: row["id"] for row in body["patients"]}
        assert by_uuid["uuid-antonio"] == 7
        assert by_uuid["uuid-maria"] == 1

    def test_nao_expoe_nome_nem_dados_clinicos(self, db, client, cenario):
        headers = _key(db, cenario["user"])
        body = client.get("/api/patients/directory", headers=headers).json()
        assert set(body["patients"][0].keys()) == {"id", "uuid", "pseudonym"}
        assert "António" not in client.get("/api/patients/directory", headers=headers).text

    def test_so_lista_os_pacientes_a_que_o_utilizador_tem_direito(self, db, client, cenario):
        estranho = _make_user(db, name="Estranho")
        body = client.get("/api/patients/directory", headers=_key(db, estranho)).json()
        assert body["patients"] == []

    def test_exige_autenticacao(self, client):
        assert client.get("/api/patients/directory").status_code == 401


class TestResolucaoDeIdentidade:
    def test_p2_resolve_para_a_pk_7_e_nao_para_a_2(self, db, client, cenario):
        headers = _key(db, cenario["user"])
        directory = client.get("/api/patients/directory", headers=headers).json()
        mapa = _resolve_db_ids(directory, DASHBOARD_PATIENTS)
        assert mapa["p2"] == 7
        assert mapa["p2"] != 2

    def test_p2_nao_le_dados_da_pk_2(self, db, client, cenario):
        """O TESTE que protege contra "dados do paciente errado": o
        relatório semanal pedido para o 'p2' do dashboard tem de trazer o
        pseudónimo do António (PK 7), nunca o da pessoa com PK 2."""
        headers = _key(db, cenario["user"])
        directory = client.get("/api/patients/directory", headers=headers).json()
        mapa = _resolve_db_ids(directory, DASHBOARD_PATIENTS)

        relatorio = client.get(f"/api/patients/{mapa['p2']}/weekly-report", headers=headers).json()
        assert relatorio["patient_pseudonym"] == "pseudo-antonio"
        assert relatorio["patient_pseudonym"] != "pseudo-outra-pessoa"

        # E a prova pelo contrário: a heurística ingénua ('p2' -> 2) traria
        # mesmo os dados da pessoa errada — este endpoint responde 200 na
        # mesma, porque o id 2 existe e o utilizador até está associado.
        errado = client.get("/api/patients/2/weekly-report", headers=headers).json()
        assert errado["patient_pseudonym"] == "pseudo-outra-pessoa"

    def test_paciente_sem_dbuuid_nao_e_resolvido(self, db, client, cenario):
        """'p3' é só demonstração: sem `dbUuid` fica FORA do mapa, e o
        dashboard cai nos dados locais dizendo-o — em vez de adivinhar."""
        headers = _key(db, cenario["user"])
        directory = client.get("/api/patients/directory", headers=headers).json()
        assert "p3" not in _resolve_db_ids(directory, DASHBOARD_PATIENTS)

    def test_uuid_desconhecido_nesta_base_nao_e_resolvido(self, db, client, cenario):
        headers = _key(db, cenario["user"])
        directory = client.get("/api/patients/directory", headers=headers).json()
        mapa = _resolve_db_ids(directory, [{"id": "pX", "dbUuid": "uuid-de-outra-instalacao"}])
        assert mapa == {}

    def test_o_uuid_sobrevive_a_pks_diferentes_entre_bases(self, db, client, cenario):
        """Justificação da escolha do `uuid` em vez da PK: simula-se uma
        segunda base de dados (a "de produção") onde o mesmo paciente tem
        outra PK. O uuid resolve para a PK certa nas duas."""
        directory_producao = {"patients": [
            {"id": 43, "uuid": "uuid-antonio", "pseudonym": "outro-pseudo"},
            {"id": 44, "uuid": "uuid-maria", "pseudonym": "outro-pseudo-2"},
        ]}
        assert _resolve_db_ids(directory_producao, DASHBOARD_PATIENTS)["p2"] == 43

        headers = _key(db, cenario["user"])
        directory_local = client.get("/api/patients/directory", headers=headers).json()
        assert _resolve_db_ids(directory_local, DASHBOARD_PATIENTS)["p2"] == 7
