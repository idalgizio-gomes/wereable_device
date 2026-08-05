"""Testes de `duration_detector.py` (passo 3 do pipeline, ver ml/README.md).

Cobre só a lógica determinística (`evaluate_block`/`evaluate_subject`) com
segmentos escritos à mão — sem chamar `generate_subject_segments` (que usa
um RNG) nem `run_evaluation()` (script de avaliação completo, já coberto
manualmente em `reports/duration_detector_metrics.json`). Sem TensorFlow/
XGBoost/emlearn — só numpy/pandas (herdados de `synthetic_data.py`).
"""
from duration_detector import (
    REASON_OUT_OF_BOUNDS,
    REASON_UNEXPECTED_CLASS,
    SESSION_BLOCK_MINUTES,
    evaluate_block,
    evaluate_subject,
    explain_block,
)


def test_evaluate_block_within_limits_is_not_anomaly():
    d_min, d_max = SESSION_BLOCK_MINUTES["dia"]["Higiene"]
    midpoint = (d_min + d_max) / 2
    is_anomaly, reason = evaluate_block("dia", "Higiene", midpoint)
    assert is_anomaly is False
    assert reason is None


def test_evaluate_block_below_minimum_is_flagged():
    d_min, _d_max = SESSION_BLOCK_MINUTES["dia"]["Higiene"]
    is_anomaly, reason = evaluate_block("dia", "Higiene", d_min - 1)
    assert is_anomaly is True
    assert reason == REASON_OUT_OF_BOUNDS


def test_evaluate_block_above_maximum_is_flagged():
    _d_min, d_max = SESSION_BLOCK_MINUTES["dia"]["Higiene"]
    is_anomaly, reason = evaluate_block("dia", "Higiene", d_max + 1)
    assert is_anomaly is True
    assert reason == REASON_OUT_OF_BOUNDS


def test_evaluate_block_at_exact_boundaries_is_not_anomaly():
    # Limites são inclusivos ([d_min, d_max], não intervalo aberto).
    d_min, d_max = SESSION_BLOCK_MINUTES["noite"]["Dormir"]
    assert evaluate_block("noite", "Dormir", d_min) == (False, None)
    assert evaluate_block("noite", "Dormir", d_max) == (False, None)


def test_evaluate_block_unexpected_class_for_session_is_flagged():
    # "Atividade" não consta de SESSION_BLOCK_MINUTES["noite"] — mesmo com
    # uma duração perfeitamente plausível, deve ser sinalizada como classe
    # inesperada nessa sessão (ex.: agitação/deambulação noturna).
    is_anomaly, reason = evaluate_block("noite", "Atividade", 10)
    assert is_anomaly is True
    assert reason == REASON_UNEXPECTED_CLASS


def test_evaluate_subject_marks_true_anomaly_and_flags_it():
    night_segments = [("Dormir", 60), ("Descanso", 10)]
    day_segments = [("Descanso", 20), ("Atividade", 999)]  # duração absurda
    anomaly_marker = ("dia", 1)  # (sessão, índice do bloco anómalo)

    rows = evaluate_subject(night_segments, day_segments, anomaly_marker)

    anomalous_rows = [r for r in rows if r["is_true_anomaly"]]
    assert len(anomalous_rows) == 1
    assert anomalous_rows[0]["session"] == "dia"
    assert anomalous_rows[0]["cls"] == "Atividade"
    assert anomalous_rows[0]["flagged"] is True


def test_evaluate_subject_normal_blocks_are_not_flagged():
    d_min, d_max = SESSION_BLOCK_MINUTES["dia"]["Descanso"]
    night_segments = [("Dormir", 45)]
    day_segments = [("Descanso", (d_min + d_max) / 2)]

    rows = evaluate_subject(night_segments, day_segments, anomaly_marker=None)

    assert all(r["is_true_anomaly"] is False for r in rows)
    assert all(r["flagged"] is False for r in rows)


def test_evaluate_subject_marks_last_block_of_each_session():
    night_segments = [("Dormir", 45), ("Descanso", 10)]
    day_segments = [("Descanso", 20)]

    rows = evaluate_subject(night_segments, day_segments, anomaly_marker=None)

    night_rows = [r for r in rows if r["session"] == "noite"]
    day_rows = [r for r in rows if r["session"] == "dia"]
    assert [r["is_last_of_session"] for r in night_rows] == [False, True]
    assert [r["is_last_of_session"] for r in day_rows] == [True]


# ---------------------------------------------------------------------------
# explain_block — "explicação de alerta" (2026-08-05)
# ---------------------------------------------------------------------------

def test_explain_block_not_anomaly_mentions_actual_duration():
    d_min, d_max = SESSION_BLOCK_MINUTES["dia"]["Higiene"]
    midpoint = (d_min + d_max) / 2
    text = explain_block("dia", "Higiene", midpoint, is_anomaly=False, reason=None)
    assert "Higiene" in text
    assert f"{midpoint:.0f}" in text
    assert "dentro do" in text


def test_explain_block_out_of_bounds_mentions_limits():
    d_min, d_max = SESSION_BLOCK_MINUTES["dia"]["Higiene"]
    duration = d_max + 30
    text = explain_block("dia", "Higiene", duration, is_anomaly=True, reason=REASON_OUT_OF_BOUNDS)
    assert f"{duration:.0f}" in text
    assert f"{d_min:.0f}" in text
    assert f"{d_max:.0f}" in text


def test_explain_block_unexpected_class_lists_expected_classes():
    expected_classes = sorted(SESSION_BLOCK_MINUTES["noite"].keys())
    text = explain_block("noite", "Atividade", 10, is_anomaly=True, reason=REASON_UNEXPECTED_CLASS)
    assert "Atividade" in text
    for cls in expected_classes:
        assert cls in text


def test_explain_block_matches_evaluate_block_verdict():
    """Integração: o texto de explain_block tem de refletir o mesmo
    veredito que evaluate_block já produziu, sem recalcular a lógica de
    decisão (só a explica)."""
    d_min, _d_max = SESSION_BLOCK_MINUTES["dia"]["Higiene"]
    duration = d_min - 1
    is_anomaly, reason = evaluate_block("dia", "Higiene", duration)
    text = explain_block("dia", "Higiene", duration, is_anomaly, reason)
    assert is_anomaly is True
    assert "fora do" in text
