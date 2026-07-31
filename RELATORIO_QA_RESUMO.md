# CareWear — Relatório de Perguntas e Dilemas (Resumo)

> Nível de profundidade: **resumo**. Cada entrada aqui é um parágrafo
> autossuficiente — dá para perceber a pergunta, a resposta e a razão sem
> abrir o ficheiro detalhado. Para o nível pormenorizado (código exato,
> linhas, métodos de investigação), ver `RELATORIO_QA_DETALHADO.md`. Não
> contém gestão de trabalho — isso vive na lista de tarefas da sessão e em
> `PROJECT_STATUS.md`.

## 2026-07-24 — Sessão de arquitetura, redundâncias e coordenação LoRa/BLE

### 1. Porquê duas bases de dados?

O bridge escreve os dados do wearable em duas bases de dados diferentes
ao mesmo tempo: `storage.py`, uma implementação direta em SQLite que
existe desde o início do projeto e que é hoje a única fonte que o
dashboard efetivamente lê; e `storage_advanced.py`, criada mais tarde com
um modelo de dados mais completo (cifra de campos sensíveis como NIF e
morada, mais entidades como alertas de emergência e auditoria), mas cujo
único leitor é uma API que nunca chegou a ser ligada a nada. Ou seja, há
trabalho a dobrar (cada leitura do wearable é gravada duas vezes) sem
benefício real hoje, porque a segunda base de dados não é consultada por
ninguém no caminho normal de uso.

### 2. O que é redundante no projeto?

Além das duas bases de dados, identificaram-se mais dois pontos: a API
REST (`api.py`), que está totalmente implementada (com autenticação e
limite de pedidos) mas sem qualquer aplicação a consumi-la; e a
coordenação entre as antenas de BLE e LoRa, que partilham fisicamente a
mesma trilha de antena mas cujo código só trata da entrada nesse recurso
partilhado, nunca da saída — um problema encontrado a fundo só nesta
sessão (ver ponto 5).

### 3. Qual base de dados remover?

Foi apresentada a escolha ao utilizador em vez de decidida unilateralmente,
porque as duas opções eram defensáveis e a decisão não é reversível sem
controlo de versões neste diretório. A decisão tomada foi manter
`storage_advanced.py` como fonte única a prazo — por ter o modelo mais
rico e ser onde os campos futuros de doenças/alergias fariam mais
sentido — e descontinuar `storage.py`. A migração de código (mudar o que
o dashboard lê) ainda não foi feita; ficou registada como tarefa.

### 4. Porque é que `api.py` está desligado, e onde devia ligar?

A API nasceu como um primeiro passo para o dashboard poder um dia
consultar dados por rede, em vez de só localmente através do bridge — mas
esse "consumidor" do lado do dashboard nunca chegou a ser escrito, porque
o trabalho ficou sempre atrás de bugs mais urgentes (frequência cardíaca,
calibração do movimento). A vantagem real de a manter não é "permitir
ligação" (o dashboard já está ligado ao bridge por outro caminho) — é
permitir acesso **autenticado à distância**, coisa que o canal atual do
dashboard não tem. Fica como decisão de arquitetura em aberto: vale a
pena esse caminho remoto, ou deve ser descontinuada?

### 5. Como corrigir a coordenação entre LoRa e BLE?

Ao investigar esta pergunta a fundo (não só teoricamente, lendo o código
linha a linha), encontrou-se um problema real e novo: o interruptor que
decide qual dos dois rádios está ligado à antena partilhada só é
comutado para o lado do LoRa quando este liga com sucesso — mas nunca é
devolvido ao lado do BLE depois. Isto significa que, na primeira vez que
o LoRa funcionar, o BLE pode ficar sem antena para sempre a partir daí,
sem que nada no código o repare. A correção sugerida é simples: devolver
esse interruptor ao estado BLE assim que a transmissão LoRa terminar, já
que o LoRa só transmite em rajadas curtas, ao contrário do BLE que está
sempre ativo.

### 6. Porque é que BLE e LoRa precisam mesmo de coordenação?

Não é uma escolha de software, é uma limitação física: os dois rádios
partilham uma única trilha de antena na placa, e cada um precisa de um
circuito de adaptação diferente, próprio da sua frequência. Ligar os dois
ao mesmo tempo degrada ou anula o sinal de ambos — foi exatamente isso
que já se observou no passado (o BLE desligava-se sozinho quando o LoRa
arrancava). Por isso alguém — neste caso, o software — tem de decidir
explicitamente qual rádio "possui" a antena a cada momento; hoje só essa
decisão de entrada existe, não a de saída.

### 7. O NFC ligava-se ao bridge no diagrama do firmware — estava errado?

Sim. O caso de uso do NFC é leitura passiva local (um leitor externo
encosta-se ao relógio, sem BLE nem bridge envolvidos) — o diagrama tinha
isso ao contrário. Corrigido: o bridge só entra na configuração do
conteúdo (desce por BLE), nunca na leitura em si. A mesma revisão
encontrou o NFC e o `notifications.py` como "becos sem saída" nos outros
dois diagramas (05, 06) — também corrigidos, e foi criado um quarto
diagrama de detalhe (09) para o `notifications.py`, que tem um pipeline
interno real (Twilio, SendGrid, janela de indisponibilidade do cuidador,
escalonamento).

### 8. `activity_inference.py` devia estar ligado aos modelos de ML?

Sim, e estava mal no diagrama do bridge (06): só tinha uma etiqueta de
texto ("carrega modelo offline"), sem ligação real a um nó de modelo,
ao contrário do diagrama de ML (08). Corrigido acrescentando um nó
"Modelo treinado" ligado por uma relação "carrega modelo", com nota a
remeter para o diagrama 08 para o detalhe completo do treino.

### 9. Dois nós de "storage" no mesmo diagrama são um erro?

Não — é o estado real do código nesse momento: a decisão de migrar para
`storage_advanced.py` (ponto 3) já tinha sido tomada, mas o código ainda
não tinha mudado, e o diagrama refletia isso com as etiquetas `"leitura
atual (a sair)"` vs. `"decidido: fonte única"`. Entretanto já foi
corrigido de vez — ver ponto 12 abaixo.

### 10. "Retenção ORM" é o mesmo que guardar os dados?

Não, é o oposto: os loops de retenção só **apagam** dados antigos
(política de retenção/GDPR); quem grava é `orm_persistence.py`. São dois
mecanismos completamente separados no código, cada um com o seu próprio
loop.

### 11. Faltam diagramas de detalhe para o dashboard e o notifications?

Para o dashboard, não — já existe um artefacto equivalente e mais
completo (`02_Arquitetura_do_Dashboard.html`, com 5 separadores
interativos), feito numa sessão anterior; refazê-lo seria trabalho
duplicado. Para `notifications.py`, sim — tem complexidade interna real
comparável ao ML (Twilio, SendGrid, janela de indisponibilidade do
cuidador, escalonamento com timeout, decisão deliberada de nunca
contactar o 112), por isso foi criado o diagrama 09.

### 12. Glossário rápido e correção de um erro meu

Termos explicados nesta sessão: **Twilio** (SMS), **SendGrid** (email),
ambos serviços externos pagos chamados por `notifications.py`;
**`bleak`** (biblioteca Python que fala BLE com o wearable); **WebSocket**
(ligação persistente e bidirecional, por isso usada para dados "ao
vivo", ao contrário de um pedido HTTP normal). Também uma correção
minha: tinha afirmado antes que "não há cron no projeto", o que estava
errado por generalização — só tinha verificado a ausência de cron
*dentro do bridge* (verdade, é só um loop interno) e estendi isso
incorretamente ao projeto inteiro. Existe sim um cron real: GitHub
Actions (`.github/workflows/demo-data.yml`, diário às 04:15 UTC), que
regenera dados de demonstração simulados — nunca toca em dados reais.

### 13. Pedido de corrigir rotas tortas e aproximar nós relacionados

Pedido reconhecido como válido — os diagramas passavam a validação
técnica (sem erros), mas isso não garante boa legibilidade nem
proximidade espacial entre nós logicamente ligados. Corrigido nas
entregas seguintes: leitor NFC reposicionado para junto do nó NFC (05);
ligações `ppg_task`/`imu_task → storage_task` explicitadas antes do QSPI
(07, depois substituído pelo nó central `g_latest` — ver ponto 14 desta
lista, mais abaixo); rotas com ângulos desnecessários corrigidas onde
foram encontradas.

### 14. `storage.py` continuava no diagrama do bridge (06) apesar de já decidido tirar

O utilizador teve de repetir este pedido mais do que uma vez. A decisão
de sair `storage.py` (ponto 3) tinha sido tomada em 2026-07-24, mas só se
tinha pedido para não alterar o fluxograma ainda nessa altura — o pedido
seguinte para atualizar ficou por executar a tempo. Corrigido em
2026-07-25: nó `storage.py` e o loop de retenção SQLite removidos do
diagrama 06; `ble_bridge.py → orm_persistence.py → storage_advanced.py`
passa a ser a única escrita representada, já sem "dual-write". O código
em si ainda não foi migrado (isso é o item 13 da lista de tarefas) — o
diagrama já mostra o destino, não (ainda) o estado do código.

## Arquitetura, visão resumida

O fluxo essencial do sistema é: o wearable tem quatro tarefas a correr em
paralelo (uma para frequência cardíaca/oxigénio, uma para movimento, uma
para guardar dados localmente quando não há ligação, e uma para os
enviar); os dados saem por Bluetooth já cifrados; um programa em Python
(o "bridge") recebe-os, decifra-os, grava-os numa base de dados
(decidido: só `storage_advanced.py`, ver ponto 3 e entrada 15 do
relatório detalhado), classifica a atividade da pessoa com um modelo de machine
learning, e dispara alertas reais só em emergências confirmadas; por fim,
esses dados chegam ao painel de controlo (dashboard) através de uma
ligação sem autenticação, porque hoje só é usada localmente. Em paralelo,
sem estarem integrados no fluxo principal: uma API por rede pronta mas
sem uso, e duas antenas de rádio que competem pelo mesmo recurso físico
sem uma arbitragem completa.

Diagramas associados (pasta `Análise/`): `05` mostra o sistema completo a
alto nível; `06`, `07` e `08` detalham, respetivamente, o interior do
bridge, do firmware do wearable, e do pipeline de machine learning.

## 15. O que é `g_latest`?

Uma struct C++ em `main.cpp` do firmware — não é um ficheiro nem um
processo, é uma variável em RAM. `ppg_task` (FC/SpO2) e `imu_task`
(movimento) escrevem lá dentro o valor mais recente que leram, sempre
dentro de uma **secção crítica** (interrupções suspensas por instantes,
para nenhuma outra tarefa ler a meio de uma escrita). `storage_task`
(grava offline) e `ble_gatt_dump_task` (serve por BLE) só leem de lá,
nunca escrevem. É, na prática, o único "ponto de verdade" do wearable:
várias tarefas produtoras → um sítio central → várias tarefas
consumidoras, em vez de cada tarefa falar diretamente com as outras.
Está representado como o nó central da análise 7 (arquitetura interna do
firmware).

## 16. Porque é que o RF switch parecia só ligado ao LoRa?

Inconsistência entre dois diagramas, não um erro conceptual: o RF switch
(pino `A2`) liga eletricamente **um** dos dois rádios (BLE 2.4 GHz ou
LoRa 868 MHz) à antena única que a placa partilha — não há duas antenas,
e ligar os dois rádios ao mesmo tempo desalinha a impedância de ambos.
Estado por omissão: BLE. Quando o LoRa transmite com sucesso, o switch
muda para o lado do LoRa — e, por bug ainda por corrigir (item 14 da
lista de tarefas), nada o devolve ao BLE depois, ficando preso
indefinidamente. A análise 7 (firmware) já mostrava esta ligação dupla
corretamente; a análise 5 (sistema completo) só mencionava a partilha no
rótulo da ligação ao LoRa, sem nó `rf_switch` nenhum, dando a impressão
errada de que era uma coisa exclusiva do LoRa. Corrigido nesta sessão:
nó `rf_switch` adicionado à análise 5, com as duas ligações explícitas.

## 17. Porque é que `storage_advanced.py` e o bridge estão ambos ligados ao dashboard?

Não é duplicação — são dois tipos de dados diferentes, com dois caminhos
diferentes, já assim no código antes desta sessão:

- **`bridge → dashboard`** (WebSocket, `:8765`, sempre ligado): dados
  **em tempo real** — cada leitura nova do wearable é reencaminhada assim
  que chega, sem tocar em nenhuma base de dados nesse caminho.
- **`storage_advanced.py → dashboard`** (pedido pontual): dados
  **históricos** — quando o dashboard mostra tendências, histórico ou
  exporta um CSV, lê diretamente a base de dados, não o bridge.

Ponto relevante para a arquitetura: é precisamente este caminho
histórico que `api.py` (REST autenticado, chaves por-utilizador, hoje
sem consumidor nenhum) parece ter sido desenhada para substituir — o
dashboard está a falar diretamente com a base de dados em vez de passar
por essa API já pronta. Continua por decidir se se liga `api.py` a este
fluxo ou se se descontinua (item 15 da lista de tarefas).

## 18. O GNSS precisaria de partilhar o RF switch com BLE/LoRa?

Não. Duas razões: (1) o GNSS liga-se por **I2C**, não por RF — é um
módulo à parte (CAM-M8Q) com a sua própria antena, e é só recetor (nunca
transmite), pelo que não existe o conflito "dois transmissores, uma
antena" que motiva o switch; (2) a frequência é outra — GNSS L1 é
~1.575 GHz, banda fisicamente diferente de 2.4 GHz (BLE) ou 868 MHz
(LoRa), exigiria uma antena própria de qualquer forma. O desenho atual
(GNSS sem ligação ao `rf_switch`) está correto, não é uma omissão a
corrigir. Ressalva: pode ainda existir dessensibilização (o transmissor
BLE/LoRa a reduzir a sensibilidade do recetor GNSS por proximidade
elétrica) — problema diferente, de interferência entre componentes
vizinhos, não de partilha de antena; fica coberto pelo teste de
coexistência já previsto (item 7 da lista de tarefas). Detalhe completo
na entrada 20 do relatório detalhado.

## 19. Porque há duas fontes de frequência cardíaca (FC)?

Duas rotinas separadas em `Ppg.cpp`, nascidas em alturas diferentes,
nunca unificadas. `measureSpo2()` (a cada ~30s) usa o algoritmo de
referência Maxim, que devolve SpO2 e FC juntos, com deteção real de dedo
presente — robusta mas lenta. `processHrSample()` (streaming a cada
~10ms) é uma pipeline caseira criada para preencher os intervalos entre
ciclos de SpO2 com uma leitura "ao vivo" — mas nasceu sem verificação de
dedo presente (bug ainda pendente, item 2 da lista de tarefas). A FC que
`measureSpo2()` já calculava e descartava foi publicada nesta sessão
(item 1, concluído); a duplicação em si não é tratada como erro a
remover — servem propósitos diferentes (precisão vs. frequência de
atualização). Detalhe completo na entrada 21 do relatório detalhado.

## 20. A antena BLE partilha mesmo o RF switch com o LoRa, ou já vem incorporada na XIAO? (por confirmar)

Ponto levantado pelo utilizador que contesta uma premissa dada como
"confirmada" há várias sessões: é uma placa XIAO, e a antena BLE já vem
incorporada no módulo nRF52840 Sense Plus — não precisaria de switch
nenhum para ser partilhada com o LoRa. Ao reler a documentação, o pino
`RF_SW` está descrito dentro do pinout do próprio módulo LoRa
(Wio-SX1262), o que é consistente com ser um switch **interno ao LoRa**
(comum em chips SX126x), sem relação com a antena BLE. Isto reabriria a
causa raiz do bug de 2026-07-03 (BLE a apagar-se quando o LoRa
arrancava) — se confirmado, não pode ter sido "corte físico da antena
BLE". **Por instrução explícita do utilizador, nada foi alterado hoje**
(nem diagramas, nem a explicação do bug) — só registo do ponto em
aberto, acrescentado como novo item à lista de tarefas: confirmar com o
esquemático real se `RF_SW` toca ou não na antena BLE. Detalhe completo
na entrada 22 do relatório detalhado.

## 21. Migração real de `storage.py` para `storage_advanced.py` — concluída

Depois de sessões anteriores só terem decidido (2026-07-24) e depois
corrigido o diagrama (2026-07-25), esta sessão executou a mudança em
código a sério, a pedido explícito: "Quero que storage deixe de ser a
minha base de dados principal e quero que passe a ser
storage_advanced." Resultado: `bridge/storage.py` removido do
repositório; `storage_advanced.py` (via `orm_persistence.py`) é agora a
única escrita e a única leitura do bridge — o dashboard continua a
funcionar sem alterações, porque o formato dos dados na rede não mudou.
Corrigido também um erro de modelação que o próprio diagrama 5 tinha:
mostrava uma ligação direta `storage_advanced.py → dashboard`
"planeada", que nunca correspondeu à implementação real — o histórico
(get_history/get_daily_trend/export_csv) sempre passou e continua a
passar pelo `bridge` (mesmo canal WebSocket), só o backend por dentro é
que mudou. Suite de testes completa: 141 passed. Achado durante a
migração: duas funções de `storage.py`
(`get_recent_emergency_alerts`/`export_emergency_alerts_csv`) eram
código morto, nunca chamadas em lado nenhum — decidido não as migrar.
Relatório técnico do módulo removido escrito antes da remoção, a pedido
explícito do utilizador
(`Análise/11_Relatorio_Tecnico_Storage_Legado.html`). Detalhe completo
nas entradas 23 e 24 do relatório detalhado.

## 22. Pedido de reescrever a arquitetura do zero, como exercício de aprendizagem

Depois de terminada a migração de storage, pedido para "refazer este
trabalho do zero" — âmbito inicialmente ambíguo, esclarecido como a
**arquitetura inteira** (firmware → bridge → dashboard), motivado por
querer aprender a fazê-lo sozinha, não por insatisfação com o código
atual (que continua a passar 141 testes). Proposto um roteiro faseado no
firmware — IMU → PPG → storage no dispositivo → BLE → RF switch/LoRa →
GNSS/NFC — aplicando desde o início as lições já aprendidas em sessões
anteriores (atraso de assentamento do IMU, taxa de amostragem do PPG,
publicar as duas fontes de FC, RF switch corrigido por desenho), seguido
de bridge e depois dashboard. Recomendado manter o `main` intacto como
rede de segurança e trabalhar num branch novo (`rewrite-v2`) — estratégia
ainda por confirmar. Nenhuma alteração de código feita, só planeamento.
Detalhe completo na entrada 25 do relatório detalhado.

## 23. Reformulação do modo de ensino: mentor crítico, orientação em formato de prompt, sem executar pelo utilizador

O modo pedagógico pedido em 2026-07-19 foi reenviado e muito expandido,
como um "prompt" de sistema completo (papel de professor/investigador/
mentor/engenheiro sénior, método de ensino por primeiros princípios,
pensamento crítico sem concordar por defeito, investigação científica,
gestão de estudo, rigor), com pedido explícito de formatação limpa — e
esclarecido a seguir que, para a reescrita da arquitetura, o papel do
assistente é dizer o que fazer e como, em formato de prompt, nunca
executar o trabalho por ela. Entregues dois documentos: o prompt de
mentor reformulado, e um prompt de orientação para a Fase 0 do firmware
(perguntas a responder antes de começar, passos, pistas, critério de
sucesso, sem código nenhum). Guardado em memória persistente para reger
esta e futuras sessões. Detalhe completo na entrada 26 do relatório
detalhado.

## 24. "Não existem diferenças de programação entre Seeed e Adafruit nRF52" — premissa incompleta, corrigida com evidência do toolchain instalado

Durante a Fase 0 da reescrita, o utilizador assumiu que não há diferenças
de programação entre uma placa Seeed XIAO e uma Adafruit nRF52. Parcialmente
certo, corrigido em vez de confirmado às cegas: a placa usa mesmo o core
Arduino da Adafruit (`"bsp": "adafruit"` no `board.json` real, confirmado
localmente) — a API de programação é idêntica — mas o *variant* (pinos +
periféricos) não é partilhado. Prova concreta: `PIN_A2 = 2` na XIAO Sense
Plus vs. `PIN_A2 = 16` numa Adafruit Feather nRF52840 Sense, comparando os
`variant.h` instalados. Ligado ao uso real de `kPinRfSwitch = A2` em
`Lora.cpp`, como aviso para a Fase 5 do roteiro de reescrita. Detalhe
completo na entrada 27 do relatório detalhado.

## 25. Correção real de dois bugs conhecidos (RF switch + delay da calibração do IMU), e achado novo sobre `sendTest()`

Usado um workflow multi-agente (Fable 5 planeia/valida, Sonnet implementa,
a pedido explícito do utilizador) para corrigir dois bugs de `main` sem
placa ligada: `Lora.cpp` passou a devolver sempre o RF switch a BLE depois
de qualquer transmissão LoRa (sucesso ou falha), e `Imu.cpp` passou a
esperar `delay(3000)` (em vez de 2000) antes de calibrar. Ambas
verificadas por leitura direta do ficheiro, pelo agente e por mim.
Achado novo durante a validação: `sendTest()` só sabe pôr o switch em
LoRa (HIGH) através de `begin()`, uma única vez — uma segunda chamada
transmitiria "com sucesso" no código de retorno mas com a antena já
roteada para BLE, sem erro reportado. Apresentado ao utilizador em vez
de corrigido às cegas; decisão: **deixar registado, não corrigir agora**
— só relevante quando a lógica de alertas de emergência por LoRa for
desenhada a sério. Nenhuma das duas correções foi testada em hardware.
Detalhe completo na entrada 28 do relatório detalhado.

## 26. Varredura completa dos .md à procura de tarefas por fazer (multi-agente, 13 agentes)

O utilizador questionou se a lista de tarefas ativa refletia mesmo tudo o
que os documentos já registavam como pendente. Resposta honesta: não,
só havia leituras dirigidas a perguntas específicas. Workflow com 13
agentes em paralelo (todo o `PROJECT_STATUS.md` em 5 fatias,
`SECURITY_STATUS.md` em 2, e os restantes `.md` inteiros) extraiu ~150
itens pendentes citados no texto. Depois de deduplicar: a maioria é
bloqueada por hardware (não adicionada individualmente) ou já faz parte
do backlog de segurança/RGPD com o seu próprio sistema de IDs em
`SECURITY_STATUS.md` (também não duplicado). Ficaram 6 itens
genuinamente novos e acionáveis, adicionados à lista: bug de CI que só
valida ~60% do `dashboard.yml`, HR com valores implausíveis sustidos
(175-187 bpm, distinto do bug de `finger_present`), struct
`ImuPpgPayloadV1` duplicada entre `main.cpp`/`Ble.cpp`, condição de
corrida no `QspiRingBuffer` só mitigada, decisão sobre apagar o branch
remoto `Main`, e — o achado mais relevante — a origem do "vexp" e de
commits "v3"/"v4" nunca publicados está registada como pergunta em
aberto ao utilizador, possível explicação para o branch `rewrite-v2` e
o reset não atribuídos a ninguém (ver entrada 25). Detalhe completo na
entrada 29 do relatório detalhado.

## 27. Remoção da extensão "vexp" — confirmado como extensão pessoal do utilizador, sem conteúdo único

O utilizador esclareceu que o "vexp" é uma extensão VS Code que ele
próprio instalou, já sem uso, e pediu para verificar o conteúdo antes
de apagar. Lidos todos os ficheiros relacionados
(`.claude/CLAUDE.md`, hook, `copilot-instructions.md`, `mcp.json`,
`settings.json`/`.vexp-bak`, e os 4 ficheiros de `.vexp/`) — confirmado
que são 100% vexp, sem nada misturado de outra ferramenta;
`manifest.json` era só uma cache de índice regenerável, `index.lock`
confirmava inatividade desde 2026-07-17. Removidos do git (9
ficheiros/pastas), `settings.json` limpo em vez de apagado. Isto
esclarece a origem do payload em si, mas **não** confirma se alguma
rotina ligada a ele causou o `git reset`/branch `rewrite-v2` da entrada
25 — esse mistério continua em aberto. Detalhe completo na entrada 30
do relatório detalhado.
