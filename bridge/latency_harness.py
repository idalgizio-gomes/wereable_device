"""Harness de latencia/jitter/packet-loss do WebSocket bridge->dashboard (C20).

O QUE MEDE COM PRECISAO (C20): liga-se ao mesmo WebSocket do dashboard e usa o
campo `_send_ts_ms` (epoch ms, adicionado em broadcast() de ble_bridge.py) para
calcular latency_ms = agora - _send_ts_ms no instante em que ESTE processo
recebe a mensagem. So valido a correr na mesma maquina que o bridge (sem isso
seria preciso NTP entre relogios, ver comentario em broadcast()).

O QUE MEDE COM PRECISAO (C13, sensor BLE -> bridge): o firmware publica, por
registo, {rec_seq, epoch_ms} numa characteristic separada nao-cifrada
(latencyProbeChar). O bridge correlaciona esse anuncio por rec_seq com o
momento em que o dado cifrado correspondente chega, e calcula o delta
(epoch_ms do probe vs. instante de receção no bridge). Esse delta e' anexado
a cada mensagem "record"/"live_record" enviada ao dashboard como
`_ble_latency_ms` (float, ms). Quando o campo nao vem (firmware antigo sem o
probe, ou registo sem correlacao encontrada), este harness simplesmente nao
conta essa amostra para o resumo C13.

Uso:
    python latency_harness.py --session <token de sessao> --duration 120 \\
        --out latencia_c20.csv

O <token de sessao> obtem-se autenticando via API (POST /api/auth/login) --
este script nao faz login sozinho de proposito, para nao duplicar logica de
autenticacao que ja existe em auth-navegacao.js/api.py.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics
import sys
import time
import urllib.parse

import websockets

DEFAULT_KINDS = {"record", "live_record", "vital_alert", "emergency_alert", "anomaly_detected"}


def build_url(host: str, port: int, tls: bool, session: str, token: str | None) -> str:
    scheme = "wss" if tls else "ws"
    params = {"session": session}
    if token:
        params["token"] = token
    return f"{scheme}://{host}:{port}/?{urllib.parse.urlencode(params)}"


async def collect(url: str, duration_s: float, kinds: set[str], out_path: str) -> None:
    rows = []
    ble_latencies: list[float] = []
    last_recv_ms_by_kind: dict[str, float] = {}
    last_seq_by_kind: dict[str, int] = {}
    seq_gaps = 0
    seq_gap_total_missing = 0
    n_matched = 0
    n_skipped_no_ts = 0

    print(f"[HARNESS] a ligar a {url.split('?')[0]} ...")
    async with websockets.connect(url) as ws:
        print("[HARNESS] ligado. A recolher...")
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                break

            recv_ms = time.time() * 1000
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue

            kind = payload.get("kind")
            if kind not in kinds:
                continue

            send_ts_ms = payload.get("_send_ts_ms")
            if send_ts_ms is None:
                n_skipped_no_ts += 1
                continue

            latency_ms = recv_ms - send_ts_ms
            inter_arrival_ms = None
            prev_recv = last_recv_ms_by_kind.get(kind)
            if prev_recv is not None:
                inter_arrival_ms = recv_ms - prev_recv
            last_recv_ms_by_kind[kind] = recv_ms

            rec_seq = payload.get("rec_seq")
            if isinstance(rec_seq, int):
                prev_seq = last_seq_by_kind.get(kind)
                if prev_seq is not None and rec_seq > prev_seq + 1:
                    missing = rec_seq - prev_seq - 1
                    seq_gaps += 1
                    seq_gap_total_missing += missing
                last_seq_by_kind[kind] = rec_seq

            ble_latency_ms = payload.get("_ble_latency_ms")
            if ble_latency_ms is not None:
                ble_latencies.append(ble_latency_ms)

            n_matched += 1
            rows.append({
                "recv_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(recv_ms / 1000)),
                "kind": kind,
                "rec_seq": rec_seq if rec_seq is not None else "",
                "latency_ms": round(latency_ms, 3),
                "ble_latency_ms": round(ble_latency_ms, 3) if ble_latency_ms is not None else "",
                "inter_arrival_ms": round(inter_arrival_ms, 3) if inter_arrival_ms is not None else "",
            })

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["recv_iso", "kind", "rec_seq", "latency_ms", "ble_latency_ms", "inter_arrival_ms"],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n[HARNESS] {n_matched} mensagens medidas, {n_skipped_no_ts} ignoradas "
          f"(sem _send_ts_ms -- reinicia o bridge se isto for alto, e' sinal de versao antiga).")
    print(f"[HARNESS] CSV escrito em {out_path}")

    if not rows:
        print("[HARNESS] AVISO: nenhuma mensagem medida -- confirma que o wearable esta ligado "
              "e a transmitir, e que a sessao tem permissao para ver dados clinicos.")
        return

    latencies = [r["latency_ms"] for r in rows]
    jitters = [r["inter_arrival_ms"] for r in rows if r["inter_arrival_ms"] != ""]

    def pct(data, p):
        s = sorted(data)
        k = int(round((len(s) - 1) * p))
        return s[k]

    print("\n=== Resumo latencia bridge->dashboard (C20), ms ===")
    print(f"  media={statistics.mean(latencies):.2f}  mediana={statistics.median(latencies):.2f}  "
          f"p95={pct(latencies, 0.95):.2f}  max={max(latencies):.2f}")
    if len(latencies) > 1:
        print(f"  desvio padrao={statistics.stdev(latencies):.2f}")

    if jitters:
        print("\n=== Resumo jitter (variacao do intervalo entre chegadas), ms ===")
        print(f"  media={statistics.mean(jitters):.2f}  mediana={statistics.median(jitters):.2f}  "
              f"p95={pct(jitters, 0.95):.2f}")

    print("\n=== Resumo latencia sensor BLE->bridge (C13), ms ===")
    if ble_latencies:
        print(f"  media={statistics.mean(ble_latencies):.2f}  mediana={statistics.median(ble_latencies):.2f}  "
              f"p95={pct(ble_latencies, 0.95):.2f}  max={max(ble_latencies):.2f}")
    else:
        print("  AVISO: nenhuma amostra de _ble_latency_ms nesta sessao -- C13 nao foi medido. "
              "Causas possiveis: o wearable ainda esta com firmware antigo sem o probe "
              "(latencyProbeChar), ou nao houve correlacao rec_seq<->epoch_ms bem sucedida "
              "no bridge durante a recolha.")

    print("\n=== Packet loss / gaps de rec_seq (proxy, so' para streams com rec_seq) ===")
    print(f"  gaps detetados={seq_gaps}  registos em falta estimados={seq_gap_total_missing}")
    print("  (gap aqui so' significa 'saltou rec_seq no canal WS' -- pode ser perda real no BLE "
          "ou so' um registo filtrado antes do broadcast; nao distingue as duas causas)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--tls", action="store_true", help="usa wss:// (CAREWEAR_WS_TLS=1 no bridge)")
    parser.add_argument("--session", required=True, help="token de sessao (login previo via API)")
    parser.add_argument("--token", default=None, help="CAREWEAR_WS_TOKEN, se estiver configurado no bridge")
    parser.add_argument("--duration", type=float, default=60.0, help="segundos de recolha (default 60)")
    parser.add_argument("--out", default="latencia_c20.csv")
    parser.add_argument("--kinds", default=",".join(sorted(DEFAULT_KINDS)),
                         help="lista de 'kind' a medir, separados por virgula")
    args = parser.parse_args()

    kinds = set(k.strip() for k in args.kinds.split(",") if k.strip())
    url = build_url(args.host, args.port, args.tls, args.session, args.token)

    try:
        asyncio.run(collect(url, args.duration, kinds, args.out))
    except KeyboardInterrupt:
        print("\n[HARNESS] interrompido pelo utilizador")
        sys.exit(130)


if __name__ == "__main__":
    main()
