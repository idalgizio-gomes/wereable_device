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

NOTA DE ÂMBITO (2026-09-07): este módulo passou a alojar também duas
regras que não são estritamente "sinais vitais fora do limiar" — a escada
de severidade do RF-07 e o detetor de não-uso do dispositivo do RF-05.
Ambas são, tal como as funções acima, regras determinísticas sem estado
partilhado com o resto do bridge e avaliadas a partir do mesmo registo
`FullPlain`, por isso vivem no mesmo módulo em vez de num ficheiro novo
(decisão de organização, não técnica — ver relatório do RF-05).
"""

from __future__ import annotations

import math
import time
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


# ============================================================
# RF-07 (2026-09-07) — ESCADA DE SEVERIDADE
# ------------------------------------------------------------
# Os quatro níveis são exatamente os aceites pela CheckConstraint da
# coluna `alerts.severity` (ver bridge/schema.sql e
# storage_advanced.py::Alert) — a ordem abaixo é a ordem de gravidade, e
# é ela que define o que "escalar para o nível seguinte" significa.
# Manter esta tupla como fonte única evita o erro clássico de o bridge e
# o dashboard discordarem sobre qual é o nível a seguir a 'warning'.
# ============================================================
SEVERITY_LADDER = ("info", "warning", "serious", "critical")


def next_severity(severity: str) -> Optional[str]:
    """Nível imediatamente acima na escada, ou None se já for o máximo
    ('critical' nunca escala mais — não há nada acima) ou se o valor não
    for reconhecido (falha fechada: um valor inesperado não escala nada,
    em vez de saltar para 'critical' por omissão)."""
    try:
        idx = SEVERITY_LADDER.index(severity)
    except ValueError:
        return None
    if idx + 1 >= len(SEVERITY_LADDER):
        return None
    return SEVERITY_LADDER[idx + 1]


def severity_rank(severity: Optional[str]) -> int:
    """Posição na escada (-1 para valores desconhecidos/None), útil para
    comparar dois níveis sem espalhar a ordem por vários ficheiros."""
    if severity is None:
        return -1
    try:
        return SEVERITY_LADDER.index(severity)
    except ValueError:
        return -1


# Fração acima/abaixo do limiar a partir da qual o alerta nasce já num
# nível mais alto. NÃO são pontos de corte clínicos validados — são uma
# heurística de engenharia determinística, escolhida para que "92 bpm com
# limite 90" e "160 bpm com limite 90" não cheguem ao cuidador com
# exatamente o mesmo aspeto (que era o comportamento até 2026-09-07: todos
# os alertas de vitais chegavam sem severidade nenhuma associada).
_HR_SERIOUS_DEVIATION = 0.15   # 15% fora do limiar
_HR_CRITICAL_DEVIATION = 0.35  # 35% fora do limiar
# SpO2 tem uma escala estreita (a diferença entre 94% e 85% é enorme),
# por isso usa pontos absolutos em vez de percentagem do limiar.
_SPO2_SERIOUS_ABSOLUTE = 90.0
_SPO2_CRITICAL_ABSOLUTE = 85.0


def severity_for_vital_alert(alert: dict) -> str:
    """Severidade inicial de um alerta produzido por evaluate_hr/
    evaluate_spo2 — nunca 'info' (uma leitura fora do limiar definido pelo
    cuidador é, no mínimo, um aviso) e nunca inventa 'critical' a partir de
    um desvio marginal."""
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


# ============================================================
# RF-05 (2026-09-07) — DETEÇÃO DE NÃO-USO DO DISPOSITIVO
# ------------------------------------------------------------
# Critério de aceitação: ausência de sinal cutâneo E de movimento por mais
# de 30 minutos gera o estado "dispositivo removido".
#
# Porquê estes dois sinais, e não um sensor de contacto dedicado: o
# wearable não tem nenhum. A literatura revista mede exatamente a mesma
# coisa a partir dos sinais que já existem — Ding et al. (2024) e Godkin
# et al. (2021) identificam períodos de não-uso ("non-wear") pela
# combinação de sinal fotopletismográfico ausente/plano com ausência de
# variação no acelerómetro, em vez de um interruptor físico.
#
#   - "sinal cutâneo": o PPG do firmware só publica FC/SpO2 quando deteta
#     contacto suficiente com a pele (checkFingerPresentBrief() em
#     src/Ppg/Ppg.cpp); sem contacto, os campos chegam a 0 e o bridge
#     converte-os para None (decode_full_plain). Portanto "hr e spo2
#     sempre None" É o sinal de ausência de pele, não uma falha de dados.
#   - "movimento": um dispositivo pousado numa mesa produz uma norma de
#     aceleração praticamente constante (só a gravidade); ao pulso, mesmo
#     durante o sono, a norma oscila. Usa-se a variação entre amostras
#     consecutivas, mais o flag `inactivity` que o próprio firmware já
#     calcula (ver Imu.cpp) como sinal redundante.
#
# DISTINÇÃO EXIGIDA PELO REQUISITO (e a razão de este detetor ter estado,
# e não ser uma função pura): "sem dados porque foi removido" e "sem dados
# porque a ligação caiu" são coisas diferentes para o cuidador.
#   - REMOVIDO só pode ser concluído com registos A CHEGAR: o wearable
#     esteve a comunicar durante os 30 minutos inteiros e o que ele
#     comunicou foi "sem pele, sem movimento".
#   - LIGAÇÃO PERDIDA é a ausência dos próprios registos. Aí o bridge não
#     sabe nada sobre o pulso do utente — e dizer "removido" nesse caso
#     seria uma afirmação que os dados não sustentam.
# Por isso `observe()` nunca corre sem amostras, e `on_link_lost()` é um
# ponto de entrada separado que apaga os contadores em vez de os deixar a
# envelhecer sozinhos (senão, uma ligação em baixo durante 30 min
# produzia um falso "dispositivo removido" no instante da reconexão).
# ============================================================
WEAR_ABSENCE_SECONDS = 30 * 60  # 30 min, o critério de aceitação do RF-05

# Variação mínima da norma da aceleração (em g) entre amostras
# consecutivas para contar como movimento. O IMU tem ruído próprio na
# ordem de alguns mili-g com o dispositivo imóvel; 0.05 g fica bem acima
# desse ruído e bem abaixo de qualquer movimento humano real (levantar o
# braço passa facilmente de 0.3 g).
WEAR_MOTION_DELTA_G = 0.05

# Estados possíveis. 'unknown' é o estado inicial (ainda não houve
# amostras suficientes para afirmar seja o que for) e é deliberadamente
# distinto de 'worn' — o dashboard não deve prometer "em uso" antes de o
# saber.
WEAR_STATE_UNKNOWN = "unknown"
WEAR_STATE_WORN = "worn"
WEAR_STATE_REMOVED = "removed"
WEAR_STATE_LINK_LOST = "link_lost"


def _minutes(seconds: float) -> int:
    return int(seconds // 60)


class WearDetector:
    """Máquina de estados de uso/não-uso do wearable (RF-05).

    Alimentada por `observe(record)` a cada registo recebido do
    dispositivo; devolve um dict de evento SÓ quando o estado muda (mesmo
    padrão de `BleBridge._maybe_broadcast_vital_alert`, para não inundar o
    dashboard com uma mensagem por amostra a ~54 Hz).
    """

    def __init__(
        self,
        absence_seconds: float = WEAR_ABSENCE_SECONDS,
        motion_delta_g: float = WEAR_MOTION_DELTA_G,
    ) -> None:
        self.absence_seconds = absence_seconds
        self.motion_delta_g = motion_delta_g
        self.state = WEAR_STATE_UNKNOWN
        # Instantes (relógio do bridge, não do dispositivo — o RTC do
        # wearable pode nunca ter recebido a hora por CTS) da última
        # evidência de cada um dos dois sinais.
        self.last_skin_ts: Optional[float] = None
        self.last_motion_ts: Optional[float] = None
        self._last_magnitude: Optional[float] = None
        self._state_since_ts: Optional[float] = None

    # -- entradas ---------------------------------------------------------
    def observe(self, record: dict, now_ts: Optional[float] = None) -> Optional[dict]:
        """Processa um registo FullPlain já descodificado. Devolve o evento
        de mudança de estado, ou None se o estado se manteve."""
        now = time.time() if now_ts is None else now_ts

        if record.get("hr") is not None or record.get("spo2") is not None:
            self.last_skin_ts = now
        if self._has_motion(record):
            self.last_motion_ts = now

        # Primeira amostra desta sessão: sem histórico não se pode afirmar
        # ausência de 30 min. Arranca os dois contadores em "agora" — a
        # contagem só começa quando há de facto observação a decorrer.
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
        """A ligação BLE ao wearable caiu (ou foi desligada pelo
        dashboard). Ver o cabeçalho: sem registos não há como distinguir um
        dispositivo retirado de um dispositivo fora de alcance, por isso o
        estado passa a 'link_lost' e os contadores são apagados."""
        now = time.time() if now_ts is None else now_ts
        self.last_skin_ts = None
        self.last_motion_ts = None
        self._last_magnitude = None
        return self._transition(WEAR_STATE_LINK_LOST, now, None, None)

    def on_link_restored(self, now_ts: Optional[float] = None) -> Optional[dict]:
        """Reconexão: volta a 'unknown' (não a 'worn') — só a primeira
        amostra é que diz alguma coisa sobre o pulso do utente."""
        now = time.time() if now_ts is None else now_ts
        if self.state != WEAR_STATE_LINK_LOST:
            return None
        return self._transition(WEAR_STATE_UNKNOWN, now, None, None)

    # -- internos ---------------------------------------------------------
    def _has_motion(self, record: dict) -> bool:
        """Movimento presente nesta amostra. Dois sinais independentes:
        o flag `inactivity` calculado pelo próprio firmware (False = houve
        movimento recente) e a variação da norma da aceleração face à
        amostra anterior."""
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
            return False  # sem termo de comparação, não se inventa movimento
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
        return {
            "state": new_state,
            "previous_state": previous,
            "since_ts": now,
            "seconds_without_skin": None if without_skin is None else int(without_skin),
            "seconds_without_motion": None if without_motion is None else int(without_motion),
            "explanation": explain_wear_state(new_state, without_skin, without_motion),
        }


def explain_wear_state(
    state: str,
    seconds_without_skin: Optional[float] = None,
    seconds_without_motion: Optional[float] = None,
) -> str:
    """"Motivo textual legível" (mesma exigência do RF-07 aplicada aqui):
    a frase diz o que foi medido e durante quanto tempo, não só o veredito
    — e, no caso de 'removed', diz explicitamente que a ligação se manteve,
    que é o que separa este estado de 'link_lost'."""
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
# Severidade do alerta de não-uso: 'warning', não mais alto. Um wearable
# retirado não é, por si só, uma emergência clínica — mas é uma perda de
# monitorização, e se ninguém confirmar o alerta o escalonamento do RF-07
# encarrega-se de o subir sozinho.
WEAR_REMOVED_SEVERITY = "warning"
