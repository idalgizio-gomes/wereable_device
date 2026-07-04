"""Detetor de duração baseado em regras — passo 3 do pipeline de ML (ver
ml/README.md).

Compara a duração de cada bloco de atividade classificado (uma sequência
contígua da mesma classe, ex.: "Higiene" durante 12 minutos) com o que é
esperado para essa classe na sessão (dia/noite) em que ocorre, sinalizando
como anómalo qualquer bloco:
  (a) fora do intervalo [d_min, d_max] esperado para essa classe+sessão, ou
  (b) de uma classe que não é esperada nessa sessão de todo (ex.:
      "Atividade" a meio da noite — a sessão noturna só admite
      Dormir/Descanso neste gerador, ver `synthetic_data.py`).

Complementar ao LSTM Autoencoder (passo 2), não um substituto: aquele deteta
padrões CONTEXTUALMENTE atípicos numa janela de 2 minutos; este vê a
DURAÇÃO TOTAL de um bloco inteiro — exatamente o que o autoencoder não
consegue ver (ver "achado honesto" na secção do passo 2 do README, que
motivou diretamente este módulo). Ao contrário dos passos 1 e 2, isto não é
um modelo treinado — é uma regra determinística, por isso não há
treino/pesos, só avaliação.

Limitação assumida (documentada, não escondida):
- Os limites usados aqui (`SESSION_BLOCK_MINUTES`) são os mesmos intervalos
  [lo, hi] que o próprio gerador sintético (`DAY_BLOCK_MINUTES`/
  `NIGHT_BLOCK_MINUTES` em `synthetic_data.py`) usa para amostrar a duração
  de um bloco NORMAL — não os valores da vista "Limites de duração" já
  existente no dashboard, que descreve um template de 21 passos com 10
  classes mais granulares (o classificador aqui usa só 5 classes
  simplificadas, sem correspondência 1-para-1 com esse template — ver
  ml/README.md, secção "Porquê 5 classes"). Quando existirem dados reais,
  os limites devem vir de um histórico real por pessoa (item 3 do backlog
  do dashboard, "modelos personalizados por pessoa"), não dos parâmetros do
  próprio gerador sintético.
- Consequência da limitação acima: como os limites usados para sinalizar
  SÃO os mesmos usados para gerar os dados "normais", seria de esperar
  falsos positivos ~0 por construção — **mas a avaliação mede 7.17%**,
  inteiramente explicado pelo último bloco de cada sessão sintética, que o
  próprio gerador corta (`dur = min(dur, remaining)`) para a sessão somar
  exatamente o total de minutos configurado (não é uma anomalia real; ver
  `run_evaluation()`/`ml/README.md` para o detalhe medido). Isto **não**
  valida a especificidade da regra sobre variabilidade humana real (essa só
  pode ser medida com dados reais, ainda não disponíveis).
"""

import json

import numpy as np

from synthetic_data import DAY_BLOCK_MINUTES, NIGHT_BLOCK_MINUTES
from synthetic_sequences import ANOMALY_TYPES, generate_subject_segments

SESSION_BLOCK_MINUTES = {"dia": DAY_BLOCK_MINUTES, "noite": NIGHT_BLOCK_MINUTES}

REASON_UNEXPECTED_CLASS = "classe_inesperada_nesta_sessao"
REASON_OUT_OF_BOUNDS = "duracao_fora_dos_limites"


def evaluate_block(session, cls, duration_min):
    """Devolve (is_anomaly: bool, reason: str|None) para um único bloco."""
    limits = SESSION_BLOCK_MINUTES.get(session, {})
    if cls not in limits:
        return True, REASON_UNEXPECTED_CLASS
    d_min, d_max = limits[cls]
    if duration_min < d_min or duration_min > d_max:
        return True, REASON_OUT_OF_BOUNDS
    return False, None


def evaluate_subject(night_segments, day_segments, anomaly_marker):
    """Avalia todos os blocos (noite+dia) de um sujeito, devolvendo uma
    lista de dicts com o veredito do detetor vs. o rótulo verdadeiro
    (`anomaly_marker`, o mesmo formato devolvido por
    `synthetic_sequences.generate_subject_segments`).

    `is_last_of_session` fica marcado no último bloco de cada sessão — o
    gerador sintético (`_build_segment_sequence` em `synthetic_data.py`)
    corta esse bloco (`dur = min(dur, remaining)`) para a sessão somar
    exatamente `DAY_SESSION_MINUTES`/`NIGHT_SESSION_MINUTES`, o que pode
    produzir uma duração normal mais curta que `d_min` sem ser uma
    anomalia real — ver nota de falsos positivos no docstring do módulo."""
    anomalous_session, anomalous_idx = anomaly_marker if anomaly_marker else (None, None)

    rows = []
    for session, segments in (("noite", night_segments), ("dia", day_segments)):
        n_segments = len(segments)
        for idx, (cls, dur) in enumerate(segments):
            is_true_anomaly = session == anomalous_session and idx == anomalous_idx
            flagged, reason = evaluate_block(session, cls, dur)
            rows.append(dict(
                session=session, cls=cls, duration_min=dur,
                is_true_anomaly=is_true_anomaly, flagged=flagged, reason=reason,
                is_last_of_session=idx == n_segments - 1,
            ))
    return rows


def run_evaluation(n_normal_subjects=40, n_subjects_per_anomaly=40, seed=123):
    """Gera sujeitos sintéticos novos (seed distinta dos passos 1/2, para não
    reutilizar exatamente as mesmas sequências já usadas para calibrar o
    LSTM Autoencoder) e avalia a regra bloco a bloco."""
    rng = np.random.default_rng(seed)

    all_rows = []
    for _ in range(n_normal_subjects):
        night_segments, day_segments, _marker, _applied = generate_subject_segments(rng, inject_anomaly=None)
        all_rows += evaluate_subject(night_segments, day_segments, None)

    recall_by_type = {}
    for anomaly_type in ANOMALY_TYPES:
        type_rows = []
        n_injected = 0
        for _ in range(n_subjects_per_anomaly):
            night_segments, day_segments, marker, applied = generate_subject_segments(
                rng, inject_anomaly=anomaly_type
            )
            type_rows += evaluate_subject(night_segments, day_segments, marker)
            if applied is not None:
                n_injected += 1
        all_rows += type_rows

        true_anomaly_rows = [r for r in type_rows if r["is_true_anomaly"]]
        recall = (
            sum(1 for r in true_anomaly_rows if r["flagged"]) / len(true_anomaly_rows)
            if true_anomaly_rows else None
        )
        recall_by_type[anomaly_type] = dict(
            n_subjects_requested=n_subjects_per_anomaly,
            n_subjects_with_injected_block=n_injected,
            recall=recall,
        )

    normal_blocks = [r for r in all_rows if not r["is_true_anomaly"]]
    normal_flagged = [r for r in normal_blocks if r["flagged"]]
    false_positive_rate = len(normal_flagged) / len(normal_blocks)
    fp_last_of_session = sum(1 for r in normal_flagged if r["is_last_of_session"])

    return dict(
        n_normal_subjects=n_normal_subjects,
        n_subjects_per_anomaly_type=n_subjects_per_anomaly,
        seed=seed,
        n_normal_blocks_evaluated=len(normal_blocks),
        false_positive_rate_normal_blocks=false_positive_rate,
        false_positives_explained_by_last_block_of_session=(
            f"{fp_last_of_session}/{len(normal_flagged)}"
        ),
        recall_by_anomaly_type=recall_by_type,
        note=(
            "Detetor de duração baseado em regras (passo 3 do pipeline, ver "
            "ml/README.md) — não é um modelo treinado; é uma comparação "
            "determinística contra os limites [d_min, d_max] usados pelo "
            "próprio gerador sintético (synthetic_data.py). 100% sintético. "
            "Achado honesto: TODOS os falsos positivos medidos (ver campo "
            "'false_positives_explained_by_last_block_of_session') "
            "acontecem no último bloco de cada sessão, que o gerador corta "
            "(`dur = min(dur, remaining)`) para a sessão somar exatamente "
            "o total de minutos configurado — não é uma anomalia real nem "
            "uma falha da regra, é um artefacto da forma como as sessões "
            "sintéticas comprimidas são construídas (ver docstring do "
            "módulo). Isto não valida especificidade em dados reais, onde "
            "não existe esse corte artificial por orçamento de minutos."
        ),
    )


def main():
    metrics = run_evaluation()
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    with open("reports/duration_detector_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
