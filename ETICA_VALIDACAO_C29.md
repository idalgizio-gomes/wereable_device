# C29 — Protocolo de validação do dispositivo CareWear (desenho de estudo)

> **Documento de desenho de estudo, para eventual submissão a uma comissão de
> ética.** Não contém resultados nem dados de participantes reais — é a
> definição de objetivos, população, procedimentos, métricas, riscos e
> proteção de dados **antes** de qualquer recolha acontecer. Distingue-se do
> C22 (`USABILIDADE_SUS_UEQ.md`), que testa a usabilidade do *dashboard* com
> cuidadores/clínicos; o C29 testa o *dispositivo e a deteção* em pessoas
> reais, incluindo potencialmente pessoas com demência (população
> vulnerável).

## 0. Enquadramento e diferença face ao C22

O C22 já correu (ou está pronto a correr) com n=5-8 participantes nos perfis
`utente` (cuidador/familiar) e `clinico` (profissional de saúde), testando se
o dashboard é fácil de usar. Esse protocolo não recolhe dados fisiológicos
reais de ninguém a usar o wearable no corpo — usa dados de demonstração
(`demo-data.js`).

O C29 é diferente em natureza, não só em conteúdo: aqui o dispositivo
(nRF52840 + firmware + bridge) é colocado no corpo de uma pessoa real, e o
que se valida é se a deteção (queda, saída da rotina, estado de
uso/"wear status", eventualmente frequência cardíaca/SpO2) funciona em
condições reais — não simuladas. Isto muda a categoria do estudo: passa a
envolver risco físico direto (por mínimo que seja) e, se a população-alvo
incluir pessoas com demência, uma população vulnerável que não pode dar
consentimento informado nos mesmos termos que um adulto capaz. É por isso
que este documento tem secções que o C22 não precisa de ter (consentimento
por representante, avaliação de risco físico, checklist de submissão a
comissão de ética).

---

## 1. Objetivos do estudo

**Objetivo geral:** validar, em condições de uso real (fora do laboratório),
se o dispositivo CareWear deteta corretamente os eventos para os quais foi
desenhado, com uma taxa de falsos positivos/negativos aceitável, sem impor
risco ou desconforto indevido à pessoa que o usa.

**Objetivos específicos:**

1. Medir a taxa de deteção correta de eventos de queda/emergência
   simulados (sensibilidade) e a taxa de falsos positivos em uso normal
   (especificidade), num período de uso contínuo.
2. Verificar se o estado "a usar o dispositivo" (wear status) reportado
   pelo firmware corresponde ao estado real, incluindo os casos limite
   (dispositivo mal colocado, colocado mas parado, retirado temporariamente).
3. Avaliar a precisão dos sensores (frequência cardíaca, e outros sensores
   presentes no hardware) por comparação com uma referência conhecida
   (ex. oxímetro/monitor validado, não outro wearable de consumo).
4. Recolher a experiência subjetiva de uso do dispositivo físico em corpo —
   conforto, incómodo, ansiedade — distinta da usabilidade do dashboard já
   coberta pelo C22.
5. Verificar se, quando o dispositivo falha (bateria, desconexão BLE, falha
   de deteção), essa falha é visível e comunicada a tempo ao
   cuidador/clínico, e não silenciosa.

**O que este estudo explicitamente não faz:** não testa a usabilidade do
dashboard (já coberto pelo C22), não é um ensaio clínico de eficácia
terapêutica, e não pretende diagnosticar ou tratar demência — é validação
técnica de um dispositivo de deteção/monitorização.

---

## 2. Perfis de participante e recrutamento

Três perfis, com processos de recrutamento e consentimento distintos:

### 2.1 Pessoas com demência (população vulnerável) — participantes que usam o dispositivo

Este é o perfil central do C29 e o que exige mais cuidado. Aplicam-se
princípios de investigação com populações vulneráveis (ex. Declaração de
Helsínquia, orientações CNPD/RGPD para dados de saúde):

- **Consentimento em dois níveis, não um só:**
  1. **Representante legal ou cuidador principal** (procurador de cuidados
     de saúde, tutor, ou familiar responsável reconhecido) dá o
     consentimento informado formal, por escrito, depois de explicação
     completa do estudo (o que o dispositivo faz, que dados recolhe, riscos,
     direito de retirada a qualquer momento sem justificação).
  2. **Assentimento da própria pessoa**, sempre que a sua capacidade o
     permita — mesmo em demência ligeira a moderada, a pessoa deve ser
     informada de forma simples e adaptada, e o seu desconforto ou recusa
     visível (verbal ou comportamental) deve ser respeitado e interrompe a
     participação **mesmo que o representante tenha consentido**. O
     consentimento do representante nunca sobrepõe a recusa expressa da
     pessoa.
- **Recrutamento:** através de instituições de cuidados (lares, centros de
  dia) ou associações de apoio a cuidadores/doentes com demência, nunca por
  contacto direto não solicitado à pessoa com demência. Recomenda-se
  parceria formal com uma instituição que já tenha relação de confiança
  estabelecida com as famílias.
- **Reavaliação contínua do consentimento:** em estudos longitudinais com
  demência, a capacidade pode variar ao longo do tempo; o consentimento
  deve ser reconfirmado em cada sessão, não assumido como permanente desde
  o dia 1.

### 2.2 Cuidadores/familiares — participantes que interagem com o sistema mas não necessariamente usam o dispositivo

- Podem participar como observadores/relatores da experiência da pessoa com
  demência (ex. "notou desconforto ao longo do dia?") e/ou como utilizadores
  do dashboard em paralelo (ligação ao perfil `utente` do C22).
- Consentimento informado direto, adulto capaz — processo padrão.
- Podem também ser o próprio representante legal do participante com
  demência (papel duplo, deve ser clarificado no formulário: em que
  capacidade está a assinar cada secção).

### 2.3 Profissionais de saúde — participantes que avaliam a deteção do ponto de vista clínico

- Enfermeiros, médicos ou técnicos que possam validar clinicamente se um
  evento assinalado pelo dispositivo correspondeu a um evento real
  (ex. revisão retrospetiva de alertas registados).
- Podem também usar o próprio dispositivo em contexto controlado (não em
  demência) para testes de precisão de sensores num adulto saudável,
  separando essa validação técnica da validação em população vulnerável.
- Consentimento informado direto, adulto capaz — processo padrão, ligação
  ao perfil `clinico` do C22.

### 2.4 Dimensão amostral (indicativa, para desenho — não é um resultado)

Dado o contexto académico (curso "2AI", IPCA) e o precedente do C22 (n=5-8),
antecipa-se uma amostra pequena e não-representativa estatisticamente:
indicativamente 3-6 pessoas com demência (só se a instituição parceira e a
comissão de ética aprovarem), 3-6 cuidadores/familiares, 2-4 profissionais
de saúde. Tal como no C22, os números devem ser tratados como sinal
direcional e qualitativo, não prova estatística — sem testes de
significância com n tão pequeno.

---

## 3. Critérios de inclusão/exclusão

### 3.1 Pessoas com demência

**Inclusão:**
- Diagnóstico de demência (qualquer estádio) documentado por profissional
  de saúde responsável.
- Representante legal/cuidador principal identificável e disponível para
  consentimento e acompanhamento durante as sessões.
- Sem contraindicação médica conhecida ao uso de um dispositivo vestível no
  pulso/corpo (ex. lesão cutânea na zona, alergia a materiais do
  dispositivo).
- Capacidade de tolerar o dispositivo por período mínimo definido no
  protocolo (ver secção 4), avaliada previamente pelo cuidador.

**Exclusão:**
- Recusa expressa ou sinais de agitação/desconforto significativo perante o
  dispositivo em fase de familiarização (antes mesmo de iniciar a recolha).
- Condição médica aguda instável no momento (ex. internamento recente,
  infeção ativa) que desaconselhe qualquer procedimento adicional.
- Ausência de representante legal/cuidador disponível para todo o período
  do estudo.
- Estádio de demência tão avançado que introduza risco de auto-lesão ao
  manusear ou tentar remover o dispositivo sem supervisão constante (avaliar
  caso a caso com o cuidador/profissional de saúde responsável).

### 3.2 Cuidadores/familiares

**Inclusão:** maior de idade, capacidade de dar consentimento informado
próprio, relação de cuidado direta e continuada com a pessoa com demência
participante (quando aplicável).

**Exclusão:** incapacidade de compreender o formulário de consentimento
mesmo com apoio de explicação oral.

### 3.3 Profissionais de saúde

**Inclusão:** exercício profissional ativo em área relevante (enfermagem,
medicina, ou técnico de saúde com experiência em cuidados a pessoas com
demência ou em monitorização de sinais vitais).

**Exclusão:** nenhum critério de exclusão específico para além do
consentimento informado padrão.

---

## 4. Procedimentos e tarefas

### 4.1 Estrutura geral das sessões

| Fase | Conteúdo | Duração indicativa |
|---|---|---|
| 0. Consentimento e familiarização | Explicação do estudo, obtenção de consentimento (e assentimento), período de habituação ao dispositivo sem recolha de dados válida para análise | 20-30 min |
| 1. Colocação e verificação | Colocar o dispositivo, confirmar wear status correto no dashboard, checklist de conforto inicial | 5-10 min |
| 2. Uso monitorizado | Uso contínuo do dispositivo em atividades do quotidiano supervisionadas (ver 4.2) | 1 sessão de 2-4h, ou várias sessões mais curtas — a decidir com a instituição parceira, nunca uso não supervisionado em pessoa com demência nesta fase de validação |
| 3. Eventos simulados controlados | Simulação supervisionada de queda (ex. protocolo de queda controlada em colchão/ambiente seguro, ou simulação com boneco/manequim instrumentado como alternativa sem risco físico — a decidir por avaliação de risco) e de remoção do dispositivo | 15-30 min, com supervisão direta de profissional de saúde presente |
| 4. Remoção e debriefing | Retirar o dispositivo, questionário/entrevista curta de conforto e experiência subjetiva, oportunidade de retirar consentimento retroativamente para os dados da sessão | 15-20 min |

### 4.2 O que se pede a cada perfil

**Pessoa com demência:** usar o dispositivo no pulso durante as atividades
habituais do dia (sentada, a caminhar com supervisão, a comer, a descansar),
sem instruções técnicas — o dispositivo deve ser transparente ao
quotidiano. Não se pede a executar tarefas artificiais fora da sua rotina.
Toda a sessão decorre com supervisão direta e contínua (cuidador e/ou
profissional de saúde presente fisicamente, não à distância).

**Cuidador/familiar:** acompanhar a sessão, reportar observações sobre
desconforto ou reação da pessoa (verbal ou não-verbal), e opcionalmente
interagir com o dashboard em paralelo (ligação ao C22 se o mesmo
participante já tiver feito esse teste).

**Profissional de saúde:** supervisionar clinicamente a sessão de eventos
simulados, confirmar retrospetivamente se os eventos assinalados pelo
dispositivo correspondem a eventos reais, e opcionalmente testar a precisão
de sensores em si próprio (contexto controlado, não em demência).

### 4.3 Número de sessões

Indicativo: 1 sessão de familiarização + 1-2 sessões de recolha por
participante com demência, com intervalo mínimo entre sessões a definir
consoante a tolerância observada — não encadear sessões longas sem
avaliação de fadiga/desconforto entre elas.

---

## 5. Métricas

### 5.1 Segurança / deteção (métrica central do C29)

- **Sensibilidade de deteção de queda/emergência**: proporção de eventos
  simulados corretamente detetados e assinalados pelo sistema (firmware →
  bridge → dashboard/alerta), dentro de uma janela de tempo aceitável
  definida a priori.
- **Taxa de falsos positivos**: eventos assinalados como queda/emergência
  que não corresponderam a evento real, durante o uso normal supervisionado.
- **Taxa de falsos negativos**: eventos reais simulados que não foram
  detetados.
- **Latência de deteção → alerta**: tempo entre o evento e a chegada do
  alerta ao dashboard (ligação direta ao trabalho de deteção de bugs de
  pipeline já feito no projeto, ex. bug do `force_reading` BLE corrigido).
- **Precisão do wear status**: correspondência entre o estado reportado
  ("a usar"/"não a usar") e o estado real observado pelo cuidador presente.

### 5.2 Precisão dos sensores

- Comparação do valor de frequência cardíaca (e outros sensores presentes)
  do CareWear contra um dispositivo de referência validado clinicamente
  (não outro wearable de consumo), no mesmo intervalo de tempo.
- Reportar diferença média e amplitude de erro, não apenas médias — dado o
  n pequeno, tal como definido para o C22.

### 5.3 Usabilidade (ligação ao C22)

- Este protocolo não repete o SUS/UEQ do C22. Em vez disso, regista
  observações qualitativas específicas do uso do **dispositivo físico**
  (não do dashboard): facilidade de colocar/retirar, se o cuidador
  conseguiu operar o dispositivo sem ajuda técnica, se o wear status ficou
  claro no dashboard durante a sessão.
- Se o mesmo participante cuidador/clínico já tiver feito o C22, os dois
  conjuntos de observações podem ser cruzados no relatório final (C30), mas
  mantêm-se como instrumentos distintos, não fundidos num só score.

### 5.4 Conforto e experiência subjetiva

- Questionário/entrevista curta pós-sessão (adaptado à capacidade da
  pessoa — escala simples de rostos/cores para a pessoa com demência
  quando aplicável, ou relato do cuidador quando a pessoa não conseguir
  autorrelatar), e relato livre do cuidador presente sobre sinais de
  ansiedade ou desconforto observados.

---

## 6. Riscos e mitigações

| Risco | Probabilidade | Gravidade | Mitigação |
|---|---|---|---|
| Desconforto físico de usar o dispositivo (pressão, calor, irritação cutânea) | Média | Baixa | Período de familiarização antes de recolha válida; checklist de conforto inicial; interromper de imediato perante sinais de desconforto; sessões curtas, não uso 24h nesta fase de validação. |
| Ansiedade ou confusão da pessoa com demência perante um objeto novo no corpo | Média-alta (varia muito por estádio/pessoa) | Média | Supervisão constante por cuidador conhecido da pessoa (não um estranho); introdução gradual; assentimento contínuo — parar sempre que haja sinal de mal-estar, independentemente do consentimento do representante; preferir ambiente familiar à pessoa (casa, instituição onde já vive) em vez de laboratório. |
| Falha do dispositivo durante um evento real (não simulado) durante a sessão — ex. queda genuína não detetada | Baixa (mas gravidade alta se ocorrer) | Alta | Supervisão humana direta e contínua durante toda a sessão é a rede de segurança real, não o dispositivo — o CareWear nesta fase de validação é um sistema em teste, nunca a única camada de proteção. Profissional de saúde ou cuidador presente deve poder intervir fisicamente independentemente do que o dispositivo reporta. |
| Simulação de queda controlada causar lesão física | Baixa (se protocolo seguido) | Alta se ocorrer | Preferir simulação sem impacto real (manequim instrumentado, ou simulação de movimento sem queda física) para participantes com demência; se for necessário movimento real, apenas em ambiente seguro (colchão, superfície amortecida) e com profissional de saúde presente a validar a segurança do protocolo antes de cada repetição. |
| Dados de sensores revelarem informação de saúde inesperada (ex. arritmia não diagnosticada) | Baixa | Média-alta | Definir antecipadamente o procedimento: se um valor fora do normal for observado, o profissional de saúde presente decide se e como comunicar à família/médico assistente — não fica só registado para o estudo. |
| Fadiga da pessoa com demência por sessões longas ou repetidas | Média | Baixa-média | Sessões curtas (ver secção 4), intervalo entre sessões avaliado caso a caso, direito de parar a qualquer momento sem necessidade de justificação. |
| Estigmatização ou desconforto do cuidador ao ver a pessoa "vigiada" tecnologicamente | Baixa | Baixa | Debriefing pós-sessão inclui espaço para o cuidador expressar desconforto próprio, não só da pessoa com demência; enquadrar o estudo como validação técnica, não vigilância permanente. |

---

## 7. Proteção de dados (RGPD)

Este estudo recolhe dados de saúde de pessoas identificáveis — categoria
especial de dados ao abrigo do RGPD (art. 9º), com um agravante: parte dos
titulares (pessoas com demência) pode não ter capacidade plena de exercer
os seus próprios direitos de titular de dados, o que reforça, não reduz, as
obrigações do responsável pelo tratamento.

**Estado atual do sistema (`SECURITY_STATUS.md`), relevante para este
protocolo:**

- **GDPR-005 (cifra da base de dados em repouso)**: mitigado em
  2026-09-21 com cifra AES-256-GCM ao nível do ficheiro (`.db.enc`), não ao
  nível do motor (sem SQLCipher). Existe um risco residual documentado:
  janela em texto simples durante a sessão ativa e entre checkpoints (até
  15 min). **Antes de recolher dados reais de participantes do C29, este
  risco residual deve ser reavaliado explicitamente** — para dados de saúde
  reais de pessoas identificáveis (e não apenas dados de demonstração), a
  janela em texto simples pode não ser um risco aceitável sem controlo de
  acesso físico adicional à máquina onde a base de dados corre durante a
  sessão. Recomenda-se decidir e documentar esta aceitação de risco (ou
  fechá-la com SQLCipher) antes da fase 4.4, não depois.
- **GDPR-001 (consentimento por representante)**: parcialmente
  implementado no modelo de dados (`ConsentRecord` com `given_by`,
  `representative_relationship`, `representative_name`, `legal_basis`), mas
  a UI do dashboard ainda não liga `setConsent()` a este registo com
  verificação de identidade de quem aciona. **Para o C29, o consentimento
  do representante legal não pode depender só desta ligação por fazer** — o
  processo de consentimento em papel/formulário físico descrito na secção 2
  é o mecanismo formal válido para o estudo, independentemente do estado do
  software; o registo digital é um espelho a manter sincronizado, não a
  fonte de verdade legal enquanto a UI não estiver pronta.
- **GDPR-002 (PII em `localStorage`)**: maioritariamente corrigido (TTL de
  30 dias, opção de apagar tudo), mas continua sem cifra no `localStorage`
  por limitação estrutural do browser. Relevante se o dashboard for usado
  em dispositivo partilhado (ex. tablet da instituição) — recomenda-se usar
  sessão dedicada e fazer logout/purga manual no fim de cada sessão do
  estudo.
- **GDPR-004 (TLS no WebSocket bridge↔dashboard)**: desligado por omissão,
  opt-in via `CAREWEAR_WS_TLS=1`. **Para o C29 deve ser ativado**, dado que
  os dados em trânsito passam a ser fisiológicos reais de pessoas
  identificáveis, não dados de demonstração.

**Princípios a aplicar especificamente neste estudo:**

1. **Minimização**: recolher apenas os sinais necessários para responder
   às métricas da secção 5 — não ativar sensores ou funcionalidades do
   dispositivo que não estejam a ser avaliados.
2. **Pseudonimização desde a origem**: atribuir código de participante
   (ex. `P01`) em vez de nome em qualquer ficheiro de dados de sessão; a
   tabela de correspondência nome↔código fica separada, com acesso
   restrito, e não entra no mesmo repositório/base de dados que os dados
   fisiológicos.
3. **Retenção limitada**: os dados recolhidos neste estudo têm finalidade
   de validação técnica, não de cuidado clínico continuado — definir
   antecipadamente por quanto tempo são guardados após o fim do estudo e
   quando são apagados (ligação a GDPR-006, retenção de alertas de
   emergência, já definida para o sistema em geral).
4. **Direito de retirada retroativa**: o participante (ou o seu
   representante) pode pedir a eliminação dos seus dados mesmo depois da
   sessão terminada, sem necessidade de justificação — o fluxo de
   `eraseAllLocalData()` já existente deve ser estendido/confirmado para
   cobrir também os dados de sessão do C29, não só o `localStorage` do
   dashboard.
5. **Sem transferência para terceiros**: os dados do estudo não saem do
   ambiente do projeto académico sem novo consentimento explícito — em
   particular, nenhuma cloud de terceiros não avaliada, nenhuma partilha
   com fornecedores do hardware.
6. **Condição prévia à recolha real**: como já assinalado, GDPR-005 (cifra
   em repouso) deve estar numa posição de risco aceite e documentada — não
   silenciosa — antes do início da fase 4.4 (recolha de dados reais). Este
   protocolo não deve avançar para participantes reais enquanto essa
   decisão não estiver tomada e registada.

---

## 8. Checklist de submissão a comissão de ética

Checklist de preparação do dossier de submissão — a preencher antes de
qualquer contacto com participantes reais:

- [ ] Protocolo completo (este documento) revisto e assinado pelo
      responsável do projeto/orientador académico.
- [ ] Formulário de consentimento informado para adulto capaz
      (cuidador/familiar, profissional de saúde) redigido em linguagem
      acessível, com secção de retirada de consentimento a qualquer momento.
- [ ] Formulário de consentimento informado para representante legal de
      pessoa com demência, com campos para identificação da relação/base
      legal de representação (ligado ao modelo `ConsentRecord` já existente
      no sistema).
- [ ] Formulário/procedimento de assentimento adaptado para a própria
      pessoa com demência (linguagem simples, apoio visual se necessário),
      incluindo instrução explícita à equipa de como reconhecer e respeitar
      sinais de recusa não-verbal.
- [ ] Avaliação de risco físico assinada por profissional de saúde
      responsável, cobrindo especificamente o procedimento de simulação de
      queda/emergência (secção 6).
- [ ] Confirmação por escrito da instituição parceira (lar, centro de dia,
      associação) de que aceita colaborar no recrutamento e supervisão.
- [ ] Plano de proteção de dados anexado (secção 7 deste documento), com
      confirmação explícita do estado do GDPR-005 (cifra em repouso) à data
      da submissão — não referir apenas o estado geral do sistema, indicar
      a decisão tomada especificamente para este estudo.
- [ ] Procedimento definido para o caso de deteção incidental de um valor
      de saúde anómalo (ex. arritmia) fora do âmbito do estudo.
- [ ] Critérios de inclusão/exclusão (secção 3) revistos por profissional
      de saúde com experiência em demência, não só pela equipa técnica.
- [ ] Plano de debriefing e de resposta a eventos adversos durante a
      sessão (quem contactar, em que prazo, como registar).
- [ ] Confirmação de que nenhum dado de participante real foi recolhido
      antes da aprovação formal da comissão de ética.
- [ ] Identificação do responsável pelo tratamento de dados (RGPD) e
      contacto para exercício de direitos dos titulares, incluído nos
      formulários de consentimento.
- [ ] Data prevista de início, número indicativo de participantes por
      perfil (secção 2.4), e critério de paragem antecipada do estudo se
      surgirem eventos adversos.

---

## Nota final

Este documento é desenho de estudo, não execução. Nenhuma sessão com
participantes reais (em particular pessoas com demência) deve começar antes
de: (a) aprovação formal da comissão de ética competente, (b) confirmação
de que a mitigação de GDPR-005 está numa posição aceite e documentada para
dados reais, e (c) formulários de consentimento/assentimento assinados e
revistos por quem tem competência para os validar. O relatório de
resultados do C29 (se e quando o estudo correr) é um documento separado
deste, tal como o C22 tem o seu próprio ficheiro de resultados distinto do
protocolo.
