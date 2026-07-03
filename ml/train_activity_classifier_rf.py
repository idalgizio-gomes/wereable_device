"""Treino do classificador de atividades — variante Random Forest.

Contexto (ver ml/README.md, secção "Decisão pendente"): o artigo científico
de referência usa XGBoost, mas em modo multiclasse (10 classes no artigo,
5 neste projeto) o XGBoost treina uma árvore por classe por ronda de
boosting — "400 estimadores" no artigo são, na prática, ~4000 árvores
internas. Um precedente publicado mostrou 500 árvores a exigirem 553-727KB
de flash só para caberem — não cabe no orçamento desta placa (~608KB
livres). Por isso este script treina a alternativa recomendada: um Random
Forest com árvores mais rasas e em menor número, convertível para C via
`emlearn` (ao contrário do XGBoost, que precisaria do `micromlgen`, sem
manutenção ativa).

IMPORTANTE: isto é só o treino/avaliação comparativa, para termos números
reais dos dois lados (XGBoost vs. Random Forest) sobre o mesmo dataset e a
mesma metodologia de avaliação. A decisão de qual dos dois usar
definitivamente numa eventual versão embarcada continua por validar pelo
utilizador (ver ml/README.md) — este script não decide nada sozinho, só
mede.

Reutiliza a mesma lógica de carregamento/split por sujeito de
train_activity_classifier.py (ver esse ficheiro para a justificação
detalhada de por que o split é por sujeito e não por janela aleatória).
"""

import json

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.preprocessing import LabelEncoder
from sklearn.utils.class_weight import compute_sample_weight

from train_activity_classifier import load_dataset, save_confusion_matrix_plot, split_by_subject

# Profundidade e número de árvores escolhidos deliberadamente pequenos —
# não para dar a melhor accuracy possível no backend, mas para já ficarem
# perto do que caberia embarcado via `emlearn` (regra prática do
# PROJECT_STATUS.md: ~50-100 árvores rasas, profundidade ≤4-5). Um Random
# Forest "grande e profundo" no backend não ajudaria a decisão real, que é
# sobre o que cabe na placa.
N_ESTIMATORS = 80
MAX_DEPTH = 5


def train(df, feature_cols):
    train_df, test_df, test_subjects = split_by_subject(df)

    encoder = LabelEncoder()
    y_train = encoder.fit_transform(train_df["label"])
    y_test = encoder.transform(test_df["label"])
    X_train = train_df[feature_cols]
    X_test = test_df[feature_cols]

    # Mesmo tratamento de desequilíbrio de classes que o script XGBoost,
    # para a comparação entre os dois modelos ser justa (mesmos dados,
    # mesmos pesos, mesmo split).
    sample_weight = compute_sample_weight("balanced", y_train)

    model = RandomForestClassifier(
        n_estimators=N_ESTIMATORS,
        max_depth=MAX_DEPTH,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train, sample_weight=sample_weight)

    y_pred = model.predict(X_test)

    report = classification_report(
        y_test, y_pred, target_names=encoder.classes_, output_dict=True, zero_division=0
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
        "n_estimators": N_ESTIMATORS,
        "max_depth": MAX_DEPTH,
    }


def main():
    df, feature_cols = load_dataset()
    model, encoder, feature_cols, metrics = train(df, feature_cols)

    # scikit-learn não tem um formato nativo tipo "save_model" do XGBoost;
    # guardamos via joblib (padrão para modelos scikit-learn) para uso
    # futuro no backend/emlearn, sem depender de pickle diretamente.
    import joblib
    joblib.dump(model, "models/activity_classifier_rf.joblib")
    with open("models/activity_classifier_rf_labels.json", "w") as f:
        json.dump({"classes": encoder.classes_.tolist(), "feature_cols": feature_cols}, f, indent=2, ensure_ascii=False)

    with open("reports/activity_classifier_rf_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)

    save_confusion_matrix_plot(
        np.array(metrics["confusion_matrix"]), metrics["class_order"],
        "reports/activity_classifier_rf_confusion_matrix.png",
    )

    print(f"Random Forest — {N_ESTIMATORS} arvores, profundidade {MAX_DEPTH}")
    print(f"Accuracy (sujeitos de teste nunca vistos no treino): {metrics['accuracy']:.3f}")
    print(f"Sujeitos de treino: {metrics['train_subject_ids']}  |  Sujeitos de teste: {metrics['test_subject_ids']}")
    print(f"Janelas treino/teste: {metrics['n_train_windows']}/{metrics['n_test_windows']}")
    print()
    for cls in metrics["class_order"]:
        r = metrics["classification_report"][cls]
        print(f"  {cls:14s} precision={r['precision']:.2f} recall={r['recall']:.2f} f1={r['f1-score']:.2f} support={int(r['support'])}")


if __name__ == "__main__":
    main()
