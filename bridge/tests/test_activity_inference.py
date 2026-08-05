"""Testes de `bridge/activity_inference.py` — classificação de atividade em
tempo real sobre o stream real do IMU/PPG, ligando o classificador Random
Forest já treinado em `ml/` (nunca invocado fora dessa pasta antes de
2026-07-20) ao bridge.

Corre inteiramente contra o modelo real já commitado em
`ml/models/activity_classifier_rf.joblib` (não um mock) — o objetivo é
confirmar que o caminho dados→features→modelo→bloco funciona de facto, não
só que as peças se encaixam. Sem hardware BLE nem rede real.

Cobre:
  (a) o buffer só classifica quando a janela atinge WINDOW_SECONDS, não antes;
  (b) uma janela demasiado esparsa (perda de pacotes) é descartada, não
      classificada;
  (c) um sinal parado classifica como uma classe de repouso, com o aviso
      (ACTIVITY_ML_DISCLAIMER) sempre presente no resultado;
  (d) blocos consecutivos da MESMA classe não fecham nada; uma mudança de
      classe fecha o bloco anterior e aplica duration_detector.evaluate_block;
  (e) FC em falta na janela inteira usa o último valor conhecido (ou um
      placeholder neutro na primeira janela) em vez de rebentar com NaN;
  (f) mapeamento de classe (PT) para categoria da BD (EN, CLASS_TO_DB_CATEGORY)
      cobre as 5 classes do classificador;
  (g) falha ao carregar o modelo (ficheiro em falta) degrada para
      `available=False`, nunca lança exceção.
"""
import time

import numpy as np
import pytest

import activity_inference as ai
from duration_detector import evaluate_block


def _still_record(ts, hr=None):
    """Uma amostra de sinal parado (accel só com gravidade em Z, giro a
    zero) — corresponde à classe de repouso mais próxima que o classificador
    aprendeu no dataset sintético."""
    return {"ts": ts, "ax": 0.0, "ay": 0.0, "az": 1.0, "gx": 0.0, "gy": 0.0, "gz": 0.0, "hr": hr}


def _feed_still_window(inf, start_ts=0.0, n=530, hr_every=20, hr_value=60):
    """Alimenta uma janela completa de sinal parado. Devolve o resultado da
    última amostra (não-None só quando a janela fecha).

    ts em SEGUNDOS (não ms) — mesmo formato do "device_timestamp" real
    gravado por storage.py (Unix epoch em segundos, ver schema.sql). Um
    bug real (corrigido 2026-07-20, apanhado só com hardware real) tratava
    ts como já estando em ms; estes fixtures alimentavam ts em ms também,
    o que escondia o bug em vez de o apanhar — corrigido aqui para o
    formato real."""
    result = None
    ts = start_ts
    for i in range(n):
        hr = hr_value if (hr_every and i % hr_every == 0) else None
        r = inf.add_sample(_still_record(ts, hr=hr))
        if r:
            result = r
        ts += 1.0 / ai.FS_HZ
    return result, ts


class TestCarregamentoDoModelo:
    def test_modelo_real_carrega_com_sucesso(self):
        inf = ai.ActivityInference()
        assert inf.available
        assert inf.load_error is None
        assert set(ai.CLASS_TO_DB_CATEGORY) == set(inf._classes)

    def test_ficheiro_de_modelo_em_falta_degrada_sem_excecao(self, monkeypatch):
        monkeypatch.setattr(
            ai, "_ML_DIR", ai._ML_DIR.parent / "nao_existe_de_todo"
        )
        inf = ai.ActivityInference()
        assert not inf.available
        assert inf.load_error is not None
        # add_sample nunca deve rebentar só porque o modelo está indisponível.
        assert inf.add_sample(_still_record(0)) is None


class TestJanelaDeslizante:
    def test_nao_classifica_antes_da_janela_completa(self):
        inf = ai.ActivityInference()
        ts = 0.0
        for _ in range(100):  # bem menos que os ~520 esperados em 10s a 52Hz
            result = inf.add_sample(_still_record(ts))
            assert result is None
            ts += 1.0 / ai.FS_HZ

    def test_janela_esparsa_e_descartada_nao_classificada(self):
        """Simula perda de pacotes: poucas amostras mas span temporal >=
        WINDOW_SECONDS (ex.: só chegaram 5 de ~520 amostras esperadas). ts
        em SEGUNDOS (formato real do device_timestamp, ver docstring de
        _feed_still_window)."""
        inf = ai.ActivityInference()
        inf.add_sample(_still_record(0))
        result = inf.add_sample(_still_record(ai.WINDOW_SECONDS + 1))
        assert result is None
        assert inf._buffer == []  # janela foi descartada, não deixada a acumular

    def test_sinal_parado_classifica_com_aviso_presente(self):
        inf = ai.ActivityInference()
        result, _ = _feed_still_window(inf)
        assert result is not None
        assert result["kind"] == "activity_classification"
        assert result["category"] in ai.CLASS_TO_DB_CATEGORY
        assert result["db_category"] == ai.CLASS_TO_DB_CATEGORY[result["category"]]
        assert 0.0 <= result["confidence"] <= 1.0
        assert result["disclaimer"] == ai.ACTIVITY_ML_DISCLAIMER
        assert result["session"] in ("dia", "noite")


class TestFrequenciaCardiacaEmFalta:
    def test_sem_nenhuma_leitura_de_fc_nao_classifica_com_valor_inventado(self):
        # Corrigido 2026-07-21 (achado com hardware real, ver comentário em
        # activity_inference.py): antes, esta janela era classificada sobre
        # um placeholder de 70bpm inventado, o que enviesava a previsão para
        # classes de repouso mesmo sem qualquer FC real. Agora fica por
        # classificar em vez de arriscar uma classificação confiante sobre
        # dados fabricados.
        inf = ai.ActivityInference()
        result, _ = _feed_still_window(inf, hr_every=None)  # nunca envia hr
        assert result is None  # não rebenta, mas também não inventa FC

    def test_usa_ultimo_valor_conhecido_quando_janela_atual_nao_tem_fc(self):
        inf = ai.ActivityInference()
        # primeira janela com FC real...
        result1, next_ts = _feed_still_window(inf, start_ts=0, hr_every=20, hr_value=72)
        assert inf._last_hr == 72
        # ...segunda janela sem nenhuma leitura nova de FC, mas ainda dentro
        # de HR_STALE_AFTER_S — o último valor conhecido continua válido.
        result2, _ = _feed_still_window(inf, start_ts=next_ts, hr_every=None)
        assert inf._last_hr == 72  # não foi apagado só por a janela não trazer FC
        assert result2 is not None  # ainda classifica, a FC de 72 continua fresca

    def test_ultimo_valor_conhecido_expira_apos_hr_stale_after_s(self):
        # Corrigido 2026-07-21 (achado ao vivo: FC parou de chegar a meio de
        # um teste real, mas a classificação continuava confiante minutos
        # depois usando a última FC real, já muito antiga). self._last_hr
        # nunca expirava antes desta correção.
        inf = ai.ActivityInference()
        _feed_still_window(inf, start_ts=0, hr_every=20, hr_value=72)
        assert inf._last_hr == 72
        # Janela seguinte, começando bem depois de HR_STALE_AFTER_S ter
        # passado desde a última leitura real de FC — não deve reutilizar
        # o valor antigo.
        far_future_ts = ai.HR_STALE_AFTER_S + 3 * ai.WINDOW_SECONDS
        result, _ = _feed_still_window(inf, start_ts=far_future_ts, hr_every=None)
        assert result is None  # FC antiga demasiado velha para ser reutilizada


class TestAgrupamentoDeBlocosEDeteccaoDeDuracao:
    def test_janelas_consecutivas_da_mesma_classe_nao_fecham_bloco(self):
        inf = ai.ActivityInference()
        result1, next_ts = _feed_still_window(inf, start_ts=0)
        result2, _ = _feed_still_window(inf, start_ts=next_ts)
        assert result1["category"] == result2["category"]
        assert result1["closed_block"] is None
        assert result2["closed_block"] is None
        assert inf._current_block["cls"] == result1["category"]

    def test_mudanca_de_classe_fecha_bloco_anterior_com_veredito_do_duration_detector(self):
        inf = ai.ActivityInference()
        result1, next_ts = _feed_still_window(inf, start_ts=0)
        first_cls = result1["category"]

        # Força uma classe diferente na 2ª janela via monkeypatch do modelo
        # (mais robusto do que tentar desenhar um sinal sintético "de
        # movimento" que bata certo com o classificador real).
        other_cls = next(c for c in inf._classes if c != first_cls)
        other_idx = inf._classes.index(other_cls)

        class _FakePredict:
            def predict(self, x):
                import numpy as np
                return np.array([other_idx])

            def predict_proba(self, x):
                import numpy as np
                proba = np.zeros(len(inf._classes))
                proba[other_idx] = 0.99
                return np.array([proba])

        inf._model = _FakePredict()
        result2, _ = _feed_still_window(inf, start_ts=next_ts)

        assert result2["category"] == other_cls
        closed = result2["closed_block"]
        assert closed is not None
        assert closed["cls"] == first_cls
        assert closed["db_category"] == ai.CLASS_TO_DB_CATEGORY[first_cls]
        assert closed["duration_min"] > 0
        assert closed["confidence"] == pytest.approx(result1["confidence"])
        assert "start_time_minutes" in closed and "end_time_minutes" in closed

        # O veredito devolvido bate certo com uma chamada direta e
        # independente a duration_detector.evaluate_block (não é um valor
        # inventado pelo módulo, é o mesmo detetor já validado em ml/).
        expected_anomaly, expected_reason = evaluate_block(
            closed["session"], closed["cls"], closed["duration_min"]
        )
        assert closed["is_anomaly"] == expected_anomaly
        assert closed["reason"] == expected_reason


class TestIndicadorDeIncerteza:
    """'Indicador de incerteza' (2026-08-05) — runner_up_category/
    runner_up_confidence/confidence_margin/is_uncertain, derivados da
    distribuição de probabilidade completa (predict_proba), não só do
    top-1 (confidence)."""

    def _fake_model(self, inf, proba_by_idx: dict, pred_idx: int):
        class _FakePredict:
            def predict(self, x):
                return np.array([pred_idx])

            def predict_proba(self, x):
                proba = np.zeros(len(inf._classes))
                for idx, p in proba_by_idx.items():
                    proba[idx] = p
                return np.array([proba])

        return _FakePredict()

    def test_confident_prediction_is_not_uncertain(self):
        inf = ai.ActivityInference()
        cls_a, cls_b = inf._classes[0], inf._classes[1]
        idx_a, idx_b = 0, 1
        inf._model = self._fake_model(inf, {idx_a: 0.9, idx_b: 0.1}, idx_a)

        result, _ = _feed_still_window(inf)

        assert result["category"] == cls_a
        assert result["runner_up_category"] == cls_b
        assert result["runner_up_confidence"] == pytest.approx(0.1)
        assert result["confidence_margin"] == pytest.approx(0.8)
        assert result["is_uncertain"] is False

    def test_close_call_between_top_two_is_uncertain(self):
        inf = ai.ActivityInference()
        idx_a, idx_b = 0, 1
        inf._model = self._fake_model(inf, {idx_a: 0.42, idx_b: 0.40}, idx_a)

        result, _ = _feed_still_window(inf)

        assert result["confidence"] == pytest.approx(0.42)
        assert result["runner_up_confidence"] == pytest.approx(0.40)
        assert result["confidence_margin"] == pytest.approx(0.02)
        assert result["is_uncertain"] is True

    def test_margin_exactly_at_threshold_is_not_flagged_uncertain(self):
        """UNCERTAINTY_MARGIN_THRESHOLD é um limiar estrito (margin <
        threshold), não <=  — a margem exatamente no limiar ainda conta
        como suficientemente clara."""
        inf = ai.ActivityInference()
        idx_a, idx_b = 0, 1
        top1 = 0.5
        runner_up = top1 - ai.UNCERTAINTY_MARGIN_THRESHOLD
        inf._model = self._fake_model(inf, {idx_a: top1, idx_b: runner_up}, idx_a)

        result, _ = _feed_still_window(inf)

        assert result["confidence_margin"] == pytest.approx(ai.UNCERTAINTY_MARGIN_THRESHOLD)
        assert result["is_uncertain"] is False

    def test_low_top1_but_clear_margin_is_not_uncertain(self):
        """Top-1 baixo (0.3) mas as restantes classes muito abaixo — é uma
        decisão clara apesar da confidence nominal baixa; distingue-se de
        um verdadeiro empate só pela margem, não pelo valor de confidence
        sozinho (motivação do próprio indicador, ver docstring do módulo)."""
        inf = ai.ActivityInference()
        idx_a, idx_b = 0, 1
        inf._model = self._fake_model(inf, {idx_a: 0.3, idx_b: 0.05}, idx_a)

        result, _ = _feed_still_window(inf)

        assert result["confidence"] == pytest.approx(0.3)
        assert result["confidence_margin"] == pytest.approx(0.25)
        assert result["is_uncertain"] is False


class TestMapeamentoDeClasses:
    def test_todas_as_5_classes_tem_categoria_de_bd_valida(self):
        db_categories_validas = {"sleep", "rest", "activity", "eating", "hygiene"}
        assert set(ai.CLASS_TO_DB_CATEGORY.values()) <= db_categories_validas
        assert set(ai.CLASS_TO_DB_CATEGORY) == {
            "Dormir", "Descanso", "Atividade", "Alimentação", "Higiene",
        }


class TestSessaoDiaNoite:
    def test_hora_dentro_do_intervalo_diurno_e_dia(self):
        # meio-dia de qualquer data — sempre dentro de [7h, 22h)
        noon = time.mktime(time.strptime("2026-07-20 12:00:00", "%Y-%m-%d %H:%M:%S"))
        assert ai.ActivityInference._session_for(noon) == "dia"

    def test_hora_de_madrugada_e_noite(self):
        night = time.mktime(time.strptime("2026-07-20 03:00:00", "%Y-%m-%d %H:%M:%S"))
        assert ai.ActivityInference._session_for(night) == "noite"


@pytest.fixture
def fresh_ml_schema():
    """Isola o schema inteiro (mesmo padrão de
    test_storage_advanced.py::_fresh_schema) só para os testes de
    versionamento/rollback abaixo -- o engine/SessionLocal de
    storage_advanced.py é um singleton partilhado por TODA a sessão de
    pytest (ver conftest.py), por isso "BD vazia" (necessário para o teste
    de auto-registo da versão inicial) só é garantido recriando o schema
    do zero antes do teste. Não é autouse: o resto deste ficheiro nunca
    dependeu do estado da BD (a degradação em _resolve_active_model_paths
    tolera tabela em falta/ausente) e não deve passar a depender agora."""
    ai.sa.Base.metadata.drop_all(bind=ai.sa.engine)
    ai.sa.Base.metadata.create_all(bind=ai.sa.engine)
    yield ai.sa
    ai.sa.Base.metadata.drop_all(bind=ai.sa.engine)


class TestVersionamentoDoModelo:
    """Versionamento e rollback do modelo ML (2026-08-05, ver
    storage_advanced.py::MlModelVersion e
    activity_inference.py::_resolve_active_model_paths/
    reload_active_model)."""

    def test_auto_registo_da_versao_inicial_no_primeiro_arranque(self, fresh_ml_schema):
        sa = fresh_ml_schema
        inf = ai.ActivityInference()
        assert inf.available
        assert inf.load_error is None

        db = sa.get_db_session()
        try:
            active = sa.get_active_model_version(db, ai.DEFAULT_MODEL_NAME)
        finally:
            db.close()
        assert active is not None
        assert active["version"] == "1"
        assert active["is_active"] is True
        assert active["file_path"] == ai.DEFAULT_MODEL_FILE_PATH
        assert active["labels_path"] == ai.DEFAULT_MODEL_LABELS_PATH

    def test_reload_active_model_troca_o_modelo_apos_ativar_outra_versao(self, fresh_ml_schema):
        sa = fresh_ml_schema
        inf = ai.ActivityInference()  # auto-regista e ativa a versão "1"
        assert inf.available
        modelo_antes = inf._model
        classes_antes = inf._classes

        # 2ª "versão" a apontar para os MESMOS ficheiros físicos já
        # existentes -- confirma o MECANISMO de troca funciona, sem
        # precisar de um segundo modelo .joblib real treinado.
        db = sa.get_db_session()
        try:
            sa.register_model_version(
                db, ai.DEFAULT_MODEL_NAME, version="2",
                file_path=ai.DEFAULT_MODEL_FILE_PATH,
                labels_path=ai.DEFAULT_MODEL_LABELS_PATH,
                notes="versao de teste -- mesmo ficheiro fisico da versao 1",
            )
            sa.activate_model_version(db, ai.DEFAULT_MODEL_NAME, "2")
        finally:
            db.close()

        ok = inf.reload_active_model()

        assert ok is True
        assert inf.load_error is None
        # joblib.load()/json.load() produzem objetos NOVOS a cada
        # carregamento -- prova que o modelo foi mesmo recarregado (troca
        # de facto), não só que a chamada devolveu True sem fazer nada.
        assert inf._model is not modelo_antes
        assert inf._classes is not classes_antes
        assert inf._classes == classes_antes  # mesmo conteúdo (mesmo ficheiro físico)

    def test_reload_active_model_com_caminho_invalido_preserva_modelo_anterior(self, fresh_ml_schema):
        sa = fresh_ml_schema
        inf = ai.ActivityInference()  # auto-regista e carrega a versão "1" (boa)
        assert inf.available
        modelo_bom = inf._model
        classes_boas = inf._classes

        db = sa.get_db_session()
        try:
            sa.register_model_version(
                db, ai.DEFAULT_MODEL_NAME, version="2-invalida",
                file_path="models/nao_existe_de_todo.joblib",
                labels_path="models/nao_existe_de_todo_labels.json",
                activate=True,
            )
        finally:
            db.close()

        ok = inf.reload_active_model()

        assert ok is False
        assert inf.load_error is not None
        # O modelo BOM anterior continua lá, intacto -- um rollback para
        # um caminho inválido nunca pode apagar um modelo que funcionava.
        assert inf._model is modelo_bom
        assert inf._classes is classes_boas
        assert inf.available  # continua a classificar com o modelo antigo
