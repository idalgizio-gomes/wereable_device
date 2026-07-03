"""Gerador de dados sintéticos de rotina diária para o classificador de
atividades (passo 1 do pipeline — ver ml/README.md).

Não existem ainda dados reais rotulados de utentes (o classificador HAR não
está embarcado no firmware — ver PROJECT_STATUS.md). Este módulo gera sinal
sintético de acelerómetro+giroscópio (a 52 Hz, igual à taxa real do LSM6DS3
no wearable) e de frequência cardíaca (PPG), com características plausíveis
por categoria de atividade, para servir de substituto temporário até haver
dados reais — a mesma abordagem do artigo científico de referência
("Wearable-Derived Synthetic Daily Routines").

Limitação assumida e documentada (não escondida): os parâmetros de sinal por
classe abaixo são a nossa própria modelação, inspirada em padrões típicos da
literatura de HAR sobre acelerómetro de pulso — não são os parâmetros exatos
do artigo (não publicados/disponíveis), nem dados clínicos reais. Servem para
validar a pipeline de ponta a ponta (dados → features → XGBoost → avaliação),
não para tirar conclusões clínicas.

As 5 classes usadas são exatamente as já apresentadas no dashboard (chips em
"Análise por atividade", ver web/dashboard/index.html) — Dormir, Descanso,
Atividade, Alimentação, Higiene — para que um futuro classificador real possa
alimentar diretamente essa UI sem remapear categorias.
"""

import json

import numpy as np
import pandas as pd

from features import ACCEL_AXES, GYRO_AXES, extract_features

FS_HZ = 52  # taxa de amostragem real do IMU (LSM6DS3), ver PROJECT_STATUS.md
WINDOW_SECONDS = 10  # janela usada para extração de features (HAR típico: 2-10s)
WINDOW_SAMPLES = FS_HZ * WINDOW_SECONDS

# Duração (minutos) de cada "sessão comprimida" gerada por sujeito sintético.
# Uma sessão NÃO representa um dia de 24h completo — é uma amostra
# comprimida, com proporção de classes plausível, para manter o dataset
# desta primeira iteração pequeno e rápido de gerar/treinar. Gerar dias
# completos de 24h por sujeito fica registado como melhoria futura (ver
# ml/README.md).
DAY_SESSION_MINUTES = 240  # sessão "diurna": tudo menos Dormir
NIGHT_SESSION_MINUTES = 90  # sessão "noturna": sobretudo Dormir

# Parâmetros de sinal por classe. Amplitudes em "g" (aceleração) e "°/s"
# (giroscópio), aproximados/plausíveis, não medidos em hardware real.
CLASS_PARAMS = {
    "Dormir": dict(
        accel_noise_std=0.02, accel_amp=0.0, accel_freq_hz=0.0,
        gyro_noise_std=1.0, gyro_amp=0.0,
        hr_range=(45, 60), hr_std=2.0,
    ),
    "Descanso": dict(
        accel_noise_std=0.035, accel_amp=0.03, accel_freq_hz=0.15,
        gyro_noise_std=2.5, gyro_amp=3.0,
        hr_range=(60, 75), hr_std=3.0,
    ),
    "Atividade": dict(
        accel_noise_std=0.12, accel_amp=0.55, accel_freq_hz=1.8,  # cadência de marcha
        gyro_noise_std=8.0, gyro_amp=40.0,
        hr_range=(90, 150), hr_std=8.0,
    ),
    "Alimentação": dict(
        accel_noise_std=0.06, accel_amp=0.22, accel_freq_hz=0.5,  # gesto mão-boca
        gyro_noise_std=5.0, gyro_amp=18.0,
        hr_range=(65, 85), hr_std=4.0,
    ),
    "Higiene": dict(
        accel_noise_std=0.09, accel_amp=0.32, accel_freq_hz=2.4,  # ex: escovar dentes
        gyro_noise_std=7.0, gyro_amp=25.0,
        hr_range=(70, 95), hr_std=5.0,
    ),
}

# Pesos de amostragem de duração de blocos (minutos) por classe/sessão —
# refletem uma rotina plausível (ex: refeições curtas e pontuais, descanso
# em blocos maiores), não um modelo clínico validado.
DAY_BLOCK_MINUTES = {
    "Descanso": (15, 45),
    "Atividade": (5, 25),
    "Alimentação": (10, 30),
    "Higiene": (5, 15),
}
DAY_CLASS_WEIGHTS = {"Descanso": 0.45, "Atividade": 0.25, "Alimentação": 0.15, "Higiene": 0.15}

NIGHT_BLOCK_MINUTES = {"Dormir": (30, 90), "Descanso": (5, 15)}
NIGHT_CLASS_WEIGHTS = {"Dormir": 0.85, "Descanso": 0.15}


def _build_segment_sequence(rng, total_minutes, block_minutes, class_weights):
    """Constrói uma sequência de (classe, duração_min) que soma total_minutes."""
    classes = list(class_weights.keys())
    weights = np.array(list(class_weights.values()), dtype=float)
    weights /= weights.sum()

    segments = []
    remaining = total_minutes
    while remaining > 0:
        cls = rng.choice(classes, p=weights)
        lo, hi = block_minutes[cls]
        dur = int(rng.integers(lo, hi + 1))
        dur = min(dur, remaining)
        if dur <= 0:
            break
        segments.append((cls, dur))
        remaining -= dur
    return segments


def _subject_jitter(rng):
    """Variação individual por sujeito sintético (amplitude/HR baseline),
    para não gerar todos os sujeitos com sinal idêntico — testbed simples
    para futura personalização por pessoa (ver PROJECT_STATUS.md, backlog
    de investigação, item 3: "modelos personalizados por pessoa").
    """
    return dict(
        amp_scale=float(rng.normal(1.0, 0.08)),
        hr_shift=float(rng.normal(0.0, 3.0)),
    )


def _generate_window_signal(rng, cls, jitter):
    p = CLASS_PARAMS[cls]
    t = np.arange(WINDOW_SAMPLES) / FS_HZ
    amp = p["accel_amp"] * max(jitter["amp_scale"], 0.5)

    window = {}
    # 3 eixos do acelerómetro com fase/orientação ligeiramente diferentes
    # entre eixos, para não serem sinais idênticos.
    for i, axis in enumerate(ACCEL_AXES):
        phase = i * (np.pi / 3)
        base = amp * np.sin(2 * np.pi * p["accel_freq_hz"] * t + phase) if p["accel_freq_hz"] > 0 else 0.0
        gravity = 1.0 if axis == "accel_z" else 0.0  # eixo Z inclui componente de gravidade em repouso
        noise = rng.normal(0, p["accel_noise_std"], WINDOW_SAMPLES)
        window[axis] = gravity + base + noise

    for i, axis in enumerate(GYRO_AXES):
        phase = i * (np.pi / 4)
        base = p["gyro_amp"] * np.sin(2 * np.pi * p["accel_freq_hz"] * t + phase) if p["accel_freq_hz"] > 0 else 0.0
        noise = rng.normal(0, p["gyro_noise_std"], WINDOW_SAMPLES)
        window[axis] = base + noise

    hr_lo, hr_hi = p["hr_range"]
    hr_center = rng.uniform(hr_lo, hr_hi) + jitter["hr_shift"]
    n_hr_samples = max(1, WINDOW_SECONDS // 4)  # PPG amostrado com menor frequência que o IMU
    window["hr"] = np.clip(rng.normal(hr_center, p["hr_std"], n_hr_samples), 35, 200)

    return window


def generate_dataset(n_subjects=8, seed=42):
    rng = np.random.default_rng(seed)
    rows = []

    for subject_id in range(n_subjects):
        jitter = _subject_jitter(rng)

        day_segments = _build_segment_sequence(rng, DAY_SESSION_MINUTES, DAY_BLOCK_MINUTES, DAY_CLASS_WEIGHTS)
        night_segments = _build_segment_sequence(rng, NIGHT_SESSION_MINUTES, NIGHT_BLOCK_MINUTES, NIGHT_CLASS_WEIGHTS)

        for session_name, segments in (("dia", day_segments), ("noite", night_segments)):
            elapsed_min = 0
            for cls, dur_min in segments:
                n_windows = max(1, (dur_min * 60) // WINDOW_SECONDS)
                for _ in range(int(n_windows)):
                    window = _generate_window_signal(rng, cls, jitter)
                    feats = extract_features(window)
                    feats["label"] = cls
                    feats["subject_id"] = subject_id
                    feats["session"] = session_name
                    feats["elapsed_min"] = elapsed_min
                    rows.append(feats)
                elapsed_min += dur_min

    df = pd.DataFrame(rows)
    return df


def main():
    df = generate_dataset(n_subjects=8, seed=42)

    out_csv = "data/synthetic_routine_dataset.csv"
    df.to_csv(out_csv, index=False)

    meta = {
        "n_rows": len(df),
        "n_subjects": int(df["subject_id"].nunique()),
        "classes": sorted(df["label"].unique().tolist()),
        "class_counts": df["label"].value_counts().to_dict(),
        "fs_hz": FS_HZ,
        "window_seconds": WINDOW_SECONDS,
        "note": (
            "Dataset 100% sintético, gerado por ml/synthetic_data.py. Não contém "
            "dados reais de nenhum utente. Sessões 'dia'/'noite' são comprimidas "
            "(nao 24h reais) — ver docstring do módulo."
        ),
    }
    with open("data/synthetic_routine_dataset.meta.json", "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print(f"Gerado {len(df)} janelas em {out_csv}")
    print(df["label"].value_counts())


if __name__ == "__main__":
    main()
