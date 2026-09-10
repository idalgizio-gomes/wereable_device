#!/usr/bin/env python3
r"""cron_weekly_report.py — tarefa agendada que gera e arquiva o relatório semanal de cada
paciente ativo (RF-12), chamando /api/patients/{id}/weekly-report. Não reimplementa
agregação nem lógica clínica; só stdlib (sem dependências novas).

Autentica-se com uma api_auth.ApiKey de um utilizador de serviço dedicado (env
CAREWEAR_CRON_API_KEY), perfil clinician, associado via patient_caregivers só aos
pacientes a reportar — nunca chave de admin nem chave estática partilhada.

`end` (última data incluída) é fixado UMA vez no arranque (default: ontem UTC) para
todos os pedidos, evitando atravessar meia-noite numa execução longa.

Layout: <archive_dir>/<AAAA-Wnn>/patient-<uuid>.json + _run.json (manifesto). Semana
ISO 8601. Contém dados clínicos (RGPD) — manter fora do controlo de versões, 0700.

Idempotente por ficheiro existente (--force reescreve), escrita atómica (.tmp +
os.replace), falha por paciente não aborta os restantes. Exit codes: 0 ok, 1 falha
parcial, 2 não arrancou.

Provisionar a chave: `python3 -m api_auth create --email cron@carewear.local --label
cron-weekly-report`. Agendar com cron/systemd timer/schtasks (ver README/deploy notes).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

API_KEY_ENV = "CAREWEAR_CRON_API_KEY"
DEFAULT_BASE_URL = "http://127.0.0.1:8766"
DEFAULT_ARCHIVE_DIR = Path(__file__).resolve().parent / "reports_archive"
DEFAULT_TIMEOUT_SECONDS = 30

# estados por paciente, usados no manifesto e no resumo
STATUS_OK = "ok"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

EXIT_OK = 0
EXIT_PARTIAL_FAILURE = 1
EXIT_CANNOT_START = 2


class CronError(RuntimeError):
    """Erro que impede a execução de arrancar (não é falha de um paciente)."""


# ----------------------------------------------------------------------
# Credencial
# ----------------------------------------------------------------------

def resolve_api_key(env: Optional[dict] = None) -> str:
    """Lê a chave do utilizador de serviço do ambiente. Falha cedo se não existir."""
    key = (env if env is not None else os.environ).get(API_KEY_ENV, "").strip()
    if not key:
        raise CronError(
            f"Variável de ambiente {API_KEY_ENV} em falta ou vazia. "
            "Emita uma chave para o utilizador de serviço com "
            "`python3 -m api_auth create --email cron@carewear.local "
            "--label cron-weekly-report` e exporte-a (ficheiro de ambiente 0600)."
        )
    return key


def key_fingerprint(api_key: str) -> str:
    """Prefixo identificador da chave para o manifesto — nunca a chave inteira."""
    return api_key[:8] + "..." if len(api_key) > 8 else "(curta)"


# ----------------------------------------------------------------------
# Cliente HTTP mínimo
# ----------------------------------------------------------------------

class ApiClient:
    """GET autenticado com `X-API-Key`, resposta JSON. Só stdlib."""

    def __init__(self, base_url: str, api_key: str, timeout: int = DEFAULT_TIMEOUT_SECONDS):
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout

    def get_json(self, path: str, params: Optional[dict] = None) -> Any:
        url = self.base_url + path
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, method="GET")
        request.add_header("X-API-Key", self._api_key)
        request.add_header("Accept", "application/json")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))


# ----------------------------------------------------------------------
# Janela temporal e caminhos do arquivo
# ----------------------------------------------------------------------

def default_end_date(now: Optional[datetime] = None) -> date:
    """Último dia a incluir no relatório: ontem em UTC (hoje ainda está a decorrer)."""
    reference = now or datetime.now(timezone.utc)
    return (reference - timedelta(days=1)).date()


def parse_end_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise CronError(f"--end inválido: '{value}'. Formato esperado: AAAA-MM-DD.")


def week_folder_name(end: date) -> str:
    """Pasta da semana ISO 8601 de `end` (ex.: '2026-W36') — garante mesma pasta por semana."""
    iso_year, iso_week, _ = end.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def report_path(archive_dir: Path, end: date, patient_uuid: str) -> Path:
    """Caminho do relatório do paciente. uuid sanitizado (dado externo, evita path traversal)."""
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(patient_uuid))
    if not safe:
        raise CronError("uuid de paciente vazio ou inutilizável como nome de ficheiro")
    return Path(archive_dir) / week_folder_name(end) / f"patient-{safe}.json"


def write_json_atomic(path: Path, payload: Any) -> None:
    """Escreve JSON via .tmp + os.replace (atómico), evita ficheiro truncado numa interrupção."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, path)


# ----------------------------------------------------------------------
# Descoberta de pacientes
# ----------------------------------------------------------------------

def discover_patients(client: ApiClient) -> list[dict]:
    """Pacientes ativos = os associados à credencial via /api/patients/directory."""
    payload = client.get_json("/api/patients/directory")
    patients = payload.get("patients") if isinstance(payload, dict) else None
    if not isinstance(patients, list):
        raise CronError("Resposta inesperada de /api/patients/directory (sem lista 'patients')")
    return patients


def fetch_weekly_report(client: ApiClient, patient_id: int, end: date) -> dict:
    """Relatório semanal de um paciente, com a janela fixada por `end`."""
    return client.get_json(
        f"/api/patients/{patient_id}/weekly-report",
        {"end": end.isoformat()},
    )


# ----------------------------------------------------------------------
# Execução
# ----------------------------------------------------------------------

def build_envelope(report: Any, patient: dict, end: date, base_url: str, fingerprint: str) -> dict:
    """Envelope com metadados da execução em torno do relatório (que vai intacto)."""
    return {
        "schema": "carewear.weekly-report-archive/1",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "week": week_folder_name(end),
        "end": end.isoformat(),
        "base_url": base_url,
        "api_key_fingerprint": fingerprint,
        "patient": {
            "id": patient.get("id"),
            "uuid": patient.get("uuid"),
            "pseudonym": patient.get("pseudonym"),
        },
        "report": report,
    }


def process_patient(
    client: ApiClient,
    patient: dict,
    end: date,
    archive_dir: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    fingerprint: str = "",
) -> dict:
    """Trata UM paciente, nunca levanta (falha de um não pode abortar os restantes)."""
    entry: dict[str, Any] = {
        "patient_id": patient.get("id"),
        "patient_uuid": patient.get("uuid"),
        "status": STATUS_FAILED,
    }
    try:
        path = report_path(archive_dir, end, patient.get("uuid"))
        entry["path"] = str(path)
        if path.exists() and not force:
            entry["status"] = STATUS_SKIPPED
            entry["reason"] = "já arquivado nesta semana (use --force para reescrever)"
            return entry
        report = fetch_weekly_report(client, int(patient["id"]), end)
        if dry_run:
            entry["status"] = STATUS_OK
            entry["reason"] = "dry-run: nada foi escrito"
            return entry
        write_json_atomic(path, build_envelope(report, patient, end, client.base_url, fingerprint))
        entry["status"] = STATUS_OK
    except urllib.error.HTTPError as exc:
        entry["error"] = f"HTTP {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        entry["error"] = f"rede: {exc.reason}"
    except Exception as exc:  # noqa: BLE001 - deliberado, ver docstring
        entry["error"] = f"{type(exc).__name__}: {exc}"
    return entry


def run(
    *,
    base_url: str = DEFAULT_BASE_URL,
    api_key: Optional[str] = None,
    end: Optional[date] = None,
    archive_dir: Path = DEFAULT_ARCHIVE_DIR,
    force: bool = False,
    dry_run: bool = False,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    client_factory: Optional[Callable[..., ApiClient]] = None,
    log: Callable[[str], None] = print,
) -> dict:
    """Corre a tarefa inteira e devolve o manifesto. client_factory permite injetar um cliente falso nos testes."""
    api_key = api_key or resolve_api_key()
    end = end or default_end_date()  # fixada aqui, usada em todos os pedidos
    archive_dir = Path(archive_dir)
    fingerprint = key_fingerprint(api_key)

    factory = client_factory or ApiClient
    client = factory(base_url=base_url, api_key=api_key, timeout=timeout)

    started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    log(f"[cron] semana {week_folder_name(end)} (end={end.isoformat()}), arquivo em {archive_dir}")

    try:
        patients = discover_patients(client)
    except Exception as exc:  # noqa: BLE001
        raise CronError(f"Não foi possível descobrir os pacientes: {exc}") from exc

    results = [
        process_patient(
            client, patient, end, archive_dir,
            force=force, dry_run=dry_run, fingerprint=fingerprint,
        )
        for patient in patients
    ]

    counts = {
        STATUS_OK: sum(1 for r in results if r["status"] == STATUS_OK),
        STATUS_SKIPPED: sum(1 for r in results if r["status"] == STATUS_SKIPPED),
        STATUS_FAILED: sum(1 for r in results if r["status"] == STATUS_FAILED),
    }
    manifest = {
        "schema": "carewear.weekly-report-run/1",
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "week": week_folder_name(end),
        "end": end.isoformat(),
        "base_url": base_url,
        "api_key_fingerprint": fingerprint,
        "force": force,
        "dry_run": dry_run,
        "counts": counts,
        "patients": results,
    }
    if not dry_run:
        write_json_atomic(Path(archive_dir) / week_folder_name(end) / "_run.json", manifest)  # sempre reescrito

    log(
        f"[cron] {counts[STATUS_OK]} gerados, {counts[STATUS_SKIPPED]} saltados, "
        f"{counts[STATUS_FAILED]} falhados"
    )
    for result in results:
        if result["status"] == STATUS_FAILED:
            log(f"[cron] FALHA paciente id={result.get('patient_id')}: {result.get('error')}")
    return manifest


def exit_code_for(manifest: dict) -> int:
    return EXIT_PARTIAL_FAILURE if manifest["counts"][STATUS_FAILED] else EXIT_OK


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gera e arquiva o relatório semanal de cada paciente ativo (RF-12).",
        epilog=f"Credencial: variável de ambiente {API_KEY_ENV} (ver cabeçalho do ficheiro).",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="URL base da API CareWear.")
    parser.add_argument(
        "--end",
        default=None,
        help="Último dia do relatório (AAAA-MM-DD). Por omissão, ontem (UTC).",
    )
    parser.add_argument(
        "--archive-dir",
        default=str(DEFAULT_ARCHIVE_DIR),
        help="Raiz do arquivo. Deve ter permissões restritas (contém dados clínicos).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Reescreve relatórios já arquivados desta semana.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Faz os pedidos mas não escreve nada em disco.",
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        manifest = run(
            base_url=args.base_url,
            end=parse_end_date(args.end) if args.end else None,
            archive_dir=Path(args.archive_dir),
            force=args.force,
            dry_run=args.dry_run,
            timeout=args.timeout,
        )
    except CronError as exc:
        print(f"[cron] ERRO: {exc}", file=sys.stderr)
        return EXIT_CANNOT_START
    return exit_code_for(manifest)


if __name__ == "__main__":
    raise SystemExit(main())
