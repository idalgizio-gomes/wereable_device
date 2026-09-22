# C30 — Avaliação Experimental

Esta secção reporta os resultados dos quatro ensaios anteriores (C13+C20,
C22, C26, C29). **Importante para quem for integrar isto no relatório**:
nem todos têm números reais ainda — alguns instrumentos foram construídos
e validados nesta sessão, mas ainda não foram corridos com dados reais
(precisam de sessões com participantes/hardware a decorrer no tempo, não
de mais trabalho de engenharia). Cada secção abaixo diz explicitamente se
tem resultados reais ou se é um placeholder pronto a preencher — não
inventei nenhum número.

## 1. Desempenho de comunicação (C13 + C20)

**Estado: instrumentação pronta e validada, resultados por recolher.**

Ferramenta: `bridge/latency_harness.py`. Mede:
- **C20** (bridge→dashboard, WebSocket): latência com precisão de ms, via
  `_send_ts_ms` injetado em cada broadcast.
- **C13** (sensor BLE→bridge): latência com precisão de ms, via uma
  characteristic BLE dedicada (`latencyProbeChar`) que publica
  `{rec_seq, epoch_ms}` no momento em que cada registo é gravado no
  wearable; o bridge correlaciona por `rec_seq` com a chegada do dado
  cifrado e calcula o delta.
- Jitter (variação do intervalo entre chegadas).
- Packet loss / gaps de `rec_seq` (proxy, não distingue perda real no BLE
  de registos filtrados antes do broadcast).

Confirmado nesta sessão: firmware com o probe de latência compila e está
flashado na placa; bridge liga, empareleia e recebe dados (log real:
`ligado e a receber dados`).

**Por fazer** (fora do âmbito de engenharia, precisa de tempo de recolha):
```
python bridge/latency_harness.py --session <token> --duration 120 --out latencia.csv
```
correr durante uso normal do dispositivo (idealmente >10 min, várias
distâncias/obstáculos entre wearable e o PC do bridge), depois colar aqui
o resumo impresso pelo script (médias/medianas/p95 de latência C13, C20,
jitter, gaps de sequência).

### Tabela de resultados (preencher depois de correr o harness)

| Métrica | C13 (sensor→bridge) | C20 (bridge→dashboard) |
|---|---|---|
| Latência média (ms) | _(pendente)_ | _(pendente)_ |
| Latência mediana (ms) | _(pendente)_ | _(pendente)_ |
| p95 (ms) | _(pendente)_ | _(pendente)_ |
| Jitter médio (ms) | — | _(pendente)_ |
| Gaps de `rec_seq` detetados | _(pendente)_ | _(pendente)_ |

## 2. Usabilidade (C22)

**Estado: instrumento pronto e testado, resultados por recolher.**

Ferramenta: `USABILIDADE_SUS_UEQ.md` (guião de tarefas por perfil `utente`/
`clinico`, questionário SUS + UEQ-S) + `USABILIDADE_RESPOSTAS_TEMPLATE.csv`
+ `scoring_sus_ueq.py` (testado nesta sessão com dados fictícios — SUS=85.0
calculado corretamente à mão, confirmando que a fórmula está certa).

**Por fazer**: recrutar n=5-8 participantes por perfil, correr o guião de
tarefas, aplicar os questionários, preencher o CSV, correr:
```
python scoring_sus_ueq.py USABILIDADE_RESPOSTAS_TEMPLATE.csv
```

### Tabela de resultados (preencher depois da recolha)

| Perfil | n | SUS médio | UEQ pragmática | UEQ hedónica | Taxa de sucesso nas tarefas |
|---|---|---|---|---|---|
| utente (cuidador) | _(pendente)_ | _(pendente)_ | _(pendente)_ | _(pendente)_ | _(pendente)_ |
| clinico | _(pendente)_ | _(pendente)_ | _(pendente)_ | _(pendente)_ | _(pendente)_ |

Lembrar (já documentado em `USABILIDADE_SUS_UEQ.md`): com n=5-8, apresentar
como sinal direcional + observações qualitativas, não como resultado
estatisticamente significativo.

## 3. Acessibilidade — WCAG 2.1 (C26)

**Estado: única secção com resultados reais já obtidos** — auditoria de
código completa, não uma recolha pendente.

Fonte: `WCAG_CHECKLIST.md`. Resumo dos achados (já verificados por leitura
direta do código, não são estimativas):

- **Conforme**: contraste de texto (rácios calculados e documentados no
  próprio CSS, ambos os temas), skip link, fecho de todos os 5 modais por
  Escape, `<html lang>` acompanha a troca de idioma em runtime, técnica de
  associação label⇄campo em formulários.
- **Não conforme / gaps reais**:
  1. `aria-live` praticamente ausente (1 ocorrência em todo o dashboard) —
     alertas clínicos não são anunciados a leitores de ecrã. Maior risco
     dado o contexto (dashboard de vigilância clínica).
  2. Mensagens de erro de formulário sem `aria-describedby`/`aria-invalid`
     (zero ocorrências confirmadas).
  3. Focus-trap por Tab não implementado nos modais (Escape já funciona,
     mas Tab pode sair do modal para a página por trás).
- **Não testável por leitura de código** (precisa de teste manual no
  browser): zoom 200%, ordem de tabulação, contraste de componentes
  não-textuais.

Este é o único bloco desta secção pronto a citar tal e qual no relatório
final, sem mais trabalho de recolha.

## 4. Validação/ética (C29)

**Estado: desenho do estudo concluído, execução não iniciada (depende de
aprovação institucional).**

Fonte: `ETICA_VALIDACAO_C29.md`. Não há resultados a reportar aqui — por
definição, C29 é só o protocolo (perfis de participante, critérios de
inclusão/exclusão, procedimentos, métricas de segurança/usabilidade/
precisão, riscos e mitigações, proteção de dados, checklist de submissão a
comissão de ética). A secção 7 desse documento assinala explicitamente que
a cifra em repouso da base de dados (GDPR-005) tem de ser reavaliada e
aceite antes de recolher dados reais de participantes.

## 5. Síntese e limitações do que existe hoje

O que este projeto tem, no momento em que este documento foi escrito
(2026-09-22): instrumentação de medição validada e testada para C13/C20/
C22, uma auditoria de acessibilidade já completa (C26), e um protocolo de
validação pronto para submissão (C29). O que falta para o relatório final
estar completo é puramente **tempo de recolha** (correr o harness em uso
real, recrutar participantes para o SUS/UEQ) — não mais desenvolvimento.

Limitação a reportar no relatório, seja qual for o resultado numérico
obtido: a medição de C13/C20 foi feita com o bridge a correr na mesma
máquina que recebe o WebSocket (necessário para os timestamps serem
comparáveis sem NTP entre relógios — ver docstring de
`latency_harness.py`), o que não reflete um cenário de dashboard acedido a
partir de outra máquina na rede; e o n do C22 (5-8) é adequado para sinal
qualitativo, não para conclusões estatísticas generalizáveis.
