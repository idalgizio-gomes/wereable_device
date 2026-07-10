"""Testes de `combined_pipeline_report.py` — cobre só `predicted_blocks_from_rows`
(agrupamento de janelas em blocos a partir da classe PREVISTA), a única
função pura/determinística do módulo, sem RNG nem modelos treinados. O
resto do módulo (`run_evaluation`) exige XGBoost + TensorFlow instalados e
os modelos já treinados em models/ — já corrido manualmente e coberto em
`reports/combined_pipeline_metrics.json`, mesmo padrão já usado para
`duration_detector.run_evaluation()` em test_duration_detector.py."""
from combined_pipeline_report import predicted_blocks_from_rows


def _row(session, is_anomalous_window, idx):
    return dict(session=session, is_anomalous_window=is_anomalous_window, _idx=idx)


def test_consecutive_same_predicted_class_same_session_merge_into_one_block():
    rows = [_row("dia", False, 0), _row("dia", False, 1), _row("dia", False, 2)]
    predicted = ["Descanso", "Descanso", "Descanso"]

    blocks = predicted_blocks_from_rows(rows, predicted)

    assert len(blocks) == 1
    assert blocks[0]["n_windows"] == 3
    assert blocks[0]["row_idxs"] == [0, 1, 2]
    assert blocks[0]["duration_min"] == 0.5  # 3 janelas de 10s = 30s = 0.5 min


def test_predicted_class_change_splits_into_separate_blocks():
    rows = [_row("dia", False, 0), _row("dia", False, 1), _row("dia", False, 2)]
    predicted = ["Descanso", "Descanso", "Atividade"]

    blocks = predicted_blocks_from_rows(rows, predicted)

    assert len(blocks) == 2
    assert [b["cls"] for b in blocks] == ["Descanso", "Atividade"]
    assert [b["n_windows"] for b in blocks] == [2, 1]


def test_session_change_splits_block_even_if_predicted_class_repeats():
    rows = [_row("noite", False, 0), _row("dia", False, 1)]
    predicted = ["Descanso", "Descanso"]

    blocks = predicted_blocks_from_rows(rows, predicted)

    assert len(blocks) == 2
    assert [b["session"] for b in blocks] == ["noite", "dia"]


def test_block_is_true_anomaly_if_any_of_its_windows_is():
    rows = [_row("dia", False, 0), _row("dia", True, 1), _row("dia", False, 2)]
    predicted = ["Higiene", "Higiene", "Higiene"]

    blocks = predicted_blocks_from_rows(rows, predicted)

    assert len(blocks) == 1
    assert blocks[0]["is_true_anomaly"] is True


def test_misclassified_single_window_fragments_a_normal_block_in_three():
    # Simula o efeito de fragmentacao medido em run_evaluation(): uma unica
    # janela mal classificada a meio de um bloco continuo (10 janelas da
    # mesma classe) parte-o em tres blocos previstos mais curtos.
    rows = [_row("dia", False, i) for i in range(10)]
    predicted = ["Descanso"] * 4 + ["Atividade"] + ["Descanso"] * 5

    blocks = predicted_blocks_from_rows(rows, predicted)

    assert len(blocks) == 3
    assert [b["cls"] for b in blocks] == ["Descanso", "Atividade", "Descanso"]
    assert [b["n_windows"] for b in blocks] == [4, 1, 5]
