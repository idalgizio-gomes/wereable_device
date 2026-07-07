"""Testes de `features.py` (extração de features estatísticas por janela).

Deliberadamente sem `pytest` a treinar nada — só exercita funções puras,
com sinais sintéticos pequenos e propriedades conhecidas de antemão, para
detetar regressões sem depender de TensorFlow/XGBoost/emlearn (pesados,
fora do âmbito deste CI leve — ver `ml/README.md`, "Próximos passos").
"""
import numpy as np
import pytest

from features import ACCEL_AXES, GYRO_AXES, _zero_crossing_rate, extract_features


def _make_window(accel_x, accel_y=None, accel_z=None, gyro=0.0, hr=(70.0, 71.0)):
    n = len(accel_x)
    accel_x = np.asarray(accel_x, dtype=float)
    accel_y = np.full(n, 0.0) if accel_y is None else np.asarray(accel_y, dtype=float)
    accel_z = np.full(n, 1.0) if accel_z is None else np.asarray(accel_z, dtype=float)
    return {
        "accel_x": accel_x,
        "accel_y": accel_y,
        "accel_z": accel_z,
        "gyro_x": np.full(n, gyro),
        "gyro_y": np.full(n, gyro),
        "gyro_z": np.full(n, gyro),
        "hr": np.asarray(hr, dtype=float),
    }


def test_zero_crossing_rate_of_constant_signal_is_zero():
    # Um sinal constante nunca cruza a própria média — 0 cruzamentos.
    assert _zero_crossing_rate(np.full(50, 3.0)) == 0.0


def test_zero_crossing_rate_of_alternating_signal_is_one():
    # +1/-1 alternado cruza a média (0) em TODAS as transições consecutivas —
    # taxa de cruzamento máxima (1.0), o caso que expõe um off-by-one na
    # normalização (ver histórico do off-by-one já corrigido no docstring
    # da função, PROJECT_STATUS.md 2026-07-07).
    signal = np.array([1.0, -1.0] * 25)
    assert _zero_crossing_rate(signal) == pytest.approx(1.0)


def test_zero_crossing_rate_denominator_is_n_minus_one():
    # len(signal)-1 transições possíveis, não len(signal) — uma única
    # transição num sinal de 4 amostras deve dar 1/3, não 1/4.
    signal = np.array([1.0, 1.0, -1.0, -1.0])
    assert _zero_crossing_rate(signal) == pytest.approx(1 / 3)


def test_extract_features_returns_expected_keys():
    window = _make_window(accel_x=np.linspace(-1, 1, 20))
    feats = extract_features(window)

    for axis in ACCEL_AXES + GYRO_AXES:
        for suffix in ("mean", "std", "min", "max", "rms"):
            assert f"{axis}_{suffix}" in feats
    assert "accel_sma" in feats
    assert "accel_x_zcr" in feats
    assert "hr_mean" in feats
    assert "hr_std" in feats
    for a, b in (("accel_x", "accel_y"), ("accel_x", "accel_z"), ("accel_y", "accel_z")):
        assert f"corr_{a}_{b}" in feats


def test_extract_features_stationary_signal_has_zero_std_and_zcr():
    window = _make_window(accel_x=np.full(30, 0.5), accel_y=np.full(30, 0.2), accel_z=np.full(30, 1.0))
    feats = extract_features(window)

    assert feats["accel_x_std"] == pytest.approx(0.0)
    assert feats["accel_x_mean"] == pytest.approx(0.5)
    assert feats["accel_x_zcr"] == pytest.approx(0.0)


def test_extract_features_correlation_falls_back_to_zero_for_constant_axis():
    # Correlação de Pearson não está definida quando um dos eixos não varia
    # (desvio-padrão ~0) — extract_features() deve devolver 0.0 em vez de
    # propagar o NaN que np.corrcoef produziria.
    window = _make_window(accel_x=np.linspace(-1, 1, 20), accel_y=np.full(20, 0.5))
    feats = extract_features(window)

    assert feats["corr_accel_x_accel_y"] == 0.0
    assert not np.isnan(feats["corr_accel_x_accel_y"])


def test_extract_features_hr_stats_match_input():
    window = _make_window(accel_x=np.zeros(10), hr=(60.0, 80.0))
    feats = extract_features(window)

    assert feats["hr_mean"] == pytest.approx(70.0)
    assert feats["hr_std"] == pytest.approx(10.0)
