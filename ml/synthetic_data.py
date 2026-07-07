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

# Duração (minutos) de cada sessão gerada por sujeito sintético.
# Alterado em 2026-07-07 (rotina cloud, item 4 do roteiro de ml/README.md
# "Próximos passos") de uma amostra COMPRIMIDA (240+90=330 min) para um
# dia completo de 24h (960+480=1440 min: 16h "diurnas" + 8h "noturnas") —
# a primeira iteração comprimia a sessão de propósito para manter o
# dataset pequeno/rápido, mas isso também tornava as proporções de tempo
# por classe (e o nº de janelas por bloco) pouco comparáveis a um dia real.
DAY_SESSION_MINUTES = 960  # sessão "diurna": tudo menos Dormir (16h)
NIGHT_SESSION_MINUTES = 480  # sessão "noturna": sobretudo Dormir (8h)

# Parâmetros de sinal por classe, cada um agora um INTERVALO (min, max) em vez
# de uma constante — alterado em 2026-07-07 (rotina cloud, item 4 do roteiro)
# para introduzir variância dentro da classe e, deliberadamente, SOBREPOSIÇÃO
# entre classes vizinhas em intensidade/frequência de movimento (ex.:
# "Atividade" e "Higiene" partilham uma faixa de amplitude/frequência —
# escovar os dentes vigorosamente pode ter energia de movimento semelhante a
# andar devagar). Isto é uma correção deliberada à primeira iteração, cujas
# classes eram artificialmente bem separáveis (accuracy=1.000 no XGBoost,
# sinal de dataset fácil demais, não de classificador excelente — ver
# ml/README.md). Cada valor é amostrado por JANELA (não por sujeito), dentro
# do intervalo indicado; amplitudes em "g" (aceleração) e "°/s" (giroscópio),
# aproximadas/plausíveis, não medidas em hardware real (ver limitação
# honesta já documentada — sem HAR embarcado, sem dados reais rotulados).
CLASS_PARAMS = {
    "Dormir": dict(
        accel_noise_std=(0.015, 0.030), accel_amp=(0.0, 0.0), accel_freq_hz=(0.0, 0.0),
        gyro_noise_std=(0.6, 1.4), gyro_amp=(0.0, 0.0),
        hr_range=(45, 62), hr_std=2.0,
    ),
    "Descanso": dict(
        accel_noise_std=(0.025, 0.050), accel_amp=(0.01, 0.08), accel_freq_hz=(0.05, 0.25),
        gyro_noise_std=(1.5, 3.5), gyro_amp=(1.0, 5.0),
        hr_range=(58, 78), hr_std=3.0,
    ),
    # Overlap deliberado com "Higiene" em amplitude (0.35-0.45g) e frequência
    # (1.3-2.3 Hz) — marcha lenta e tarefas de higiene mais vigorosas geram
    # energia de movimento semelhante no pulso.
    "Atividade": dict(
        accel_noise_std=(0.09, 0.16), accel_amp=(0.35, 0.70), accel_freq_hz=(1.3, 2.3),
        gyro_noise_std=(6.0, 11.0), gyro_amp=(28.0, 50.0),
        hr_range=(85, 155), hr_std=8.0,
    ),
    # Overlap deliberado com "Descanso" na ponta baixa (até 0.08g) — preparar
    # uma refeição sentado pode ser quase tão parado como descansar.
    "Alimentação": dict(
        accel_noise_std=(0.04, 0.09), accel_amp=(0.08, 0.30), accel_freq_hz=(0.3, 0.7),
        gyro_noise_std=(3.0, 7.0), gyro_amp=(8.0, 24.0),
        hr_range=(62, 88), hr_std=4.0,
    ),
    # Overlap deliberado com "Atividade" (ver acima).
    "Higiene": dict(
        accel_noise_std=(0.06, 0.13), accel_amp=(0.20, 0.45), accel_freq_hz=(1.0, 2.6),
        gyro_noise_std=(5.0, 10.0), gyro_amp=(18.0, 35.0),
        hr_range=(66, 98), hr_std=5.0,
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
    """Constrói uma sequência de (classe, duração_min) cuja soma se aproxima
    de total_minutes (alvo/média, não um orçamento exato a cumprir).

    Alterado em 2026-07-07 (rotina cloud, item 4 do roteiro de
    ml/README.md): a versão anterior cortava sempre o ÚLTIMO bloco
    (`dur = min(dur, remaining)`) para a sessão somar exatamente
    total_minutes — isso criava um bloco final artificialmente curto e sem
    relação com a duração real amostrada da classe, que o detetor de
    duração do passo 3 (`duration_detector.py`) depois sinalizava como
    anomalia (achado documentado no ml/README.md: 100% dos falsos positivos
    do detetor vinham exatamente deste bloco cortado pelo gerador, não de
    uma anomalia real). Corrigido: cada bloco usa sempre a sua duração
    amostrada por inteiro; a sessão termina assim que a duração acumulada
    atinge total_minutes, podendo ultrapassá-lo ligeiramente (no máximo
    `hi-1` minutos do último bloco) — a mesma variabilidade natural que
    existiria numa rotina real, em vez de um corte artificial do gerador.
    """
    classes = list(class_weights.keys())
    weights = np.array(list(class_weights.values()), dtype=float)
    weights /= weights.sum()

    segments = []
    elapsed = 0
    while elapsed < total_minutes:
        cls = rng.choice(classes, p=weights)
        lo, hi = block_minutes[cls]
        dur = int(rng.integers(lo, hi + 1))
        segments.append((cls, dur))
        elapsed += dur
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
    """Gera o sinal bruto de uma janela. Desde 2026-07-07 (rotina cloud,
    item 4 do roteiro), amplitude/frequência/ruído deixaram de ser
    constantes por classe e passaram a ser amostradas por JANELA dentro do
    intervalo de `CLASS_PARAMS[cls]` — introduz variância dentro da classe
    e sobreposição real entre classes vizinhas (ver comentário em
    CLASS_PARAMS), em vez de cada classe gerar sempre o mesmo sinal
    "canónico" fácil de separar.
    """
    p = CLASS_PARAMS[cls]
    t = np.arange(WINDOW_SAMPLES) / FS_HZ

    accel_noise_std = rng.uniform(*p["accel_noise_std"])
    accel_amp = rng.uniform(*p["accel_amp"]) * max(jitter["amp_scale"], 0.5)
    freq_lo, freq_hi = p["accel_freq_hz"]
    accel_freq_hz = rng.uniform(freq_lo, freq_hi) if freq_hi > 0 else 0.0
    gyro_noise_std = rng.uniform(*p["gyro_noise_std"])
    gyro_amp = rng.uniform(*p["gyro_amp"])

    window = {}
    # 3 eixos do acelerómetro com fase/orientação ligeiramente diferentes
    # entre eixos, para não serem sinais idênticos.
    for i, axis in enumerate(ACCEL_AXES):
        phase = i * (np.pi / 3)
        base = accel_amp * np.sin(2 * np.pi * accel_freq_hz * t + phase) if accel_freq_hz > 0 else 0.0
        gravity = 1.0 if axis == "accel_z" else 0.0  # eixo Z inclui componente de gravidade em repouso
        noise = rng.normal(0, accel_noise_std, WINDOW_SAMPLES)
        window[axis] = gravity + base + noise

    for i, axis in enumerate(GYRO_AXES):
        phase = i * (np.pi / 4)
        base = gyro_amp * np.sin(2 * np.pi * accel_freq_hz * t + phase) if accel_freq_hz > 0 else 0.0
        noise = rng.normal(0, gyro_noise_std, WINDOW_SAMPLES)
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
            "dados reais de nenhum utente. Sessões 'dia'/'noite' cobrem um dia "
            "completo de 24h (16h+8h, desde 2026-07-07) com sobreposição "
            "deliberada entre classes vizinhas — ver docstring do módulo."
        ),
    }
    with open("data/synthetic_routine_dataset.meta.json", "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print(f"Gerado {len(df)} janelas em {out_csv}")
    print(df["label"].value_counts())


if __name__ == "__main__":
    main()
