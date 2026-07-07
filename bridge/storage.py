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
  - Não faz cifra da base de dados (ficheiro SQLite em texto simples no
    disco) — aceitável para um protótipo local, mas seria um requisito
    real antes de qualquer uso fora de um ambiente de desenvolvimento.

RETENÇÃO CONFIGURÁVEL (`get_retention_days`/`set_retention_days`, 2026-07-04)
------------------------------------------------------------------------------
`DEFAULT_RETENTION_DAYS` abaixo continua a ser o valor de arranque, mas
deixou de ser a única fonte de verdade: uma tabela `settings` (par
chave/valor) guarda um valor efetivo, editável pelo utilizador através do
dashboard (vista "Exportar dados", Médico/Técnico — ver
`bridge/ble_bridge.py`, comandos `get_retention_days`/`set_retention_days`).
Isto não muda a natureza da decisão (continua não certificada/validada
legalmente, ver acima) — só deixa de estar fixa no código-fonte, como
pedido explicitamente no backlog (PROJECT_STATUS.md, Prioridade 4).

RETENÇÃO DE DADOS (`purge_old_sensor_records`, ver abaixo)
------------------------------------------------------------
`sensor_records` cresce indefinidamente sem limpeza (o IMU produz ~14-52
registos/seg). Pesquisa aplicada nesta sessão confirma que reter dados de
saúde além do necessário é um problema real e comum ("83% dos modelos de
IA em saúde revistos violavam políticas de retenção do RGPD/GDPR, guardando
dados de pacientes por mais tempo do que o necessário"). `DEFAULT_RETENTION_DAYS`
abaixo é um valor por omissão razoável para um protótipo de uso pessoal,
NÃO uma política de retenção certificada/validada legalmente — a
decisão real de quantos dias reter dados clínicos de um utente é do
utilizador/responsável pelos dados, não algo que este código possa decidir
sozinho. `emergency_alerts` propositadamente NÃO é limpo por esta política
(histórico de segurança, mantido para sempre — ver insert_emergency_alert()).
"""

from __future__ import annotations

import csv
import io
import sqlite3
import time
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "carewear_history.db"

# Valor por omissão para purge_old_sensor_records() (ver docstring do
# módulo acima) — configurável por quem correr o bridge, não uma decisão
# de compliance final.
DEFAULT_RETENTION_DAYS = 30


def get_connection() -> sqlite3.Connection:
    """Liga à base de dados SQLite local (cria o ficheiro se não existir).

    `check_same_thread=False` porque o bridge corre em asyncio de thread
    única mas pode chamar a ligação a partir de diferentes callbacks —
    não há acesso concorrente real (é tudo sequencial no mesmo event
    loop), por isso isto é seguro aqui.

    *** OTIMIZAÇÃO DE CPU (2026-07-07, rotina diária) ***: `PRAGMA
    journal_mode=WAL` + `synchronous=NORMAL`. `insert_record()` (abaixo)
    é chamado de forma síncrona, direto no event loop asyncio, a partir
    de `_on_dump_data()` em `ble_bridge.py` — o callback de notificação
    BLE que corre a cada registo de sensores, até ~52/seg (taxa do IMU,
    ver comentário de `_on_dump_data`). Com o modo por omissão do SQLite
    (rollback journal + `synchronous=FULL`), CADA `conn.commit()` faz até
    2 `fsync()` (journal + ficheiro principal) — uma operação de disco
    síncrona que tipicamente custa alguns a várias dezenas de
    milissegundos, bloqueando o único thread do event loop nesse
    intervalo (atrasando o envio de WebSockets, outras notificações BLE
    e a task de retenção periódica, tudo a correr no mesmo loop). Ao
    ritmo documentado, isso é até ~52 fsyncs/seg (~104 com o modo por
    omissão) só para persistência local. Em WAL, `commit()` só acrescenta
    ao ficheiro `-wal` (sem fsync a cada escrita, com checkpoint
    periódico automático do próprio SQLite) — no mesmo protótipo local
    de uso pessoal já documentado acima (sem requisitos de durabilidade
    contra perda de energia), isto elimina a bloqueio do event loop por
    registo sem mudar a lógica de commit-por-registo já existente (ao
    contrário de acumular/atrasar commits, que arriscaria perder mais
    registos num encerramento abrupto do bridge — não há hoje nenhum
    "flush no shutdown" a que isso pudesse ficar ligado).
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
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
    # Tabela genérica de configuração (par chave/valor) — hoje só guarda
    # "retention_days" (ver get_retention_days/set_retention_days abaixo),
    # mas fica pensada para outras opções configuráveis futuras sem
    # precisar de nova migração de esquema.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
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


RETENTION_DAYS_SETTING_KEY = "retention_days"
# Limites de sanidade do valor introduzido pelo utilizador (não é uma
# política de compliance — só evita valores sem sentido como 0 ou
# negativos, ou tão grandes que a limpeza nunca teria efeito prático).
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 3650  # 10 anos


def get_retention_days(conn: sqlite3.Connection) -> float:
    """Devolve a retenção atualmente configurada (dias), ou
    `DEFAULT_RETENTION_DAYS` se o utilizador nunca a tiver alterado."""
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (RETENTION_DAYS_SETTING_KEY,)
    ).fetchone()
    if row is None:
        return DEFAULT_RETENTION_DAYS
    try:
        return float(row["value"])
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS


def set_retention_days(conn: sqlite3.Connection, days: float) -> float:
    """Atualiza a retenção configurada (ver docstring do módulo,
    "RETENÇÃO CONFIGURÁVEL"). Lança `ValueError` se `days` estiver fora dos
    limites de sanidade — quem chama (ver ble_bridge.py) deve apanhar isto
    e devolver um erro claro ao dashboard, em vez de gravar um valor sem
    sentido silenciosamente."""
    days = float(days)
    if not (MIN_RETENTION_DAYS <= days <= MAX_RETENTION_DAYS):
        raise ValueError(
            f"retenção tem de estar entre {MIN_RETENTION_DAYS} e {MAX_RETENTION_DAYS} dias"
        )
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (RETENTION_DAYS_SETTING_KEY, str(days)),
    )
    conn.commit()
    return days


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


def purge_old_sensor_records(conn: sqlite3.Connection, days: float = DEFAULT_RETENTION_DAYS) -> int:
    """Apaga registos de sensores mais antigos que `days` dias (ver
    política de retenção na docstring do módulo). Devolve o nº de linhas
    apagadas, para o chamador poder registar/reportar.

    Só afeta `sensor_records` — `emergency_alerts` é histórico de
    segurança, propositadamente nunca apagado automaticamente (ver
    insert_emergency_alert()). Chamado pelo bridge (`ble_bridge.py`) no
    arranque e periodicamente enquanto corre (ver
    BleBridge.periodic_retention_task()), não pelo dashboard. `days` por
    omissão é `DEFAULT_RETENTION_DAYS` só como referência de leitura desta
    função isolada — `ble_bridge.py` passa sempre o valor efetivo de
    `get_retention_days()` (configurável, ver acima), não esta omissão.
    """
    cutoff = time.time() - days * 86400
    cur = conn.execute("DELETE FROM sensor_records WHERE received_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


# Próximos passos (não implementados nesta primeira versão, ver
# PROJECT_STATUS.md): cifra do ficheiro .db se este serviço vier a
# correr fora de um ambiente de desenvolvimento local confiável.
# (Retenção configurável pelo utilizador — ver get_retention_days/
# set_retention_days acima — já deixou de estar nesta lista, 2026-07-04.)
