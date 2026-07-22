#!/usr/bin/env python3
"""activity_inference.py — Classificação de atividade em tempo real sobre o
stream real do IMU/PPG, usando o Random Forest já treinado em `ml/`.

CONTEXTO (2026-07-20): até agora o pipeline de ML (`ml/`) existia treinado e
avaliado, mas nunca era invocado fora dessa pasta — o dashboard mostrava
sempre dados simulados (`chore(dashboard): dados simulados do dia ...`),
nunca uma classificação real. Este módulo fecha essa lacuna, seguindo o
mesmo padrão degradável já usado por `orm_persistence.py`/`notifications.py`:
qualquer falha aqui (scikit-learn não instalado, modelo em falta, etc.)
nunca deve impedir o streaming BLE nem o resto do bridge — só desativa a
classificação.

AVISO ÉTICO, REPETIDO DE PROPÓSITO (ver também ml/README.md e a UI do
dashboard, que mostra `ACTIVITY_ML_DISCLAIMER` para cada resultado): o
classificador (`ml/models/activity_classifier_rf.joblib`) foi treinado
inteiramente sobre dados SINTÉTICOS (`ml/synthetic_data.py`) — nunca
validado com comportamento humano real. O resultado devolvido aqui não é
uma medição clínica; é um sinal experimental, mostrado ao cuidador sempre
com aviso explícito, e serve também para acumular dados reais rotulados por
categoria prevista (não rótulo verdadeiro) para uma futura validação.

O detetor de duração (`ml/duration_detector.py`, regra determinística, não
treinada) é aplicado sobre os blocos de classes consecutivas — mesma lógica
de agrupamento usada em `ml/combined_pipeline_report.py`, adaptada para
avaliar bloco a bloco à medida que chegam, não sobre um dataset já completo.
Herda a mesma limitação já documentada nesse ficheiro: os limites
[d_min, d_max] usados são os do próprio gerador sintético, não uma
calibração feita sobre dados reais.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np

_ML_DIR = Path(__file__).resolve().parent.parent / "ml"
if str(_ML_DIR) not in sys.path:
    sys.path.insert(0, str(_ML_DIR))

FS_HZ = 52  # taxa real do IMU — tem de bater certo com ml/synthetic_data.py
WINDOW_SECONDS = 10  # mesma janela usada no treino (ver ml/synthetic_data.py)
WINDOW_MS = WINDOW_SECONDS * 1000
# Uma janela de 10s a 52Hz devia ter ~520 amostras; com perdas de pacotes
# BLE (notify() sem confirmação, ver ble_bridge.py) isso nunca é garantido.
# Abaixo deste mínimo a janela é descartada (não classificada) em vez de
# alimentar o classificador com um sinal demasiado esparso para ser fiável.
MIN_SAMPLES_PER_WINDOW = 20

# Acima desta idade (segundos, relógio do dispositivo), uma leitura de FC
# guardada em _last_hr deixa de ser reutilizada — ver bug real corrigido em
# _classify_window (2026-07-21). Em funcionamento normal, measureSpo2() do
# firmware corre a cada SPO2_INTERVAL_MS=30s (ver ml/../src/Ppg/Ppg.cpp) e
# devolve HR como subproduto sempre que há dedo/pulso; 90s (3x esse
# intervalo) dá margem para uma medição falhada isolada sem reagir de
# imediato a um único soluço, mas não deixa uma FC antiga a ser tratada
# como atual minutos depois de o dispositivo ter deixado de medir.
HR_STALE_AFTER_S = 90

ACTIVITY_ML_DISCLAIMER = (
    "Classificador treinado apenas com dados sintéticos (ver ml/README.md) "
    "— não validado clinicamente. Não usar como diagnóstico."
)

# Aproximação de "sessão dia/noite" por hora do relógio local do bridge —
# ml/synthetic_data.py define sessões por duração (16h dia + 8h noite), não
# por hora real do dia; isto é a nossa melhor aproximação ao mundo real, não
# um valor extraído do gerador. Documentado como limitação assumida.
DAY_SESSION_START_HOUR = 7
DAY_SESSION_END_HOUR = 22  # exclusivo

# Mapa entre as 5 classes do classificador (PT, ver
# ml/models/activity_classifier_rf_labels.json) e as categorias aceites pelo
# esquema SQL de bridge/storage_advanced.py (activity_windows.activity_category,
# CheckConstraint em inglês) — os dois vocabulários nasceram em rotinas
# diferentes do projeto e nunca foram unificados.
CLASS_TO_DB_CATEGORY = {
    "Dormir": "sleep",
    "Descanso": "rest",
    "Atividade": "activity",
    "Alimentação": "eating",
    "Higiene": "hygiene",
}


class ActivityInference:
    """Acumula amostras cruas do IMU/PPG numa janela deslizante (tumbling,
    não sobreposta) e, a cada `WINDOW_SECONDS` completos, classifica com o
    Random Forest treinado em `ml/` e aplica o detetor de duração sobre a
    sequência de blocos resultante."""

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
        try:
            import joblib  # import tardio: só falha aqui, nunca ao importar este módulo

            model_path = _ML_DIR / "models" / "activity_classifier_rf.joblib"
            labels_path = _ML_DIR / "models" / "activity_classifier_rf_labels.json"
            self._model = joblib.load(model_path)
            with open(labels_path, encoding="utf-8") as f:
                labels = json.load(f)
            self._classes = labels["classes"]
            self._feature_cols = labels["feature_cols"]
        except Exception as exc:  # noqa: BLE001 - degradação silenciosa, ver docstring do módulo
            self.load_error = str(exc)
            self._model = None

    @property
    def available(self) -> bool:
        return self._model is not None

    def current_category(self) -> Optional[str]:
        """Última classe (PT) em curso no bloco aberto, ou None se ainda
        não houve nenhuma classificação nesta sessão de ligação — usado
        pelo bridge para saber o que a IA achava no momento em que o
        cuidador corrigiu manualmente (ver cmd "correct_activity" em
        ble_bridge.py)."""
        return self._current_block["cls"] if self._current_block else None

    def add_sample(self, record: dict) -> Optional[dict]:
        """Acumula um registo já descodificado (ver decode_full_plain em
        ble_bridge.py: ts, ax..gz, hr, ...). Devolve um dict de resultado
        quando uma janela de WINDOW_SECONDS fica completa, ou None enquanto
        ainda está a acumular (ou se a inferência estiver indisponível)."""
        if not self.available:
            return None

        if record["hr"] is not None:
            self._last_hr = record["hr"]
            self._last_hr_ts = record["ts"]

        self._buffer.append(record)
        # BUG REAL corrigido (2026-07-20, apanhado em teste com hardware real):
        # record["ts"] é o "device_timestamp" gravado por storage.py — Unix
        # epoch em SEGUNDOS (ver schema.sql "Unix timestamp (segundos)" e
        # storage.py::insert_record, que grava record["ts"] tal e qual em
        # device_timestamp), não millis() nem já em ms como este módulo
        # assumia. Sem o *1000, span_ms nunca atingia WINDOW_MS=10000 num
        # stream real (precisaria de ~2.8h de span) — a janela nunca fechava
        # e activity_classification nunca era emitido, apesar de
        # sensor_records estar a encher normalmente. Confirmado em
        # bridge/tests/test_activity_inference.py, que alimentava ts já em
        # ms (consistente com este bug, não com o formato real) — os testes
        # unitários passavam apesar do bug porque partilhavam a mesma
        # assunção errada; só o teste com o dispositivo real o expôs.
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
            # BUG REAL corrigido (2026-07-21, achado com hardware real):
            # este ramo usava self._last_hr indefinidamente, sem nunca
            # expirar — se a FC parasse de chegar (pulso retirado, sensor
            # solto, sinal perdido), o último valor real continuava a ser
            # reutilizado para sempre, como se fosse uma leitura atual.
            # Combinado com o classificador nunca comunicar "sem sinal",
            # isto produzia classificações confiantes sobre uma pessoa que
            # pode já nem ter o dispositivo vestido. Agora só se usa
            # self._last_hr enquanto a idade dessa leitura (medida no
            # relógio do próprio dispositivo, não no relógio de parede,
            # para não repetir o bug de mistura de relógios já corrigido
            # noutro sítio deste ficheiro) não ultrapassar HR_STALE_AFTER_S.
            age_s = window[-1]["ts"] - self._last_hr_ts
            if age_s <= HR_STALE_AFTER_S:
                hr_values = [self._last_hr]
            else:
                return None
        elif not hr_values:
            # BUG REAL corrigido (2026-07-21, achado com hardware real):
            # quando nunca chegou nenhuma leitura de FC, este ramo
            # alimentava o classificador com um valor inventado (70bpm,
            # "plausível de repouso"). Isso introduz um viés real: a FC é
            # uma feature do modelo, e um valor de repouso empurra
            # sistematicamente a previsão para classes de baixa atividade
            # (Descanso/Dormir) mesmo quando o movimento real não
            # corresponde a isso. Confirmado ao vivo: utilizador com a
            # placa no pulso o tempo todo, SpO2 válido (dedo/contacto
            # confirmado pelo firmware), mas measureSpo2() devolveu hr=0
            # (vHr=0 do algoritmo Maxim) nessa janela — e a app mostrava
            # "Dormir" com confiança, calculada sobre uma FC 100%
            # fabricada. Em vez de classificar sobre dados inventados,
            # esta janela fica por classificar (mesmo tratamento que uma
            # janela demasiado esparsa — ver add_sample) até chegar pelo
            # menos uma leitura real de FC nesta sessão.
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

        now = time.time()  # relógio real do bridge — ver storage.py (record["ts"]
        # é um contador relativo do dispositivo, não sincronizado a epoch real;
        # usado aqui só para medir a DURAÇÃO do bloco, nunca como hora absoluta)
        session = self._session_for(now)
        duration_flag = self._update_block(
            cls, session, window[0]["ts"], window[-1]["ts"], now, confidence,
        )

        return {
            "kind": "activity_classification",
            "category": cls,
            "db_category": CLASS_TO_DB_CATEGORY[cls],
            "confidence": confidence,
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
        """Agrupa janelas consecutivas da mesma classe+sessão num bloco.
        Quando a classe (ou a sessão) muda, fecha o bloco anterior e aplica
        `duration_detector.evaluate_block` sobre a sua duração — devolve o
        veredito do bloco FECHADO (None enquanto o bloco atual continua),
        pronto a persistir em activity_windows (start_time/end_time em
        minutos desde a meia-noite local, como o esquema espera)."""
        from duration_detector import evaluate_block  # ml/duration_detector.py

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
        # device_ts em segundos (ver correção acima em add_sample) -> minutos = /60, não /60000.
        duration_min = (prev["end_device_ts"] - prev["start_device_ts"]) / 60.0
        is_anomaly, reason = evaluate_block(prev["session"], prev["cls"], duration_min)
        start_local = time.localtime(prev["start_wall_clock_s"])
        end_local = time.localtime(prev["end_wall_clock_s"])
        closed = {
            "cls": prev["cls"],
            "db_category": CLASS_TO_DB_CATEGORY[prev["cls"]],
            "session": prev["session"],
            "duration_min": duration_min,
            "is_anomaly": is_anomaly,
            "reason": reason,
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
