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
- **Pendente**: confirmar os valores `[STACK] ... free_words=` reais após as
  reduções de stack (device estava inacessível via USB na última tentativa).

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
  embarcado no firmware.
- Base de dados: **decidido SQL** (motor concreto — SQLite vs Postgres/MySQL —
  por decidir quando desenharmos o serviço de persistência).

## Deteção de emergência (desenhado, ainda por implementar)

Decisões já tomadas com o utilizador:

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
- Pendente: mapeamento de pinos do Wio-SX1262 para a XIAO nRF52840 (a
  variante nRF52840 usa ligação por pinos, não o conector B2B da versão
  ESP32S3 — os pinouts NÃO são iguais entre variantes). Fontes encontradas
  até agora mencionam D6/D7 para série e D30/D31 para I2C nalgumas variantes
  de terceiros (Meshtastic), mas os pinos SPI/CS/BUSY/DIO1/RESET do rádio em
  si ainda não foram confirmados numa fonte fiável — não inventar pinos sem
  confirmação, sob risco de danificar o módulo.
- Ainda por decidir: quem são as "entidades competentes" concretas por
  utente (família vs serviços de emergência), e o provedor de SMS/email real
  a integrar no bridge (ex.: Twilio) — precisa de conta/credenciais do
  utilizador, não posso criar isso de forma autónoma.

## Riscos / bloqueios ativos

1. **Botão físico de ligar/desligar partido.** Bypass por série (`WAKE`/`SLEEP`)
   é só paliativo — não substitui reparação/substituição do botão.
2. **Sem base de dados nem backend.** O dashboard web é um protótipo de
   interface; não há ainda serviço que decifre os pacotes BLE (AES) e os
   persista numa BD para os dados serem reais no dashboard.
3. **Sem classificador HAR embarcado.** As categorias de rotina no dashboard
   são simuladas — o pipeline do artigo (XGBoost + LSTM Autoencoder) ainda não
   foi implementado no firmware nem em nenhum serviço.
4. **GPS/LoRa** mencionados no roadmap alargado não estão no hardware/firmware
   atual (`SparkFun u-blox GNSS` é dependência declarada em `platformio.ini`
   mas não usada em código nenhum).
5. Reduções de stack (RAM/CPU) ainda **não confirmadas** com dados reais de
   hardware — ver `DEBUG_STACK_WATERMARKS`.

## Estudo de viabilidade TinyML (preliminar, ver conversa para detalhe)

Recursos livres nesta placa: ~220KB RAM, ~638KB flash (com IMU/PPG/BLE/storage
já a correr). Avaliação teórica (não testada/treinada ainda):
- Detetor de duração (regras): trivial, cabe facilmente.
- XGBoost (400 árvores × 10 classes ≈ 4000 árvores internamente): duvidoso
  sem poda agressiva — provavelmente não cabe tal como descrito no artigo.
- LSTM Autoencoder: plausível com inferência em fluxo + quantização int8,
  mas precisa de TensorFlow Lite Micro (~20-30KB de biblioteca) ou motor
  próprio, e medição real (não só matemática de papel).
Recomendação registada: treinar/quantizar primeiro, medir footprint/latência
reais nesta placa antes de decidir "tudo embarcado" vs "classificador no
backend" (o artigo já aponta este último como caminho comum).

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
   cloud diária (pasta `ml/`).
6. Reparar/substituir o botão físico e remover os bypasses de debug
   (`DEBUG_SERIAL_WAKE`) do firmware.
7. Decidir quem são as "entidades competentes" concretas por utente e o
   provedor de SMS/email a integrar (ex.: Twilio) — decisão do utilizador,
   precisa de conta/credenciais próprias.
