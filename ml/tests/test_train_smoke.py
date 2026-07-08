"""Teste de fumo dos scripts de treino do classificador de atividades
(passo 1 do pipeline, ver ml/README.md) — item explicitamente registado
como "próximo passo possível" em `ml/README.md` ("Testes automáticos + CI")
desde 2026-07-07: confirmar que os scripts treinam sem erro, não validar
métricas (isso já é feito manualmente e documentado em
`reports/activity_classifier*_metrics.json`).

Deliberadamente NÃO cobre `train_lstm_autoencoder.py` nem
`measure_rf_footprint.py` — precisam de TensorFlow/emlearn, o mesmo custo
(minutos de instalação) já registado como motivo para os deixar de fora do
CI leve. XGBoost + scikit-learn são bem mais leves e o próprio
`train()` de cada script é uma função pura (não escreve ficheiros) — dá
para testar sem tocar em `models/`/`reports/` nem sujar os artefactos já
commitados.

Usa `generate_dataset(n_subjects=2, seed=7)` (não os 8 sujeitos/seed=42 da
`main()` de produção) — dataset minúsculo só para exercitar o caminho de
código completo (dados → split por sujeito → treino → métricas) depressa
(~10s no total, ver medição desta rotina), não para reproduzir os números
já documentados no README.
"""
import numpy as np
import pytest

from synthetic_data import generate_dataset
from train_activity_classifier import NON_FEATURE_COLS
from train_activity_classifier import train as train_xgb
from train_activity_classifier_rf import train as train_rf


@pytest.fixture(scope="module")
def tiny_dataset():
    df = generate_dataset(n_subjects=2, seed=7)
    feature_cols = [c for c in df.columns if c not in NON_FEATURE_COLS]
    return df, feature_cols


def _assert_sane_metrics(metrics, n_classes):
    assert 0.0 <= metrics["accuracy"] <= 1.0
    assert not np.isnan(metrics["accuracy"])
    assert len(metrics["class_order"]) == n_classes
    assert len(metrics["confusion_matrix"]) == n_classes
    assert metrics["n_train_windows"] > 0
    assert metrics["n_test_windows"] > 0
    # sujeitos de treino/teste não podem ter sobreposição (split por sujeito,
    # não por janela — ver docstring de train_activity_classifier.py)
    assert not (set(metrics["train_subject_ids"]) & set(metrics["test_subject_ids"]))


def test_train_activity_classifier_xgb_runs_end_to_end(tiny_dataset):
    df, feature_cols = tiny_dataset
    model, encoder, fc, metrics = train_xgb(df, feature_cols)

    assert model is not None
    assert fc == feature_cols
    _assert_sane_metrics(metrics, n_classes=len(encoder.classes_))


def test_train_activity_classifier_rf_runs_end_to_end(tiny_dataset):
    df, feature_cols = tiny_dataset
    model, encoder, fc, metrics = train_rf(df, feature_cols)

    assert model is not None
    assert fc == feature_cols
    _assert_sane_metrics(metrics, n_classes=len(encoder.classes_))
    assert metrics["n_estimators"] > 0
    assert metrics["max_depth"] > 0
