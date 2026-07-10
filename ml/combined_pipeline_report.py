"""Relatório combinado dos 3 detetores do pipeline (ver ml/README.md,
"Próximos passos" — combinação dos 3 detetores num relatório único).

MOTIVAÇÃO: `duration_detector.py` avalia a regra de duração (passo 3)
contra os segmentos VERDADEIROS (ground truth) do gerador sintético —
mede a qualidade da regra em si, isolada do classificador, com recall
0.925-1.000 (ver ml/README.md). Isso não é como o sistema funcionaria de
facto embarcado: em produção, o detetor de duração só vê os BLOCOS QUE O
CLASSIFICADOR (passo 1) PRODUZIU, com todos os seus erros — janelas mal
classificadas podem fragmentar um bloco contínuo em vários blocos mais
curtos, alguns dos quais caem fora de [d_min, d_max] por CAUSA do erro de
classificação, não de uma anomalia real. Este script mede esse efeito de
facto (não o assume) e junta o LSTM Autoencoder (passo 2, que vê a mesma
sequência de janelas) num veredito combinado por bloco — os "3 detetores"
do título são o classificador (produz os blocos), o detetor de duração e
o autoencoder.

Cohort de avaliação: seed própria (555), distinta de 42 (passos 1/2,
treino/avaliação dos modelos) e 123 (avaliação isolada do passo 3) — nunca
vista por nenhum treino nem calibração de limiar anteriores.

Requer os modelos já treinados em models/ (classificador XGBoost, passo 1;
LSTM Autoencoder + scaler + limiar de deteção, passo 2) — não retreina
nada, só avalia. Requer tensorflow instalado (só para carregar o
autoencoder), ao contrário de duration_detector.py/measure_rf_footprint.py.
"""

import json

import numpy as np
import xgboost as xgb
import joblib

from features import extract_features
from synthetic_data import WINDOW_SECONDS, _generate_window_signal, _subject_jitter
from synthetic_sequences import ANOMALY_TYPES, generate_subject_segments
from duration_detector import evaluate_block, evaluate_subject as oracle_evaluate_subject
from train_lstm_autoencoder import SEQ_LEN, SEQ_STEP, reconstruction_error

SEED = 555
N_NORMAL_SUBJECTS = 10
N_SUBJECTS_PER_ANOMALY = 10


def load_classifier():
    with open("models/activity_classifier_labels.json") as f:
        meta = json.load(f)
    model = xgb.XGBClassifier()
    model.load_model("models/activity_classifier_xgb.json")
    return model, meta["classes"], meta["feature_cols"]


def load_autoencoder():
    from tensorflow import keras

    model = keras.models.load_model("models/lstm_autoencoder.keras")
    scaler = joblib.load("models/lstm_autoencoder_scaler.joblib")
    with open("models/lstm_autoencoder_labels.json") as f:
        ae_meta = json.load(f)
    with open("reports/lstm_autoencoder_metrics.json") as f:
        ae_metrics = json.load(f)
    return model, scaler, ae_meta["feature_names"], ae_metrics["detection_threshold_mse"]


def generate_subject_windows(rng, inject_anomaly=None):
    """Gera as janelas noite+dia de UM sujeito sintético (mesma lógica de
    synthetic_sequences.generate_subject_sequence), mas preservando por
    janela a classe VERDADEIRA, a sessão e o índice do segmento de
    origem — necessário aqui (e não em synthetic_sequences.py, que só
    devolve a matriz de features) para depois comparar os blocos
    PREVISTOS pelo classificador com os segmentos REAIS."""
    jitter = _subject_jitter(rng)
    night_segments, day_segments, anomaly_marker, applied = generate_subject_segments(rng, inject_anomaly)

    rows = []
    for session_name, segments in (("noite", night_segments), ("dia", day_segments)):
        for seg_idx, (cls, dur_min) in enumerate(segments):
            n_windows = max(1, (dur_min * 60) // WINDOW_SECONDS)
            is_anom_segment = anomaly_marker == (session_name, seg_idx)
            for _ in range(int(n_windows)):
                window = _generate_window_signal(rng, cls, jitter)
                feats = extract_features(window)
                rows.append(dict(
                    features=feats, true_class=cls, session=session_name,
                    true_segment_idx=seg_idx, is_anomalous_window=is_anom_segment,
                ))
    return rows, night_segments, day_segments, anomaly_marker, applied


def predicted_blocks_from_rows(rows, predicted_classes):
    """Agrupa janelas CONSECUTIVAS da mesma sessão com a mesma classe
    PREVISTA (não a verdadeira) em blocos — simula o que o detetor de
    duração veria de facto a jusante do classificador embarcado, em vez
    dos segmentos verdadeiros do gerador."""
    blocks = []
    current = None
    for row, pred_cls in zip(rows, predicted_classes):
        if current is not None and current["session"] == row["session"] and current["cls"] == pred_cls:
            current["n_windows"] += 1
            current["row_idxs"].append(row["_idx"])
            current["is_true_anomaly"] = current["is_true_anomaly"] or row["is_anomalous_window"]
        else:
            if current is not None:
                blocks.append(current)
            current = dict(
                session=row["session"], cls=pred_cls, n_windows=1,
                row_idxs=[row["_idx"]], is_true_anomaly=row["is_anomalous_window"],
            )
    if current is not None:
        blocks.append(current)
    for b in blocks:
        b["duration_min"] = b["n_windows"] * WINDOW_SECONDS / 60.0
    return blocks


def ae_window_flags(model, scaler, feature_names, rows, threshold):
    """Erro de reconstrução do autoencoder por subsequência (SEQ_LEN
    janelas, passo SEQ_STEP — mesma janela deslizante de
    train_lstm_autoencoder.py), projetado para um array booleano por
    JANELA (não por subsequência): uma janela fica marcada se pertencer a
    pelo menos uma subsequência com erro acima do limiar já calibrado
    (percentil 95, ver reports/lstm_autoencoder_metrics.json)."""
    X = np.array([[r["features"][name] for name in feature_names] for r in rows], dtype=float)
    Xs = scaler.transform(X)
    n_windows = Xs.shape[0]
    flagged = np.zeros(n_windows, dtype=bool)
    starts = list(range(0, max(0, n_windows - SEQ_LEN + 1), SEQ_STEP))
    if not starts:
        return flagged
    subseqs = np.stack([Xs[s:s + SEQ_LEN] for s in starts])
    errors = reconstruction_error(model, subseqs)
    for s, err in zip(starts, errors):
        if err > threshold:
            flagged[s:s + SEQ_LEN] = True
    return flagged


def process_subject(rng, classifier, classes, feature_names, ae_model, ae_scaler, ae_threshold, inject_anomaly=None):
    rows, night_segments, day_segments, anomaly_marker, applied = generate_subject_windows(rng, inject_anomaly)
    for i, r in enumerate(rows):
        r["_idx"] = i

    X = np.array([[r["features"][name] for name in feature_names] for r in rows], dtype=float)
    y_pred = [classes[idx] for idx in classifier.predict(X)]
    window_accuracy = float(np.mean([r["true_class"] == p for r, p in zip(rows, y_pred)]))

    blocks = predicted_blocks_from_rows(rows, y_pred)
    ae_flags = ae_window_flags(ae_model, ae_scaler, feature_names, rows, ae_threshold)
    for b in blocks:
        b["flag_duration"], b["reason"] = evaluate_block(b["session"], b["cls"], b["duration_min"])
        b["flag_ae"] = bool(any(ae_flags[i] for i in b["row_idxs"]))
        b["flag_combined"] = b["flag_duration"] or b["flag_ae"]

    oracle_rows = oracle_evaluate_subject(night_segments, day_segments, anomaly_marker)

    return dict(
        blocks=blocks, oracle_rows=oracle_rows, applied=applied,
        window_accuracy=window_accuracy,
        n_true_blocks=len(night_segments) + len(day_segments),
        n_predicted_blocks=len(blocks),
    )


def run_evaluation(n_normal_subjects=N_NORMAL_SUBJECTS, n_subjects_per_anomaly=N_SUBJECTS_PER_ANOMALY, seed=SEED):
    classifier, classes, feature_cols = load_classifier()
    ae_model, ae_scaler, ae_feature_names, ae_threshold = load_autoencoder()
    assert feature_cols == ae_feature_names, (
        "feature_cols do classificador e feature_names do autoencoder devem estar na "
        "mesma ordem (ambos derivam de features.extract_features()) — verificado, não assumido."
    )
    feature_names = feature_cols

    rng = np.random.default_rng(seed)

    subject_results = []
    for _ in range(n_normal_subjects):
        subject_results.append(("normal", process_subject(
            rng, classifier, classes, feature_names, ae_model, ae_scaler, ae_threshold, None
        )))

    per_type_summary = {}
    for anomaly_type in ANOMALY_TYPES:
        type_results = [
            process_subject(rng, classifier, classes, feature_names, ae_model, ae_scaler, ae_threshold, anomaly_type)
            for _ in range(n_subjects_per_anomaly)
        ]
        subject_results += [(anomaly_type, r) for r in type_results]

        n_applied = sum(1 for r in type_results if r["applied"] is not None)

        def recall_for(pred_fn):
            if n_applied == 0:
                return None
            detected = sum(
                1 for r in type_results if r["applied"] is not None and pred_fn(r)
            )
            return detected / n_applied

        per_type_summary[anomaly_type] = dict(
            n_subjects_requested=n_subjects_per_anomaly,
            n_subjects_with_injected_block=n_applied,
            recall_duration_oracle_blocks=recall_for(
                lambda r: any(row["is_true_anomaly"] and row["flagged"] for row in r["oracle_rows"])
            ),
            recall_duration_predicted_blocks=recall_for(
                lambda r: any(b["is_true_anomaly"] and b["flag_duration"] for b in r["blocks"])
            ),
            recall_autoencoder=recall_for(
                lambda r: any(b["is_true_anomaly"] and b["flag_ae"] for b in r["blocks"])
            ),
            recall_combined_predicted_plus_autoencoder=recall_for(
                lambda r: any(b["is_true_anomaly"] and b["flag_combined"] for b in r["blocks"])
            ),
        )

    all_blocks = [b for _t, r in subject_results for b in r["blocks"]]
    normal_blocks = [b for b in all_blocks if not b["is_true_anomaly"]]

    def fp_rate(flag_key):
        return (sum(1 for b in normal_blocks if b[flag_key]) / len(normal_blocks)) if normal_blocks else None

    all_oracle_rows = [row for _t, r in subject_results for row in r["oracle_rows"]]
    normal_oracle_rows = [row for row in all_oracle_rows if not row["is_true_anomaly"]]
    oracle_fp_rate = (
        sum(1 for row in normal_oracle_rows if row["flagged"]) / len(normal_oracle_rows)
        if normal_oracle_rows else None
    )

    mean_true_blocks = float(np.mean([r["n_true_blocks"] for _t, r in subject_results]))
    mean_predicted_blocks = float(np.mean([r["n_predicted_blocks"] for _t, r in subject_results]))
    mean_window_accuracy = float(np.mean([r["window_accuracy"] for _t, r in subject_results]))

    metrics = dict(
        seed=seed,
        n_normal_subjects=n_normal_subjects,
        n_subjects_per_anomaly_type=n_subjects_per_anomaly,
        classifier_model="xgboost (models/activity_classifier_xgb.json, passo 1)",
        window_level_classifier_accuracy_on_this_cohort=mean_window_accuracy,
        block_fragmentation=dict(
            mean_true_blocks_per_subject=mean_true_blocks,
            mean_predicted_blocks_per_subject=mean_predicted_blocks,
            fragmentation_ratio=(mean_predicted_blocks / mean_true_blocks) if mean_true_blocks else None,
        ),
        per_anomaly_type=per_type_summary,
        false_positive_rate_normal_blocks=dict(
            duration_oracle_blocks=oracle_fp_rate,
            duration_predicted_blocks=fp_rate("flag_duration"),
            autoencoder=fp_rate("flag_ae"),
            combined_predicted_plus_autoencoder=fp_rate("flag_combined"),
        ),
        note=(
            "Avaliação end-to-end sobre um cohort sintético novo (seed=555, nunca "
            "visto no treino/calibração dos modelos usados: XGBoost seed=42, LSTM "
            "Autoencoder seed=42, limiar percentil 95 calibrado nesse mesmo treino). "
            "'_oracle_blocks' usa os segmentos VERDADEIROS do gerador (mesma "
            "metodologia de duration_detector.py, avalia a regra isolada do "
            "classificador); '_predicted_blocks' usa os blocos que o classificador "
            "XGBoost (passo 1) de facto produziu, com os seus erros de classificação "
            "incluídos — a diferença entre os dois mede o custo real de encadear os "
            "passos 1+3 em vez de avaliar cada um isoladamente. 'autoencoder' e "
            "'combined' operam sobre os blocos previstos (não os segmentos "
            "verdadeiros). 100% sintético — não valida desempenho em dados reais "
            "(mesma limitação documentada em todo o ml/README.md)."
        ),
    )

    return metrics


def _plot_recall_by_detector(per_type_summary, path="reports/combined_pipeline_recall_by_detector.png"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    types = list(per_type_summary.keys())
    detectors = [
        ("recall_duration_oracle_blocks", "Duração (blocos oráculo)"),
        ("recall_duration_predicted_blocks", "Duração (blocos do classificador)"),
        ("recall_autoencoder", "LSTM Autoencoder"),
        ("recall_combined_predicted_plus_autoencoder", "Combinado (class.+duração+AE)"),
    ]
    x = np.arange(len(types))
    width = 0.2
    fig, ax = plt.subplots(figsize=(9, 5))
    for i, (key, label) in enumerate(detectors):
        values = [per_type_summary[t][key] or 0.0 for t in types]
        ax.bar(x + i * width, values, width, label=label)
    ax.set_xticks(x + width * 1.5)
    ax.set_xticklabels(types, rotation=15)
    ax.set_ylabel("Recall (fração de sujeitos com a anomalia detetada)")
    ax.set_ylim(0, 1.05)
    ax.set_title("Recall por tipo de anomalia e detetor — pipeline combinado (sintético)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def main():
    metrics = run_evaluation()
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    with open("reports/combined_pipeline_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)
    _plot_recall_by_detector(metrics["per_anomaly_type"])


if __name__ == "__main__":
    main()
