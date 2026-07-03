"""Gerador de sequências diárias sintéticas, com anomalias injetadas, para
treinar/avaliar o LSTM Autoencoder (passo 2 do pipeline — ver ml/README.md).

Diferença face a `synthetic_data.py` (passo 1, classificador de atividades):
aquele gera janelas de 10s em qualquer ordem/mistura (adequado para um
classificador que vê uma janela de cada vez); este módulo gera, por
sujeito sintético, uma SEQUÊNCIA ordenada no tempo (noite seguida de dia,
como um ciclo diário simplificado) — o LSTM Autoencoder precisa da ordem
temporal para aprender o padrão normal de transições entre atividades.

Reutiliza deliberadamente as mesmas funções de geração de sinal/features de
`synthetic_data.py` (mesmos parâmetros por classe, mesmo jitter por sujeito)
para os dois passos do pipeline partilharem a mesma noção de "sinal
plausível" — não duplicado aqui.

ANOMALIAS INJETADAS (mesma ideia já usada na simulação do dashboard,
`web/dashboard/index.html::buildRoutine(seed, anomalous)`, agora aplicada
ao sinal/features em vez de só à timeline visual):
  - "duracao_prolongada": um bloco de Higiene fica 3-5x mais longo que o
    normal (ex.: duche demasiado longo — possível sinal de confusão/queda
    na casa de banho).
  - "substituicao_contextual": um bloco no meio da sessão "noite" (que
    devia ser sobretudo Dormir/Descanso) é substituído por Atividade —
    possível sinal de agitação/deambulação noturna ("sundowning").
  - "truncamento": um bloco de Alimentação é cortado a meio (curto demais)
    — possível sinal de refeição interrompida/incompleta.

Continua 100% sintético — nenhuma anomalia aqui representa um evento real
observado; serve para validar a pipeline de deteção de ponta a ponta antes
de existirem dados reais (mesma limitação já documentada para o passo 1).
"""

import numpy as np

from features import extract_features
from synthetic_data import (
    DAY_BLOCK_MINUTES,
    DAY_CLASS_WEIGHTS,
    DAY_SESSION_MINUTES,
    NIGHT_BLOCK_MINUTES,
    NIGHT_CLASS_WEIGHTS,
    NIGHT_SESSION_MINUTES,
    WINDOW_SECONDS,
    _build_segment_sequence,
    _generate_window_signal,
    _subject_jitter,
)

ANOMALY_TYPES = ("duracao_prolongada", "substituicao_contextual", "truncamento")


def _inject_anomaly(rng, night_segments, day_segments, anomaly_type):
    """Aplica UMA alteração aos segmentos gerados (ver ANOMALY_TYPES acima).
    Devolve os segmentos alterados (novas listas, não modifica in-place) e
    o índice da sessão/posição afetada, para depois sabermos que janelas
    marcar como anómalas."""
    night_segments = list(night_segments)
    day_segments = list(day_segments)

    if anomaly_type == "duracao_prolongada":
        candidates = [i for i, (cls, _dur) in enumerate(day_segments) if cls == "Higiene"]
        if not candidates:
            return night_segments, day_segments, None
        idx = int(rng.choice(candidates))
        cls, dur = day_segments[idx]
        day_segments[idx] = (cls, dur * int(rng.integers(3, 6)))
        return night_segments, day_segments, ("dia", idx)

    if anomaly_type == "substituicao_contextual":
        candidates = [i for i, (cls, _dur) in enumerate(night_segments) if cls != "Atividade"]
        if not candidates:
            return night_segments, day_segments, None
        idx = int(rng.choice(candidates))
        _cls, dur = night_segments[idx]
        night_segments[idx] = ("Atividade", dur)
        return night_segments, day_segments, ("noite", idx)

    if anomaly_type == "truncamento":
        candidates = [i for i, (cls, _dur) in enumerate(day_segments) if cls == "Alimentação"]
        if not candidates:
            return night_segments, day_segments, None
        idx = int(rng.choice(candidates))
        cls, dur = day_segments[idx]
        day_segments[idx] = (cls, max(1, dur // 3))
        return night_segments, day_segments, ("dia", idx)

    raise ValueError(f"tipo de anomalia desconhecido: {anomaly_type}")


def _segments_to_windows(rng, segments, jitter, session_name, feature_names,
                          anomaly_marker=None):
    """Gera as janelas (features + metadados) de uma lista de segmentos
    (classe, duração_min), na ordem em que aparecem. `anomaly_marker`, se
    dado, e' (session_name, idx_do_segmento) do segmento alterado por
    _inject_anomaly() — todas as janelas desse segmento ficam marcadas
    is_anomaly=True."""
    rows = []
    for seg_idx, (cls, dur_min) in enumerate(segments):
        n_windows = max(1, (dur_min * 60) // WINDOW_SECONDS)
        is_anomalous_segment = anomaly_marker == (session_name, seg_idx)
        for _ in range(int(n_windows)):
            window = _generate_window_signal(rng, cls, jitter)
            feats = extract_features(window)
            rows.append([feats[name] for name in feature_names] + [is_anomalous_segment])
    return rows


def generate_subject_sequence(rng, feature_names, inject_anomaly=None):
    """Gera a sequência completa de um sujeito sintético (noite seguida de
    dia — um ciclo diário simplificado, ver docstring do módulo).

    inject_anomaly: None (sequência normal) ou um valor de ANOMALY_TYPES.

    Devolve (feature_matrix [n_windows, n_features], anomaly_mask [n_windows]
    bool, anomaly_type ou None).
    """
    jitter = _subject_jitter(rng)
    night_segments = _build_segment_sequence(rng, NIGHT_SESSION_MINUTES, NIGHT_BLOCK_MINUTES, NIGHT_CLASS_WEIGHTS)
    day_segments = _build_segment_sequence(rng, DAY_SESSION_MINUTES, DAY_BLOCK_MINUTES, DAY_CLASS_WEIGHTS)

    anomaly_marker = None
    if inject_anomaly is not None:
        night_segments, day_segments, anomaly_marker = _inject_anomaly(
            rng, night_segments, day_segments, inject_anomaly
        )
        if anomaly_marker is None:
            # Nao havia nenhum bloco candidato para este tipo de anomalia
            # nesta sequencia gerada (ex.: sem bloco de Higiene) — a
            # sequencia fica normal em vez de falhar silenciosamente.
            inject_anomaly = None

    rows = []
    rows += _segments_to_windows(rng, night_segments, jitter, "noite", feature_names, anomaly_marker)
    rows += _segments_to_windows(rng, day_segments, jitter, "dia", feature_names, anomaly_marker)

    matrix = np.array([r[:-1] for r in rows], dtype=float)
    anomaly_mask = np.array([r[-1] for r in rows], dtype=bool)
    return matrix, anomaly_mask, inject_anomaly


def get_feature_names():
    """Nomes das features na mesma ordem em que extract_features() as
    produz, obtidos gerando uma janela de exemplo (evita duplicar a lista
    de nomes à mão e correr o risco de ficar dessincronizada de
    features.py)."""
    rng = np.random.default_rng(0)
    jitter = _subject_jitter(rng)
    window = _generate_window_signal(rng, "Descanso", jitter)
    feats = extract_features(window)
    return list(feats.keys())
