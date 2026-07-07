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

### Histórico: o "modo de dados" BLE não estava cifrado (RESOLVIDO 2026-07-07)

Apesar de existir troca e persistência de uma chave AES (`aesKeyChar`) desde
o início do projeto, o registo transmitido no streaming de sensores
(`FullPlain`, `src/Ble/Ble.cpp`) ia em texto simples — a biblioteca
`rweather/Crypto` já estava declarada em `platformio.ini` mas nunca tinha
sido usada. Ver secção "Cifra AES-CTR do modo de dados (2026-07-07)" mais
abaixo para a implementação que fechou esta lacuna.

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
- Dependências: `pip install -r bridge/requirements.txt` (bleak, websockets,
  pycryptodome).

### Cifra AES-CTR do "modo de dados" (2026-07-07, rotina cloud, Prioridade 1 aplicada à Prioridade 3)

Pesquisa aplicada (nRF52840 + cifra de dados BLE) confirmou que a
biblioteca `rweather/Crypto` já estava declarada em `platformio.ini` desde
o início do projeto mas nunca tinha sido usada — o registo `FullPlain` ia
pelo ar em texto simples apesar de o dispositivo já trocar/guardar uma
chave AES para outros fins (ver histórico acima). Implementado por
completo nesta execução:

- **Firmware** (`src/Ble/Ble.cpp`, `encryptRecord()`): cada `FullPlain`
  (39 bytes) é cifrado com AES-CTR (128/192/256 bits, conforme o
  comprimento da chave — 16/24/32 bytes) antes de ser fragmentado. IV de
  16 bytes = `[nonce de 32 bits][0x00000000][contador de bloco de 8
  bytes]`, `setCounterSize(8)`.
- **Nonce nunca reutilizado, mesmo depois de "Repor leituras"**: o desenho
  inicial mais óbvio (usar o `rec_seq` do ring buffer QSPI como nonce) foi
  descartado ao perceber, em revisão própria antes de commitar, que
  `QspiRingBuffer::format()` reinicia `rec_seq` em 1 mas **não** apaga a
  chave AES — reutilizar `rec_seq` como nonce repetiria o par
  (chave, nonce) depois de um "Repor leituras", quebrando a segurança do
  CTR. Usa-se em vez disso um contador de 64 bits dedicado, persistido em
  LittleFS (`Storage::counter_load()`/`counter_save()` — infraestrutura
  que já existia no ficheiro, preparada por uma sessão anterior mas nunca
  ligada a nenhuma cifra real), nunca tocado por `format()`.
- **Bug próprio encontrado e corrigido antes de commitar (revisão dirigida
  desta tarefa)**: a primeira versão obtinha um nonce novo com uma
  escrita na flash INTERNA a cada registo — até ~52/seg à taxa do IMU,
  o que esgotaria os ciclos de escrita da flash em horas/dias e
  introduzia atrasos (ms por escrita) capazes de recriar as desconexões
  BLE já resolvidas anteriormente. Corrigido para alocar nonces em
  **lotes de 65536 em RAM** (`allocateNonce()`/`reserveNonceBatch()`),
  com uma única escrita de flash a cada ~21 minutos de streaming
  contínuo — não a cada registo.
- **Bug próprio corrigido em `aesKeyCallback()`**: a validação antiga
  aceitava qualquer comprimento entre 16 e 32 bytes (ex.: 20), que não
  corresponde a nenhuma variante AES real e bloquearia
  silenciosamente todo o streaming (`encryptRecord()` falharia sempre).
  Restrito agora a exatamente 16, 24 ou 32 bytes.
- **Wire format**: `DumpDataPacket` ganhou um campo `nonce` (uint32).
  Para não crescer o pacote além do MTU BLE por omissão (nunca validado
  nesta placa), `kGattDumpChunkLen` encolheu de 12 para 8 bytes — o
  pacote continua com 20 bytes no total, mas um registo passa de 4 para 5
  fragmentos.
- **Bridge** (`bridge/ble_bridge.py`, `decrypt_full_plain()`): decifra com
  a mesma lógica de contador, implementada "à mão" com AES em modo ECB
  bloco a bloco (não um modo CTR de alto nível de alguma biblioteca), para
  controlar byte a byte a construção do bloco de contador e garantir que
  bate certo com o firmware. **Validado com um script de teste dedicado**
  (não fabricado): 600 casos aleatórios (200 por cada tamanho de chave
  16/24/32 bytes) confirmando round-trip cifra→decifra correto, mais
  verificação de que nonces diferentes produzem keystreams diferentes.
- **Limitação honesta, por resolver numa fase futura**: não existe (ainda)
  uma app de provisioning que entregue a chave AES ao bridge de forma
  automática e segura — só o dispositivo a recebe hoje. Solução desta
  fase: variável de ambiente `CAREWEAR_AES_KEY_HEX` (quem provisiona o
  dispositivo configura o bridge manualmente com a mesma chave). Sem essa
  variável, o bridge descarta os registos de sensores em vez de os
  interpretar como texto simples (o que produziria valores fabricados).
- **Não testado com o par firmware↔bridge real**: sem toolchain ARM nem
  hardware acessíveis nesta rotina cloud (ver "Riscos/bloqueios ativos"),
  o firmware não foi compilado — só revisto manualmente (sintaxe,
  balanceamento de chavetas/parênteses verificado com um script) e
  desenhado a partir do código-fonte real do `CTR.cpp`/`CTR.h` da
  biblioteca (consultado via pesquisa, não assumido de memória). O
  protocolo de cifra/decifra em si foi validado byte a byte em Python
  (round-trip determinístico), não o par firmware↔bridge real em
  hardware. Próximo passo quando a placa voltar a estar acessível:
  confirmar que o bridge consegue decifrar um registo real, com a chave
  correta configurada.
- **Bug de segurança real encontrado e corrigido (2026-07-07, rotina cloud,
  revisão dirigida à cifra AES-CTR adicionada pela execução anterior)**:
  o desenho original só envia os 32 bits BAIXOS do contador persistente de
  64 bits como nonce (campo `nonce` de `DumpDataPacket`, limitado a
  `uint32_t` para não crescer o pacote além do MTU por omissão — ver
  acima). O comentário original do código assumia que isto era
  "suficiente para nunca repetir durante vários anos de streaming
  contínuo", mas a conta exata é mais apertada do que essa frase sugere:
  a ~52 registos/seg contínuos, 2^32 registos esgotam-se em **~2.6 anos**
  — passado esse ponto, o valor truncado enviado pelo ar recomeçaria a
  **repetir os nonces usados no início da vida da mesma chave**, uma
  quebra real da confidencialidade do CTR (permite recuperar o XOR de
  dois registos diferentes cifrados com o mesmo par chave+nonce a quem
  gravar o tráfego BLE). Como este firmware não suporta rotação de chave
  (`aesKeyCallback()` rejeita qualquer escrita nova enquanto já existir
  uma chave em flash — só um apagar completo da flash interna permite
  reprovisionar), um dispositivo em campo por vários anos atingiria este
  limite sem qualquer aviso. **Corrigido em `src/Ble/Ble.cpp`**:
  `allocateNonce()`/`reserveNonceBatch()` agora recusam-se a continuar
  (falha fechada, mesmo tratamento que qualquer outra falha de reserva de
  nonce — o registo fica pendente e o streaming cifrado para) assim que o
  contador persistido ultrapassa `kMaxNonceValue` (0xFFFFFFFF), com um
  aviso único e claro no Serial (`[BLEG] AVISO CRITICO: ...`) a explicar
  que é preciso reprovisionar uma chave nova. **Não é uma correção
  completa do limite de 2.6 anos** (isso exigiria crescer o campo nonce
  ou negociar um MTU maior, uma decisão de protocolo/hardware fora do
  âmbito desta revisão pontual — ver "Estudo de viabilidade"/limitações
  do design acima), mas fecha a lacuna real: em vez de reutilizar nonces
  silenciosamente, o dispositivo agora para de enviar dados cifrados e
  avisa, dando ao responsável pelo dispositivo a oportunidade de agir
  antes de qualquer quebra de confidencialidade acontecer. Revisão feita
  por leitura direta do código (sem toolchain ARM/hardware nesta rotina,
  mesma limitação já documentada acima) + verificação de balanceamento de
  chavetas/parênteses/colchetes com um script Python sobre o ficheiro
  inteiro (124/124, 600/600, 23/23) — não testado em hardware real.
- **Limitação adicional, confirmada por pesquisa aplicada nesta revisão
  (2026-07-07)**: AES-CTR cifra mas não autentica — não há MAC/tag de
  integridade no pacote, por isso o modo escolhido protege a
  confidencialidade dos dados mas não deteta nem impede alteração/injeção
  de pacotes por alguém que consiga transmitir na mesma characteristic
  BLE (fonte: comparação com AES-GCM/AES-CCM, recomendados pelo NIST/BSI
  precisamente por incluírem autenticação; ver também RFC 3686, que impõe
  o mesmo aviso para AES-CTR em IPsec). **Não implementado nesta
  execução** — adicionar um MAC exigiria crescer ainda mais o pacote (já
  apertado a 20 bytes, ver acima) ou reduzir mais o `chunk_len`, uma
  decisão de protocolo/hardware que não me compete tomar sozinho fora de
  uma revisão pontual; registado aqui como limitação honesta e possível
  trabalho futuro, não uma falha desta revisão.

### Persistência local — SQLite (`bridge/storage.py`, 2026-07-03)

Primeira versão do item "Base de dados" do backlog. Motor concreto: SQLite
(embutido, sem servidor separado, adequado ao uso local/pessoal deste
protótipo). Ficheiro `bridge/carewear_history.db` (ignorado no git —
dados reais de cada instalação, não código nem exemplos do repositório).

- `init_db()` cria `sensor_records` (cada `FullPlain` descodificado) e
  `emergency_alerts` (cada alerta de emergência), com índice em
  `received_at` (consulta mais comum: "últimas N horas").
- `ble_bridge.py` grava automaticamente cada registo/alerta recebido,
  independentemente do rate-limit do broadcast ao dashboard — o
  histórico real não perde amostras só porque o browser não precisa de
  as ver todas ao vivo.
- Dois novos comandos WebSocket (mesmo canal `{"cmd":"..."}` dos
  comandos já existentes): `get_history` (devolve `{kind:"history",
  records, total_records, hours}`) e `export_csv` (devolve
  `{kind:"csv_export", csv, hours}` — texto CSV gerado em memória via
  `csv.DictWriter`, sem ficheiros temporários no servidor).
- **Dashboard ligado ao `export_csv`** (pedido do utilizador: "quero que
  dê para exportar os dados também em CSV... CSV dá para ser lido por
  softwares SQL"): nova vista "Dados reais (CSV)" em `TEMPLATES.exportar`,
  com botões para exportar últimas 24h / últimos 7 dias — chama
  `exportRealCsv(hours)` → `sendWsCommandWithArgs('export_csv',{hours})`
  → `handleCsvExportResult()` descarrega o CSV recebido via Blob + `<a
  download>` (mesma técnica de `exportFhirSummary()`).
- **Histórico real ligado à vista "Tendência semanal" (2026-07-03, rotina
  cloud)**: nova função `storage.get_daily_summary()` agrega os
  registos por dia (FC média + nº de leituras, extremos do contador de
  passos) diretamente em SQL — devolver os registos em bruto (como
  `get_history` já fazia) seria demasiado lento/pesado para uma janela
  de vários dias (~14-52 registos/seg). Novo comando WebSocket
  `get_daily_trend` em `ble_bridge.py` expõe isto; no dashboard, novo
  cartão "Histórico real (BD local do bridge)" na vista Tendência
  semanal, deliberadamente **separado** do gráfico sintético
  (`trendData`) já existente — nunca mistura dados reais e simulados na
  mesma série. Não inclui horas de sono (nenhuma deteção real de sono
  existe no firmware ainda). Verificado com um bridge falso (Playwright
  real): estado sem ligação, e resposta com dias com/sem leituras de FC,
  sem erros de consola.
- **Política de retenção implementada (2026-07-03, rotina cloud, reforçada
  por pesquisa aplicada desta execução)**: pesquisa sobre retenção de
  dados de saúde encontrou uma estatística concreta — "83% dos modelos
  de IA em saúde revistos violavam políticas de retenção do RGPD/GDPR,
  guardando dados de pacientes por mais tempo do que o necessário" —
  motivo direto para implementar já a limpeza automática de
  `sensor_records`, que crescia sem limite. `storage.purge_old_sensor_records()`
  apaga registos com mais de `DEFAULT_RETENTION_DAYS` (30, valor por
  omissão do protótipo, **não uma decisão de compliance certificada** —
  a retenção real de dados clínicos de um utente é uma decisão do
  utilizador/responsável pelos dados). O bridge chama isto uma vez no
  arranque e depois a cada 6h enquanto corre
  (`BleBridge.periodic_retention_task()`). `emergency_alerts`
  propositadamente **nunca** é limpo por esta política (histórico de
  segurança, mantido para sempre). Testado com uma base de dados
  temporária (registo com 40 dias é apagado, registo com 1 dia e o
  alerta de emergência sobrevivem).
- **Retenção configurável pelo utilizador (2026-07-04, rotina cloud)**:
  `DEFAULT_RETENTION_DAYS` deixou de ser a única fonte de verdade — nova
  tabela `settings` (par chave/valor) em `bridge/storage.py`
  (`get_retention_days()`/`set_retention_days()`, limites de sanidade
  1-3650 dias) guarda um valor efetivo, editável pelo utilizador. Dois
  novos comandos WebSocket em `ble_bridge.py` (`get_retention_days`/
  `set_retention_days`); `periodic_retention_task()` lê o valor a cada
  ciclo (já não uma constante), por isso uma alteração feita a meio da
  execução tem efeito sem reiniciar o bridge. No dashboard, novo cartão
  "Retenção de dados locais (BD do bridge)" na vista "Exportar dados"
  (Médico/Técnico) — mostra o valor atual (pedido ao bridge ao abrir a
  vista), permite editar e guardar, com aviso claro de que **não é uma
  política de retenção certificada** (decisão real continua do
  utilizador/responsável pelos dados, só deixou de estar fixa no
  código-fonte). Testado com um bridge falso (Playwright real): valor
  inicial correto, gravação e persistência entre reaberturas da vista,
  rejeição de valor inválido (0), sem regressões nos botões vizinhos
  (CSV/FHIR) nem erros de consola.
- **Ainda não feito**: cifra do `.db` se este serviço vier a correr fora
  de um ambiente de desenvolvimento local confiável.

### Base de Dados SQL Completa — SQLAlchemy ORM (`bridge/storage_advanced.py`, 2026-07-04)

Refatoração da camada de persistência com ORM e schema produção-ready:

**Ficheiros novos**:
- `bridge/schema.sql` — Referência SQL completa (13 tabelas, índices, constraints, comentários PT)
- `bridge/storage_advanced.py` — ORM SQLAlchemy com 14 modelos (User, Patient, Device, SensorRecord, Medication, MedicationAdherence, Alert, EmergencyAlert, AnomalyDetection, PersonalizedThreshold, DailyStatistics, AuditLog, ConsentRecord, ActivityWindow)
- `bridge/requirements_db.txt` — Dependências (sqlalchemy, alembic, cryptography, twilio, pydantic)

**Tabelas principais**:
1. **users** (família, clínico, admin) — email único, role, instituição/cédula profissional
2. **patients** — nome, DOB, NIF/morada encriptados, contacto de emergência
3. **devices** — MAC BLE, firmware version, battery, storage, last sync
4. **sensor_records** — timestamp, accel/gyro (6 eixos), steps, freefall, inactivity, HR, SpO2, pacing_index — **índices em device_id+timestamp para queries rápidas**
5. **medications** — prescrição (nome, dosage, frequency, prescribed_by_user)
6. **medication_adherence** — histórico (taken/not_taken, timestamp, método: manual/wearable/AI)
7. **alerts** — anomalias (type, severity, raw_data, read_at, silenced_until, escalated_at, resolved_at, resolution_note)
8. **emergency_alerts** — SOS/queda (sequence_number dedup, timestamp_utc, response_user, response_action, confirmation_code OTP, blocked_until TTL)
9. **anomaly_detections** — LSTM Autoencoder (type, score, start/end datetime, investigated)
10. **personalized_thresholds** — limites por pessoa (FC min/max, SpO2 min, sleep/activity targets, steps daily)
11. **daily_statistics** — cache (total_steps, avg/min/max HR, avg SpO2, durations por atividade, alerts/anomalies count, adherence_percent) — **atualizado incrementalmente para dashboard rápido**
12. **consent_records** — GDPR/HIPAA (patient, scope, granted, version, signed_at, expires_at)
13. **audit_log** — auditoria (user, action, resource_type/id, details JSONB, ip_address, created_at) — **para compliance e forensics**
14. **activity_windows** — agregadas (date, category, start/end minutos, duration, confidence)

**Queries analíticas** (classe `Analytics`):
- `heart_rate_trends(device_id, days)` — tendência FC com avg/min/max + série completa
- `medication_adherence_summary(patient_id, days)` — resumo por medicamento (taken%, overall%)
- `daily_activity_distribution(device_id, date)` — distribuição do dia (sleep/rest/activity/eating/hygiene com durations e ocorrências)

**Políticas de retenção automática** (classe `DataRetention`):
- `sensor_records` — 365 dias (apaga mesmo, não soft delete — volume crescente)
- `activity_windows` — 1825 dias (5 anos)
- `alerts` — 2555 dias (7 anos, soft delete com `deleted_at`)
- `emergency_alerts` — 3650 dias (10 anos, **nunca apagado automaticamente**)
- `anomaly_detections` — 1825 dias (5 anos)
- `medication_adherence` — 1095 dias (3 anos)
- Método `cleanup(db, dry_run)` para chamar manualmente ou via scheduler (ex.: cron nightly)

**Segurança/Compliance**:
- Modelos com `deleted_at` (soft delete) para auditoria em `audit_log`
- `ConsentRecord` para rastreabilidade GDPR (patient, scope, version, signed_at, expires_at)
- Campos sensíveis (NIF, morada) marcados como `_encrypted` — cifra real a implementar com `cryptography` (AES + salt)
- `AuditLog` JSONB com details completos (valores antigos/novos para comparar)
- IP address registado em cada ação sensível

**Motor de BD**:
- **SQLite em desenvolvimento** (`sqlite:///./carewear.db`) — embutido, sem servidor
- **PostgreSQL em produção** (via `DATABASE_URL` env var) — suporta JSONB nativo, constraints mais fortes, pool connection
- Migrations via **Alembic** (próxima fase — criará versões incrementais do schema)

**Próximas fases**:
- Integração Twilio para SMS/email (alertas críticos, OTP confirmação emergência)
- Alembic migrations (script versionado para evolução do schema)
- Endpoints REST/GraphQL para dashboard (ligar queries analíticas a tempo real)
- Cifra real dos campos sensíveis (derivação de chave com argon2)
- Testes unitários com pytest (fixtures, mocks BLE)

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
- **Passo 2 implementado: LSTM Autoencoder para deteção de anomalias
  (2026-07-03)** — `ml/synthetic_sequences.py` (sequências diárias
  sintéticas com 3 tipos de anomalia injetada: duração prolongada,
  substituição contextual noturna, truncamento) + `ml/train_lstm_autoencoder.py`.
  **Achado honesto**: AUC-ROC 0.74-0.91 por tipo de anomalia (o modelo
  ordena corretamente anómalo vs. normal bem acima do acaso nos 3 tipos),
  mas o recall a um limiar único fixo (percentil 95) é fraco para as duas
  anomalias baseadas em duração — porque prolongar/encurtar um bloco não
  muda o sinal estatístico dentro dele, só a duração total, algo que uma
  janela de 2 minutos não vê sozinha. Isto reforça, com evidência
  concreta, a necessidade do passo 3 (detetor de duração) em vez de ser
  um bug a corrigir — ver `ml/README.md`, secção "Passo 2", para o
  detalhe completo e as limitações honestas (limiar único global,
  100% sintético, não embarcado/medido em hardware).
- **Passo 3 do artigo (detetor de duração baseado em regras) implementado
  (2026-07-04, rotina cloud)** — `ml/duration_detector.py`, motivado
  diretamente pelo achado do passo 2 (recall fraco do LSTM Autoencoder para
  anomalias de duração). É uma regra determinística (não treinada): compara
  a duração de cada bloco classificado com `[d_min, d_max]` da classe+sessão
  (os mesmos parâmetros do gerador sintético, `DAY_BLOCK_MINUTES`/
  `NIGHT_BLOCK_MINUTES`) e sinaliza também classes inesperadas para a sessão
  (ex.: "Atividade" de noite). **Resultado**: recall 0.972-1.000 nos 3 tipos
  de anomalia (vs. 0.000-0.331 do LSTM Autoencoder para os mesmos tipos) —
  confirma com números concretos a complementaridade dos dois detetores.
  **Achado honesto**: 7.17% de falsos positivos em blocos normais, mas
  100% desses (154/154, medido diretamente) vêm do último bloco de cada
  sessão sintética, cortado pelo gerador para a sessão somar exatamente o
  total de minutos configurado — artefacto do gerador, não uma anomalia
  real nem falha da regra; não valida especificidade em dados reais. Ver
  `ml/README.md`, secção "Passo 3", para o detalhe completo. Não embarcado
  no firmware (depende do classificador do passo 1 estar embarcado
  primeiro, o que ainda não aconteceu).
- **Footprint do Random Forest (alternativa TinyML ao XGBoost) medido de
  facto (2026-07-03)**, compilando o C gerado pelo `emlearn` com o
  toolchain ARM real (`ml/measure_rf_footprint.py`): flash não é problema
  (~4,7-19KB de ~638KB livres), mas a quantização `int16_t` por omissão
  do `emlearn` reduz a accuracy de 0.978 para 0.789 — ver "Estudo de
  viabilidade TinyML" abaixo e `ml/README.md` para o detalhe e as vias
  possíveis daqui para a frente.
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

**Nota desta execução (2026-07-04, rotina cloud)**: pesquisa dirigida sobre
o "ground-face coordinate system" para deteção de queda (referido acima
como direção futura não implementada) e sobre anomalias comportamentais em
demência não trouxe nada de novo diretamente acionável — o artigo que
descreve a técnica em detalhe está bloqueado por paywall (403), e as
fontes livres só confirmam a técnica genérica já conhecida (filtro
passa-baixo para extrair o vetor gravidade, sem a fórmula exata).
Decisão consciente: não implementar uma versão adivinhada de um algoritmo
de deteção de queda (código de segurança) sem confiança na fonte. Com o
backlog do dashboard (itens 1-3, 6-10) e os itens específicos de
firmware/bridge/ML da Prioridade 3 todos concluídos, esta execução avançou
antes para a Prioridade 4 (Base de dados) — ver "Retenção configurável
pelo utilizador" na secção "Persistência local — SQLite" acima.

**Nota desta execução (2026-07-04, rotina cloud, mesmo dia — 2ª passagem)**:
com o backlog do dashboard e a Prioridade 3 (bridge/emlearn) confirmados
concluídos, e a Prioridade 4 (BD SQLite) já com um protótipo funcional
(persistência + CSV + retenção configurável), esta execução verificou o
roteiro do pipeline de ML (`ml/README.md`, "Próximos passos") e encontrou
aí trabalho concreto ainda por fazer, não coberto pela redação literal da
Prioridade 3 mas claramente dentro do seu âmbito ("Firmware, bridge e ML"):
o passo 3 do artigo científico (detetor de duração baseado em regras),
sinalizado no próprio README como próximo passo natural depois do achado
do passo 2. Implementado e avaliado — ver acima. Não foi tocada a base de
dados nesta passagem (Prioridade 4 já tem um protótipo funcional e o
roteiro de ML tinha um item concreto mais avançado na ordem de prioridade).

**Nota desta execução (2026-07-07, rotina cloud)**: com o backlog do
dashboard (itens 1-10), a Prioridade 3 nomeada (bridge↔`emergencyAlertChar`,
footprint real do Random Forest) e um protótipo funcional de Prioridade 4
(BD SQLite) todos já confirmados concluídos por execuções anteriores, e
com uma execução paralela tendo aplicado a Prioridade 1 diretamente à
Prioridade 3 horas antes desta (cifra AES-CTR do "modo de dados", ver
secção acima) — sem ainda ter sido revista por ninguém, e explicitamente
marcada "não compilada nem testada em hardware" pela própria execução que
a escreveu — esta execução decidiu que a ação de maior valor concreto
disponível era rever essa mudança em vez de abrir uma frente nova
(consistente com a "REGRA NOVA": revisão dirigida ao que foi alterado
antes de seguir em frente, mesmo quando a alteração não foi desta
execução). Duas pesquisas dirigidas feitas primeiro (Prioridade 1):
(a) deteção de quedas/anomalias comportamentais em demência — nada de
concretamente novo e acionável além do já registado (um estudo de 2026
sobre deteção de quedas com sensores ultrassónicos + RNN/LSTM híbrido
atinge 98.14%, mas usa uma modalidade de sensor — ultrassom ambiente —
diferente do acelerómetro wearable deste projeto, não diretamente
aplicável sem hardware novo); (b) boas práticas de exaustão de nonce
AES-CTR em IoT — confirmou que a mitigação aplicada (falha fechada ao
esgotar o espaço de nonces) é a abordagem correta para quem não pode
mudar de modo de cifra, e revelou uma limitação adicional (CTR não
autentica, sem MAC) documentada honestamente acima, não implementada
(cresceria o pacote outra vez). A revisão em si encontrou e corrigiu um
bug de segurança real (nonce de 32 bits pode repetir ao fim de ~2.6 anos
de streaming contínuo, silenciosamente) — ver "Cifra AES-CTR do 'modo de
dados'" acima para o detalhe completo. Nada mais de concreto ficou por
fazer nas Prioridades 1-4 nesta execução além desta revisão — a
Prioridade 5 (varredura completa de bugs) não chegou a ser necessária
porque esta revisão dirigida já tinha trabalho real e concreto disponível
dentro do âmbito da Prioridade 3.

**Funcionalidades (por ordem de valor percebido):**
1. Explicações de anomalias em linguagem simples para a família (não só
   scores técnicos) — maior valor percebido em quase todas as fontes.
   **IMPLEMENTADO** (2026-07-03): toggle "O que significa isto?" em cada
   alerta, 7 idiomas.
2. Métrica de "curvas apertadas"/pacing via giroscópio como sinal precoce
   de deambulação (wandering), complementar ao geofencing por GPS.
   **IMPLEMENTADO (2026-07-03, dados simulados)**: cartão "Padrão de
   deambulação (pacing)" na vista Rotina diária, com índice 0-100 e
   tendência de 7 dias.
   **Cálculo real implementado no firmware (2026-07-03, rotina cloud)**:
   `Imu::detectPacing()` (novo, `src/Imu/Imu.cpp`) conta "curvas
   apertadas" — rajadas da NORMA do giroscópio (`sqrt(gx²+gy²+gz²)`,
   escolhida em vez de um único eixo por não haver orientação fixa do
   dispositivo no pulso) acima de um limiar heurístico (45 dps, com
   histerese de rearmamento a 15 dps e mínimo de 5 amostras consecutivas
   — mesmo padrão rise/rearm já usado em `detectStep()`), acumuladas numa
   janela deslizante de 1 minuto e convertidas num índice 0-100 (12+
   curvas/min → índice 100). Novo campo `Imu::Sample::pacing_index`
   (uint8_t), propagado por todo o pipeline: `storageTask` (`main.cpp`,
   `ImuPpgPayloadV1` cresce 34→35 bytes, ainda bem dentro dos 44 bytes de
   `QspiRingBuffer::kPayloadSize`) → `Ble::mapRingRecordToFull` →
   `FullPlain` (`Ble.cpp`, **cresce de 38 para 39 bytes** — não havia
   bytes reservados livres para reaproveitar desta vez, ao contrário do
   que aconteceu com `data_loss_flag`; `static_assert` atualizado para
   39, `FULL_PLAIN_STRUCT`/`decode_full_plain()` em `bridge/ble_bridge.py`
   atualizados em conjunto para `"<IffffffIBBhhB"`) → dashboard
   (`liveState.pacing`, preenchido em `handleBridgeMessage()`;
   `renderPacingSummary()` mostra este valor real como "hoje — ao vivo"
   quando o bridge está ligado, mantendo a tendência de 7 dias simulada —
   badge do cartão ajustado de "dados simulados" para "tendência
   simulada" para refletir isto com precisão). **Limitações honestas,
   documentadas em comentário no próprio `Imu.cpp`**: (a) é um sinal
   complementar, não uma deteção de wandering validada clinicamente — a
   evidência desta família de sinais é ainda mista segundo a pesquisa já
   registada nesta secção; (b) os limiares (45/15 dps, 12 curvas/min para
   índice máximo) são heurísticas desta primeira iteração, por afinar
   quando houver dados reais de uso — não há ainda histórico real de
   wandering confirmado para calibrar contra ele; (c) usar a norma total
   do giroscópio (em vez de um eixo fixo) não distingue rotação do
   próprio pulso/braço de uma curva real do corpo a andar. **Não testado
   em hardware real** — bloqueado pela indisponibilidade atual da placa
   (ver "Riscos/bloqueios ativos", ponto 8); sintaxe C++ revista
   manualmente (sem toolchain ARM disponível nesta rotina cloud — ver
   nota abaixo) e sintaxe JS do dashboard confirmada com `node --check`
   sobre o `<script>` extraído (parser real, não só contagem de
   chavetas).
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
   é uma preocupação distinta da atividade diurna. **IMPLEMENTADO**: cartão
   "Resumo noturno" na vista Resumo (`renderNightSummary()`,
   `web/dashboard/index.html`) — dados simulados a partir do bloco
   "dormir" da rotina (badge "dados simulados" visível). Esta entrada não
   estava marcada como feita — corrigido aqui só para refletir o código
   já existente (implementado numa sessão anterior), sem alterar nada.
5. Notas/diário do cuidador ligadas à timeline — funcionalidade mais pedida
   em todas as plataformas revistas (CarePredict, etc.), fecha o fosso entre
   dados passivos e contexto humano. **IMPLEMENTADO**: cartão "Notas do
   cuidador" na vista Rotina diária (`addCaregiverNote()`/
   `renderCaregiverNotes()`), persistido em localStorage
   (`carewear_caregiver_notes`) — protótipo sem base de dados. Idem nota
   acima: código já existia, só a marcação aqui estava desatualizada.
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
  **Atualização (2026-07-03, rotina cloud) — footprint do Random Forest
  medido de facto** (`ml/measure_rf_footprint.py`, compilado com o
  toolchain ARM real para Cortex-M4F, não estimativa): ~4,7-19KB de flash
  consoante a variante — uma fração ínfima dos ~638KB livres, confirmando
  que o flash deixou de ser o fator limitante para este modelo (80
  árvores, profundidade 5). **Mas revelou um problema novo, não previsto**:
  a quantização `int16_t` usada por omissão pelo `emlearn` reduz a
  accuracy de 0.978 para 0.789 (várias features, como a correlação entre
  eixos, ficam entre -1 e 1 e colapsam para 0 quando truncadas sem
  escala). Usar `dtype='float'` no `emlearn` recupera a accuracy original
  (0.978) a troco de mais flash (~19KB, continua a caber facilmente). Ver
  `ml/README.md`, secção "Footprint real medido via emlearn", para o
  detalhe completo e as duas vias possíveis daqui para a frente.
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

## Lembretes de Medicação — Sistema Front-End (2026-07-04)

**Ficheiro novo: `web/dashboard/medication-reminders.js`** (339 linhas)

Implementação de um sistema de lembretes de medicação baseado em Browser
Notifications API, integrado com a interface de medicações já existente no
dashboard. Modularizado em duas classes principais:

### Classe `MedicationReminder`

- **Polling**: verificação a cada 5 minutos (configurável) de medicações
  agendadas para o paciente selecionado atualmente.
- **Janela de lembrete**: 30 minutos antes e depois da hora prevista (passa a
  notificação quando entra nesse intervalo).
- **Notificações nativas**: usa `Notification.API` do browser (requer
  permissão do utilizador). Apresenta título, corpo com nome do medicamento +
  dose + horário, e ações:
  - ✓ Marcar como tomada (fecha notificação, chama `markDoseTaken()`)
  - ⏰ Adiar 5 minutos (re-agenda notificação, permite "snooze")
  - ✗ Descartar (fecha sem registar)
- **Fallback**: se o browser não suportar Notificações ou o utilizador não
  consentir, mostra banner flutuante no topo da página (position:fixed,
  upper-right) com os mesmos dados e botão "Tomei agora", com
  auto-fecho após 30 segundos.
- **Deduplica**çao**: usa um `Set` (chave: patientId_medId_time) para evitar
  notificações duplicadas da mesma dose até à meia-noite (quando se limpa).
- **Inicialização automática**: injeções CSS + instância global no evento
  `DOMContentLoaded`, chamando imediatamente `start()` para não esperar 5
  minutos até à primeira verificação.

### Classe `AdherenceAnalytics`

- **Registo por dia**: método `recordDay(patientId, adherence_pct, activityLevel,
  hrAvg)` guarda num localStorage namespaced (`carewear_adherence_analytics_*`)
  o percentual de adesão diária + contexto (atividade/vitais, quando
  disponível).
- **Análise de 7 dias** (`getWeekSummary(patientId)`): devolve:
  - `avg_adherence`: média de adesão dos últimos 7 dias (com histórico).
  - `patterns`: string com padrão detetado (ex.: "Possível correlação:
    menor adesão em dias mais ativos").
  - `alert`: recomendação por severidade (🔴 crítica, 🟡 moderada, 🟢 ótima).
  - `entries`: array com todas as entradas ([date, adherence_pct,
    activity_level, hr_avg]).
- **Recomendações** (`getRecommendations(patientId, patient)`): gera lista de
  dicas baseadas no padrão semanal (ex.: "agendar medicação em períodos de
  menor atividade" se há correlação óbvia).

### Integração com código existente

- Usa `patientMedications(patient)` (já existente) para obter lista de
  medicamentos ativos.
- Usa `isDoseTakenToday()` e `markDoseTaken()` (já existentes) para estado de
  adesão de hoje.
- Chama `getCurrentPatient()` (dever ser implementada — retorna paciente
  selecionado atualmente) ou fallback a `selectedPatient()` se a função não
  existir (para compatibilidade com dashboard utente, onde não há multi-
  paciente).
- Compatível com localStorage já existente (`carewear_medication_log` para
  registro de doses de hoje).

### Integração no dashboard concluída (2026-07-07, rotina cloud)

O ficheiro tinha ficado órfão desde 2026-07-04 — escrito e revisto, mas nunca
incluído em `index.html`, por isso não corria de todo (item explícito na
lista "Próximas etapas de integração" abaixo, agora resolvida):

1. **`<script src="medication-reminders.js"></script>` adicionado no fim de
   `index.html`** (depois do `<script>` principal, para que
   `selectedPatient()`/`patientMedications()`/`isDoseTakenToday()`/
   `markDoseTaken()` já estejam definidas quando `DOMContentLoaded` corre).
   Isto ativa o sistema automaticamente, tal como planeado.
2. **Bug real encontrado e corrigido antes de ativar** (revisão dirigida a
   esta alteração): `checkAndNotify()` fazia `getCurrentPatient ?
   getCurrentPatient() : null` — uma referência nua a um identificador nunca
   declarado em lado nenhum (`getCurrentPatient()` não existe em
   `index.html`, ao contrário do que o comentário do ficheiro assumia).
   Isto lança sempre `ReferenceError` em JavaScript (diferente de aceder a
   uma propriedade indefinida), o que travaria o sistema de lembretes
   assim que `start()` corresse — nunca chegaria a mostrar uma notificação.
   Corrigido para `typeof getCurrentPatient === 'function' ? ... :
   typeof selectedPatient === 'function' ? selectedPatient() : null`,
   implementando o *fallback* a `selectedPatient()` que já estava
   documentado como intenção mas nunca escrito no código. Mesmo tratamento
   aplicado por consistência a `patientMedications`/`isDoseTakenToday`/
   `markDoseTaken` (que já existiam e não estavam a falhar, mas ficam
   protegidas de futuras reordenações de scripts).
3. **Verificado em Playwright real (Chromium)**: página carrega sem erros de
   consola (só o aviso esperado de WebSocket recusado, sem bridge a
   correr); login como Utente/Família, vista "Medicação" mostra a tabela de
   doses normalmente, "Marcar como tomado" continua a funcionar sem
   regressões; `console.log` confirma "Sistema de lembretes ativo" sem
   exceções.
4. **Ainda por fazer** (fora do âmbito desta correção pontual, já registado
   antes): UI dedicada ao resumo semanal de `AdherenceAnalytics`
   (`getWeekSummary()`/`getRecommendations()` continuam por chamar — a
   classe está instanciada em `window.adherenceAnalytics` mas nada ainda
   invoca `recordDay()`, por isso não acumula histórico próprio); ligação a
   `storage_advanced.py`; envio real de SMS/email/push (Twilio, decisão
   pendente do utilizador).

### Validação & testes

- Sintaxe JavaScript: `node --check` sobre `medication-reminders.js` e sobre
  o `<script>` principal extraído de `index.html` — ambos sem erros.
- Testado em Playwright real (Chromium), ver ponto 3 acima.

## Roadmap alargado (definido pelo utilizador, por implementar)

Wearable · Firmware · IA embarcada · App móvel (Android/iOS) · Dashboard Web ·
BD SQL · BLE · LoRa (futuro) · Armazenamento de dados · OTA · Deteção de
anomalias · Human Activity Recognition · Apoio à decisão clínica.
Migração de hardware futura possível: nRF5340 ou nRF54H20.

## Próximas tarefas (por prioridade)

0. **ORDEM DE TESTES DE HARDWARE, confirmada pelo utilizador (2026-07-03)**:
   quando a placa voltar a estar ligada, testar por ordem de simplicidade
   crescente, NÃO começar pelo LoRa (o utilizador pediu explicitamente
   para não começar por aí). Primeiro confirmar que o básico continua a
   funcionar (upload do firmware principal + leitura série normal, já
   testado nesta sessão com sucesso — ver "Verificado em hardware real"),
   só depois avançar para testes isolados mais arriscados/novos (ver
   `src/test_lora_isolated.cpp` + ambiente `test_lora_isolated` em
   `platformio.ini`, preparado mas ainda por correr em hardware — testa
   os parâmetros do LoRa um a um, com leitura SPI em bruto, antes do
   RadioLib completo). Esta ordem existe precisamente para isolar
   problemas — não saltar para o código mais complexo primeiro.

   **Sessão de hardware de 2026-07-03 (placa ligada, depois desconectada
   pelo utilizador)**: seguida a ordem acima.
   - Leitura série do firmware já instalado: OK, heartbeat BLE estável
     (`[BLE] wait TIME... adv=1 connected=0`) durante 45s sem cortes.
   - Build + upload do firmware principal via `pio run -t upload
     --upload-port COM6`: **sucesso** (32.55s, RAM/Flash sem alterações
     face à última verificação).
   - **Depois do upload**: o dispositivo deixou de responder — sem
     output série, LED azul apagado, bridge (`ble_bridge.py`) a correr
     e à escuta em `ws://localhost:8765` mas incapaz de encontrar o
     dispositivo "Wearable" por BLE mesmo após repetidos scans (~35s).
     Diagnóstico apontado ao utilizador: o firmware não arrancou/ficou
     preso após o DFU (diferente da instabilidade de porta USB já
     conhecida — desta vez a porta ficou visível e estável, só o
     firmware é que parece "sem vida"). Pedido feito ao utilizador para
     premir o botão de reset físico, mas a placa foi desconectada antes
     de se confirmar.
   - **NÃO chegou a testar-se `test_lora_isolated`** — ficou por fazer
     por este bloqueio anterior, não por ter sido saltado deliberadamente.
   - Próximo passo, quando a placa voltar a ligar-se: confirmar primeiro
     se um reset físico resolve (LED azul + heartbeat série voltam);
     se não resolver, pode ser sintoma novo a investigar antes de
     avançar para qualquer teste LoRa.

   **Continuação da sessão de hardware, mesmo dia (placa reconectada)**:
   - **Causa real do "dispositivo sem vida" identificada**: não era um
     crash nem instabilidade nova. O commit `d053442` tinha mudado
     `DEBUG_DISABLE_SLEEP` de 1 para 0 (só para testar uma hipótese sobre
     a instabilidade USB, já descartada). Com o valor a 0, o firmware
     exige um long-press do botão físico (partido/desligado) OU o
     comando série "WAKE" dentro de 8s do arranque, senão adormece de
     propósito (`goToSleep()`, SYSTEM OFF) — LED apaga, USB suspende.
     Como a abertura do monitor série demorava sempre mais de 8s, o
     dispositivo adormecia sempre antes de conseguirmos ler nada.
     Confirmado enviando "WAKE" a tempo: o arranque prosseguiu
     normalmente. **Reposto `DEBUG_DISABLE_SLEEP` a 1** (`src/main.cpp`),
     como o próprio commit já prometia fazer se o teste não mostrasse
     melhoria — recompilado e reinstalado com sucesso.
   - Depois deste ajuste, o toque a 1200bps (usado pelo PlatformIO antes
     de um upload) confirmou que o circuito de reset/bootloader funciona
     bem (entra em bootloader em COM4 de forma fiável).
   - **Teste isolado do LoRa (`test_lora_isolated`) instalado e a
     correr** — mas ainda sem leitura série capturada dos seus 3 testes
     (TESTE 1/2/3 do ficheiro); reposto o firmware principal logo a
     seguir a pedido do utilizador, para poder validar a ligação real ao
     bridge/dashboard primeiro. **Continua por confirmar em hardware.**
   - **BLE confirmado funcional de ponta a ponta**: firmware principal
     reinstalado, bridge (`ble_bridge.py`) ligou automaticamente
     (`encontrado E6:ED:42:57:1F:20 — a ligar...`), pediu o "start" e
     recebeu registos IMU em tempo real (~13/s, confirmado via
     `bridge/storage.py::count_records()` — 78 registos novos em 6s).
   - **Bug/limitação real encontrada — HR nunca chega a ser lido**:
     com a placa mesmo no pulso do utilizador e o botão "Medir agora"
     (`force_reading` → `Ppg::requestManualHr()`) usado várias vezes,
     **nenhum registo em toda a base de dados tem HR preenchido** (`SELECT
     COUNT(*) FROM sensor_records WHERE hr IS NOT NULL` → 0). SpO2 só
     tem 1 registo válido (99%) em toda a história, de uma sessão
     anterior. O caminho de código (`requestManualHr()` →
     `g_manualHrDeadlineMs` → `wantHr` → `startHrStreaming()` →
     `processHrSample()` em `src/Ppg/Ppg.cpp`) parece arquiteturalmente
     correto — falta confirmar se `processHrSample()`/`computeBPM()`
     alguma vez deteta um batimento válido (`bpm > 30 && bpm < 200`) com
     o sensor real, ou se fica sempre a filtrar tudo como inválido. Só
     dá para diagnosticar com leitura série ao vivo durante um pedido de
     "Medir agora" (prints `[PPG] HR beat -> ...` esperados em
     `Ppg.cpp:626-629`, nunca vistos ainda) — **fica como próximo passo
     de hardware**, não implementado/corrigido nesta sessão.
   - **Suspeita concreta encontrada e corrigida por pesquisa aplicada
     (2026-07-04, rotina cloud, Prioridade 1)**: `setupForHr()`
     (`src/Ppg/Ppg.cpp`) configurava `sampleAverage=8` com
     `sampleRate=100`. Confirmado no datasheet Maxim/SparkFun e no
     código-fonte da `SparkFun_MAX3010x_Sensor_Library`
     ([MAX30105.cpp](https://github.com/sparkfun/SparkFun_MAX3010x_Sensor_Library/blob/master/src/MAX30105.cpp),
     [hookup guide](https://learn.sparkfun.com/tutorials/max30105-particle-and-pulse-ox-sensor-hookup-guide/all)):
     o registo `SMP_AVE` da FIFO faz média de N amostras do ADC por
     **cada** entrada nova na FIFO, dividindo a taxa efetiva de dados
     por N — com `sampleAverage=8`, a FIFO só recebia uma amostra nova a
     cada ~80ms (~12.5 Hz reais), não a cada 10ms (100 Hz) como
     `lowPassFilter()`/`highPassFilter()` (`Fs=100` fixo no código) e
     `HR_SAMPLE_INTERVAL_MS` (10ms) assumem — um desfasamento de **8x**
     entre a taxa real e a taxa suposta pelo pipeline de filtros que
     deteta o batimento (zero-crossing da derivada). O mesmo erro já
     estava no sketch de referência `test/HR.cpp` (não é uma regressão
     introduzida por este projeto, já vinha de lá). Corrigido:
     `sampleAverage` de 8 para 1 em `setupForHr()` (o modo de SpO2,
     `setupForSpo2()`, usa `sampleAverage=4` mas alimenta o algoritmo
     oficial da Maxim, desenhado para essa taxa — não tocado). **Nível de
     confiança honesto**: isto é uma causa plausível e bem fundamentada
     (matemática do datasheet + código da biblioteca, não uma suposição),
     mas **não confirmada como A causa** nem testada em hardware real —
     continua bloqueado pela deteção USB intermitente (ver "Riscos/
     bloqueios ativos", ponto 8). Próximo passo de hardware: repetir o
     teste "Medir agora" com este firmware e confirmar se aparecem prints
     `[PPG] HR beat -> ...` (Ppg.cpp) que nunca apareceram antes.
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
8. Confirmar em hardware real a cifra AES-CTR do "modo de dados" (ver
   secção "Cifra AES-CTR do modo de dados", 2026-07-07) — compilar o
   firmware (sem toolchain ARM nesta rotina cloud), fazer upload, e
   confirmar que o bridge (com `CAREWEAR_AES_KEY_HEX` configurada)
   consegue decifrar um registo real e mostrar dados corretos no
   dashboard. Bloqueado pela mesma indisponibilidade de hardware das
   restantes tarefas (ver "Riscos/bloqueios ativos", ponto 8).

## Verificação de bugs (rotina automática) — 2026-07-07

Com o backlog do dashboard (itens 1-10), a Prioridade 3 nomeada
(bridge↔`emergencyAlertChar`, footprint real do Random Forest, cifra
AES-CTR) e um protótipo funcional de Prioridade 4 (BD SQLite) todos já
confirmados concluídos por execuções anteriores (a última delas horas
antes desta, na mesma data), esta execução seguiu a ordem de prioridade:

**Prioridade 1 (pesquisa aplicada) — nada de novo com segurança acionável
agora**: três pesquisas dirigidas não cobertas nas notas anteriores desta
mesma data — (a) `CryptoCell`/coprocessador AES-CCM/AAR nativo do
nRF52840: confirmado que o SoC tem um coprocessador de hardware
128-bit AES/ECB/CCM/AAR dedicado (distinto do software `CryptoCell-310`,
que exige a biblioteca `nrf_cc310` da SDK Nordic, não integrada no BSP
Adafruit usado por este projeto Arduino/PlatformIO) — migrar a cifra
`FullPlain` deste coprocessador para o modo CCM de hardware resolveria de
raiz a limitação já documentada "AES-CTR cifra mas não autentica", mas
exigiria reescrever todo o caminho de cifra sem toolchain ARM nem
hardware disponíveis nesta rotina para validar — registado como direção
concreta futura, não implementado; (b) deteção de quedas/agitação em
demência: nada de novo aplicável sem hardware novo (um estudo de 2026 com
98.14% usa sensores ultrassónicos ambiente, não acelerómetro wearable);
(c) MAC truncado de 4 bytes em AES-CCM* para redes de baixa potência:
confirmado como prática real do IEEE 802.15.4 (não uma invenção desta
rotina), o que valida a viabilidade de um dia fechar a lacuna "sem
autenticação" da cifra atual — mas continua a ser uma decisão de
protocolo/hardware (cresce o pacote BLE já apertado a 20 bytes), não uma
correção que me compete tomar sozinho fora de uma revisão pontual, tal
como já registado na sessão anterior. Prioridades 2-4 confirmadas sem
nenhum item concreto novo por fazer (backlog do dashboard completo,
bridge↔emergência e footprint TinyML feitos, BD SQLite com protótipo
funcional) — por isso esta execução avançou para a Prioridade 5.

**Prioridade 5 (varredura completa de bugs)**: 4 revisões dirigidas em
paralelo (firmware C++, bridge Python, dashboard JS/HTML, pipeline `ml/`),
cada uma instruída a ler primeiro este ficheiro/`ml/README.md` para não
repetir limitações já documentadas. Achados reais, todos corrigidos nesta
execução (revisão própria de cada correção feita antes de commitar, por
`node --check`/`py_compile`/verificação de delta de chavetas-parênteses
sem toolchain ARM, e um teste Playwright real para o dashboard):

1. **Contador persistente de nonce AES-CTR não era resistente a
   corrupção/escrita cortada — corrigido** (`src/Storage/Storage.cpp`,
   `include/Storage/Storage.h`, `src/Ble/Ble.cpp`). `counter_save()` faz
   `remove()`+`open()`+`write()` (não é uma transação atómica do
   filesystem); `counter_load()` tratava QUALQUER falha de leitura
   (ficheiro em falta OU corrompido/tamanho errado, ex.: escrita cortada
   por perda de energia a meio de `counter_save()`, ~1x a cada ~21min de
   streaming contínuo) da mesma forma que "nunca guardado" —
   `reserveNonceBatch()` interpretava isso como "começa do zero",
   reutilizando nonces já usados com a mesma chave AES (nunca rotacionada
   sem apagar a flash inteira) e quebrando silenciosamente a
   confidencialidade do CTR — a mesma classe de vulnerabilidade que a
   revisão anterior desta data já tinha corrigido por outra via
   (esgotamento do contador), mas por um caminho diferente e não coberto
   por essa correção. **Corrigido**: o ficheiro do contador passou a ter
   um magic number + checksum simples (`CounterRecord`), permitindo
   `counter_load()` distinguir "ficheiro nunca criado" (primeiro arranque
   genuíno, seguro começar do zero) de "ficheiro existe mas está
   corrompido" (novo parâmetro de saída opcional `corrupted`); em caso de
   corrupção, `reserveNonceBatch()` falha FECHADA (streaming de dados
   para, aviso único `[BLEG] AVISO CRITICO: contador... corrompido`),
   nunca assume zero silenciosamente. Não testado em hardware real (mesma
   limitação já documentada — sem toolchain ARM/hardware nesta rotina),
   só revisto por leitura direta do código; nota: isto muda o formato do
   ficheiro `/counter.bin` (cresce ~16 bytes), mas nenhum dispositivo real
   ainda produziu dados neste formato (cifra nunca testada em hardware),
   por isso não há migração a fazer.
2. **Bridge: `broadcast()` podia rebentar com `RuntimeError` sob ligação/
   desligação concorrente de clientes WebSocket — corrigido**
   (`bridge/ble_bridge.py`). Iterava diretamente sobre `self.ws_clients`
   (um `set` partilhado) enquanto estava suspenso num `await ws.send(...)`
   — se `ws_handler` fizesse `add()`/`discard()` no mesmo set nesse
   intervalo (ex.: um separador do dashboard a recarregar mesmo quando um
   registo/status chegava), o Python lança "Set changed size during
   iteration", perdendo essa mensagem para os clientes ainda não
   alcançados nessa iteração. Corrigido: itera sobre `list(self.ws_clients)`
   (uma cópia), imune a mutações concorrentes do set original.
3. **Bridge: fragmentos BLE incompletos nunca eram limpos —
   fuga de memória real — corrigido** (`bridge/ble_bridge.py`,
   `_pending_fragments`). `notify()` não é um transporte com confirmação;
   se um fragmento de um registo se perdesse, essa entrada nunca recebia
   todos os fragmentos e por isso nunca era removida — a ~14-52
   registos/seg, mesmo uma perda de pacotes pequena acumula milhares de
   entradas órfãs numa sessão de várias horas. Corrigido: cada entrada
   guarda agora `created_at` (timestamp), e `_prune_stale_fragments()`
   (nova função, chamada a cada fragmento incompleto recebido) remove
   entradas mais velhas que `PENDING_FRAGMENT_TIMEOUT_S` (5s) — evita a
   fuga de memória e também o risco secundário identificado pela revisão
   (uma entrada antiga a ser "herdada" por um `rec_seq` reciclado depois
   deste, um contador `uint32`, dar a volta ao fim de anos de streaming
   contínuo).
4. **Dashboard: três bugs reais em `medication-reminders.js`, todos
   corrigidos e verificados em Playwright real (Chromium)**:
   (a) `showFallbackAlert()` interpolava `patient.id` sem aspas no
   `onclick` gerado (`markDoseTaken(${patient.id}, ...)`, mas `patient.id`
   é uma string tipo `'p1'`) — o HTML resultante tentava avaliar `p1` como
   variável, `ReferenceError` ao clicar, o botão "Tomei agora" do cartão
   de fallback nunca marcava a dose nem fechava o cartão. Corrigido
   (aspas à volta dos três argumentos). (b) `options.actions` +
   `notification.onaction` na notificação nativa do browser nunca
   funcionavam — essa API só é entregue via evento `notificationclick` de
   um Service Worker, que este projeto não tem; os botões "✓ Tomei
   agora/⏰ Adiar/✗ Fechar" da notificação nativa eram sempre inertes
   (confirmado por pesquisa da spec — não é o que `PROJECT_STATUS.md`
   descrevia antes desta correção). Corrigido: removida a API falsa;
   `onclick` da notificação foca a janela e navega para a vista
   "Medicação" (`activateNavItem`), e o cartão de fallback com botão
   funcional passa a ser sempre mostrado também quando a notificação
   nativa é usada, garantindo que há sempre uma ação real disponível.
   (c) Duas doses com a mesma hora prevista (ex.: dois medicamentos às
   08:00) faziam a segunda ser silenciosamente descartada — `showFallbackAlert()`
   usava um único ID de elemento fixo (`medicationReminder`) partilhado
   por todos os alertas do dia; se já existisse um cartão (de OUTRA dose),
   o segundo nunca aparecia, mas ficava marcado como "já mostrado" até à
   meia-noite. Corrigido: cada dose tem agora um cartão com ID único
   (paciente+medicamento+hora) dentro de um contentor empilhável
   (`#medicationReminderStack`, flex column) — testado com duas doses na
   mesma hora, ambos os cartões aparecem e cada um marca a dose certa ao
   clicar (verificado com Playwright real, incluindo o clique no botão
   "Tomei agora" chamando `markDoseTaken` com os argumentos corretos e
   removendo só o seu próprio cartão).
5. **`ml/`: `LabelEncoder` ajustado só ao split de treino — corrigido**
   (`train_activity_classifier.py`, `train_activity_classifier_rf.py`).
   Como a divisão treino/teste é por sujeito sintético (não por janela),
   é possível — por azar da amostra aleatória de sujeitos — uma classe
   mais rara (ex.: "Higiene") ficar inteiramente do lado do teste e
   ausente do treino; nesse caso `encoder.transform(test_df[...])`
   rebentava com `ValueError: y contains previously unseen labels`,
   reproduzido diretamente com outros parâmetros (`n_subjects=4, seed=9`).
   Não acontece com os 8 sujeitos/seed=42 usados atualmente (confirmado
   por re-execução real desta rotina, accuracy idêntica à já documentada:
   XGBoost 1.000, Random Forest 0.981 — a pequena diferença face ao 0.978
   já registado é deriva de versão do `scikit-learn` do ambiente, não
   deste fix, confirmado reproduzindo o mesmo valor com e sem a
   correção), mas era uma armadilha real para a próxima iteração do
   dataset já prevista no roteiro (`ml/README.md`, "mais sujeitos/
   sementes diferentes"). Corrigido ajustando o encoder ao conjunto
   completo de classes antes da divisão — o mesmo padrão que
   `measure_rf_footprint.py` já usava corretamente (o código estava
   inconsistente entre scripts). **Modelos/relatórios treinados
   (`ml/models/activity_classifier_rf.*`, `ml/reports/
   activity_classifier_rf_metrics.json`) NÃO foram re-commitados** — a
   correção não muda o comportamento do dataset atual (todas as classes
   já estão presentes nos dois lados), por isso não há motivo para
   substituir o modelo já treinado e avaliado só por causa deste fix
   defensivo; a pequena diferença de accuracy (0.978→0.981) observada
   numa reexecução de verificação é deriva de ambiente, não uma
   retreinagem intencional — evitado para não fabricar/substituir
   resultados sem uma decisão explícita de retreinar.
6. **`ml/data/synthetic_routine_dataset.meta.json`**: mojibake de
   acentuação corrigido (`Alimenta�o` → `Alimentação`, etc.) —
   encontrado incidentalmente durante a revisão do pipeline de ML,
   mesma classe de bug (ficheiro gravado sem UTF-8 explícito) já corrigida
   noutros ficheiros do projeto em sessões anteriores.

As 4 revisões dirigidas (firmware, bridge, dashboard, `ml/`) desta
Prioridade 5 estão agora todas concluídas e os achados reais corrigidos
acima.
