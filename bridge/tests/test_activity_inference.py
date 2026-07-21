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
