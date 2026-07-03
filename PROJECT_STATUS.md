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

**Ainda por fazer / decidir** (não implementado de propósito, fora do meu
alcance decidir sozinho):
- O bridge (`ble_bridge.py`) ainda não escuta `emergencyAlertChar` nem
  reencaminha o alerta para SMS/email/push — precisa de um provedor real
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

## Backlog de investigação — funcionalidades e decisões técnicas

Duas pesquisas alargadas (~90 fontes sobre wearables/dementia care, ~70
fontes sobre IoMT/HAR/TinyML) trouxeram ideias concretas, priorizadas por
relevância. Nenhuma implementada ainda — registo para priorização futura.

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
   genéricos.
4. Resumo noturno dedicado (tempo fora da cama, inquietação) — sundowning
   é uma preocupação distinta da atividade diurna.
5. Notas/diário do cuidador ligadas à timeline — funcionalidade mais pedida
   em todas as plataformas revistas (CarePredict, etc.), fecha o fosso entre
   dados passivos e contexto humano.
6. Desenho consciente de "fadiga de alerta": escalonamento gradual antes de
   alertas fortes (achado explícito na literatura de RPM).
7. Exportação clínica em FHIR/PDF para a área Médico/Técnico.
8. Gestão de consentimento/partilha de dados (quem vê o quê) — relevante
   dado o contexto de demência (capacidade de consentimento é eticamente
   sensível, ver PMC11990963).
9. Lembretes de medicação + registo de adesão, correlacionado com
   atividade/vitais.
10. Múltiplos cuidadores/família com permissões por papel.

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
   ainda sem fonte fiável para os pinos SPI/CS/BUSY/DIO1/RESET do rádio),
   implementar módulo de firmware (gesto SOS editável + confirmação temporizada
   + auto-deteção queda/inatividade 60s editável), estender `Ble.cpp` com uma
   characteristic de emergência, estender `ble_bridge.py` para reencaminhar o
   alerta e (mais tarde) notificar externamente (canal duplo: bridge/telemóvel
   E LoRa — decidido pelo utilizador).
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
