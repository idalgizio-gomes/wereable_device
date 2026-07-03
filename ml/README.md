# Pipeline de Machine Learning — CareWear

Esta pasta vai conter o pipeline de deteção de rotina/anomalias descrito no
artigo científico do projeto ("Routine-Aware Behavioural Monitoring Framework
for Dementia Care Using Wearable-Derived Synthetic Daily Routines"), adaptado
às restrições reais desta placa (Seeed XIAO nRF52840 Sense Plus).

**Estado: ainda não implementado.** Este README documenta as decisões
técnicas já tomadas, para quem (humano ou a rotina cloud diária) continuar
o trabalho não ter de repetir a investigação.

## Componentes do pipeline (do artigo)

1. **Classificador de atividades** — 10 classes (Sentado, Deitado, A andar,
   Movimento ligeiro, A comer com talheres/à mão, Duche, Higiene oral, De pé,
   Lavar as mãos).
2. **Detetor de anomalias comportamentais** — LSTM Autoencoder sobre a
   sequência de atividades classificadas.
3. **Detetor de duração** — baseado em regras (limites do template de 21
   passos), já trivialmente embarcável (ver `Ble.cpp`/`kDumpCtrlResetReadings`
   e a tabela de limites no dashboard, vista "Limites de duração").

## Decisão técnica: classificador de atividades

**O artigo usa XGBoost (400 estimadores, profundidade 6). Esta decisão foi
revista** depois de investigação aprofundada (2026-07-03, ver
`PROJECT_STATUS.md` → "Estudo de viabilidade TinyML"):

- 400 estimadores × 10 classes em modo multiclasse do XGBoost não são 400
  árvores — são **~4000 árvores internas** (uma árvore por classe por ronda
  de boosting).
- Um precedente publicado mostrou 500 árvores a exigirem 553–727KB de flash
  só para caberem — praticamente todo o orçamento de flash desta placa
  (811KB total, ~638KB livres com o resto do firmware já a correr), para
  menos de um oitavo do número de árvores do artigo.
- `micromlgen` (a ferramenta que converteria XGBoost→C) está sem manutenção
  ativa (repositório arquivado) e tem bugs documentados por resolver.

**Decisão (a confirmar com o utilizador antes de treinar)**: substituir o
classificador XGBoost por um **Random Forest com ~50-100 árvores rasas
(profundidade ≤4-5)**, convertido via
[`emlearn`](https://github.com/emlearn/emlearn) — mantido ativamente,
já comprovado em hardware nRF52 real segundo a investigação feita. Isto é
uma mudança metodológica face ao artigo original, não só uma escolha de
implementação — precisa de validação humana antes de se avançar.

`micromlgen` (XGBoost→C) fica como alternativa de recurso, só se a precisão
do Random Forest não for suficiente depois de treinado e avaliado.

## Decisão técnica: detetor de anomalias (LSTM Autoencoder)

Ainda por decidir em detalhe. Caminhos identificados na investigação:
- Inferência em fluxo (um instante de cada vez) + quantização int8, usando
  `CMSIS-NN`/`CMSIS-DSP` (ARM, otimizado para o Cortex-M4F desta placa) ou
  TensorFlow Lite Micro (~20-30KB de biblioteca).
- Considerar partir de um modelo pré-treinado
  ([`OxWearables/ssl-wearables`](https://github.com/OxWearables/ssl-wearables),
  aprendizagem auto-supervisionada sobre o UK-Biobank) em vez de treinar do
  zero — a literatura mostra ganhos consistentes de F1 com esta abordagem
  quando os dados rotulados são escassos, que é exatamente a situação aqui.

## Dados de treino

O artigo usa rotinas sintéticas geradas a partir de segmentos reais de
sensores (12 participantes, 10 classes de atividade, template de 21 passos).
Este projeto ainda não tem esse corpus de dados reais — os "dados
sintéticos" atualmente no dashboard (`web/dashboard/index.html`) são
puramente ilustrativos/de demonstração, não vêm de sensores reais.

**Fonte de validação externa identificada**: o
[TIHM Dataset](https://www.nature.com/articles/s41597-023-02519-y) (dados
reais multi-sensor de demência, com eventos adversos rotulados,
[código/dados aqui](https://github.com/PBarnaghi/TIHM-Dataset)) pode servir
para validar o detetor de anomalias, complementando os dados sintéticos do
artigo.

## Próximos passos concretos

1. Recolher/gerar um corpus de treino real (dados sintéticos próprios, à
   semelhança do artigo, a partir dos sensores reais desta placa).
2. Treinar o Random Forest (scikit-learn) e avaliar contra o classificador
   XGBoost do artigo como baseline de comparação.
3. Converter via `emlearn`, medir footprint real (flash/RAM) e latência de
   inferência nesta placa — não assumir, medir.
4. Só depois, avançar para o detetor de anomalias (LSTM Autoencoder).

Este ficheiro deve ser atualizado a cada avanço — é o que a rotina cloud
diária "CareWear — Melhoria diária do dashboard e do modelo ML" lê e edita
(ver `PROJECT_STATUS.md` → "Rotinas cloud agendadas").
