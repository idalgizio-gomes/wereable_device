# CareWear — Estado do Projeto

> Este ficheiro existe para retomar o trabalho sem ser preciso reler a conversa inteira.
> Atualiza-o no fim de cada sessão de trabalho relevante.


## Visão geral

Plataforma wearable para monitorização de rotina em contexto de demência: wearable
(Seeed XIAO nRF52840 Sense Plus) + firmware + dashboard web. Roadmap alargado
(app móvel, BD SQL, LoRa, HAR/deteção de anomalias embarcada) definido pelo
utilizador — ver secção "Roadmap alargado" no fim.

Contexto científico: o firmware e a estrutura de dados seguem o artigo
"Routine-Aware Behavioural Monitoring Framework for Dementia Care Using
Wearable-Derived Synthetic Daily Routines" (pipeline XGBoost + LSTM Autoencoder
+ detetor de duração baseado em regras), partilhado pelo utilizador como base
científica do projeto.

## Repositório

https://github.com/idalgizio-gomes/wereable_device (branch `main`). Estado
local e remoto sincronizados. Identidade git configurada: Idalgizio Gomes
(idalgizio12@gmail.com).

## Hardware atual

- Seeed Studio XIAO nRF52840 Sense Plus (framework Arduino via PlatformIO).
- Sensores: IMU LSM6DS3 (acel+giro, ~52 Hz), PPG MAX3010x (SpO2/HR), ecrã OLED
  SSD1351, flash QSPI externa (armazenamento), BLE (SoftDevice S140).
- **Antena LoRa Wio-SX1262 já integrada na placa** (confirmado pelo
  utilizador) — ainda sem driver/firmware nenhum a usá-la. Mapeamento exato
  de pinos (NSS/CS, DIO1, RESET, BUSY) para a variante nRF52840 ainda por
  confirmar (pesquisa em curso — ver "Próximas tarefas").
- Botão físico de ligar/desligar (BTN_PIN) **partido** — ver "Riscos".

## Firmware — módulos e estado

| Módulo | Ficheiros | Estado |
|---|---|---|
| `main.cpp` | `src/main.cpp` | Comentado exaustivamente (PT). Contém bypass de debug `WAKE`/`SLEEP` via série (ver abaixo). |
| `Imu` | `src/Imu/`, `include/Imu/` | Comentado. Stack reduzida 1024→768 words (conservador, não confirmado em hardware). |
| `Ppg` | `src/Ppg/`, `include/Ppg/` | Comentado. Stack reduzida 1536→1152 words (idem). |
| `Ble` | `src/Ble/`, `include/Ble/` | Comentado. Bug corrigido: nome BLE ausente no advertising de provisioning (agora usa `Bluefruit.ScanResponse.addName()`). Stack reduzida 3072→2560 words. Prints não regulados de `gattDumpTask` agora atrás de `kGattDumpVerboseLogs`. |
| `Storage` | `src/Storage/`, `include/Storage/` | Comentado. |
| `Clock` | `src/Clock/`, `include/Clock/` | Comentado. |
| `QspiRingBuffer` | `src/QspiRingBuffer/`, `include/QspiRingBuffer/` | Comentado. Ring buffer de 64 bytes/slot na flash externa. |

`STORAGE_TASK_STACK_WORDS` (em `main.cpp`) reduzido 2048→1536 words.

### Correções e funcionalidades confirmadas em hardware real (após a 1ª otimização)

- **Instabilidade BLE corrigida**: `kGattDumpInterPacketMs` 0→2ms em `Ble.cpp` — sem atraso nenhum, o streaming (~208 notificações/seg) sobrecarregava a pilha BLE do central (Windows) e causava desconexões repetidas poucos segundos após o início. Confirmado estável em hardware real depois da correção.
- **Deteção de inatividade corrigida** (`Imu.cpp`, `detectInactivity`): o contador de "parado" reiniciava para 0 numa única amostra de ruído (de 156 exigidas, ~3s a 52Hz), tornando quase impossível atingir os 3s mesmo genuinamente parado (confirmado em teste real: vários segundos imóvel sem mudar de estado). Passou a "contador com fuga" (decai, não zera) + limiar de aceleração alargado de 0.05g para 0.08g. **O tempo de deteção continua 3 segundos** — só ficou mais robusto a ruído, não mais "sensível/rápido". Nota do utilizador: este flag de 3s serve só para decidir quando medir FC, é um conceito totalmente separado de uma futura deteção de "inatividade prolongada/emergência" (essa terá o seu próprio limiar, muito mais longo — ver secção de emergência abaixo).
- **Leitura forçada de FC+SpO2** (`Ppg.h/cpp`, `Ble.cpp`): `Ppg::requestManualHr(durationMs)` e `Ppg::requestManualSpo2()` permitem medir mesmo em movimento, quando pedido explicitamente. Comando BLE `kDumpCtrlForceHr` (0x03) em `dumpCtrlChar` aciona os dois de uma vez (um só botão no dashboard, "Medir agora").
- **Reset de leituras** (`Ble.cpp`): comando `kDumpCtrlResetReadings` (0x04) chama `QspiRingBuffer::format()`, apagando só os registos guardados (não toca em calibração nem chave AES). **Risco de corrida conhecido e não resolvido**: `storageTask` (escreve) e `gattDumpTask` (lê) podem aceder ao ring buffer ao mesmo tempo que o format() corre a partir do contexto BLE; pede-se paragem do streaming + pausa de 100ms antes de formatar, o que reduz mas não elimina a janela de corrida — uma correção completa exigiria sincronização dentro do próprio `QspiRingBuffer`.

### Dependência corrigida

`platformio.ini` estava sem `adafruit/Adafruit SPIFlash` (usada por `QspiRingBuffer.cpp`)
— build falhava. Já adicionada.

### Flags de debug ativas (main.cpp) — remover quando já não forem necessárias

- `DEBUG_SERIAL_WAKE` (1): comandos `WAKE`/`SLEEP` pela porta série substituem o
  long-press físico, porque **o botão físico está partido**. Escrever `WAKE` liga
  o dispositivo sem botão; `SLEEP` desliga.
- `DEBUG_STACK_WATERMARKS` (1): imprime a cada 15s a folga mínima histórica de
  stack (`uxTaskGetStackHighWaterMark`) de `storage_task`, `imu_task`, `ppg_task`,
  `ble_gatt_dump_task` — usar para validar/ajustar os tamanhos de stack acima.

### Verificado em hardware real (build + upload OK via `pio run -t upload`)

- Boot completo, ecrã, storage, calibração, IMU a 52Hz, PPG (SpO2 sem dedo = OK),
  storageTask a gravar no ring buffer, BLE provisioning + sync de hora via
  nRF Connect (escrita manual na characteristic Current Time 0x2A2B, formato:
  10 bytes — ano LE uint16, mês, dia, hora, min, seg, +3 bytes ignorados).
- **Valores reais de `[STACK] ... free_words=` confirmados em 2026-07-03**
  (captura de ~30s durante streaming BLE ativo, sem fome/atividade forçada):
  `storage_task` livre=1420/1536 (~92% livre — muito sobredimensionado);
  `imu_task` livre=457/768 (~60% livre — bem dimensionado);
  `ppg_task` livre=992/1152 (~86% livre — sobredimensionado);
  `ble_gatt_dump_task` livre=2453/2560 (~96% livre — muito sobredimensionado).

### 2ª ronda de otimização de RAM (2026-07-03, rotina diária) — stacks reduzidas com base nos dados reais acima

A partir dos valores medidos em hardware real na sessão anterior (mesmo
dia), aplicados os tamanhos-alvo já propostos, todos mantendo ≥3x de
margem sobre o uso máximo observado (o habitual recomendado é 2-3x):

| Task | Antes | Depois | Uso observado (words) | Margem nova | Poupança |
|---|---|---|---|---|---|
| `storage_task` (`main.cpp`) | 1536 | **768** | ~116 (1536-1420) | ~5.6x | -3072 bytes |
| `ppg_task` (`Ppg.cpp`) | 1152 | **640** | ~160 (1152-992) | ~3x | -2048 bytes |
| `ble_gatt_dump_task` (`Ble.cpp`) | 2560 | **1280** | ~107 (2560-2453) | ~11x | -5120 bytes |
| `imu_task` (`Imu.cpp`) | 768 | 768 (sem alteração) | ~311 (768-457) | ~1.5x | — |

Total poupado nesta ronda: **-10240 bytes (10 KB) de RAM** só em reservas
de stack FreeRTOS, sobre as já reduzidas na 1ª ronda (que por sua vez já
tinham poupado ~6656 bytes face aos valores originais). `imu_task` não foi
tocada — já tem a menor margem relativa (~1.5x) das quatro, por isso fica
como está.
**Pendente de confirmação em hardware real**: `DEBUG_STACK_WATERMARKS`
continua ativo em `main.cpp` — a próxima vez que o dispositivo estiver
acessível por USB, confirmar que `free_words` de cada task se mantém
confortavelmente acima de 0 com os novos tamanhos, incluindo durante os
ramos mais pesados de cada task (ex.: medição de SpO2 completa em
`ppg_task`, prints de HR/SpO2 válidos em `storage_task`). Esta rotina não
tem acesso ao dispositivo físico, por isso não pôde validar isto
diretamente — só a aritmética/margens de segurança acima.

### Scripts obsoletos removidos

`test/csv_serial_capture.py`, `test/spiffs_serial_cli.py`, `test/GATT.cpp` — eram
de uma versão antiga (SPIFFS + CSV) incompatível com o protocolo BLE binário
atual (`DumpDataPacket`/`DumpStatusPacket`, comandos START/STOP via
`dumpCtrlChar`). Substituídos por `bridge/ble_bridge.py` (ver abaixo).

### Correção importante: o "modo de dados" BLE NÃO está cifrado

Apesar de existir troca e persistência de uma chave AES (`aesKeyChar`), o
registo transmitido no streaming de sensores chama-se `FullPlain` no código
(`src/Ble/Ble.cpp`) — vai em texto simples. Há um comentário no firmware a
dizer que a versão cifrada é "eventual" (futura, não implementada). Isto tinha
sido descrito incorretamente como "cifrado" numa versão anterior deste
ficheiro — corrigido.

## Bridge BLE ↔ WebSocket (`bridge/ble_bridge.py`)

Liga o wearable ao dashboard web sem precisar de Web Bluetooth (que nunca vai
funcionar em Safari/iOS). Fluxo: `Wearable (BLE)` → `ble_bridge.py` (Python,
bleak) → `ws://localhost:8765` → dashboard (browser).

- Deteta e liga-se ao dispositivo "Wearable" automaticamente, com reconexão.
- **Escreve a hora atual (UTC) sozinho** na characteristic Current Time
  (0x2A2B) se o dispositivo ainda estiver em provisioning — substitui a
  necessidade de usar o nRF Connect manualmente.
- Remonta os fragmentos `DumpDataPacket` em registos `FullPlain` completos e
  reencaminha-os em JSON para todos os clientes WebSocket ligados.
- **Limite de taxa para o dashboard**: registos "normais" (sem HR/SpO2 novo)
  só são reenviados no máximo a 4/seg (`RECORD_BROADCAST_MIN_INTERVAL_S`);
  enviar ao ritmo total (~14/seg observado) causava desconexões repetidas da
  ligação WebSocket no browser. Registos com HR/SpO2 novos são sempre
  enviados de imediato.
- **Aceita comandos do dashboard**: mensagens JSON `{"cmd":"force_reading"}`
  e `{"cmd":"reset_readings"}` recebidas via WebSocket são traduzidas em
  escritas em `dumpCtrlChar` (0x03/0x04), com resposta `command_result`
  (ok/erro) para a interface dar feedback. Canal não autenticado — só deve
  ser exposto em localhost.
- Dependências: `pip install -r bridge/requirements.txt` (bleak, websockets).

## Dashboard web (protótipo)

Ficheiro: `web/dashboard/index.html` (versionado no repo).

**Alterações 2026-07-03 (feedback direto do utilizador sobre a interface real, com screenshot)**:
1. **Perfil expandido**: telemóvel, NIF, morada (utente) e cédula
   profissional (clínico) adicionados. **Campos sensíveis (NIF, morada)
   exigem aprovação da equipa clínica** antes de serem aplicados
   (`requestProfileFieldChange()`/`approveProfileFieldChange()`/
   `rejectProfileFieldChange()`) — o Utente/Família submete, fica
   pendente, e o Médico/Técnico aprova/rejeita num novo cartão
   "Aprovações pendentes" na mesma vista Perfil. Adicionado também
   "Contacto de emergência (cuidador)" (nome, telemóvel, relação) ao
   perfil Utente/Família.
2. **Bug corrigido: destaque de navegação não atualizava ao trocar de
   perfil.** `renderView()` nunca tocava nas classes `.active` dos
   botões da barra lateral — só `activateNavItem()` (chamada pelo clique
   direto) o fazia. Resultado visível no screenshot do utilizador: o
   botão "Medicação" continuava destacado depois de entrar como
   Médico/Técnico, mesmo a mostrar a vista "Pacientes". Corrigido em
   `login()`: limpa `.active` de todos os nav-items e marca
   explicitamente o botão da vista por omissão do perfil que entrou.
3. **"Marcar como lida" agora remove o alerta da área recente.** Antes só
   trocava o botão por "✓ Lida" mas o alerta continuava visível.
   `unreadActiveAlerts()` (novo) filtra os já lidos — usado em "Alertas
   recentes" (Resumo) e "Alertas por severidade" (Pacientes). Nova vista
   **"Histórico de alertas"** (ambos os perfis) mostra todos os alertas
   (lidos e não lidos); o Médico/Técnico pode apagar individualmente
   (`deleteAlert()`) ou limpar tudo (`clearAllAlertsForPatient()`),
   mesma lógica do Registo de emergências.
4. **Gestão de medicação pelo médico**: novo cartão em "Medicação"
   (visível só ao Médico/Técnico) para adicionar (`addMedicationForPatient()`)
   ou remover (`removeMedicationForPatient()`) medicamentos prescritos,
   persistido por paciente em localStorage (`carewear_medications_registry`)
   sobre os dados de base de `PATIENTS[i].medications` — sem alterar a
   constante `PATIENTS` diretamente. `patientMedications(patient)` passou
   a ser a função a chamar em vez de `patient.medications` diretamente.
5. **Exportação em PDF melhorada**: cabeçalho com marca "CW", rodapé com
   nota de confidencialidade, numeração de página (`@page` + contador CSS
   — suportado no motor de impressão do Chromium), tabelas com
   alinhamento/espaçamento consistente e linhas alternadas. **Removida** a
   secção "Exportar dados" (CSV/JSON desativados, dependente da BD ainda
   por construir) — o utilizador pediu para a tirar por não ter função
   nenhuma enquanto essa BD não existir; a exportação FHIR/PDF (que já
   funciona) continua.

- HTML/CSS/JS autocontido, tema escuro clínico.
- **Login + registo**: seleção de perfil (Utente/Família vs Médico/Técnico),
  e ecrã "Criar conta" com campos que se ajustam ao perfil (relação/utente
  monitorizado vs instituição/cédula profissional). Sem backend real —
  protótipo que valida o fluxo, não persiste nada (ver riscos).
- Área Utente/Família: resumo, rotina diária (timeline em canvas +
  **análise individual por categoria de atividade** com chips
  Dormir/Descanso/Atividade/Alimentação/Higiene, mostrando tempo hoje,
  ocorrências, duração média, comparação semanal e lista de blocos), sinais
  vitais (+ botão "Medir agora" para forçar FC+SpO2), tendência semanal,
  heatmap semanal, alertas, **Definições** com zona de risco ("Repor
  leituras" + modal de confirmação explicando o que é apagado).
- Área Médico/Técnico: pacientes, estado do dispositivo/firmware (liga aos
  dados reais da otimização RAM/CPU), registo de anomalias, limites de duração
  (tabela do template do artigo, editável em protótipo), exportar dados.
  **Seletor de paciente** (adicionado 2026-07-03, pedido do utilizador — um
  médico tem vários pacientes): a vista "Pacientes" lista agora 3 pacientes
  fictícios com botão "Selecionar" por linha (`selectPatient()`), persistido
  em localStorage, atualizando o rótulo do menu lateral e o título do
  cartão de alertas. **Limitação honesta, documentada na própria interface**:
  isto só muda a identidade apresentada — a ligação BLE real continua
  limitada a um único dispositivo físico de cada vez (o que estiver por
  perto e for encontrado pelo bridge); o bridge ainda não suporta
  selecionar/alternar por MAC. Ligar isso a sério exigiria o bridge aceitar
  um comando `{"cmd":"connect_to","mac":"..."}` e usar esse MAC específico
  em vez de procurar por nome — não implementado ainda, fica no backlog.
- **Bug corrigido (2026-07-03)**: o sino de notificações na topbar não
  fazia nada na prática — usava um `querySelector` com vários seletores
  separados por vírgula que, por ordem de posição no DOM (não por ordem de
  preferência), resolvia quase sempre para o botão "Resumo" dentro de
  `#navUtente`, mesmo estando esse grupo escondido. Como o perfil por
  omissão (Utente/Família) já mostra essa vista, clicar no sino parecia
  não ter efeito nenhum. Corrigido com `onNotificationBellClick()`, uma
  função que decide explicitamente pelo `currentRole` atual: no perfil
  Utente/Família ativa a vista Resumo e desloca a página até ao cartão de
  alertas (para dar feedback visível mesmo se já lá estava); no perfil
  Médico/Técnico ativa a vista "Anomalias detetadas".
- **Bugs corrigidos (2026-07-03, reportados pelo utilizador)**:
  1. Mudar de paciente na vista "Pacientes" não mudava os dados do
     "Registo de anomalias" nem do "Dispositivo & firmware" — ambos
     continuavam sempre fixos na Maria Silva. Corrigido: `alerts` e
     `anomalyLog` deixaram de ser constantes globais e passaram a campos
     de cada objeto em `PATIENTS` (`p.alerts`, `p.anomalyLog`), acedidos
     via `currentAlerts()`/`currentAnomalyLog()` (sempre relativos a
     `selectedPatient()`). Adicionados dados fictícios distintos para os
     3 pacientes (incluindo um paciente sem alertas ativos e outro com o
     dispositivo desligado há mais de um dia, para testar os estados
     vazios). Bateria e ocupação do ring buffer também passaram a variar
     por paciente (dados reais de dispositivo); **RAM/Flash de programa e
     folga de stack continuam iguais para todos de propósito** — são
     propriedades do firmware instalado (o mesmo binário em todos os
     wearables), não do dispositivo individual, com uma nota explícita
     disso na própria interface para não parecer um esquecimento.
  2. O sino de notificações não tinha forma de "desligar" o ponto
     vermelho — pedido do utilizador foi um botão explícito de
     confirmação, em vez de o ponto desligar sozinho só por abrir a
     vista (fácil de disparar sem querer). Adicionado "Marcar como lida"
     por alerta (`markAlertRead()`), com estado persistido em
     `localStorage` namespaced por paciente+alerta
     (`patientAlertKey()`); o ponto vermelho (`#notifBadgeDot`) só
     desliga quando já não há nenhum alerta por ler do paciente
     selecionado (`updateNotificationBadge()`). Distinto de silenciar
     (que pausa por um período e afeta o escalonamento) — marcar como
     lida só regista que o cuidador já viu a informação.
- **Ligação em direto ao bridge**: a página tenta ligar-se sozinha a
  `ws://localhost:8765` ao carregar. Se conseguir, os cartões de FC/SpO2/
  passos/quedas/movimento e o gráfico de FC na vista "Sinais vitais" passam a
  mostrar dados reais recebidos do dispositivo; caso contrário, mantém os
  dados de demonstração, agora claramente rotulados ("Demonstração", "—")
  em vez de valores falsos fixos. **Importante**: a página publicada como
  Artifact (claude.ai/code/artifact/...) nunca consegue alcançar
  `localhost` — para dados reais tem de se abrir o ficheiro local
  `web/dashboard/index.html` diretamente no browser (não o link do
  Artifact), com o bridge a correr.
- Dados de classificação de rotina (Dormir/Descanso/Atividade/Alimentação/
  Higiene) continuam **simulados** — o classificador HAR ainda não está
  embarcado no firmware (ver secção "Modelo de Machine Learning" abaixo para
  o progresso do treino, ainda só no backend/offline).
- Base de dados: **decidido SQL** (motor concreto — SQLite vs Postgres/MySQL —
  por decidir quando desenharmos o serviço de persistência).

### Correções e melhorias (sessão 2026-07-03, rotina diária)

- **Bug de rotulagem de dados simulados corrigido**: os tiles "Sono" e
  "Alimentação" no Resumo mostravam valores fixos (`7h 24m`, `3 refeições`)
  sem nenhum `sim-flag`/aviso, ao contrário dos restantes cartões de rotina —
  violava diretamente a regra do projeto de nunca apresentar dados simulados
  sem rótulo. `statTile()` ganhou um parâmetro `sim` opcional; os dois tiles
  agora mostram o badge "simulado" tal como os cartões de rotina diária.
- **Botão "Alertas" da topbar corrigido**: chamava `switchNav(...)`, função
  que não existia (`ReferenceError` ao clicar, botão morto). Adicionada
  `activateNavItem()`/`switchNav()` reutilizando a lógica do listener de
  navegação. **Causa raiz adicional encontrada só ao testar no browser real
  (Playwright)**: o próprio atributo `onclick` estava malformado —
  continha `\"` (aspas duplas escapadas com barra invertida) dentro de um
  atributo HTML delimitado por aspas duplas. HTML não suporta esse tipo de
  escape; o parser cortava o atributo no primeiro `"` literal, partindo o
  `querySelector(...)` a meio e gerando `SyntaxError: Invalid or unexpected
  token` ao clicar (confirmado via `el.getAttribute('onclick')` no browser,
  que mostrava o atributo truncado). Corrigido removendo as aspas à volta
  dos valores dos seletores de atributo CSS (`[data-view=anomalias]` em vez
  de `[data-view=\"anomalias\"]` — válido em CSS para identificadores
  simples), eliminando o conflito de aspas.
- **Corrupção de acentuação (mojibake) em todo o texto português — corrigida**:
  o ficheiro não tinha `<!DOCTYPE html>` nem `<meta charset="UTF-8">` (nem
  sequer `<html>`/`<head>` explícitos). Sem declaração de charset, a
  deteção de codificação fica dependente do servidor/browser — confirmado
  em teste real que servir o ficheiro via `python -m http.server` (um dos
  métodos mais comuns para testar localmente) corrompia todos os
  caracteres acentuados (`ção` → `Ã§Ã£o`, `—` → `â€”`). Corrigido
  adicionando `<!DOCTYPE html>` + `<meta charset="UTF-8">` no topo do
  ficheiro.
- **XSS potencial corrigido**: valores `hr`/`spo2`/`steps` vindos do bridge
  via WebSocket (canal `ws://localhost:8765`, não autenticado, sem Same-Origin
  Policy em WebSocket) eram guardados sem validação e escritos com
  `innerHTML`. Adicionada `toFiniteNumber()` — só números finitos entram em
  `liveState`.
- **Perda de dados ao redimensionar a janela corrigida**: o listener de
  `resize` chamava `renderView()`, que recria o HTML da vista inteira e
  apagava texto não submetido na textarea "Notas do cuidador" e edições em
  curso na tabela de limites de duração. Passou a só invocar os hooks
  `AFTER_RENDER` (redesenha canvases, não recria o DOM), com debounce de
  150ms.
- **Botões presos corrigidos**: se a ligação WebSocket caísse a meio de um
  pedido "Medir agora"/"Repor leituras", o botão ficava desativado
  indefinidamente (nunca chegava `command_result`). `resetPendingCommandButtons()`
  agora repõe o estado em `ws.onclose`.
- **Afirmação incorreta corrigida**: a vista "Exportar dados" descrevia a
  origem como "dump BLE cifrado (AES)" — mas o streaming BLE **não** é
  cifrado (`FullPlain`, ver secção acima). Texto corrigido; os três botões
  "Descarregar" (sem handler, mortos) passaram a `disabled` com tooltip a
  explicar que a exportação depende do serviço de persistência SQL ainda por
  construir, em vez de parecerem funcionais e falharem silenciosamente.
- **Acessibilidade**: todos os `<canvas>` de gráficos (timeline de rotina,
  heatmap, tendência, FC) ganharam `role="img"` + `aria-label` descritivo —
  antes eram invisíveis para leitores de ecrã, sem qualquer alternativa
  textual.
- **Limiares personalizados por pessoa (item 3 do backlog, protótipo)**: novo
  cartão "Limiares personalizados" na vista Definições, com alternador
  Limiares fixos (população) / Limiares personalizados. Calcula média ±
  2×desvio-padrão do histórico de 7 dias desta pessoa (FC, sono, passos) a
  partir de `trendData` e compara lado a lado com os limiares fixos atuais.
  Ver detalhe e limitações na secção "Backlog de investigação" acima.
- **Escalonamento gradual de alertas (item 6 do backlog)**: completa a
  mitigação de fadiga de alerta (silenciamento já existia). Alertas
  'warning' com 3+ ocorrências em 24h sobem automaticamente para 'serious'
  (`alertEscalation()`), com nota visível ao cuidador; silenciar reinicia a
  contagem. Nunca escala para 'critical' automaticamente. Ver detalhe na
  secção "Backlog de investigação" acima.
- **Aviso antecipado de armazenamento (2026-07-03, pedido do utilizador)**:
  antes só havia perda silenciosa de dados quando o ring buffer QSPI
  enchia (comportamento correto de "ring buffer", mas sem aviso nenhum).
  Agora `DumpStatusPacket::data_loss_flag` (byte antes sempre 0, agora
  reaproveitado — `Ble.cpp`) sinaliza 3 estados: 0=normal, 1=≥90% da
  capacidade (aviso antecipado, ainda sem perdas, dá tempo de exportar),
  2=já a substituir registos antigos não consumidos
  (`QspiRingBuffer::droppedByErase() > 0`). O firmware também regista um
  aviso único no Serial em cada transição (`[QSPIRB] AVISO: ...`, ver
  `QspiRingBuffer.cpp`). O bridge (`ble_bridge.py`) reencaminha o flag via
  WebSocket (`kind:"status"`), e o dashboard mostra uma barra de aviso
  persistente (`renderStorageWarningBanner()`) com link direto para
  "Exportar dados".
- **Registo de emergências + cancelamento com confirmação reforçada
  (2026-07-03, pedido do utilizador)**: nova vista "Registo de
  emergências" (ambos os perfis) lista eventos SOS/queda por paciente
  (`EMERGENCY_LOG`, dados de demonstração — o bridge ainda não escuta
  `emergencyAlertChar`, ver backlog). Um alerta "ativo" pode ser cancelado
  (ex.: relógio sem resposta a meio de um falso positivo), mas só depois
  de confirmação em duas etapas: palavra-passe da conta + um código de
  verificação de 6 dígitos. **Limitação honesta**: sem provedor de SMS/
  email ligado (decisão pendente do utilizador), o código é mostrado na
  própria página em vez de enviado a um segundo dispositivo — isto
  demonstra o FLUXO de confirmação reforçada exigido, não uma verificação
  real de posse de um segundo fator. Um sistema real precisaria de um
  provedor (Twilio ou semelhante) a enviar o código para o telemóvel do
  responsável.
- **Aba de Perfil editável (2026-07-03, pedido do utilizador)**: nova
  vista "Perfil" (rodapé da barra lateral, ambos os perfis) onde a pessoa
  a usar a conta atualiza os SEUS PRÓPRIOS dados (nome, email,
  instituição se for médico/técnico) — distinto dos dados clínicos do
  paciente monitorizado. Persistido em `localStorage`
  (`carewear_profile`), refletido no cartão de avatar da topbar.
- **Bug real encontrado e corrigido durante verificação de sintaxe
  (2026-07-03)**: ao instalar e correr um parser JavaScript real
  (`esprima`, via Python) sobre o `<script>` do dashboard — em vez de só
  contar chavetas/backticks, como nas verificações anteriores desta
  sessão — foi apanhado um backtick literal dentro de texto num template
  literal (`` `emergencyAlertChar` `` dentro da vista "Registo de
  emergências"), que fechava a string prematuramente. Corrigido
  (substituído por `<code>emergencyAlertChar</code>`). Nota para sessões
  futuras: a verificação de balanceamento de chavetas usada antes nesta
  sessão tinha também uma falha na extração do `<script>` (apanhava a
  primeira ocorrência literal da string "&lt;script&gt;" dentro de um
  comentário CSS, não a tag real) — corrigido para usar a última
  ocorrência. Recomenda-se `pip install esprima` + parsing real em vez de
  só contar chavetas, para verificações futuras (nota: esprima não
  reconhece `??`/`?.`, que são JS válido/suportado por todos os
  browsers — substituir por `||`/`.` só para efeitos de teste, não no
  ficheiro real).

## Modelo de Machine Learning (`ml/`)

Primeira iteração da pasta `ml/`, a implementar incrementalmente o pipeline
do artigo científico (XGBoost + LSTM Autoencoder + detetor de duração) —
ver `ml/README.md` para o detalhe completo (decisões técnicas, estrutura,
como reproduzir). Resumo:

- **Passo 1 implementado: classificador de atividades (XGBoost)**, treinado
  sobre dados 100% sintéticos (`ml/synthetic_data.py`) — não existem ainda
  dados reais rotulados de utentes. Sinal sintético gerado a 52Hz (mesma
  taxa real do IMU) para acelerómetro+giroscópio, mais FC simulada, com as
  mesmas 5 classes já usadas no dashboard (Dormir/Descanso/Atividade/
  Alimentação/Higiene).
- Features estatísticas por janela de 10s (`ml/features.py`) — abordagem
  clássica de HAR sobre acelerómetro wearable, sem FFT nesta iteração.
- XGBoost com `max_depth=3` (decisão deliberada, alinhada com a regra
  prática de TinyML já documentada abaixo, mesmo treinando só no backend
  por agora) e split de avaliação por sujeito sintético (não por janela
  aleatória, para não inflacionar a métrica).
- **Resultado da última execução: accuracy = 1.000** no conjunto de teste
  (ver `ml/reports/activity_classifier_metrics.json`). **Não é uma
  validação clínica** — as classes sintéticas foram desenhadas
  deliberadamente bem separáveis para validar a pipeline de ponta a ponta;
  dados reais serão mais ambíguos. Ver `ml/README.md` para a interpretação
  honesta completa deste número.
- **Passos 2 e 3 do artigo (LSTM Autoencoder, detetor de duração) ainda não
  implementados** — próxima iteração desta rotina.
- Dataset sintético gerado (`ml/data/*.csv`) não é versionado no git
  (regenerável de forma determinística via `python synthetic_data.py`,
  seed fixa) — só os metadados, o modelo treinado e as métricas de
  avaliação ficam no repositório.

## Deteção de emergência (código completo, por confirmar em hardware real)

**Implementado em 2026-07-03**: módulo novo `Emergency.h`/`Emergency.cpp`
(`include/Emergency/`, `src/Emergency/`), integrado em `main.cpp`
(`Emergency::begin(BTN_PIN)` no `setup()`, `Emergency::update()` a cada
iteração do `loop()`). Cobre as duas formas de alerta desenhadas:

- **SOS manual**: deteção de cliques do botão físico (`BTN_PIN`) por borda
  de descida, com contagem dentro de uma janela configurável
  (`Config::sosClickCount`/`sosClickWindowMs`, omissão 3 cliques / 1200ms),
  seguida de um período de confirmação antes de disparar
  (`sosConfirmDelayMs`, omissão 2500ms) — um novo clique durante essa
  espera cancela o SOS pendente.
- **Deteção automática**: vigia `Imu::getLatestSample().freefall` e
  `.inactivity`; ao detetar queda, arranca um temporizador de inatividade
  sustentada (`fallInactivityTimeoutMs`, omissão 60000ms); se a pessoa se
  mexer antes do fim (`inactivity` volta a `false`), cancela.
- **Alerta dual**: quando confirmado (SOS ou queda), chama
  `Ble::notifyEmergencyAlert()` (nova characteristic `emergencyAlertChar`,
  UUID `...200004`, notify+read, pacote `EmergencyAlertPacket` de 8 bytes)
  e, se `Lora::isReady()`, também `Lora::sendTest()` com uma mensagem
  distinta consoante o tipo de alerta.
- **Comando de teste por série**: `SOS` + Enter (gated por
  `DEBUG_SERIAL_WAKE`) dispara `Emergency::triggerTestAlert()` sem
  depender do gesto de cliques — útil enquanto o botão físico estiver por
  confirmar/testar fisicamente.

**Bridge → dashboard implementado (2026-07-03, rotina cloud)**: o bridge
(`ble_bridge.py`) subscreve agora `emergencyAlertChar` (`_on_emergency_alert()`,
`EMERGENCY_ALERT_STRUCT = "<BBHI"`, mesmo layout do `EmergencyAlertPacket` em
`Ble.cpp`) e reencaminha o alerta de imediato via WebSocket
(`{"kind":"emergency_alert","alert_type":...,"alert_name":"sos_manual"|
"fall_inactivity","seq":...,"timestamp_utc":...}`), sem passar pelo limite
de taxa dos registos normais. No dashboard (`web/dashboard/index.html`),
`onLiveEmergencyAlert()` regista o evento em `EMERGENCY_LOG` do paciente
selecionado (deduplicado por `seq`, marcado `live:true`), mostra uma nova
barra crítica persistente (`#emergencyLiveBanner`/`updateLiveEmergencyBanner()`)
enquanto houver alertas em direto por resolver, e a vista "Registo de
emergências" passou a distinguir "Em direto" de "Demonstração" por linha.
Testado em Playwright real (Chromium): mensagem simulada adiciona a linha e
mostra a barra, uma segunda mensagem com o mesmo `seq` não duplica, e
cancelar o alerta (fluxo de confirmação reforçada já existente) esconde a
barra — sem erros de consola. **Limitação honesta, documentada na própria
interface**: o bridge só liga a um dispositivo físico de cada vez, por isso
o alerta fica sempre atribuído ao paciente selecionado no momento em que
chega, não a uma identidade confirmada pelo hardware (mesma limitação já
registada para o seletor de paciente).

**Ainda por fazer / decidir** (não implementado de propósito, fora do meu
alcance decidir sozinho):
- O bridge reencaminha o alerta para o dashboard, mas ainda **não** notifica
  externamente por SMS/email/push — precisa de um provedor real
  (ex.: Twilio) com credenciais do utilizador.
- As regras de "cancelamento" implementadas (novo clique cancela SOS
  pendente; retomar movimento cancela vigilância de queda) são uma
  decisão de implementação minha, documentada nos comentários do código,
  ainda **não validada explicitamente pelo utilizador** — podem precisar
  de ajuste depois de testadas em hardware real.
- **Não testado em hardware real** — bloqueado pela deteção USB
  intermitente da placa (ver "Riscos/bloqueios ativos", ponto 8). Assim
  que a placa voltar a ser detetada, testar: (a) `SOS` por série dispara
  notify em `emergencyAlertChar` (visível num scanner BLE genérico), (b)
  o gesto de 3 cliques reais no botão físico funciona e é cancelável, (c)
  simular queda (agitar a placa) + ficar imóvel 60s dispara o alerta
  automático.

Decisões já tomadas com o utilizador (contexto original do desenho):

- **Canal de alerta**: ambos — (a) via bridge/telemóvel com internet
  (SMS/email/push aos contactos configurados) **e** (b) via LoRa (Wio-SX1262),
  para cobrir o caso de não haver telemóvel/bridge por perto.
- **Gesto SOS manual**: triplo clique do botão físico por omissão, mas o
  número de cliques/janela de tempo deve ser **editável**. Depois de detetado
  o gesto, NÃO envia de imediato — espera um período de confirmação
  (2–3s, editável) antes de disparar o alerta, para permitir cancelar.
- **Deteção automática (sem SOS manual)**: queda (freefall) + inatividade
  sustentada sem resposta do utilizador. Tempo de espera **60s** (não minutos),
  também editável.
- **Pinout do Wio-SX1262 — confirmado via esquemático real** (`Pulseira_Esquemático.pdf`,
  fornecido pelo utilizador, ficheiro "a729bfd4-...-030409c9d36a", datado
  2026-06-30 — placa custom, não um kit de terceiros): `VCC`→3.3V, `GND`→GND,
  `DIO1`→**D7**, `BUSY`→**D8**, `RF_SW` (controlo da antena)→**AD2** (via
  R13 10k). SPI partilhado nas redes `MISO`/`MOSI`/`SCK`.
- **Módulo `Lora.h`/`Lora.cpp` criado e integrado em `main.cpp`** (via
  RadioLib), com falha segura e não-bloqueante — testado em hardware real
  2026-07-03: `NSS=AD3` + `NRST` não controlado (`RADIOLIB_NC`) **falhou**
  (`RADIOLIB_ERR_CHIP_NOT_FOUND`, código -2), confirmando que essa hipótese
  estava errada — mas o resto do arranque (BLE/IMU/PPG/storage) continuou
  normalmente, validando o desenho "falha segura". **Próximo passo**:
  utilizador vai reenviar um recorte aproximado da zona NRST/SPI_NSS do
  esquemático para confirmar os pinos certos antes de nova tentativa.
- **Pesquisa online (2026-07-03) não encontrou pinout oficial aplicável**:
  nenhuma fonte pública (Seeed wiki, variantes Meshtastic oficiais, fórum
  Seeed) usa a mesma combinação DIO1=D7/BUSY=D8/RF_SW=AD2 — confirma que
  esta é mesmo uma placa custom sem precedente documentado publicamente.
  As variantes oficiais também usam DIO2 interno como RF switch automático
  (sem pino de MCU dedicado), diferente do design aqui (RF_SW em AD2) — a
  polaridade correta de RF_SW (ativo alto ou baixo) também não tem
  documentação pública encontrada. **Conclusão**: só o recorte do
  esquemático real resolve isto, pesquisa externa esgotada por agora.
- Ainda por decidir: quem são as "entidades competentes" concretas por
  utente (família vs serviços de emergência), e o provedor de SMS/email real
  a integrar no bridge (ex.: Twilio) — precisa de conta/credenciais do
  utilizador, não posso criar isso de forma autónoma.

## Descobertas do esquemático real da placa (`Pulseira_Esquemático.pdf`)

O utilizador partilhou o esquemático da placa custom (não é uma XIAO "nua" —
é um design próprio à volta da XIAO nRF52840 Sense Plus). Corrige/acrescenta
várias suposições anteriores:

1. **Há um módulo GPS real (CAM-M8Q, u-blox) na placa**, ligado por I2C.
   Corrige a secção "Riscos" anterior, que dizia incorretamente que não
   havia GPS no hardware — a dependência `SparkFun u-blox GNSS Arduino
   Library` já declarada em `platformio.ini` é para este módulo, ainda sem
   nenhum código a usá-lo. Isto abre a porta a geofencing/deteção de
   deambulação (wandering), uma funcionalidade validada como muito relevante
   pela investigação (ver secção seguinte).
2. **Podem existir DOIS botões físicos** (`BT1`→AD0, `BT2`→AD1 no
   esquemático), nenhum ligado ao pino que o firmware lê (`BTN_PIN` = pino
   digital 0, diferente de AD0/analógico 0 na numeração Arduino/XIAO).
   **Hipótese a confirmar com o utilizador**: o "botão partido" pode nunca
   ter estado fisicamente partido — pode estar ligado a um pino que o
   firmware nunca leu. Não alterado ainda; precisa de confirmação física.
3. **Existe um MAX32664 (hub sensor biométrico da Maxim) entre o MAX30101 e
   o resto do sistema — RISCO REAL confirmado por pesquisa (2026-07-03).**
   O firmware atual (`Ppg.cpp`) fala diretamente com o MAX30105/30101 via a
   biblioteca SparkFun (registos em bruto). Nos designs de referência da
   Maxim/SparkFun, o MAX32664 deve ser o **único mestre I2C** ligado ao
   sensor — o MCU nunca deveria "ver" o MAX30101 diretamente, só o
   protocolo de alto nível do hub. Ter os dois ligados em paralelo às
   mesmas linhas `S_SDA`/`S_SCL` (como parece ser o caso aqui) não é uma
   topologia validada e pode causar leituras corrompidas ou bloqueio do
   barramento se o hub estiver ativo ao mesmo tempo. **Confirmado pelo
   utilizador**: o MAX32664 está soldado e ligado no design (não é uma
   posição vazia) — não há confirmação se está a causar problemas.
   Funciona até agora provavelmente porque o hub está inativo/em reset
   (ver pino `IRST` no esquemático, com pull-up R8 10k e jumper JP1 — pode
   ser o controlo de reset do hub, por confirmar). **Ação recomendada**:
   verificar fisicamente se JP1 está montado/em que posição, e considerar
   isolar o hub (não alimentar ou manter em reset) até se decidir usar o
   protocolo dele em vez do acesso direto atual.
4. Também presentes no esquemático, sem uso no firmware ainda: um boost
   converter de 13V (MIC2288) e um LDO (MIC5365) — função exata por
   confirmar (possível sensor/atuador adicional não documentado no código).

## Pesquisa alargada por tema (2026-07-03, sessão interativa) — 71 pesquisas

A pedido explícito do utilizador ("mais pesquisas por tema"), esta sessão fez
71 pesquisas dirigidas (23 + 23 + 25) cobrindo praticamente todas as áreas do
projeto. Achados novos e concretos, por tema:

**Hardware/firmware:**
- **Pista nova para o mistério do LoRa**: fóruns do RadioLib mencionam que a
  versão 4.6.0 é "recomendada como versão que funciona", com versões mais
  recentes (a nossa é 7.5.0) a terem introduzido "mudanças significativas"
  que já causaram `RADIOLIB_ERR_CHIP_NOT_FOUND` a outros utilizadores mesmo
  com pinout correto. **Não aplicado nesta sessão** — fixar a versão exigiria
  confirmar que a nossa API (`Lora.cpp`) continua compatível com a 4.6.0 (o
  RadioLib mudou a assinatura de `begin()` entre versões) e depois testar em
  hardware real, que está indisponível. Registado como próximo passo
  concreto quando o hardware voltar a estar acessível — tentar ANTES de
  gastar mais tempo à procura do pino NSS certo, porque pode ser a
  biblioteca, não o pino.
- Confirmado (RF PCB design): antenas partilhadas por RF switch (o nosso
  caso, BLE+LoRa) precisam de traço de 50Ω o mais curto possível entre o
  switch e a antena — não há nada a corrigir no firmware por causa disto,
  é uma restrição de layout já fixa na placa fabricada.
- USB CDC em nRF52: bug documentado (Nordic DevZone) de paragem de
  comunicação após 3-15 min quando USB CDC e USB MSC coexistem — não é o
  nosso caso (só CDC), mas confirma que instabilidade USB intermitente é um
  problema conhecido da plataforma, não necessariamente hardware defeituoso.
- nrfutil/1200bps touch: confirmado como mecanismo frágil e bem documentado
  como origem de falhas intermitentes de upload — consistente com o que já
  observámos.
- XIAO nRF52840 Sense: bug conhecido é P0.14(D14)/P0.31 durante carregamento
  da bateria — **verificado: o nosso firmware não usa D14**, por isso não é
  a causa direta do aquecimento reportado, mas pode ainda ser relevante se o
  design custom desta placa usar esse pino para outra coisa (a confirmar no
  esquemático).

**Dashboard/UX (25 pesquisas dedicadas, a pedido do utilizador):**
- Confirmado: seletor de idioma sem bandeiras (só nomes nativos) já segue a
  boa prática (evitar bandeiras — representam países, não línguas).
- Confirmado: o stylesheet de impressão já simplifica cores corretamente
  (fundo branco, texto preto) — padrão "hide chrome, simplify colors".
- "5-9 métricas por ecrã" como limite recomendado — não auditado
  exaustivamente ecrã a ecrã nesta sessão (a maioria dos nossos cartões já
  segue isto naturalmente por estarem divididos por tema).
- Ponto vermelho simples (não numérico) no sino de notificações já segue a
  prática recomendada ("usar um ponto quando o que importa é que mudou
  algo, não quantos").
- CarePredict (concorrente direto): "por desenho, o idoso não controla o
  dashboard" — ao contrário do nosso, que dá ao Utente/Família controlo
  total sobre o seu próprio perfil/consentimento/dados. Diferenciação
  válida da nossa app, não um erro de design.
- Confirmado pela pesquisa: estados vazios devem ter CTA quando o
  utilizador pode adicionar algo — os nossos ("sem alertas", "sem
  anomalias", "sem eventos") são gerados pelo sistema, não pelo
  utilizador, por isso um CTA não se aplica (decisão consciente, não
  esquecimento).

## Verificação/debugging desta sessão (2026-07-03)

Depois de todas as alterações desta sessão, antes de avançar para testes
de comunicação com o dispositivo físico:
- **Sintaxe JS real** (parser `esprima`, não só contagem de chavetas):
  sem erros (à parte de `??`/`?.`, que o esprima de 2018 não reconhece mas
  são JS válido suportado por todos os browsers modernos).
- **Sem funções/variáveis duplicadas** em todo o `<script>`.
- **Sem IDs duplicados** em todo o HTML.
- **Build do firmware** (`pio run`): sucesso, RAM 7.6%, Flash 25.3%, sem
  avisos novos.
- Estado do git: limpo, tudo commitado e sincronizado com o GitHub.

## Backlog de investigação — funcionalidades e decisões técnicas

Duas pesquisas alargadas (~90 fontes sobre wearables/dementia care, ~70
fontes sobre IoMT/HAR/TinyML) trouxeram ideias concretas, priorizadas por
relevância. Progresso por item marcado abaixo (rotina diária de melhoria
do dashboard segue esta lista, por ordem, item a item).

**Nota desta execução (2026-07-03, rotina cloud, ordem de prioridade
atualizada pelo utilizador)**: 3 buscas dirigidas (footprint `emlearn`/
Random Forest em nRF52 — sem números concretos novos, só confirma que
MLP/Random Forest são as famílias que já se comprovam viáveis em Cortex-M4
segundo a literatura, nada que mude a recomendação já registada no estudo
de viabilidade TinyML; algoritmo de deteção de queda "ground-face
coordinate system" — mesma referência já registada anteriormente, sem
detalhe novo de implementação; mudanças de API do RadioLib 4.6.0→7.x).
Esta última levou a uma leitura da discussão
[jgromes/RadioLib#1668](https://github.com/jgromes/RadioLib/discussions/1668)
sobre nRF52840+SX1262: o caso relatado aí (BUSY a deixar de responder)
era causado por um limite de hardware do nRF52840 — só 2 periféricos SPI
conseguem estar ativos ao mesmo tempo, e um 3º SPI a ser inicializado
"empurrava" o rádio para fora. **Verificado que não se aplica aqui**: o
nosso design só tem 2 periféricos nesta categoria a usar SPI em
simultâneo (ecrã OLED SSD1351 + rádio LoRa Wio-SX1262) — a flash QSPI usa
o periférico QSPI dedicado do nRF52840, distinto dos SPIM0-3, por isso
não conta para esse limite. Não é a causa do nosso `RADIOLIB_ERR_CHIP_NOT_FOUND`
persistente. Nada de novo e diretamente acionável sem hardware físico
(continua bloqueado — ver "Riscos/bloqueios ativos", ponto 8); o trabalho
concreto desta execução foi antes no bridge (ver "Deteção de emergência"
abaixo).

**Nota desta execução (2026-07-03, rotina cloud horária)**: 3 buscas
direcionadas de pesquisa extensiva (footprint `emlearn`/Random Forest em
nRF52 — nada de novo além do já registado no estudo de viabilidade TinyML
abaixo; consentimento/partilha de dados em demência; deteção de quedas
por acelerómetro/giroscópio — "FallCNN", Frontiers 2026, direção futura
interessante mas exigiria pipeline de treino novo, não uma alteração
direta ao código existente) não trouxeram nada de novo e imediatamente
acionável além do que já é tratado no backlog abaixo. Ao começar a
implementar o item 8 (consentimento), esta rotina descobriu — via
`git fetch`/rebase, como sempre pedido antes do push — que uma sessão
interativa em paralelo já o tinha implementado e commitado entretanto
(`loadConsent()`/`setConsent()` em `web/dashboard/index.html`); o
trabalho já feito aqui para o item 8 foi descartado (nunca chegou a ser
commitado) para não duplicar, e esta execução avançou para o item 9
(lembretes de medicação) em vez disso — ver detalhe em cada item abaixo.

**Pesquisa mais aprofundada (2026-07-03, sessão interativa, pedido
explícito do utilizador de "mais pesquisas por tema")**: 8 buscas
adicionais, cobrindo break-glass access (segurança/saúde), rate-limiting
de OTP por SMS, adesão a medicação com pillboxes/wearables inteligentes,
estatísticas de não-adesão específicas de demência, resolução de
conflitos entre cuidadores familiares, HIPAA/GDPR e consentimento
granular, eficácia real de GPS/geofencing para wandering, e algoritmos de
deteção de queda por acelerómetro. Aplicações concretas:
- **Bug de segurança real corrigido** no modal de cancelamento de
  emergência: o limite de 3 tentativas antes só acrescentava uma frase ao
  aviso, mas continuava a aceitar tentativas indefinidamente — o
  "bloqueio" era cosmético. Corrigido para bloquear mesmo, e adicionado
  TTL de 5 min ao código (valor comum na indústria — Twilio/Plivo),
  alinhado com práticas reais de OTP por SMS.
- **Break-glass confirma o desenho já escolhido**: acesso de emergência
  deve ser raro/auditável/temporário, nunca um bypass de rotina — o
  registo de quem/quando em `resolvedNote` já cobre a parte de
  auditoria.
- **Estatística concreta de adesão em demência**: 17-42% de adesão
  documentada, com défice cognitivo + ausência de cuidador coabitante
  como principais fatores de risco (não "idade" isoladamente) — sugere
  que, quando o histórico real existir, os alertas de adesão deviam
  pesar mais para pacientes sem cuidador residente, não só a contagem de
  doses falhadas.
- **HIPAA/GDPR confirma o desenho de consentimento**: princípio do
  "mínimo necessário" e revogação a qualquer momento — já é o que
  `loadConsent()`/`setConsent()` implementam.
- **GPS/geofencing — evidência mista, não definitiva**: revisões
  sistemáticas recentes (2025-2026) mostram promessa mas consideram a
  evidência de benefício clínico ainda insuficiente — reforça a decisão
  já tomada de tratar o cartão de pacing/GPS como "sinal complementar",
  não uma alegação de eficácia clínica provada.
- **Deteção de queda — direção futura de firmware, não aplicada agora**:
  a pesquisa aponta um sistema de coordenadas "ground-face" (independente
  da orientação do dispositivo no corpo) como técnica real de redução de
  falsos positivos, distinta do limiar simples de aceleração usado hoje
  em `Imu.cpp`. Não implementado nesta sessão (exigiria acesso a
  hardware para validar, indisponível — ver "Riscos/bloqueios ativos");
  registado aqui como ideia concreta para quando o hardware voltar a
  estar acessível.

**Funcionalidades (por ordem de valor percebido):**
1. Explicações de anomalias em linguagem simples para a família (não só
   scores técnicos) — maior valor percebido em quase todas as fontes.
   **IMPLEMENTADO** (2026-07-03): toggle "O que significa isto?" em cada
   alerta, 7 idiomas.
2. Métrica de "curvas apertadas"/pacing via giroscópio como sinal precoce
   de deambulação (wandering), complementar ao geofencing por GPS.
   **IMPLEMENTADO** (2026-07-03, dados simulados): cartão "Padrão de
   deambulação (pacing)" na vista Rotina diária, com índice 0-100 e
   tendência de 7 dias. O cálculo real a partir de gx/gy/gz do IMU ainda
   não existe no firmware — só a visualização/conceito no dashboard.
3. Modelos personalizados por pessoa (não populacionais) — literatura mostra
   consistentemente melhor deteção de agitação/BPSD do que limiares
   genéricos. **IMPLEMENTADO (nível estatístico, protótipo) — 2026-07-03**:
   pesquisa adicional desta sessão (Iaboni et al. 2022, "Wearable multimodal
   sensors... personalized machine learning models", PMC9043905; revisões de
   "adaptive reference ranges" em monitorização remota) confirma que
   limiares fixos e iguais para todos geram mais falsos positivos/negativos
   do que limiares calculados a partir da própria linha de base da pessoa.
   Implementado em `web/dashboard/index.html` (vista Definições, cartão
   "Limiares personalizados"): calcula média ± 2×desvio-padrão do histórico
   de 7 dias desta pessoa (FC, sono, passos) e permite alternar entre limiar
   fixo (população) e limiar personalizado, com persistência em
   `localStorage` (`carewear_alert_mode`). **Não é ainda um modelo de ML
   treinado por pessoa** — isso exigiria histórico real acumulado, que só
   existirá depois do serviço de persistência (Prioridade 4 — Base de
   dados) estar construído; hoje o cálculo usa o histórico de tendência
   sintético já existente (`trendData`). É o primeiro passo honesto nessa
   direção: um limiar estatístico adaptado ao indivíduo, não um modelo de
   aprendizagem automática por pessoa.
4. Resumo noturno dedicado (tempo fora da cama, inquietação) — sundowning
   é uma preocupação distinta da atividade diurna.
5. Notas/diário do cuidador ligadas à timeline — funcionalidade mais pedida
   em todas as plataformas revistas (CarePredict, etc.), fecha o fosso entre
   dados passivos e contexto humano.
6. Desenho consciente de "fadiga de alerta": escalonamento gradual antes de
   alertas fortes (achado explícito na literatura de RPM).
   **IMPLEMENTADO (protótipo, em duas partes no mesmo dia, 2026-07-03)**:
   (a) mitigação por silenciamento — alertas com severidade `serious`/
   `warning` podem ser silenciados por 4h (`muteAlert()`/`unmuteAlert()`,
   persistido em `localStorage`); a linha fica visualmente recessiva mas
   nunca desaparece da lista. (b) escalonamento gradual — cada alerta
   'warning' tem um nº de ocorrências nas últimas 24h (`occurrences`, dado
   de exemplo nesta versão protótipo); ao atingir 3 ocorrências sem ser
   silenciado, a prioridade visual sobe automaticamente de "aviso" para
   "grave" (`alertEscalation()` em `web/dashboard/index.html`), com nota
   explícita ao cuidador e reset da contagem ao silenciar (= reconhecer o
   alerta). **Decisão de segurança não negociável, comum às duas partes**:
   alertas `critical` nunca podem ser silenciados nem gerados
   automaticamente por escalonamento — mostrado explicitamente na
   interface, não apenas omitido. **Limitação honesta**: a contagem de
   ocorrências ainda não vem de um histórico real persistido (isso só é
   possível depois do serviço de persistência da Prioridade 4); hoje é um
   valor de exemplo por alerta.
7. Exportação clínica em FHIR/PDF para a área Médico/Técnico.
   **IMPLEMENTADO (2026-07-03)**: cartão "Resumo clínico (FHIR / PDF)" na
   vista Exportar dados — `exportFhirSummary()` gera um Bundle FHIR
   simplificado (Patient + Observation por alerta) como download JSON;
   `exportClinicalPdf()` monta uma folha de impressão (`@media print`) e
   chama `window.print()` (sem bibliotecas externas, usa "Guardar como PDF"
   do próprio browser). **Diferente** da exportação de dados brutos (que
   continua bloqueada até existir BD) — cobre só o que está visível na
   sessão atual (alertas + anomalias), não o histórico completo. Nota
   honesta escrita na própria interface: não é uma implementação FHIR
   certificada, só usa a forma dos recursos.
8. Gestão de consentimento/partilha de dados (quem vê o quê) — relevante
   dado o contexto de demência (capacidade de consentimento é eticamente
   sensível, ver PMC11990963). **IMPLEMENTADO (2026-07-03, sessão
   interativa em paralelo a esta rotina)**: cartão "Consentimento e
   partilha de dados" na vista Definições (`loadConsent()`/`setConsent()`
   em `web/dashboard/index.html`) — três interruptores (sinais vitais,
   rotina, alertas/anomalias) que controlam se a equipa clínica vê essa
   informação; as vistas "Pacientes" e "Anomalias detetadas" mostram uma
   mensagem explícita em vez dos dados quando "Alertas e anomalias" está
   desligado. Persistido em localStorage (`carewear_consent`), aplica-se
   só a esta conta/sessão (protótipo sem backend). Esta entrada da lista
   não estava marcada como feita — corrigido aqui só para refletir o
   código já existente, sem alterar a implementação.
9. Lembretes de medicação + registo de adesão, correlacionado com
   atividade/vitais. **IMPLEMENTADO (protótipo, 2026-07-03, rotina
   cloud)**: nova vista "Medicação" (`data-view="medicacao"`, visível nos
   dois perfis, tal como "Registo de emergências"), com tabela de doses
   de hoje por medicamento/horário e botão "Marcar como tomado"
   (`markDoseTaken()`), guardado em `localStorage`
   (`carewear_medication_log`, namespaced por paciente + dia +
   medicamento + horário — sobrevive a recarregar a página, ao contrário
   do resto dos dados de demonstração). Estado de cada dose calculado em
   `doseStatus()`: "Tomado" / "Pendente" / "Em atraso" (>30 min após a
   hora prevista sem confirmação). Histórico de adesão dos últimos 6 dias
   com dados de exemplo por paciente (`patient.adherenceHistory`,
   claramente rotulado "simulado"). **Correlação com atividade/vitais**
   (pedida explicitamente no backlog): implementada como uma nota que
   aponta os dias com adesão incompleta para serem comparados manualmente
   com a vista "Tendência semanal" — uma correspondência simples de
   datas, não uma análise estatística automática (não fabricamos uma
   correlação numérica sem histórico real acumulado para a sustentar; ver
   Prioridade 4, Base de dados, para quando isso for possível a sério).
   **Limitação honesta**: não substitui um registo clínico de adesão real
   nem envia lembretes (push/SMS) ao dispositivo do cuidador — só regista
   o clique manual "Marcar como tomado". Verificado sem erros de consola
   em Playwright real (Chromium): 3 pacientes, marcação de dose,
   navegação nos dois perfis, sem regressões nas restantes vistas.
   **Nota de sincronização (mesma sessão interativa)**: uma implementação
   paralela e mais simples (cartão dentro de "Rotina diária", em vez de
   vista dedicada) foi feita ao mesmo tempo nesta sessão interativa —
   removida a favor desta (mais completa e já verificada), sem perda de
   trabalho porque nenhuma das duas tinha sido usada por um utilizador
   real ainda.
10. Múltiplos cuidadores/família com permissões por papel.
    **IMPLEMENTADO (2026-07-03)**: cartão "Equipa de cuidadores" em
    Definições — convidar (protótipo, sem envio real), permissões
    granulares por membro (ver alertas / editar notas e medicação), e
    remoção com **efeito imediato** (`removeCaregiver()`) — recomendação
    explícita encontrada na pesquisa (Caring Village, Jointly: um membro
    tem de poder ser removido da equipa sem demora).

**Decisões técnicas a considerar:**
- `emlearn` (já nos repositórios com estrela do utilizador) cobre árvores
  tipo scikit-learn mas **não XGBoost diretamente** — `micromlgen` suporta
  XGBoost→C nativamente e pode ser necessário como complemento.
- Regra prática para XGBoost em MCU: profundidade ≤3, ≤~4000 árvores no
  total, para caber em flash — o artigo original usa profundidade 6, pode
  exigir poda.
- Considerar `CMSIS-DSP` (extração de features/FFT) como complemento ao
  `CMSIS-NN`/`emlearn` antes da inferência.
- Considerar modelos pré-treinados (`OxWearables/ssl-wearables`,
  transfer learning) em vez de treinar do zero, dado o volume de dados
  reais ainda limitado.
- [TIHM Dataset](https://www.nature.com/articles/s41597-023-02519-y) (dados
  reais multi-sensor de demência, com eventos adversos rotulados) — possível
  fonte de validação externa para o detetor de anomalias, para além dos
  dados sintéticos do artigo.
- Arduino framework tem custo real de RAM/flash (~280KB flash/65KB RAM só
  para BLE básico, segundo uma fonte) — migração para nRF Connect
  SDK/Zephyr a considerar sobretudo se `mcuboot`/OTA (já no roadmap) avançar,
  já que mcuboot é nativo do Zephyr.

## Riscos / bloqueios ativos

1. **Botão físico de ligar/desligar — estado incerto.** Pode estar ligado a
   um pino diferente do que o firmware lê (ver "Descobertas do esquemático"
   acima), não necessariamente partido. Bypass por série (`WAKE`/`SLEEP`)
   continua ativo como paliativo até isto ser esclarecido.
2. **Sem base de dados nem backend.** O dashboard web é um protótipo de
   interface; não há ainda serviço que persista os dados numa BD SQL para
   os dados serem reais no dashboard (só os valores "ao vivo" via bridge o
   são atualmente).
3. **Sem classificador HAR embarcado.** As categorias de rotina no dashboard
   são simuladas — o pipeline do artigo (XGBoost + LSTM Autoencoder) ainda não
   foi implementado no firmware nem em nenhum serviço. Progresso: o passo 1
   (classificador XGBoost) já está implementado e treinado sobre dados
   sintéticos em `ml/` (ver secção "Modelo de Machine Learning" acima), mas
   isto é só o modelo treinado no backend/offline — continua sem estar
   embarcado nem ligado ao dashboard/firmware, e sem validação em dados
   reais.
4. **GPS presente mas sem código.** Módulo CAM-M8Q real na placa (ver acima),
   biblioteca já declarada mas nunca inicializada em `main.cpp`.
5. Reduções de stack (RAM/CPU): a 1ª ronda (2048/1024/1536/3072→1536/768/
   1152/2560 words) já tem watermarks reais de 2026-07-03 a confirmá-la
   como segura. A 2ª ronda (aplicada nesta mesma sessão, ver secção acima
   — 1536/1152/2560→768/640/1280 words) ainda **não foi confirmada** em
   hardware real — ver `DEBUG_STACK_WATERMARKS`.
6. Possível descoordenação entre o driver PPG atual (acesso direto ao
   MAX30101) e a presença de um hub MAX32664 no design — funciona, mas pode
   não ser o caminho pretendido (ver "Descobertas do esquemático").
7. **Bug encontrado e corrigido (2026-07-03): `Lora::begin()` cortava o BLE.**
   O RF switch partilhado entre a antena BLE e a antena LoRa (pino `A2`,
   `kPinRfSwitch`) era ligado incondicionalmente no início de `Lora::begin()`,
   antes de se saber se a inicialização do rádio LoRa tinha sucesso. Como o
   LoRa falha sempre nesta placa (pinout NSS ainda por confirmar — ver
   "Descobertas do esquemático"), isto cortava fisicamente a antena BLE assim
   que `initLora()` corria a seguir a `initBleDataLink()` no `setup()`,
   mesmo com a pilha BLE a reportar-se como "a anunciar" normalmente.
   Sintoma observado em hardware real: LED do BLE piscava e apagava-se
   pouco depois do arranque; `BleakScanner` (via `ble_bridge.py` e testes
   diretos) deixava de encontrar o dispositivo, nem por nome nem pelo UUID
   do serviço custom. Corrigido em `src/Lora/Lora.cpp`: o RF switch só é
   tocado (`pinMode`/`digitalWrite`) DEPOIS de `s_radio.begin()` confirmar
   sucesso — em caso de falha, o pino fica no estado por omissão e o BLE
   continua a usar a antena normalmente. **Ainda por confirmar em hardware**
   porque a porta USB da placa deixou de ser detetada pelo Windows a meio do
   reteste (ver ponto 8).
8. **Bloqueio ativo (2026-07-03): placa deixou de ser detetada por USB.**
   Depois de um upload bem-sucedido (firmware com `DEBUG_DISABLE_SLEEP=1`,
   ainda com o bug do ponto 7 por corrigir nesse build), um ciclo de
   desligar/religar USB pedido para destravar a porta série (que estava
   "pendurada" a nível do Windows) resultou na placa deixar de ser detetada
   de todo — nem porta COM, nem unidade de bootloader UF2, nem sequer um
   "dispositivo desconhecido" no Gestor de Dispositivos. Testado com cabo
   USB diferente e porta USB diferente do PC, sem qualquer reação do
   Windows (nem o som habitual de dispositivo ligado). LED verde (alimentação)
   continua aceso. Isto aponta para um problema elétrico nas linhas de
   dados USB (cabo/porta já excluídos) — pode ser o conector USB da própria
   placa. Ainda por resolver; o utilizador vai inspecionar visualmente o
   conector. **Não tentar mais uploads até a placa voltar a ser detetada.**
   **Atualização (mesmo dia, mais tarde)**: a porta voltou a aparecer
   várias vezes (COM4, depois COM6), de forma instável — surge, desaparece
   em segundos, muda de número, e mesmo quando `Open()` tem sucesso não
   chega nenhum dado pela série (nem o heartbeat "Sistema a correr..." que
   o firmware imprime a cada segundo). Isto aponta para uma ligação física
   marginal/intermitente (linha de dados USB com mau contacto — talvez o
   próprio conector da placa), não um problema de driver ou de software:
   já foi testado com cabo diferente e porta diferente do PC, sem
   melhoria. Uploads não são fiáveis nesta condição — recomenda-se
   inspecionar/reparar a ligação física antes de continuar a tentar.

## Estudo de viabilidade TinyML (atualizado 2026-07-03 com dados concretos)

Recursos livres nesta placa: ~220KB RAM, ~638KB flash (com IMU/PPG/BLE/storage
já a correr).

- Detetor de duração (regras): trivial, cabe facilmente. Sem mudanças.
- **XGBoost — CONFIRMADO INVIÁVEL tal como descrito no artigo, decisão
  técnica tomada.** Pesquisa aprofundada (2026-07-03) revelou que 400
  estimadores × 10 classes não são 400 árvores — o modo multiclasse do
  XGBoost treina uma árvore por classe por ronda de boosting, logo o
  modelo real tem **~4000 árvores internas**, não 400. Um precedente real
  publicado mostrou 500 árvores a exigir 553–727KB de flash só para caber
  — praticamente o orçamento de flash inteiro desta placa, para APENAS UM
  OITAVO do número de árvores do artigo. `micromlgen` (que suporta
  XGBoost→C) existe mas está sem manutenção (repo arquivado) e tem bugs
  documentados por resolver. **Recomendação técnica (a confirmar com o
  utilizador antes de agir)**: não portar o XGBoost tal como está —
  treinar antes um **Random Forest com ~50-100 árvores rasas (profundidade
  ≤4-5)** e usar `emlearn` (mantido ativamente, já comprovado em hardware
  nRF52 real segundo a pesquisa) como caminho principal. `micromlgen`/
  XGBoost fica só como alternativa de recurso se a precisão do Random
  Forest não for suficiente.
- LSTM Autoencoder: plausível com inferência em fluxo + quantização int8,
  mas precisa de TensorFlow Lite Micro (~20-30KB de biblioteca) ou
  `CMSIS-NN`/`CMSIS-DSP` (ARM, otimizado para este Cortex-M4F), e medição
  real (não só matemática de papel). Considerar também partir de um modelo
  pré-treinado (`OxWearables/ssl-wearables`) em vez de treinar do zero.

**Nota importante**: mudar de XGBoost para Random Forest é uma mudança
metodológica em relação ao artigo científico original do projeto — decisão
que precisa de validação do utilizador antes de se avançar com o treino,
não só uma escolha de implementação.

## Rotinas cloud agendadas (via `/schedule`)

Duas rotinas diárias às 05:00 UTC (6h em Lisboa), ambas publicam direto no
`main` (decisão do utilizador — sem PR de revisão intermédio):

1. **CareWear — Otimização diária de código** (`trig_01FavJELFcXwPXjVccGR2BoX`)
   — revê RAM/CPU/desempenho no firmware, bridge e dashboard; só altera com
   justificação mensurável, senão só regista no PROJECT_STATUS.md.
2. **CareWear — Melhoria diária do dashboard e do modelo ML**
   (`trig_01GrQtaJrqNp3Yg57qpmbmbh`) — melhora o dashboard (bugs, usabilidade,
   tarefas pendentes) e progride incrementalmente uma pasta `ml/` com o
   pipeline do artigo científico (XGBoost + LSTM Autoencoder + detetor de
   duração), documentando decisões técnicas.

Ambas instruídas a: ler este ficheiro primeiro, nunca inventar resultados/
validações não feitas, não decidir por questões que só o utilizador pode
decidir (ex.: dados reais de pacientes, credenciais de SMS/email), e manter
este ficheiro atualizado. Geridas em https://claude.ai/code/routines.

## Roadmap alargado (definido pelo utilizador, por implementar)

Wearable · Firmware · IA embarcada · App móvel (Android/iOS) · Dashboard Web ·
BD SQL · BLE · LoRa (futuro) · Armazenamento de dados · OTA · Deteção de
anomalias · Human Activity Recognition · Apoio à decisão clínica.
Migração de hardware futura possível: nRF5340 ou nRF54H20.

## Próximas tarefas (por prioridade)

1. Deteção de emergência: confirmar pinout do Wio-SX1262 (pesquisa em curso,
   ainda sem fonte fiável para os pinos SPI/CS/BUSY/DIO1/RESET do rádio) —
   módulo de firmware, characteristic BLE e reencaminhamento bridge→dashboard
   já feitos (ver secção "Deteção de emergência" acima). Falta ainda: testar
   tudo em hardware real (bloqueado pela deteção USB intermitente) e, mais
   tarde, notificar externamente por SMS/email/push (canal duplo:
   bridge/telemóvel E LoRa — decidido pelo utilizador, precisa de provedor
   com credenciais próprias).
2. Confirmar reduções de stack em hardware real (`[STACK] ...`) assim que o
   dispositivo estiver acessível via USB (tem sido intermitente nesta sessão).
3. Decidir e implementar app móvel (Android/iOS) e software desktop, a partir
   do mesmo dashboard/design system.
4. Desenhar o serviço que recebe os dumps BLE, persiste numa base de dados
   **SQL** — pré-requisito para o dashboard mostrar histórico real (routine/
   heatmap/tendência), hoje só os valores "ao vivo" via bridge são reais.
5. HAR/deteção de anomalias: portar o pipeline do artigo científico — ver
   "Estudo de viabilidade TinyML" acima; agora também progride via rotina
   cloud diária (pasta `ml/`). Passo 1 (classificador XGBoost sobre dados
   sintéticos) feito — falta LSTM Autoencoder, detetor de duração, dados
   sintéticos mais realistas, e só depois medir footprint/latência reais em
   hardware antes de decidir embarcar ou não (ver `ml/README.md`).
6. Reparar/substituir o botão físico e remover os bypasses de debug
   (`DEBUG_SERIAL_WAKE`) do firmware.
7. Decidir quem são as "entidades competentes" concretas por utente e o
   provedor de SMS/email a integrar (ex.: Twilio) — decisão do utilizador,
   precisa de conta/credenciais próprias.
