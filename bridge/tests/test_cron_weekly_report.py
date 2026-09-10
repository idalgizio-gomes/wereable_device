"""Testes da tarefa agendada do relatório semanal (`bridge/cron_weekly_report.py`,
2026-09-07).

O que interessa provar aqui não é "chama a API" — é o comportamento que
distingue uma tarefa agendada decente de um `curl` num crontab:

  * a janela (`end`) é fixada UMA vez no arranque e é a mesma para todos
    os pacientes (senão uma execução lenta produz semanas diferentes na
    mesma corrida);
  * uma falha num paciente não leva os outros atrás;
  * correr duas vezes na mesma semana não duplica trabalho (idempotência
    DO SCRIPT, não só da API);
  * sem credencial, falha alto e cedo em vez de gerar 401 em cadeia.

O cliente HTTP é injetado (`client_factory`) — os testes não sobem
servidor nenhum, o que é o ponto: testa-se a lógica de agendamento, não o
`urllib`. O último teste, esse sim, liga o script à API REAL por um
adaptador sobre `TestClient`, para provar que os dois contratos encaixam.
"""
import json
import uuid as _uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api
import api_auth
import cron_weekly_report as cron
import storage_advanced as sa


# ------------------------------------------------------------------
# Cliente falso
# ------------------------------------------------------------------

class _FakeClient:
    """Cliente com o mesmo contrato de `ApiClient.get_json`, sem rede.

    `failing_ids` permite simular pacientes que rebentam individualmente.
    `calls` regista tudo, para se poder afirmar sobre o `end` enviado.
    """

    def __init__(self, base_url="http://test", api_key="cw_testkey0000", timeout=30,
                 patients=None, failing_ids=(), fail_directory=False):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.patients = patients if patients is not None else []
        self.failing_ids = set(failing_ids)
        self.fail_directory = fail_directory
        self.calls = []

    def get_json(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path == "/api/patients/directory":
            if self.fail_directory:
                raise RuntimeError("API indisponível")
            return {"patients": self.patients}
        patient_id = int(path.split("/")[3])
        if patient_id in self.failing_ids:
            raise RuntimeError("boom")
        return {"paciente_id": patient_id, "rotina": {}, "alertas": {}, "periodo": params}


def _patients(n):
    return [
        {"id": i + 1, "uuid": f"uuid-{i + 1}", "pseudonym": f"pseudo-{i + 1}"}
        for i in range(n)
    ]


def _factory(**kwargs):
    def make(base_url, api_key, timeout):
        return _FakeClient(base_url=base_url, api_key=api_key, timeout=timeout, **kwargs)
    return make


END = date(2026, 9, 6)  # domingo -> semana ISO 2026-W36


# ------------------------------------------------------------------
# 1. Credencial de serviço
# ------------------------------------------------------------------

class TestCredencial:

    def test_sem_variavel_de_ambiente_falha_cedo(self):
        with pytest.raises(cron.CronError) as exc:
            cron.resolve_api_key({})
        assert cron.API_KEY_ENV in str(exc.value)

    def test_variavel_vazia_conta_como_ausente(self):
        with pytest.raises(cron.CronError):
            cron.resolve_api_key({cron.API_KEY_ENV: "   "})

    def test_chave_valida_e_devolvida(self):
        assert cron.resolve_api_key({cron.API_KEY_ENV: " cw_abc "}) == "cw_abc"

    def test_manifesto_nunca_contem_a_chave_inteira(self, tmp_path):
        chave = "cw_segredo_muito_secreto_1234567890"
        manifest = cron.run(
            api_key=chave, end=END, archive_dir=tmp_path,
            client_factory=_factory(patients=_patients(1)), log=lambda m: None,
        )
        serializado = json.dumps(manifest) + (tmp_path / "2026-W36" / "patient-uuid-1.json").read_text("utf-8")
        assert chave not in serializado
        assert manifest["api_key_fingerprint"] == "cw_segre..."

    def test_main_sem_credencial_devolve_codigo_2(self, monkeypatch, tmp_path):
        """Distinguir "não arrancou" de "falhou um paciente" é o ponto."""
        monkeypatch.delenv(cron.API_KEY_ENV, raising=False)
        assert cron.main(["--archive-dir", str(tmp_path)]) == cron.EXIT_CANNOT_START


# ------------------------------------------------------------------
# 2. Janela temporal
# ------------------------------------------------------------------

class TestJanelaTemporal:

    def test_end_por_omissao_e_ontem_utc(self):
        agora = datetime(2026, 9, 9, 3, 15, tzinfo=timezone.utc)
        assert cron.default_end_date(agora) == date(2026, 9, 8)

    def test_end_e_o_mesmo_para_todos_os_pacientes(self, tmp_path):
        """O motivo de `end` existir: uma execução lenta não pode deslocar a janela."""
        client_holder = {}

        def make(base_url, api_key, timeout):
            client_holder["c"] = _FakeClient(patients=_patients(5))
            return client_holder["c"]

        cron.run(api_key="cw_k", end=END, archive_dir=tmp_path,
                 client_factory=make, log=lambda m: None)
        ends = {
            params["end"]
            for path, params in client_holder["c"].calls
            if "weekly-report" in path
        }
        assert ends == {"2026-09-06"}

    def test_semana_iso_e_a_pasta(self):
        assert cron.week_folder_name(date(2026, 9, 6)) == "2026-W36"
        # Dias diferentes da MESMA semana ISO caem na mesma pasta — é isso
        # que torna a idempotência por ficheiro fiável quando a execução de
        # recuperação corre noutro dia.
        assert cron.week_folder_name(date(2026, 9, 2)) == cron.week_folder_name(date(2026, 9, 6))

    def test_end_invalido_e_rejeitado(self):
        with pytest.raises(cron.CronError):
            cron.parse_end_date("06-09-2026")


# ------------------------------------------------------------------
# 3. Arquivo
# ------------------------------------------------------------------

class TestArquivo:

    def test_layout_do_arquivo(self, tmp_path):
        cron.run(api_key="cw_k", end=END, archive_dir=tmp_path,
                 client_factory=_factory(patients=_patients(2)), log=lambda m: None)
        semana = tmp_path / "2026-W36"
        assert (semana / "patient-uuid-1.json").exists()
        assert (semana / "patient-uuid-2.json").exists()
        assert (semana / "_run.json").exists()

    def test_envelope_tem_metadados_e_relatorio_intacto(self, tmp_path):
        cron.run(api_key="cw_k", end=END, archive_dir=tmp_path,
                 client_factory=_factory(patients=_patients(1)), log=lambda m: None)
        payload = json.loads((tmp_path / "2026-W36" / "patient-uuid-1.json").read_text("utf-8"))
        assert payload["schema"] == "carewear.weekly-report-archive/1"
        assert payload["end"] == "2026-09-06"
        assert payload["week"] == "2026-W36"
        assert payload["patient"]["uuid"] == "uuid-1"
        assert payload["report"]["paciente_id"] == 1

    def test_uuid_malicioso_nao_escapa_da_pasta(self, tmp_path):
        """O uuid vem da API e vira nome de ficheiro — travessia bloqueada."""
        caminho = cron.report_path(tmp_path, END, "../../etc/passwd")
        assert caminho.parent == tmp_path / "2026-W36"
        assert ".." not in caminho.name

    def test_escrita_atomica_nao_deixa_ficheiro_tmp(self, tmp_path):
        cron.run(api_key="cw_k", end=END, archive_dir=tmp_path,
                 client_factory=_factory(patients=_patients(3)), log=lambda m: None)
        assert list((tmp_path / "2026-W36").glob("*.tmp")) == []

    def test_dry_run_nao_escreve_nada(self, tmp_path):
        manifest = cron.run(api_key="cw_k", end=END, archive_dir=tmp_path, dry_run=True,
                            client_factory=_factory(patients=_patients(2)), log=lambda m: None)
        assert manifest["counts"][cron.STATUS_OK] == 2
        assert not (tmp_path / "2026-W36").exists()


# ------------------------------------------------------------------
# 4. Idempotência
# ------------------------------------------------------------------

class TestIdempotencia:

    def test_segunda_execucao_na_mesma_semana_salta_tudo(self, tmp_path):
        comum = dict(api_key="cw_k", end=END, archive_dir=tmp_path, log=lambda m: None)
        primeira = cron.run(client_factory=_factory(patients=_patients(3)), **comum)
        assert primeira["counts"][cron.STATUS_OK] == 3

        segundo_cliente = {}

        def make(base_url, api_key, timeout):
            segundo_cliente["c"] = _FakeClient(patients=_patients(3))
            return segundo_cliente["c"]

        segunda = cron.run(client_factory=make, **comum)
        assert segunda["counts"][cron.STATUS_SKIPPED] == 3
        assert segunda["counts"][cron.STATUS_OK] == 0
        # E não se chega sequer a pedir o relatório — poupa a chamada E a
        # linha de auditoria de leitura clínica repetida.
        assert not any("weekly-report" in path for path, _ in segundo_cliente["c"].calls)

    def test_force_reescreve(self, tmp_path):
        comum = dict(api_key="cw_k", end=END, archive_dir=tmp_path, log=lambda m: None)
        cron.run(client_factory=_factory(patients=_patients(2)), **comum)
        segunda = cron.run(force=True, client_factory=_factory(patients=_patients(2)), **comum)
        assert segunda["counts"][cron.STATUS_OK] == 2
        assert segunda["counts"][cron.STATUS_SKIPPED] == 0

    def test_semana_diferente_nao_e_saltada(self, tmp_path):
        comum = dict(api_key="cw_k", archive_dir=tmp_path, log=lambda m: None)
        cron.run(end=END, client_factory=_factory(patients=_patients(1)), **comum)
        outra = cron.run(end=END + timedelta(days=7),
                         client_factory=_factory(patients=_patients(1)), **comum)
        assert outra["counts"][cron.STATUS_OK] == 1
        assert (tmp_path / "2026-W37" / "patient-uuid-1.json").exists()


# ------------------------------------------------------------------
# 5. Tolerância a falhas
# ------------------------------------------------------------------

class TestTolerenciaAFalhas:

    def test_falha_de_um_paciente_nao_aborta_os_restantes(self, tmp_path):
        manifest = cron.run(
            api_key="cw_k", end=END, archive_dir=tmp_path, log=lambda m: None,
            client_factory=_factory(patients=_patients(4), failing_ids=(2,)),
        )
        assert manifest["counts"][cron.STATUS_OK] == 3
        assert manifest["counts"][cron.STATUS_FAILED] == 1
        falhado = [r for r in manifest["patients"] if r["status"] == cron.STATUS_FAILED]
        assert falhado[0]["patient_id"] == 2
        assert "boom" in falhado[0]["error"]
        # Os outros três ficaram mesmo escritos.
        for i in (1, 3, 4):
            assert (tmp_path / "2026-W36" / f"patient-uuid-{i}.json").exists()

    def test_paciente_falhado_e_reprocessado_na_execucao_seguinte(self, tmp_path):
        """Sem isto, uma falha transitória perdia o relatório dessa semana."""
        comum = dict(api_key="cw_k", end=END, archive_dir=tmp_path, log=lambda m: None)
        cron.run(client_factory=_factory(patients=_patients(2), failing_ids=(2,)), **comum)
        segunda = cron.run(client_factory=_factory(patients=_patients(2)), **comum)
        assert segunda["counts"][cron.STATUS_SKIPPED] == 1  # o que já tinha corrido
        assert segunda["counts"][cron.STATUS_OK] == 1       # o que tinha falhado
        assert (tmp_path / "2026-W36" / "patient-uuid-2.json").exists()

    def test_codigo_de_saida_reflete_falhas_parciais(self, tmp_path):
        manifest = cron.run(
            api_key="cw_k", end=END, archive_dir=tmp_path, log=lambda m: None,
            client_factory=_factory(patients=_patients(2), failing_ids=(1,)),
        )
        assert cron.exit_code_for(manifest) == cron.EXIT_PARTIAL_FAILURE

    def test_descoberta_falhada_e_erro_de_arranque(self, tmp_path):
        with pytest.raises(cron.CronError):
            cron.run(api_key="cw_k", end=END, archive_dir=tmp_path, log=lambda m: None,
                     client_factory=_factory(fail_directory=True))

    def test_sem_pacientes_e_execucao_valida(self, tmp_path):
        manifest = cron.run(api_key="cw_k", end=END, archive_dir=tmp_path, log=lambda m: None,
                            client_factory=_factory(patients=[]))
        assert manifest["counts"] == {cron.STATUS_OK: 0, cron.STATUS_SKIPPED: 0, cron.STATUS_FAILED: 0}
        assert cron.exit_code_for(manifest) == cron.EXIT_OK


# ------------------------------------------------------------------
# 6. Contrato real com a API (sem rede, mas com a app verdadeira)
# ------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh_schema():
    sa.Base.metadata.drop_all(bind=sa.engine)
    sa.Base.metadata.create_all(bind=sa.engine)
    yield
    sa.Base.metadata.drop_all(bind=sa.engine)


class _TestClientAdapter:
    """Adaptador que dá a `ApiClient.get_json` sobre o `TestClient` do FastAPI.

    Prova que os caminhos, os parâmetros e o header de autenticação que o
    script usa são os que a API realmente serve — um teste só com o
    cliente falso não apanharia uma rota mal escrita.
    """

    def __init__(self, base_url, api_key, timeout=30):
        self.base_url = base_url
        self._client = TestClient(api.app)
        self._headers = {"X-API-Key": api_key}

    def get_json(self, path, params=None):
        response = self._client.get(path, params=params, headers=self._headers)
        response.raise_for_status()
        return response.json()


class TestContratoComApiReal:

    def test_execucao_ponta_a_ponta_contra_a_api(self, tmp_path):
        db = sa.get_db_session()
        try:
            # Utilizador de SERVIÇO com perfil clinician (nunca admin — o
            # Admin de Sistema não acede a dados clínicos nesta API).
            service = sa.User(
                uuid=str(_uuid.uuid4()),
                email="cron@carewear.local",
                password_hash="(bcrypt em produção)",
                role="clinician",
                name="Serviço Cron",
            )
            db.add(service)
            db.commit()
            db.refresh(service)

            plaintext, key_hash = api_auth.generate_api_key()
            db.add(api_auth.ApiKey(user_id=service.id, key_hash=key_hash, label="cron-weekly-report"))
            db.commit()

            associados = []
            for i in range(2):
                patient = sa.Patient(
                    uuid=f"pat-cron-{i}",
                    pseudonym=f"pseudo-cron-{i}",
                    name=f"Paciente {i}",
                    date_of_birth=datetime(1945, 3, 1),
                )
                db.add(patient)
                db.commit()
                db.refresh(patient)
                associados.append(patient)
                db.execute(sa.patient_caregivers.insert().values(
                    patient_id=patient.id,
                    user_id=service.id,
                    can_view_alerts=True,
                    can_edit_notes=False,
                    can_edit_medications=False,
                ))
                db.commit()

            # Paciente NÃO associado ao utilizador de serviço: não deve
            # sequer aparecer na descoberta (menor privilégio).
            db.add(sa.Patient(
                uuid="pat-cron-fora",
                pseudonym="pseudo-cron-fora",
                name="Fora do âmbito",
                date_of_birth=datetime(1950, 1, 1),
            ))
            db.commit()
        finally:
            db.close()

        manifest = cron.run(
            base_url="http://testserver",
            api_key=plaintext,
            end=END,
            archive_dir=tmp_path,
            client_factory=lambda base_url, api_key, timeout: _TestClientAdapter(base_url, api_key),
            log=lambda m: None,
        )

        assert manifest["counts"][cron.STATUS_OK] == 2
        assert manifest["counts"][cron.STATUS_FAILED] == 0
        assert {r["patient_uuid"] for r in manifest["patients"]} == {"pat-cron-0", "pat-cron-1"}

        payload = json.loads((tmp_path / "2026-W36" / "patient-pat-cron-0.json").read_text("utf-8"))
        # As quatro secções do RF-12 chegaram intactas ao arquivo.
        for seccao in ("rotina", "sinais_vitais", "alertas", "adesao_medicacao"):
            assert seccao in payload["report"]

        # E a leitura ficou auditada em nome do utilizador de SERVIÇO.
        db = sa.get_db_session()
        try:
            linhas = db.query(sa.AuditLog).filter(sa.AuditLog.action == "weekly_report.read").all()
            assert len(linhas) == 2
        finally:
            db.close()
