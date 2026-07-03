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

**Estado atual: passo 1 (classificador de atividades) implementado e
treinado sobre dados sintéticos** (código nesta pasta). Passos 2 e 3 ainda
não existem neste repositório — ver "Próximos passos" abaixo.

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
  features.py                    # extração de features estatísticas por janela
  synthetic_data.py              # gerador de dados sintéticos de rotina
  train_activity_classifier.py   # treino + avaliação do XGBoost
  requirements.txt
  data/
    synthetic_routine_dataset.csv        # gerado, NÃO versionado (ver .gitignore)
    synthetic_routine_dataset.meta.json  # metadados do dataset gerado, versionado
  models/
    activity_classifier_xgb.json         # modelo treinado (XGBoost, formato nativo)
    activity_classifier_labels.json      # classes + nomes das features, na mesma ordem do modelo
  reports/
    activity_classifier_metrics.json          # accuracy, classification report, matriz de confusão
    activity_classifier_confusion_matrix.png   # visualização da matriz de confusão
```

Para reproduzir:

```bash
cd ml
pip install -r requirements.txt
python synthetic_data.py              # gera data/synthetic_routine_dataset.csv
python train_activity_classifier.py   # treina e avalia, escreve em models/ e reports/
```

Ambos os scripts são determinísticos (seed fixa = 42).

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
2. **LSTM Autoencoder para deteção de anomalias** — treinar sobre a
   sequência de atividades classificadas (ou sobre as features brutas por
   janela) para detetar padrões que fogem à rotina habitual de cada sujeito.
   Vai exigir gerar também sequências sintéticas com anomalias injetadas
   (o dashboard já tem uma visualização de protótipo disto — "Simulação com
   anomalias injetadas" em `web/dashboard/index.html` — mas sem modelo real
   por trás ainda). Caminhos identificados para a inferência: fluxo (um
   instante de cada vez) + quantização int8 via `CMSIS-NN`/`CMSIS-DSP` ou
   TensorFlow Lite Micro (~20-30KB de biblioteca); considerar partir de um
   modelo pré-treinado
   ([`OxWearables/ssl-wearables`](https://github.com/OxWearables/ssl-wearables),
   aprendizagem auto-supervisionada sobre o UK-Biobank) em vez de treinar do
   zero — a literatura mostra ganhos consistentes de F1 com esta abordagem
   quando os dados rotulados são escassos, que é exatamente a situação aqui.
3. **Detetor de duração baseado em regras** — comparar a duração de cada
   bloco de atividade classificado com os limites configuráveis já
   presentes no dashboard (vista "Limites de duração", Médico/Técnico).
4. Tornar os dados sintéticos mais realistas (overlap entre classes,
   sessões de 24h completas em vez de comprimidas, ruído medido em hardware
   real em vez de estimado).

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
