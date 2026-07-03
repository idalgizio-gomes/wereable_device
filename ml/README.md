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

1. Medir footprint real (flash/RAM) e latência do modelo XGBoost treinado
   aqui, convertido via `micromlgen`, numa placa de teste — e comparar com
   um Random Forest via `emlearn` treinado sobre os mesmos dados/features,
   como baseline de decisão (ver ressalva acima). Só depois decidir se este
   classificador é embarcado no firmware ou corre num serviço backend.
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
   artigo original — precisa de validação do utilizador antes de se avançar,
   depois de medido o footprint real de ambas as opções (ver "Próximos
   passos" acima).

Ambas continuam registadas como decisão pendente também no PROJECT_STATUS.md.
