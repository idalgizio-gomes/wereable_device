# CareWear — Pipeline de Machine Learning (`ml/`)

Pipeline de deteção de rotina/anomalias baseado em "Routine-Aware Behavioural
Monitoring Framework for Dementia Care Using Wearable-Derived Synthetic Daily
Routines", em três partes:

1. **Classificador de atividades** — classifica janelas de sinal do wearable
   em categorias de rotina (XGBoost no artigo original; Random Forest
   treinado aqui como alternativa embarcável).
2. **LSTM Autoencoder** — deteta anomalias comportamentais na sequência de
   atividades classificadas (erro de reconstrução alto = padrão anómalo).
3. **Detetor de duração baseado em regras** — sinaliza quando uma atividade
   dura fora dos limites esperados.

Os três passos estão implementados e avaliados sobre **dados sintéticos**.
Os passos 1 e 3 já correm ao vivo no bridge (`bridge/activity_inference.py`,
que reutiliza `features.py` e `duration_detector.py` diretamente), com o
aviso "treinado só com dados sintéticos" sempre visível no dashboard — o
modelo em si não foi revalidado com dados reais, só passou a ser invocado em
produção. Nenhum passo está embarcado no firmware.

## Estrutura da pasta e reprodução do pipeline

```
ml/
  features.py                     # extração de features estatísticas por janela
  synthetic_data.py               # gerador de dados sintéticos de rotina (passo 1)
  synthetic_sequences.py          # gerador de sequências diárias c/ anomalias injetadas (passo 2)
  train_activity_classifier.py    # treino + avaliação do XGBoost (passo 1)
  train_activity_classifier_rf.py # treino + avaliação do Random Forest (alternativa TinyML, passo 1)
  train_lstm_autoencoder.py       # treino + avaliação do LSTM Autoencoder (passo 2)
  duration_detector.py            # detetor de duração baseado em regras + avaliação (passo 3)
  measure_rf_footprint.py         # footprint real (flash/RAM) do Random Forest via emlearn
  combined_pipeline_report.py     # relatório combinado: classificador + duração + autoencoder, blocos previstos (não ground truth)
  retrain_autoencoder_from_real_data.py  # fine-tuning do autoencoder sobre dados reais do bridge
  requirements.txt
  data/
    synthetic_routine_dataset.csv        # gerado, NÃO versionado (ver .gitignore)
    synthetic_routine_dataset.meta.json  # metadados do dataset gerado, versionado
  models/
    activity_classifier_xgb.json         # modelo treinado (XGBoost, formato nativo)
    activity_classifier_rf.joblib        # modelo treinado (Random Forest)
    activity_classifier_labels.json      # classes + nomes das features, na mesma ordem do modelo
    lstm_autoencoder.keras               # modelo treinado (LSTM Autoencoder)
    lstm_autoencoder_scaler.joblib       # StandardScaler usado antes do autoencoder
    lstm_autoencoder_labels.json         # nomes das features + comprimento da subsequência
  reports/
    activity_classifier_metrics.json           # accuracy, classification report, matriz de confusão
    activity_classifier_confusion_matrix.png    # visualização da matriz de confusão
    activity_classifier_rf_metrics.json         # idem, para o Random Forest
    activity_classifier_rf_footprint.json       # footprint real (flash/RAM) via emlearn
    lstm_autoencoder_metrics.json               # AUC-ROC/recall geral e por tipo de anomalia
    lstm_autoencoder_error_distribution.png     # histograma do erro de reconstrução, normal vs. anómalo
    duration_detector_metrics.json              # recall por tipo de anomalia + falsos positivos (passo 3)
    combined_pipeline_metrics.json              # relatório combinado (oráculo vs. blocos previstos)
    combined_pipeline_recall_by_detector.png    # recall por tipo de anomalia e detetor (barras agrupadas)
```

Para reproduzir do zero:

```bash
cd ml
pip install -r requirements.txt
python synthetic_data.py              # gera data/synthetic_routine_dataset.csv
python train_activity_classifier.py   # treina e avalia o XGBoost, escreve em models/ e reports/
python train_activity_classifier_rf.py  # idem, Random Forest
python train_lstm_autoencoder.py      # gera sequências sintéticas + treina/avalia o autoencoder
python duration_detector.py           # avalia o detetor de duração baseado em regras (sem treino)
python combined_pipeline_report.py    # relatório combinado dos 3 detetores (requer os modelos já treinados acima)
```

Todos os scripts são determinísticos (seed fixa = 42; `duration_detector.py`
usa seed=123; `combined_pipeline_report.py` usa seed=555 — cohorts distintos
de propósito, para nunca reavaliar sobre dados já vistos no treino).

### Dados sintéticos (`synthetic_data.py`)

- Sinal a 52 Hz (taxa real do IMU LSM6DS3 do wearable) para acelerómetro (3
  eixos) e giroscópio (3 eixos), mais FC (PPG) a frequência mais baixa.
- **5 classes**: `Dormir`, `Descanso`, `Atividade`, `Alimentação`, `Higiene`
  — as já usadas no dashboard, em vez das 10 classes mais granulares do
  artigo original. Escolha deliberada para alimentar a UI existente sem
  remapear categorias.
- Sessões "dia"/"noite" simulam um **dia completo de 24h** (960 min / 480 min
  = 16h+8h). Parâmetros de sinal por classe são intervalos amostrados por
  janela, com sobreposição deliberada entre classes vizinhas em
  intensidade/frequência (evita accuracy artificialmente perfeita).
- Última execução: 72 402 janelas de 10s, 8 sujeitos sintéticos,
  desequilibrado entre classes (Descanso e Dormir dominam, refletindo uma
  rotina plausível — ver `data/synthetic_routine_dataset.meta.json`).
- Split de avaliação por `subject_id` (nunca por janela) em todos os
  passos — janelas do mesmo sujeito partilham "jitter" individual e
  inflacionariam a métrica se misturadas entre treino e teste.

### Features (`features.py`)

Estatísticas no domínio do tempo por janela, por eixo (média, desvio-padrão,
mín., máx., RMS), mais Signal Magnitude Area, correlação entre pares de eixos
do acelerómetro, taxa de cruzamentos por zero (proxy de periodicidade sem
FFT) e média/desvio da FC na janela. Sem features no domínio da frequência
(FFT).

## Escolha de modelo e footprint em hardware real (ARM)

**Porquê XGBoost (artigo original)**: alinhamento científico, lida bem com
features tabulares em escalas heterogéneas sem normalização, e
`max_depth=3` (artigo usa 6) já respeita a regra prática de manter
profundidade baixa para caber em flash de MCU.

**Problema identificado**: num XGBoost multiclasse o nº de árvores internas
é `n_estimators × n_classes`. Este treino usa 300 estimadores × 5 classes =
**~1500 árvores internas** (o artigo original, 400×10, dá ~4000). Um
precedente publicado mostrou 500 árvores a exigirem 553–727KB de flash —
quase todo o orçamento desta placa (811KB total, ~638KB livres). Além disso
`micromlgen` (conversor XGBoost→C) está sem manutenção ativa e tem bugs
documentados.

**Alternativa avaliada**: Random Forest com 80 árvores rasas (profundidade
5), convertido via [`emlearn`](https://github.com/emlearn/emlearn) —
mantido ativamente, já comprovado em hardware nRF52 real.

| | XGBoost (`activity_classifier_xgb.json`) | Random Forest (`activity_classifier_rf.joblib`) |
|---|---|---|
| Nº de árvores | 300 estimadores × 5 classes ≈ **1500 árvores internas** | **80 árvores** |
| Profundidade | 3 | 5 |
| Accuracy (sujeitos de teste nunca vistos) | 0.996 | 0.992 |

Diferença de accuracy pequena (0.4 pontos percentuais) para uma fração do
número de árvores — reforça o Random Forest como via mais realista para
embarcar. "Higiene" é a classe mais confundida em ambos os modelos
(precision/recall 0.89/0.94 no RF, 0.94/0.98 no XGBoost), consistente com o
overlap deliberado com "Atividade".

### Footprint real medido (`measure_rf_footprint.py`)

Random Forest convertido via `emlearn` e **compilado com o toolchain ARM
real do firmware** (`arm-none-eabi-gcc`, `-mcpu=cortex-m4 -mfpu=fpv4-sp-d16
-mfloat-abi=hard -Os`) — footprint medido, não estimado.

| Variante | Flash | RAM | Accuracy (código C real, compilado e corrido) |
|---|---|---|---|
| `inline`, quantizado (`int16_t`) | ~13,7 KB (14 012 bytes) | 0 bytes | 0.795 |
| `inline`, `float` (limiares não quantizados) | ~27,2 KB (27 836 bytes) | 0 bytes | 0.991 |
| `loadable` (tabela de dados, só `int16_t`) | ~10,3 KB (10 513 bytes) | 28 bytes | 0.795 |

Duas conclusões:

1. **Flash não é fator limitante** — mesmo a variante maior é uma fração
   ínfima dos ~638KB livres.
2. **A quantização `int16_t` por omissão do `emlearn` destrói a accuracy**
   (de 0.978 para 0.789 na medição original, ~19 pontos percentuais). Causa:
   features como a correlação entre eixos do acelerómetro (tipicamente entre
   -1 e 1) perdem quase toda a informação quando truncadas para `int16_t`
   sem escala. O caminho `loadable` está preso a este dtype no `emlearn`
   atual; `inline`+`float` evita o problema ao custo de mais flash (ainda
   assim pequeno face ao orçamento disponível).

Ver "Decisão pendente" abaixo para as duas vias possíveis de embarque.

## Resultados finais atuais

| Componente | Métrica | Valor |
|---|---|---|
| Classificador de atividades — XGBoost | Accuracy (sujeitos de teste nunca vistos) | **0.996** |
| Classificador de atividades — Random Forest | Accuracy (sujeitos de teste nunca vistos) | **0.992** |
| LSTM Autoencoder — geral | AUC-ROC | 0.849 |
| LSTM Autoencoder — geral | Recall ao limiar (percentil 95) | 0.198 |
| LSTM Autoencoder — geral | Precisão ao limiar (percentil 95) | 0.035 |
| LSTM Autoencoder — geral | PR-AUC | 0.040 |
| LSTM Autoencoder — `duracao_prolongada` | AUC-ROC / Recall | 0.799 / 0.008 |
| LSTM Autoencoder — `substituicao_contextual` | AUC-ROC / Recall | 0.928 / 0.471 |
| LSTM Autoencoder — `truncamento` | AUC-ROC / Recall | 0.861 / 0.136 |
| Detetor de duração — `duracao_prolongada` | Recall | 0.925 |
| Detetor de duração — `substituicao_contextual` | Recall | 1.000 |
| Detetor de duração — `truncamento` | Recall | 0.925 |
| Detetor de duração — falsos positivos (blocos normais, oráculo) | Taxa | 0.0% |
| Pipeline combinado — falsos positivos (blocos previstos pelo classificador) | Taxa | 70.8% |
| Pipeline combinado — falsos positivos (com LSTM Autoencoder via OR) | Taxa | 85.2% |
| Pipeline combinado — recall por tipo (blocos previstos) | Recall | 1.000 / 1.000 / 1.000 |

**Leitura do achado mais importante**: o detetor de duração tem 0.0% de
falsos positivos quando avaliado contra os segmentos verdadeiros (oráculo),
mas 70.8% quando alimentado pelos blocos que o classificador realmente
produz — o classificador fragmenta blocos contínuos em ~1.94x mais blocos
do que o real (accuracy alta ao nível da janela, 0.996, não implica
especificidade alta ao nível do bloco). Combinar com o autoencoder via OR
lógico piora ainda mais a especificidade (85.2%), porque um OR nunca reduz
falsos positivos — só aumenta a sensibilidade combinada. Os três detetores
são complementares por desenho (o autoencoder falha em anomalias de
duração, onde a regra determinística acerta quase sempre), mas encadeá-los
sem suavizar a saída do classificador (filtro de mediana/histerese, ainda
não implementado) destrói a especificidade da regra de duração em
produção.

## Limitações honestas

- **Dados 100% sintéticos** em toda a pasta — gerados por `synthetic_data.py`
  e `synthetic_sequences.py`, sem nenhum utente real a usar o dispositivo
  com rótulos de atividade (o firmware não tem classificador HAR embarcado).
  Suficiente para validar a pipeline de ponta a ponta (dados → features →
  modelo → avaliação), **não** valida desempenho em dados clínicos reais.
- Ruído do acelerómetro/giroscópio é estimado, nunca medido no hardware real
  (bloqueado pela indisponibilidade de placa).
- A sobreposição entre classes e os limites de duração usados pelo detetor
  do passo 3 são escolhas de design próprias (vindas do próprio gerador
  sintético), não calibrações independentes nem dados reais.
- Anomalias injetadas nas sequências são plausíveis, não clinicamente
  validadas.
- Nenhum componente está embarcado ou medido em latência de hardware real
  (só footprint estático de flash/RAM do Random Forest foi medido).
- **Alternativa de validação externa**: o [TIHM Dataset](https://www.nature.com/articles/s41597-023-02519-y)
  (dados reais multi-sensor de demência, com eventos adversos rotulados,
  [código/dados aqui](https://github.com/PBarnaghi/TIHM-Dataset)) é a via
  identificada para validar o pipeline contra dados clínicos reais sem
  depender de recrutar utentes próprios.

## Decisão pendente

1. **Dados reais**: treinar/validar com dados reais de utentes exige
   consentimento e dados reais que só o proprietário do projeto pode
   disponibilizar — não decidido nem assumido nesta pasta.
2. **XGBoost vs. Random Forest para embarque**: mudança metodológica face ao
   artigo original, não só uma escolha de implementação — precisa de
   validação humana. O footprint do Random Forest já foi medido (flash não é
   fator limitante), mas o do XGBoost via `micromlgen` continua por medir
   (ferramenta sem manutenção ativa, reduzindo a urgência). Mesmo optando
   por Random Forest, falta decidir entre `inline`+`float` (accuracy
   intacta, ~19KB, menor risco) e adaptar as features para escala inteira e
   usar `loadable` (mais compacto e rápido, mas exige retreinar).

## Cobertura de testes automáticos (`ml/tests/`, CI: `.github/workflows/ml-tests.yml`)

**24/24 testes a passar** no total, cobrindo:

- `test_features.py`, `test_duration_detector.py` — lógica determinística e
  pura de `features.py` (`_zero_crossing_rate`, `extract_features`) e
  `duration_detector.py` (`evaluate_block`, `evaluate_subject`), com
  sinais/segmentos escritos à mão. Sem gerar dataset nem treinar modelo.
- `test_train_smoke.py` — chama `train_activity_classifier.train()` e
  `train_activity_classifier_rf.train()` sobre um dataset minúsculo gerado em
  memória, confirmando que o caminho dados → split por sujeito → treino →
  métricas corre sem erro. Inclui 2 testes de regressão para um bug real
  encontrado e corrigido em `split_by_subject()` (um split degenerado podia
  deixar uma classe inteiramente fora do treino, rebentando o `.fit()` do
  XGBoost). **Não valida métricas de produção.**
- `test_combined_pipeline_report.py` — só `predicted_blocks_from_rows()`, a
  função pura de agrupamento de janelas em blocos, com linhas escritas à
  mão. Inclui um teste que reproduz o mecanismo do achado de fragmentação.
- `test_lstm_autoencoder_smoke.py`, `test_retrain_autoencoder_from_real_data.py`
  — presentes na suite (ver ficheiros para o que cobrem exatamente).

**O que não está coberto**: `train_lstm_autoencoder.py` (treino completo) e
`measure_rf_footprint.py` não têm teste automático de execução completa —
exigiriam TensorFlow/emlearn instalados e minutos de execução em CI, o
mesmo custo que motivou deixar `ml-tests.yml` a instalar só
`numpy`/`pandas`/`scikit-learn`/`xgboost`/`pytest` (não
`ml/requirements.txt` completo). `run_evaluation()` de
`duration_detector.py` e `combined_pipeline_report.py` (que carregam
modelos treinados/TensorFlow) também não estão cobertos por teste
automático — validados manualmente, com o resultado registado nos
relatórios em `reports/`.
