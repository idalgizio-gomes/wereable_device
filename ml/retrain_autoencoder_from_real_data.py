#!/usr/bin/env python3
"""retrain_autoencoder_from_real_data.py — retreino periódico do LSTM
Autoencoder (passo 2, ver README.md) sobre dados REAIS acumulados pelo
bridge (`bridge/storage_advanced.py::SensorRecord`), em vez dos dados
sintéticos usados em `train_lstm_autoencoder.py`.

PORQUÊ SÓ O AUTOENCODER, NÃO O CLASSIFICADOR (2026-07-20, ver discussão em
PROJECT_STATUS.md): o autoencoder é NÃO SUPERVISIONADO — só precisa de
sequências "normais" para aprender, sem rótulo humano nenhum. O classificador
de atividade (Random Forest) é SUPERVISIONADO — precisa de um rótulo real
("isto foi Higiene") para cada janela, que hoje não existe (o esquema
`activity_windows` guarda `confidence`, não um rótulo verdadeiro corrigido
por um cuidador). Retreinar o classificador sobre dados reais sem rótulos
não teria alvo nenhum para aprender; este script não tenta fazer isso.

ASSUNÇÃO EXPLÍCITA (não escondida): trata TODA a janela real acumulada como
"normal" — não há filtragem de anomalias reais antes do fine-tuning, porque
não existe ainda um oráculo para as distinguir de rotina normal. Isto é a
mesma limitação já documentada no resto de `ml/`: útil para adaptar o modelo
ao "jeito de se mexer" real da pessoa (amplitude, ritmo, ruído do sensor
real — nunca medidos, só estimados, ver README.md), mas não uma validação
clínica, e um período com comportamento genuinamente atípico prolongado
podia ser aprendido como "normal" por engano. Fica registado como limitação
conhecida, não resolvida aqui.

GUARDA DE DADOS MÍNIMOS: com poucas subsequências reais, um fine-tuning
sobrescreveria os pesos aprendidos com milhares de sequências sintéticas
por uma amostra demasiado pequena para generalizar — teria mais probabilidade
de PIORAR o modelo do que de o adaptar. Por isso este script recusa-se a
retreinar abaixo de MIN_REAL_SUBSEQUENCES, e diz exatamente porquê.

EXECUÇÃO: script local, não um cron cloud como demo-data.yml — precisa da
base de dados REAL do bridge (`bridge/carewear_history.db` por omissão, ou
DATABASE_URL), que só existe na máquina onde o bridge corre com hardware
ligado. Pensado para ser corrido manualmente ou agendado localmente (Task
Scheduler/cron do sistema operativo), nunca em GitHub Actions.

Uso:
    cd ml
    python retrain_autoencoder_from_real_data.py [--days N] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

_BRIDGE_DIR = Path(__file__).resolve().parent.parent / "bridge"
if str(_BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(_BRIDGE_DIR))

# storage_advanced.py resolve DATABASE_URL (se não definida) para o caminho
# RELATIVO "sqlite:///./carewear.db" — correto quando o bridge corre com
# cwd=bridge/, mas ESTE script corre normalmente a partir de ml/ (ver
# docstring "Uso" abaixo). Sem isto, uma execução sem DATABASE_URL explícita
# criaria/leria silenciosamente ml/carewear.db (sempre vazia), nunca a BD
# real do bridge — um bug de "0 dados encontrados" persistente e difícil de
# diagnosticar. os.environ.setdefault: se o utilizador já definiu
# DATABASE_URL (ex.: PostgreSQL em produção), essa escolha prevalece.
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(_BRIDGE_DIR / 'carewear.db').as_posix()}")

from features import extract_features  # ml/features.py
from train_lstm_autoencoder import SEQ_LEN, SEQ_STEP, build_subsequences, reconstruction_error  # noqa: E402

FS_HZ = 52
WINDOW_SECONDS = 10
WINDOW_SAMPLES = FS_HZ * WINDOW_SECONDS

# Abaixo disto, recusa-se a retreinar (ver docstring do módulo — "GUARDA DE
# DADOS MÍNIMOS"). SEQ_LEN=12 janelas de 10s = 2min; 20 subsequências reais
# são só ~40min de dados reais COM SEQ_STEP=6 de sobreposição — um limiar
# deliberadamente baixo porque não há ainda experiência real de quanto é
# "suficiente"; ajustar para cima à medida que houver mais dados reais.
MIN_REAL_SUBSEQUENCES = 20


def _windows_from_sensor_records(records: list) -> list[dict]:
    """Agrupa registos crus (ordenados por timestamp_utc) em janelas não
    sobrepostas de WINDOW_SECONDS e extrai features de cada uma — mesma
    lógica de bridge/activity_inference.py::add_sample, mas em lote sobre
    histórico já persistido em vez de ao vivo sobre o stream BLE."""
    feature_windows = []
    buffer = []
    last_hr = None
    for r in records:
        if r.heart_rate is not None:
            last_hr = r.heart_rate
        buffer.append(r)
        span_ms = (buffer[-1].timestamp_utc - buffer[0].timestamp_utc) * 1000
        if span_ms < WINDOW_SECONDS * 1000:
            continue
        window, buffer = buffer, []
        if len(window) < WINDOW_SAMPLES * 0.1:  # janela demasiado esparsa — descarta
            continue
        hr_values = [w.heart_rate for w in window if w.heart_rate is not None]
        if not hr_values:
            hr_values = [last_hr] if last_hr is not None else [70.0]
        feat_window = {
            "accel_x": np.array([w.accel_x for w in window], dtype=float),
            "accel_y": np.array([w.accel_y for w in window], dtype=float),
            "accel_z": np.array([w.accel_z for w in window], dtype=float),
            "gyro_x": np.array([w.gyro_x for w in window], dtype=float),
            "gyro_y": np.array([w.gyro_y for w in window], dtype=float),
            "gyro_z": np.array([w.gyro_z for w in window], dtype=float),
            "hr": np.array(hr_values, dtype=float),
        }
        feature_windows.append(extract_features(feat_window))
    return feature_windows


def load_real_feature_matrix(days: int) -> np.ndarray:
    """Lê SensorRecord reais dos últimos `days` dias (bridge/storage_advanced.py)
    e devolve a matriz [n_windows, n_features], na mesma ordem de
    features.extract_features() (compatível com o scaler/modelo já
    treinados — ver ml/README.md, get_feature_names())."""
    import storage_advanced as sa  # bridge/storage_advanced.py

    # Idempotente (CREATE TABLE IF NOT EXISTS internamente, ver
    # sa.create_all_tables) — necessário quando este script é o primeiro a
    # tocar numa BD nova/vazia (ex.: testes), já que ao contrário do bridge
    # normal (que corre orm_persistence.OrmPersistence() no arranque) este
    # script só faz leitura e nunca passou por esse bootstrap.
    sa.create_all_tables()
    session = sa.get_db_session()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_ts = int(cutoff.timestamp())
        records = (
            session.query(sa.SensorRecord)
            .filter(sa.SensorRecord.timestamp_utc >= cutoff_ts)
            .order_by(sa.SensorRecord.device_id, sa.SensorRecord.timestamp_utc)
            .all()
        )
    finally:
        session.close()

    if not records:
        return np.empty((0, 0))

    # Agrupar por device_id: registos de dispositivos diferentes não devem
    # ser encadeados na mesma janela deslizante (span_ms sem sentido entre
    # dois dispositivos distintos).
    by_device: dict[int, list] = {}
    for r in records:
        by_device.setdefault(r.device_id, []).append(r)

    feature_dicts = []
    for device_records in by_device.values():
        feature_dicts += _windows_from_sensor_records(device_records)

    if not feature_dicts:
        return np.empty((0, 0))

    feature_names = list(feature_dicts[0].keys())
    matrix = np.array([[fd[name] for name in feature_names] for fd in feature_dicts])
    return matrix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="janela de dados reais a usar (dias)")
    parser.add_argument("--dry-run", action="store_true", help="só reporta quantos dados existem, não retreina")
    parser.add_argument("--epochs", type=int, default=10, help="épocas de fine-tuning (poucas — ver docstring)")
    args = parser.parse_args()

    matrix = load_real_feature_matrix(args.days)
    n_windows = matrix.shape[0]
    print(f"[RETREINO] {n_windows} janelas reais de {args.days} dias encontradas na BD do bridge.")

    if n_windows < SEQ_LEN:
        print(f"[RETREINO] menos de SEQ_LEN={SEQ_LEN} janelas — impossível construir "
              f"nenhuma subsequência. Nada a fazer (precisa de mais dias de uso real).")
        return

    dummy_mask = np.zeros(n_windows, dtype=bool)  # toda a janela real tratada como "normal" (ver docstring)
    X_real, _y = build_subsequences(matrix, dummy_mask)
    n_sub = X_real.shape[0]
    print(f"[RETREINO] {n_sub} subsequências reais construídas (SEQ_LEN={SEQ_LEN}, SEQ_STEP={SEQ_STEP}).")

    if args.dry_run:
        print("[RETREINO] --dry-run: a terminar sem retreinar.")
        return

    if n_sub < MIN_REAL_SUBSEQUENCES:
        print(f"[RETREINO] RECUSADO: {n_sub} < MIN_REAL_SUBSEQUENCES={MIN_REAL_SUBSEQUENCES}. "
              f"Fine-tuning com tão poucos dados reais arrisca desaprender o padrão sintético "
              f"em vez de o adaptar. Sem alterações ao modelo — volta a correr quando houver "
              f"mais dias de uso real (ver PROJECT_STATUS.md).")
        return

    import joblib
    from tensorflow import keras

    model = keras.models.load_model("models/lstm_autoencoder.keras")
    scaler = joblib.load("models/lstm_autoencoder_scaler.joblib")

    n_windows_flat, n_features = matrix.shape
    scaled_matrix = scaler.transform(matrix)
    X_real_scaled, _ = build_subsequences(scaled_matrix, dummy_mask)

    errors_before = reconstruction_error(model, X_real_scaled)

    keras.utils.set_random_seed(42)
    history = model.fit(
        X_real_scaled, X_real_scaled,
        epochs=args.epochs, batch_size=min(32, n_sub), shuffle=True, verbose=0,
    )

    errors_after = reconstruction_error(model, X_real_scaled)

    model.save("models/lstm_autoencoder.keras")

    report = {
        "retrained_at_days_window": args.days,
        "n_real_windows": int(n_windows_flat),
        "n_real_subsequences": int(n_sub),
        "epochs": args.epochs,
        "final_train_loss": float(history.history["loss"][-1]),
        "mean_reconstruction_error_before": float(np.mean(errors_before)),
        "mean_reconstruction_error_after": float(np.mean(errors_after)),
        "note": (
            "Fine-tuning sobre dados REAIS (não sintéticos) do bridge, tratando toda "
            "a janela real como 'normal' (sem filtragem de anomalias — ver docstring "
            "do módulo). O limiar de deteção (detection_threshold_mse em "
            "lstm_autoencoder_metrics.json) NÃO foi recalibrado aqui — continua o "
            "valor calibrado sobre dados sintéticos. Não é uma validação clínica."
        ),
    }
    with open("reports/lstm_autoencoder_real_retrain.json", "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"[RETREINO] concluído. Erro médio de reconstrução: {report['mean_reconstruction_error_before']:.4f} "
          f"-> {report['mean_reconstruction_error_after']:.4f} (relatório completo em "
          f"reports/lstm_autoencoder_real_retrain.json)")


if __name__ == "__main__":
    main()
