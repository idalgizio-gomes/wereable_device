#!/usr/bin/env python3
"""
fhir_export.py — mapeamento dos sinais guardados pelo CareWear para
recursos **HL7 FHIR R4 `Observation`** (RF-11, 2026-09-07).

PORQUÊ ESTE FICHEIRO EXISTE
---------------------------
Na revisão de literatura deste projeto (PRISMA_SYSTEMATIC_REVIEW.md), a
dimensão "Interoperabilidade" está a **0 de 20 estudos**: nenhum dos
trabalhos do corpus implementa API, FHIR, HL7 ou integração com registo
clínico eletrónico. É a lacuna mais total encontrada em toda a revisão.
Por isso este requisito não é "mais uma exportação" — é o ponto de
diferenciação demonstrável do protótipo, e a razão pela qual o mapeamento
é feito com códigos reais e validado por esquema, em vez de um JSON
"parecido com FHIR".

Já existia uma exportação FHIR no dashboard
(`web/dashboard/export-clinico.js::buildFhirBundle`), mas essa é
client-side, cobre só os *alertas* visíveis na sessão e usa
`code: { text: ... }` sem qualquer código normalizado — não é
interoperável na prática, porque nenhum sistema recetor consegue
perceber o que é cada Observation. Este módulo trata do lado servidor e
dos SINAIS MEDIDOS (FC, SpO2, passos, atividade), que são os que têm
códigos LOINC estabelecidos.

DECISÃO SOBRE CÓDIGOS LOINC (2026-09-07)
----------------------------------------
Regra assumida: **nunca inventar um código clínico**. Cada sinal tem uma
entrada em `SIGNAL_MAPPINGS` com um campo `confirmed`:

  * `confirmed=True`  -> o `CodeableConcept` sai com `coding` LOINC/UCUM
    completo. Só para códigos de que há certeza.
  * `confirmed=False` -> o `CodeableConcept` sai **apenas com `text`**
    (legal em FHIR: `CodeableConcept.text` é o "plain text
    representation of the concept" e é o que se usa exatamente quando
    não há codificação de confiança). O código candidato fica registado
    em `candidate_code`/`note` para revisão clínica, mas NÃO é emitido
    como se fosse verdade.

`unconfirmed_signals()` devolve a lista do que está por confirmar, para
que isso seja visível no relatório do projeto e testável.

Um recetor FHIR que receba um `CodeableConcept` só com `text` sabe que
não pode fazer processamento automático daquele valor — o que é o
comportamento seguro e honesto. Emitir um LOINC errado seria pior do que
não emitir nenhum: o recetor confiaria nele.

REFERÊNCIAS DE ESTRUTURA
------------------------
FHIR R4 (4.0.1), recurso Observation: `status` (1..1, obrigatório) e
`code` (1..1, obrigatório) são os únicos elementos obrigatórios; as
invariantes obs-6 e obs-7 estão implementadas em `validate_observation`.

SEM DEPENDÊNCIAS NOVAS
----------------------
Este módulo não importa nada fora da biblioteca padrão (há uma CSP
restritiva no dashboard e uma regra de projeto de não engordar
`requirements.txt` sem necessidade real). A validação de esquema é feita
de duas formas complementares:

  1. `validate_observation()` — validador próprio, sem dependências,
     que verifica cardinalidades, tipos, value sets e as invariantes
     obs-6/obs-7 que um JSON Schema não exprime bem.
  2. `OBSERVATION_JSON_SCHEMA` — o mesmo contrato expresso como JSON
     Schema (draft 2020-12), para poder ser validado por uma biblioteca
     externa e independente (`jsonschema`) nos testes, quando essa
     estiver instalada. Isto evita a circularidade de "o meu validador
     valida o meu próprio output".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

LOINC_SYSTEM = "http://loinc.org"
UCUM_SYSTEM = "http://unitsofmeasure.org"
OBSERVATION_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/observation-category"

# Sistema de identificação local do pseudónimo do paciente. `urn:` porque é
# um espaço de nomes deste protótipo, não um registo público — não se finge
# que é um identificador nacional.
PSEUDONYM_IDENTIFIER_SYSTEM = "urn:carewear:patient-pseudonym"

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

    `confirmed` é o campo que decide se sai `coding` ou só `text` — ver o
    cabeçalho do módulo. `candidate_code` guarda o código que *parece* ser
    o correto mas que ainda não foi confirmado contra o browser LOINC
    oficial; existe para documentar o trabalho por fazer, nunca para ser
    emitido.
    """

    key: str
    text: str
    category_code: str
    category_display: str
    unit_text: Optional[str] = None
    unit_ucum: Optional[str] = None
    confirmed: bool = False
    codings: tuple[tuple[str, str], ...] = ()  # (código LOINC, display oficial)
    candidate_code: Optional[str] = None
    note: str = ""
    integer_value: bool = True


# ----------------------------------------------------------------------
# REGISTO DE MAPEAMENTOS (2026-09-07)
# ----------------------------------------------------------------------
# Colunas de origem: `SensorRecord.heart_rate`, `SensorRecord.spo2_percent`,
# `SensorRecord.steps_count`, `SensorRecord.pacing_index` e
# `ActivityWindow.duration_minutes` (storage_advanced.py).
SIGNAL_MAPPINGS: dict[str, SignalMapping] = {
    # CONFIRMADO. 8867-4 "Heart rate" é o código LOINC do perfil de sinais
    # vitais do próprio FHIR R4 (StructureDefinition/heartrate) — não há
    # ambiguidade possível. Unidade UCUM "/min" (batimentos por minuto),
    # também fixada por esse perfil.
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
    # CONFIRMADO. O perfil oxygensat do FHIR R4 exige 2708-6 ("Oxygen
    # saturation in Arterial blood") e acrescenta 59408-5 quando a medição
    # vem de oximetria de pulso — que é exatamente o caso aqui (sensor PPG
    # do wearable, ver ImuPpgPayloadV1 no firmware). Emitem-se os dois
    # codings no MESMO CodeableConcept, como o perfil manda: são duas
    # traduções do mesmo conceito, não dois conceitos.
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
    # POR CONFIRMAR. Há pelo menos dois candidatos plausíveis e a escolha
    # entre eles depende de uma semântica que este protótipo NÃO garante:
    #   * 41950-7 "Number of steps in 24 hour Measured" — pressupõe um
    #     total de 24 horas;
    #   * 55423-8 "Number of steps in unspecified time Pedometer" — período
    #     não especificado.
    # `SensorRecord.steps_count` é o contador do dispositivo no instante da
    # amostra (acumulado desde o arranque/reset do firmware), não um total
    # diário nem uma contagem de janela — não corresponde limpo a nenhum
    # dos dois. Fica com `text` até a semântica do contador ser fixada.
    "steps_count": SignalMapping(
        key="steps_count",
        text="Contagem de passos (contador acumulado do dispositivo)",
        category_code="activity",
        category_display="Activity",
        unit_text="passos",
        unit_ucum="{steps}",
        confirmed=False,
        candidate_code="41950-7 ou 55423-8",
        note=(
            "A escolha depende de fixar a semântica de SensorRecord.steps_count "
            "(acumulado do dispositivo vs. total de 24h vs. janela). Confirmar "
            "no browser LOINC oficial antes de codificar."
        ),
    ),
    # POR CONFIRMAR. As categorias de rotina do CareWear (sleep/rest/
    # activity/eating/hygiene) vêm do template de 21 passos do artigo do
    # projeto — são um vocabulário PRÓPRIO de classificação de atividades
    # de vida diária, não um conceito LOINC existente. Codificar isto como
    # "Exercise duration" (candidato abaixo) seria errado para 4 das 5
    # categorias (dormir, comer, higiene e descanso não são exercício).
    "activity_duration": SignalMapping(
        key="activity_duration",
        text="Duração de bloco de rotina diária",
        category_code="activity",
        category_display="Activity",
        unit_text="minutos",
        unit_ucum="min",
        confirmed=False,
        candidate_code="41981-2 (apenas para a categoria 'activity')",
        note=(
            "Vocabulário próprio do projeto (sleep/rest/activity/eating/hygiene). "
            "Nenhum código LOINC único cobre as 5 categorias; o mapeamento correto "
            "é provavelmente por categoria e exige validação clínica."
        ),
    ),
    # POR CONFIRMAR — e quase de certeza NÃO EXISTE em LOINC. O "índice de
    # pacing" é uma métrica derivada, definida por este projeto. Fica com
    # `text`, deliberadamente sem código: inventar um seria criar um falso
    # conceito clínico.
    "pacing_index": SignalMapping(
        key="pacing_index",
        text="Índice de pacing CareWear (métrica derivada do projeto, 0-100)",
        category_code="activity",
        category_display="Activity",
        unit_text="índice (0-100)",
        unit_ucum="{score}",
        confirmed=False,
        candidate_code=None,
        note=(
            "Métrica específica do projeto, sem equivalente LOINC conhecido. "
            "Interoperar isto exigiria publicar um CodeSystem próprio."
        ),
    ),
}


def unconfirmed_signals() -> list[dict[str, Any]]:
    """Sinais cujo código clínico ficou POR CONFIRMAR (ver cabeçalho).

    Existe para o relatório do projeto e para os testes: a lista de
    "dívida clínica" é dado explícito do sistema, não uma nota de rodapé
    que se perde.
    """
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
    """Converte um timestamp para `dateTime` FHIR em UTC ("...Z").

    Aceita epoch (int/float, como `SensorRecord.timestamp_utc`) ou
    `datetime`. Datetimes "naive" são tratados como UTC — é a convenção
    de todo o bridge (`datetime.utcnow()`, ver storage_advanced.py).
    """
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
    else:
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    # isoformat() dá "+00:00"; FHIR aceita, mas "Z" é a forma canónica.
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _codeable_concept(mapping: SignalMapping, text_override: Optional[str] = None) -> dict:
    """CodeableConcept do sinal — com `coding` só se o código for confirmado."""
    concept: dict[str, Any] = {"text": text_override or mapping.text}
    if mapping.confirmed and mapping.codings:
        concept["coding"] = [
            {"system": LOINC_SYSTEM, "code": code, "display": display}
            for code, display in mapping.codings
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
    """Referência ao paciente.

    DECISÃO (2026-09-07, RGPD): a referência NUNCA leva `display` com o
    nome do doente. Vai o id lógico (`Patient/<id>`, resolúvel só por quem
    já está autorizado nesta API) e, quando existe, o `pseudonym`
    (storage_advanced.Patient.pseudonym, Art. 4(5)) como identificador
    opaco. Um ficheiro FHIR exportado deste sistema não identifica ninguém
    por si só — é preciso a base de dados para reverter o pseudónimo.
    """
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
) -> dict:
    """Constrói um `Observation` FHIR R4 a partir de um sinal medido."""
    observation: dict[str, Any] = {
        "resourceType": "Observation",
        "id": resource_id,
        "status": status,
        "category": _category(mapping),
        "code": _codeable_concept(mapping, text_override=text_override),
        "subject": subject,
        "effectiveDateTime": _instant(effective),
        "valueQuantity": _quantity(mapping, value),
    }
    if device_id is not None:
        # `Observation.device` é a origem da medição — mantém a
        # rastreabilidade do wearable concreto sem expor nada do doente.
        observation["device"] = {"reference": f"Device/{device_id}"}
    return observation


def observations_from_sensor_record(record: Any, subject: dict, device_id: int) -> list[dict]:
    """Todas as Observations de UM `SensorRecord`.

    Sinais a `None` são OMITIDOS (não saem com `dataAbsentReason`): a
    ausência aqui significa "este registo não trouxe este sinal", não
    "foi tentado medir e não se conseguiu" — e `dataAbsentReason` diz a
    segunda coisa. Omitir é a leitura honesta.
    """
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
    """Observation de uma `ActivityWindow` (duração de um bloco de rotina).

    `effectiveDateTime` usa `activity_date` + `start_time` (minutos desde o
    início do dia, ver ActivityWindow) quando `start_time` existe — assim o
    instante da observação é o início real do bloco, não a meia-noite.
    """
    duration = getattr(window, "duration_minutes", None)
    if duration is None:
        return None
    activity_date = getattr(window, "activity_date", None)
    if activity_date is None:
        return None
    start_minutes = getattr(window, "start_time", None)
    effective = activity_date
    if isinstance(activity_date, datetime) and start_minutes is not None:
        effective = activity_date + timedelta(minutes=int(start_minutes))
    mapping = SIGNAL_MAPPINGS["activity_duration"]
    category = getattr(window, "activity_category", None) or "desconhecida"
    return build_observation(
        resource_id=f"activity-{getattr(window, 'id', None)}",
        mapping=mapping,
        value=duration,
        effective=effective,
        subject=subject,
        device_id=device_id,
        text_override=f"{mapping.text}: {category}",
    )


def build_observation_bundle(observations: Iterable[dict], *, base_url: str = "") -> dict:
    """Empacota Observations num `Bundle` FHIR de tipo `searchset`.

    `searchset` (e não `collection`) porque é o que uma resposta a uma
    pesquisa `GET [base]/Observation?...` é, em FHIR — que é exatamente o
    que o endpoint da API faz. `total` é o número de resultados.
    """
    entries = []
    for obs in observations:
        entry: dict[str, Any] = {"resource": obs}
        if base_url:
            entry["fullUrl"] = f"{base_url.rstrip('/')}/Observation/{obs.get('id')}"
        entries.append(entry)
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "timestamp": _instant(datetime.now(timezone.utc)),
        "total": len(entries),
        "entry": entries,
    }


# ----------------------------------------------------------------------
# VALIDAÇÃO
# ----------------------------------------------------------------------
# Contrato do subconjunto de FHIR R4 Observation que este módulo produz,
# em JSON Schema (draft 2020-12). Serve para validação por biblioteca
# EXTERNA e independente nos testes (`jsonschema`, quando instalada) — é
# o que impede o critério de aceitação de ser circular ("o meu validador
# aprova o meu output"). O módulo em si não importa `jsonschema`: continua
# a funcionar sem ela.
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
            # `dateTime` FHIR, restringido aqui ao instante completo em UTC
            # que este módulo produz sempre.
            "type": "string",
            "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
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
            # UCUM: `system` e `code` andam sempre juntos (um código sem
            # sistema não é interpretável).
            "dependentRequired": {"code": ["system"], "system": ["code"]},
            "additionalProperties": False,
        },
        "dataAbsentReason": _CODEABLE_CONCEPT_SCHEMA,
    },
    # FHIR R4: só `resourceType`, `status` e `code` são obrigatórios.
    "required": ["resourceType", "status", "code"],
    # obs-6 (invariante FHIR): dataAbsentReason SHALL only be present if
    # Observation.value[x] is not present.
    "not": {"required": ["valueQuantity", "dataAbsentReason"]},
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
        "entry": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fullUrl": {"type": "string"},
                    "resource": OBSERVATION_JSON_SCHEMA,
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
    """Valida um `Observation` FHIR R4; devolve a lista de erros (vazia = válido).

    Cobre o que o JSON Schema acima cobre MAIS as invariantes que um
    schema não exprime bem (obs-6, obs-7 e a coerência UCUM). Devolve
    erros acumulados em vez de rebentar no primeiro — é mais útil tanto
    em teste como em diagnóstico.
    """
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

    # obs-7: se um component.code for igual ao Observation.code, o
    # Observation não pode ter value[x].
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
        # Um CodeableConcept sem coding NEM text não diz nada a ninguém.
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
    # UCUM: system e code andam sempre juntos.
    has_system = bool(quantity.get("system"))
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
    total = bundle.get("total")
    if total is not None and total != len(entries):
        errors.append(f"Bundle.total ({total}) não coincide com o número de entradas ({len(entries)})")
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict) or "resource" not in entry:
            errors.append(f"entry[{i}] tem de ter 'resource'")
            continue
        for err in validate_observation(entry["resource"]):
            errors.append(f"entry[{i}].resource: {err}")
    return errors
