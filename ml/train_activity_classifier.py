"""Treino do classificador de atividades — passo 1 do pipeline do artigo
científico de referência (XGBoost + LSTM Autoencoder + detetor de duração,
ver ml/README.md). Este script só cobre o classificador XGBoost; o LSTM
Autoencoder (deteção de anomalias) e o detetor de duração baseado em regras
ainda não foram implementados (ver "Próximos passos" em ml/README.md).

Decisão técnica — porquê XGBoost (e não, p.ex., uma rede neuronal ou random
forest simples):
  1. É o algoritmo usado no artigo científico de referência para a
     classificação de atividades a partir de features estatísticas por
     janela — mantemos a mesma escolha para ficarmos alinhados com a
     literatura que fundamenta este projeto.
  2. Boosting de árvores lida bem com features tabulares heterogéneas
     (estatísticas de acelerómetro, giroscópio e FC em escalas diferentes)
     sem normalização cuidadosa, ao contrário de redes neuronais.
  3. Path realista para MCU: há ferramentas conhecidas para exportar
     XGBoost para C (`micromlgen`, ver PROJECT_STATUS.md), ao contrário de
     abordagens mais pesadas (LSTM/CNN 1D) que exigiriam TensorFlow Lite
     Micro. Isto mantém em aberto a opção de embarcar este classificador no
     firmware no futuro, sem comprometer a decisão a este nível.
  4. `max_depth=3` é usado deliberadamente (em vez de um valor maior, mais
     habitual em backend) para já respeitar a regra prática documentada no
     PROJECT_STATUS.md ("profundidade ≤3, ≤~4000 árvores, para caber em
     flash de MCU") — mesmo treinando no backend nesta fase, preparamos o
     modelo para uma eventual conversão TinyML sem re-treino.

Avaliação por sujeito (não por janela aleatória): o conjunto de teste é um
subconjunto de sujeitos sintéticos nunca vistos no treino (split por
`subject_id`), não uma amostragem aleatória de janelas. Janelas do mesmo
sujeito são fortemente correlacionadas (mesmo jitter individual) — uma
amostragem aleatória de janelas inflacionaria artificialmente a métrica.
"""

import json

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.preprocessing import LabelEncoder
from sklearn.utils.class_weight import compute_sample_weight

NON_FEATURE_COLS = {"label", "subject_id", "session", "elapsed_min"}
TEST_SUBJECT_FRACTION = 0.25  # ~25% dos sujeitos sintéticos ficam de fora do treino


def load_dataset(path="data/synthetic_routine_dataset.csv"):
    df = pd.read_csv(path)
    feature_cols = [c for c in df.columns if c not in NON_FEATURE_COLS]
    return df, feature_cols


def split_by_subject(df, test_fraction=TEST_SUBJECT_FRACTION, seed=42):
    subjects = sorted(df["subject_id"].unique())
    rng = np.random.default_rng(seed)
    n_test = max(1, round(len(subjects) * test_fraction))
    test_subjects = set(rng.choice(subjects, size=n_test, replace=False).tolist())
    train_df = df[~df["subject_id"].isin(test_subjects)]
    test_df = df[df["subject_id"].isin(test_subjects)]
    return train_df, test_df, sorted(test_subjects)


def train(df, feature_cols):
    train_df, test_df, test_subjects = split_by_subject(df)

    # BUG CORRIGIDO (2026-07-07, rotina cloud): o encoder era ajustado só
    # com train_df["label"] — como a divisão é por sujeito (não por
    # janela), é possível (por azar da amostra aleatória de sujeitos) uma
    # classe mais rara (ex.: "Higiene") ficar inteiramente do lado do
    # teste e ausente do treino; nesse caso encoder.transform(test_df[...])
    # rebentava com "ValueError: y contains previously unseen labels" — não
    # acontece com os 8 sujeitos/seed=42 atuais (confirmado), mas é uma
    # armadilha real para a próxima iteração do dataset (mais sujeitos/
    # sementes diferentes, já no roteiro do ml/README.md). Ajustar o
    # encoder ao conjunto completo de classes (antes da divisão) evita
    # isto — o mesmo padrão já usado em measure_rf_footprint.py.
    encoder = LabelEncoder()
    encoder.fit(df["label"])
    y_train = encoder.transform(train_df["label"])
    y_test = encoder.transform(test_df["label"])
    X_train = train_df[feature_cols]
    X_test = test_df[feature_cols]

    # Dataset sintético é desequilibrado entre classes (ver
    # data/synthetic_routine_dataset.meta.json — "Descanso" domina, refletindo
    # uma rotina plausível). Sample weights "balanced" evitam que o modelo
    # aprenda a prever quase sempre a classe maioritária.
    sample_weight = compute_sample_weight("balanced", y_train)

    model = xgb.XGBClassifier(
        objective="multi:softprob",
        num_class=len(encoder.classes_),
        max_depth=3,
        n_estimators=300,
        learning_rate=0.1,
        subsample=0.8,
        colsample_bytree=0.8,
        eval_metric="mlogloss",
        random_state=42,
    )
    model.fit(X_train, y_train, sample_weight=sample_weight)

    y_pred = model.predict(X_test)

    # labels= explícito (bug corrigido): sem isto, classification_report()
    # deriva os labels de np.unique(y_test, y_pred) — se alguma classe
    # ficar com zero exemplos em y_test E em y_pred (possível com uma
    # amostra/seed diferente de sujeitos de teste), o nº de labels
    # derivados fica menor que len(target_names) e a chamada rebenta com
    # ValueError em vez de simplesmente reportar 0 para essa classe.
    report = classification_report(
        y_test, y_pred, labels=range(len(encoder.classes_)),
        target_names=encoder.classes_, output_dict=True, zero_division=0
    )
    cm = confusion_matrix(y_test, y_pred, labels=range(len(encoder.classes_)))
    acc = accuracy_score(y_test, y_pred)

    return model, encoder, feature_cols, {
        "accuracy": acc,
        "classification_report": report,
        "confusion_matrix": cm.tolist(),
        "class_order": encoder.classes_.tolist(),
        "n_train_windows": len(train_df),
        "n_test_windows": len(test_df),
        "test_subject_ids": test_subjects,
        "train_subject_ids": sorted(set(train_df["subject_id"].unique().tolist())),
    }


def save_confusion_matrix_plot(cm, class_names, path):
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(cm, cmap="Blues")
    ax.set_xticks(range(len(class_names)))
    ax.set_yticks(range(len(class_names)))
    ax.set_xticklabels(class_names, rotation=45, ha="right")
    ax.set_yticklabels(class_names)
    ax.set_xlabel("Previsto")
    ax.set_ylabel("Real")
    ax.set_title("Matriz de confusão — classificador de atividades (XGBoost, dados sintéticos)")
    for i in range(len(class_names)):
        for j in range(len(class_names)):
            ax.text(j, i, int(cm[i][j]), ha="center", va="center",
                     color="white" if cm[i][j] > cm.max() / 2 else "black")
    fig.colorbar(im)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return True


def main():
    df, feature_cols = load_dataset()
    model, encoder, feature_cols, metrics = train(df, feature_cols)

    model.save_model("models/activity_classifier_xgb.json")
    with open("models/activity_classifier_labels.json", "w") as f:
        json.dump({"classes": encoder.classes_.tolist(), "feature_cols": feature_cols}, f, indent=2, ensure_ascii=False)

    with open("reports/activity_classifier_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)

    save_confusion_matrix_plot(
        np.array(metrics["confusion_matrix"]), metrics["class_order"],
        "reports/activity_classifier_confusion_matrix.png",
    )

    print(f"Accuracy (sujeitos de teste nunca vistos no treino): {metrics['accuracy']:.3f}")
    print(f"Sujeitos de treino: {metrics['train_subject_ids']}  |  Sujeitos de teste: {metrics['test_subject_ids']}")
    print(f"Janelas treino/teste: {metrics['n_train_windows']}/{metrics['n_test_windows']}")
    print()
    for cls in metrics["class_order"]:
        r = metrics["classification_report"][cls]
        print(f"  {cls:14s} precision={r['precision']:.2f} recall={r['recall']:.2f} f1={r['f1-score']:.2f} support={int(r['support'])}")


if __name__ == "__main__":
    main()
