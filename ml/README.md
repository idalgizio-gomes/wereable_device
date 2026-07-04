# CareWear — Pipeline de Machine Learning (`ml/`)

> Progresso incremental, dia a dia, do pipeline de deteção de rotina/anomalias
> descrito no artigo científico de referência. Duas rotinas cloud diárias
> tocam nesta pasta (ver PROJECT_STATUS.md → "Rotinas cloud agendadas") — este
> README é o ponto de sincronização entre elas, atualizado a cada avanço.

## Referência científica

"Routine-Aware Behavioural Monitoring Framework for Dementia Care Using
Wearable-Derived Synthetic Daily Routines" — pipeline em três partes:

1. **Classificador de atividades** (XGBoost no artigo original) — classifica
   janelas de sinal do wearable em categorias de rotina.
2. **LSTM Autoencoder** — deteta anomalias comportamentais na sequência de
   atividades classificadas (erro de reconstrução alto = padrão anómalo).
3. **Detetor de duração baseado em regras** — sinaliza quando uma atividade
   dura fora dos limites esperados (já trivialmente embarcável — ver
   `Ble.cpp`/`kDumpCtrlResetReadings` e a tabela de limites no dashboard,
   vista "Limites de duração").

**Estado atual: os três passos estão implementados e avaliados sobre dados
sintéticos** (código nesta pasta) — classificador (passo 1), LSTM
Autoencoder (passo 2) e detetor de duração baseado em regras (passo 3).
Nenhum está ainda embarcado no firmware nem validado com dados reais — ver
"Próximos passos" abaixo.

## Porque não há dados reais ainda

O firmware do wearable não tem classificador HAR embarcado (ver
PROJECT_STATUS.md, "Riscos"), e não há nenhum utente real a usar o
dispositivo com rótulos de atividade. Por isso, todo o desenvolvimento desta
pasta usa **dados 100% sintéticos**, gerados por `synthetic_data.py` — a
mesma estratégia do artigo de referência (que também usa rotinas diárias
sintéticas geradas a partir de segmentos reais de 12 participantes, 10
classes de atividade, template de 21 passos). Isto é suficiente para validar
a pipeline de ponta a ponta (dados → features → modelo → avaliação), mas
**não** valida desempenho em dados clínicos reais — essa validação só pode
acontecer com dados reais (ver "Decisão pendente" abaixo) ou com o
[TIHM Dataset](https://www.nature.com/articles/s41597-023-02519-y) (dados
reais multi-sensor de demência, com eventos adversos rotulados,
[código/dados aqui](https://github.com/PBarnaghi/TIHM-Dataset)).

## Estrutura

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
    activity_classifier_metrics.json          # accuracy, classification report, matriz de confusão
    activity_classifier_confusion_matrix.png   # visualização da matriz de confusão
    activity_classifier_rf_metrics.json        # idem, para o Random Forest
    activity_classifier_rf_footprint.json      # footprint real (flash/RAM) via emlearn
    lstm_autoencoder_metrics.json              # AUC-ROC/recall geral e por tipo de anomalia
    lstm_autoencoder_error_distribution.png    # histograma do erro de reconstrução, normal vs. anómalo
    duration_detector_metrics.json             # recall por tipo de anomalia + falsos positivos (passo 3)
```

Para reproduzir:

```bash
cd ml
pip install -r requirements.txt
python synthetic_data.py              # gera data/synthetic_routine_dataset.csv
python train_activity_classifier.py   # treina e avalia o XGBoost, escreve em models/ e reports/
python train_activity_classifier_rf.py  # idem, Random Forest
python train_lstm_autoencoder.py      # gera sequências sintéticas + treina/avalia o autoencoder
python duration_detector.py           # avalia o detetor de duração baseado em regras (sem treino)
```

Todos os scripts são determinísticos (seed fixa = 42).

## Passo 1 — Classificador de atividades (XGBoost)

### Dados sintéticos (`synthetic_data.py`)

- Sinal gerado a 52 Hz (mesma taxa real do IMU LSM6DS3 do wearable, ver
  PROJECT_STATUS.md) para acelerómetro (3 eixos) e giroscópio (3 eixos), mais
  FC (PPG) amostrada a frequência mais baixa dentro da mesma janela.
- **5 classes** — `Dormir`, `Descanso`, `Atividade`, `Alimentação`, `Higiene`
  — exatamente as já usadas no dashboard (`web/dashboard/index.html`, chips
  "Análise por atividade"), em vez das 10 classes mais granulares do artigo
  original (Sentado, Deitado, A andar, Movimento ligeiro, A comer com
  talheres/à mão, Duche, Higiene oral, De pé, Lavar as mãos). Escolha
  deliberada: um classificador treinado com estas 5 classes pode alimentar
  diretamente a UI já existente sem remapear categorias; o esquema mais
  granular do artigo fica registado aqui como possível evolução futura, se a
  perda de detalhe (ex.: agrupar "Duche"+"Higiene oral" em "Higiene") se
  revelar insuficiente para deteção de anomalias mais fina.
- Cada "sujeito sintético" tem uma pequena variação individual (amplitude de
  movimento, baseline de FC) — testbed simples para a ideia de "modelos
  personalizados por pessoa" do backlog de investigação (PROJECT_STATUS.md),
  ainda não explorada a fundo.
- **Limitação assumida**: os parâmetros de sinal por classe (amplitude,
  frequência, ruído) são a nossa própria modelação plausível, não os
  parâmetros exatos do artigo (não publicados) nem dados reais. As sessões
  "dia"/"noite" são comprimidas (240 min / 90 min), não dias de 24h
  completos — mantém o dataset pequeno e rápido de gerar nesta primeira
  iteração. Gerar dias completos fica registado como melhoria futura.
- Dataset da última execução: 15 840 janelas de 10s, 8 sujeitos sintéticos,
  desequilibrado entre classes (Descanso e Dormir dominam, refletindo uma
  rotina plausível — ver `data/synthetic_routine_dataset.meta.json`).

### Features (`features.py`)

Estatísticas no domínio do tempo por janela, por eixo (média, desvio-padrão,
mín., máx., RMS), mais Signal Magnitude Area, correlação entre pares de eixos
do acelerómetro, taxa de cruzamentos por zero (proxy de periodicidade sem
FFT) e média/desvio da FC na janela — abordagem clássica de HAR sobre
acelerómetro wearable, compatível com o formato tabular que o XGBoost espera.
Não há features no domínio da frequência (FFT) nesta iteração.

### Porquê XGBoost e não outro algoritmo

1. É o algoritmo usado no artigo de referência para este passo — mantém-nos
   alinhados com a base científica do projeto.
2. Lida bem com features tabulares em escalas heterogéneas sem normalização
   cuidadosa (ao contrário de redes neuronais).
3. `max_depth=3` foi escolhido deliberadamente (o artigo usa profundidade 6)
   para já respeitar a regra prática documentada no PROJECT_STATUS.md
   ("profundidade ≤3, ≤~4000 árvores, para caber em flash de MCU") — ver
   também a ressalva de footprint abaixo, que se aplica tanto a este
   classificador como a qualquer alternativa.

### Ressalva importante: viabilidade de embarcar este XGBoost em MCU (ainda por medir)

Investigação anterior desta rotina (ver histórico de `PROJECT_STATUS.md`)
levantou um ponto que se mantém válido e não foi resolvido por este treino:

- Num classificador XGBoost multiclasse, o número de árvores internas é
  aproximadamente `n_estimators × n_classes` (uma árvore por classe, por
  ronda de boosting) — com o artigo original (400 estimadores × 10 classes)
  isso são **~4000 árvores**. Este treino usa `n_estimators=300` e só 5
  classes (`300 × 5 = 1500` árvores internas), com profundidade 3 em vez de
  6 — bem menor, mas ainda não medido em hardware.
- Um precedente publicado mostrou 500 árvores (profundidade não especificada
  aqui) a exigirem 553–727KB de flash só para caberem — praticamente todo o
  orçamento desta placa (811KB total, ~638KB livres com o resto do firmware
  já a correr).
- `micromlgen` (a ferramenta óbvia para converter XGBoost→C) está sem
  manutenção ativa (repositório arquivado) e tem bugs documentados por
  resolver — um risco real de integração, independente do footprint.
- **Alternativa de recurso identificada, ainda não implementada**: se o
  footprint medido não couber ou `micromlgen` se revelar inviável na
  prática, substituir por um **Random Forest com ~50-100 árvores rasas
  (profundidade ≤4-5)**, convertido via
  [`emlearn`](https://github.com/emlearn/emlearn) — mantido ativamente,
  já comprovado em hardware nRF52 real segundo a investigação feita. Isto
  seria uma mudança metodológica face ao artigo original, não só uma escolha
  de implementação — precisa de validação humana antes de se avançar (ver
  "Decisão pendente" abaixo).

**Nada disto foi medido em hardware real ainda** — nem o footprint do
XGBoost treinado aqui, nem uma alternativa Random Forest. Este treino
serve para validar a pipeline de dados→features→modelo→avaliação no
backend; a decisão de embarcar (e com que algoritmo) só deve ser tomada
depois de medir footprint/latência reais nesta placa (ver "Estudo de
viabilidade TinyML" no PROJECT_STATUS.md).

### Random Forest treinado (2026-07-03) — comparação real com o XGBoost

Visto que o XGBoost fiel ao artigo não é viável para embarcar (secção
acima), o utilizador confirmou avançar com o treino comparativo do Random
Forest sem esperar mais, já que treinar um ou outro não faz diferença
para o estado atual da placa (nenhum dos dois está embarcado). Script:
`train_activity_classifier_rf.py`, reutiliza o mesmo dataset/split/
metodologia de avaliação do script XGBoost (ver `train_activity_classifier.py`),
para a comparação ser justa.

| | XGBoost (`ml/models/activity_classifier_xgb.json`) | Random Forest (`ml/models/activity_classifier_rf.joblib`) |
|---|---|---|
| Nº de árvores | 300 estimadores × 5 classes = **~1500 árvores internas** | **80 árvores** |
| Profundidade | 3 | 5 |
| Accuracy (sujeitos de teste nunca vistos) | **1.000** | **0.978** |

A diferença de accuracy é pequena (2.2 pontos percentuais) para uma
fração do número de árvores (80 vs. ~1500) — reforça que o Random Forest
é a via mais realista para uma eventual versão embarcada via `emlearn`,
sem sacrificar muita qualidade face ao XGBoost. **Isto continua a não ser
uma decisão de produção**: falta medir o footprint real via `emlearn`
(flash/RAM/latência nesta placa) antes de decidir embarcar qualquer um
dos dois — ver `reports/activity_classifier_rf_metrics.json` e
`reports/activity_classifier_rf_confusion_matrix.png` para o detalhe por
classe (nota: "Higiene" tem recall mais baixo, 0.74, no Random Forest do
que no XGBoost — sinal de que árvores mais rasas/em menor número têm mais
dificuldade nesta classe especificamente, algo a ter em conta se se
avançar para esta via).

### Footprint real medido via `emlearn` (2026-07-03, `measure_rf_footprint.py`)

Primeira medição real (não estimativa) do footprint deste Random Forest,
convertido para C via `emlearn` e **compilado com o toolchain ARM real do
firmware** (`arm-none-eabi-gcc`, `-mcpu=cortex-m4 -mfpu=fpv4-sp-d16
-mfloat-abi=hard -Os`) — não retreinou nada, só mediu o modelo já treinado
acima. Ver `reports/activity_classifier_rf_footprint.json` para os números
completos.

| Variante | Flash | RAM | Accuracy (código C real, compilado e corrido) |
|---|---|---|---|
| `inline`, quantizado (`int16_t`, omissão do `emlearn`) | **10 540 bytes** (~10,3 KB) | 0 bytes | **0.789** |
| `inline`, `float` (limiares não quantizados) | **19 136 bytes** (~18,7 KB) | 0 bytes | **0.978** |
| `loadable` (tabela de dados, só suporta `int16_t`) | **4 841 bytes** (~4,7 KB) | 28 bytes | **0.789** |

**Duas conclusões, uma boa e uma que exige atenção antes de qualquer
decisão de embarcar:**

1. **Flash: não é problema nenhum.** Mesmo a variante maior (`inline`
   float, ~19KB) é uma fração ínfima dos ~638KB livres nesta placa — ao
   contrário do precedente de terceiros (500 árvores a exigirem
   553-727KB) que motivou a preocupação original com o XGBoost. Para este
   modelo (80 árvores, profundidade 5), o footprint de flash deixa de ser
   um fator de decisão.
2. **A quantização `int16_t` por omissão do `emlearn` destrói a accuracy
   — de 0.978 para 0.789, uma queda de ~19 pontos percentuais.** Isto não
   estava documentado antes de se medir; a suposição implícita nas notas
   anteriores era que "quantizar" é sempre um trade-off pequeno de
   precisão por espaço, o que não se confirma aqui. **Causa identificada**:
   várias das features estatísticas usadas (`features.py`) — em
   particular a correlação entre eixos do acelerómetro, que varia
   tipicamente entre -1 e 1 — ficam com quase toda a sua informação
   destruída quando truncadas para `int16_t` sem escala (um valor 0.87 e
   um valor 0.12 tornam-se ambos `0`). O caminho `loadable` (o único que
   permitiria uma tabela de dados bem compacta, 4,7KB) está **preso** a
   este mesmo dtype no `emlearn` atual — não há como usar `loadable` com
   `float`. A variante `inline` com `dtype='float'` evita o problema
   (mantém os limiares em vírgula flutuante) e recupera a accuracy
   original exatamente (0.978), ao custo de ~19KB em vez de ~5-10KB —
   ainda assim uma fração pequena do orçamento de flash.

**Decisão técnica ainda pendente** (não tomada aqui, só medida): se se
avançar para embarcar este classificador, há duas vias honestas: (a) usar
`inline`+`float` (mais simples, accuracy intacta, ~19KB — como o flash não
é fator limitante aqui, esta é a opção que menos risco introduz); ou (b)
adaptar `features.py` para produzir features já escaladas para inteiro
antes da conversão (ex.: multiplicar a correlação por 10000 antes de
alimentar o `emlearn`), o que permitiria usar `loadable` (mais compacto e
mais rápido) sem perder accuracy — mas isso exigiria retreinar com as
features escaladas e não foi feito nesta sessão (fora do âmbito "medir,
não retreinar" desta execução).

### Avaliação — split por sujeito, não por janela

O conjunto de teste é composto por sujeitos sintéticos **nunca vistos no
treino** (split por `subject_id`, não uma amostragem aleatória de janelas).
Janelas do mesmo sujeito partilham o mesmo "jitter" individual e estariam
fortemente correlacionadas — uma amostragem aleatória de janelas inflacionaria
artificialmente a métrica de avaliação.

### Resultado da última execução

**Accuracy = 1.000** no conjunto de teste (2 sujeitos sintéticos nunca vistos
no treino, 3 960 janelas). Ver `reports/activity_classifier_metrics.json` e
`reports/activity_classifier_confusion_matrix.png` para o detalhe por classe.

**Interpretação honesta deste resultado — não é uma validação clínica:**
100% de accuracy é o resultado esperado, não uma surpresa nem uma prova de
qualidade do modelo. As classes sintéticas foram desenhadas com sinais
claramente separáveis (amplitudes/frequências bem distintas por classe, ruído
moderado) para poderem validar a pipeline de ponta a ponta rapidamente. Dados
reais serão muito mais ambíguos entre classes (ex.: "Descanso" vs. "Higiene"
sentada, ou "Alimentação" vs. outro gesto repetitivo de mão) e um modelo
treinado só nestes dados sintéticos **não deve ser considerado validado para
uso real** — falta ainda: (a) dados reais rotulados ou o TIHM Dataset como
validação externa, (b) tornar os dados sintéticos mais realistas (overlap
entre classes, artefactos de movimento, sensor noise real medido em
hardware), (c) validar em hardware embarcado se a via TinyML avançar.

## Passo 2 — LSTM Autoencoder (deteção de anomalias comportamentais)

**Implementado (2026-07-03)**: `synthetic_sequences.py` (geração de
sequências diárias sintéticas, com anomalias injetadas) +
`train_lstm_autoencoder.py` (treino, calibração de limiar, avaliação).

### Ideia e porquê esta arquitetura

Diferente do passo 1 (classifica UMA janela isolada), este passo olha para
uma **subsequência de janelas consecutivas** (`SEQ_LEN=12` janelas de 10s =
2 minutos de contexto) e tenta reconstruí-la. Treinado **só com sequências
normais** (nunca vê uma anomalia durante o treino — autoencoder, não
classificador supervisionado), o modelo aprende o padrão normal de
transições/rotina; uma subsequência com erro de reconstrução muito acima do
normal é sinalizada como possível anomalia. LSTM Autoencoder é a escolha do
artigo científico de referência para este passo — mantemo-nos alinhados com
a base científica do projeto. Arquitetura pequena deliberadamente
(`LSTM(32)`) — este treino corre no backend/offline; embarcar isto no
firmware exigiria TensorFlow Lite Micro/CMSIS-NN e quantização, footprint
ainda por medir (não feito nesta sessão — ver "Próximos passos").

### Dados: sequências sintéticas com anomalias injetadas (`synthetic_sequences.py`)

Reutiliza as mesmas funções de geração de sinal/features de
`synthetic_data.py` (mesmos parâmetros por classe, mesmo jitter por
sujeito), mas gera uma **sequência ordenada no tempo** por sujeito (noite
seguida de dia) em vez de janelas em qualquer ordem — o autoencoder precisa
da ordem temporal para aprender transições. Três tipos de anomalia
injetada, escolhidos para cobrir categorias distintas de desvio de rotina
(mesma ideia já usada na simulação visual do dashboard,
`buildRoutine(seed, anomalous)`, agora aplicada ao sinal real em vez de só
à timeline):

- `duracao_prolongada`: um bloco de Higiene fica 3-5x mais longo (ex.:
  duche demasiado longo).
- `substituicao_contextual`: um bloco da sessão "noite" (que devia ser
  Dormir/Descanso) é substituído por Atividade (agitação/deambulação
  noturna, "sundowning").
- `truncamento`: um bloco de Alimentação é cortado a menos de metade da
  duração (refeição interrompida).

Continua 100% sintético — mesma limitação já documentada para o passo 1.

### Metodologia de avaliação (4 grupos de sujeitos, sem sobreposição)

Split por sujeito sintético (nunca por janela/subsequência aleatória, mesma
lógica dos passos 1): `train` (10 sujeitos normais, ajustam os pesos) →
`val` (3 sujeitos normais, só early stopping) → `threshold` (8 sujeitos
normais, calibram o limiar de deteção como o percentil 95 do erro de
reconstrução — **subiu de 3 para 8 sujeitos** depois de uma primeira
execução mostrar sensibilidade alta a esta amostra pequena, um percentil é
uma estimativa ruidosa com poucos pontos) → avaliação final com 3 sujeitos
normais + 3 sujeitos por cada um dos 3 tipos de anomalia, **nenhum deles
visto em nenhum passo anterior**.

### Resultado da última execução — achado honesto, não só um número

Ver `reports/lstm_autoencoder_metrics.json` e
`reports/lstm_autoencoder_error_distribution.png`.

| | Geral | `duracao_prolongada` | `substituicao_contextual` | `truncamento` |
|---|---|---|---|---|
| AUC-ROC (score vs. normal) | **0.876** | 0.813 | 0.910 | 0.744 |
| Recall ao limiar (percentil 95) | 0.179 | 0.015 | 0.331 | 0.000 |

**O AUC-ROC por tipo (0.74-0.91, todos bem acima de 0.5) mostra que o
modelo consegue, de facto, ordenar corretamente subsequências anómalas
acima de normais nos 3 tipos** — não é um modelo que não aprendeu nada.
Mas o **recall a um limiar único e fixo é muito mau para os dois tipos de
anomalia baseados em duração** (`duracao_prolongada`, `truncamento`), e só
razoável para a anomalia contextual (`substituicao_contextual`). O
histograma de erro (`reports/lstm_autoencoder_error_distribution.png`)
explica porquê: prolongar ou encurtar um bloco de uma atividade já
conhecida produz mais/menos do **mesmo sinal estatístico** — as
subsequências dentro do bloco continuam a "parecer" Higiene ou Alimentação
normais, só a DURAÇÃO TOTAL do bloco é que é anómala, algo que uma janela
de 2 minutos não consegue ver sozinha. Já a substituição contextual
(Atividade a meio da noite) produz um sinal que o modelo nunca viu nesse
contexto durante o treino (a sessão "noite" é quase só Dormir/Descanso) —
por isso é o tipo mais claramente detetado.

**Isto não é um problema a "corrigir" no LSTM Autoencoder — é exatamente a
razão pela qual o artigo de referência desenha um pipeline de 3 partes em
vez de confiar tudo a um único modelo**: o classificador (passo 1) diz QUAL
atividade está a decorrer, o autoencoder (este passo) deteta padrões
CONTEXTUALMENTE atípicos, e o **detetor de duração baseado em regras**
(passo 3, ainda por implementar) é especificamente para o que o autoencoder
não vê — durações fora dos limites esperados. Os três são complementares,
não redundantes; este resultado é evidência concreta disso, não só teoria.

### Limitações honestas

- Um único limiar global (percentil 95 de um conjunto pequeno de sujeitos)
  serve mal tipos de anomalia com magnitudes de desvio muito diferentes —
  limiares por contexto/pessoa (ligado ao item 3 do backlog do dashboard,
  "modelos personalizados por pessoa") seriam um passo natural a seguir.
- 100% sintético, com anomalias desenhadas para serem plausíveis mas não
  clinicamente validadas — dados reais serão mais subtis e ambíguos.
- Não embarcado nem medido em hardware — só validado no backend/offline.

## Passo 3 — Detetor de duração baseado em regras (2026-07-04)

**Implementado**: `duration_detector.py`, motivado diretamente pelo achado
honesto do passo 2 acima — o LSTM Autoencoder tem recall muito fraco
(0.000-0.331) para anomalias baseadas em duração, porque uma janela de 2
minutos não vê a duração TOTAL de um bloco. Este passo é, ao contrário dos
passos 1 e 2, **uma regra determinística, não um modelo treinado**: compara
a duração de cada bloco de atividade classificado com o intervalo
`[d_min, d_max]` esperado para essa classe+sessão (dia/noite), e sinaliza
também como anómala qualquer classe que não seja esperada de todo nessa
sessão (ex.: "Atividade" a meio da noite, quando só Dormir/Descanso são
esperados).

**Limitação assumida sobre os limites usados**: `[d_min, d_max]` vêm
diretamente de `DAY_BLOCK_MINUTES`/`NIGHT_BLOCK_MINUTES` (`synthetic_data.py`)
— os mesmos parâmetros que o gerador sintético usa para amostrar a duração
de um bloco normal — e **não** dos valores já existentes na vista "Limites
de duração" do dashboard (template de 21 passos, 10 classes mais
granulares, sem correspondência 1-para-1 com as 5 classes simplificadas
usadas aqui). Quando existir histórico real por pessoa, os limites devem
vir daí (item 3 do backlog do dashboard), não do gerador sintético.

### Avaliação (`reports/duration_detector_metrics.json`, seed=123, distinta da usada nos passos 1/2)

40 sujeitos normais + 40 sujeitos por tipo de anomalia, avaliados bloco a
bloco (não janela a janela, ao contrário dos passos 1/2):

| | `duracao_prolongada` | `substituicao_contextual` | `truncamento` |
|---|---|---|---|
| Recall | **1.000** | **1.000** | **0.972** |

Comparação direta com o LSTM Autoencoder (passo 2, recall a limiar fixo):
0.015→**1.000**, 0.331→**1.000**, 0.000→**0.972** — confirma com números
concretos, não só teoria, que os dois detetores são complementares: onde o
autoencoder falha (duração), a regra simples acerta quase sempre, incluindo
a anomalia contextual (via a checagem "classe inesperada nesta sessão",
que também serve de regra de calendário, não só de duração).

**Achado honesto sobre falsos positivos**: taxa de falsos positivos em
blocos normais = 7.17% (154/2147), mas **100% desses falsos positivos
(154/154) acontecem no último bloco de cada sessão** — confirmado
medindo diretamente (não suposição), ver `false_positives_explained_by_last_block_of_session`
no relatório. Causa raiz identificada: `_build_segment_sequence`
(`synthetic_data.py`) corta o último bloco de cada sessão
(`dur = min(dur, remaining)`) para a sessão somar exatamente
`DAY_SESSION_MINUTES`/`NIGHT_SESSION_MINUTES` — um artefacto de como as
sessões sintéticas COMPRIMIDAS são construídas, não uma anomalia real nem
uma falha da regra. **Isto não valida especificidade em dados reais**, onde
não existe esse corte artificial por orçamento de minutos — só confirma que
a implementação da regra está correta e que o artefacto tem uma explicação
concreta, não fabricada.

### Limitações honestas

- 100% sintético, mesma limitação já documentada nos passos 1 e 2.
- Os limites usados são os do próprio gerador (ver acima) — não uma
  calibração independente nem dados reais.
- Não embarcado no firmware — hoje é um script Python de avaliação
  offline; embarcar isto é trivial em comparação com os passos 1/2 (é só
  comparar dois números), mas exige primeiro que o classificador do passo 1
  esteja embarcado e a produzir blocos classificados em tempo real (ver
  "Riscos" no PROJECT_STATUS.md — ainda não está).
- A taxa de falsos positivos ~7% medida aqui é inteiramente um artefacto do
  gerador sintético (ver acima) — não uma medida útil de especificidade em
  produção.

## Próximos passos (por ordem)

1. ~~Medir footprint real (flash/RAM) do Random Forest via `emlearn`~~ —
   **FEITO (2026-07-03)**: ver "Footprint real medido via `emlearn`"
   acima. Resultado: flash não é fator limitante (~5-19KB de ~638KB
   livres), mas a quantização `int16_t` por omissão destrói a accuracy
   (0.978→0.789) — usar `dtype='float'` no `emlearn` resolve isso.
   **Ainda por medir**: footprint do XGBoost via `micromlgen` (não feito
   nesta sessão — `micromlgen` está sem manutenção ativa e o Random
   Forest já mostrou ser viável, reduzindo a urgência de medir a via
   XGBoost) e latência de inferência real em hardware (só foi medido
   footprint estático via compilação; falta correr num nRF52840 real e
   cronometrar, bloqueado pela indisponibilidade atual do hardware — ver
   PROJECT_STATUS.md, "Riscos/bloqueios ativos").
2. ~~LSTM Autoencoder para deteção de anomalias~~ — **FEITO (2026-07-03)**:
   ver "Passo 2 — LSTM Autoencoder" acima. Treinado e avaliado sobre
   sequências sintéticas com anomalias injetadas; AUC-ROC 0.74-0.91 por
   tipo, mas recall a um limiar fixo muito fraco para anomalias de
   duração — achado honesto que reforça a necessidade do passo 3
   (detetor de duração), não um bug a corrigir. **Ainda por fazer**:
   footprint/latência em hardware embarcado (TensorFlow Lite
   Micro/CMSIS-NN, não medido), limiares por contexto/pessoa em vez de um
   único limiar global, dados sintéticos mais realistas (ver item 4).
3. ~~Detetor de duração baseado em regras~~ — **FEITO (2026-07-04)**: ver
   "Passo 3 — Detetor de duração baseado em regras" acima. Recall 0.972-1.000
   nos 3 tipos de anomalia (vs. 0.000-0.331 do LSTM Autoencoder para os
   mesmos tipos), confirmando a complementaridade dos dois detetores com
   números concretos. **Ainda por fazer**: embarcar no firmware (depende do
   passo 1 estar embarcado primeiro), e usar limites calibrados por pessoa
   em vez dos parâmetros do gerador sintético.
4. Tornar os dados sintéticos mais realistas (overlap entre classes,
   sessões de 24h completas em vez de comprimidas, ruído medido em hardware
   real em vez de estimado, e um orçamento de minutos por sessão que não
   force o corte artificial do último bloco — ver achado de falsos
   positivos no passo 3 acima).

## Decisão pendente (não posso decidir por conta própria)

1. Treinar/validar com dados reais de utentes exige consentimento e dados
   reais que só o utilizador (proprietário do projeto) pode disponibilizar —
   não decidido nem assumido aqui.
2. A escolha final entre manter XGBoost (via `micromlgen`, sem manutenção
   ativa) ou migrar para Random Forest (via `emlearn`, mantido ativamente)
   para uma eventual versão embarcada é uma mudança metodológica face ao
   artigo original — precisa de validação do utilizador antes de se avançar.
   O footprint do Random Forest já foi medido (ver "Footprint real medido
   via `emlearn`" acima: flash não é fator limitante), mas o do XGBoost via
   `micromlgen` continua por medir. Adicionalmente, mesmo optando por
   Random Forest, falta decidir entre `inline`+`float` (accuracy intacta,
   ~19KB) e adaptar as features para escala inteira e usar `loadable`
   (mais compacto, mas exige retreinar) — ver detalhe na secção do
   footprint.

Ambas continuam registadas como decisão pendente também no PROJECT_STATUS.md.
