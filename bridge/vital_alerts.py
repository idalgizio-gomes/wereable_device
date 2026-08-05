"""Avaliação de sinais vitais (FC/SpO2) contra a baseline comportamental
personalizada do paciente — "baseline comportamental personalizada"
(2026-08-05, funcionalidade derivada da revisão de literatura PRISMA).

`storage_advanced.py::PersonalizedThreshold` já existia no esquema desde a
migração inicial, mas nenhuma rotina do bridge alguma vez o lia ou
escrevia — os alertas de FC/SpO2 fora do esperado que aparecem no
dashboard eram só dados de demonstração fixos (nunca calculados a partir
de uma leitura real). Este módulo fecha esse gap com uma regra
determinística, no mesmo espírito de `ml/duration_detector.py`: não é um
modelo treinado, é uma comparação direta contra limiares (por omissão ou
definidos pelo cuidador via `storage_advanced.set_thresholds`).

Cada leitura é avaliada de forma independente e sem estado — quem decide
SE vale a pena difundir um alerta (ex.: só em mudança de estado, para não
repetir a cada amostra) é o chamador (`ble_bridge.py`), tal como
`duration_detector.evaluate_block` também não decide sozinho quando
persistir/difundir.
"""

from __future__ import annotations

from typing import Optional


def evaluate_hr(hr: Optional[float], thresholds: dict) -> Optional[dict]:
    """Devolve um dict de alerta se `hr` estiver fora de
    [heart_rate_min, heart_rate_max], ou None se estiver dentro (ou se
    `hr` for None — sem leitura, não há o que avaliar)."""
    if hr is None:
        return None
    lo, hi = thresholds.get("heart_rate_min"), thresholds.get("heart_rate_max")
    if lo is not None and hr < lo:
        return {"vital": "hr", "level": "low", "value": hr, "limit": lo}
    if hi is not None and hr > hi:
        return {"vital": "hr", "level": "high", "value": hr, "limit": hi}
    return None


def evaluate_spo2(spo2: Optional[float], thresholds: dict) -> Optional[dict]:
    """Devolve um dict de alerta se `spo2` estiver abaixo de spo2_min, ou
    None caso contrário. SpO2 só tem limite inferior (um SpO2 "alto" não é
    clinicamente um problema, ao contrário da FC) — por isso, ao contrário
    de evaluate_hr, não há ramo 'high'."""
    if spo2 is None:
        return None
    lo = thresholds.get("spo2_min")
    if lo is not None and spo2 < lo:
        return {"vital": "spo2", "level": "low", "value": spo2, "limit": lo}
    return None


_VITAL_LABELS = {"hr": "frequência cardíaca", "spo2": "SpO2"}
_VITAL_UNITS = {"hr": "bpm", "spo2": "%"}


def explain_vital_alert(alert: dict) -> str:
    """"Explicação de alerta" (mesmo padrão de
    ml/duration_detector.py::explain_block, feature 2026-08-05) — frase em
    português com os números reais envolvidos, não só o veredito."""
    label = _VITAL_LABELS.get(alert["vital"], alert["vital"])
    unit = _VITAL_UNITS.get(alert["vital"], "")
    value, limit = alert["value"], alert["limit"]
    if alert["level"] == "low":
        return f"{label.capitalize()} em {value:.0f}{unit} — abaixo do limiar mínimo definido ({limit:.0f}{unit})."
    return f"{label.capitalize()} em {value:.0f}{unit} — acima do limiar máximo definido ({limit:.0f}{unit})."


def explain_vital_cleared(vital: str, thresholds: dict) -> str:
    """Frase para quando um alerta em curso deixa de se aplicar (leitura
    voltou para dentro dos limiares) — o dashboard usa isto para limpar o
    aviso com uma explicação, não só fazê-lo desaparecer em silêncio."""
    label = _VITAL_LABELS.get(vital, vital)
    return f"{label.capitalize()} voltou a estar dentro dos limiares definidos."
