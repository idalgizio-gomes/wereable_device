"""RF-02 — autorizacao por perfil no canal WebSocket.

Ate 2026-09-07 handle_dashboard_command() despachava os 17 comandos sem
consultar identidade nem perfil: a sessao era resolvida no handshake mas
so' alimentava o campo user_id do registo de auditoria. Uma ligacao
anonima executava get_history, export_csv, set_consent, set_retention_days
e o destrutivo reset_readings.

Estes testes sao o criterio de aceitacao do requisito: provam que o
comando e' recusado ANTES de produzir qualquer efeito, e que a recusa
distingue os perfis em vez de bloquear tudo.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ble_bridge


class FakeWebSocket:
    def __init__(self, role=None):
        self.sent = []
        self._carewear_user_role = role

    async def send(self, message):
        self.sent.append(json.loads(message))


def _despacha(role, cmd, **extra):
    bridge = ble_bridge.BleBridge()
    ws = FakeWebSocket(role)
    payload = {"cmd": cmd}
    payload.update(extra)
    asyncio.run(bridge.handle_dashboard_command(ws, json.dumps(payload)))
    return ws.sent


def _foi_recusado(enviados, cmd):
    return any(
        m.get("kind") == "command_result"
        and m.get("cmd") == cmd
        and m.get("ok") is False
        and m.get("error") == "nao_autorizado"
        for m in enviados
    )


TODOS_OS_COMANDOS = sorted(ble_bridge.WS_COMMAND_ROLES)


@pytest.mark.parametrize("cmd", TODOS_OS_COMANDOS)
def test_ligacao_sem_perfil_e_recusada_em_todos_os_comandos(cmd):
    """Uma ligacao sem sessao valida nao tem perfil, e nenhum comando passa."""
    assert _foi_recusado(_despacha(None, cmd), cmd)


@pytest.mark.parametrize("cmd", TODOS_OS_COMANDOS)
def test_perfil_desconhecido_e_recusado(cmd):
    """Um valor de perfil fora dos previstos falha fechado."""
    assert _foi_recusado(_despacha("qualquer_coisa", cmd), cmd)


COMANDOS_SO_CLINICOS = [
    "reset_readings",
    "get_retention_days",
    "set_retention_days",
    "get_consent_status",
    "set_consent",
    "list_model_versions",
    "activate_model_version",
]


@pytest.mark.parametrize("cmd", COMANDOS_SO_CLINICOS)
def test_cuidador_nao_executa_comandos_exclusivos_do_perfil_clinico(cmd):
    """O perfil Utente/Familia nao apaga dados nem altera politicas de
    governacao: reset_readings e' destrutivo, e retencao, consentimento e
    versao do modelo sao carregados pela vista 'exportar', exclusiva do
    perfil Medico/Tecnico."""
    assert _foi_recusado(_despacha("family", cmd), cmd)


COMANDOS_PARTILHADOS = [
    "get_daily_trend",
    "get_thresholds",
    "get_episode_timeline",
    "acknowledge_alert",
    # RF-07/RF-08 (2026-09-07): confirmar um alerta e' precisamente a acao
    # de quem o recebe — se o perfil Utente/Familia nao pudesse confirmar,
    # o escalonamento nunca poderia ser travado por quem esta' com o
    # utente. Ver tambem test_alert_lifecycle.py.
    "get_alerts",
    "confirm_alert",
]


@pytest.mark.parametrize("cmd", COMANDOS_PARTILHADOS)
def test_cuidador_executa_os_comandos_das_suas_vistas(cmd):
    """As vistas 'vitais', 'tendencia', 'alertas' e 'emergencias' pertencem
    ao perfil Utente/Familia — recusar estes comandos partiria o dashboard
    desse perfil, e o requisito nao e' bloquear tudo."""
    assert not _foi_recusado(_despacha("family", cmd), cmd)


def test_admin_nao_acede_a_dados_clinicos():
    """Decisao documentada no cabecalho de web/dashboard/admin-view.js: o
    administrador navega entre paginas de gestao mas nunca abre o dossie
    clinico de ninguem."""
    for cmd in ("get_history", "get_daily_trend", "export_csv", "get_episode_timeline"):
        assert _foi_recusado(_despacha("admin", cmd), cmd), cmd


def test_recusa_acontece_antes_de_qualquer_efeito():
    """A recusa e' a UNICA mensagem devolvida: nada de dados a seguir."""
    enviados = _despacha("family", "set_retention_days", days=1)
    assert len(enviados) == 1
    assert enviados[0]["error"] == "nao_autorizado"


def test_comando_desconhecido_continua_a_ser_ignorado_em_silencio():
    """Comportamento pre-existente que nao deve mudar: um comando que nao
    esta no mapa nao e' 'recusado', e' simplesmente ignorado."""
    enviados = _despacha("clinician", "comando_que_nao_existe")
    assert enviados == []
