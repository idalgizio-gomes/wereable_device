"""RF-05 — deteção e sinalização de não-uso do dispositivo (2026-09-07).

Critério de aceitação testado aqui: ausência de sinal cutâneo E de
movimento por MAIS de 30 minutos gera o estado "dispositivo removido".

O teste que mais importa neste ficheiro não é o do caminho feliz, é o
`TestDistincaoLigacaoVsRemocao`: o requisito existe precisamente porque
"sem dados porque foi retirado" e "sem dados porque a ligação caiu"
significam coisas diferentes para o cuidador, e um detetor que baseasse a
decisão só no silêncio confundiria as duas.

Não há hardware BLE nem base de dados envolvidos — `WearDetector` é uma
máquina de estados pura com o tempo injetado (`now_ts`), pelo que 30
minutos de ausência custam zero segundos de teste.
"""
from __future__ import annotations

import vital_alerts


MINUTO = 60.0
LIMITE = vital_alerts.WEAR_ABSENCE_SECONDS  # 30 min


def registo(hr=None, spo2=None, ax=0.0, ay=0.0, az=1.0, inactivity=True):
    """Registo FullPlain já descodificado, no formato que
    decode_full_plain() produz (ver ble_bridge.py). Por omissão: pousado
    (só gravidade em az), imóvel e sem leitura de PPG — ou seja, o caso
    "fora do pulso"."""
    return {
        "ts": 0, "ax": ax, "ay": ay, "az": az,
        "gx": 0.0, "gy": 0.0, "gz": 0.0,
        "steps": 0, "freefall": False, "inactivity": inactivity,
        "spo2": spo2, "hr": hr, "pacing_index": 0,
    }


class TestDetecaoDeRemocao:
    def test_sem_pele_e_sem_movimento_acima_de_30min_gera_removido(self):
        d = vital_alerts.WearDetector()
        # Primeira amostra: arranca os contadores, estado passa a 'worn'
        # (há observação a decorrer, ainda sem ausência acumulada).
        primeiro = d.observe(registo(hr=70), now_ts=0.0)
        assert primeiro["state"] == vital_alerts.WEAR_STATE_WORN

        # 30 min exatos ainda NÃO chegam — o critério é "mais de 30 min".
        assert d.observe(registo(), now_ts=LIMITE) is None
        assert d.state == vital_alerts.WEAR_STATE_WORN

        evento = d.observe(registo(), now_ts=LIMITE + 1)
        assert evento is not None
        assert evento["state"] == vital_alerts.WEAR_STATE_REMOVED
        assert evento["previous_state"] == vital_alerts.WEAR_STATE_WORN

    def test_evento_traz_motivo_textual_com_os_minutos_reais(self):
        """Mesma exigência do RF-07 aplicada ao RF-05: o cuidador tem de
        ler PORQUÊ, não só o veredito."""
        d = vital_alerts.WearDetector()
        d.observe(registo(hr=70), now_ts=0.0)
        evento = d.observe(registo(), now_ts=45 * MINUTO)

        assert evento["seconds_without_skin"] == int(45 * MINUTO)
        assert evento["seconds_without_motion"] == int(45 * MINUTO)
        assert "45 min" in evento["explanation"]
        # A explicação tem de afirmar explicitamente que não foi falha de
        # ligação — é essa frase que distingue os dois casos para quem lê.
        assert "ligação" in evento["explanation"]

    def test_estado_removido_nao_se_repete_a_cada_amostra(self):
        """Mesmo debounce de _maybe_broadcast_vital_alert: o IMU produz
        ~54 amostras/seg; um evento por amostra inundava o dashboard."""
        d = vital_alerts.WearDetector()
        d.observe(registo(hr=70), now_ts=0.0)
        assert d.observe(registo(), now_ts=LIMITE + 1) is not None
        for i in range(10):
            assert d.observe(registo(), now_ts=LIMITE + 2 + i) is None

    def test_volta_a_worn_quando_o_ppg_volta_a_ler(self):
        d = vital_alerts.WearDetector()
        d.observe(registo(hr=70), now_ts=0.0)
        d.observe(registo(), now_ts=LIMITE + 1)

        evento = d.observe(registo(hr=72), now_ts=LIMITE + 60)
        assert evento["state"] == vital_alerts.WEAR_STATE_WORN
        assert evento["previous_state"] == vital_alerts.WEAR_STATE_REMOVED


class TestOsDoisSinaisSaoNecessarios:
    """O critério é uma CONJUNÇÃO. Cada sinal sozinho tem um falso
    positivo óbvio: um dispositivo ao pulso durante um período em que o
    PPG não conseguiu ler nada continua a mexer-se; um dispositivo pousado
    numa mesa com um dedo acidentalmente em cima do sensor não se mexe."""

    def test_movimento_sem_leitura_de_ppg_nao_e_removido(self):
        d = vital_alerts.WearDetector()
        d.observe(registo(hr=70), now_ts=0.0)
        # Sem PPG durante 40 min, mas com movimento reportado pelo firmware.
        for minuto in range(1, 41):
            evento = d.observe(registo(inactivity=False), now_ts=minuto * MINUTO)
            assert evento is None
        assert d.state == vital_alerts.WEAR_STATE_WORN

    def test_ppg_sem_movimento_nao_e_removido(self):
        d = vital_alerts.WearDetector()
        d.observe(registo(hr=70), now_ts=0.0)
        for minuto in range(1, 41):
            evento = d.observe(registo(spo2=97), now_ts=minuto * MINUTO)
            assert evento is None
        assert d.state == vital_alerts.WEAR_STATE_WORN

    def test_variacao_da_aceleracao_conta_como_movimento(self):
        """Sinal independente do flag `inactivity` do firmware: mesmo com
        inactivity=True, uma norma de aceleração que muda acima do limiar
        de ruído é movimento real."""
        d = vital_alerts.WearDetector()
        d.observe(registo(hr=70), now_ts=0.0)
        for minuto in range(1, 41):
            # az alterna entre 1.0 e 1.5 -> variação de 0.5 g, muito acima
            # do limiar de ruído (WEAR_MOTION_DELTA_G = 0.05).
            az = 1.0 if minuto % 2 else 1.5
            assert d.observe(registo(az=az), now_ts=minuto * MINUTO) is None
        assert d.state == vital_alerts.WEAR_STATE_WORN

    def test_ruido_do_imu_abaixo_do_limiar_nao_conta_como_movimento(self):
        """O contrário do teste anterior: uma oscilação de mili-g (ruído do
        próprio sensor, com o dispositivo imóvel) não pode impedir a
        deteção — senão o requisito nunca dispararia na prática."""
        d = vital_alerts.WearDetector()
        d.observe(registo(hr=70), now_ts=0.0)
        for minuto in range(1, 41):
            az = 1.0 if minuto % 2 else 1.001  # 1 mg, bem abaixo de 0.05 g
            d.observe(registo(az=az), now_ts=minuto * MINUTO)
        assert d.state == vital_alerts.WEAR_STATE_REMOVED


class TestDistincaoLigacaoVsRemocao:
    """O ponto do requisito."""

    def test_queda_de_ligacao_produz_link_lost_e_nao_removido(self):
        d = vital_alerts.WearDetector()
        d.observe(registo(hr=70), now_ts=0.0)

        evento = d.on_link_lost(now_ts=MINUTO)
        assert evento["state"] == vital_alerts.WEAR_STATE_LINK_LOST
        assert d.state != vital_alerts.WEAR_STATE_REMOVED
        # A mensagem tem de admitir que NÃO se sabe se foi retirado.
        assert "não é" in evento["explanation"] or "nao e" in evento["explanation"]

    def test_uma_hora_de_ligacao_em_baixo_nao_gera_removido_na_reconexao(self):
        """Regressão do falso positivo mais provável: sem apagar os
        contadores na queda de ligação, a primeira amostra depois de uma
        hora desligado via 60 min "sem pele e sem movimento" e declarava o
        dispositivo retirado — quando na verdade ninguém sabe o que
        aconteceu durante esse período."""
        d = vital_alerts.WearDetector()
        d.observe(registo(hr=70), now_ts=0.0)
        d.on_link_lost(now_ts=MINUTO)

        d.on_link_restored(now_ts=61 * MINUTO)
        assert d.state == vital_alerts.WEAR_STATE_UNKNOWN

        evento = d.observe(registo(), now_ts=61 * MINUTO + 1)
        assert evento["state"] == vital_alerts.WEAR_STATE_WORN
        assert d.state != vital_alerts.WEAR_STATE_REMOVED

    def test_apos_reconexao_a_contagem_recomeca_do_zero(self):
        d = vital_alerts.WearDetector()
        d.observe(registo(hr=70), now_ts=0.0)
        d.on_link_lost(now_ts=MINUTO)
        d.on_link_restored(now_ts=61 * MINUTO)
        d.observe(registo(), now_ts=61 * MINUTO)

        # 30 min DEPOIS da reconexão (não desde a última leitura de PPG).
        assert d.observe(registo(), now_ts=61 * MINUTO + LIMITE) is None
        evento = d.observe(registo(), now_ts=61 * MINUTO + LIMITE + 1)
        assert evento["state"] == vital_alerts.WEAR_STATE_REMOVED

    def test_estado_inicial_e_desconhecido_nao_em_uso(self):
        """Sem nenhuma amostra o bridge não sabe nada — prometer "em uso"
        seria uma afirmação sem dados que a sustentem."""
        assert vital_alerts.WearDetector().state == vital_alerts.WEAR_STATE_UNKNOWN

    def test_link_restored_sem_link_lost_previo_nao_faz_nada(self):
        d = vital_alerts.WearDetector()
        d.observe(registo(hr=70), now_ts=0.0)
        assert d.on_link_restored(now_ts=MINUTO) is None
        assert d.state == vital_alerts.WEAR_STATE_WORN


class TestRobustez:
    def test_registo_sem_campos_de_aceleracao_nao_rebenta(self):
        """O callback BLE não pode levantar por causa de um registo
        estranho — mesmo raciocínio de is_plausible_full_plain()."""
        d = vital_alerts.WearDetector()
        assert d.observe({"hr": None, "spo2": None}, now_ts=0.0) is not None
        assert d.observe({"hr": None, "spo2": None}, now_ts=LIMITE + 1) is not None

    def test_limite_configuravel(self):
        d = vital_alerts.WearDetector(absence_seconds=5 * MINUTO)
        d.observe(registo(hr=70), now_ts=0.0)
        assert d.observe(registo(), now_ts=5 * MINUTO + 1)["state"] == vital_alerts.WEAR_STATE_REMOVED
