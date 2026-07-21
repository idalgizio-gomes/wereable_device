"""Teste de fumo do LSTM Autoencoder (passo 2 do pipeline, ver ml/README.md
e `train_lstm_autoencoder.py`) — confirma que a arquitetura constrói e que
`model.fit()` corre sem exceção, produzindo uma loss finita. Não valida
métricas de deteção (precisão/recall/AUC-ROC) — isso é feito manualmente e
já documentado em `reports/lstm_autoencoder_metrics.json`. Mesmo espírito
de `test_train_smoke.py` (passo 1), agora estendido ao script que precisa
de TensorFlow — motivo pelo qual ambos ficaram fora do CI leve até agora
(ver comentário atualizado em `.github/workflows/ml-tests.yml`, que passou
a instalar `tensorflow-cpu` só por causa deste ficheiro).

DATASET SINTÉTICO ENCOLHIDO AO MÍNIMO: por omissão, `synthetic_sequences.py`
gera por sujeito um ciclo diário completo (`DAY_SESSION_MINUTES=960` +
`NIGHT_SESSION_MINUTES=480` = 8640 janelas de 10s) — pesado demais para um
smoke test. Este ficheiro faz monkeypatch dessas duas constantes no módulo
`synthetic_sequences` (que as importou por VALOR de `synthetic_data.py` com
`from synthetic_data import DAY_SESSION_MINUTES, ...` — por isso o alvo do
monkeypatch tem de ser `synthetic_sequences.DAY_SESSION_MINUTES`, não
`synthetic_data.DAY_SESSION_MINUTES`, ao contrário do padrão usado em
`test_train_smoke.py` para os scripts do passo 1, que importam
`synthetic_data` como módulo). `_build_segment_sequence()` gera sempre PELO
MENOS um segmento por sessão (o `while elapsed < total_minutes` só para
depois de o primeiro bloco elevar `elapsed`), por isso mesmo um alvo de poucos
minutos ainda produz dezenas a centenas de janelas reais por sujeito —
mais do que suficiente para exceder `SEQ_LEN` (12 janelas = 2 min de
contexto) e treinar 1 epoch em segundos, sem pretender reproduzir a
distribuição temporal de um dia real.

NÃO cobre `retrain_autoencoder_from_real_data.py`: esse script lê dados
REAIS acumulados pelo bridge (`bridge/storage_advanced.py`, BD SQLite do
bridge, ver a sua própria docstring "EXECUÇÃO") — não tem uma função pura
equivalente a `train()`/`build_model()` que opere sobre dados sintéticos em
memória sem tocar numa base de dados; simular esse caminho aqui testaria uma
BD fake em vez do uso real do script (correr manualmente/agendado
localmente, "nunca em GitHub Actions" segundo a própria docstring). Reutiliza
porém `build_subsequences()` e `build_autoencoder()` de
`train_lstm_autoencoder.py`, já exercitadas pelos testes abaixo.
"""
import numpy as np
import pytest

import synthetic_sequences as seq
from train_lstm_autoencoder import SEQ_LEN, build_autoencoder, build_subsequences


def _tiny_feature_matrices(monkeypatch, seed, n_subjects=2):
    """Gera `n_subjects` matrizes [n_windows, n_features] sintéticas, todas
    normais (sem anomalia injetada — o mesmo grupo usado para treino em
    `train_lstm_autoencoder.py::train_group`), com sessões dia/noite
    encolhidas para o smoke test correr depressa (ver docstring do módulo)."""
    monkeypatch.setattr(seq, "DAY_SESSION_MINUTES", 5)
    monkeypatch.setattr(seq, "NIGHT_SESSION_MINUTES", 5)

    rng = np.random.default_rng(seed)
    feature_names = seq.get_feature_names()
    matrices = []
    for _ in range(n_subjects):
        matrix, _mask, actual_type = seq.generate_subject_sequence(rng, feature_names)
        assert actual_type is None  # sem anomalia injetada neste grupo
        matrices.append(matrix)
    return feature_names, matrices


def test_build_autoencoder_returns_a_compiled_keras_model_with_expected_shapes():
    n_features = 5
    model = build_autoencoder(SEQ_LEN, n_features)

    assert model.input_shape == (None, SEQ_LEN, n_features)
    assert model.output_shape == (None, SEQ_LEN, n_features)
    assert model.loss == "mse"


def test_lstm_autoencoder_fits_one_epoch_on_tiny_synthetic_data_without_crashing(monkeypatch):
    feature_names, matrices = _tiny_feature_matrices(monkeypatch, seed=7, n_subjects=2)
    n_features = len(feature_names)

    subsequences = []
    for matrix in matrices:
        dummy_mask = np.zeros(matrix.shape[0], dtype=bool)  # grupo de treino: tudo normal
        x, _y = build_subsequences(matrix, dummy_mask)
        subsequences.append(x)
    X = np.concatenate(subsequences)
    # confirma que o dataset encolhido (ver docstring do módulo) ainda
    # produz pelo menos uma subsequência de SEQ_LEN janelas — se isto falhar
    # no futuro (ex.: SEQ_LEN aumentado), é o dataset de teste que precisa
    # de crescer, não um bug do autoencoder.
    assert X.shape[0] > 0
    assert X.shape[1:] == (SEQ_LEN, n_features)

    model = build_autoencoder(SEQ_LEN, n_features)
    history = model.fit(
        X, X,
        epochs=1,
        batch_size=8,
        shuffle=True,
        verbose=0,
    )

    loss = history.history["loss"][-1]
    assert np.isfinite(loss)


def test_reconstruction_error_is_finite_and_non_negative_after_one_epoch(monkeypatch):
    # Complemento ao teste acima: cobre reconstruction_error(), usada tanto
    # na calibração do limiar de deteção como na avaliação final (ver
    # train_lstm_autoencoder.py) — confirma que o caminho model.predict() +
    # erro quadrático médio também corre sem exceção sobre dados sintéticos
    # minúsculos.
    from train_lstm_autoencoder import reconstruction_error

    feature_names, matrices = _tiny_feature_matrices(monkeypatch, seed=11, n_subjects=1)
    n_features = len(feature_names)
    matrix = matrices[0]
    dummy_mask = np.zeros(matrix.shape[0], dtype=bool)
    X, _y = build_subsequences(matrix, dummy_mask)
    assert X.shape[0] > 0

    model = build_autoencoder(SEQ_LEN, n_features)
    model.fit(X, X, epochs=1, batch_size=8, shuffle=True, verbose=0)

    errors = reconstruction_error(model, X)
    assert errors.shape == (X.shape[0],)
    assert np.all(np.isfinite(errors))
    assert np.all(errors >= 0.0)
