"""Treino do LSTM Autoencoder — passo 2 do pipeline do artigo científico de
referência (XGBoost [passo 1, feito] + LSTM Autoencoder [este script] +
detetor de duração baseado em regras [passo 3, ver ml/README.md]).

IDEIA: em vez de classificar uma janela isolada (passo 1), este passo olha
para uma SUBSEQUÊNCIA de janelas consecutivas (`SEQ_LEN` janelas de 10s, ver
abaixo) e tenta reconstruí-la. Treinado só com sequências normais (nunca vê
uma anomalia durante o treino — é um autoencoder, não um classificador
supervisionado), o modelo aprende o padrão normal de transições/routine; uma
subsequência com erro de reconstrução muito acima do normal é sinalizada
como possível anomalia comportamental (ex.: duração de atividade fora do
habitual, atividade a horas erradas). Distinto e complementar ao passo 1
(que classifica QUAL atividade está a decorrer numa janela) — este passo
não sabe nem precisa de saber a classe, só se o PADRÃO TEMPORAL é usual.

DADOS: sequências sintéticas com anomalias injetadas, geradas por
`synthetic_sequences.py` (ver docstring aí para os 3 tipos de anomalia
usados). Split por sujeito sintético (mesma metodologia dos scripts do
passo 1) em 4 grupos, para nunca misturar propósitos diferentes no mesmo
conjunto de sujeitos:
  - `train`: só sequências normais, usadas para ajustar os pesos do
    autoencoder (nunca vê anomalias).
  - `val`: só sequências normais, não usadas no treino — só para parar o
    treino cedo (early stopping) e escolher o melhor modelo.
  - `threshold`: só sequências normais, não usadas em `train`/`val` — a
    distribuição do erro de reconstrução destas subsequências define o
    limiar de deteção (percentil 95).
  - `eval_normal` + `eval_anomaly`: avaliação final (nunca vistos em
    nenhum passo anterior) — reporta precisão/recall/AUC-ROC do limiar
    escolhido contra anomalias reais (injetadas) nunca vistas antes.

Porquê esta arquitetura (LSTM Autoencoder) e não outra: é a escolha do
artigo científico de referência para este passo — mantemo-nos alinhados
com a base científica do projeto (ver PROJECT_STATUS.md). Arquitetura
pequena deliberadamente (LSTM(32)) — este treino corre no backend/offline;
embarcar isto no firmware exigiria TensorFlow Lite Micro ou CMSIS-NN e
quantização, medição de footprint real, ainda não feito (ver
"Estudo de viabilidade TinyML" em PROJECT_STATUS.md) — este script só
valida a pipeline de deteção de ponta a ponta no backend.
"""

import json

import numpy as np
from sklearn.metrics import average_precision_score, precision_recall_fscore_support, roc_auc_score
from sklearn.preprocessing import StandardScaler

import synthetic_sequences as seq

SEQ_LEN = 12          # 12 janelas de 10s = 2 minutos de contexto por subsequência
SEQ_STEP = 6           # subsequências parcialmente sobrepostas (50%), não redundantes ao ponto de repetir tudo
SUBSEQ_ANOMALY_FRACTION = 0.5  # subsequência conta como anómala se >=50% das suas janelas o forem
THRESHOLD_PERCENTILE = 95      # limiar de deteção = percentil 95 do erro em subsequências normais
SEED = 42

N_TRAIN_SUBJECTS = 10       # normais, ajustam os pesos do autoencoder
N_VAL_SUBJECTS = 3          # normais, só para early stopping
# 3 sujeitos era pouco para estimar de forma estável um percentil 95 (alta
# variância de amostragem) — subiu para 8 depois de uma primeira execução
# mostrar um limiar sensível de mais a este tamanho de amostra pequeno.
N_THRESHOLD_SUBJECTS = 8    # normais, só para calibrar o limiar de deteção
N_EVAL_NORMAL_SUBJECTS = 3  # normais, só para a avaliação final (nunca vistos antes)
N_EVAL_SUBJECTS_PER_ANOMALY = 3  # por cada tipo em synthetic_sequences.ANOMALY_TYPES


def build_subsequences(matrix, anomaly_mask):
    """Fatia uma sequência [n_windows, n_features] em subsequências de
    SEQ_LEN janelas, com passo SEQ_STEP. Devolve (X [n_sub, SEQ_LEN,
    n_features], y [n_sub] bool — subsequência anómala ou não)."""
    n_windows = matrix.shape[0]
    xs, ys = [], []
    for start in range(0, n_windows - SEQ_LEN + 1, SEQ_STEP):
        end = start + SEQ_LEN
        xs.append(matrix[start:end])
        ys.append(bool(anomaly_mask[start:end].mean() >= SUBSEQ_ANOMALY_FRACTION))
    if not xs:
        return np.empty((0, SEQ_LEN, matrix.shape[1])), np.empty((0,), dtype=bool)
    return np.stack(xs), np.array(ys, dtype=bool)


def generate_group(rng, feature_names, n_subjects, anomaly_type=None):
    """Gera `n_subjects` sequências (todas normais se anomaly_type=None,
    todas com o mesmo tipo de anomalia injetada caso contrário) e devolve
    a lista de (matrix, anomaly_mask) por sujeito."""
    out = []
    for _ in range(n_subjects):
        matrix, mask, actual_type = seq.generate_subject_sequence(rng, feature_names, inject_anomaly=anomaly_type)
        out.append((matrix, mask, actual_type))
    return out


def subsequences_from_group(group):
    xs, ys = [], []
    for matrix, mask, _actual_type in group:
        x, y = build_subsequences(matrix, mask)
        xs.append(x)
        ys.append(y)
    return np.concatenate(xs), np.concatenate(ys)


def build_autoencoder(seq_len, n_features):
    # Import feito aqui dentro (nao no topo do ficheiro) para os restantes
    # scripts de ml/ (treino XGBoost/Random Forest, medicao de footprint)
    # continuarem a nao precisar de tensorflow instalado so' para correr.
    from tensorflow import keras
    from tensorflow.keras import layers

    inputs = keras.Input(shape=(seq_len, n_features))
    encoded = layers.LSTM(32, activation="tanh")(inputs)
    repeated = layers.RepeatVector(seq_len)(encoded)
    decoded = layers.LSTM(32, activation="tanh", return_sequences=True)(repeated)
    outputs = layers.TimeDistributed(layers.Dense(n_features))(decoded)
    model = keras.Model(inputs, outputs)
    model.compile(optimizer="adam", loss="mse")
    return model


def reconstruction_error(model, X):
    """MSE de reconstrução por subsequência (média sobre timesteps e
    features) — score de anomalia: quanto maior, mais fora do padrão
    aprendido como normal."""
    recon = model.predict(X, verbose=0)
    return np.mean(np.square(X - recon), axis=(1, 2))


def main():
    rng = np.random.default_rng(SEED)
    feature_names = seq.get_feature_names()
    n_features = len(feature_names)

    # --- geração das sequências, por grupo (ver docstring do módulo) ---
    train_group = generate_group(rng, feature_names, N_TRAIN_SUBJECTS)
    val_group = generate_group(rng, feature_names, N_VAL_SUBJECTS)
    threshold_group = generate_group(rng, feature_names, N_THRESHOLD_SUBJECTS)
    eval_normal_group = generate_group(rng, feature_names, N_EVAL_NORMAL_SUBJECTS)
    eval_anomaly_groups = {
        anomaly_type: generate_group(rng, feature_names, N_EVAL_SUBJECTS_PER_ANOMALY, anomaly_type=anomaly_type)
        for anomaly_type in seq.ANOMALY_TYPES
    }

    # --- escala das features (ajustada só nas sequências de treino, como
    # nos outros scripts do pipeline) ---
    train_windows = np.concatenate([m for m, _mask, _t in train_group])
    scaler = StandardScaler().fit(train_windows)

    def scale_group(group):
        return [(scaler.transform(m), mask, t) for m, mask, t in group]

    train_group = scale_group(train_group)
    val_group = scale_group(val_group)
    threshold_group = scale_group(threshold_group)
    eval_normal_group = scale_group(eval_normal_group)
    eval_anomaly_groups = {k: scale_group(v) for k, v in eval_anomaly_groups.items()}

    X_train, _y_train = subsequences_from_group(train_group)
    X_val, _y_val = subsequences_from_group(val_group)
    X_threshold, y_threshold = subsequences_from_group(threshold_group)
    X_eval_normal, y_eval_normal = subsequences_from_group(eval_normal_group)

    assert not _y_train.any(), "grupo de treino nao deve conter subsequencias anomalas"
    assert not y_threshold.any(), "grupo de calibracao do limiar nao deve conter subsequencias anomalas"
    assert not y_eval_normal.any(), "grupo de avaliacao normal nao deve conter subsequencias anomalas"

    # --- treino (só com sequências normais) ---
    from tensorflow import keras

    # Bug corrigido: só `rng` (geração dos dados sintéticos) estava semeado
    # com SEED. A inicialização dos pesos do LSTM/Dense e o "shuffle=True"
    # do model.fit() abaixo dependem do RNG global do TensorFlow/Keras, que
    # não era semeado — duas execuções com os mesmos dados produziam pesos
    # finais, `detection_threshold_mse` e métricas ligeiramente diferentes
    # de cada vez, ao contrário do que o README ("seed fixa = 42, todos os
    # scripts determinísticos") garante. set_random_seed() cobre Python,
    # NumPy e TensorFlow com uma única chamada.
    keras.utils.set_random_seed(SEED)

    model = build_autoencoder(SEQ_LEN, n_features)
    early_stop = keras.callbacks.EarlyStopping(monitor="val_loss", patience=4, restore_best_weights=True)
    history = model.fit(
        X_train, X_train,
        validation_data=(X_val, X_val),
        epochs=40,
        batch_size=64,
        shuffle=True,
        callbacks=[early_stop],
        verbose=0,
    )

    # --- limiar de deteção: percentil 95 do erro em subsequências normais
    # nunca vistas no treino/validação ---
    threshold_errors = reconstruction_error(model, X_threshold)
    detection_threshold = float(np.percentile(threshold_errors, THRESHOLD_PERCENTILE))

    # --- avaliação final ---
    eval_normal_errors = reconstruction_error(model, X_eval_normal)

    per_anomaly_metrics = {}
    all_scores, all_labels = list(eval_normal_errors), [0] * len(eval_normal_errors)
    for anomaly_type, group in eval_anomaly_groups.items():
        X_anom, y_anom = subsequences_from_group(group)
        errors = reconstruction_error(model, X_anom)
        # Só as subsequencias efetivamente marcadas anomalas (y_anom==True)
        # contam para o recall deste tipo - as restantes (mesmo sujeito,
        # bloco nao afetado) sao normais e entram na avaliacao global como tal.
        anomalous_errors = errors[y_anom]
        normal_errors_same_subjects = errors[~y_anom]
        recall = float(np.mean(anomalous_errors > detection_threshold)) if len(anomalous_errors) else None
        # AUC-ROC deste tipo de anomalia SOZINHO contra as subsequências
        # normais de avaliação (eval_normal_errors) — separado da métrica
        # "overall" (que mistura os 3 tipos) para não esconder que tipos
        # de anomalia muito distintos entre si têm separabilidade muito
        # diferente (ver ml/README.md).
        type_auc = None
        type_pr_auc = None
        if len(anomalous_errors) and len(eval_normal_errors):
            type_labels = [1] * len(anomalous_errors) + [0] * len(eval_normal_errors)
            type_scores = list(anomalous_errors) + list(eval_normal_errors)
            type_auc = float(roc_auc_score(type_labels, type_scores))
            # PR-AUC (average precision): ao contrário do ROC-AUC, é sensível
            # à prevalência da classe positiva (anómala) — mais informativo
            # do que "precision a um limiar fixo" quando essa prevalência é
            # pequena (ver ml/README.md, achado das sessões de 24h: precisão
            # a limiar fixo caiu 0.276->0.035 sem o modelo ter piorado,
            # AUC-ROC manteve-se estável — sintoma clássico de desequilíbrio
            # de classes que o ROC-AUC não capta bem).
            type_pr_auc = float(average_precision_score(type_labels, type_scores))
        per_anomaly_metrics[anomaly_type] = {
            "n_subsequences_anomalous": int(y_anom.sum()),
            "n_subsequences_normal_same_subjects": int((~y_anom).sum()),
            "recall_at_threshold": recall,
            "mean_reconstruction_error": float(np.mean(anomalous_errors)) if len(anomalous_errors) else None,
            "auc_roc_vs_eval_normal": type_auc,
            "pr_auc_vs_eval_normal": type_pr_auc,
        }
        all_scores += list(anomalous_errors) + list(normal_errors_same_subjects)
        all_labels += [1] * len(anomalous_errors) + [0] * len(normal_errors_same_subjects)

    all_scores = np.array(all_scores)
    all_labels = np.array(all_labels)
    predictions = (all_scores > detection_threshold).astype(int)
    precision, recall, f1, _support = precision_recall_fscore_support(
        all_labels, predictions, average="binary", zero_division=0
    )
    auc = float(roc_auc_score(all_labels, all_scores))
    pr_auc = float(average_precision_score(all_labels, all_scores))

    metrics = {
        "seq_len_windows": SEQ_LEN,
        "seq_step_windows": SEQ_STEP,
        "window_seconds": 10,
        "detection_threshold_percentile": THRESHOLD_PERCENTILE,
        "detection_threshold_mse": detection_threshold,
        "n_train_subjects": N_TRAIN_SUBJECTS,
        "n_val_subjects": N_VAL_SUBJECTS,
        "n_threshold_subjects": N_THRESHOLD_SUBJECTS,
        "n_eval_normal_subjects": N_EVAL_NORMAL_SUBJECTS,
        "n_eval_subjects_per_anomaly": N_EVAL_SUBJECTS_PER_ANOMALY,
        "epochs_trained": len(history.history["loss"]),
        "final_train_loss": float(history.history["loss"][-1]),
        "final_val_loss": float(history.history["val_loss"][-1]),
        "overall": {
            "precision": float(precision),
            "recall": float(recall),
            "f1": float(f1),
            "auc_roc": auc,
            "pr_auc": pr_auc,
            "n_eval_subsequences": int(len(all_labels)),
            "n_eval_anomalous": int(all_labels.sum()),
            "eval_anomalous_prevalence": float(all_labels.mean()),
        },
        "per_anomaly_type": per_anomaly_metrics,
        "note": (
            "Avaliado inteiramente sobre dados sintéticos com anomalias "
            "INJETADAS (ver synthetic_sequences.py) — as anomalias aqui são "
            "desenhadas para serem claramente distinguíveis do padrão normal, "
            "para validar a pipeline de deteção de ponta a ponta. NÃO é uma "
            "validação clínica: anomalias comportamentais reais em demência "
            "são muito mais subtis e ambíguas do que as simuladas aqui. Ver "
            "ml/README.md para a interpretação honesta completa."
        ),
        "pr_auc_note": (
            "pr_auc (average precision) foi adicionado para complementar "
            "precision/recall a um limiar fixo, que se revelou muito "
            "sensível à prevalência da classe anómala depois da mudança "
            "para sessões de 24h (ver ml/README.md, Passo 2) — ao contrário "
            "do ROC-AUC, o PR-AUC é sensível a essa prevalência, por isso "
            "compara-se diretamente com eval_anomalous_prevalence: um "
            "PR-AUC muito acima da prevalência de base indica que o modelo "
            "ainda ordena bem as subsequências anómalas, mesmo quando um "
            "limiar único fixo tem má precisão."
        ),
    }

    with open("reports/lstm_autoencoder_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)

    model.save("models/lstm_autoencoder.keras")
    import joblib
    joblib.dump(scaler, "models/lstm_autoencoder_scaler.joblib")
    with open("models/lstm_autoencoder_labels.json", "w") as f:
        json.dump({"feature_names": feature_names, "seq_len": SEQ_LEN}, f, indent=2, ensure_ascii=False)

    _plot_error_distribution(eval_normal_errors, eval_anomaly_groups, model, detection_threshold)

    print(json.dumps(metrics["overall"], indent=2, ensure_ascii=False))
    print("Recall por tipo de anomalia:")
    for anomaly_type, m in per_anomaly_metrics.items():
        print(f"  {anomaly_type}: {m['recall_at_threshold']}")


def _plot_error_distribution(eval_normal_errors, eval_anomaly_groups, model, threshold):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.hist(eval_normal_errors, bins=30, alpha=0.6, label="Normal (avaliação)", color="#4c9be8")
    for anomaly_type, group in eval_anomaly_groups.items():
        X_anom, y_anom = subsequences_from_group(group)
        errors = reconstruction_error(model, X_anom)[y_anom]
        if len(errors):
            ax.hist(errors, bins=30, alpha=0.5, label=f"Anómalo: {anomaly_type}")
    ax.axvline(threshold, color="red", linestyle="--", label="Limiar de deteção (percentil 95)")
    ax.set_xlabel("Erro de reconstrução (MSE por subsequência)")
    ax.set_ylabel("Nº de subsequências")
    ax.set_title("LSTM Autoencoder — erro de reconstrução, normal vs. anómalo (sintético)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig("reports/lstm_autoencoder_error_distribution.png", dpi=140)
    plt.close(fig)


if __name__ == "__main__":
    main()
