# CareWear — Relatório de Perguntas, Dilemas e Decisões (Detalhado)

> Nível de profundidade: **detalhado**. Documento de apoio à elaboração do
> relatório académico (IPCA, "2AI"). Regista, em estilo de relatório, cada
> pergunta/dilema genuíno colocado durante o desenvolvimento e como foi
> respondido — não cobre correções de rotina, só decisões e pontos de
> dúvida reais. Mantido sessão a sessão até ao fim do projeto. Para uma
> versão curta de consulta rápida, ver `RELATORIO_QA_RESUMO.md`.

Formato de cada entrada:

- **Pergunta/dilema**
- **Onde/quando**
- **Forma da resposta**
- **Artifícios/métodos usados**
- **Melhorias feitas / ainda necessárias**

---

## 2026-07-24 — Sessão de arquitetura, redundâncias e coordenação LoRa/BLE

### 1. Porquê duas bases de dados (`storage.py` vs `storage_advanced.py`)?

- **Pergunta/dilema**: o utilizador perguntou porque existem duas bases de
  dados no bridge.
- **Onde/quando**: 2026-07-24, sessão de revisão de arquitetura.
- **Forma da resposta**: explicação de que `storage.py` (SQLite direto) foi
  a implementação original e continua a ser a única lida pelo dashboard;
  `storage_advanced.py` (SQLAlchemy ORM) foi criada depois, com modelo mais
  rico (cifra de campos sensíveis, mais entidades), mas o bridge faz
  dual-write para as duas sem que ninguém leia a segunda no fluxo normal —
  o único leitor é `api.py`, que por sua vez não está ligado ao dashboard.
- **Artifícios/métodos usados**: leitura de `bridge/storage.py`,
  `bridge/storage_advanced.py`, `bridge/orm_persistence.py` e do docstring
  de `orm_persistence.py` (que já documentava o dual-write como
  "transitório").
- **Melhorias feitas / ainda necessárias**: nenhuma alteração de código
  nesta resposta — só diagnóstico. Levou à pergunta seguinte.

### 2. O que é redundante no projeto?

- **Pergunta/dilema**: pedido de uma lista geral de redundâncias
  encontradas no projeto.
- **Onde/quando**: 2026-07-24, imediatamente a seguir à pergunta anterior.
- **Forma da resposta**: lista de 4 pontos — (1) as duas bases de dados,
  (2) `api.py` desligado de tudo, (3) falta de `patient_conditions` (não é
  redundância, é lacuna, mas relacionada com a decisão da BD), (4) RF
  switch partilhado entre LoRa e BLE sem coordenação robusta.
- **Artifícios/métodos usados**: revisão cruzada dos ficheiros já lidos
  nesta sessão e em sessões anteriores (histórico de `PROJECT_STATUS.md`).
- **Melhorias feitas / ainda necessárias**: identificou os alvos concretos
  das duas perguntas seguintes (qual BD remover, o que fazer ao `api.py`).

### 3. Qual base de dados remover — `storage.py` ou `storage_advanced.py`?

- **Pergunta/dilema**: o utilizador pediu para "tirar a base de dados
  desnecessária", sem especificar qual.
- **Onde/quando**: 2026-07-24.
- **Forma da resposta**: em vez de decidir sozinho, apresentei as duas
  opções com o trade-off explícito (remover `storage_advanced.py` é mais
  simples e não quebra nada hoje, mas perde-se o modelo com cifra de campos
  e a base pensada para alergias/doenças; remover `storage.py` é mais
  trabalho — exige migrar `get_history`/`get_daily_trend`/`export_csv` —
  mas evita reconstruir a base mais rica depois). O utilizador escolheu a
  segunda via `AskUserQuestion`.
- **Artifícios/métodos usados**: pergunta direta ao utilizador
  (`AskUserQuestion`) em vez de assumir — justificado por (a) não haver
  repositório git neste diretório, logo apagar ficheiros não é reversível,
  e (b) a minha própria recomendação anterior já apontava para
  `storage_advanced.py` como fonte de verdade a prazo, o que tornava a
  escolha "óbvia" (remover a que não tem consumidor) tecnicamente errada a
  médio prazo.
- **Melhorias feitas / ainda necessárias**: decisão registada; a migração
  de código em si **ainda não foi feita** — ficou explicitamente para
  depois ("a alteração total fica para depois"), e está agora na lista de
  tarefas (ver tarefa 13, secção final deste documento).

### 4. Porque é que `api.py` está desligado, onde devia ligar, e porque não foi ligado ainda?

- **Pergunta/dilema**: o utilizador questionou diretamente porque não
  tinha sido sinalizado como problema concreto a resolver, já sabendo eu
  que era uma redundância.
- **Onde/quando**: 2026-07-24.
- **Forma da resposta**: `api.py` nasceu como protótipo (Prioridade 4,
  2026-07-07) para o dashboard um dia consumir histórico via rede; tem
  autenticação por chave e rate limiting completos, mas nunca foi escrito
  o lado cliente (dashboard) que o chamaria. Onde devia ligar: ao
  dashboard, como via HTTP complementar ao WebSocket — mas só faz sentido
  claro se houver necessidade real de acesso remoto/multi-dispositivo,
  decisão que ainda não foi tomada. Reconheci que já tinha esta informação
  mas não a tinha transformado numa ação concreta na lista de tarefas —
  falha de acompanhamento (follow-through), não de deteção.
- **Artifícios/métodos usados**: releitura do docstring de `bridge/api.py`
  (histórico completo de decisões já documentado no próprio ficheiro:
  API-002/API-003, migração de chave estática para chaves por-utilizador).
- **Melhorias feitas / ainda necessárias**: nenhuma alteração de código;
  adicionada como pergunta em aberto (decisão de arquitetura pendente:
  vale a pena um caminho remoto?) e como ponto a marcar no fluxograma
  refeito.

### 5. Como corrigir a coordenação LoRa/BLE (RF switch partilhado)?

- **Pergunta/dilema**: o utilizador pediu uma sugestão concreta de
  alteração, e perguntou porque tinha sido feito assim e porque ainda não
  tinha sido alterado.
- **Onde/quando**: 2026-07-24.
- **Forma da resposta**: esclareci primeiro que o mecanismo atual
  (`Lora.cpp::begin()`, linhas 64-97) não foi escrito nesta sessão — vem de
  2026-07-03, e já resolveu o bug original (cortar o BLE antes de saber se
  o LoRa tinha sucesso). **Ao reler o ficheiro inteiro para responder,
  encontrei um problema novo, não detetado antes**: depois do
  `digitalWrite(kPinRfSwitch, HIGH)` em caso de sucesso, nenhum código
  alguma vez volta a pôr o pino a `LOW` — nem em `Lora.cpp` nem em
  `Ble.cpp` (que nunca toca neste pino). Ou seja, uma vez que o LoRa
  inicialize com sucesso, o switch de antena fica preso no caminho LoRa
  indefinidamente. Proposta concreta: tratar o RF switch como recurso
  partilhado com devolução simétrica — acrescentar
  `digitalWrite(kPinRfSwitch, LOW)` no fim de `sendTest()` (e de qualquer
  futuro caminho de emergência via LoRa), já que o uso do LoRa é em rajadas
  curtas, não contínuo como o BLE.
- **Artifícios/métodos usados**: leitura completa e literal de
  `src/Lora/Lora.cpp` linha a linha, cruzada com uma pesquisa (`Grep`) por
  todas as ocorrências de `kPinRfSwitch`/`RF_SW` no repositório inteiro
  para confirmar que nenhum outro ficheiro toca nesse pino.
- **Melhorias feitas / ainda necessárias**: **nenhuma alteração de código
  feita ainda** — o problema só foi encontrado ao formular esta resposta,
  não estava sinalizado antes. Adicionado à lista de tarefas como novo
  achado de hoje (não substitui o item 5 do plano existente, que trata só
  de confirmar reprodutibilidade — este é um problema distinto e mais
  específico).

### 6. Pedido de dividir o fluxograma por níveis de arquitetura (um por subsistema)

- **Pergunta/dilema**: o utilizador achou o diagrama único (sistema
  completo) confuso, e pediu diagramas separados por subsistema (bridge,
  ML, firmware, etc.), com fluxos resumidos e também detalhados.
- **Onde/quando**: 2026-07-24.
- **Forma da resposta**: construídos 3 diagramas archify novos —
  `06_Arquitetura_Interna_Bridge_Archify.html` (orquestrador, as duas
  escritas paralelas, os dois loops de retenção, `api.py` isolado),
  `07_Arquitetura_Interna_Firmware_Archify.html` (as 4 tasks FreeRTOS,
  sensores, RF switch partilhado) e `08_Arquitetura_Interna_ML_Archify.html`
  (treino offline vs. inferência em runtime, separados por artefacto). O
  diagrama de dashboard não foi refeito — já existe um nível de detalhe
  equivalente no artefacto `02_Arquitetura_do_Dashboard.html`, guardado em
  sessão anterior, e reconstruí-lo seria trabalho duplicado.
- **Artifícios/métodos usados**: aplicação directa dos "Layout principles"
  da skill archify (uma via principal por diagrama, poucas etiquetas,
  detalhe em cartões), com várias iterações de `validate`/correção de
  sobreposições de rótulos e cruzamentos de linhas até `ok: true` em cada
  um dos 3 ficheiros.
- **Melhorias feitas / ainda necessárias**: os 3 diagramas foram entregues
  e validados sem erros. O diagrama de sistema (nível 1, resumido) ainda
  não foi refeito para corresponder a este novo nível de detalhe — fica
  para quando o utilizador pedir.

### 7. `activity_inference.py` devia estar ligado aos modelos de ML no diagrama do bridge?

- **Pergunta/dilema**: o utilizador reparou que o diagrama do bridge (06)
  mostrava `activity_inference.py` só com uma etiqueta de texto ("carrega
  modelo offline"), sem nenhuma ligação real a um nó de modelo — ao
  contrário do diagrama de ML (08), onde essa relação já estava explícita.
- **Onde/quando**: 2026-07-24, depois da entrega dos 3 diagramas por
  subsistema.
- **Forma da resposta**: reconhecida a inconsistência entre os dois
  diagramas — corrigido o diagrama do bridge acrescentando um nó "Modelo
  treinado", ligado a `activity_inference.py` por uma relação "carrega
  modelo", com nota a remeter para o diagrama de ML (08) para o detalhe
  completo do treino.
- **Artifícios/métodos usados**: edição direta do JSON do diagrama
  (`carrega modelo` como relação nova, nó `ml_model` com sublabel a
  apontar para o diagrama 08), revalidação e nova entrega no mesmo
  ficheiro (sem criar cópia nova).
- **Melhorias feitas / ainda necessárias**: corrigido e entregue. Fica em
  aberto uma decisão futura: se valer a pena, os dois diagramas (06 e 08)
  podiam ligar-se por um link clicável dentro do próprio HTML, em vez de
  só por uma nota de texto — não implementado, por ser uma funcionalidade
  da skill archify que não foi explorada nesta sessão.

### 8. Aparecem dois nós de "storage" no diagrama do bridge — é um erro?

- **Pergunta/dilema**: o utilizador confirmou se ver `storage.py` e
  `storage_advanced.py` como dois nós distintos no mesmo diagrama era um
  erro de construção.
- **Onde/quando**: 2026-07-24.
- **Forma da resposta**: não é erro — é o estado real do código hoje. A
  decisão de migrar para uma única base de dados (pergunta 3) já foi
  tomada, mas o código ainda não foi alterado (item 13 da lista de
  tarefas); o diagrama reflete o código como está, não como vai ficar. As
  etiquetas em cada nó (`"leitura atual (a sair)"` vs. `"decidido: fonte
  única"`) já tentavam comunicar isto.
- **Artifícios/métodos usados**: nenhuma investigação nova — resposta
  direta a partir do estado já conhecido da decisão da pergunta 3.
- **Melhorias feitas / ainda necessárias**: nenhuma alteração ao diagrama
  — considerou-se que as etiquetas existentes já cumprem o objetivo; se o
  utilizador continuar a achar confuso depois da migração de código
  (item 13) ser feita, o diagrama deve ser atualizado nessa altura para
  remover `storage.py` por completo (como já tinha sido feito uma vez no
  diagrama de sistema, item 5, antes de existir o diagrama do bridge).

### 9. "Retenção ORM" é o mesmo que guardar os dados?

- **Pergunta/dilema**: dúvida sobre se o nó "retenção ORM" do diagrama do
  bridge representava a escrita de dados ou outra coisa.
- **Onde/quando**: 2026-07-24.
- **Forma da resposta**: esclarecido que é o oposto — os loops de
  retenção (`retention_sqlite`, `retention_orm`) só **apagam** dados
  antigos (política de retenção/GDPR), nunca gravam. Quem grava são as
  ligações distintas `bridge-storage` (escrita direta) e
  `bridge-orm`→`orm-storage-adv` (dual-write em lote). São dois
  mecanismos completamente separados no código (`purge_old_sensor_records`
  vs. `run_retention_cleanup`), cada um servindo uma das duas bases de
  dados.
- **Artifícios/métodos usados**: releitura do próprio texto já escrito na
  secção 3 desta explicação de arquitetura (a resposta já lá estava,
  só precisava de ser reformulada de forma mais direta).
- **Melhorias feitas / ainda necessárias**: nenhuma alteração de código
  ou diagrama — só clarificação textual, aqui e na resposta em chat.

### 10. Reutilização de ficheiros entregues, sem criar versões quase iguais

- **Pergunta/dilema**: o utilizador pediu explicitamente para não deixar
  a pasta `Análise/` cheia de "informação quase igual" e para apagar
  versões anteriores dos diagramas.
- **Onde/quando**: 2026-07-24.
- **Forma da resposta**: confirmado que a prática já em curso (entregar
  cada atualização para o mesmo caminho de ficheiro, nunca criar
  `_v2`/`_novo`) já cumpria o pedido — verificado com uma listagem real da
  pasta (`ls`), que confirmou 8 ficheiros, todos com conteúdo distinto
  (nenhum par duplicado ou quase-duplicado). Mantida esta prática para
  todas as entregas seguintes.
- **Artifícios/métodos usados**: listagem direta do sistema de ficheiros
  (`ls -la`) em vez de assumir — verificação factual do estado real da
  pasta antes de responder.
- **Melhorias feitas / ainda necessárias**: nenhuma limpeza foi necessária
  porque não havia duplicados; regra confirmada como prática permanente
  desta sessão em diante.

### 11. O NFC liga-se ao bridge no diagrama do firmware — está certo?

- **Pergunta/dilema**: o utilizador notou que o diagrama do firmware (07)
  mostrava o NFC a enviar dados diretamente para o bridge, e perguntou se
  isso fazia sentido dado o caso de uso definido para o NFC (leitura
  passiva local por um leitor externo).
- **Onde/quando**: 2026-07-25.
- **Forma da resposta**: confirmado que era um erro real de modelação, não
  só falta de detalhe — o caso de uso do NFC (definido em sessão anterior)
  é leitura passiva local: um telemóvel/médico encosta-se ao relógio e lê
  os dados sem BLE nem bridge envolvidos. A única relação real com o
  bridge é a *configuração* do conteúdo (o dashboard define o que a tag
  mostra, e isso desce por BLE até ao firmware) — não a leitura em si.
  Corrigido: substituída a relação `nfc_module → bridge` por duas
  relações distintas — `ble_task → nfc_module` (configuração, desce por
  BLE) e `nfc_module → leitor externo` (leitura, não passa pelo bridge).
- **Artifícios/métodos usados**: reconciliação com a decisão de âmbito do
  NFC já registada em sessão anterior (leitura tipo tag, sem
  autenticação, sem depender do bridge estar por perto) — não foi preciso
  reler código, porque o `Nfc.cpp` ainda é andaime sem lógica real; o erro
  estava na modelação do diagrama, não no código.
- **Melhorias feitas / ainda necessárias**: corrigido e entregue
  (`07_Arquitetura_Interna_Firmware_Archify.html`). Mesma correção também
  detetou dois "becos sem saída" adicionais nos diagramas 05 e 06: o NFC e
  o `notifications.py` tinham entrada mas nenhuma saída visível — também
  corrigidos, acrescentando os nós externos "Leitor NFC" e "Twilio/
  SendGrid" em ambos.

### 12. Faltam diagramas de detalhe para o dashboard e o notifications?

- **Pergunta/dilema**: o utilizador perguntou se, tal como se fez para o
  bridge, o firmware e o ML, não deveriam existir diagramas de detalhe
  para as restantes features (dashboard, notifications).
- **Onde/quando**: 2026-07-25.
- **Forma da resposta**: para o dashboard, não foi criado um novo diagrama
  — já existe um nível de detalhe equivalente (e mais completo, com 5
  separadores interativos) no artefacto `02_Arquitetura_do_Dashboard.html`,
  guardado numa sessão anterior; refazê-lo seria trabalho duplicado (ver
  também pergunta 10, sobre não criar ficheiros quase iguais). Para
  `notifications.py`, foi confirmado que tinha complexidade interna real
  (Twilio + SendGrid + janela de indisponibilidade do cuidador +
  escalonamento com timeout) comparável ao ML — por isso foi criado um
  diagrama novo, `09_Arquitetura_Interna_Notifications_Archify.html`.
- **Artifícios/métodos usados**: leitura do docstring completo de
  `bridge/notifications.py` para confirmar que a complexidade interna
  justificava um diagrama próprio (não foi uma escolha arbitrária — o
  módulo tem de facto um fluxo de decisão com vários passos: notificação
  imediata, verificação de janela de indisponibilidade, armar/cancelar
  escalonamento por `acknowledge()`, e a decisão deliberada e documentada
  de nunca contactar o 112).
- **Melhorias feitas / ainda necessárias**: diagrama 09 entregue, validado
  sem erros. Não foram identificados outros módulos com complexidade
  interna comparável que ainda não tenham diagrama próprio ou cobertura
  equivalente.

---

## 2026-07-25 — Glossário técnico, correção do cron, e pedido de limpeza dos diagramas

### 13. Explicação de Twilio, SendGrid, `orm_persistence.py` vs. retenção ORM, `api.py`, `bleak` e WebSocket

- **Pergunta/dilema**: pedido de explicação (geral + detalhada) de vários
  termos técnicos usados nos diagramas e relatórios anteriores, que
  tinham sido mencionados mas nunca explicados em separado: o que são
  Twilio/SendGrid, a diferença entre `orm_persistence.py` e "retenção
  ORM" (dois nós distintos no diagrama do bridge), o que é `api.py`, o
  que é `bleak`, o que é um WebSocket, e porque é que o dashboard aparece
  ligado tanto ao bridge como (no futuro) à API.
- **Onde/quando**: 2026-07-25.
- **Forma da resposta**: cada termo respondido em dois níveis — uma
  explicação geral (o que é, sem jargão) e uma detalhada (mecanismo
  exato, ficheiros/variáveis envolvidos). Resumo: Twilio (SMS) e
  SendGrid (email) são serviços externos pagos chamados por
  `notifications.py`; `orm_persistence.py` **escreve** dados na base ORM
  em lote, "retenção ORM" **apaga** dados antigos num loop periódico
  totalmente separado; `api.py` é uma segunda via de acesso (REST,
  autenticada) sem consumidor ligado; `bleak` é a biblioteca Python que
  fala BLE com o wearable; WebSocket é uma ligação persistente
  bidirecional, ao contrário de um pedido HTTP normal — é por isso que é
  usada para dados "ao vivo" (streaming), enquanto a API (se algum dia
  for ligada) serviria pedidos pontuais de histórico.
- **Artifícios/métodos usados**: respostas construídas a partir do
  conhecimento já adquirido em sessões anteriores desta conversa (leitura
  de `notifications.py`, `orm_persistence.py`, `api.py`, `ble_bridge.py`)
  — sem necessidade de releitura de código novo, exceto para a correção
  do cron (ver entrada 14).
- **Melhorias feitas / ainda necessárias**: respostas dadas em chat;
  passadas para este ficheiro nesta entrada. Ver secção "Glossário" mais
  abaixo para a versão de referência permanente.

### 14. "O cron não é o processo que cria diariamente novos dados?" — correção de um erro anterior

- **Pergunta/dilema**: o utilizador contestou a afirmação anterior de que
  "não existe cron nenhum no projeto", lembrando que existe um processo
  que gera dados novos todos os dias.
- **Onde/quando**: 2026-07-25.
- **Forma da resposta**: confirmado que o utilizador tinha razão e que a
  resposta anterior estava **errada por generalização** — eu tinha
  verificado apenas a ausência de cron dentro do processo do bridge (o
  que é verdade: a limpeza de dados aí é mesmo só um loop interno) e
  generalizei incorretamente essa conclusão para "o projeto inteiro não
  tem cron". Existe, sim, um cron real: `.github/workflows/demo-data.yml`,
  agendado via GitHub Actions (`cron: "15 4 * * *"`, 04:15 UTC todos os
  dias), que corre `scripts/generate-demo-data.js` para regenerar
  `web/dashboard/demo-data.js` com dados de demonstração simulados e
  determinísticos — nunca toca em dados reais (`bridge/carewear_history.db`),
  regra documentada no próprio ficheiro como "Regra de ouro". O commit só
  acontece se algo mudou de facto (compara linhas alteradas excluindo o
  timestamp).
- **Artifícios/métodos usados**: leitura completa de
  `scripts/generate-demo-data.js` e `.github/workflows/demo-data.yml`
  depois da contestação do utilizador — devia ter sido feita antes de
  afirmar "não há cron", não depois.
- **Melhorias feitas / ainda necessárias**: correção comunicada e
  registada aqui. Fica por investigar a outra automação mencionada no
  comentário do workflow ("rotinas cloud diárias, 05:00 UTC") — não
  aprofundada ainda.

### 15. Pedido de corrigir rotas tortas nos diagramas e mostrar a origem dos dados do QSPI

- **Pergunta/dilema**: o utilizador apontou (com uma captura de ecrã) que
  alguns diagramas archify têm ligações com ângulos desnecessários em vez
  de linhas retas/ortogonais limpas, pediu para aproximar visualmente
  nós que estão relacionados (ex.: o leitor NFC devia ficar perto do nó
  NFC no diagrama 05), e notou que o diagrama do firmware (07) mostra o
  `QSPI Flash` a ser atualizado sem deixar claro que os dados vêm de
  `ppg_task`/`imu_task` através de `storage_task`.
- **Onde/quando**: 2026-07-25.
- **Forma da resposta**: pedido reconhecido como válido — os diagramas já
  passam a validação técnica da skill archify (sem erros de sobreposição
  ou cruzamento), mas isso não garante que a disposição seja a mais
  legível ou que a proximidade espacial reflita a relação lógica entre
  nós. Correção agendada para logo a seguir a esta entrada: reposicionar
  `nfc_reader` perto de `nfc` no diagrama 05, adicionar as ligações em
  falta `ppg_task → storage_task` e `imu_task → storage_task` no diagrama
  07 (para deixar claro que o QSPI só recebe o que essas tasks já
  processaram, não gera dados sozinho), e rever rotas com bends
  desnecessários nos diagramas existentes.
- **Artifícios/métodos usados**: — (ver próxima sessão de trabalho nos
  ficheiros JSON dos diagramas).
- **Melhorias feitas / ainda necessárias**: **ainda não aplicado** no
  momento desta entrada — registado como pedido explícito, a executar a
  seguir.

## Glossário técnico de referência

Termos usados nos diagramas e no resto deste relatório, para consulta
rápida sem precisar de procurar a explicação espalhada pelas entradas
acima:

- **Twilio**: serviço externo pago de envio de SMS, usado por
  `notifications.py` para alertar cuidador/contacto de emergência.
- **SendGrid**: serviço externo pago de envio de email (também da
  Twilio, produto distinto do SMS), usado pelo mesmo módulo.
- **`orm_persistence.py`**: módulo que **escreve** os dados do wearable
  na base ORM (`storage_advanced.py`), em lote (buffer de até 50
  registos ou 1 segundo).
- **"Retenção ORM"**: loop periódico, dentro de `ble_bridge.py`, que
  **apaga** registos antigos da base ORM — não escreve nada, não é o
  mesmo código que `orm_persistence.py`.
- **`api.py`**: servidor REST (FastAPI, porta 8766) que lê
  `storage_advanced.py`, com autenticação por chave — hoje sem nenhum
  consumidor ligado.
- **`bleak`**: biblioteca Python usada por `ble_bridge.py` para falar BLE
  (Bluetooth Low Energy) com o wearable — descoberta, ligação, leitura e
  escrita de características GATT.
- **WebSocket**: ligação de rede persistente e bidirecional (ao
  contrário de um pedido HTTP normal, que abre e fecha a cada troca) —
  usada entre o bridge e o dashboard para dados "ao vivo".
- **Cron**: mecanismo de agendamento periódico. No CareWear existe um
  real (GitHub Actions, `.github/workflows/demo-data.yml`, diário) para
  gerar dados de demonstração — não confundir com os loops internos de
  retenção do bridge, que não são cron (não usam nenhum agendador
  externo, são só `while True` + `asyncio.sleep` dentro do próprio
  processo Python).

---

## Explicação da arquitetura, ponta a ponta (2026-07-24)

> Nota sobre este ficheiro: só regista perguntas/dilemas e a explicação de
> arquitetura que os sustenta. Gestão de trabalho (o que falta fazer) vive
> na lista de tarefas da sessão e em `PROJECT_STATUS.md` — não aqui.

### 1. Wearable (nRF52840 Sense Plus) — firmware C++/PlatformIO, FreeRTOS

O firmware corre 4 tasks paralelas, cada uma com a sua própria stack
dedicada (RAM alocada por task, não partilhada):

- **`ppg_task`**: lê o sensor MAX30105 (fotopletismografia). Faz DUAS
  coisas distintas com o mesmo hardware: (a) `measureSpo2()`, a cada 30s,
  usa os LEDs vermelho+infravermelho e o algoritmo de referência da Maxim
  (`maxim_heart_rate_and_oxygen_saturation()`) para calcular SpO2 **e**
  FC em simultâneo, com deteção real de presença de dedo
  (`FINGER_THRESHOLD`); (b) `processHrSample()`, continuamente, usa só o
  LED verde e um detetor de batimento por cruzamento de zero, com um
  filtro de amplitude mínima (`kMinBeatPeakAmplitude`, acrescentado
  2026-07-22). Há, portanto, DUAS fontes independentes de FC no mesmo
  firmware — a robusta (SpO2, com gate de dedo real) só passou a ser
  publicada a jusante nesta sessão (achado do item "SpO2 já calcula HR").
- **`imu_task`**: lê acelerómetro+giroscópio, calcula deteção de
  inatividade (para saber quando o utente está parado vs. em movimento) e
  deteção de queda livre. Depende de uma calibração inicial
  (`runCalibration()`, `Storage::saveCalibration()`) persistida em flash —
  se essa calibração for gravada com a placa em movimento, fica
  permanentemente errada até ser limpa manualmente (bug já visto e
  corrigido uma vez, 2026-07-22, mas que regrediu).
- **`storage_task`**: grava os dados localmente em flash QSPI
  (`QspiRingBuffer`) sempre que não há ligação BLE ativa — é o que permite
  ao wearable continuar a registar dados mesmo sem o bridge por perto, e
  entregá-los mais tarde quando a ligação for restabelecida.
- **`ble_gatt_dump_task`**: serve os dados acumulados por características
  GATT ao bridge, já cifrados (ver secção 2).

**Antenas**: BLE (2.4 GHz) e LoRa (868 MHz) partilham a mesma trilha de
antena através de um switch RF físico (`kPinRfSwitch`, pino A2) — só um
dos dois pode estar eletricamente ligado à antena a cada instante (ver
pergunta 6 abaixo, sobre porque é que isto exige coordenação explícita em
software, não é uma escolha arbitrária). NFC e GNSS existem como
andaime/planeado, sem implementação funcional ainda.

### 2. Transporte (BLE GATT)

Os dados vão cifrados ponta-a-ponta entre o wearable e o bridge com
AES-CTR: cada pacote tem um nonce próprio, os dados são agrupados
("batched") para caber no MTU negociado da ligação BLE, e a cifra/decifra
usa a mesma chave partilhada (`bridge/device_key.env`, nunca exposta em
texto). A Fase A de segurança BLE (bonding + encriptação nativa do
transporte, além da cifra aplicativa) já está validada em hardware; MITM
(pareamento com confirmação visual) e RPA (endereços aleatórios rotativos)
continuam bloqueados por falta do ecrã OLED físico onde mostrar o PIN de
confirmação.

### 3. Bridge (`bridge/ble_bridge.py`) — o "cérebro" central

É um processo Python (`asyncio`) que corre localmente, liga-se ao
wearable via `bleak` (biblioteca BLE cross-platform), decifra o stream, e
distribui os dados recebidos para vários destinos **dentro do mesmo
processo** — não são serviços separados:

- **`storage.py`** (SQLite direto, sem ORM) — hoje a única fonte lida
  pelo próprio bridge quando o dashboard pede histórico
  (`get_history`/`get_daily_trend`/`export_csv`, servidos via WebSocket).
- **`storage_advanced.py`** (SQLAlchemy ORM, via `orm_persistence.py`) —
  segundo destino de escrita ("dual-write"), com um modelo mais rico
  (cifra de campos sensíveis como NIF/morada, mais entidades:
  `EmergencyAlert`, `AuditLog`, `ActivityWindow`). Foi decidido nesta
  sessão que passa a ser a fonte única — migração ainda por fazer.
- **`notifications.py`** — só é acionado para alertas de emergência reais
  (queda confirmada, inatividade prolongada), envia SMS/email.
- **Limpeza periódica de dados (retenção/GDPR)**: NÃO existe cron nenhum
  do sistema operativo. É um loop assíncrono dentro do próprio processo
  do bridge (`while True` + `asyncio.sleep`, dois loops distintos — um
  para `storage.py`, outro para o ORM). Consequência relevante: se o
  processo do bridge cair, a limpeza para com ele — não há rede de
  segurança externa a garantir que os dados antigos continuam a ser
  apagados.

**`api.py`** corre à parte (processo FastAPI/uvicorn distinto, porta
8766), lê só de `storage_advanced.py`, e tem autenticação por chave
própria — mas não tem nenhum consumidor ligado (ver pergunta 4 desta
sessão).

### 4. Pipeline de Machine Learning

Duas fases completamente separadas, que só se tocam através de um
ficheiro de artefacto — nunca partilham processo nem código em runtime:

- **Treino (offline, manual)**: `ml/train_activity_classifier.py` corre
  fora do bridge, à mão, quando alguém decide treinar/retreinar. Lê
  **exclusivamente** `data/synthetic_routine_dataset.csv` — dados
  sintéticos, gerados artificialmente, nunca uma captura real de um
  utente. Produz um artefacto de modelo (classificador XGBoost) gravado
  em disco. Não há, hoje, nenhum agendamento nem gatilho automático para
  isto correr — é sempre um comando manual de um developer.
- **Inferência (runtime, dentro do processo do bridge)**:
  `activity_inference.py` carrega esse artefacto **uma vez**, ao arrancar,
  e a partir daí classifica cada novo conjunto de features (extraídas do
  stream já decifrado de acelerómetro/giroscópio) numa categoria de
  atividade (parado, a andar, a comer, etc.), publicada ao dashboard via
  `activity_duration_flag`. Este processo nunca escreve de volta no
  artefacto do modelo — é só leitura/uso.
- **Correções do cuidador**: quando um cuidador corrige manualmente a
  categoria sugerida pela IA (funcionalidade já implementada,
  `activity_corrections` em `storage.py`), essa correção fica gravada na
  base de dados, mas **hoje não alimenta nada** — não há nenhum código que
  leia `activity_corrections` e a use como novo dado de treino. É a
  lacuna central identificada nesta área do projeto: o único modelo que
  existe nunca viu uma atividade real de um utilizador, e o único sinal
  real que já existe (as correções) está a ser descartado.
- **Consequência prática desta separação**: um erro de classificação
  observado no dashboard hoje não pode ser corrigido "a quente" — exige
  sempre um novo treino manual, com novos dados, e um novo carregamento
  do artefacto (reiniciar o processo do bridge, ou implementar um
  mecanismo de recarregamento a quente, que também não existe hoje).

Ver `Análise/08_Arquitetura_Interna_ML_Archify.html` para o diagrama
completo desta separação treino/inferência, incluindo a ligação (hoje
inexistente, a tracejado no diagrama) entre `activity_corrections` e o
script de treino.

### 5. Dashboard (`web/dashboard/index.html`)

Página única sem framework, liga por WebSocket ao bridge
(`ws://localhost:8765`) — **não** à base de dados diretamente. Recebe
mensagens JSON (`kind: sensor_data`, `activity_duration_flag`, etc.) e
envia comandos limitados (`force_reading`, `acknowledge_alert`). Este
canal WebSocket não tem qualquer autenticação — decisão de risco aceite e
já documentada, porque hoje o acesso é sempre local (mesma máquina).

### Fluxo essencial, resumido

**Wearable (4 tasks paralelas) → BLE cifrado (AES-CTR) → Bridge (decifra,
grava em duas BDs, classifica atividade, dispara notificações de
emergência, limpa dados antigos via loop interno) → WebSocket sem
autenticação → Dashboard.** Em paralelo, sem estarem integrados: uma API
REST autenticada (`api.py`) sem consumidor, e dois rádios (BLE/LoRa) que
partilham fisicamente uma única antena e por isso não podem transmitir em
simultâneo.

### 6. Porque é que BLE e LoRa precisam de coordenação explícita?

Não é uma escolha de software — é uma limitação física da placa. O switch
RF (`kPinRfSwitch`) decide qual dos dois rádios está ligado
eletricamente à **única** trilha de antena partilhada: o caminho de
2.4 GHz (BLE) ou o de 868 MHz (LoRa). As redes de adaptação de impedância
de cada caminho são específicas da sua frequência — não é possível ligar
os dois rádios à mesma antena ao mesmo tempo sem desadaptação de
impedância e perda/degradação de sinal em ambos, potencialmente com
stress no amplificador de potência do lado não selecionado. Foi
precisamente isto que se observou no bug de 2026-07-03: ativar o LoRa
cortava fisicamente a antena do BLE, mesmo com a pilha BLE a continuar a
"pensar" que estava a anunciar-se normalmente. Por isso a arbitragem tem
de ser feita explicitamente em software — hoje só existe a entrada (LoRa
liga-se com sucesso → assume a antena) mas não a saída (nada devolve o
caminho ao BLE depois).

## 2026-07-25 — Correção de diagramas: storage.py, g_latest e RF switch

### 15. `storage.py` continuava no diagrama 6 depois de decidido remover

- **Pergunta/dilema**: o utilizador assinalou (mais de uma vez, ao longo
  de várias mensagens) que já tinha pedido para tirar `storage.py` do
  diagrama, e que isso ainda não tinha acontecido na análise 6
  (`06_Arquitetura_Interna_Bridge_Archify.html`).
- **Onde/quando**: 2026-07-25. A decisão em si (manter
  `storage_advanced.py`, sair `storage.py`) já tinha sido tomada em
  2026-07-24 (ver entrada 3), mas só a fase "não alterar o fluxograma
  ainda" tinha sido pedida nessa altura — o pedido para atualizar o
  diagrama ficou implícito e não foi executado a tempo.
- **Forma da resposta**: reconhecimento direto do atraso, sem
  justificação — e correção imediata do ficheiro `carewear.
  bridge-internal.architecture.json`: nó `storage` removido, nó
  `retention_sqlite` removido (retenção SQLite deixa de fazer sentido sem
  a base SQLite direta), ligação `ble_bridge → orm_persistence` passa a
  única escrita (`variant: emphasis`, antes era `dual-write secundário`
  a tracejado), cartão de resumo atualizado com uma entrada "Já resolvido
  (2026-07-25)".
- **Artifícios/métodos usados**: edição direta do JSON-fonte do archify,
  `node bin/archify.mjs validate` (1 erro de sobreposição de rótulo,
  corrigido com `labelDy: 24`) e `deliver` para gerar o HTML final.
- **Melhorias feitas / ainda necessárias**: o **diagrama** já reflete a
  decisão. O **código** ainda não — o dashboard continua a ler de
  `storage.py` na prática; essa migração continua como item 13 da lista
  de tarefas, sem alteração nesta sessão.

### 16. O que é `g_latest`?

- **Pergunta/dilema**: pergunta direta sobre o significado do nó
  `g_latest` introduzido no redesenho da análise 7
  (firmware interno).
- **Onde/quando**: 2026-07-25, ao ler a análise 7.
- **Forma da resposta**: `g_latest` é uma struct C++ em `main.cpp` — não
  um ficheiro nem um processo — que guarda o **último valor conhecido**
  de cada leitura do wearable (FC, SpO2, inatividade, deteção de queda).
  `ppg_task` e `imu_task` escrevem lá dentro dentro de uma secção crítica
  (interrupções suspensas por instantes, para não haver leitura a meio de
  uma escrita); `storage_task` e `ble_gatt_dump_task` só leem. Modela o
  padrão real do firmware — várias tasks produtoras, um único ponto de
  verdade, várias tasks consumidoras — em vez das ligações task-a-task
  ad-hoc do desenho anterior, que o utilizador já tinha identificado como
  visualmente confuso.
- **Artifícios/métodos usados**: nenhuma pesquisa nova — resposta a
  partir do próprio JSON da análise 7, já construído nesta sessão a
  partir da leitura de `main.cpp`.
- **Melhorias feitas / ainda necessárias**: nenhuma alteração — resposta
  apenas explicativa.

### 17. Porque é que o RF switch só aparece ligado ao LoRa?

- **Pergunta/dilema**: o utilizador reparou que, olhando para os
  diagramas, o RF switch parecia só relacionar-se com o LoRa.
- **Onde/quando**: 2026-07-25.
- **Forma da resposta**: inconsistência real entre dois diagramas da
  mesma sessão, não um erro conceptual único. Na análise 7 (firmware
  interno) o nó `rf_switch` liga-se aos DOIS rádios — `rf-ble` ("caminho
  por omissão") e `rf-lora` ("liga após sucesso, nunca desliga"). Mas na
  análise 5 (sistema completo) não existe nó `rf_switch` — a partilha de
  antena só é mencionada no rótulo da ligação `wearable → lora`
  ("RF switch partilhado c/ BLE"), sem qualquer marca equivalente na
  ligação `wearable → bridge` (que é, na prática, o caminho BLE). Quem lê
  só a análise 5 fica com a impressão de que o RF switch é uma coisa do
  LoRa, quando na realidade o caminho por omissão é o BLE e é o LoRa que
  rouba a antena (e, por bug, nunca a devolve).
- **Artifícios/métodos usados**: comparação direta dos dois ficheiros JSON-
  fonte (`carewear.architecture.json` vs
  `carewear.firmware-internal.architecture.json`), sem necessidade de
  reler código-fonte (já lido nas sessões de 2026-07-24/25).
- **Melhorias feitas / ainda necessárias**: corrigido. Nó `rf_switch`
  adicionado à análise 5, com as duas ligações (`wearable→rf_switch`,
  "BLE por omissão"; `rf_switch→lora`, "liga após sucesso, nunca
  desliga"), espelhando a análise 7. Validado (0 erros) e entregue.

### 18. O que faz o RF switch, em concreto?

- **Pergunta/dilema**: pergunta direta sobre a função do RF switch, depois
  de ele ter sido adicionado à análise 5.
- **Onde/quando**: 2026-07-25.
- **Forma da resposta**: é um componente físico (pino `A2`,
  `kPinRfSwitch`) que liga eletricamente **um** dos dois rádios (BLE
  2.4 GHz ou LoRa 868 MHz) à antena única partilhada pela placa — não há
  duas antenas, e cada rádio tem a sua própria rede de adaptação de
  impedância afinada à sua frequência, pelo que ligar os dois ao mesmo
  tempo degrada o sinal de ambos. Estado por omissão: BLE. Quando o LoRa
  transmite com sucesso, o switch muda para o lado do LoRa — e, por bug
  ainda não corrigido (item 14 da lista de tarefas), nada o devolve ao
  BLE depois.
- **Artifícios/métodos usados**: nenhuma pesquisa nova — resposta a
  partir do conhecimento já reunido em sessões anteriores sobre
  `src/Lora/Lora.cpp` e o bug de 2026-07-03.
- **Melhorias feitas / ainda necessárias**: nenhuma alteração de código
  — resposta apenas explicativa.

### 19. Porque é que `storage_advanced.py` e o bridge estão ligados ao dashboard?

- **Pergunta/dilema**: na análise 5, o dashboard recebe duas ligações
  diferentes — uma do `bridge` e uma de `storage_advanced.py` — o
  utilizador perguntou porquê.
- **Onde/quando**: 2026-07-25.
- **Forma da resposta**: são dois tipos de dados com dois caminhos
  distintos, não uma duplicação acidental. `bridge → dashboard`
  (WebSocket, `:8765`) é o canal de **dados em tempo real** — cada leitura
  nova do wearable é reencaminhada assim que chega, sem passar por
  nenhuma base de dados nesse caminho. `storage_advanced.py → dashboard`
  é o canal de **dados históricos** — quando o dashboard mostra
  tendências, histórico ou exporta CSV, faz um pedido pontual à base de
  dados, não ao bridge. É a mesma distinção que já existia no código
  antes desta sessão entre "stream" e "consulta", só que agora
  representada explicitamente no diagrama.
- **Artifícios/métodos usados**: leitura das ligações já existentes no
  JSON-fonte da análise 5 (`bridge-dashboard` vs
  `storage-adv-dashboard`), sem necessidade de reler código-fonte.
- **Melhorias feitas / ainda necessárias**: identificado um ponto de
  arquitetura pouco elegante, já achado antes mas relevante aqui — é
  precisamente esta separação em dois caminhos para o dashboard que
  `api.py` (REST autenticado, hoje sem consumidor) parecia destinada a
  substituir para o caminho histórico; não é ligada. Ver item 15 da lista
  de tarefas.

### 20. O GNSS precisaria de partilhar o RF switch com BLE/LoRa?

- **Pergunta/dilema**: o utilizador sabia que, no diagrama, a antena GNSS
  não está ligada ao `rf_switch`, e quis perceber se isso seria
  tecnicamente necessário na realidade (ou se é uma omissão a corrigir).
- **Onde/quando**: 2026-07-25.
- **Forma da resposta**: não seria necessário, por duas razões
  independentes. (1) O GNSS liga-se por **I2C**, não por uma linha de RF
  — é um módulo separado (CAM-M8Q) com antena própria, não um segundo
  caminho a competir pela mesma trilha de 50Ω que BLE/LoRa partilham; e é
  **só recetor** (nunca transmite), pelo que não há o conflito de "dois
  transmissores, uma antena" que motiva o switch. (2) A frequência é
  completamente diferente — GNSS L1 é ~1.575 GHz, uma banda de antena
  fisicamente distinta (tipicamente antena patch com polarização
  circular) de 2.4 GHz (BLE) ou 868 MHz (LoRa); mesmo que se quisesse
  partilhar hardware, precisaria de um switch de 3 vias afinado às três
  bandas, não de "mais uma entrada" no switch atual de 2 vias.
  Ressalva dada: mesmo sem conflito de antena, pode existir
  **dessensibilização** — o transmissor LoRa/BLE perto do recetor GNSS
  pode reduzir a sensibilidade dele durante uma transmissão; isto é um
  problema de interferência elétrica entre componentes vizinhos na
  placa, não de arbitragem de switch, e só se confirma com hardware real.
- **Artifícios/métodos usados**: `Grep` a `PROJECT_STATUS.md` por
  "GNSS"/"CAM-M8Q"/"antena", confirmando o mapeamento por I2C
  (`PROJECT_STATUS.md:1211-1215`) e a restrição de trilho de 50Ω do RF
  switch BLE/LoRa (`PROJECT_STATUS.md:1264-1267`); resto é raciocínio de
  RF geral (bandas de frequência, TX vs. RX), não pesquisa nova.
- **Melhorias feitas / ainda necessárias**: nenhuma alteração de código
  ou diagrama — o desenho atual (GNSS sem ligação ao `rf_switch`) está
  correto e não precisa de mudar. O teste de coexistência de antenas
  (item 7 da lista de tarefas) continua relevante, mas por causa de
  dessensibilização, não de partilha de switch.

### 21. Porque há duas fontes de frequência cardíaca (FC)?

- **Pergunta/dilema**: pergunta direta sobre a razão de existirem dois
  cálculos separados de FC no firmware.
- **Onde/quando**: 2026-07-26.
- **Forma da resposta**: são duas rotinas distintas dentro de `Ppg.cpp`,
  nascidas em alturas diferentes e nunca unificadas. (1) `measureSpo2()`,
  a cada ~30s, usa o algoritmo de referência Maxim
  (`maxim_heart_rate_and_oxygen_saturation`), que devolve SEMPRE SpO2 e
  FC juntos, com deteção real de dedo presente (`FINGER_THRESHOLD`) —
  robusta, mas de baixa frequência. (2) `processHrSample()`, streaming
  contínuo a cada ~10ms, é uma pipeline caseira criada para preencher os
  intervalos entre ciclos de SpO2 com uma leitura "ao vivo" — mas nasceu
  sem verificação de dedo presente (bug ainda pendente, item 2 da lista
  de tarefas: sobrescreve `finger_present` com `true` fixo). Achado já
  registado em sessão anterior e corrigido nesta: a FC calculada dentro
  de `measureSpo2()` estava a ser computada e imediatamente descartada
  (nunca escrita em `g_latest`) — já publicada (`Ppg.cpp:667`, item 1,
  concluído).
- **Artifícios/métodos usados**: nenhuma pesquisa nova — resposta a
  partir da análise de `Ppg.cpp` já feita em sessões anteriores
  (documentada em `PROJECT_STATUS.md`, narrativa cronológica de bugs de
  FC/oximetria).
- **Melhorias feitas / ainda necessárias**: a duplicação em si não é
  tratada como bug a remover — as duas fontes servem propósitos
  diferentes (precisão vs. frequência de atualização) e não há decisão
  tomada de as fundir. O que falta é só corrigir o gate de dedo em falta
  na fonte contínua (item 2, pendente).

### 22. A antena BLE partilha mesmo o RF switch com o LoRa, ou já vem incorporada na XIAO?

- **Pergunta/dilema**: o utilizador contestou a premissa usada em várias
  sessões anteriores (e nesta) de que a antena BLE partilha fisicamente
  um RF switch com a antena LoRa — apontando que é uma placa XIAO, e que
  a antena BLE já vem incorporada no próprio módulo nRF52840 Sense Plus,
  não precisando de switch nenhum para ser partilhada.
- **Onde/quando**: 2026-07-26.
- **Forma da resposta**: reconhecida como um ponto tecnicamente válido
  que obriga a rever uma premissa dada como "confirmada" há várias
  sessões. Ao reler `PROJECT_STATUS.md:1178-1182`, o pino `RF_SW`
  aparece descrito dentro do **pinout do próprio módulo Wio-SX1262**
  (LoRa) — ao lado de `NSS`, `DIO1`, `BUSY` — o que é consistente com ser
  um switch **interno ao módulo LoRa** (comum em chips SX126x, para
  seleção TX/RX ou de amplificador de potência), sem relação com a
  antena BLE da XIAO. A frase "antenas partilhadas por RF switch
  (BLE+LoRa)" registada em `PROJECT_STATUS.md:1264-1267` pode ter sido
  uma inferência de sessão anterior, não uma leitura literal do
  esquemático a confirmar que a trilha BLE passa por ali. Isto também
  reabre a causa raiz do bug de 2026-07-03 (BLE a apagar-se quando o
  LoRa arrancava) — se a antena BLE é mesmo independente, a explicação
  "RF switch cortou fisicamente a antena BLE" não pode estar certa;
  candidatos alternativos: pico de consumo/queda de tensão ao
  inicializar o SX1262, contenção no barramento SPI partilhado, ou
  colisão de pino por acaso do layout.
- **Artifícios/métodos usados**: releitura atenta de `PROJECT_STATUS.md`
  (secções "Descobertas do esquemático real" e "Riscos/bloqueios
  ativos"), sem acesso direto ao PDF do esquemático nesta sessão (não
  está no repositório).
- **Melhorias feitas / ainda necessárias**: **por decisão explícita do
  utilizador, nenhuma alteração feita hoje** (nem diagramas 05/07, nem
  texto dos relatórios de arquitetura, nem a explicação do bug de
  2026-07-03) — só esta entrada de registo. Fica pendente: confirmar com
  o utilizador (ele tem o esquemático) se `RF_SW`/pino `A2` é mesmo
  interno ao módulo LoRa e nunca toca na antena BLE da XIAO; se
  confirmado, corrigir os diagramas 05/07, a explicação do bug de
  2026-07-03, e investigar a causa real da queda de BLE observada nessa
  altura. Adicionado como novo item à lista de tarefas.

## 2026-07-26 — Migração real: storage.py removido, storage_advanced.py como fonte única

### 23. Executar a migração de `storage.py` para `storage_advanced.py` (item 13, código real)

- **Pergunta/dilema**: pedido direto e sem ambiguidade do utilizador —
  "Quero que storage deixe de ser a minha base de dados principal e
  quero que passe a ser storage_advanced." Ao contrário de 2026-07-24
  (só a decisão) e 2026-07-25 (só o diagrama), desta vez o pedido era
  para mudar o código a sério.
- **Onde/quando**: 2026-07-26, sessão dedicada a esta migração
  (interrompida a meio por uma ativação do modo de planeamento, retomada
  a partir do plano escrito nessa pausa).
- **Forma da resposta**: migração de código real em 4 ficheiros do
  bridge, em duas fases (código + testes/limpeza), cada uma com o seu
  commit:
  1. `storage_advanced.py` ganhou os modelos `Setting` e
     `ActivityCorrection`, e as funções `get_records_since`,
     `count_records`, `get_daily_summary`, `export_records_csv`,
     `get_retention_days`/`set_retention_days`,
     `insert_activity_correction` — com os MESMOS nomes de campo que
     `storage.py` devolvia (`ax`/`ay`/`az`, não `accel_x`/`accel_y`/
     `accel_z`), para o dashboard não precisar de nenhuma alteração de
     código (o formato na rede não mudou, só a fonte).
  2. `orm_persistence.py` deixou de se descrever como "dual-write
     transitório" e passou a expor `get_history`/`get_daily_trend`/
     `export_csv`/`get_retention_days`/`set_retention_days`/
     `insert_activity_correction` — com uma diferença deliberada de
     comportamento: os métodos de LEITURA agora lançam `RuntimeError`
     explícito se a persistência estiver desativada, em vez de degradar
     em silêncio como os de escrita (já não há `storage.py` a responder
     no lugar).
  3. `ble_bridge.py`: removido `import storage` e
     `self.db = storage.init_db()`; todos os callbacks de escrita
     (`_on_dump_data`/`_on_emergency_alert`) e todos os comandos do
     dashboard (`get_history`, `get_daily_trend`, `export_csv`,
     `get_retention_days`, `set_retention_days`, `correct_activity`)
     migrados para `self.orm`, cada um com um `if not self.orm:`
     explícito antes de tentar ler/escrever.
  4. `requirements.txt` (o mínimo, usado por `start_carewear.ps1/.bat`)
     ganhou `sqlalchemy`/`argon2-cffi` — descoberta feita durante a
     migração: essas dependências só estavam em `requirements_db.txt`
     (opcional) porque antes eram só para o dual-write; agora que
     `storage_advanced.py` é obrigatório, sem isto o lançador de um
     clique arrancaria sem persistência nenhuma.
- **Erro cometido e corrigido durante esta tarefa**: ao commitar a
  primeira fase, um `git commit` sem restringir ficheiros apanhou também
  as remoções do vexp que já estavam staged de uma sessão anterior,
  criando um commit misto sem isso estar descrito na mensagem. Detetado
  de imediato, corrigido com `git reset --soft HEAD^` (seguro, commit
  ainda não tinha sido feito push) e recommitado só com os ficheiros
  certos via `git commit -- <ficheiros>`.
- **Artifícios/métodos usados**: leitura integral de `storage.py`,
  `storage_advanced.py`, `orm_persistence.py` e das secções relevantes
  de `ble_bridge.py` antes de qualquer edição; `grep` ao repositório
  inteiro para mapear todos os pontos de uso de `storage.py` (incluindo
  testes) antes de remover; suite `pytest` corrida depois de cada fase.
- **Melhorias feitas / ainda necessárias**: **feito por completo** —
  `bridge/storage.py` removido do repositório; suite de testes completa
  a passar (141 passed); diagramas 05 e 06 atualizados para refletir o
  estado real (incluindo a correção de uma ligação `storage_adv →
  dashboard` que o diagrama 05 desenhava como planeada mas que nunca
  correspondeu à implementação real — o histórico continua a passar
  pelo `bridge`, não por uma ligação direta à base de dados);
  `PROJECT_STATUS.md` com entrada datada; relatório técnico do módulo
  removido escrito antes da remoção
  (`Análise/11_Relatorio_Tecnico_Storage_Legado.html`, a pedido
  explícito do utilizador). Item 13 da lista de tarefas geral marcado
  como concluído.

### 24. Achado durante a migração: duas funções de `storage.py` eram código morto

- **Pergunta/dilema**: não foi uma pergunta do utilizador — um achado
  meu durante a migração, registado por transparência (a instrução
  geral desta sessão é registar tudo o que for relevante, não só
  perguntas explícitas).
- **Onde/quando**: 2026-07-26, durante o mapeamento de usos de
  `storage.py` antes da migração.
- **Forma da resposta**: `get_recent_emergency_alerts()` e
  `export_emergency_alerts_csv()` estavam definidas em `storage.py` mas
  nunca eram chamadas por nenhum outro ficheiro do repositório
  (confirmado por pesquisa ao código inteiro, não só ao bridge) — só
  apareciam documentadas em `SECURITY_STATUS.md`. Decisão: não portar
  estas duas para `storage_advanced.py`.
- **Artifícios/métodos usados**: `Grep` ao repositório inteiro pelos
  nomes das duas funções.
- **Melhorias feitas / ainda necessárias**: nenhuma migração feita para
  estas duas (decisão consciente, documentada no relatório técnico de
  `storage.py`). Se um dia for preciso mostrar/exportar histórico de
  emergências no dashboard, é trabalho novo sobre
  `storage_advanced.py`, não uma migração em falta.

## 2026-07-30 — Reescrita da arquitetura do zero, como exercício de aprendizagem

### 25. Pedido de reescrever a arquitetura do zero (firmware → bridge → dashboard)

- **Pergunta/dilema**: depois de terminada a migração de storage, o
  utilizador pediu para "refazer este trabalho do zero" — pedido
  inicialmente ambíguo (não estava claro se se referia aos diagramas,
  aos relatórios Q&A, à migração de storage, ou a outra coisa).
  Esclarecido em duas mensagens seguintes: o âmbito é a **arquitetura
  inteira** ("Começar pelo firmware, seguir para o bridge e dashboard"),
  e o motivo explícito é **"para aprender a fazer sozinho"** — não
  insatisfação com o código atual, que continua a passar 141 testes.
- **Onde/quando**: 2026-07-30.
- **Forma da resposta**: dado o âmbito genuinamente ambíguo e o risco de
  descartar trabalho concluído sem necessidade, usada uma pergunta de
  escolha múltipla para esclarecer o âmbito antes de avançar. Depois de
  esclarecido, proposto um roteiro faseado para o firmware — IMU → PPG →
  storage no dispositivo → BLE → RF switch/LoRa → GNSS/NFC (por esta
  ordem, aplicando desde o início as lições já aprendidas: atraso de
  assentamento do IMU, `sampleAverage` do PPG, publicar as duas fontes
  de FC, RF switch corrigido por desenho) — seguido de bridge e depois
  dashboard. Recomendado explicitamente **não apagar** o código atual:
  manter o branch `main` intacto como rede de segurança e trabalhar num
  branch novo (`rewrite-v2`) — proposta feita, ainda **não confirmada**
  pelo utilizador.
- **Artifícios/métodos usados**: `AskUserQuestion` para esclarecer o
  âmbito antes de assumir; `git branch --show-current`/`git status`/
  `git log --oneline -5` para confirmar o estado real do repositório
  (branch, working tree, commits) antes de propor uma estratégia de
  branch, em vez de assumir; releitura do histórico de bugs documentado
  em `PROJECT_STATUS.md` para construir o roteiro com base nas
  armadilhas já conhecidas (não repetir a mesma depuração duas vezes).
- **Melhorias feitas / ainda necessárias**: nenhuma alteração de código
  feita — só planeamento e um documento de orientação (ver entrada 26).
  Pendente: confirmação da estratégia de branch pelo utilizador; decisão
  sobre se "do zero" inclui re-derivar algoritmos (ex. lógica do
  MAX30105) ou só reestruturar a arquitetura reutilizando chamadas de
  baixo nível já corretas; a Fase 0 (esqueleto do firmware) foi entregue
  como prompt de orientação, ainda por executar pelo utilizador.

### 26. Reformulação do modo de ensino: mentor crítico, orientação em formato de prompt, sem executar pelo utilizador

- **Pergunta/dilema**: o utilizador reenviou e expandiu substancialmente
  a instrução de modo pedagógico já dada em 2026-07-19 (ver
  [[feedback_pedagogical_teaching_mode]] na memória), agora como um
  "prompt" de sistema completo e explícito (papel, missão, método de
  ensino, pensamento crítico, investigação científica, engenharia,
  gestão de estudo, rigor, comunicação), pedindo que fosse "fornecido em
  formato de prompt". Esclarecido a seguir, em duas interjeições: (1) o
  âmbito da reescrita é a arquitetura inteira, para aprender sozinha; (2)
  "o suposto não é seres tu a fazeres, mas sim dizer-me o que fazer e
  como, mas em formato de prompt" — ou seja, o papel do assistente na
  reescrita é orientar, nunca implementar.
- **Onde/quando**: 2026-07-30, imediatamente a seguir ao pedido de
  reescrita do zero.
- **Forma da resposta**: reformulado o texto do utilizador num documento
  markdown limpo e estruturado, sem alterar o conteúdo pedido, só a
  organização (papel, missão, método de ensino por primeiros princípios,
  pensamento crítico/debate, aprendizagem ativa, investigação científica,
  programação/engenharia, gestão de estudo e memorização, avaliação
  contínua, rigor, comunicação, objetivo final). Como demonstração
  concreta do modo pedido, produzido um segundo documento no mesmo
  formato — um prompt de orientação para a Fase 0 do firmware (objetivo,
  perguntas a responder antes de começar, passos, pistas se encravar,
  critério de sucesso, e um pedido explícito de revisão antes de avançar
  para a fase seguinte) — sem escrever nenhum código da reescrita em si.
- **Artifícios/métodos usados**: leitura da memória persistente existente
  (`feedback_pedagogical_teaching_mode.md`) antes de expandir, para não
  duplicar nem contradizer a versão anterior; atualização dessa memória
  com a versão 2026-07-30 (mais estrita, não mais permissiva) e criação
  de uma nova memória de projeto (`project_carewear_rewrite.md`) para que
  este modo de operação (guiar, não implementar) persista em sessões
  futuras sem precisar de ser repetido.
- **Melhorias feitas / ainda necessárias**: modo de ensino e formato de
  entrega documentados e guardados em memória persistente, a reger tanto
  explicações como a reescrita da arquitetura. Pendente: aguardar o
  utilizador trazer o resultado da Fase 0 (o `main.cpp` novo e as
  respostas às 3 perguntas orientadoras) antes de fornecer o prompt da
  Fase 1 (IMU) — por desenho, o assistente não avança essa fase sozinho.

### 27. "Não existem diferenças de programação entre Seeed e Adafruit nRF52" — premissa incompleta, corrigida com evidência do toolchain instalado

- **Pergunta/dilema**: durante a Fase 0 da reescrita (que pede
  explicitamente para identificar o framework em uso), o utilizador
  afirmou como assumida: "Não existe diferenças a nível de programação
  entre um seed e adafruit nrf52, pois não?"
- **Onde/quando**: 2026-07-30, resposta parcial à pergunta 1 do prompt
  de orientação da Fase 0.
- **Forma da resposta**: afirmação parcialmente correta, corrigida em
  vez de confirmada às cegas (modo de pensamento crítico pedido pelo
  utilizador). Confirmado no `board.json` real da placa
  (`.platformio/platforms/SeeedStudio/boards/seeed-xiao-afruitnrf52-
  nrf52840-sense-plus.json`) que `"bsp": {"name": "adafruit"}` — a
  Seeed usa mesmo o core Arduino nRF52 da Adafruit, não um core próprio;
  a API de programação (`digitalWrite`, `Wire`, `SPI`, Bluefruit BLE,
  FreeRTOS) é portanto idêntica. Mas o *variant* (mapeamento de pinos +
  periféricos da placa) não é partilhado: comparação direta dos
  ficheiros `variant.h` instalados mostrou `PIN_A2 = 2` na XIAO Sense
  Plus vs. `PIN_A2 = 16` na Adafruit Feather nRF52840 Sense — o mesmo
  símbolo `A2` resolve para um pino físico diferente consoante a placa.
  Adicionalmente, `platformio.ini` traz `Seeed Arduino LSM6DS3` como
  dependência, uma biblioteca só relevante por causa do IMU específico
  soldado na XIAO Sense Plus (não existe numa Adafruit Feather genérica).
  Conclusão comunicada: API de programação = igual; pinos/periféricos da
  placa = diferentes, mesmo com o mesmo core por baixo.
- **Artifícios/métodos usados**: leitura direta do `board.json` da placa
  no pacote `platform-seeedboards` instalado localmente; comparação lado
  a lado dos ficheiros `variant.h` da XIAO Sense Plus e da Adafruit
  Feather nRF52840 Sense (ambos presentes no `.platformio/packages/`
  local); inspeção do `lib_deps` do `platformio.ini` do projeto.
- **Melhorias feitas / ainda necessárias**: nenhuma alteração de código
  — só esclarecimento conceptual, ligado de volta ao uso real de
  `kPinRfSwitch = A2` em `Lora.cpp`, como aviso para a Fase 5 do roteiro
  de reescrita (não copiar pinos de exemplos escritos para outra placa
  sem verificar o `variant.h` real).

## 2026-07-31 — Correção do RF switch e da calibração do IMU (multi-agente)

### 28. Correção real de dois bugs conhecidos (RF switch + delay da calibração do IMU), e achado novo sobre `sendTest()`

- **Pergunta/dilema**: depois de decidido (a pedido do utilizador) que a
  correção destes dois bugs de `main` tinha mais prioridade do que o
  planeamento de tarefas agendadas, e sem placa ligada para testar, foi
  usado um workflow multi-agente (Fable 5 planeia e valida, Sonnet
  implementa — 6 agentes, um par planear/implementar/validar por bug) a
  pedido explícito do utilizador ("Faz multi-agentes"). O agente de
  validação do RF switch encontrou um problema novo, não pedido
  explicitamente: `sendTest()` só sabe devolver o pino a BLE (LOW) no
  fim, mas nunca o volta a pôr em LoRa (HIGH) antes de transmitir — só a
  função `begin()` faz isso, e só uma vez. Ou seja, uma segunda chamada a
  `sendTest()` "teria sucesso" no código de retorno do RadioLib mas
  transmitiria com a antena já roteada para BLE, sem qualquer erro
  reportado.
- **Onde/quando**: 2026-07-31.
- **Forma da resposta**: aplicadas as duas correções pedidas:
  `Lora.cpp:118` — `digitalWrite(kPinRfSwitch, LOW)` movido para logo a
  seguir a `s_radio.transmit()`, antes de qualquer verificação de
  sucesso/falha (devolve sempre a BLE, nos dois caminhos);
  `Imu.cpp:517` — `delay(2000)` → `delay(3000)`, com o comentário de
  documentação da função (linha 491, "espera 2 s") também corrigido para
  não ficar desatualizado. Ambas verificadas por leitura direta do
  ficheiro real (pelo agente de validação E por mim, antes de reportar).
  O achado novo (`sendTest()` não repõe HIGH) foi apresentado ao
  utilizador como uma decisão em aberto, não corrigido às cegas — pedido
  explícito de resposta antes de mexer em mais código. Utilizador
  respondeu: **deixar registado, não corrigir agora**.
- **Artifícios/métodos usados**: workflow multi-agente (`Workflow` tool)
  com padrão planear (Fable 5) → implementar (modelo da sessão) →
  validar (Fable 5) por bug, em pipeline sem barreira entre os dois
  bugs; leitura direta dos ficheiros reais por mim depois de cada agente
  reportar, antes de confiar no resultado.
- **Melhorias feitas / ainda necessárias**: RF switch e delay do IMU
  corrigidos no código (`main`), nenhum dos dois testado em hardware
  (sem placa ligada) — novo item na lista de tarefas para essa
  confirmação. O achado sobre `sendTest()` não repor HIGH fica
  **registado, não corrigido**, por decisão explícita do utilizador —
  só relevante quando a lógica de alertas de emergência por LoRa for
  desenhada a sério (ainda não implementada).

### 29. "Já releste os ficheiros .md todos à procura de tarefas por fazer?" — varredura completa, multi-agente

- **Pergunta/dilema**: o utilizador questionou se a lista de tarefas
  ativa (TodoWrite) refletia mesmo tudo o que os documentos do projeto
  já registavam como pendente, ou se era só um subconjunto curado. Uma
  sessão anterior (2026-07-22) já tinha começado este tipo de varredura
  e ficou incompleta (só `PROJECT_STATUS.md` linhas 1-2000).
- **Onde/quando**: 2026-07-31.
- **Forma da resposta**: resposta honesta — não, não tinha sido feita
  uma varredura sistemática, só leituras dirigidas a perguntas
  específicas. Lançado um workflow com 13 agentes em paralelo (5 fatias
  de `PROJECT_STATUS.md`, 2 de `SECURITY_STATUS.md`, e um agente inteiro
  cada para `SECURITY_RESEARCH.md`, `RELATORIO_QA_DETALHADO.md`,
  `RELATORIO_QA_RESUMO.md`, `README.md`, `bridge/README.md`,
  `ml/README.md`), cada um extraindo, com citação literal e número de
  linha, tudo o que o próprio texto marca como pendente. Resultado:
  ~150 itens brutos. Depois de deduplicar e agrupar, ficaram 3 grupos:
  1. **Bloqueado por hardware** (a maioria, dezenas de itens) — testes e
     confirmações que só podem avançar com a placa ligada; não
     adicionados individualmente à lista ativa, já coberto pelo item
     genérico de confirmação em hardware.
  2. **Backlog de segurança/RGPD** (GDPR-001/002/003/005/006,
     BLE-003/004/005/006, WS-001, TLS/CORS/rotação de chave da API,
     SBOM, auditorias de dependências DEP-001/005, fila de pesquisa
     OWASP/NIST/ENISA/MITRE) — já tem o seu próprio sistema de IDs em
     `SECURITY_STATUS.md`/`SECURITY_RESEARCH.md`; não duplicado na lista
     geral.
  3. **Genuinamente novo e acionável** — 6 itens adicionados à lista:
     bug de CI em `dashboard.yml` (a extração do `<script>` principal
     apanha um comentário e só valida ~60% do script real); HR com
     valores fisiologicamente implausíveis sustidos (175-187 bpm) mesmo
     com o gate de amplitude já aplicado — distinto do bug de
     `finger_present` já rastreado, sinalizado numa sessão anterior como
     "próximo item de maior prioridade" mas nunca promovido à lista
     ativa; duplicação da struct `ImuPpgPayloadV1` entre `main.cpp` e
     `Ble.cpp` (risco de desalinhamento se só um for editado); condição
     de corrida no `QspiRingBuffer` entre `format()` e leitura/escrita
     concorrente, só mitigada; decisão pendente sobre apagar o branch
     remoto `Main` (maiúscula); e — achado mais relevante — a
     legitimidade/origem do próprio "vexp" (o payload que instrui
     agentes de IA a ignorar Grep/Glob, injetado em `.claude/CLAUDE.md`)
     e dos commits "v3"/"v4" nunca publicados nem associados a PR está
     registada como pergunta em aberto ao utilizador em três secções
     distintas de `PROJECT_STATUS.md` — **possível explicação para o
     branch `rewrite-v2` e o reset não atribuídos a ninguém** (ver
     entrada 26 sobre esse mistério).
- **Artifícios/métodos usados**: `Workflow` tool, 13 agentes em paralelo
  (fan-out puro, sem barreira — cada ficheiro/fatia é independente),
  cada um com `schema` estruturado (resumo, linha, citação literal,
  categoria) para reduzir risco de invenção; leitura do ficheiro de
  resultado completo (1709 linhas) antes de sintetizar, em vez de confiar
  só no resumo truncado da notificação.
- **Melhorias feitas / ainda necessárias**: lista de tarefas atualizada
  com os 6 itens novos + a confirmação pendente da estratégia de branch
  e do âmbito de "do zero" (já levantadas antes, agora também
  formalizadas como itens da lista). O grande volume de itens
  bloqueados por hardware ou já cobertos pelo backlog de segurança fica
  deliberadamente fora da lista ativa, para não a diluir — registado
  aqui, não perdido.

### 30. Remoção da extensão "vexp" — confirmado como extensão pessoal do utilizador, sem conteúdo único

- **Pergunta/dilema**: a origem do "vexp" (payload em `.claude/CLAUDE.md`
  que instruía agentes de IA a ignorarem Grep/Glob) ficou registada como
  mistério na entrada 29. O utilizador esclareceu: é uma extensão VS
  Code que ele próprio instalou, já não está em uso, e pediu para
  verificar o conteúdo antes de apagar.
- **Onde/quando**: 2026-07-31.
- **Forma da resposta**: lidos todos os ficheiros relacionados antes de
  apagar (regra: nunca apagar sem verificar primeiro) —
  `.claude/CLAUDE.md`, `.claude/hooks/vexp-guard.sh`,
  `.github/copilot-instructions.md`, `.vscode/mcp.json`,
  `.claude/settings.json`/`.vexp-bak`, e os 4 ficheiros de
  `.vexp/` (`.gitattributes`, `.gitignore`, `index.lock`,
  `manifest.json`). Confirmado que todos são 100% relacionados com o
  vexp — nenhum continha configuração ou dados de outra ferramenta
  misturados. `manifest.json` era só uma cache de hashes de 59
  ficheiros do projeto (índice de 2026-07-09, regenerável, sem valor
  próprio); `index.lock` mostrava o último acesso em 2026-07-17,
  confirmando que já não estava ativo (consistente com o aviso "no vexp
  binary found" visto nos commits desta sessão). Removidos do git via
  `git rm` os 9 ficheiros/pastas confirmados vexp-only;
  `.claude/settings.json` (que só continha o registo do hook do vexp)
  limpo para `{}` em vez de apagado, já que o ficheiro em si pode voltar
  a ser útil para outras definições.
- **Artifícios/métodos usados**: leitura direta de cada ficheiro
  candidato antes de qualquer remoção; `ls -la` a `.vexp/` e
  `.claude/hooks/` para confirmar que não havia mais nada nessas pastas
  além do que já se sabia.
- **Melhorias feitas / ainda necessárias**: vexp removido do repositório
  (não commitado ainda — fica no working tree para revisão). Nota
  importante: isto confirma que a extensão em si não tinha nada
  escondido de relevante, mas **não** confirma nem exclui que uma rotina
  associada a ela (ou outra coisa) tenha causado o `git reset`/checkout
  para `rewrite-v2` da entrada 26 — esse mistério continua em aberto,
  só o item da lista de tarefas foi reformulado para não sugerir que já
  está explicado.

## 2026-07-31 (continuação) — consolidação de branches, patient_conditions/allergies, e correções de firmware sem hardware

### 31. "O Main é para apagar" — verificação antes de executar uma instrução aparentemente destrutiva

- **Pergunta/dilema**: o utilizador pediu explicitamente para apagar um
  branch remoto `Main` (maiúscula) e para "usar sempre o main" daí em
  diante. Antes de correr `git push origin --delete Main` (ação
  irreversível sobre estado partilhado), decidi confirmar o estado real
  do repositório em vez de assumir que o branch existia como descrito.
- **Onde/quando**: 2026-07-31.
- **Forma da resposta**: `git ls-remote --heads origin` mostrou que não
  existe nenhum branch `Main` no remoto — só `main` (minúscula). O que
  o utilizador via como "Main" era uma referência `origin/HEAD` local
  desatualizada, sobrevivente de uma renomeação do branch default no
  GitHub que nunca tinha sido sincronizada localmente. Corrigido com
  `git remote set-head origin -a` (ação local, não destrutiva) em vez de
  tentar apagar algo que não existia. De seguida, `main` local (9
  commits à frente, incluindo os merges de `origin/main` e `rewrite-v2`
  feitos nesta mesma sessão) publicado com `git push origin main` —
  fast-forward limpo.
- **Artifícios/métodos usados**: `git ls-remote --heads origin` (fonte
  de verdade do servidor, não uma cópia local em cache) antes de
  qualquer comando destrutivo — princípio geral: nunca assumir o estado
  do repositório sem confirmar.
- **Melhorias feitas / ainda necessárias**: nenhuma ação destrutiva
  necessária; item da lista fechado por verificação, não por remoção.

### 32. Verificação de compatibilidade de `bridge/api.py` com `storage_advanced.py`

- **Pergunta/dilema**: o utilizador pediu para verificar se `api.py`
  (ficheiro FastAPI já existente mas nunca confirmado como estando em
  uso) era compatível com `storage_advanced.py`, para decidir entre
  reutilizar ou descartar.
- **Onde/quando**: 2026-07-31.
- **Forma da resposta**: leitura completa de `api.py` (304 linhas).
  Confirmado que está construído diretamente sobre o ORM de
  `storage_advanced.py` (`import storage_advanced as sa`), com
  autenticação por API-key por utilizador (`api_auth.py`), rate limiting
  real (`RateLimitMiddleware`), audit log, autorização por paciente via
  `patient_caregivers` (devolve 404 em vez de 403 para não permitir
  enumeração de IDs), e um endpoint de aderência a medicação idempotente
  com retry em `IntegrityError` para corridas de escrita concorrente.
  Veredicto: código real e funcional, não código morto — reutilizar, não
  descartar.
- **Artifícios/métodos usados**: leitura direta do ficheiro completo,
  sem assumir a partir do nome/localização.
- **Melhorias feitas / ainda necessárias**: decisão de manter tomada;
  falta ainda ligar `api.py` a um consumidor real (o dashboard atual
  usa o canal WebSocket do bridge, não este REST API) — item pendente
  na lista de tarefas.

### 33. Implementação de `patient_conditions`/`patient_allergies` — desenho aprovado passa a código

- **Pergunta/dilema**: o desenho (duas tabelas separadas, uma linha por
  entrada, inspirado em `Condition`/`AllergyIntolerance` do FHIR) já
  tinha sido aprovado pelo utilizador numa sessão anterior, com uma
  ressalva sobre normalização que, confirmado, já era satisfeita pelo
  desenho original (não exigiu redesenho). Faltava passar de desenho a
  código real.
- **Onde/quando**: 2026-07-31.
- **Forma da resposta**: adicionados os modelos ORM `PatientCondition` e
  `PatientAllergy` a `storage_advanced.py` (propriedade `display_text`
  cifrada, mesmo padrão de `nif`/`address` em `Patient`; `code_system`/
  `code` opcionais), tabelas correspondentes em `schema.sql`, e uma
  migração Alembic nova (`6181ca0ce076`, encadeada ao head anterior
  `b7c4f1a9e230`). Testado ponta-a-ponta antes de comitar: criação e
  leitura via relacionamento SQLAlchemy, migração aplicada do zero
  contra uma base de dados limpa (`alembic upgrade head`), e os 141
  testes existentes do bridge continuam todos a passar.
- **Artifícios/métodos usados**: leitura do modelo `Patient` existente e
  das migrações anteriores para replicar convenções exatas (nomes de
  índice, estilo de comentário, `String(512)` para campos cifrados);
  teste manual em Python (`sqlite:///:memory:`) antes de confiar na
  migração; `pytest bridge/tests/ -q` completo depois.
- **Melhorias feitas / ainda necessárias**: dados agora podem ser
  guardados; falta ainda expor isto no dashboard/API (fora do âmbito
  pedido nesta tarefa).

### 34. "Esquece o rewrite e segue para a frente" — ambiguidade sobre qual "rewrite"

- **Pergunta/dilema**: a frase podia referir-se a duas coisas muito
  diferentes: (a) abandonar a investigação da origem do branch
  `rewrite-v2`/reset (tema mais recente, mencionado por mim no turno
  anterior), ou (b) abandonar o objetivo maior, declarado no início
  desta sessão, de reescrever a arquitetura inteira do zero como
  exercício de aprendizagem. Interpretar mal isto de forma silenciosa
  seria um erro consequente — a opção (b) seria abandonar um objetivo
  pedagógico central da sessão.
- **Onde/quando**: 2026-07-31.
- **Forma da resposta**: escolhida a interpretação (a) — mais próxima
  no diálogo (era literalmente o último "rewrite" mencionado, no meu
  próprio turno anterior) e de menor impacto se errada (abandonar uma
  investigação é facilmente reversível; abandonar o projeto de
  reescrita não seria). Comunicada explicitamente a interpretação ao
  utilizador no mesmo turno ("assumo que é a essa rewrite que te
  referes... diz-me se querias dizer outra coisa"), em vez de decidir
  em silêncio ou bloquear a pedir confirmação para algo de baixo risco.
- **Artifícios/métodos usados**: nenhum — julgamento direto sobre risco/
  reversibilidade de cada interpretação, com a decisão tornada visível
  para permitir correção.
- **Melhorias feitas / ainda necessárias**: utilizador não corrigiu a
  interpretação nas mensagens seguintes — presume-se confirmada por
  omissão, mas fica registada como decisão explícita, não assumida.

### 35. Três correções de firmware sem hardware + condição de corrida do QspiRingBuffer já resolvida + resposta ao RF_SW

- **Pergunta/dilema**: com a placa fora do pulso da utilizadora
  ("segue para o que não precisa [de hardware] e testas quando eu
  regressar", depois "podes prosseguir continuamente até não
  conseguires mais"), avançar autonomamente pela lista de tarefas sem
  bloquear hardware, escolhendo quais itens eram mesmo seguros de
  fazer sem placa.
- **Onde/quando**: 2026-07-31.
- **Forma da resposta**: (1) `ImuPpgPayloadV1` deduplicada — extraída
  de `main.cpp`/`Ble.cpp` para `include/ImuPpgPayload.h`; as duas cópias
  já tinham divergido no nome de um campo (`hr_x10` vs `hr`, mesmo
  layout) — escolhido `hr` como nome canónico por ser o nome correto
  (o valor nunca foi x10). (2) `finger_present`: `Ppg.cpp` corrigido
  para o streaming contínuo de HR deixar de sobrescrever
  `g_latest.finger_present` com um `true` fixo a cada ~10ms — só
  `measureSpo2()` (deteção real via `FINGER_THRESHOLD`, a cada ~30s)
  escreve nesse campo agora. (3) `dumpCtrlCallback` (Ble.cpp)
  instrumentado com um log de todo o write recebido (cmd/len/estado de
  `s_dataModeEnabled`) antes do descarte silencioso que existia — prepara
  o diagnóstico real de "force_reading sem efeito" para quando houver
  hardware. As três verificadas por `pio run` (sucesso, sem avisos
  novos) — não testadas em placa real. Adicionalmente, revisitada a
  suspeita de condição de corrida no `QspiRingBuffer`: leitura completa
  do ficheiro confirmou que já estava corrigida desde 2026-07-08 com um
  mutex FreeRTOS a proteger todas as funções públicas — fechado por
  confirmação, não por correção nova. E respondida a pergunta pendente
  sobre o `RF_SW` (interno ao LoRa ou também liga à antena BLE?) por
  síntese de notas já registadas do esquemático real (o PDF em si nunca
  esteve no repositório) — é um switch externo ao módulo LoRa,
  partilhado com a antena BLE, mesmo componente do bug de 2026-07-03.
- **Artifícios/métodos usados**: build real (`python -m platformio run`)
  depois de cada alteração, não só leitura — para apanhar erros de
  compilação que uma revisão visual não apanharia. Tentativa de remover
  3 hooks locais do vexp (`.git/hooks/pre-commit`/`post-merge`/
  `post-checkout`) bloqueada de forma consistente (duas tentativas) pelo
  classificador de permissões por tocar diretamente em `.git/` — não
  contornada, deixada pendente para o utilizador.
- **Melhorias feitas / ainda necessárias**: os 3 fixes de firmware
  precisam de confirmação em hardware real (`force_reading` em
  particular só fica realmente resolvido depois de testado); os 3 hooks
  vexp residuais continuam por remover.
