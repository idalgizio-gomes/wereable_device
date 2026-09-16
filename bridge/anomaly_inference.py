#!/usr/bin/env python3
"""Deteção de anomalias comportamentais em tempo real via LSTM Autoencoder (ml/train_lstm_autoencoder.py).

TensorFlow é uma dependência PESADA e deliberadamente ausente de
requirements_db.txt (ver comentário lá) — quem só quer o streaming BLE não
precisa de a instalar. Por isso este módulo importa tensorflow/joblib de
forma tardia (dentro de _load_model), exactamente como activity_inference.py
faz com pandas/sklearn: se TensorFlow não estiver instalado, `available`
fica False e o resto do bridge continua a funcionar sem deteção de
anomalias, nunca travando o arranque.

Janela de 10s + reaproveitamento de FC: mesma lógica de
activity_inference.py::add_sample/_classify_window, duplicada aqui (não
partilhada) para manter os dois classificadores independentes — um
consumidor a menos não deve conseguir travar o outro. Chaveado por
device_id (dict de buffers) mesmo só havendo um dispositivo ligado de
cada vez neste bridge: preparação explícita para um bridge multi-dispositivo
futuro, sem custo extra no ponto de chamada atual.

Escalabilidade: `model.predict()` (Keras) é mais pesado que o RandomForest
do classificador de atividade — o chamador (ble_bridge.py) deve correr
`add_sample()` via `asyncio.to_thread(...)` para não bloquear o loop de
eventos enquanto vários dispositivos estão ligados. Este módulo em si é
síncrono (mesma escolha de activity_inference.py), só o ponto de chamada
muda.
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path
from typing import Optional

import numpy as np

import storage_advanced as sa

_ML_DIR = Path(__file__).resolve().parent.parent / "ml"
if str(_ML_DIR) not in sys.path:
    sys.path.insert(0, str(_ML_DIR))

FS_HZ = 52
WINDOW_SECONDS = 10
WINDOW_MS = WINDOW_SECONDS * 1000
MIN_SAMPLES_PER_WINDOW = 20
HR_STALE_AFTER_S = 90

MODEL_NAME = "lstm_autoencoder"
DEFAULT_MODEL_FILE_PATH = "models/lstm_autoencoder.keras"
DEFAULT_LABELS_PATH = "models/lstm_autoencoder_labels.json"
DEFAULT_SCALER_PATH = "models/lstm_autoencoder_scaler.joblib"
DEFAULT_METRICS_PATH = "reports/lstm_autoencoder_metrics.json"  # só a origem do limiar por omissão

MAX_EPISODE_S = 30 * 60  # fecha e reabre um episódio "eterno" (ex.: sensor preso) em vez de o deixar crescer sem limite

ANOMALY_ML_DISCLAIMER = (
    "Autoencoder treinado apenas com dados sintéticos (ver ml/README.md) "
    "— não validado clinicamente. Não usar como diagnóstico."
)


def _severity_for_ratio(ratio: float) -> str:
    """Heurística simples score/limiar -> severidade; não validada clinicamente."""
    if ratio < 1.5:
        return "minor"
    if ratio < 2.5:
        return "moderate"
    return "severe"


class AnomalyInference:
    """Acumula amostras cruas numa janela deslizante de 10s POR DISPOSITIVO
    (mesma janela de activity_inference.py), extrai features, mantém um
    buffer de SEQ_LEN janelas escaladas e pontua a cada janela nova assim
    que o buffer enche. Agrupa janelas CONSECUTIVAS acima do limiar num
    único episódio (evita 1 linha por cada 10s — ver
    storage_advanced.py::AnomalyDetection, desenhada para 1 linha por
    episódio fechado)."""

    def __init__(self) -> None:
        self._model = None
        self._scaler = None
        self._feature_names: Optional[list[str]] = None
        self._seq_len: int = 12
        self._threshold: Optional[float] = None
        self._model_version: Optional[str] = None
        self.load_error: Optional[str] = None

        self._record_buffers: dict[int, list[dict]] = {}
        self._last_hr: dict[int, float] = {}
        self._last_hr_ts: dict[int, float] = {}
        self._seq_buffers: dict[int, deque] = {}
        self._episodes: dict[int, dict] = {}

        self._load_model()

    def _load_model(self) -> None:
        file_path, labels_path, scaler_path, threshold, version = self._resolve_active_model()
        self._load_model_from_paths(file_path, labels_path, scaler_path, threshold, version)

    def reload_active_model(self) -> bool:
        """Troca o modelo em memória em runtime (mesmo mecanismo de activity_inference.py). Nunca lança."""
        file_path, labels_path, scaler_path, threshold, version = self._resolve_active_model()
        return self._load_model_from_paths(file_path, labels_path, scaler_path, threshold, version)

    def _resolve_active_model(self) -> tuple[str, str, str, Optional[float], Optional[str]]:
        """Consulta sa.get_active_model_version(MODEL_NAME); o limiar/versão vêm de metrics_json.
        Degrada para os caminhos/limiar fixos em qualquer falha (arranque a frio, sem versão registada)."""
        try:
            db = sa.get_db_session()
        except Exception as exc:  # noqa: BLE001
            print(f"[ANOMALY_INFERENCE] AVISO: nao foi possivel abrir sessao de BD "
                  f"para consultar a versao ativa do modelo ({exc}); a usar os caminhos fixos")
            return DEFAULT_MODEL_FILE_PATH, DEFAULT_LABELS_PATH, DEFAULT_SCALER_PATH, None, None

        try:
            active = sa.get_active_model_version(db, MODEL_NAME)
            if active is not None:
                threshold = (active.get("metrics") or {}).get("detection_threshold_mse")
                return (
                    active["file_path"], active["labels_path"],
                    active.get("extra_path") or DEFAULT_SCALER_PATH, threshold, active["version"],
                )

            # nenhuma versão registada ainda: usa os caminhos fixos e regista-os como versão "1" ativa
            threshold = self._read_default_threshold()
            try:
                sa.register_model_version(
                    db, MODEL_NAME, version="1",
                    file_path=DEFAULT_MODEL_FILE_PATH, labels_path=DEFAULT_LABELS_PATH,
                    extra_path=DEFAULT_SCALER_PATH,
                    metrics={"detection_threshold_mse": threshold} if threshold is not None else None,
                    notes="Registo automático — versão inicial já existente antes deste "
                          "sistema de versionamento (2026-09-15)",
                    activate=True,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[ANOMALY_INFERENCE] AVISO: falha ao auto-registar a versao inicial "
                      f"do autoencoder em MlModelVersion ({exc}); deteccao continua a usar "
                      f"os caminhos fixos, sem versionamento registado")
            return DEFAULT_MODEL_FILE_PATH, DEFAULT_LABELS_PATH, DEFAULT_SCALER_PATH, threshold, "1"
        except Exception as exc:  # noqa: BLE001
            print(f"[ANOMALY_INFERENCE] AVISO: erro ao consultar a versao ativa do autoencoder "
                  f"em BD ({exc}); a usar os caminhos fixos")
            return DEFAULT_MODEL_FILE_PATH, DEFAULT_LABELS_PATH, DEFAULT_SCALER_PATH, None, None
        finally:
            db.close()

    @staticmethod
    def _read_default_threshold() -> Optional[float]:
        import json
        try:
            with open(_ML_DIR / DEFAULT_METRICS_PATH, encoding="utf-8") as f:
                return json.load(f)["detection_threshold_mse"]
        except Exception:  # noqa: BLE001 - ficheiro pode nao existir num checkout minimo
            return None

    def _load_model_from_paths(
        self, file_path: str, labels_path: str, scaler_path: str,
        threshold: Optional[float], version: Optional[str],
    ) -> bool:
        """Só substitui o estado em memória depois de TODOS os artefactos carregarem com sucesso."""
        try:
            import json

            import joblib
            from tensorflow import keras  # import tardio: só falha aqui, nunca ao importar este módulo

            model = keras.models.load_model(_ML_DIR / file_path)
            scaler = joblib.load(_ML_DIR / scaler_path)
            with open(_ML_DIR / labels_path, encoding="utf-8") as f:
                labels = json.load(f)
            feature_names = labels["feature_names"]
            seq_len = labels.get("seq_len", 12)
            if threshold is None:
                threshold = self._read_default_threshold()
            if threshold is None:
                raise ValueError("limiar de deteccao (detection_threshold_mse) indisponivel")
        except Exception as exc:  # noqa: BLE001
            self.load_error = str(exc)
            return False

        self._model = model
        self._scaler = scaler
        self._feature_names = feature_names
        self._seq_len = seq_len
        self._threshold = float(threshold)
        self._model_version = version
        self._record_buffers.clear()
        self._seq_buffers.clear()
        self._episodes.clear()
        self.load_error = None
        return True

    @property
    def available(self) -> bool:
        return self._model is not None

    def add_sample(self, device_id: int, record: dict) -> Optional[dict]:
        """Acumula um registo descodificado por dispositivo; devolve uma pontuação nova
        quando uma janela de 10s fecha, None enquanto acumula. Potencialmente pesada
        (model.predict, uma vez a cada ~10s por dispositivo) — o chamador deve correr
        isto via asyncio.to_thread(...), nunca diretamente no loop de eventos do bridge."""
        if not self.available:
            return None

        if record["hr"] is not None:
            self._last_hr[device_id] = record["hr"]
            self._last_hr_ts[device_id] = record["ts"]

        buf = self._record_buffers.setdefault(device_id, [])
        buf.append(record)
        span_ms = (buf[-1]["ts"] - buf[0]["ts"]) * 1000
        if span_ms < WINDOW_MS:
            return None

        window, self._record_buffers[device_id] = buf, []
        if len(window) < MIN_SAMPLES_PER_WINDOW:
            return None
        return self._score_window(device_id, window)

    def _score_window(self, device_id: int, window: list[dict]) -> Optional[dict]:
        from features import extract_features  # ml/features.py

        hr_values = [r["hr"] for r in window if r["hr"] is not None]
        if not hr_values:
            last_hr = self._last_hr.get(device_id)
            last_hr_ts = self._last_hr_ts.get(device_id)
            if last_hr is not None and (window[-1]["ts"] - last_hr_ts) <= HR_STALE_AFTER_S:
                hr_values = [last_hr]
            else:
                return None  # sem FC real nenhuma: não pontua (evita viés de FC inventada)

        feat_window = {
            "accel_x": np.array([r["ax"] for r in window], dtype=float),
            "accel_y": np.array([r["ay"] for r in window], dtype=float),
            "accel_z": np.array([r["az"] for r in window], dtype=float),
            "gyro_x": np.array([r["gx"] for r in window], dtype=float),
            "gyro_y": np.array([r["gy"] for r in window], dtype=float),
            "gyro_z": np.array([r["gz"] for r in window], dtype=float),
            "hr": np.array(hr_values, dtype=float),
        }
        feats = extract_features(feat_window)

        row = np.array([[feats[name] for name in self._feature_names]], dtype=float)
        scaled = self._scaler.transform(row)[0]

        seq_buf = self._seq_buffers.setdefault(device_id, deque(maxlen=self._seq_len))
        seq_buf.append(scaled)
        if len(seq_buf) < self._seq_len:
            return None  # ainda a acumular contexto (2 min) para este dispositivo

        subseq = np.stack(seq_buf)[np.newaxis, :, :]  # [1, SEQ_LEN, n_features]
        recon = self._model.predict(subseq, verbose=0)
        score = float(np.mean(np.square(subseq - recon)))
        is_anomaly = score > self._threshold

        window_start_ts = window[0]["ts"]
        window_end_ts = window[-1]["ts"]
        closed_episode = self._update_episode(device_id, is_anomaly, score, window_start_ts, window_end_ts)

        return {
            "kind": "anomaly_score",
            "device_id": device_id,
            "score": score,
            "threshold": self._threshold,
            "is_anomaly": is_anomaly,
            "window_start_ts": window_start_ts,
            "window_end_ts": window_end_ts,
            "closed_episode": closed_episode,
            "disclaimer": ANOMALY_ML_DISCLAIMER,
        }

    def _update_episode(
        self, device_id: int, is_anomaly: bool, score: float,
        window_start_ts: float, window_end_ts: float,
    ) -> Optional[dict]:
        episode = self._episodes.get(device_id)

        if is_anomaly:
            if episode is None:
                self._episodes[device_id] = {
                    "start_ts": window_start_ts, "end_ts": window_end_ts, "max_score": score,
                }
                return None
            episode["end_ts"] = window_end_ts
            episode["max_score"] = max(episode["max_score"], score)
            if (episode["end_ts"] - episode["start_ts"]) < MAX_EPISODE_S:
                return None
            # episódio longo demais (ex.: sensor preso numa leitura estranha) — fecha e reabre
            del self._episodes[device_id]
            return self._episode_to_dict(episode)

        if episode is None:
            return None  # não estava em anomalia, nada a fechar
        del self._episodes[device_id]
        return self._episode_to_dict(episode)

    def _episode_to_dict(self, episode: dict) -> dict:
        ratio = episode["max_score"] / self._threshold if self._threshold else 0.0
        duration_min = (episode["end_ts"] - episode["start_ts"]) / 60.0
        return {
            "detector": "lstm_autoencoder",
            "anomaly_category": "padrao_temporal_atipico",
            "score": episode["max_score"],
            "threshold_used": self._threshold,
            "window_start_ts": episode["start_ts"],
            "window_end_ts": episode["end_ts"],
            "description": (
                f"Padrão de movimento/rotina fora do habitual durante {duration_min:.1f} min "
                f"(erro de reconstrução {episode['max_score']:.3f}, limiar {self._threshold:.3f})."
            ),
            "severity": _severity_for_ratio(ratio),
            "model_version": self._model_version,
        }
