"""storage.py — Serviço de persistência local (SQLite) para o bridge BLE.

CONTEXTO
--------
Até agora, os dados "ao vivo" que chegam do wearable via `ble_bridge.py`
só existiam enquanto a página do dashboard estivesse aberta — fechar o
browser ou o bridge perdia tudo. Este módulo adiciona uma camada de
persistência mínima em SQLite (motor concreto escolhido: SQLite, por ser
embutido, sem servidor separado a gerir, adequado ao uso local/pessoal
deste protótipo — ver PROJECT_STATUS.md, "Base de dados").

Escopo desta primeira versão (deliberadamente pequeno):
  - Guarda cada registo de sensores (`FullPlain` descodificado) e cada
    alerta de emergência recebido, com timestamp de receção.
  - Expõe funções simples de consulta (últimas N horas / últimos N
    registos) para o bridge poder responder a pedidos de histórico do
    dashboard.
  - NÃO faz agregações/estatísticas (isso fica para quando o dashboard
    precisar de as mostrar) — guarda os dados em bruto, granulares.

NÃO faz (por decisão consciente, para manter o âmbito pequeno e correto):
  - Não substitui ainda os gráficos de tendência/heatmap do dashboard
    (esses continuam com dados sintéticos, claramente rotulados como tal)
    — isso é um passo seguinte, de ligar o dashboard a este histórico real
    via um novo comando WebSocket, não implementado nesta primeira versão.
  - Não faz cifra da base de dados (ficheiro SQLite em texto simples no
    disco) — aceitável para um protótipo local, mas seria um requisito
    real antes de qualquer uso fora de um ambiente de desenvolvimento.
"""

from __future__ import annotations

import csv
import io
import sqlite3
import time
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "carewear_history.db"


def get_connection() -> sqlite3.Connection:
    """Liga à base de dados SQLite local (cria o ficheiro se não existir).

    `check_same_thread=False` porque o bridge corre em asyncio de thread
    única mas pode chamar a ligação a partir de diferentes callbacks —
    não há acesso concorrente real (é tudo sequencial no mesmo event
    loop), por isso isto é seguro aqui.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> sqlite3.Connection:
    """Cria as tabelas se ainda não existirem. Chamar uma vez no arranque
    do bridge, antes de qualquer insert/query."""
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sensor_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at REAL NOT NULL,
            device_timestamp INTEGER NOT NULL,
            ax REAL, ay REAL, az REAL,
            gx REAL, gy REAL, gz REAL,
            steps INTEGER,
            freefall INTEGER,
            inactivity INTEGER,
            spo2 INTEGER,
            hr INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS emergency_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at REAL NOT NULL,
            alert_type INTEGER NOT NULL,
            alert_name TEXT NOT NULL,
            seq INTEGER NOT NULL,
            device_timestamp_utc INTEGER NOT NULL
        )
        """
    )
    # Índice pelo timestamp de receção — a consulta mais comum é "últimas
    # N horas", por isso o índice acelera exatamente essa operação.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sensor_received_at ON sensor_records(received_at)"
    )
    conn.commit()
    return conn


def insert_record(conn: sqlite3.Connection, record: dict) -> None:
    """Grava um registo de sensores já descodificado (ver
    decode_full_plain() em ble_bridge.py) na base de dados."""
    conn.execute(
        """
        INSERT INTO sensor_records
            (received_at, device_timestamp, ax, ay, az, gx, gy, gz,
             steps, freefall, inactivity, spo2, hr)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            time.time(),
            record["ts"],
            record["ax"], record["ay"], record["az"],
            record["gx"], record["gy"], record["gz"],
            record["steps"],
            int(record["freefall"]),
            int(record["inactivity"]),
            record["spo2"],
            record["hr"],
        ),
    )
    conn.commit()


def insert_emergency_alert(conn: sqlite3.Connection, alert: dict) -> None:
    """Grava um alerta de emergência (ver decode_emergency_alert() em
    ble_bridge.py) — histórico permanente, nunca apagado automaticamente
    (ao contrário dos registos de sensores, que podem vir a precisar de
    uma política de retenção quando o volume crescer — não implementada
    ainda, ver "Próximos passos" no fim deste ficheiro)."""
    conn.execute(
        """
        INSERT INTO emergency_alerts
            (received_at, alert_type, alert_name, seq, device_timestamp_utc)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            time.time(),
            alert["alert_type"],
            alert["alert_name"],
            alert["seq"],
            alert["timestamp_utc"],
        ),
    )
    conn.commit()


def get_records_since(conn: sqlite3.Connection, hours: float) -> list[dict]:
    """Devolve os registos de sensores das últimas `hours` horas, mais
    antigos primeiro (ordem cronológica, útil para desenhar um gráfico)."""
    cutoff = time.time() - hours * 3600
    rows = conn.execute(
        "SELECT * FROM sensor_records WHERE received_at >= ? ORDER BY received_at ASC",
        (cutoff,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_recent_emergency_alerts(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """Devolve os últimos `limit` alertas de emergência, mais recentes
    primeiro."""
    rows = conn.execute(
        "SELECT * FROM emergency_alerts ORDER BY received_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def export_records_csv(conn: sqlite3.Connection, hours: float) -> str:
    """Exporta os registos de sensores das últimas `hours` horas como
    texto CSV (2026-07-03, pedido do utilizador: "quero que dê para
    exportar os dados também em CSV" — confirmado: CSV é lido diretamente
    por praticamente qualquer ferramenta SQL/de dados via import — SQLite
    `.import`, PostgreSQL `COPY`, MySQL `LOAD DATA`, Excel, pandas, etc.).

    Devolve o CSV como string (cabeçalho + linhas), para o bridge poder
    enviá-lo ao dashboard via WebSocket sem precisar de escrever um
    ficheiro no disco do servidor — o download acontece no browser.
    """
    records = get_records_since(conn, hours)
    buffer = io.StringIO()
    fieldnames = [
        "id", "received_at", "device_timestamp",
        "ax", "ay", "az", "gx", "gy", "gz",
        "steps", "freefall", "inactivity", "spo2", "hr",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for record in records:
        writer.writerow(record)
    return buffer.getvalue()


def export_emergency_alerts_csv(conn: sqlite3.Connection, limit: int = 1000) -> str:
    """Exporta o registo de emergências como CSV, mesma lógica de
    export_records_csv() acima."""
    alerts = get_recent_emergency_alerts(conn, limit=limit)
    buffer = io.StringIO()
    fieldnames = ["id", "received_at", "alert_type", "alert_name", "seq", "device_timestamp_utc"]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for alert in alerts:
        writer.writerow(alert)
    return buffer.getvalue()


def count_records(conn: sqlite3.Connection) -> int:
    """Nº total de registos de sensores guardados — útil para a vista
    'Exportar dados' do dashboard reportar contagens reais em vez de
    valores fixos de demonstração (ver TEMPLATES.exportar no dashboard)."""
    row = conn.execute("SELECT COUNT(*) AS n FROM sensor_records").fetchone()
    return row["n"] if row else 0


def get_daily_summary(conn: sqlite3.Connection, days: float = 7) -> list[dict]:
    """Agrega os registos de sensores por dia civil (UTC), para a vista
    "Tendência semanal" do dashboard poder mostrar um cartão de dados
    REAIS a par do gráfico sintético já existente (ver PROJECT_STATUS.md,
    "Base de dados" — próximo passo natural depois de `get_history`).

    Agregação feita aqui em SQL, não devolvendo os registos em bruto:
    `get_records_since()` pode ter dezenas de milhares de linhas numa
    janela de vários dias (o IMU amostra a ~14-52 registos/seg) — enviar
    isso ao browser via WebSocket e agregar em JavaScript seria lento e
    desnecessário quando o próprio SQLite já faz isto de forma eficiente.

    Por dia devolve:
      - `record_count`: nº de registos de sensores guardados nesse dia.
      - `avg_hr`/`hr_samples`: média de FC e quantas leituras válidas
        existem (pode ser 0 — a leitura de FC em hardware real tem um
        problema conhecido ainda por diagnosticar, ver PROJECT_STATUS.md,
        "HR nunca chega a ser lido").
      - `min_steps`/`max_steps`: extremos do contador cumulativo de
        passos (`g_stepCount` em Imu.cpp) dentro do dia — a diferença
        (max-min) é uma estimativa de passos nesse dia, que SUBESTIMA se
        o dispositivo reiniciar a meio do dia (o contador não é
        persistido, volta a 0 no arranque). Não há como distinguir isso
        só a partir destes dados.

    NÃO calcula horas de sono — não existe nenhuma deteção real de sono
    no firmware ainda (só a rotina simulada do dashboard o faz); esse
    campo fica de fora deliberadamente em vez de ser inventado.
    """
    cutoff = time.time() - days * 86400
    rows = conn.execute(
        """
        SELECT
            date(device_timestamp, 'unixepoch') AS day,
            COUNT(*) AS record_count,
            AVG(hr) AS avg_hr,
            COUNT(hr) AS hr_samples,
            MIN(steps) AS min_steps,
            MAX(steps) AS max_steps
        FROM sensor_records
        WHERE received_at >= ?
        GROUP BY day
        ORDER BY day ASC
        """,
        (cutoff,),
    ).fetchall()
    return [dict(row) for row in rows]


# Próximos passos (não implementados nesta primeira versão, ver
# PROJECT_STATUS.md): (1) política de retenção/limpeza para
# sensor_records não crescer indefinidamente (ex.: manter só os últimos
# N dias); (2) cifra do ficheiro .db se este serviço vier a correr fora
# de um ambiente de desenvolvimento local confiável.
