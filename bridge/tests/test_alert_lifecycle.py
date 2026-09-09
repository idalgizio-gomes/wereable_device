"""RF-07 (gravidade, motivo, confirmação e escalonamento) e RF-08
(registo da ação tomada) — lado do bridge (2026-09-07).

Contexto que estes testes fixam: a tabela `alerts` existia no esquema
desde a migração inicial com TODAS as colunas necessárias (severity,
read_at, escalated_to_severity/escalated_at, resolved_by_user_id/
resolved_at/resolution_note) e nenhuma linha de código alguma vez escreveu
ou leu uma única delas. Os alertas viviam só como mensagens WebSocket
efémeras: fechar o browser apagava-os.

Corre contra SQLite em memória (ver conftest.py), sem hardware BLE nem
rede — mesmo padrão de test_vital_alert_broadcast.py.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest

import ble_bridge
import vital_alerts


class FakeWebSocket:
    def __init__(self, role="clinician", user_id=None):
        self.sent = []
        self._carewear_user_role = role
        self._carewear_user_id = user_id

    async def send(self, message):
        self.sent.append(json.loads(message))


@pytest.fixture
def bridge():
    b = ble_bridge.BleBridge()
    # Sem isto, o limite de taxa de escrita (WRITE_COMMAND_MIN_INTERVAL_S,
    # 2s) faz falhar a segunda confirmação de cada teste. É um limite
    # global por nome de comando e partilhado entre instâncias só através
    # do próprio objeto, por isso basta limpá-lo aqui.
    b._last_write_command_monotonic.clear()
    # O SQLite em memória (ver conftest.py) é criado uma única vez a nível
    # de módulo em storage_advanced, por isso a tabela `alerts` sobrevive
    # entre testes — e o escalonamento, que varre TODOS os alertas por
    # confirmar do dispositivo, apanharia os alertas deixados pelos testes
    # anteriores. Cada teste arranca com a tabela vazia.
    import storage_advanced as sa
    b.orm.session.query(sa.Alert).delete()
    b.orm.session.commit()
    return b


def _cmd(bridge, ws, **payload):
    asyncio.run(bridge.handle_dashboard_command(ws, json.dumps(payload)))
    return ws.sent


def _cria_alerta(bridge, severity="warning", alert_type="abnormal_vitals_hr"):
    uuid = bridge._persist_alert(
        alert_type=alert_type,
        severity=severity,
        title="Frequência cardíaca fora do intervalo definido",
        description="Frequência cardíaca em 130bpm — acima do limiar máximo definido (100bpm).",
        raw_data={"vital": "hr", "level": "high", "value": 130, "limit": 100},
    )
    assert uuid, "persistencia indisponivel — o resto do teste nao faz sentido"
    return uuid


def _linha(bridge, uuid):
    import storage_advanced as sa
    return bridge.orm.session.query(sa.Alert).filter(sa.Alert.uuid == uuid).one()


class TestSeveridadeDoAlerta:
    """RF-07, "cada alerta tem nível (info/aviso/sério/crítico)". Antes
    desta data um alerta de FC chegava ao dashboard sem severidade
    nenhuma: 92 bpm com limite 90 e 160 bpm com limite 90 eram
    visualmente idênticos."""

    def test_escada_e_a_do_esquema_da_base_de_dados(self):
        # A CheckConstraint de alerts.severity aceita exatamente estes.
        assert vital_alerts.SEVERITY_LADDER == ("info", "warning", "serious", "critical")

    def test_desvio_marginal_e_aviso(self):
        alerta = {"vital": "hr", "level": "high", "value": 105, "limit": 100}
        assert vital_alerts.severity_for_vital_alert(alerta) == "warning"

    def test_desvio_grande_e_serio(self):
        alerta = {"vital": "hr", "level": "high", "value": 125, "limit": 100}
        assert vital_alerts.severity_for_vital_alert(alerta) == "serious"

    def test_desvio_extremo_e_critico(self):
        alerta = {"vital": "hr", "level": "high", "value": 150, "limit": 100}
        assert vital_alerts.severity_for_vital_alert(alerta) == "critical"

    def test_spo2_usa_pontos_absolutos_e_nao_percentagem_do_limiar(self):
        """94% e 84% estão ambos "abaixo de 95", mas clinicamente não são
        a mesma coisa — e em percentagem do limiar a diferença entre eles
        seria de apenas 11%, o que os poria no mesmo nível."""
        assert vital_alerts.severity_for_vital_alert(
            {"vital": "spo2", "level": "low", "value": 94, "limit": 95}) == "warning"
        assert vital_alerts.severity_for_vital_alert(
            {"vital": "spo2", "level": "low", "value": 88, "limit": 95}) == "serious"
        assert vital_alerts.severity_for_vital_alert(
            {"vital": "spo2", "level": "low", "value": 84, "limit": 95}) == "critical"

    def test_next_severity_percorre_a_escada_e_para_no_topo(self):
        assert vital_alerts.next_severity("info") == "warning"
        assert vital_alerts.next_severity("warning") == "serious"
        assert vital_alerts.next_severity("serious") == "critical"
        assert vital_alerts.next_severity("critical") is None

    def test_next_severity_falha_fechado_em_valor_desconhecido(self):
        """Um valor inesperado não pode saltar para 'critical'."""
        assert vital_alerts.next_severity("catastrofico") is None


class TestPersistenciaDoAlerta:
    def test_alerta_de_vitais_e_gravado_com_severidade_e_motivo(self, bridge):
        ws = FakeWebSocket()
        bridge.ws_clients.add(ws)

        async def run():
            bridge._maybe_broadcast_vital_alert(
                "hr", {"vital": "hr", "level": "high", "value": 150, "limit": 100}, {}
            )
            await asyncio.sleep(0.02)

        asyncio.run(run())

        difundido = [m for m in ws.sent if m.get("kind") == "vital_alert"][0]
        assert difundido["severity"] == "critical"
        assert difundido["alert_uuid"]

        linha = _linha(bridge, difundido["alert_uuid"])
        assert linha.severity == "critical"
        # "motivo textual legível que explique porque foi gerado" — a
        # descrição tem de trazer os números reais, não só o veredito.
        assert "150" in linha.description and "100" in linha.description
        assert linha.read_at is None and linha.resolved_at is None

    def test_get_alerts_devolve_o_alerta_com_severidade_efetiva(self, bridge):
        uuid = _cria_alerta(bridge, severity="warning")
        ws = FakeWebSocket()
        _cmd(bridge, ws, cmd="get_alerts")

        resposta = [m for m in ws.sent if m["kind"] == "alerts"][0]
        alerta = next(a for a in resposta["alerts"] if a["uuid"] == uuid)
        assert alerta["severity"] == "warning"
        assert alerta["effective_severity"] == "warning"
        assert alerta["reason"].startswith("Frequência cardíaca em 130bpm")
        assert resposta["escalation_minutes"] == bridge.ALERT_ESCALATION_MINUTES


class TestConfirmacao:
    """RF-07 "botão de confirmação" + RF-08 "ação tomada e nota livre"."""

    def test_confirmar_grava_acao_nota_utilizador_e_instante(self, bridge):
        uuid = _cria_alerta(bridge)
        # `resolved_by_user_id` é uma FOREIGN KEY para users(id) — tem de
        # ser um utilizador que exista mesmo. O utilizador criado no
        # bootstrap do OrmPersistence é o equivalente ao cuidador com
        # sessão aberta no dashboard.
        utilizador = bridge.orm.user_id
        ws = FakeWebSocket(user_id=utilizador)

        _cmd(bridge, ws, cmd="confirm_alert", alert_uuid=uuid,
             action="verifiquei_presencialmente", note="Estava a dormir, respiração normal.")

        resultado = [m for m in ws.sent if m["kind"] == "confirm_alert_result"][0]
        assert resultado["ok"] is True

        linha = _linha(bridge, uuid)
        assert linha.resolved_by_user_id == utilizador
        assert linha.resolved_at is not None
        assert linha.read_at is not None  # confirmar implica ter visto
        assert "verifiquei_presencialmente" in linha.resolution_note
        assert "Estava a dormir" in linha.resolution_note

    def test_acao_fora_da_allowlist_e_recusada(self, bridge):
        uuid = _cria_alerta(bridge)
        ws = FakeWebSocket()
        _cmd(bridge, ws, cmd="confirm_alert", alert_uuid=uuid, action="ignorei", note="")

        resultado = [m for m in ws.sent if m["kind"] == "confirm_alert_result"][0]
        assert resultado["ok"] is False
        assert _linha(bridge, uuid).resolved_at is None

    def test_nota_demasiado_longa_e_recusada(self, bridge):
        uuid = _cria_alerta(bridge)
        ws = FakeWebSocket()
        _cmd(bridge, ws, cmd="confirm_alert", alert_uuid=uuid,
             action="falso_alarme", note="x" * (ble_bridge.ALERT_RESOLUTION_NOTE_MAX_CHARS + 1))

        resultado = [m for m in ws.sent if m["kind"] == "confirm_alert_result"][0]
        assert resultado["ok"] is False
        assert _linha(bridge, uuid).resolved_at is None

    def test_nota_com_html_e_guardada_literalmente(self, bridge):
        """A nota é texto livre e É guardada tal como foi escrita — o
        escaping é responsabilidade de quem a mostra (escapeHtml() no
        dashboard). Este teste fixa essa fronteira: se algum dia o bridge
        começar a "limpar" a nota, o dashboard deixa de poder assumir que o
        que recebe é o que o cuidador escreveu."""
        uuid = _cria_alerta(bridge)
        ws = FakeWebSocket()
        _cmd(bridge, ws, cmd="confirm_alert", alert_uuid=uuid,
             action="falso_alarme", note="<script>alert(1)</script>")

        assert "<script>" in _linha(bridge, uuid).resolution_note

    def test_alerta_desconhecido_devolve_erro_sem_rebentar(self, bridge):
        ws = FakeWebSocket()
        _cmd(bridge, ws, cmd="confirm_alert", alert_uuid="nao-existe",
             action="falso_alarme", note="")
        resultado = [m for m in ws.sent if m["kind"] == "confirm_alert_result"][0]
        assert resultado["ok"] is False
        assert resultado["error"] == "alerta desconhecido"


class TestEscalonamentoPorTempo:
    """RF-07: "sem confirmação ao fim de N minutos, escala para o nível
    seguinte e regista escalated_at/escalated_to_severity"."""

    def _agora_mais(self, bridge, minutos):
        return datetime.utcnow() + timedelta(minutes=minutos)

    def test_alerta_nao_confirmado_escala_um_nivel(self, bridge):
        uuid = _cria_alerta(bridge, severity="warning")
        futuro = self._agora_mais(bridge, bridge.ALERT_ESCALATION_MINUTES + 1)

        escalados = asyncio.run(bridge._escalate_overdue_alerts(now=futuro))

        assert [a["uuid"] for a in escalados] == [uuid]
        linha = _linha(bridge, uuid)
        assert linha.severity == "warning"          # o nível original não muda
        assert linha.escalated_to_severity == "serious"
        assert linha.escalated_at is not None

    def test_antes_do_prazo_nao_escala(self, bridge):
        uuid = _cria_alerta(bridge, severity="warning")
        quase = self._agora_mais(bridge, bridge.ALERT_ESCALATION_MINUTES - 1)

        assert asyncio.run(bridge._escalate_overdue_alerts(now=quase)) == []
        assert _linha(bridge, uuid).escalated_to_severity is None

    def test_alerta_confirmado_nunca_escala(self, bridge):
        """O critério de aceitação do requisito: é a confirmação que trava
        o escalonamento."""
        uuid = _cria_alerta(bridge, severity="warning")
        ws = FakeWebSocket()
        _cmd(bridge, ws, cmd="confirm_alert", alert_uuid=uuid,
             action="contactei_o_utente", note="Falei com ela, está bem.")

        futuro = self._agora_mais(bridge, 10 * bridge.ALERT_ESCALATION_MINUTES)
        assert asyncio.run(bridge._escalate_overdue_alerts(now=futuro)) == []
        assert _linha(bridge, uuid).escalated_to_severity is None

    def test_escalonamento_e_gradual_e_nao_salta_para_critico(self, bridge):
        uuid = _cria_alerta(bridge, severity="info")
        futuro = self._agora_mais(bridge, 10 * bridge.ALERT_ESCALATION_MINUTES)

        asyncio.run(bridge._escalate_overdue_alerts(now=futuro))
        assert _linha(bridge, uuid).escalated_to_severity == "warning"

    def test_escalonamentos_sucessivos_sobem_um_nivel_de_cada_vez(self, bridge):
        uuid = _cria_alerta(bridge, severity="info")
        n = bridge.ALERT_ESCALATION_MINUTES
        for esperado, minutos in (("warning", n + 1), ("serious", 2 * n + 2), ("critical", 3 * n + 3)):
            asyncio.run(bridge._escalate_overdue_alerts(now=self._agora_mais(bridge, minutos)))
            assert _linha(bridge, uuid).escalated_to_severity == esperado

    def test_critico_nao_escala_mais(self, bridge):
        uuid = _cria_alerta(bridge, severity="critical")
        futuro = self._agora_mais(bridge, 100 * bridge.ALERT_ESCALATION_MINUTES)
        assert asyncio.run(bridge._escalate_overdue_alerts(now=futuro)) == []
        assert _linha(bridge, uuid).escalated_to_severity is None

    def test_get_alerts_mostra_o_nivel_escalado_como_efetivo(self, bridge):
        uuid = _cria_alerta(bridge, severity="warning")
        asyncio.run(bridge._escalate_overdue_alerts(
            now=self._agora_mais(bridge, bridge.ALERT_ESCALATION_MINUTES + 1)))

        ws = FakeWebSocket()
        _cmd(bridge, ws, cmd="get_alerts")
        alerta = next(a for a in [m for m in ws.sent if m["kind"] == "alerts"][0]["alerts"]
                      if a["uuid"] == uuid)
        assert alerta["severity"] == "warning"
        assert alerta["effective_severity"] == "serious"
        assert alerta["escalated_at"] is not None


class TestAutorizacaoDosComandosNovos:
    """RF-02 continua a valer para tudo o que foi acrescentado — um
    comando novo fora de WS_COMMAND_ROLES nunca seria recusado por perfil."""

    def test_comandos_novos_estao_registados(self):
        assert "get_alerts" in ble_bridge.WS_COMMAND_ROLES
        assert "confirm_alert" in ble_bridge.WS_COMMAND_ROLES

    def test_admin_nao_ve_nem_confirma_alertas(self, bridge):
        """Um alerta traz título, motivo clínico e nota do cuidador — é
        dossiê clínico, e o admin nunca abre o dossiê de ninguém."""
        uuid = _cria_alerta(bridge)
        for payload in ({"cmd": "get_alerts"},
                        {"cmd": "confirm_alert", "alert_uuid": uuid,
                         "action": "falso_alarme", "note": ""}):
            ws = FakeWebSocket(role="admin")
            _cmd(bridge, ws, **payload)
            assert any(m.get("error") == "nao_autorizado" for m in ws.sent), payload
        assert _linha(bridge, uuid).resolved_at is None

    def test_cuidador_confirma_alertas(self, bridge):
        """Quem recebe o alerta tem de o poder confirmar — recusar aqui
        tornava o RF-07 inalcançável para o perfil Utente/Família."""
        uuid = _cria_alerta(bridge)
        ws = FakeWebSocket(role="family", user_id=bridge.orm.user_id)
        _cmd(bridge, ws, cmd="confirm_alert", alert_uuid=uuid,
             action="sem_acao_necessaria", note="")
        assert [m for m in ws.sent if m["kind"] == "confirm_alert_result"][0]["ok"] is True
        assert _linha(bridge, uuid).resolved_by_user_id == bridge.orm.user_id
