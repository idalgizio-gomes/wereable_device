"""Extração de features estatísticas por janela, a partir do sinal bruto
simulado de IMU (acelerómetro + giroscópio) e PPG (frequência cardíaca).

As features seguem a abordagem clássica de HAR (Human Activity Recognition)
sobre acelerómetros wearable — estatísticas no domínio do tempo por eixo,
mais medidas de correlação entre eixos e de periodicidade — usada como
entrada de um classificador de árvores (XGBoost no artigo de referência,
ver ml/README.md). Não há extração de features no domínio da frequência
(FFT) nesta primeira iteração; fica registado como possível melhoria futura
(o CMSIS-DSP mencionado no PROJECT_STATUS.md serviria para isso caso o
classificador venha a ser embarcado).
"""

import numpy as np

ACCEL_AXES = ("accel_x", "accel_y", "accel_z")
GYRO_AXES = ("gyro_x", "gyro_y", "gyro_z")


def _zero_crossing_rate(signal):
    centered = signal - signal.mean()
    signs = np.sign(centered)
    signs[signs == 0] = 1
    crossings = np.count_nonzero(np.diff(signs) != 0)
    return crossings / len(signal)


def extract_features(window):
    """window: dict com arrays 1D (mesmo comprimento) para cada eixo em
    ACCEL_AXES + GYRO_AXES, mais 'hr' (array curto de leituras de FC dentro
    da janela). Devolve um dict de features escalares.
    """
    feats = {}

    for axis in ACCEL_AXES + GYRO_AXES:
        sig = window[axis]
        feats[f"{axis}_mean"] = float(np.mean(sig))
        feats[f"{axis}_std"] = float(np.std(sig))
        feats[f"{axis}_min"] = float(np.min(sig))
        feats[f"{axis}_max"] = float(np.max(sig))
        feats[f"{axis}_rms"] = float(np.sqrt(np.mean(np.square(sig))))

    # Signal Magnitude Area do acelerómetro — distingue bem repouso de
    # movimento, independentemente da orientação do pulso.
    sma = np.mean(
        np.abs(window["accel_x"]) + np.abs(window["accel_y"]) + np.abs(window["accel_z"])
    )
    feats["accel_sma"] = float(sma)

    # Correlação entre eixos do acelerómetro — movimentos coordenados
    # (ex.: passos) tendem a correlacionar eixos de forma diferente de
    # movimentos finos (ex.: higiene) ou de repouso (ruído descorrelacionado).
    for a, b in (("accel_x", "accel_y"), ("accel_x", "accel_z"), ("accel_y", "accel_z")):
        if np.std(window[a]) > 1e-9 and np.std(window[b]) > 1e-9:
            corr = float(np.corrcoef(window[a], window[b])[0, 1])
        else:
            corr = 0.0
        feats[f"corr_{a}_{b}"] = corr

    # Taxa de cruzamentos por zero no eixo X do acelerómetro — proxy simples
    # de periodicidade/frequência de movimento (parado vs. gesto repetitivo
    # vs. marcha), sem exigir FFT.
    feats["accel_x_zcr"] = float(_zero_crossing_rate(window["accel_x"]))

    hr = window["hr"]
    feats["hr_mean"] = float(np.mean(hr))
    feats["hr_std"] = float(np.std(hr))

    return feats


FEATURE_NAMES = None  # preenchido em runtime a partir das chaves do 1º dict
