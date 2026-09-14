#!/usr/bin/env python3
"""fhir_export.py — mapeia sinais do CareWear para recursos HL7 FHIR R4
Observation (RF-11). Regra: nunca inventar código clínico — SIGNAL_MAPPINGS
marca cada sinal `confirmed=True/False`; só os confirmados saem com `coding`
LOINC/UCUM, os restantes saem só com `text` (ver unconfirmed_signals()).
Validação em duas formas: validate_observation() (sem dependências) e
OBSERVATION_JSON_SCHEMA (JSON Schema para validação externa via `jsonschema`
nos testes, evita circularidade). Sem dependências fora da stdlib."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

LOINC_SYSTEM = "http://loinc.org"
UCUM_SYSTEM = "http://unitsofmeasure.org"
OBSERVATION_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/observation-category"

# CodeSystem próprio do CareWear para métricas derivadas sem equivalente LOINC (pacing_index).
# `urn:` porque é um espaço de nomes deste protótipo, não um registo público.
CAREWEAR_CODESYSTEM_URL = "urn:carewear:codesystem:derived-metrics"

# recurso FHIR CodeSystem publicável à parte do Bundle (endpoint próprio, ver api.py)
CAREWEAR_CODESYSTEM_RESOURCE: dict[str, Any] = {
    "resourceType": "CodeSystem",
    "url": CAREWEAR_CODESYSTEM_URL,
    "version": "1.0.0",
    "name": "CareWearDerivedMetrics",
    "title": "CareWear — Derived Metrics (não-LOINC)",
    "status": "draft",
    "experimental": True,
    "description": (
        "Métricas derivadas, específicas do projeto CareWear, sem código LOINC "
        "correspondente. Cada conceito é uma definição do próprio protótipo, "
        "explicitamente marcada como tal — não deve ser confundida com "
        "terminologia clínica estabelecida."
    ),
    "caseSensitive": True,
    "content": "complete",
    "concept": [
        {
            "code": "pacing-index",
            "display": "Índice de pacing (0-100)",
            "definition": (
                "Contagem de curvas apertadas do pulso (rotação do giroscópio "
                "acima de 45 graus/s, com histerese) numa janela deslizante de "
                "60 segundos, normalizada para 0-100. Sinal COMPLEMENTAR de "
                "deambulação (wandering), NÃO validado clinicamente — os "
                "limiares são heurísticas de primeira iteração (ver "
                "src/Imu/Imu.cpp, detectPacing()). Não deve ser interpretado "
                "como cadência de marcha nem velocidade."
            ),
        },
    ],
}

PSEUDONYM_IDENTIFIER_SYSTEM = "urn:carewear:patient-pseudonym"  # espaço de nomes local, não registo público

# Value set obrigatório de Observation.status (FHIR R4, ObservationStatus).
OBSERVATION_STATUS_CODES = (
    "registered",
    "preliminary",
    "final",
    "amended",
    "corrected",
    "cancelled",
    "entered-in-error",
    "unknown",
)

# FHIR: `Resource.id` é um `id` primitive — [A-Za-z0-9\-\.]{1,64}.
_FHIR_ID_RE = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")
# Reference.reference relativa: "TipoDeRecurso/id".
_FHIR_RELATIVE_REFERENCE_RE = re.compile(r"^[A-Z][A-Za-z]+/[A-Za-z0-9\-.]{1,64}$")


@dataclass(frozen=True)
class SignalMapping:
    """Mapeamento de um sinal do CareWear para um conceito clínico.
    `confirmed` decide se sai `coding` ou só `text`. `candidate_code` documenta
    o código provável mas não confirmado — nunca é emitido."""

    key: str
    text: str
    category_code: str
    category_display: str
    unit_text: Optional[str] = None
    unit_ucum: Optional[str] = None
    confirmed: bool = False
    codings: tuple[tuple[str, str], ...] = ()  # (código, display oficial)
    candidate_code: Optional[str] = None
    note: str = ""
    integer_value: bool = True
    coding_systems: tuple[str, ...] = ()  # sistema por entrada de codings; omisso = LOINC
    represent_as_period: bool = False  # True: effectivePeriod (start+end) em vez de valor pontual


# colunas de origem: SensorRecord.heart_rate/spo2_percent/steps_count/pacing_index,
# ActivityWindow.duration_minutes (storage_advanced.py)
SIGNAL_MAPPINGS: dict[str, SignalMapping] = {
    # 8867-4 "Heart rate", perfil de sinais vitais FHIR R4 (StructureDefinition/heartrate)
    "heart_rate": SignalMapping(
        key="heart_rate",
        text="Frequência cardíaca",
        category_code="vital-signs",
        category_display="Vital Signs",
        unit_text="batimentos/minuto",
        unit_ucum="/min",
        confirmed=True,
        codings=(("8867-4", "Heart rate"),),
    ),
    # perfil oxygensat FHIR R4: 2708-6 + 59408-5 (medição por oximetria de pulso PPG)
    "spo2_percent": SignalMapping(
        key="spo2_percent",
        text="Saturação periférica de oxigénio (SpO2), por oximetria de pulso",
        category_code="vital-signs",
        category_display="Vital Signs",
        unit_text="%",
        unit_ucum="%",
        confirmed=True,
        codings=(
            ("2708-6", "Oxygen saturation in Arterial blood"),
            ("59408-5", "Oxygen saturation in Arterial blood by Pulse oximetry"),
        ),
    ),
    # confirmado após firmware reiniciar contador à meia-noite UTC (resetStepsIfNewDay());
    # antes da 1ª sync de relógio ainda se comporta como acumulado desde arranque (ver note)
    "steps_count": SignalMapping(
        key="steps_count",
        text="Número de passos, contador reiniciado à meia-noite UTC",
        category_code="activity",
        category_display="Activity",
        unit_text="passos",
        unit_ucum="{steps}",
        confirmed=True,
        codings=(("41950-7", "Number of steps in 24 hour Measured"),),
        note=(
            "Confirmado em 2026-09-07 após alteração do firmware "
            "(resetStepsIfNewDay(), reinício diário à meia-noite UTC). "
            "Ressalva: antes da primeira sincronização de relógio válida no "
            "dispositivo (Clock::isValid()==false), o contador ainda se "
            "comporta como acumulado desde o arranque, não como total de 24h "
            "— um recetor FHIR não distingue os dois casos só pelo valor."
        ),
    ),
    # vocabulário próprio (sleep/rest/activity/eating/hygiene), sem LOINC único cobrindo as 5;
    # representação temporal segue o HL7 FHIR Physical Activity IG: effectivePeriod, não Quantity
    "activity_duration": SignalMapping(
        key="activity_duration",
        text="Bloco de rotina diária (categoria própria do CareWear)",
        category_code="activity",
        category_display="Activity",
        confirmed=False,
        candidate_code="41981-2 (apenas se a categoria for 'activity'; as outras 4 não têm candidato)",
        represent_as_period=True,
        note=(
            "Vocabulário próprio (sleep/rest/activity/eating/hygiene). "
            "Representação temporal resolvida: effectivePeriod (start/end), "
            "conforme o HL7 FHIR Physical Activity IG, em vez de duração "
            "como Quantity isolada. O código clínico por categoria continua "
            "por confirmar."
        ),
    ),
    # código próprio do CareWear, não LOINC (ver coding_systems/CAREWEAR_CODESYSTEM_URL);
    # métrica derivada sem equivalente clínico estabelecido (src/Imu/Imu.cpp::detectPacing())
    "pacing_index": SignalMapping(
        key="pacing_index",
        text="Índice de pacing CareWear (métrica derivada, não validada clinicamente, 0-100)",
        category_code="activity",
        category_display="Activity",
        unit_text="índice (0-100)",
        unit_ucum="{score}",
        confirmed=True,
        codings=(("pacing-index", "Índice de pacing (0-100)"),),
        coding_systems=(CAREWEAR_CODESYSTEM_URL,),
        note=(
            "Código do CodeSystem próprio do CareWear "
            f"({CAREWEAR_CODESYSTEM_URL}), NÃO LOINC. Ver "
            "CAREWEAR_CODESYSTEM_RESOURCE para a definição completa, incluindo "
            "o aviso de não-validação clínica embutido na própria definição do "
            "código."
        ),
    ),
}


def unconfirmed_signals() -> list[dict[str, Any]]:
    """Sinais cujo código clínico ficou por confirmar — para relatório/testes."""
    return [
        {
            "signal": m.key,
            "text": m.text,
            "candidate_code": m.candidate_code,
            "note": m.note,
        }
        for m in SIGNAL_MAPPINGS.values()
        if not m.confirmed
    ]


# ----------------------------------------------------------------------
# Construção dos recursos
# ----------------------------------------------------------------------

def _instant(value: Any) -> str:
    """Converte epoch ou datetime para dateTime FHIR em UTC ("...Z").
    Datetimes naive são tratados como UTC (convenção do bridge)."""
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
    else:
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _codeable_concept(mapping: SignalMapping, text_override: Optional[str] = None) -> dict:
    """CodeableConcept do sinal — `coding` só se confirmado. `coding_systems`
    dá o sistema por entrada de `codings`; omisso assume LOINC_SYSTEM."""
    concept: dict[str, Any] = {"text": text_override or mapping.text}
    if mapping.confirmed and mapping.codings:
        systems = mapping.coding_systems or (LOINC_SYSTEM,) * len(mapping.codings)
        concept["coding"] = [
            {"system": system, "code": code, "display": display}
            for (code, display), system in zip(mapping.codings, systems)
        ]
    return concept


def _category(mapping: SignalMapping) -> list[dict]:
    return [{
        "coding": [{
            "system": OBSERVATION_CATEGORY_SYSTEM,
            "code": mapping.category_code,
            "display": mapping.category_display,
        }],
    }]


def _quantity(mapping: SignalMapping, value: Any) -> dict:
    """Quantity UCUM. `system`+`code` só quando há unidade UCUM real."""
    numeric = int(value) if mapping.integer_value else float(value)
    quantity: dict[str, Any] = {"value": numeric}
    if mapping.unit_text:
        quantity["unit"] = mapping.unit_text
    if mapping.unit_ucum:
        quantity["system"] = UCUM_SYSTEM
        quantity["code"] = mapping.unit_ucum
    return quantity


def build_subject(patient_id: int, pseudonym: Optional[str] = None) -> dict:
    """Referência ao paciente. RGPD: nunca leva `display` com nome — só id lógico
    (Patient/<id>) e, se existir, o pseudonym (Art. 4(5)) como identificador opaco."""
    subject: dict[str, Any] = {"reference": f"Patient/{patient_id}"}
    if pseudonym:
        subject["identifier"] = {
            "system": PSEUDONYM_IDENTIFIER_SYSTEM,
            "value": pseudonym,
        }
    return subject


def build_observation(
    *,
    resource_id: str,
    mapping: SignalMapping,
    value: Any,
    effective: Any,
    subject: dict,
    device_id: Optional[int] = None,
    text_override: Optional[str] = None,
    status: str = "final",
    effective_end: Any = None,
) -> dict:
    """Constrói um Observation FHIR R4. `effective_end` + mapping.represent_as_period=True
    produz effectivePeriod em vez de effectiveDateTime pontual (ver activity_duration)."""
    observation: dict[str, Any] = {
        "resourceType": "Observation",
        "id": resource_id,
        "status": status,
        "category": _category(mapping),
        "code": _codeable_concept(mapping, text_override=text_override),
        "subject": subject,
    }
    if mapping.represent_as_period and effective_end is not None:
        observation["effectivePeriod"] = {
            "start": _instant(effective),
            "end": _instant(effective_end),
        }
    else:
        observation["effectiveDateTime"] = _instant(effective)
    if mapping.unit_ucum is not None:
        observation["valueQuantity"] = _quantity(mapping, value)
    if device_id is not None:
        observation["device"] = {"reference": f"Device/{device_id}"}
    return observation


#Patient/{id} e Device/{id} nas Observations nunca resolviam para um recurso real — usadas como entries "include" no Bundle (ver api.py)
def build_patient_resource(patient: Any) -> dict:
    """Recurso FHIR R4 Patient minimalista. RGPD: mesma disciplina de build_subject() — sem nome/data de nascimento, só id e pseudónimo."""
    resource: dict[str, Any] = {
        "resourceType": "Patient",
        "id": str(getattr(patient, "id", "")),
    }
    pseudonym = getattr(patient, "pseudonym", None)
    if pseudonym:
        resource["identifier"] = [{
            "system": PSEUDONYM_IDENTIFIER_SYSTEM,
            "value": pseudonym,
        }]
    return resource


def build_device_resource(device: Any) -> dict:
    """Recurso FHIR R4 Device minimalista — id, versão de firmware (se conhecida) e
    identificador do fabrico (UDI simplificado, não um UDI GS1 real)."""
    resource: dict[str, Any] = {
        "resourceType": "Device",
        "id": str(getattr(device, "id", "")),
    }
    firmware_version = getattr(device, "firmware_version", None)
    if firmware_version:
        resource["version"] = [{"value": str(firmware_version)}]
    hardware_variant = getattr(device, "hardware_variant", None)
    if hardware_variant:
        resource["deviceName"] = [{"name": str(hardware_variant), "type": "model-name"}]
    patient_id = getattr(device, "patient_id", None)
    if patient_id is not None:
        resource["patient"] = {"reference": f"Patient/{patient_id}"}
    return resource


def observations_from_sensor_record(record: Any, subject: dict, device_id: int) -> list[dict]:
    """Todas as Observations de um SensorRecord. Sinais a None são omitidos
    (não dataAbsentReason): ausência aqui = "não trazido", não "medição falhada"."""
    out: list[dict] = []
    columns = (
        ("heart_rate", "heart_rate", "hr"),
        ("spo2_percent", "spo2_percent", "spo2"),
        ("steps_count", "steps_count", "steps"),
        ("pacing_index", "pacing_index", "pacing"),
    )
    record_id = getattr(record, "id", None)
    effective = getattr(record, "timestamp_utc", None)
    for attr, mapping_key, id_prefix in columns:
        value = getattr(record, attr, None)
        if value is None:
            continue
        out.append(build_observation(
            resource_id=f"{id_prefix}-{record_id}",
            mapping=SIGNAL_MAPPINGS[mapping_key],
            value=value,
            effective=effective,
            subject=subject,
            device_id=device_id,
        ))
    return out


def observation_from_activity_window(window: Any, subject: dict, device_id: int) -> Optional[dict]:
    """Observation de uma ActivityWindow. Usa activity_date + start_time (minutos
    desde início do dia) quando disponível, para o instante ser o início real do bloco."""
    duration = getattr(window, "duration_minutes", None)
    if duration is None:
        return None
    activity_date = getattr(window, "activity_date", None)
    if activity_date is None:
        return None
    start_minutes = getattr(window, "start_time", None)
    end_minutes = getattr(window, "end_time", None)
    effective = activity_date
    effective_end = None
    if isinstance(activity_date, datetime) and start_minutes is not None:
        effective = activity_date + timedelta(minutes=int(start_minutes))
        if end_minutes is not None:
            # end_time < start_time quando o bloco atravessa a meia-noite (ex. sono 23:30-06:00)
            end_delta_minutes = int(end_minutes)
            if end_delta_minutes < int(start_minutes):
                end_delta_minutes += 24 * 60
            effective_end = activity_date + timedelta(minutes=end_delta_minutes)
    mapping = SIGNAL_MAPPINGS["activity_duration"]
    category = getattr(window, "activity_category", None) or "desconhecida"
    return build_observation(
        resource_id=f"activity-{getattr(window, 'id', None)}",
        mapping=mapping,
        value=duration,
        effective=effective,
        effective_end=effective_end,
        subject=subject,
        device_id=device_id,
        text_override=f"{mapping.text}: {category}",
    )


# paginação — ver api.py::fhir_observations. Teto não é truncagem: conjunto completo
# continua alcançável via Bundle.link relation="next"
DEFAULT_PAGE_SIZE = 500
MAX_PAGE_SIZE = 5000


def observation_sort_key(observation: dict) -> tuple[str, str]:
    """(instante, id) — chave total/determinística. Só o instante não chega: um
    SensorRecord produz até 4 Observations com o mesmo effectiveDateTime.
    Strings "AAAA-MM-DDTHH:MM:SSZ" comparam lexicograficamente = cronologicamente."""
    effective = observation.get("effectiveDateTime")
    if effective is None:
        effective = (observation.get("effectivePeriod") or {}).get("start")
    return (str(effective or ""), str(observation.get("id") or ""))


def build_observation_bundle(
    observations: Iterable[dict],
    *,
    base_url: str = "",
    total: Optional[int] = None,
    self_url: Optional[str] = None,
    next_url: Optional[str] = None,
    included: Iterable[dict] = (),
) -> dict:
    """Empacota Observations num Bundle FHIR type=searchset. `total` é o total da pesquisa
    (não conta `included`). `included`: Patient/Device referenciados, marcados search.mode=include."""
    entries = []
    for obs in observations:
        entry: dict[str, Any] = {"resource": obs}
        if base_url:
            entry["fullUrl"] = f"{base_url.rstrip('/')}/{obs.get('resourceType', 'Observation')}/{obs.get('id')}"
        entries.append(entry)
    observation_count = len(entries)
    for res in included:
        entry = {"resource": res, "search": {"mode": "include"}}
        if base_url:
            entry["fullUrl"] = f"{base_url.rstrip('/')}/{res.get('resourceType')}/{res.get('id')}"
        entries.append(entry)
    bundle: dict[str, Any] = {
        "resourceType": "Bundle",
        "type": "searchset",
        "timestamp": _instant(datetime.now(timezone.utc)),
        "total": observation_count if total is None else int(total),
        "entry": entries,
    }
    links = []
    if self_url:
        links.append({"relation": "self", "url": self_url})
    if next_url:
        links.append({"relation": "next", "url": next_url})
    if links:
        bundle["link"] = links
    return bundle


# contrato do subconjunto de Observation FHIR R4 produzido, em JSON Schema (draft 2020-12),
# para validação externa via `jsonschema` nos testes (evita circularidade); não é import direto
_CODEABLE_CONCEPT_SCHEMA = {
    "type": "object",
    "properties": {
        "coding": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "system": {"type": "string", "format": "uri"},
                    "code": {"type": "string", "minLength": 1},
                    "display": {"type": "string"},
                },
                "required": ["system", "code"],
                "additionalProperties": False,
            },
        },
        "text": {"type": "string", "minLength": 1},
    },
    "anyOf": [{"required": ["coding"]}, {"required": ["text"]}],
    "additionalProperties": False,
}

_REFERENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "reference": {"type": "string", "pattern": r"^[A-Z][A-Za-z]+/[A-Za-z0-9\-.]{1,64}$"},
        "identifier": {
            "type": "object",
            "properties": {
                "system": {"type": "string"},
                "value": {"type": "string", "minLength": 1},
            },
            "required": ["system", "value"],
            "additionalProperties": False,
        },
        "display": {"type": "string"},
    },
    "anyOf": [{"required": ["reference"]}, {"required": ["identifier"]}],
    "additionalProperties": False,
}

OBSERVATION_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "CareWear — subconjunto de HL7 FHIR R4 Observation",
    "type": "object",
    "properties": {
        "resourceType": {"const": "Observation"},
        "id": {"type": "string", "pattern": r"^[A-Za-z0-9\-.]{1,64}$"},
        "status": {"enum": list(OBSERVATION_STATUS_CODES)},
        "category": {"type": "array", "minItems": 1, "items": _CODEABLE_CONCEPT_SCHEMA},
        "code": _CODEABLE_CONCEPT_SCHEMA,
        "subject": _REFERENCE_SCHEMA,
        "device": _REFERENCE_SCHEMA,
        "effectiveDateTime": {
            "type": "string",
            "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
        },
        # effective[x] é choice type: activity_duration usa effectivePeriod em vez disto
        "effectivePeriod": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"},
                "end": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"},
            },
            "required": ["start"],
            "additionalProperties": False,
        },
        "valueQuantity": {
            "type": "object",
            "properties": {
                "value": {"type": "number"},
                "unit": {"type": "string"},
                "system": {"type": "string", "format": "uri"},
                "code": {"type": "string", "minLength": 1},
            },
            "required": ["value"],
            "dependentRequired": {"code": ["system"], "system": ["code"]},  # UCUM: sempre juntos
            "additionalProperties": False,
        },
        "dataAbsentReason": _CODEABLE_CONCEPT_SCHEMA,
    },
    "required": ["resourceType", "status", "code"],
    "not": {"required": ["valueQuantity", "dataAbsentReason"]},  # obs-6
    "additionalProperties": False,
}

#Schemas minimalistas para as entries "include" do Bundle — ver build_patient_resource()/build_device_resource()
PATIENT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "resourceType": {"const": "Patient"},
        "id": {"type": "string"},
        "identifier": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["resourceType", "id"],
    "additionalProperties": False,
}

DEVICE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "resourceType": {"const": "Device"},
        "id": {"type": "string"},
        "version": {"type": "array", "items": {"type": "object"}},
        "deviceName": {"type": "array", "items": {"type": "object"}},
        "patient": _REFERENCE_SCHEMA,
    },
    "required": ["resourceType", "id"],
    "additionalProperties": False,
}

BUNDLE_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "CareWear — subconjunto de HL7 FHIR R4 Bundle (searchset)",
    "type": "object",
    "properties": {
        "resourceType": {"const": "Bundle"},
        "type": {"enum": ["searchset", "collection", "document", "transaction", "batch"]},
        "timestamp": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"},
        "total": {"type": "integer", "minimum": 0},
        # relation usa os nomes IANA reutilizados pelo FHIR (self/next/previous/first/last)
        "link": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "relation": {"type": "string", "minLength": 1},
                    "url": {"type": "string", "minLength": 1},
                },
                "required": ["relation", "url"],
                "additionalProperties": False,
            },
        },
        "entry": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fullUrl": {"type": "string"},
                    "resource": {"anyOf": [OBSERVATION_JSON_SCHEMA, PATIENT_JSON_SCHEMA, DEVICE_JSON_SCHEMA]},
                    "search": {
                        "type": "object",
                        "properties": {"mode": {"enum": ["match", "include", "outcome"]}},
                        "required": ["mode"],
                        "additionalProperties": False,
                    },
                },
                "required": ["resource"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["resourceType", "type"],
    "additionalProperties": False,
}


def validate_observation(observation: Any) -> list[str]:
    """Valida um Observation FHIR R4; devolve erros acumulados (vazio = válido).
    Cobre o JSON Schema mais as invariantes obs-6/obs-7 e coerência UCUM."""
    errors: list[str] = []

    if not isinstance(observation, dict):
        return ["Observation tem de ser um objeto JSON"]

    if observation.get("resourceType") != "Observation":
        errors.append("resourceType tem de ser exatamente 'Observation'")

    # status (1..1) com value set obrigatório
    status = observation.get("status")
    if status is None:
        errors.append("status é obrigatório (cardinalidade 1..1)")
    elif status not in OBSERVATION_STATUS_CODES:
        errors.append(f"status '{status}' fora do value set ObservationStatus")

    # code (1..1)
    if "code" not in observation:
        errors.append("code é obrigatório (cardinalidade 1..1)")
    else:
        errors.extend(_validate_codeable_concept(observation["code"], "code"))

    resource_id = observation.get("id")
    if resource_id is not None and not _FHIR_ID_RE.match(str(resource_id)):
        errors.append(f"id '{resource_id}' não respeita o formato FHIR id")

    for ref_field in ("subject", "device", "performer", "encounter"):
        if ref_field in observation:
            errors.extend(_validate_reference(observation[ref_field], ref_field))

    categories = observation.get("category")
    if categories is not None:
        if not isinstance(categories, list) or not categories:
            errors.append("category, quando presente, tem de ser uma lista não vazia")
        else:
            for i, cat in enumerate(categories):
                errors.extend(_validate_codeable_concept(cat, f"category[{i}]"))

    effective = observation.get("effectiveDateTime")
    if effective is not None:
        try:
            datetime.fromisoformat(str(effective).replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"effectiveDateTime '{effective}' não é um dateTime válido")

    value_keys = [k for k in observation if k.startswith("value") and k != "value"]
    if len(value_keys) > 1:
        errors.append(f"value[x] é uma escolha (0..1); encontrados {sorted(value_keys)}")

    if "valueQuantity" in observation:
        errors.extend(_validate_quantity(observation["valueQuantity"], "valueQuantity"))

    # obs-6: dataAbsentReason SHALL only be present if value[x] is not present.
    if "dataAbsentReason" in observation and value_keys:
        errors.append("obs-6 violada: dataAbsentReason presente ao mesmo tempo que value[x]")

    # obs-7: component.code igual a Observation.code exige Observation sem value[x]
    components = observation.get("component")
    if components:
        parent_codes = _coding_keys(observation.get("code"))
        for i, comp in enumerate(components):
            if not isinstance(comp, dict):
                errors.append(f"component[{i}] tem de ser um objeto")
                continue
            if "code" not in comp:
                errors.append(f"component[{i}].code é obrigatório")
                continue
            if value_keys and _coding_keys(comp["code"]) & parent_codes:
                errors.append(
                    f"obs-7 violada: component[{i}].code é igual a Observation.code "
                    "e o Observation tem value[x]"
                )

    return errors


def _validate_codeable_concept(concept: Any, path: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(concept, dict):
        return [f"{path} tem de ser um CodeableConcept (objeto)"]
    codings = concept.get("coding")
    text = concept.get("text")
    if not codings and not text:
        errors.append(f"{path} tem de ter pelo menos 'coding' ou 'text'")
    if codings is not None:
        if not isinstance(codings, list) or not codings:
            errors.append(f"{path}.coding, quando presente, tem de ser uma lista não vazia")
        else:
            for i, coding in enumerate(codings):
                if not isinstance(coding, dict):
                    errors.append(f"{path}.coding[{i}] tem de ser um objeto")
                    continue
                if not coding.get("system"):
                    errors.append(f"{path}.coding[{i}].system em falta (código sem sistema é ambíguo)")
                if not coding.get("code"):
                    errors.append(f"{path}.coding[{i}].code em falta")
    if text is not None and not isinstance(text, str):
        errors.append(f"{path}.text tem de ser texto")
    return errors


def _validate_reference(reference: Any, path: str) -> list[str]:
    if not isinstance(reference, dict):
        return [f"{path} tem de ser uma Reference (objeto)"]
    errors: list[str] = []
    literal = reference.get("reference")
    identifier = reference.get("identifier")
    if literal is None and identifier is None:
        errors.append(f"{path} tem de ter 'reference' ou 'identifier'")
    if literal is not None and not _FHIR_RELATIVE_REFERENCE_RE.match(str(literal)):
        errors.append(f"{path}.reference '{literal}' não tem a forma 'TipoDeRecurso/id'")
    if identifier is not None:
        if not isinstance(identifier, dict):
            errors.append(f"{path}.identifier tem de ser um objeto")
        elif not identifier.get("system") or not identifier.get("value"):
            errors.append(f"{path}.identifier precisa de 'system' e 'value'")
    return errors


def _validate_quantity(quantity: Any, path: str) -> list[str]:
    if not isinstance(quantity, dict):
        return [f"{path} tem de ser uma Quantity (objeto)"]
    errors: list[str] = []
    value = quantity.get("value")
    if value is None:
        errors.append(f"{path}.value em falta")
    elif isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{path}.value tem de ser numérico")
    has_system = bool(quantity.get("system"))  # UCUM: system e code andam sempre juntos
    has_code = bool(quantity.get("code"))
    if has_code != has_system:
        errors.append(f"{path}: 'system' e 'code' (UCUM) têm de estar ambos presentes ou ambos ausentes")
    return errors


def _coding_keys(concept: Any) -> set:
    """(system, code) de um CodeableConcept — para comparar conceitos (obs-7)."""
    if not isinstance(concept, dict):
        return set()
    return {
        (c.get("system"), c.get("code"))
        for c in concept.get("coding", [])
        if isinstance(c, dict)
    }


def validate_bundle(bundle: Any) -> list[str]:
    """Valida um Bundle e todos os Observations que ele contém."""
    if not isinstance(bundle, dict):
        return ["Bundle tem de ser um objeto JSON"]
    errors: list[str] = []
    if bundle.get("resourceType") != "Bundle":
        errors.append("resourceType tem de ser exatamente 'Bundle'")
    if not bundle.get("type"):
        errors.append("Bundle.type é obrigatório")
    entries = bundle.get("entry", [])
    if not isinstance(entries, list):
        return errors + ["Bundle.entry tem de ser uma lista"]
    links = bundle.get("link")
    relations: set[str] = set()
    if links is not None:
        if not isinstance(links, list) or not links:
            errors.append("Bundle.link, quando presente, tem de ser uma lista não vazia")
            links = []
        for i, link in enumerate(links):
            if not isinstance(link, dict):
                errors.append(f"link[{i}] tem de ser um objeto {{relation, url}}")
                continue
            if not link.get("relation"):
                errors.append(f"link[{i}].relation em falta")
            if not link.get("url"):
                errors.append(f"link[{i}].url em falta")
            relations.add(str(link.get("relation")))
        if len(relations) != len([l for l in links if isinstance(l, dict)]):
            errors.append("Bundle.link tem relations repetidas")

    total = bundle.get("total")
    if total is not None:
        #total só conta entries de "match" (search.mode ausente/"match") — "include" (Patient/Device) fica de fora
        match_count = len([e for e in entries if isinstance(e, dict) and e.get("search", {}).get("mode", "match") == "match"])
        paginated = bool(relations & {"self", "next", "previous", "first", "last"})
        if paginated and total < match_count:
            errors.append(f"Bundle.total ({total}) é menor que o número de entradas 'match' ({match_count})")
        elif not paginated and total != match_count:
            errors.append(f"Bundle.total ({total}) não coincide com o número de entradas 'match' ({match_count})")
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict) or "resource" not in entry:
            errors.append(f"entry[{i}] tem de ter 'resource'")
            continue
        resource_type = entry["resource"].get("resourceType") if isinstance(entry["resource"], dict) else None
        if resource_type != "Observation":
            continue  #Patient/Device (search.mode=include) — validados pelo JSON Schema, não por validate_observation
        for err in validate_observation(entry["resource"]):
            errors.append(f"entry[{i}].resource: {err}")
    return errors
