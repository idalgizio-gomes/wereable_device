"""Avaliação de sinais vitais (FC/SpO2) contra os limiares do paciente (default ou definidos
pelo cuidador via storage_advanced.set_thresholds). Regra determinística, sem estado — quem
decide se um alerta é difundido é o chamador (ble_bridge.py).

Também aloja a escada de severidade (RF-07) e o detetor de não-uso do dispositivo (RF-05)."""

from __future__ import annotations

import math
import time
from typing import Optional


def evaluate_hr(hr: Optional[float], thresholds: dict) -> Optional[dict]:
    """Alerta se hr estiver fora de [heart_rate_min, heart_rate_max], senão None."""
    if hr is None:
        return None
    lo, hi = thresholds.get("heart_rate_min"), thresholds.get("heart_rate_max")
    if lo is not None and hr < lo:
        return {"vital": "hr", "level": "low", "value": hr, "limit": lo}
    if hi is not None and hr > hi:
        return {"vital": "hr", "level": "high", "value": hr, "limit": hi}
    return None


def evaluate_spo2(spo2: Optional[float], thresholds: dict) -> Optional[dict]:
    """Alerta se spo2 < spo2_min. Só tem limite inferior (SpO2 alto não é problema)."""
    if spo2 is None:
        return None
    lo = thresholds.get("spo2_min")
    if lo is not None and spo2 < lo:
        return {"vital": "spo2", "level": "low", "value": spo2, "limit": lo}
    return None


_VITAL_LABELS = {"hr": "frequência cardíaca", "spo2": "SpO2"}
_VITAL_UNITS = {"hr": "bpm", "spo2": "%"}


def explain_vital_alert(alert: dict) -> str:
    """Frase em português com os números reais do alerta."""
    label = _VITAL_LABELS.get(alert["vital"], alert["vital"])
    unit = _VITAL_UNITS.get(alert["vital"], "")
    value, limit = alert["value"], alert["limit"]
    if alert["level"] == "low":
        return f"{label.capitalize()} em {value:.0f}{unit} — abaixo do limiar mínimo definido ({limit:.0f}{unit})."
    return f"{label.capitalize()} em {value:.0f}{unit} — acima do limiar máximo definido ({limit:.0f}{unit})."


def explain_vital_cleared(vital: str, thresholds: dict) -> str:
    """Frase para quando um alerta deixa de se aplicar (leitura voltou ao normal)."""
    label = _VITAL_LABELS.get(vital, vital)
    return f"{label.capitalize()} voltou a estar dentro dos limiares definidos."


# Níveis aceites pela CheckConstraint de alerts.severity (schema.sql), em ordem de gravidade.
SEVERITY_LADDER = ("info", "warning", "serious", "critical")


def next_severity(severity: str) -> Optional[str]:
    """Nível seguinte na escada, ou None se já for 'critical' ou valor desconhecido."""
    try:
        idx = SEVERITY_LADDER.index(severity)
    except ValueError:
        return None
    if idx + 1 >= len(SEVERITY_LADDER):
        return None
    return SEVERITY_LADDER[idx + 1]


def severity_rank(severity: Optional[str]) -> int:
    """Posição na escada (-1 para desconhecido/None)."""
    if severity is None:
        return -1
    try:
        return SEVERITY_LADDER.index(severity)
    except ValueError:
        return -1


# Heurística de engenharia, não pontos de corte clínicos validados.
_HR_SERIOUS_DEVIATION = 0.15   # 15% fora do limiar
_HR_CRITICAL_DEVIATION = 0.35  # 35% fora do limiar
_SPO2_SERIOUS_ABSOLUTE = 90.0  # SpO2 usa pontos absolutos, escala é estreita
_SPO2_CRITICAL_ABSOLUTE = 85.0


def severity_for_vital_alert(alert: dict) -> str:
    """Severidade inicial de um alerta de evaluate_hr/evaluate_spo2. Nunca 'info'."""
    value, limit = alert.get("value"), alert.get("limit")
    if value is None or limit is None:
        return "warning"
    if alert.get("vital") == "spo2":
        if value < _SPO2_CRITICAL_ABSOLUTE:
            return "critical"
        if value < _SPO2_SERIOUS_ABSOLUTE:
            return "serious"
        return "warning"
    if not limit:
        return "warning"
    deviation = abs(value - limit) / abs(limit)
    if deviation >= _HR_CRITICAL_DEVIATION:
        return "critical"
    if deviation >= _HR_SERIOUS_DEVIATION:
        return "serious"
    return "warning"


# RF-05: sem sinal cutâneo (PPG) E sem movimento (IMU) por >30min => "dispositivo removido".
# Distinção de 'link_lost': REMOVIDO exige registos a chegar durante os 30min (o wearable
# comunicou "sem pele, sem movimento"); sem registos, não há dados que sustentem "removido".
WEAR_ABSENCE_SECONDS = 30 * 60  # critério de aceitação do RF-05

WEAR_MOTION_DELTA_G = 0.05  # acima do ruído do IMU parado, abaixo de movimento humano real

# 'unknown' = estado inicial, antes de haver amostras suficientes
WEAR_STATE_UNKNOWN = "unknown"
WEAR_STATE_WORN = "worn"
WEAR_STATE_REMOVED = "removed"
WEAR_STATE_LINK_LOST = "link_lost"


def _minutes(seconds: float) -> int:
    return int(seconds // 60)


class WearDetector:
    """Máquina de estados de uso/não-uso do wearable (RF-05).
    observe(record) devolve um dict de evento só quando o estado muda."""

    def __init__(
        self,
        absence_seconds: float = WEAR_ABSENCE_SECONDS,
        motion_delta_g: float = WEAR_MOTION_DELTA_G,
    ) -> None:
        self.absence_seconds = absence_seconds
        self.motion_delta_g = motion_delta_g
        self.state = WEAR_STATE_UNKNOWN
        # timestamps do relógio do bridge (não do dispositivo)
        self.last_skin_ts: Optional[float] = None
        self.last_motion_ts: Optional[float] = None
        self._last_magnitude: Optional[float] = None
        self._state_since_ts: Optional[float] = None
        # ultimo evento de transicao devolvido por _transition(); permite a um cliente WS que
        # so' ligou DEPOIS da transicao (o caso normal - o bridge arranca antes do dashboard
        # abrir) saber o estado atual sem esperar pela proxima mudanca, que pode nunca vir
        self._last_event: Optional[dict] = None

    # -- entradas ---------------------------------------------------------
    def observe(self, record: dict, now_ts: Optional[float] = None) -> Optional[dict]:
        """Processa um registo FullPlain. Devolve o evento de mudança de estado, ou None."""
        now = time.time() if now_ts is None else now_ts

        if record.get("hr") is not None or record.get("spo2") is not None:
            self.last_skin_ts = now
        if self._has_motion(record):
            self.last_motion_ts = now

        # primeira amostra: arranca os contadores em "agora"
        if self.last_skin_ts is None:
            self.last_skin_ts = now
        if self.last_motion_ts is None:
            self.last_motion_ts = now

        without_skin = now - self.last_skin_ts
        without_motion = now - self.last_motion_ts
        removed = (
            without_skin > self.absence_seconds
            and without_motion > self.absence_seconds
        )
        new_state = WEAR_STATE_REMOVED if removed else WEAR_STATE_WORN
        return self._transition(new_state, now, without_skin, without_motion)

    def on_link_lost(self, now_ts: Optional[float] = None) -> Optional[dict]:
        """Ligação BLE caiu. Estado passa a 'link_lost' e os contadores são apagados."""
        now = time.time() if now_ts is None else now_ts
        self.last_skin_ts = None
        self.last_motion_ts = None
        self._last_magnitude = None
        return self._transition(WEAR_STATE_LINK_LOST, now, None, None)

    def on_link_restored(self, now_ts: Optional[float] = None) -> Optional[dict]:
        """Reconexão: volta a 'unknown', não a 'worn' — ainda sem amostra do pulso."""
        now = time.time() if now_ts is None else now_ts
        if self.state != WEAR_STATE_LINK_LOST:
            return None
        return self._transition(WEAR_STATE_UNKNOWN, now, None, None)

    def current_event(self) -> Optional[dict]:
        """Ultimo evento de transicao conhecido, ou None se ainda nao houve nenhum (estado
        'unknown' desde o arranque do bridge, sem amostras). Para dar a um cliente WS recem-ligado
        o estado atual sem esperar por uma mudanca futura."""
        return self._last_event

    # -- internos ---------------------------------------------------------
    def _has_motion(self, record: dict) -> bool:
        """Movimento nesta amostra: flag `inactivity` do firmware ou variação da aceleração."""
        if record.get("inactivity") is False:
            return True
        try:
            magnitude = math.sqrt(
                float(record["ax"]) ** 2
                + float(record["ay"]) ** 2
                + float(record["az"]) ** 2
            )
        except (KeyError, TypeError, ValueError):
            return False
        previous = self._last_magnitude
        self._last_magnitude = magnitude
        if previous is None:
            return False
        return abs(magnitude - previous) > self.motion_delta_g

    def _transition(
        self,
        new_state: str,
        now: float,
        without_skin: Optional[float],
        without_motion: Optional[float],
    ) -> Optional[dict]:
        if new_state == self.state:
            return None
        previous = self.state
        self.state = new_state
        self._state_since_ts = now
        event = {
            "state": new_state,
            "previous_state": previous,
            "since_ts": now,
            "seconds_without_skin": None if without_skin is None else int(without_skin),
            "seconds_without_motion": None if without_motion is None else int(without_motion),
            "explanation": explain_wear_state(new_state, without_skin, without_motion),
        }
        self._last_event = event
        return event


def explain_wear_state(
    state: str,
    seconds_without_skin: Optional[float] = None,
    seconds_without_motion: Optional[float] = None,
) -> str:
    """Motivo textual legível: o que foi medido, durante quanto tempo, e o veredito."""
    if state == WEAR_STATE_REMOVED:
        skin_min = _minutes(seconds_without_skin or 0)
        motion_min = _minutes(seconds_without_motion or 0)
        return (
            f"Sem sinal cutâneo (o sensor ótico não obteve nenhuma leitura de "
            f"frequência cardíaca ou SpO2) há {skin_min} min e sem movimento no "
            f"acelerómetro há {motion_min} min, com o wearable a comunicar "
            f"normalmente durante todo esse período — padrão consistente com o "
            f"dispositivo retirado do pulso, e não com uma falha de ligação."
        )
    if state == WEAR_STATE_WORN:
        return (
            "Voltou a haver sinal cutâneo e/ou movimento — o dispositivo está a "
            "ser usado."
        )
    if state == WEAR_STATE_LINK_LOST:
        return (
            "O wearable deixou de comunicar com o bridge. Sem registos não é "
            "possível saber se foi retirado do pulso: pode estar apenas fora de "
            "alcance, sem bateria, ou com o Bluetooth desligado."
        )
    return (
        "Ainda sem observações suficientes para afirmar se o dispositivo está a "
        "ser usado."
    )


WEAR_REMOVED_TITLE = "Dispositivo possivelmente retirado"
WEAR_REMOVED_SEVERITY = "warning"  # perda de monitorização, não emergência clínica direta
