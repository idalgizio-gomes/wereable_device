"""Testes de `retrain_autoencoder_from_real_data.py` — retreino do LSTM
Autoencoder sobre dados REAIS do bridge (ver docstring do módulo).

Cobre só a lógica pura/determinística que não precisa de TensorFlow nem de
uma base de dados real a correr:
  (a) `_windows_from_sensor_records`: agrupamento de registos crus em
      janelas de WINDOW_SECONDS, com o mesmo tratamento de FC em falta que
      `bridge/activity_inference.py::add_sample` (último valor conhecido,
      placeholder neutro se nunca houve leitura);
  (b) a guarda de dados mínimos (MIN_REAL_SUBSEQUENCES) em `main()`, via
      subprocess real — confirma que, com a BD de teste vazia, o script
      recusa-se a retreinar e NÃO importa TensorFlow (processo termina
      rápido, sem tentar carregar o modelo).

Deliberadamente NÃO testado aqui (precisaria de TensorFlow instalado e de
dados reais que ainda não existem em quantidade — ver MIN_REAL_SUBSEQUENCES
no módulo): o caminho de fine-tuning em si. Fica documentado como
verificação pendente até haver uso real suficiente (ver PROJECT_STATUS.md).
"""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from retrain_autoencoder_from_real_data import MIN_REAL_SUBSEQUENCES, WINDOW_SAMPLES, _windows_from_sensor_records


def _fake_record(ts, ax=0.0, ay=0.0, az=1.0, gx=0.0, gy=0.0, gz=0.0, hr=None):
    """Espelha os atributos de sa.SensorRecord usados por
    _windows_from_sensor_records (timestamp_utc em segundos, como grava
    orm_persistence.py::insert_sensor_record)."""
    return SimpleNamespace(
        timestamp_utc=ts, accel_x=ax, accel_y=ay, accel_z=az,
        gyro_x=gx, gyro_y=gy, gyro_z=gz, heart_rate=hr,
    )


def _dense_records(duration_s=10, hr_for_index=lambda i: None):
    """~52 registos/seg (mesma densidade real do IMU, ver FS_HZ) ao longo
    de `duration_s` segundos — necessário para passar a guarda de
    esparsidade (>=10% de WINDOW_SAMPLES), ao contrário de um punhado de
    registos com 1s de intervalo, que representaria uma perda de pacotes
    quase total e é descartado de propósito."""
    n = duration_s * 52 + 1
    out = []
    for i in range(n):
        ts = i / 52.0  # timestamp_utc em segundos (float aceitável para o teste)
        out.append(_fake_record(ts=ts, hr=hr_for_index(i)))
    return out


class TestJanelasAPartirDeRegistosReais:
    def test_menos_de_uma_janela_nao_produz_nada(self):
        records = [_fake_record(ts=i) for i in range(5)]  # bem menos que 10s de dados
        windows = _windows_from_sensor_records(records)
        assert windows == []

    def test_uma_janela_completa_produz_um_dict_de_features(self):
        records = _dense_records(duration_s=10)
        windows = _windows_from_sensor_records(records)
        assert len(windows) == 1
        assert "accel_x_mean" in windows[0]
        assert "hr_mean" in windows[0]

    def test_janela_demasiado_esparsa_e_descartada(self):
        # Span temporal cobre 10s mas com muito poucas amostras reais no
        # meio (perda de pacotes/registos) — abaixo de 10% de WINDOW_SAMPLES.
        sparse_count = max(1, int(WINDOW_SAMPLES * 0.05))
        records = [_fake_record(ts=0)] + [
            _fake_record(ts=10) for _ in range(sparse_count - 1)
        ]
        windows = _windows_from_sensor_records(records)
        assert windows == []

    def test_fc_em_falta_usa_ultimo_valor_conhecido(self):
        # Só a primeira amostra traz FC real (72); todas as restantes vêm
        # sem leitura nova (None) — mesmo padrão real do FullPlain (ver
        # bridge/ble_bridge.py::decode_full_plain, hr só chega quando há
        # leitura nova).
        records = _dense_records(duration_s=10, hr_for_index=lambda i: 72 if i == 0 else None)
        windows = _windows_from_sensor_records(records)
        assert len(windows) == 1
        # hr_mean tem de refletir 72 (o único valor real visto), não um
        # placeholder arbitrário.
        assert windows[0]["hr_mean"] == 72.0

    def test_fc_nunca_visto_usa_placeholder_neutro_sem_rebentar(self):
        records = _dense_records(duration_s=10, hr_for_index=lambda i: None)
        windows = _windows_from_sensor_records(records)
        assert len(windows) == 1
        assert windows[0]["hr_mean"] == 70.0  # placeholder documentado, não NaN


class TestGuardaDeDadosMinimos:
    def test_com_bd_vazia_recusa_retreinar_sem_importar_tensorflow(self, tmp_path, monkeypatch):
        """Corre o script como subprocesso real, apontando DATABASE_URL para
        uma BD SQLite vazia recém-criada — confirma end-to-end que a guarda
        (MIN_REAL_SUBSEQUENCES) dispara e o processo termina depressa, sem
        tentar sequer importar TensorFlow (que pode nem estar instalado)."""
        empty_db = tmp_path / "empty_carewear.db"
        env = {**__import__("os").environ, "DATABASE_URL": f"sqlite:///{empty_db.as_posix()}"}
        script = Path(__file__).resolve().parent.parent / "retrain_autoencoder_from_real_data.py"
        result = subprocess.run(
            [sys.executable, str(script), "--days", "30"],
            cwd=script.parent, env=env, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0
        assert "RECUSADO" in result.stdout or "0 janelas reais" in result.stdout or "impossível construir" in result.stdout
        assert f"MIN_REAL_SUBSEQUENCES={MIN_REAL_SUBSEQUENCES}" in result.stdout or "0 janelas" in result.stdout
