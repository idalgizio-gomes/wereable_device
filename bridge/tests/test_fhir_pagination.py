"""Testes da paginação FHIR do endpoint `/api/devices/{id}/fhir/observations`
(2026-09-07).

Antes desta data o endpoint truncava a resposta em 5000 recursos e marcava
a truncagem com uma `meta.tag`. O resto dos dados era simplesmente
inalcançável. Agora há paginação real por `Bundle.link` (`relation="self"`
e `relation="next"`), como manda o FHIR R4.

O teste que interessa mesmo é `test_uniao_das_paginas_e_exatamente_o_conjunto
_completo`: paginação errada quase nunca falha na primeira página — falha a
duplicar ou a perder um recurso na FRONTEIRA entre páginas, e só se apanha
percorrendo TODAS as páginas e comparando a união com o conjunto sem
paginação. Os restantes testes cobrem os casos-limite um a um (primeira,
intermédia, última, vazia, mais de 5000, ordenação, presença/ausência de
`next`).

Nota sobre rate limiting: o middleware (API-003) conta 60 leituras por
minuto por `(ip, prefixo-da-chave)`. Como cada teste emite uma chave nova,
cada teste tem o seu próprio balde — mas dentro de UM teste o número de
páginas percorridas tem de ficar abaixo de 60, o que é tido em conta na
escolha dos `_count` abaixo.
"""
import uuid as _uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api
import api_auth
import fhir_export
import storage_advanced as sa


# ------------------------------------------------------------------
# Fixtures — mesmo padrão de test_fhir_export.py / test_api.py
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


def _make_user(db, role="family"):
    user = sa.User(
        uuid=str(_uuid.uuid4()),
        email=f"{_uuid.uuid4()}@example.com",
        password_hash="(bcrypt em produção)",
        role=role,
        name="Família",
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
    user = _make_user(db)
    return _Auth(user, _issue_key(db, user))


@pytest.fixture
def device(db, primary):
    patient = sa.Patient(
        uuid="pat-pag-1",
        pseudonym="pseudo-pag-1",
        name="Maria Silva",
        date_of_birth=datetime(1945, 3, 1),
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)
    dev = sa.Device(uuid="dev-pag-1", patient_id=patient.id, mac_address="AA:BB:CC:DD:EE:F2")
    db.add(dev)
    db.commit()
    db.refresh(dev)
    db.execute(sa.patient_caregivers.insert().values(
        patient_id=patient.id,
        user_id=primary.user.id,
        can_view_alerts=True,
        can_edit_notes=True,
        can_edit_medications=True,
    ))
    db.commit()
    return dev


# Cada `SensorRecord` com os 4 sinais preenchidos gera 4 Observations
# (hr/spo2/steps/pacing) — ver `observations_from_sensor_record`.
OBS_POR_REGISTO = 4


def _seed_records(db, device, n, signals=OBS_POR_REGISTO):
    """Cria `n` SensorRecord com timestamps ESTRITAMENTE crescentes.

    Timestamps distintos de propósito: assim a ordem esperada é conhecida
    sem ambiguidade e um erro de ordenação não fica escondido por empates.
    """
    base_ts = int(datetime.now(timezone.utc).timestamp()) - n - 10
    now = datetime.utcnow()
    rows = []
    for i in range(n):
        rows.append(sa.SensorRecord(
            device_id=device.id,
            timestamp_utc=base_ts + i,
            heart_rate=70 + (i % 10),
            spo2_percent=95 + (i % 4) if signals >= 2 else None,
            steps_count=1000 + i if signals >= 3 else None,
            pacing_index=i % 100 if signals >= 4 else None,
            received_at=now,
        ))
    db.add_all(rows)
    db.commit()
    return rows


def _ids(bundle):
    return [e["resource"]["id"] for e in bundle.get("entry", [])]


def _link(bundle, relation):
    for link in bundle.get("link", []):
        if link["relation"] == relation:
            return link["url"]
    return None


def _get_page(client, device, headers, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"/api/devices/{device.id}/fhir/observations"
    if query:
        url = f"{url}?{query}"
    response = client.get(url, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _walk_all_pages(client, device, headers, count, max_pages=50):
    """Percorre as páginas SEGUINDO `link next` (não construindo URLs à mão).

    Seguir o `next` é o que um cliente FHIR real faz; testar assim valida
    também que o URL emitido é utilizável, e não só que existe.
    """
    bundles = []
    page = 1
    while True:
        bundle = _get_page(client, device, headers, _count=count, _page=page)
        bundles.append(bundle)
        if _link(bundle, "next") is None:
            break
        page += 1
        assert page <= max_pages, "demasiadas páginas — provável ciclo infinito"
    return bundles


# ------------------------------------------------------------------
# 1. Casos-limite de página
# ------------------------------------------------------------------

class TestPaginas:

    def test_primeira_pagina(self, client, db, device, primary):
        _seed_records(db, device, 10)  # 40 recursos
        bundle = _get_page(client, device, primary.headers, _count=7, _page=1)
        assert len(bundle["entry"]) == 7
        # `total` é o total da PESQUISA, não o desta página (FHIR R4).
        assert bundle["total"] == 40
        assert _link(bundle, "self") is not None
        assert _link(bundle, "next") is not None
        assert "_page=2" in _link(bundle, "next")

    def test_pagina_intermedia(self, client, db, device, primary):
        _seed_records(db, device, 10)  # 40 recursos, _count=7 -> 6 páginas
        primeira = _get_page(client, device, primary.headers, _count=7, _page=1)
        terceira = _get_page(client, device, primary.headers, _count=7, _page=3)
        assert len(terceira["entry"]) == 7
        assert _link(terceira, "next") is not None
        # Uma página intermédia não repete nada da primeira.
        assert set(_ids(terceira)).isdisjoint(_ids(primeira))

    def test_ultima_pagina_nao_tem_link_next(self, client, db, device, primary):
        _seed_records(db, device, 10)  # 40 recursos
        # 40 = 6*7 + ... -> a página 6 tem 40 - 35 = 5 entradas e é a última.
        ultima = _get_page(client, device, primary.headers, _count=7, _page=6)
        assert len(ultima["entry"]) == 5
        assert _link(ultima, "next") is None, (
            "a AUSÊNCIA de link next é o sinal normativo de fim de resultados"
        )
        assert _link(ultima, "self") is not None

    def test_pagina_exatamente_cheia_no_fim_nao_anuncia_next(self, client, db, device, primary):
        """Caso mais traiçoeiro: o total é múltiplo exato do tamanho da página.

        Uma implementação que decida "há next se a página veio cheia"
        anuncia aqui uma página seguinte que está vazia. Daí o pedido de
        `_count + 1` recursos em vez de olhar para o tamanho da página.
        """
        _seed_records(db, device, 5)  # 20 recursos
        bundle = _get_page(client, device, primary.headers, _count=10, _page=2)
        assert len(bundle["entry"]) == 10
        assert bundle["total"] == 20
        assert _link(bundle, "next") is None

    def test_sem_resultados(self, client, device, primary):
        bundle = _get_page(client, device, primary.headers, _count=10, _page=1)
        assert bundle["resourceType"] == "Bundle"
        assert bundle["type"] == "searchset"
        assert bundle["total"] == 0
        assert bundle["entry"] == []
        assert _link(bundle, "next") is None
        assert _link(bundle, "self") is not None

    def test_pagina_para_alem_do_fim_vem_vazia_e_sem_next(self, client, db, device, primary):
        _seed_records(db, device, 3)  # 12 recursos
        bundle = _get_page(client, device, primary.headers, _count=10, _page=99)
        assert bundle["entry"] == []
        assert bundle["total"] == 12  # o total da pesquisa mantém-se correto
        assert _link(bundle, "next") is None

    def test_count_acima_do_teto_e_rejeitado(self, client, device, primary):
        """A protecção contra pedidos excessivos NÃO foi removida.

        O que foi removido foi a truncagem silenciosa; o teto por página
        continua a existir e um pedido acima dele é rejeitado (422), em vez
        de ser servido em surdina com menos dados do que o pedido.
        """
        response = client.get(
            f"/api/devices/{device.id}/fhir/observations"
            f"?_count={fhir_export.MAX_PAGE_SIZE + 1}",
            headers=primary.headers,
        )
        assert response.status_code == 422

    def test_page_zero_e_rejeitada(self, client, device, primary):
        response = client.get(
            f"/api/devices/{device.id}/fhir/observations?_page=0",
            headers=primary.headers,
        )
        assert response.status_code == 422

    def test_bundle_paginado_continua_valido(self, client, db, device, primary):
        _seed_records(db, device, 10)
        bundle = _get_page(client, device, primary.headers, _count=7, _page=1)
        assert fhir_export.validate_bundle(bundle) == []

    def test_link_next_preserva_os_outros_parametros(self, client, db, device, primary):
        """Um `next` que perca `hours`/`include_activity` mudaria a pesquisa."""
        _seed_records(db, device, 10)
        bundle = _get_page(
            client, device, primary.headers,
            _count=7, _page=1, hours=48, include_activity="false",
        )
        next_url = _link(bundle, "next")
        assert "hours=48" in next_url
        assert "include_activity=false" in next_url


# ------------------------------------------------------------------
# 2. Integridade do conjunto: sem duplicação, sem perda, ordem estável
# ------------------------------------------------------------------

class TestIntegridadeDoConjunto:

    def test_uniao_das_paginas_e_exatamente_o_conjunto_completo(self, client, db, device, primary):
        """O teste central: união de TODAS as páginas == conjunto sem paginação.

        Compara-se com o conjunto obtido numa só página (`_count` no teto),
        e não com uma lista construída à mão, para que a referência seja o
        próprio comportamento não-paginado do endpoint.
        """
        _seed_records(db, device, 30)  # 120 recursos
        referencia = _get_page(
            client, device, primary.headers,
            _count=fhir_export.MAX_PAGE_SIZE, _page=1,
        )
        esperados = _ids(referencia)
        assert len(esperados) == 120
        assert _link(referencia, "next") is None

        paginados = []
        for bundle in _walk_all_pages(client, device, primary.headers, count=13):
            paginados.extend(_ids(bundle))

        # Sem PERDA e sem DUPLICAÇÃO: a igualdade de LISTAS (não de
        # conjuntos) verifica as duas coisas de uma vez e ainda a ordem.
        assert paginados == esperados
        assert len(paginados) == len(set(paginados))

    def test_sem_duplicados_na_fronteira_entre_paginas(self, client, db, device, primary):
        """Fronteira exata: o último recurso da página N não abre a N+1."""
        _seed_records(db, device, 10)  # 40 recursos
        p1 = _ids(_get_page(client, device, primary.headers, _count=4, _page=1))
        p2 = _ids(_get_page(client, device, primary.headers, _count=4, _page=2))
        assert p1[-1] != p2[0]
        assert set(p1).isdisjoint(p2)

    def test_ordenacao_determinista_e_crescente_entre_paginas(self, client, db, device, primary):
        """A ordem é `(instante efetivo, id)` — total, explícita e crescente.

        Verifica-se a monotonia sobre a CONCATENAÇÃO das páginas: é aí que
        um critério de ordenação dependente da ordem natural do SQL se
        denuncia.
        """
        _seed_records(db, device, 20)  # 80 recursos
        recursos = []
        for bundle in _walk_all_pages(client, device, primary.headers, count=9):
            recursos.extend(e["resource"] for e in bundle["entry"])
        chaves = [fhir_export.observation_sort_key(r) for r in recursos]
        assert chaves == sorted(chaves)
        assert len(set(chaves)) == len(chaves), "a chave de ordenação tem de ser única"

    def test_ordem_estavel_entre_pedidos_repetidos(self, client, db, device, primary):
        """Duas leituras da mesma página dão exatamente o mesmo resultado."""
        _seed_records(db, device, 15)
        a = _ids(_get_page(client, device, primary.headers, _count=11, _page=2))
        b = _ids(_get_page(client, device, primary.headers, _count=11, _page=2))
        assert a == b

    def test_mais_de_5000_recursos_sao_todos_alcancaveis(self, client, db, device, primary):
        """O caso que motivou tudo isto: mais de 5000 recursos na janela.

        Antes, a resposta era truncada em 5000 e o resto era inalcançável.
        Agora o `next` leva ao resto, e o `total` anuncia o número real.
        """
        n = 1500
        _seed_records(db, device, n)  # 6000 recursos > MAX_PAGE_SIZE (5000)
        total_esperado = n * OBS_POR_REGISTO
        assert total_esperado > fhir_export.MAX_PAGE_SIZE

        bundles = _walk_all_pages(
            client, device, primary.headers, count=fhir_export.MAX_PAGE_SIZE,
        )
        assert len(bundles) == 2
        assert len(bundles[0]["entry"]) == fhir_export.MAX_PAGE_SIZE
        assert len(bundles[1]["entry"]) == total_esperado - fhir_export.MAX_PAGE_SIZE
        assert _link(bundles[0], "next") is not None
        assert _link(bundles[1], "next") is None

        todos = [i for b in bundles for i in _ids(b)]
        assert len(todos) == total_esperado
        assert len(set(todos)) == total_esperado, "nenhum recurso repetido"
        assert bundles[0]["total"] == total_esperado

        # E a truncagem silenciosa desapareceu de facto.
        for bundle in bundles:
            tags = (bundle.get("meta") or {}).get("tag", [])
            assert all(t.get("code") != "carewear-truncated" for t in tags)

    def test_registos_com_sinais_em_falta_nao_desalinham_as_paginas(self, client, db, device, primary):
        """Fan-out variável: nem todos os registos geram 4 recursos.

        Sinais a `None` são omitidos (decisão de `fhir_export`), portanto o
        número de recursos por linha da BD varia. Uma paginação que assumisse
        "4 recursos por linha" partia-se aqui.
        """
        _seed_records(db, device, 5, signals=4)
        _seed_records(db, device, 5, signals=1)  # só heart_rate
        total_esperado = 5 * 4 + 5 * 1
        referencia = _ids(_get_page(
            client, device, primary.headers,
            _count=fhir_export.MAX_PAGE_SIZE, _page=1,
        ))
        assert len(referencia) == total_esperado
        paginados = []
        for bundle in _walk_all_pages(client, device, primary.headers, count=3):
            paginados.extend(_ids(bundle))
        assert paginados == referencia


# ------------------------------------------------------------------
# 3. Interação com as janelas de atividade (segunda fonte de recursos)
# ------------------------------------------------------------------

class TestFusaoDasDuasFontes:

    def test_recursos_das_duas_fontes_saem_ordenados_e_completos(self, client, db, device, primary):
        """`SensorRecord` e `ActivityWindow` são fundidos numa só sequência.

        A fusão (`heapq.merge`) é o ponto onde um erro de ordenação
        produziria uma sequência não-monótona e, com paginação por offset,
        recursos perdidos.
        """
        _seed_records(db, device, 6)  # 24 recursos
        base_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        for i in range(4):
            db.add(sa.ActivityWindow(
                device_id=device.id,
                activity_date=base_date,
                activity_category="rest",
                start_time=60 * (i + 1),
                end_time=60 * (i + 2),
                duration_minutes=60,
            ))
        db.commit()

        recursos = []
        for bundle in _walk_all_pages(client, device, primary.headers, count=5):
            recursos.extend(e["resource"] for e in bundle["entry"])
        ids = [r["id"] for r in recursos]

        assert len(ids) == len(set(ids))
        assert sum(1 for i in ids if i.startswith("activity-")) == 4
        chaves = [fhir_export.observation_sort_key(r) for r in recursos]
        assert chaves == sorted(chaves)

    def test_total_reflete_include_activity(self, client, db, device, primary):
        _seed_records(db, device, 3)  # 12 recursos
        base_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        db.add(sa.ActivityWindow(
            device_id=device.id,
            activity_date=base_date,
            activity_category="sleep",
            start_time=0,
            end_time=300,
            duration_minutes=300,
        ))
        db.commit()

        com = _get_page(client, device, primary.headers, _count=100, include_activity="true")
        sem = _get_page(client, device, primary.headers, _count=100, include_activity="false")
        assert com["total"] == 13
        assert sem["total"] == 12
        assert com["total"] == len(com["entry"])
        assert sem["total"] == len(sem["entry"])
