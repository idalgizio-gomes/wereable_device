#!/usr/bin/env python3
"""Classificação de atividade em tempo real sobre o stream do IMU/PPG, usando o Random Forest treinado em ml/."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np

import storage_advanced as sa

_ML_DIR = Path(__file__).resolve().parent.parent / "ml"
if str(_ML_DIR) not in sys.path:
    sys.path.insert(0, str(_ML_DIR))

FS_HZ = 52  # taxa real do IMU, tem de bater certo com ml/synthetic_data.py
WINDOW_SECONDS = 10  # mesma janela usada no treino
WINDOW_MS = WINDOW_SECONDS * 1000
MIN_SAMPLES_PER_WINDOW = 20  # abaixo disto a janela é descartada, não classificada

HR_STALE_AFTER_S = 90  # idade máxima de uma FC reutilizada de _last_hr

ACTIVITY_ML_DISCLAIMER = (
    "Classificador treinado apenas com dados sintéticos (ver ml/README.md) "
    "— não validado clinicamente. Não usar como diagnóstico."
)

# margem entre top-1 e runner-up; abaixo disto a previsão é marcada "incerta"
UNCERTAINTY_MARGIN_THRESHOLD = 0.15

# aproximação de sessão dia/noite por hora do relógio local
DAY_SESSION_START_HOUR = 7
DAY_SESSION_END_HOUR = 22  # exclusivo

# classes do modelo (PT) -> categorias aceites por storage_advanced.py (activity_windows.activity_category, em inglês)
CLASS_TO_DB_CATEGORY = {
    "Dormir": "sleep",
    "Descanso": "rest",
    "Atividade": "activity",
    "Alimentação": "eating",
    "Higiene": "hygiene",
}

# versionamento/rollback do modelo (ver storage_advanced.py MlModelVersion)
DEFAULT_MODEL_NAME = "activity_classifier_rf"
DEFAULT_MODEL_FILE_PATH = "models/activity_classifier_rf.joblib"
DEFAULT_MODEL_LABELS_PATH = "models/activity_classifier_rf_labels.json"


class ActivityInference:
    """Acumula amostras numa janela deslizante e classifica a cada WINDOW_SECONDS completos."""

    def __init__(self) -> None:
        self._buffer: list[dict] = []
        self._model = None
        self._classes: Optional[list[str]] = None
        self._feature_cols: Optional[list[str]] = None
        self.load_error: Optional[str] = None
        self._current_block: Optional[dict] = None
        self._last_hr: Optional[float] = None
        self._last_hr_ts: Optional[float] = None  # device ts (s) da última leitura real
        self._load_model()

    def _load_model(self) -> None:
        file_path, labels_path = self._resolve_active_model_paths()
        self._load_model_from_paths(file_path, labels_path)

    def reload_active_model(self) -> bool:
        """Troca o modelo em memória em runtime, após ativação de outra versão pelo dashboard. Nunca lança."""
        file_path, labels_path = self._resolve_active_model_paths()
        return self._load_model_from_paths(file_path, labels_path)

    def _resolve_active_model_paths(self) -> tuple[str, str]:
        """Consulta sa.get_active_model_version; devolve caminhos relativos a ml/. Degrada para os fixos em qualquer falha."""
        try:
            db = sa.get_db_session()
        except Exception as exc:  # noqa: BLE001
            print(f"[ACTIVITY_INFERENCE] AVISO: nao foi possivel abrir sessao de BD "
                  f"para consultar a versao ativa do modelo ({exc}); a usar o caminho fixo")
            return DEFAULT_MODEL_FILE_PATH, DEFAULT_MODEL_LABELS_PATH

        try:
            active = sa.get_active_model_version(db, DEFAULT_MODEL_NAME)
            if active is not None:
                return active["file_path"], active["labels_path"]

            # nenhuma versão registada ainda: usa o caminho fixo e regista-o como versão "1" ativa
            try:
                sa.register_model_version(
                    db, DEFAULT_MODEL_NAME, version="1",
                    file_path=DEFAULT_MODEL_FILE_PATH, labels_path=DEFAULT_MODEL_LABELS_PATH,
                    notes="Registo automático — versão inicial já existente antes deste "
                          "sistema de versionamento (2026-08-05)",
                    activate=True,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[ACTIVITY_INFERENCE] AVISO: falha ao auto-registar a versao inicial "
                      f"do modelo em MlModelVersion ({exc}); classificacao continua a usar "
                      f"o caminho fixo, sem versionamento registado")
            return DEFAULT_MODEL_FILE_PATH, DEFAULT_MODEL_LABELS_PATH
        except Exception as exc:  # noqa: BLE001
            print(f"[ACTIVITY_INFERENCE] AVISO: erro ao consultar a versao ativa do modelo "
                  f"em BD ({exc}); a usar o caminho fixo")
            return DEFAULT_MODEL_FILE_PATH, DEFAULT_MODEL_LABELS_PATH
        finally:
            db.close()

    def _load_model_from_paths(self, file_path: str, labels_path: str) -> bool:
        """Só substitui self._model/_classes/_feature_cols depois de ambos os ficheiros carregarem com sucesso."""
        try:
            import joblib  # import tardio: só falha aqui, nunca ao importar este módulo

            model_path = _ML_DIR / file_path
            labels_full_path = _ML_DIR / labels_path
            model = joblib.load(model_path)
            with open(labels_full_path, encoding="utf-8") as f:
                labels = json.load(f)
            classes = labels["classes"]
            feature_cols = labels["feature_cols"]
        except Exception as exc:  # noqa: BLE001
            self.load_error = str(exc)
            return False

        self._model = model
        self._classes = classes
        self._feature_cols = feature_cols
        self.load_error = None
        return True

    @property
    def available(self) -> bool:
        return self._model is not None

    def current_category(self) -> Optional[str]:
        return self._current_block["cls"] if self._current_block else None

    def add_sample(self, record: dict) -> Optional[dict]:
        """Acumula um registo descodificado; devolve resultado quando a janela fecha, None enquanto acumula."""
        if not self.available:
            return None

        if record["hr"] is not None:
            self._last_hr = record["hr"]
            self._last_hr_ts = record["ts"]

        self._buffer.append(record)
        # record["ts"] está em segundos (Unix epoch), daí o *1000 para span_ms
        span_ms = (self._buffer[-1]["ts"] - self._buffer[0]["ts"]) * 1000
        if span_ms < WINDOW_MS:
            return None

        window, self._buffer = self._buffer, []
        if len(window) < MIN_SAMPLES_PER_WINDOW:
            return None
        return self._classify_window(window)

    def _classify_window(self, window: list[dict]) -> Optional[dict]:
        from features import extract_features  # ml/features.py

        hr_values = [r["hr"] for r in window if r["hr"] is not None]
        if not hr_values and self._last_hr is not None:
            # reutiliza a última FC real só enquanto não expirar (HR_STALE_AFTER_S), medido no relógio do dispositivo
            age_s = window[-1]["ts"] - self._last_hr_ts
            if age_s <= HR_STALE_AFTER_S:
                hr_values = [self._last_hr]
            else:
                return None
        elif not hr_values:
            # sem FC real nenhuma: não classifica (evita viés de FC inventada)
            return None

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

        import pandas as pd

        x = pd.DataFrame([feats])[self._feature_cols]
        pred_idx = int(self._model.predict(x)[0])
        proba = self._model.predict_proba(x)[0]
        cls = self._classes[pred_idx]
        confidence = float(proba[pred_idx])

        sorted_idx = np.argsort(proba)[::-1]
        runner_up_idx = int(sorted_idx[1]) if len(sorted_idx) > 1 else None
        runner_up_category = self._classes[runner_up_idx] if runner_up_idx is not None else None
        runner_up_confidence = float(proba[runner_up_idx]) if runner_up_idx is not None else 0.0
        confidence_margin = confidence - runner_up_confidence
        is_uncertain = confidence_margin < UNCERTAINTY_MARGIN_THRESHOLD

        now = time.time()  # relógio real do bridge, só para medir duração do bloco
        session = self._session_for(now)
        duration_flag = self._update_block(
            cls, session, window[0]["ts"], window[-1]["ts"], now, confidence,
        )

        return {
            "kind": "activity_classification",
            "category": cls,
            "db_category": CLASS_TO_DB_CATEGORY[cls],
            "confidence": confidence,
            "runner_up_category": runner_up_category,
            "runner_up_confidence": runner_up_confidence,
            "confidence_margin": confidence_margin,
            "is_uncertain": is_uncertain,
            "session": session,
            "window_start_ts": window[0]["ts"],
            "window_end_ts": window[-1]["ts"],
            "n_samples": len(window),
            "received_at": now,
            "closed_block": duration_flag,
            "disclaimer": ACTIVITY_ML_DISCLAIMER,
        }

    @staticmethod
    def _session_for(wall_clock_s: float) -> str:
        hour = time.localtime(wall_clock_s).tm_hour
        if DAY_SESSION_START_HOUR <= hour < DAY_SESSION_END_HOUR:
            return "dia"
        return "noite"

    def _update_block(
        self, cls: str, session: str, start_device_ts: int, end_device_ts: int,
        wall_clock_s: float, confidence: float,
    ) -> Optional[dict]:
        """Agrupa janelas consecutivas da mesma classe+sessão num bloco; ao mudar, fecha o anterior e avalia a duração."""
        from duration_detector import evaluate_block, explain_block  # ml/duration_detector.py

        if self._current_block is None:
            self._current_block = {
                "cls": cls, "session": session,
                "start_device_ts": start_device_ts, "end_device_ts": end_device_ts,
                "start_wall_clock_s": wall_clock_s, "end_wall_clock_s": wall_clock_s,
                "confidences": [confidence],
            }
            return None

        if cls == self._current_block["cls"] and session == self._current_block["session"]:
            self._current_block["end_device_ts"] = end_device_ts
            self._current_block["end_wall_clock_s"] = wall_clock_s
            self._current_block["confidences"].append(confidence)
            return None

        prev = self._current_block
        duration_min = (prev["end_device_ts"] - prev["start_device_ts"]) / 60.0
        is_anomaly, reason = evaluate_block(prev["session"], prev["cls"], duration_min)
        explanation = explain_block(prev["session"], prev["cls"], duration_min, is_anomaly, reason)
        start_local = time.localtime(prev["start_wall_clock_s"])
        end_local = time.localtime(prev["end_wall_clock_s"])
        closed = {
            "cls": prev["cls"],
            "db_category": CLASS_TO_DB_CATEGORY[prev["cls"]],
            "session": prev["session"],
            "duration_min": duration_min,
            "is_anomaly": is_anomaly,
            "reason": reason,
            "explanation": explanation,
            "confidence": float(np.mean(prev["confidences"])),
            "start_wall_clock_s": prev["start_wall_clock_s"],
            "start_time_minutes": start_local.tm_hour * 60 + start_local.tm_min,
            "end_time_minutes": end_local.tm_hour * 60 + end_local.tm_min,
        }
        self._current_block = {
            "cls": cls, "session": session,
            "start_device_ts": start_device_ts, "end_device_ts": end_device_ts,
            "start_wall_clock_s": wall_clock_s, "end_wall_clock_s": wall_clock_s,
            "confidences": [confidence],
        }
        return closed
