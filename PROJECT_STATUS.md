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
| `QspiRingBuffer` | `src/QspiRingBuffer/`, `include/QspiRingBuffer/` | Comentado. Ring buffer de 64 bytes/slot na flash externa. Nova função `advanceTail()` (2026-07-07, ver secção "Otimização de CPU/flash" abaixo) elimina uma leitura QSPI redundante por registo no caminho de streaming BLE. |

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

### Otimização de CPU/flash (2026-07-07, rotina diária) — leitura QSPI redundante eliminada do streaming BLE

Com as reduções de stack (RAM) já feitas em rondas anteriores e sem
watermarks novos de hardware para justificar mais cortes, esta execução
procurou desperdício de CPU/latência em vez de RAM. Achado concreto em
`src/QspiRingBuffer/QspiRingBuffer.cpp`/`src/Ble/Ble.cpp`:

- **Problema**: o caminho de streaming BLE (`gattDumpTask`/
  `peekImuPpgRecord`, `Ble.cpp`) já seguia corretamente o padrão "peek()
  (lê sem remover) → envia por BLE → só depois pop() (remove)" para nunca
  perder um registo se o envio falhar a meio. Mas `pop()` volta sempre a
  fazer o próprio trabalho que `peek()` acabara de fazer sobre o
  **mesmo** slot: uma transação QSPI (`readSlot()`) + validação CRC
  FNV-1a sobre ~60 bytes + `memcpy` de 44 bytes (`decodeSlot()`) — tudo
  isso só para deitar fora o resultado (`discard`) e avançar `tail`.  Ao
  ritmo do IMU (até ~52 registos/seg), isto duplicava as transações de
  leitura QSPI nesse caminho: até ~104 leituras+descodificações/seg em
  vez de ~52.
- **Correção**: nova função `QspiRingBuffer::advanceTail()` — faz só a
  contabilidade (`tail`/`count`/`markMetaDirty()`), sem tocar na flash,
  pensada especificamente para o caso em que o chamador já tem a certeza
  (por ter acabado de chamar `peek()` com sucesso sobre o mesmo registo)
  de que o slot é válido. Substituiu as duas chamadas a `pop()` que
  existiam só para descartar um registo já lido (`peekImuPpgRecord()`, ao
  saltar um registo de tipo inesperado; e o corpo principal de
  `gattDumpTask()`, depois de um envio bem-sucedido). `pop()` em si não
  foi alterada (continua a existir e a ser usada por `selfTest()`, e
  continua a ser a função certa a chamar quando o chamador NÃO já tem o
  registo validado em mãos).
- **Poupança por registo enviado**: 1 transação QSPI (`readBuffer` de 64
  bytes) + 1 checksum FNV-1a sobre ~60 bytes + 1 `memcpy` de 44 bytes
  evitados — a ~52 registos/seg, isto elimina até ~52 leituras QSPI/seg
  supérfluas (metade do total anterior nesse caminho).
- **Build**: sem toolchain ARM disponível de início nesta rotina cloud
  (mesma limitação já documentada noutras secções), esta execução tentou
  `pip install platformio` + `pio run` pela primeira vez — o pacote
  `platformio` instalou-se sem problema, mas a instalação da toolchain
  ARM (`https://files.seeedstudio.com/...`) foi bloqueada pelo proxy do
  ambiente (`403 Forbidden`), por isso `pio run` não chegou a compilar.
  Revisão manual feita em alternativa: leitura direta do diff, e um
  script Python que confirma que o desequilíbrio de chavetas/parênteses
  em `Ble.cpp` (proveniente de comentários com parênteses aninhados, não
  de código real — já existia antes desta alteração) se manteve
  **exatamente igual** antes/depois da edição (129/128 chavetas,
  890/897 parênteses em ambas as versões), ou seja, a edição não
  introduziu nenhum desequilíbrio novo. `QspiRingBuffer.cpp`/`.h`
  (ficheiros novos/alterados sem esse ruído de comentários) têm chavetas
  e parênteses perfeitamente equilibrados. **A CI corrigida nesta mesma
  data** (ver "Verificação de bugs" mais abaixo, item 1) deve compilar
  isto de facto no primeiro push — será a primeira confirmação real de
  build para esta alteração.
- **Não testado em hardware real** (mesma limitação de sempre — placa
  indisponível nesta rotina cloud, ver "Riscos/bloqueios ativos", ponto
  8): o comportamento lógico é idêntico ao anterior (mesma sequência de
  avanço de `tail`/`count`), só a leitura de flash redundante foi
  removida, por isso o risco de regressão funcional é baixo, mas fica
  pendente de confirmação — em particular, confirmar que o streaming BLE
  continua a enviar/consumir registos corretamente (contagem de
  `sent_records`/`acked_records` em `DumpStatusPacket` a bater certo com
  o esperado) depois de atualizar o firmware.

### Otimização de CPU (2026-07-07, rotina diária) — commit SQLite síncrono a bloquear o event loop do bridge

Área nova para esta rotina (bridge Python, não tocado nas rondas
anteriores de otimização, que se focaram só no firmware). Achado em
`bridge/storage.py` (`get_connection()`/`insert_record()`):

- **Problema**: `insert_record()` é chamado de forma síncrona, direto no
  event loop `asyncio`, a partir de `_on_dump_data()` em
  `bridge/ble_bridge.py` — o callback de notificação BLE que corre a
  cada registo de sensores descodificado, até ~52/seg (mesma taxa do
  IMU referida em várias secções acima). `get_connection()` não
  configurava nenhum `PRAGMA` de desempenho, por isso cada
  `conn.commit()` usava o modo por omissão do SQLite (rollback journal +
  `synchronous=FULL`), que faz até 2 `fsync()` síncronos (journal +
  ficheiro principal) por commit — uma operação de disco que tipicamente
  custa alguns a várias dezenas de milissegundos. Como isto corre no
  único thread do event loop, cada commit bloqueava nesse intervalo o
  envio de mensagens WebSocket, o processamento de outras notificações
  BLE (incluindo `dumpStatusChar`/`emergencyAlertChar`) e a task
  periódica de retenção — ao ritmo documentado, até ~52 (ou ~104, com os
  2 fsyncs) bloqueios do event loop por segundo, só para persistência
  local. Note-se que isto é distinto do `RECORD_BROADCAST_MIN_INTERVAL_S`
  já existente (`ble_bridge.py`), que só limita a taxa do broadcast por
  WebSocket — a escrita na base de dados acontecia sempre, a toda a
  taxa, sem nenhuma mitigação.
- **Correção**: `get_connection()` passou a configurar
  `PRAGMA journal_mode=WAL` + `PRAGMA synchronous=NORMAL` na ligação.
  Em WAL, `commit()` só acrescenta ao ficheiro `-wal` (sem `fsync` a cada
  escrita — o SQLite faz checkpoint para o ficheiro principal
  periodicamente e de forma automática), o que reduz drasticamente o
  custo de cada commit sem mudar a lógica de "um commit por registo" já
  existente. **Decisão deliberada de NÃO acumular/atrasar commits**
  (alternativa mais agressiva considerada e descartada): o bridge não
  tem hoje nenhum mecanismo de "flush no encerramento" (`asyncio.run(main())`
  termina diretamente com `KeyboardInterrupt`, sem bloco `finally` a
  fechar a base de dados) — atrasar commits arriscaria perder mais
  registos num encerramento abrupto do que perde hoje, e adicionar essa
  infraestrutura de flush-on-shutdown só para permitir o batching seria
  mais mudança do que esta rotina considera justificada por uma única
  passagem. WAL evita o bloqueio do event loop sem introduzir esse
  risco novo.
- **Validado localmente** (Python real, não fabricado): script de teste
  cria uma base de dados temporária, chama `storage.init_db()`, confirma
  `PRAGMA journal_mode` a devolver `"wal"`, insere um registo de exemplo
  e confirma que `count_records()`/`get_records_since()` continuam a
  funcionar normalmente. `python3 -m py_compile` sobre
  `bridge/storage.py`/`bridge/ble_bridge.py` sem erros.
- **Limitação honesta**: não foi medido o tempo de `commit()` antes/depois
  com um perfilador real (ex.: `time.perf_counter()` à volta da chamada,
  em hardware/disco real) — a justificação acima é baseada no
  comportamento documentado e amplamente conhecido do SQLite (fsync por
  commit no modo rollback-journal vs. WAL), não numa medição própria
  desta execução. Fica como próximo passo se se quiser confirmar o
  ganho em número concretos (ex.: `EXPLAIN`/temporização real com o
  bridge ligado a um dispositivo real).

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
- Campos sensíveis (NIF, morada): cifra real implementada 2026-07-07 —
  AES-256-GCM, chave derivada via Argon2id (`bridge/crypto_utils.py`, ver
  secção "Cifra real dos campos sensíveis (NIF, morada) + Alembic" abaixo)
- `AuditLog` JSONB com details completos (valores antigos/novos para comparar)
- IP address registado em cada ação sensível

**Motor de BD**:
- **SQLite em desenvolvimento** (`sqlite:///./carewear.db`) — embutido, sem servidor
- **PostgreSQL em produção** (via `DATABASE_URL` env var) — suporta JSONB nativo, constraints mais fortes, pool connection
- Migrations via **Alembic** (`bridge/migrations/`, configurado 2026-07-07 —
  ver secção "Cifra real dos campos sensíveis (NIF, morada) + Alembic" abaixo)

**Próximas fases**:
- Integração Twilio para SMS/email (alertas críticos, OTP confirmação emergência)
  — bloqueado, precisa de credenciais/conta do utilizador.
- ~~Endpoints REST/GraphQL para dashboard (ligar queries analíticas a tempo
  real)~~ — **primeira versão feita (2026-07-07, rotina cloud)**, só
  leitura (GET), autenticação por chave estática, ainda sem integração com
  o dashboard nem com `ble_bridge.py` — ver secção "API REST somente-leitura
  (`bridge/api.py`)" abaixo.
- ~~Alembic migrations (script versionado para evolução do schema)~~ —
  **feito (2026-07-07, rotina cloud)**, ver secção "Cifra real dos campos
  sensíveis (NIF, morada) + Alembic" abaixo.
- ~~Cifra real dos campos sensíveis (derivação de chave com argon2)~~ —
  **feito (2026-07-07, rotina cloud)**, ver secção seguinte.
- ~~Testes unitários com pytest~~ — **primeira suite feita (2026-07-07,
  rotina cloud)**, ver secção seguinte.

### Primeira suite de testes (`bridge/tests/`) + 3 bugs adicionais corrigidos (2026-07-07, rotina cloud)

Ao escrever a suite de testes já listada acima como próximo passo, esta
execução encontrou (via `git fetch`/rebase, como sempre pedido antes do
push) que uma sessão paralela tinha corrigido, na mesma janela de tempo,
os dois bugs que impediam `storage_advanced.py` de sequer ser importado
(`JSONB` fora do top-level `sqlalchemy`, tabela `patient_caregivers` em
falta) mais um bug de fuso horário em `heart_rate_trends` — ver a secção
"Varredura de bugs (Prioridade 5, 3ª passagem)" mais abaixo para o
detalhe desses três. Em vez de duplicar esse trabalho, esta execução
rebaseou sobre o que já estava corrigido e continuou a escrever a suite,
o que revelou **três problemas adicionais e distintos**, ainda por
corrigir depois do rebase:

1. **`DataRetention.cleanup()` assumia `Alert.deleted_at`** (a
   documentação já descrevia soft delete de alertas com esta coluna, mas
   a classe `Alert` nunca a declarava) — `AttributeError` ao chamar
   `cleanup()`, mesmo depois dos dois bugs de import já corrigidos.
   Adicionada a coluna.
2. **`Analytics.daily_activity_distribution()` nunca encontrava
   nenhuma janela de atividade** — comparava a coluna `activity_date`
   (DateTime, com hora) diretamente com `date.date()` (sem hora); em
   SQLite isto compara valores em formatos diferentes e nunca coincide.
   Corrigido para um intervalo `[início do dia, início do dia
   seguinte)`.
3. **`DataRetention.RETENTION_POLICIES` declara 6 políticas mas
   `cleanup()` só aplicava 3** (`sensor_records`, `activity_windows`,
   `alerts`) — `anomaly_detections` (5 anos) e `medication_adherence`
   (3 anos) nunca eram purgados, apesar de a documentação da secção
   acima já afirmar que o eram. Implementadas as duas políticas em
   falta (apagar, não soft-delete — mesma lógica das outras tabelas sem
   valor legal/clínico de longo prazo). `emergency_alerts` continua
   **deliberadamente** de fora do `cleanup()` — está no dicionário só
   como referência documental dos 10 anos, nunca é apagado
   automaticamente (histórico de segurança), comportamento já correto e
   agora coberto por teste de regressão.

Aproveitado o mesmo ficheiro já aberto para modernizar um import
depreciado sem efeito funcional: `declarative_base` passou a vir de
`sqlalchemy.orm` em vez de `sqlalchemy.ext.declarative` (avisado como
`MovedIn20Warning` pelo SQLAlchemy 2.0, mesma versão já fixada em
`requirements_db.txt`).

**Suite nova**: `bridge/tests/test_storage_advanced.py` (16 testes,
`bridge/tests/conftest.py` força `DATABASE_URL=sqlite:///:memory:` antes
do import, nunca toca em `carewear.db` real) — cobre criação de schema, a
associação paciente↔cuidador (`patient_caregivers`), as três queries de
`Analytics` (tendências de FC, aderência a medicação, distribuição diária
de atividade) e `DataRetention.cleanup()` (dry-run vs. real, hard delete
vs. soft delete, as 6 políticas incluindo a exclusão intencional de
`emergency_alerts`). Correr com `cd bridge && pip install -r
requirements_db.txt pytest && python -m pytest tests/ -v` — **16/16
passam**, sem avisos. Puro Python/SQLite, sem toolchain ARM nem hardware
envolvido — dentro do que esta rotina cloud consegue verificar
diretamente (ao contrário do firmware C++, que continua sem poder ser
testado sem acesso à placa física).

**Limitação honesta**: esta suite testa `storage_advanced.py` isoladamente
— o módulo continua **não integrado** no `ble_bridge.py` (que usa
`storage.py`, a versão mais simples já em produção). Testes de integração
bridge↔BD avançada ficam por fazer quando/se essa integração avançar.

### Cifra real dos campos sensíveis (NIF, morada) + Alembic (2026-07-07, rotina cloud)

Duas das quatro "Próximas fases" registadas na secção acima (2026-07-04)
ainda estavam por fazer nesta data — esta execução seguiu a ordem de
prioridade do projeto (0-2 hardware bloqueado, 3 app móvel fora do âmbito
de uma execução autónoma, 4 = esta), depois de sincronizar com
`origin/main` (o checkout local desta rotina estava numa branch antiga,
59 commits atrás — ver nota de processo abaixo) e confirmar que nenhuma
rotina paralela tinha tocado nestes dois itens ainda hoje.

**Nota de processo (não é uma alteração de código)**: esta execução
começou com o checkout local numa branch (`claude/wonderful-goldberg-ypuyq1`)
criada localmente e nunca publicada no GitHub, muito atrás do `origin/main`
real — um `git fetch origin --prune` revelou o estado verdadeiro do
repositório, incluindo um branch remoto extra `Main` (maiúscula, já
identificado e documentado por outra rotina, ver "Verificação de bugs —
2ª passagem" abaixo) e confirmou que `origin/main` continha 58 commits não
vistos localmente. Resolvido com `git checkout main && git merge --ff-only
origin/main` (fast-forward puro, sem perda de trabalho — a branch local
antiga não tinha nenhum commit que não estivesse já em `origin/main`).
Registado aqui como lembrete, reforçando a nota já deixada por outra
rotina horas antes: fazer sempre `git fetch`/verificar o estado real do
repositório antes de assumir o que já foi feito, especialmente com
múltiplas sessões a correr no mesmo dia.

**1. Cifra real dos campos sensíveis** (`bridge/crypto_utils.py`, novo):
`Patient.nif_encrypted`/`address_encrypted` (`storage_advanced.py`)
estavam nomeados como "encriptados" desde 2026-07-04 mas guardavam sempre
texto simples — a cifra nunca tinha sido implementada, só prometida na
documentação (o mesmo tipo de lacuna já encontrada e corrigida noutra
parte do projeto, ver "Histórico: o 'modo de dados' BLE não estava
cifrado" acima). Implementado:

- Chave derivada com **Argon2id** (`argon2-cffi`, parâmetros OWASP para
  derivação de chave: time_cost=3, memory_cost=64MiB, parallelism=4) a
  partir de duas variáveis de ambiente novas — `CAREWEAR_DB_ENCRYPTION_KEY`
  (frase-passe) + `CAREWEAR_DB_ENCRYPTION_SALT_HEX` (sal, hex ≥16 bytes) —
  nunca no código-fonte, mesmo padrão já usado para `CAREWEAR_AES_KEY_HEX`
  no bridge BLE.
- Cifra por campo com **AES-256-GCM** (autenticada — ao contrário do
  AES-CTR do streaming BLE, aqui a latência de um MAC completo por campo
  não é um problema, por isso não há razão para abrir mão da autenticação).
  Nonce aleatório por chamada, prefixo `enc:` no valor guardado para
  distinguir de texto simples legado.
- **Degrada de forma visível, nunca finge cifrar**: sem as duas variáveis
  de ambiente configuradas, `encrypt_field()`/`decrypt_field()` devolvem o
  valor tal como está (com um aviso único no arranque) — mesma filosofia já
  aplicada à cifra BLE quando `CAREWEAR_AES_KEY_HEX` está ausente.
- `Patient.nif`/`Patient.address` (novas propriedades Python) cifram/
  decifram automaticamente através destas colunas — nenhum código que use
  `Patient` precisa de chamar `encrypt_field()`/`decrypt_field()`
  diretamente.
- **`schema.sql` corrigido**: a coluna `nif` tinha `UNIQUE`, o que deixa de
  fazer sentido com cifra autenticada (nonce aleatório por escrita produz
  ciphertext sempre diferente para o mesmo NIF — uma constraint `UNIQUE`
  sobre ciphertext nunca detetaria duplicados, só rejeitaria escritas por
  coincidência). Renomeada para `nif_encrypted`/`address_encrypted`
  (alinhado com os nomes já usados no ORM) e removida a `UNIQUE`.
  Colunas alargadas de 255 para 512 bytes (overhead da cifra + base64 em
  moradas mais longas).
- **Testado**: `bridge/tests/test_crypto_utils.py` (13 testes novos —
  cifra desativada por omissão, roundtrip com/sem Unicode, dois valores
  iguais produzem ciphertext diferente, texto simples legado continua
  legível depois de a cifra ser ativada, falha fechada — `RuntimeError` —
  se a chave desaparecer entretanto, falha com frase-passe errada) +
  4 testes novos em `test_storage_advanced.py` (`TestPatientSensitiveFields`,
  integração das propriedades `nif`/`address` com o modelo ORM). **34/34
  testes passam** (16 já existentes + 18 novos), SQLite em memória, sem
  hardware nem toolchain ARM envolvidos.
- **Limitação honesta**: perder a frase-passe OU o sal torna os campos já
  cifrados irrecuperáveis (não há backdoor nem recuperação — documentado
  no `bridge/README.md` novo, secção "Base de dados avançada"). Rotação de
  chave (recifrar tudo com uma chave nova) não implementada — ficaria para
  quando/se este módulo avançar para produção real. `storage_advanced.py`
  continua **não integrado** em `ble_bridge.py` (mesma limitação já
  registada na secção anterior) — esta cifra protege um módulo ainda em
  protótipo, não dados reais de utentes em produção.

**2. Alembic configurado** (`bridge/alembic.ini`, `bridge/migrations/`,
novos): schema deixou de depender só de `Base.metadata.create_all()`
(cria tudo de uma vez, sem histórico de alterações) — `bridge/migrations/env.py`
aponta para `storage_advanced.Base.metadata` e reutiliza a mesma variável
`DATABASE_URL` já usada pela aplicação (nunca duplica a URL em
`alembic.ini`), com `render_as_batch=True` em SQLite (necessário porque
SQLite não suporta `ALTER TABLE` para a maioria das operações — sem efeito
em PostgreSQL/produção). Gerada e **testada de facto** a migração inicial
(`migrations/versions/daaeabc42ec5_schema_inicial.py`, via
`--autogenerate`, não escrita à mão): `alembic upgrade head` contra uma
BD SQLite temporária cria as 15 tabelas esperadas, `alembic downgrade
base` remove-as todas — confirmado a correr os dois comandos, não só
assumido. Documentado em `bridge/README.md` (nova secção "Base de dados
avançada").

**Ainda por fazer** (ver "Próximas fases" acima, atualizada): Twilio
(bloqueado, precisa de credenciais do utilizador). Os endpoints REST para
ligar as queries analíticas ao dashboard foram implementados numa execução
posterior — ver secção "API REST somente-leitura (`bridge/api.py`)" abaixo.

**CI confirmada a passar** (commit `2d2677d`, verificado via API do GitHub
Actions após o push): esta alteração é só Python (bridge/), não toca no
firmware, mas a CI do PlatformIO corre sempre no push a `main` — `completed`/
`success`, sem regressão no build do firmware.

### API REST somente-leitura (`bridge/api.py`, 2026-07-07, rotina cloud)

Próximo item concreto da Prioridade 4, registado na secção anterior:
liga as queries analíticas (`Analytics.heart_rate_trends`,
`Analytics.medication_adherence_summary`, `Analytics.daily_activity_distribution`,
já existentes em `storage_advanced.py`) a um serviço HTTP, em vez de só
serem chamáveis diretamente em Python. Âmbito deliberadamente pequeno para
uma única execução, como já feito noutras vezes neste projeto (ex.: passo 3
do `ml/`, cifra dos campos sensíveis): **só leitura (GET), sem
escrita/mutações**, e sem tentar já ligar isto ao dashboard nem ao
`ble_bridge.py`.

**Implementado** (`bridge/api.py`, novo, FastAPI + Uvicorn):
- `GET /health` (sem autenticação, só confirma que o serviço está de pé).
- `GET /api/devices/{device_id}/heart-rate-trends?days=N`
- `GET /api/patients/{patient_id}/medication-adherence?days=N`
- `GET /api/devices/{device_id}/activity-distribution?date=AAAA-MM-DD`
- 404 explícito quando `device_id`/`patient_id` não existe (as queries de
  `Analytics` por si só devolveriam silenciosamente valores vazios/zero
  para um ID inexistente, o que esconderia um erro de integração do lado
  de quem consome a API).

**Autenticação — decisão de implementação, não uma decisão de
produto/hardware**: chave estática partilhada por cabeçalho `X-API-Key`,
comparada com a variável de ambiente `CAREWEAR_API_KEY`. Deliberadamente
**diferente** da filosofia "degrada de forma visível" já usada na cifra de
campos sensíveis e na cifra AES-CTR do streaming BLE — aqui, sem a variável
configurada, a API **falha fechada** (503 em todos os pedidos
autenticados), porque os dados expostos são PII de saúde servidos por
rede, não um stream local; deixar passar pedidos sem chave por omissão
seria o pior comportamento possível. **Limitação honesta**: chave estática
única, sem rotação, sem autenticação por-utilizador, sem rate-limiting —
protótipo, não pronta para produção real (registado como decisão pendente
abaixo, tal como a escolha XGBoost/Random Forest do `ml/`).

**Testado**: `bridge/tests/test_api.py` (12 testes novos, `fastapi.testclient.TestClient`
contra SQLite em memória) — autenticação (chave ausente/errada/correta,
variável de ambiente não configurada), 404 para device/patient
inexistente, agregações corretas (janela temporal de FC, percentagem de
adesão, distribuição por categoria de atividade), 400 para data em formato
inválido. **34→46 testes** no total do bridge, todos a passar. Verificado
também **fora do TestClient**: servidor real via `uvicorn.Server` num
thread separado, pedido HTTP real por `urllib` contra `127.0.0.1:8766`
(`/health`, pedido autenticado com sucesso, pedido com chave errada a
devolver 401) — não só chamadas em processo.

**Ainda por fazer** (âmbito explicitamente fora desta execução, ver
`bridge/api.py` cabeçalho e `bridge/README.md`): integração com
`web/dashboard/index.html` (que hoje só fala com `ble_bridge.py` via
WebSocket, não com esta API), integração com `ble_bridge.py`
(`storage_advanced.py` continua sem ligação ao streaming BLE real — só
`storage.py`, a versão mais simples, está em produção), autenticação de
produção (por-utilizador/JWT, rotação de chave, rate-limiting). ~~Endpoints
de escrita (ex.: marcar dose de medicação como tomada via API em vez de só
`localStorage` no dashboard)~~ — **primeiro endpoint de escrita feito
(2026-07-08, rotina cloud)**, ver secção "Primeiro endpoint de escrita na
API REST" mais abaixo.

Ficheiros novos: `bridge/api.py`, `bridge/tests/test_api.py`. Ficheiros
alterados: `bridge/requirements_db.txt` (`fastapi`, `uvicorn`, `httpx`),
`bridge/README.md` (nova secção "API REST somente-leitura").

**Bug de segurança real corrigido nesta mesma API (2026-07-07, rotina
cloud, execução seguinte)**: `_require_api_key()` comparava a chave
recebida com a configurada usando `!=` — uma comparação de strings normal
em Python sai assim que encontra o primeiro byte diferente, o que teoricamente
permite reconstruir a chave certa por temporização (medir quanto tempo
demora cada tentativa a ser rejeitada), em vez de só por força bruta cega.
Não é o tipo de bug que se vê a olho num code review superficial — só
salta à vista quando se lê esta função lado a lado com `crypto_utils.py`
(cifra dos campos sensíveis) e a cifra AES-CTR do streaming BLE, ambas já
cuidadosas com este tipo de detalhe (nonces aleatórios, chaves nunca
hard-coded). Corrigido com `hmac.compare_digest()` (tempo constante,
biblioteca standard do Python). Adicionado `TestAuth.test_empty_key_rejected`
a `bridge/tests/test_api.py` — **47 testes** no total do bridge, todos a
passar. Risco prático baixo (protótipo local, chave estática de qualquer
forma marcada como "não pronta para produção" acima), mas a correção é
trivial e sem custo, por isso feita já em vez de só registada como
limitação.

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

### UI do resumo semanal de adesão ligada (2026-07-07, rotina cloud completa)

Resolvido o item "ainda por fazer" nº 4 acima (`recordDay()`/`getWeekSummary()`/
`getRecommendations()` nunca eram chamados, por isso `AdherenceAnalytics`
ficava sempre vazia):

- `markDoseTaken()` (`web/dashboard/index.html`) passou a chamar
  `window.adherenceAnalytics.recordDay(patientId, todayAdherencePct(patient))`
  sempre que uma dose é confirmada — só regista a percentagem de adesão de
  **hoje**, recalculada a partir de cliques reais, nunca um valor simulado.
  Não passa `activityLevel`/`hrAvg` (2º/3º argumentos de `recordDay()`) por
  agora — não há ainda um mapeamento honesto de "nível de atividade"
  (alto/médio/baixo) a partir dos dados existentes do dashboard sem
  inventar uma categorização; fica registado como possível extensão futura,
  não feita aqui para não fabricar uma correlação sem base.
- Novo cartão **"Análise de adesão"** em `TEMPLATES.medicacao`, com o rótulo
  "dados reais deste browser" — **deliberadamente separado** do cartão já
  existente "Adesão — últimos 6 dias" (que continua a usar
  `patient.adherenceHistory`, dados de exemplo fixos no código), mesma
  regra do resto do projeto de nunca misturar dados reais e simulados na
  mesma série. Mostra a média dos dias registados, o alerta/padrão
  calculado por `AdherenceAnalytics`, e a lista de recomendações; antes de
  haver qualquer dia registado, mostra um estado vazio explícito ("regista-se
  um dia de cada vez... não é um histórico retroativo") em vez de aparentar
  dados que não existem.
- **Testado em Playwright real (Chromium)**: vista "Medicação" antes de
  marcar qualquer dose mostra o estado vazio do novo cartão; depois de
  clicar "Marcar como tomado", o cartão passa a mostrar "Média dos
  últimos..." com a percentagem recalculada, sem erros de consola (só o
  aviso esperado de WebSocket recusado, sem bridge a correr) — confirmado
  também que o cartão de histórico simulado antigo continua a renderizar
  sem alterações. Sintaxe do `<script>` principal revalidada com
  `node --check` depois da alteração.
- **Limitação honesta**: continua sem ligação a `storage_advanced.py` —
  o histórico de `AdherenceAnalytics` vive só em `localStorage` deste
  browser, perde-se ao limpar dados do browser ou trocar de dispositivo,
  tal como o resto dos dados de demonstração do protótipo.

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

## Verificação de bugs (rotina automática) — 2026-07-07, 2ª passagem do dia

Com as Prioridades 1-4 já confirmadas esgotadas por uma execução anterior
horas antes (mesmo dia — ver secção imediatamente acima), esta execução
repetiu a Prioridade 1 com 2 buscas em ângulos ainda não tentados antes de
avançar para nova varredura de bugs:

- **"Ground-face coordinate system" para deteção de queda**: o artigo
  (Sözer, SAUCIS, dez. 2024, DOI 10.35377/saucis.1522290) foi localizado
  desta vez, incluindo um PDF direto num mirror universitário — mas
  **continua bloqueado (403)** tanto pelo `WebFetch` como por um pedido
  `curl` direto através do proxy da sessão, para o artigo original e para
  o mirror. Só ficaram disponíveis descrições de alto nível nos resultados
  de pesquisa (eixo vertical alinhado com a gravidade via filtro
  passa-baixo, eixo X alinhado com a direção de marcha, ~2% de melhoria de
  acurácia na deteção), sem a fórmula exata nem os parâmetros numéricos.
  Mantida a mesma decisão já registada antes: não implementar uma versão
  adivinhada de um algoritmo de deteção de queda (código de segurança) a
  partir de um resumo de segunda mão.
- **Bonding/pairing BLE (LE Secure Connections) para dispositivos de
  saúde**: pesquisa confirma que "Just Works" (sem MITM) é o pairing menos
  seguro e que dispositivos médicos deveriam usar LE Secure Connections
  (ECDH) + bonding, Security Mode 1 Level 4. O firmware atual (`Ble.cpp`)
  não configura nenhum destes (nem se confirmou aqui se está sequer a
  usar bonding básico). **Não implementado nesta execução**: mudar o modo
  de pareamento BLE é uma alteração de protocolo/segurança com risco real
  de repetir a classe de bugs já sofrida neste projeto (BLE a
  desconectar-se/deixar de ser encontrado, ver histórico de instabilidade
  BLE e o bug do RF switch partilhado) — só é seguro validar em hardware
  real, indisponível nesta rotina cloud. Registado como direção concreta
  futura, ao lado da já registada migração para `CryptoCell`/AES-CCM de
  hardware.

Com a Prioridade 1 sem nada de novo implementável com segurança, e as
Prioridades 2-4 sem itens novos (mesma conclusão da execução anterior),
esta execução avançou para uma nova varredura de bugs (Prioridade 5), à
procura de problemas ainda não cobertos pela varredura já feita horas
antes (nonce AES-CTR, `broadcast()`, fragmentos BLE, `medication-reminders.js`,
`LabelEncoder`):

1. **CI real e verificavelmente quebrada em todo push a `main`, corrigida.**
   `.github/workflows/c-cpp.yml` ("C/C++ CI") era o modelo genérico
   sugerido pelo GitHub para projetos C/C++ com Autotools
   (`./configure && make && make check && make distcheck`) — este projeto
   usa PlatformIO/Arduino, não tem `configure` nem `Makefile` nenhum.
   Confirmado via a API do GitHub Actions (`actions_list`,
   `list_workflow_runs`), não assumido: **100% das execuções deste
   workflow falharam** (5 falhas consecutivas revistas, 2026-07-04 a
   2026-07-07), dando um sinal falso de "CI quebrado" a cada commit sem
   testar nada de real. Substituído por uma CI que efetivamente compila o
   firmware com PlatformIO (`pip install platformio` + `pio run -e
   <ambiente>`), correndo os dois ambientes reais definidos em
   `platformio.ini` (`seeed-xiao-afruitnrf52-nrf52840-sense-plus` e
   `test_lora_isolated`) em matriz, com cache de `~/.platformio` entre
   execuções. **Isto fecha uma lacuna repetida em várias secções deste
   ficheiro hoje** ("não compilado — sem toolchain ARM nesta rotina
   cloud": nonce AES-CTR, cifra AES-CTR, pacing/giroscópio, deteção de
   emergência, etc.) — a partir do próximo push, há verificação real de
   compilação em CI, mesmo sem acesso a hardware ou toolchain local.
   **Confirmado a passar (2026-07-07, rotina cloud posterior)**: verificado
   via `actions_list`/`actions_get` da API do GitHub — as 4 execuções desde
   esta correção (`75dcb8e`, `e3c272d`, `4c060cc`, `dd3c05a`) terminaram
   `completed`/`success`; o "C/C++ CI" anterior tinha falhado 100% das
   vezes (5/5 execuções revistas). A CI do PlatformIO está mesmo a
   compilar o firmware real em cada push a `main`, não só a correr sem dar
   sinal de vida.
2. **Dois workflows GitHub Pages sem propósito real, removidos.**
   `.github/workflows/jekyll-gh-pages.yml` e `static.yml` eram também
   modelos genéricos do GitHub (nunca personalizados, nunca mencionados
   em nenhuma sessão anterior deste ficheiro), a disparar no push para um
   branch `"Main"` (maiúscula) — **confirmado via `git ls-remote` que
   existe mesmo um branch remoto extra `Main` (distinto de `main`)**,
   parado no commit `d996ac9`, quase certamente criado sem intenção pelo
   fluxo "Set up this workflow" da UI do GitHub. Este repositório não tem
   nenhum conteúdo de site (sem `_config.yml`, `Gemfile`, `index.md`) —
   publicar o repositório inteiro (firmware C++, chaves de configuração,
   `ml/`, etc.) como página do GitHub Pages não serve nenhum propósito
   deste projeto. Removidos os dois ficheiros. **Não removido** (fora do
   âmbito de uma limpeza de ficheiros, é uma operação destrutiva de git
   sobre uma ref partilhada): o branch remoto `Main` em si — fica
   registado aqui para o utilizador decidir apagá-lo
   (`git push origin --delete Main`) se confirmar que não tem uso.

Revisão dirigida a esta própria alteração (chavetas/aspas YAML validadas
com `yaml.safe_load` em Python, nomes dos ambientes conferidos byte a byte
contra `platformio.ini`) feita antes de commitar, como já é norma neste
projeto.

## Otimização diária de RAM/CPU/desempenho — 2026-07-07

Rotina diária de otimização (ver "Rotinas cloud agendadas" acima). Com as
reduções de stack FreeRTOS já feitas em rondas anteriores e sem
watermarks novos de hardware para justificar mais cortes de RAM, esta
execução procurou desperdício de CPU/latência em vez de RAM — ver as
duas secções detalhadas acima:

1. **Firmware**: eliminada uma leitura QSPI + descodificação redundante
   por registo no caminho de streaming BLE (`QspiRingBuffer::advanceTail()`,
   `src/QspiRingBuffer/QspiRingBuffer.cpp`/`.h`, usada em
   `src/Ble/Ble.cpp`) — ver secção "Otimização de CPU/flash (2026-07-07,
   rotina diária)" acima para o detalhe completo. **Pendente de
   confirmação em hardware real** (build não tentado localmente com
   sucesso — proxy do ambiente bloqueou o download da toolchain ARM do
   PlatformIO; a CI corrigida nesta mesma data deve compilar isto no
   primeiro push, ver "Verificação de bugs" acima).
2. **Bridge**: `bridge/storage.py` passou a usar
   `PRAGMA journal_mode=WAL` + `synchronous=NORMAL`, evitando que cada
   `conn.commit()` (chamado a cada registo de sensores, até ~52/seg)
   bloqueie o único thread do event loop `asyncio` do bridge com `fsync`
   síncrono — ver secção "Otimização de CPU (2026-07-07, rotina diária)
   — commit SQLite síncrono" acima para o detalhe completo. Validado
   localmente com uma base de dados temporária (Python real).

Áreas revistas sem alterações por não terem oportunidades novas e
concretas (já cobertas por rondas anteriores, ver secções respetivas
acima): `Serial.print`/`Serial.printf` em hot loops do firmware (IMU
52Hz, PPG, dump BLE) — já rate-limited ou atrás de `kGattDumpVerboseLogs`;
dashboard web (`web/dashboard/index.html`,
`web/dashboard/medication-reminders.js`) — canvases já só redesenham
quando a vista ativa muda, dados sintéticos já calculados uma vez ao
nível do módulo (não a cada render), listeners de eventos já anexados
uma única vez, sem `setInterval` demasiado agressivo. Nenhuma alteração
artificial foi feita nestas áreas só para ter algo a registar.

## Otimização diária de RAM/CPU/desempenho — 2026-07-08 (sem alterações)

Rotina firmware-optimization. `git fetch`/checkout de `origin/main`
confirmado (fast-forward, sem conflitos).

- **Baseline tentada e falhada, mesma causa de 2026-07-07**: `pip install
  platformio` seguido de `pio run` — o proxy do ambiente continua a
  bloquear `files.seeedstudio.com` (download da toolchain ARM), agora
  confirmado com detalhe via `$HTTPS_PROXY/__agentproxy/status` →
  `recentRelayFailures`: `connect_rejected`, "gateway respondeu 403 ao
  CONNECT", para `files.seeedstudio.com:443` e também
  `collector.platformio.org:443`. Sem `pio run` a compilar, não há
  RAM/Flash % de baseline nem forma de medir antes/depois nesta rotina —
  mesma limitação de ambiente já documentada, não resolvida entretanto.
- **Commits no firmware desde a última rotina de otimização
  (2026-07-07)**: só um, `ec8004a` (correção de mismatch de chave AES +
  comando de debug `CLEARKEY` pela série) — só corre quando o comando é
  recebido manualmente, não é hot path, sem relevância de CPU/RAM/energia.
- **Revisão dirigida sem hardware/build** (só leitura de código): auditoria
  de todas as chamadas `delay()` em `src/`/`include/` (26 ocorrências) —
  todas em arranque/calibração/debounce/`delayPollingEmergency` já
  existentes, nenhuma busy-wait nova dentro de um hot loop de task.
  Confirmado também que não há commits novos a tocar `Imu`/`Ppg`/`Ble`
  hot paths desde a auditoria de 2026-07-07. Sem watermarks reais novos
  desde 2026-07-03 (`DEBUG_STACK_WATERMARKS` continua sem leitura em
  hardware — ver "Riscos/bloqueios ativos", ponto 5), nenhuma stack pode
  ser tocada sem arriscar violar a margem mínima de 3x exigida.
- **Conclusão**: nenhum alvo com justificação mensurável disponível sem
  acesso a hardware físico ou à toolchain ARM (ambos bloqueados nesta
  rotina cloud). **Sem alterações de código nesta execução** — resultado
  esperado quando o firmware já está afinado, conforme os critérios de
  término desta rotina.

### Lista de candidatos a otimização (revista 2026-07-08)

| Candidato | Dado que falta | Estado |
|---|---|---|
| Confirmar 2ª ronda de stacks (`storage_task`/`ppg_task`/`ble_gatt_dump_task` → 768/640/1280 words) | Watermarks reais pós-alteração via `DEBUG_STACK_WATERMARKS` em hardware | Pendente, sem novidade desde 2026-07-03 |
| `imu_task` (768 words, margem ~1.5x) | Watermark mais recente/em pior caso, para avaliar se sobra alguma margem; não reduzir sem isso (regra de ≥3x) | Pendente, baixa prioridade — margem já apertada |
| GPS CAM-M8Q na placa sem nenhum código a inicializá-lo | Medição de corrente real (multímetro) em hardware — se o módulo não consumir em repouso sem `begin()`, não há nada a otimizar aqui | Pendente, precisa de hardware |
| LoRa Wio-SX1262 (`Lora::begin()` falha sempre nesta placa, pinout NSS por confirmar) | Medição de corrente após uma tentativa de `begin()` falhada, para confirmar que nenhum periférico fica "meio-ligado" a consumir | Pendente, precisa de hardware |
| Flags de debug ativas (`DEBUG_SERIAL_WAKE`, `DEBUG_STACK_WATERMARKS`) | Decisão do utilizador sobre quando desligar (botão físico continua partido; 2ª ronda de stacks continua por confirmar) | Fora do âmbito desta rotina — não decidir sozinho |

**Nota de processo**: a partir desta execução, esta rotina deixa de
publicar diretamente em `main` (alterado pelo utilizador) — passa a
committar numa branch dedicada (`rotina/firmware-optimization`) e a abrir/
atualizar um Pull Request contra `main`, mesmo em execuções sem alterações
de firmware (como esta), para que a atualização deste ficheiro também
passe por revisão.

## Verificação de bugs (rotina automática) — 2026-07-07, 3ª passagem do dia

Com as Prioridades 1-4 já confirmadas esgotadas por duas execuções
anteriores no mesmo dia (ver as duas secções imediatamente acima), esta
execução repetiu a Prioridade 1 (pesquisa aplicada: wearables de demência,
quedas, anomalias comportamentais, ML embarcado — `emlearn` suporte a
`loadable`+`float`, fórmula do "ground-face coordinate system", pacing/
giroscópio para wandering) sem encontrar nada de novo diretamente
acionável: os artigos com detalhe técnico concreto continuam bloqueados
(403) em todas as fontes tentadas (PMC, MDPI, dergipark, arXiv — mesmo
resultado já registado nas passagens anteriores), e as revisões
narrativas abertas só confirmam achados genéricos já documentados neste
ficheiro. Backlog do dashboard, bridge↔emergência/footprint TinyML e BD
SQLite confirmados sem itens novos — avançado para a Prioridade 5.

**Prioridade 5 (varredura completa de bugs)**: 4 revisões dirigidas em
paralelo (firmware C++, bridge Python, dashboard JS/HTML, pipeline `ml/`),
cada uma instruída a ler primeiro este ficheiro/`ml/README.md` para não
repetir os achados já corrigidos nas duas passagens anteriores desta
mesma data (nonce AES-CTR, `broadcast()`, fuga de fragmentos,
`medication-reminders.js`, `LabelEncoder`, CI). Encontraram problemas
**novos e distintos** dos já corrigidos — todos corrigidos nesta execução,
com revisão própria antes de commitar (repro isolado de cada bug em
Python onde possível, `node --check` sobre o `<script>` extraído do
dashboard, Playwright real para os fluxos de consentimento/exportação,
verificação de chavetas para o firmware — sem toolchain ARM nesta rotina
cloud, mesma limitação já registada):

1. **`bridge/storage_advanced.py` não conseguia sequer ser importado —
   corrigido.** Dois bugs que, juntos, tornavam este ficheiro (a "Base de
   Dados SQL Completa" descrita na secção acima, com 14 modelos
   SQLAlchemy) completamente inutilizável: (a) `from sqlalchemy import
   (..., JSONB, ...)` — `JSONB` só existe em
   `sqlalchemy.dialects.postgresql`, nunca no pacote `sqlalchemy` de topo;
   qualquer `import storage_advanced` rebentava com `ImportError` antes de
   correr uma única linha. Corrigido trocando por `sqlalchemy.JSON` (tipo
   genérico, portátil entre SQLite dev / PostgreSQL produção, como o
   próprio docstring do ficheiro já prometia). (b)
   `User.patients = relationship("Patient", secondary="patient_caregivers")`
   referenciava uma tabela de associação que nunca chegou a ser definida
   em lado nenhum (nem em `storage_advanced.py` nem em `schema.sql`) —
   configurar QUALQUER modelo deste ficheiro (não só `User`, porque o
   SQLAlchemy configura o registo de mappers em conjunto) rebentava com
   `InvalidRequestError`. Corrigido: nova `Table` `patient_caregivers`
   (patient_id, user_id, permissões `can_view_alerts`/`can_edit_notes`/
   `can_edit_medications`, ver item 10 do backlog do dashboard —
   "múltiplos cuidadores com permissões por papel", implementado só no
   dashboard/localStorage até agora, nunca no schema SQL de referência) e
   a tabela espelho equivalente em `schema.sql`. **Verificado de facto**:
   `python3 -c "import storage_advanced; from sqlalchemy.orm import
   configure_mappers; configure_mappers()"` — antes rebentava em duas
   fases distintas, agora corre sem erro.
2. **`Analytics.heart_rate_trends()` calculava o corte de "últimos N dias"
   com o fuso errado em qualquer servidor não-UTC — corrigido**
   (`bridge/storage_advanced.py`). `datetime.utcnow() - timedelta(...)`
   devolve um datetime "naive" que representa UTC, mas chamar
   `.timestamp()` nele interpreta-o como hora LOCAL do servidor — o corte
   ficava desviado pelo offset do fuso (confirmado reproduzindo com
   `TZ=America/Sao_Paulo`: diferença de exatamente 10800s/3h). Corrigido
   para `datetime.now(timezone.utc)` (datetime "aware", converte para
   epoch corretamente em qualquer fuso).
3. **Bridge: fragmento BLE com `frag_idx` corrompido podia rebentar o
   callback de notificação com `KeyError` não tratado — corrigido**
   (`bridge/ble_bridge.py`, `_on_dump_data`). Um único byte corrompido no
   ar (bit-flip, a mesma classe de problema já documentada para o nonce
   AES-CTR) podia produzir um `frag_idx` fora de `[0, frag_total)`; sem
   validação, `len(parts) == total` podia ser atingido com um índice em
   falta (ex.: índices 0,1,5 para total=3), e o `join()` de remontagem
   rebentava com `KeyError` dentro do callback do `bleak`. Corrigido com
   (a) validação de `frag_idx` antes de o guardar (rejeita e regista o
   fragmento inválido, sem alterar `_pending_fragments`) e (b) um
   `try/except KeyError` de defesa em profundidade à volta do `join()`
   propriamente dito. **Reproduzido diretamente**: um repro isolado com a
   sequência de índices corrompida 0,1,5 confirma que antes rebentava e
   agora é rejeitado na validação, antes sequer de chegar ao `join()`.
4. **`emergency_alerts` sem proteção contra duplicação por reentrega de
   notificação BLE — corrigido** (`bridge/storage.py`). Esta tabela é
   histórico permanente (nunca purgado pela política de retenção, por
   desenho) — se `emergencyAlertChar` alguma vez reentregasse a mesma
   notificação (ex.: replay pós-reconexão), o mesmo SOS/queda real seria
   contado duas vezes para sempre. Corrigido: índice único em
   `(alert_type, seq)` (criado com `try/except` para não impedir o
   arranque do bridge se uma instalação já existente tiver duplicados
   antigos) + `INSERT OR IGNORE` em `insert_emergency_alert()`.
   **Verificado**: inserir o mesmo alerta duas vezes numa BD SQLite
   temporária resulta em 1 linha, não 2.
5. **Firmware: comando de teste "SOS" por série era impossível de
   disparar — corrigido** (`src/main.cpp`). `serialCommandReceived(cmd)`
   usava um buffer `static` partilhado por TODAS as chamadas à função,
   independentemente do `cmd` pedido; `loop()` chama esta comparação duas
   vezes por iteração (primeiro "SLEEP", depois "SOS"). Escrever "SOS" no
   monitor série era sempre lido, comparado com "SLEEP", descartado por
   não corresponder, e a chamada seguinte para "SOS" já não tinha nada
   para ler — o único caminho documentado para testar
   `Emergency::triggerTestAlert()` sem o botão físico (partido/por
   confirmar) nunca funcionava, 100% das vezes. Corrigido com
   `pollSerialLine()`: a linha é lida uma única vez por iteração e
   comparada com ambos os candidatos sobre a MESMA linha (ver comentário
   no código). `serialCommandReceived("WAKE")`, usado em contextos
   isolados (`waitForLongPress()`, arranque), continua a funcionar sem
   alteração de comportamento.
6. **Firmware: gesto físico de 3 cliques (SOS manual) provavelmente não
   funcional tal como estava — mitigado** (`src/main.cpp`).
   `Emergency::update()` (que lê o botão para o gesto de cliques) só era
   chamado uma vez por iteração de `loop()`, e essa iteração é dominada
   por `delay(50)+delay(950)` — uma amostragem de ~1Hz, muito abaixo do
   necessário para contar 3 cliques dentro de `sosClickWindowMs` (1200ms
   por omissão, `Emergency.h`): a maioria das bordas de descida do botão
   ficava sem ser vista entre uma chamada e a seguinte. Corrigido com
   `delayPollingEmergency()`, que substitui os dois `delay()` bloqueantes
   por uma espera com a mesma duração total mas que chama
   `Emergency::update()` a cada ~5ms — amostragem ~200x mais frequente,
   confortavelmente acima do necessário para o gesto configurado. **Não
   testado em hardware real** (mesmo bloqueio de sempre — USB
   intermitente, ver "Riscos/bloqueios ativos") — só revisto por leitura
   cuidadosa do fluxo de chamadas; a lógica de `waitForLongPress()` em si
   (usada só para o long-press físico de ligar/desligar) não foi alterada,
   por ser um gesto distinto (segurar, não tocar) sem o mesmo problema de
   amostragem.
7. **Firmware: confirmação de SOS pendente não era segura a overflow de
   `millis()` — corrigido** (`src/Emergency/Emergency.cpp`). A comparação
   `nowMs >= s_sosConfirmDeadlineMs` (aritmética direta, não por subtração)
   falharia se `millis()` desse a volta (~49.7 dias de uptime contínuo)
   exatamente durante uma janela de confirmação pendente, atrasando o
   alerta até ao próximo overflow em vez dos poucos segundos configurados.
   Corrigido para o mesmo padrão seguro a overflow já usado noutras partes
   do firmware (`Ppg.cpp`/`Ble.cpp`): `static_cast<int32_t>(nowMs -
   s_sosConfirmDeadlineMs) >= 0`.
8. **`ml/train_lstm_autoencoder.py`: treino não era de facto determinístico
   apesar do README prometer "seed fixa = 42, todos os scripts
   determinísticos" — corrigido.** Só a geração dos dados sintéticos
   (`rng = np.random.default_rng(SEED)`) estava semeada; a inicialização
   dos pesos LSTM/Dense e o `shuffle=True` do `model.fit()` dependiam do
   RNG global do TensorFlow/Keras, nunca semeado — duas execuções com os
   mesmos dados produziam pesos finais e métricas (`detection_threshold_mse`,
   `recall_at_threshold`, `auc_roc`) ligeiramente diferentes de cada vez.
   Corrigido com `keras.utils.set_random_seed(SEED)` antes da construção
   do modelo (cobre Python/NumPy/TensorFlow numa só chamada). **Não
   executado nesta rotina** (tensorflow não está instalado neste
   ambiente) — mudança revista por leitura direta, mas ainda por
   confirmar reexecutando o script e comparando métricas entre duas
   corridas.
9. **`ml/train_activity_classifier.py`/`_rf.py`: `classification_report()`
   podia rebentar mesmo depois do `LabelEncoder` já corrigido (sessão
   anterior) — corrigido.** `classification_report(y_test, y_pred,
   target_names=encoder.classes_, ...)` sem `labels=` explícito deriva os
   labels de `unique(y_test, y_pred)` — se uma classe ficasse com zero
   exemplos em AMBOS `y_test` e `y_pred` (um cenário distinto do já
   corrigido "classe ausente só do treino"), o nº de labels derivados
   fica menor que `len(target_names)` e a chamada rebenta com
   `ValueError`. **Reproduzido diretamente** (4 labels presentes vs. 5
   `target_names` → `ValueError: Number of classes, 4, does not match
   size of target_names, 5`). Corrigido adicionando
   `labels=range(len(encoder.classes_))` em ambos os scripts (o mesmo
   padrão que `confusion_matrix()`, na linha seguinte, já usava
   corretamente).
10. **`ml/features.py`: off-by-one na taxa de cruzamentos por zero
    (`_zero_crossing_rate`) — corrigido.** `np.diff(signs)` tem
    `len(signal)-1` elementos (nº de transições possíveis), mas a função
    dividia por `len(signal)` — uma subestimação sistemática de ~0.2% ao
    tamanho de janela atual (520 amostras), pequena mas uma fórmula
    estatística incorreta, não uma escolha de estilo. Corrigido para
    dividir por `len(signal)-1` (definição-padrão de zero-crossing rate).
    **Verificado**: um sinal alternado perfeito (`[1,-1,1,-1,...]`, todas
    as transições são cruzamentos) devolve agora `1.0` (100%), como
    esperado; antes devolvia um valor sistematicamente abaixo de 1.0.
11. **Dashboard: exportação clínica (FHIR/PDF) e a pill de "Alertas
    ativos" na vista Pacientes ignoravam por completo o interruptor de
    consentimento "Alertas e registo de anomalias" — corrigido**
    (`web/dashboard/index.html`). As vistas "Pacientes"/"Anomalias
    detetadas" já escondiam corretamente esta informação quando o
    utente/família desligava a partilha (cartão "Consentimento e partilha
    de dados", Definições), mas `buildFhirBundle()`, `exportClinicalPdf()`
    e a contagem de alertas ativos na tabela de pacientes continuavam a
    incluir/mostrar os dados reais de qualquer forma — o Médico/Técnico
    conseguia contornar o bloqueio só usando um caminho diferente da
    interface. Corrigido: as três leituras agora consultam
    `loadConsent(patientId).shareAlerts` antes de incluir alertas/
    anomalias, substituindo por uma nota explícita quando desligado.
    **Verificado com Playwright real**: `buildFhirBundle()` com
    consentimento desligado devolve só uma Observation "consent-withheld"
    (sem dados reais); ligado, devolve as Observations normais; a pill da
    tabela de Pacientes mostra "Sem consentimento" para esse paciente
    específico sem afetar os outros.
12. **Dashboard: consentimento e equipa de cuidadores eram globais em vez
    de por paciente — corrigido** (`web/dashboard/index.html`,
    `CONSENT_KEY`/`CAREGIVER_TEAM_KEY`). Ao contrário de todos os outros
    dados adicionados desde a correção multi-paciente de 2026-07-03
    (alertas, notas, medicação, silenciamentos...), estas duas chaves de
    `localStorage` guardavam um único objeto/lista global — na vista
    Médico/Técnico com 3 pacientes fictícios, desligar o consentimento ou
    editar a equipa de cuidadores de um paciente afetava identicamente os
    outros dois. Corrigido seguindo a mesma convenção já usada para
    `medicationLog` (um único item de `localStorage`, indexado por
    `patientId` por dentro); `loadConsent`/`setConsent`/
    `loadCaregiverTeam`/`saveCaregiverTeam` ganharam um parâmetro
    `patientId` opcional (omissão: `selectedPatient().id`) — nenhuma
    chamada existente no resto do ficheiro precisou de mudar. **Verificado
    com Playwright real**: consentimento/equipa desligados e editados para
    um paciente não afetam o segundo paciente (valores lidos de volta
    corretos e distintos para cada um).
13. **Dashboard: nome de cuidador não escapado antes de `innerHTML` —
    XSS corrigido** (`web/dashboard/index.html`, cartão "Equipa de
    cuidadores"). `<td>${m.name}</td>` inseria o texto livre do campo
    "Nome do novo cuidador/familiar" sem qualquer escaping, ao contrário
    do padrão já usado para as Notas do cuidador
    (`n.text.replace(/</g,'&lt;')`) — um nome contendo `<img src=x
    onerror=...>` corromperia a tabela ou executaria script a cada
    renderização da vista Definições. Corrigido com o mesmo padrão de
    escaping (`&` antes de `<`, ordem correta para não escapar
    duplamente). **Verificado com Playwright real**: um nome de teste
    `<b>X</b> & Co` aparece escapado (`&lt;b&gt;X&lt;/b&gt; &amp; Co`) no
    DOM em vez de ser interpretado como HTML.
14. **Dashboard: ramo morto no índice de pacing — corrigido**
    (`web/dashboard/index.html`, `renderPacingSummary`). `const level =
    today >= 60 ? 'warning' : today >= 40 ? 'good' : 'good'` — as duas
    saídas do ternário aninhado eram idênticas (`'good'`), tornando a
    condição `today >= 40` sem efeito nenhum; o próprio `levelLabel`,
    logo a seguir, já só distinguia 2 estados. Simplificado para `today
    >= 60 ? 'warning' : 'good'`, sem mudança de comportamento observável
    (o resultado já era sempre este).

**Nota de sincronização**: a revisão do bridge desta execução tinha
assinalado, de forma independente, o mesmo risco de `conn.commit()`
síncrono bloqueando o event loop do `asyncio` a cada registo de sensores
(~14-52/seg) já identificado e corrigido pela rotina de otimização diária
em paralelo (ver secção "Otimização diária de RAM/CPU/desempenho —
2026-07-07" imediatamente acima, `journal_mode=WAL` + `synchronous=NORMAL`
em `bridge/storage.py::get_connection()`) — confirmado via `git rebase`
antes deste push, sem conflito real de código (ficheiros diferentes desta
mesma correção coexistem sem sobreposição: `get_connection()` ganhou os
PRAGMAs de desempenho, `init_db()`/`insert_emergency_alert()` ganharam a
proteção contra duplicados desta execução). Não repetido aqui por já
estar corrigido.

Revisão dirigida a cada uma das 14 correções acima feita antes de
commitar (repro isolado em Python para os bugs do bridge/ml, Playwright
real para os 4 do dashboard, `node --check` sobre o `<script>` extraído,
contagem de chavetas para o firmware sem toolchain ARM disponível nesta
rotina cloud).
## Modelo de Machine Learning — item 4 do roteiro concluído (2026-07-07, rotina cloud)

Nota de processo (não uma alteração de código): esta execução começou com
o checkout local desta rotina cloud desatualizado face ao `origin/main`
real (um `git fetch` explícito confirmou isto e resolveu com um simples
fast-forward, sem perda de trabalho nenhuma) — registado aqui só como
lembrete para futuras rotinas: fazer sempre `git fetch origin main` antes
de assumir o estado do repositório, especialmente havendo múltiplas
sessões/rotinas a correr sobre o mesmo repositório no mesmo dia.

Progresso concreto (Prioridade 3, item 4 do roteiro em `ml/README.md`,
"Tornar os dados sintéticos mais realistas"), com ambiente Python+ML
instalado nesta rotina (`pip install -r ml/requirements.txt`, mais
`gcc-arm-none-eabi` via `apt-get` para conseguir remedir o footprint real
do Random Forest):

- **Sobreposição deliberada entre classes vizinhas** em `synthetic_data.py`
  (`CLASS_PARAMS` passou de constantes fixas por classe a intervalos
  amostrados por janela, com overlap intencional ex.: "Atividade" vs.
  "Higiene") — corrige a limitação já assinalada de as classes serem
  artificialmente bem separáveis (accuracy XGBoost 1.000, suspeita, não
  prova de qualidade).
- **Sessões de 24h completas** em vez de comprimidas (16h+8h = 1440 min,
  antes 240+90=330 min).
- **Corrigido o corte artificial do último bloco de cada sessão**
  (`_build_segment_sequence` deixou de forçar `dur = min(dur, remaining)`)
  — eliminou por completo o achado de falsos positivos do detetor de
  duração (passo 3): 7.17%→**0.0%**.
- **Não feito** (não podia ser, honestamente): ruído medido em hardware
  real em vez de estimado — continua bloqueado pela indisponibilidade da
  placa (ver "Riscos/bloqueios ativos", ponto 8).

**Nota de sincronização (mesmo dia)**: a meio desta execução, um
`git fetch`/rebase revelou que uma rotina paralela (ver "Verificação de
bugs — 3ª passagem" acima) tinha corrigido, no mesmo dia, três bugs em
`ml/features.py`/`train_activity_classifier.py`/`_rf.py`/
`train_lstm_autoencoder.py` (off-by-one no `_zero_crossing_rate`,
`classification_report()` sem `labels=` explícito, seed do TensorFlow não
fixada) — os modelos já treinados nesta execução tinham sido gerados
ANTES dessas correções chegarem via rebase. Para não deixar artefactos
commitados desalinhados com o código corrigido, todo o pipeline foi
reexecutado depois do rebase (dataset, XGBoost, Random Forest, footprint,
detetor de duração, LSTM Autoencoder) — os números abaixo já refletem essa
segunda execução, pós-correção. Efeito prático das correções nos números:
nenhum na accuracy do classificador (o off-by-one é ~0.2% da janela, sem
impacto mensurável), e o LSTM Autoencoder passou a ser reproduzível entre
execuções graças a `keras.utils.set_random_seed`.

Todos os modelos foram retreinados/reavaliados sobre o novo dataset (72 402
janelas, antes 15 840) e os relatórios/modelos em `ml/models/`/`ml/reports/`
substituídos em conformidade — resultados honestos, não inventados:
XGBoost 1.000→**0.996**, Random Forest 0.978→**0.992**, footprint do RF
remedido com o toolchain ARM real (~14-28KB de flash conforme a variante,
mesma conclusão qualitativa de antes: quantização `int16_t` destrói
accuracy, `float` recupera-a), detetor de duração recall 0.925-1.000 (era
0.972-1.000, variação normal de reamostragem, não um efeito da correção).
**Achado novo, honesto, não escondido**: o LSTM Autoencoder perdeu muita
precisão a um limiar fixo (0.276→0.035) ao mudar para sessões de 24h — não
por o modelo ter piorado (AUC-ROC manteve-se na mesma gama, 0.80-0.93 por
tipo), mas porque uma anomalia de duração fixa passou a ser uma fatia bem
menor de um dia inteiro, um efeito de diluição/desequilíbrio de classes
esperado, não um bug. Ver `ml/README.md` (secções "Passo 1", "Passo 2",
"Passo 3" e "Próximos passos") para o detalhe completo e a interpretação
honesta de cada número.

**Ainda por fazer** (ver `ml/README.md`, "Próximos passos"/"Decisão
pendente"): ruído real de hardware (bloqueado), decisão do utilizador entre
XGBoost (`micromlgen`, sem manutenção) e Random Forest (`emlearn`, mantido)
para uma eventual versão embarcada, e um limiar/métrica do LSTM Autoencoder
menos sensível ao desequilíbrio introduzido pelas sessões de 24h (ex.:
PR-AUC em vez de contagem de falsos positivos a um limiar fixo — não
implementado nesta execução).

## Dashboard web — 4 bugs reais corrigidos (2026-07-07, rotina cloud)

Com o backlog do dashboard (itens 1-10) e três rondas de varredura de bugs
já feitas hoje por rotinas paralelas (ver secções "Verificação de bugs"
acima), esta rotina pediu uma revisão independente e dedicada só ao
dashboard (`web/dashboard/index.html` + `medication-reminders.js`),
instruída a não repetir os achados já corrigidos e a só reportar bugs
reais, reproduzíveis. Encontrou 4 problemas novos, todos corrigidos e
verificados nesta execução:

1. **O ponto vermelho de notificações ficava preso para sempre depois de
   apagar um alerta não lido sem o marcar como lido primeiro — corrigido**
   (`updateNotificationBadge()`). Calculava `hasUnread` a partir de
   `p.alerts` em bruto (inclui alertas já apagados), ao contrário de todo
   o resto da UI que usa `unreadActiveAlerts()` (exclui apagados).
   Cenário: paciente com um alerta não lido; Médico/Técnico vai a
   "Histórico de alertas" e clica "Apagar" diretamente — o alerta some da
   interface, mas o ponto vermelho fica aceso para sempre, sem nenhuma
   ação na interface capaz de o desligar (o botão "Marcar como lida"
   também já não existe, porque o alerta foi apagado). Corrigido: a
   função passou a usar `unreadActiveAlerts().length > 0`. **Verificado
   com Playwright real**: 4 alertas não lidos apagados diretamente (sem
   marcar como lidos) — badge visível antes, escondido depois,
   `unreadActiveAlerts()` a 0.
2. **Lembretes de medicação paravam de disparar a partir do 2º dia de
   utilização contínua — corrigido** (`medication-reminders.js`,
   `checkAndNotify()`). `notifKey` não incluía a data (só
   paciente+medicamento+hora); `shownNotifications` (um `Set` em memória)
   só era limpo se, por coincidência, alguma dose estivesse agendada perto
   da meia-noite. Cenário: cuidador deixa o dashboard aberto (uso normal
   de um painel de monitorização contínua) — o lembrete das 08:00 do dia 1
   fica guardado na chave `pX_medY_08:00`; no dia 2, à mesma hora, a
   mesma chave já existe no Set e a notificação nunca mais aparece,
   silenciosamente, até a página ser recarregada. Corrigido: `notifKey`
   passou a incluir a data local (`AAAA-M-D`), com poda das chaves de dias
   anteriores a cada verificação (evita o Set crescer sem limite). **Verificado
   isoladamente** (script Node com `Date` simulado via `vm`, sem browser):
   notificação dispara no dia 1, não duplica na mesma chamada do mesmo
   dia, e dispara de novo no dia 2 — confirmando que o bug estava mesmo
   presente antes da correção e desaparece depois.
3. **XSS real: texto livre inserido em `innerHTML` sem escaping em 5
   pontos — corrigido** (`web/dashboard/index.html`). Nome de medicamento
   (tabela de doses de hoje e "Gerir medicação"), valor de campo de perfil
   sensível pendente de aprovação (NIF/morada, nas duas vistas — Utente e
   Médico/Técnico) entravam diretamente em `innerHTML`, ao contrário do
   padrão de escaping já usado para notas do cuidador/nome de cuidador.
   Cenário: Médico/Técnico introduz `<img src=x onerror=...>` como nome
   de medicamento — o script injetado executaria sempre que a vista
   "Medicação" desse paciente fosse aberta, nesta sessão ou em qualquer
   sessão futura no mesmo browser (persistido em `localStorage`).
   Corrigido: nova função utilitária `escapeHtml()` (generaliza o padrão
   já usado noutros pontos, incluindo agora aspas `"` para uso seguro
   também em atributos), aplicada aos 5 pontos identificados. **Verificado
   com Playwright real**: nome de medicamento `<img src=x
   onerror="window.__xss=true">` submetido via formulário real — o script
   NÃO executa (`window.__xss` continua `undefined`) e o DOM mostra o
   texto escapado (`&lt;img src=x onerror=...`).
4. **Interruptores de consentimento/permissões sem nome acessível (WCAG
   4.1.2) — corrigido** (`web/dashboard/index.html`, cartões
   "Consentimento e partilha de dados" e "Equipa de cuidadores"). Os 5
   checkboxes (`.consent-toggle`) não tinham `aria-label` nem texto
   associado além de um `<span>` puramente decorativo (o "toggle" visual)
   — um utilizador de leitor de ecrã ouvia só "checkbox, não marcado",
   sem saber o que estava a ativar/desativar numa funcionalidade
   explicitamente "eticamente sensível" (consentimento de dados clínicos).
   Corrigido: `aria-label` descritivo em cada um dos 5 checkboxes
   (reutilizando `escapeHtml()` para os que incluem o nome do cuidador).
   **Verificado com Playwright real**: 5/5 checkboxes com `aria-label`,
   texto do primeiro confirmado ("Partilhar sinais vitais (FC, SpO₂,
   passos) com a equipa clínica").

Verificação adicional feita antes de commitar: `node --check` sobre o
`<script>` extraído do `index.html` (última ocorrência da tag, mesma
lição já registada noutra sessão sobre o bug de extração) e sobre
`medication-reminders.js` — sem erros de sintaxe.

## Modelo de Machine Learning — PR-AUC no LSTM Autoencoder (2026-07-07, rotina cloud)

Nota de processo: esta execução começou (como já é norma) com `git fetch
origin main` + rebase antes de decidir o que fazer — o checkout local
estava 57 commits atrás do `origin/main` real. Isso evitou duplicar
trabalho: a correção de `bridge/storage_advanced.py` que esta execução
tinha preparado de forma totalmente independente (mesmos 3 bugs: import
`JSONB` inválido, tabela `patient_caregivers` em falta, `Alert.deleted_at`
em falta) já tinha sido feita e commitada horas antes por outra rotina
paralela (ver "Verificação de bugs — 3ª passagem", item 1, e o commit
"Adiciona 1ª suite de testes a storage_advanced.py") — descartado sem
commitar, para não duplicar, tal como já é prática registada neste
ficheiro noutras ocasiões.

Com as Prioridades 0-2 e 7-8 bloqueadas (hardware/decisão do utilizador), a
Prioridade 3 (app móvel) fora do âmbito de uma execução autónoma (exige
decisões de arquitetura/plataforma), e a Prioridade 4 (BD SQL) já
extensivamente corrigida e testada por rotinas paralelas nesta mesma data,
esta execução avançou para o item concreto mais recente e ainda por fazer
do roteiro de `ml/` (Prioridade 5/roteiro `ml/README.md`, "Próximos
passos"): a métrica PR-AUC, registada horas antes por outra rotina como
"próximo passo honesto, não implementado" depois do achado de que a
precisão do LSTM Autoencoder a um limiar fixo colapsou (0.276→0.035) ao
mudar para sessões sintéticas de 24h.

**Implementado e executado de facto** (ambiente com `pip install -r
ml/requirements.txt`, incluindo `tensorflow-cpu`, instalado nesta rotina):
`train_lstm_autoencoder.py` passou a calcular `average_precision_score`
(PR-AUC/average precision), geral e por tipo de anomalia, ao lado do
AUC-ROC já existente, e a registar a prevalência da classe anómala no
conjunto de avaliação (`eval_anomalous_prevalence`) para dar contexto ao
número — um PR-AUC só é interpretável em relação à prevalência de base,
não isoladamente. Reexecutado o script (mesma seed=42, mesma geração de
dados — os valores de `auc_roc`/`recall`/`precision` já existentes saíram
byte a byte idênticos aos já commitados, confirmando que é mesmo uma
adição pura de métrica, não uma retreinagem com resultados diferentes).

**Resultado honesto**: PR-AUC geral = **0.040**, ~3,1x acima da
prevalência de base (0.013) — confirma que o modelo continua a ordenar
subsequências anómalas melhor do que o acaso (mesma direção que o AUC-ROC
já sugeria), mas o valor absoluto continua baixo. Por tipo:
`substituicao_contextual` continua a mais distinguível (PR-AUC 0.143,
~7,3x a sua prevalência), `duracao_prolongada` e `truncamento` bem mais
fracos (2,1x e 5,2x a respetiva prevalência, PR-AUC absoluto <0.06). **Não
é uma correção do problema de fundo** — só uma medição mais justa dele:
PR-AUC não fabrica sensibilidade que o modelo não tem, só evita que um
AUC-ROC estável esconda o colapso de precisão a um limiar fixo. A mitigação
real (limiares por pessoa/contexto) continua por implementar, bloqueada
pela ausência de histórico real por pessoa (depende da Prioridade 4 — Base
de Dados — estar em produção, o que ainda não está).

Ficheiros alterados: `ml/train_lstm_autoencoder.py` (código da métrica),
`ml/reports/lstm_autoencoder_metrics.json` (novos campos `pr_auc`,
`pr_auc_vs_eval_normal` por tipo, `eval_anomalous_prevalence`,
`pr_auc_note`), `ml/models/lstm_autoencoder.keras` (retreinado, métricas
idênticas às anteriores — ver acima), `ml/README.md` (secção "Passo 2" e
item 5 novo em "Próximos passos"). Ver `ml/README.md` para o detalhe
completo e a interpretação honesta por tipo de anomalia.

## `bridge/requirements_db.txt` inválido para o `pip` + CI de testes do bridge (2026-07-07, rotina cloud)

Nota de processo (já norma): `git fetch origin main` + fast-forward antes de
começar — o checkout local estava 61 commits atrás do `origin/main` real
(as três passagens de varredura de bugs, a correção de `ml/`/PR-AUC e a
nova API REST já lá estavam). Com Prioridades 0-2 e 6-8 bloqueadas
(hardware/decisão do utilizador) e a Prioridade 3 (app móvel) fora do
âmbito de uma execução autónoma, e depois de rever o histórico do dia (já
três varreduras de bugs feitas, dashboard/`ml/`/CI já corrigidos por
rotinas paralelas), esta execução tentou reproduzir de facto o passo
"`pip install -r bridge/requirements_db.txt`" descrito em `bridge/README.md`
e nesta mesma secção acima (protocolo "medir, não assumir" já seguido
noutras sessões) — em vez de assumir que continuava a funcionar.

**Bug real encontrado e corrigido**: `bridge/requirements_db.txt` usa `;`
como marcador de comentário em todas as suas linhas (herdado desde a sua
criação em 2026-07-04) — mas o formato de `requirements.txt` do `pip` só
reconhece `#` como comentário; `;` depois de um nome de pacote é
interpretado como um **marcador de ambiente** (`nome; python_version>=...`).
A primeira linha do ficheiro é *só* um comentário (`; Dependências
Python...`), sem nome de pacote nenhum antes do `;` — `pip` tenta compilar
isso como uma `Marker` vazia e rebenta imediatamente com
`InvalidMarker: Expected a marker variable or quoted string`, **antes de
instalar uma única dependência**. Reproduzido diretamente (venv limpo,
`pip install -r bridge/requirements_db.txt`): falha 100% das vezes, para
qualquer pessoa que siga literalmente a instrução já documentada em
`bridge/README.md` (duas ocorrências) e nesta secção do ficheiro. Isto
bloqueava por completo o caminho de instalação documentado para
`storage_advanced.py`, Alembic, `crypto_utils.py` e `bridge/api.py` — tudo
o que a Prioridade 4 construiu nas últimas sessões. **Corrigido**: todos os
comentários (de linha inteira e em fim de linha) trocados de `;` para `#`,
sem alterar nenhuma versão/dependência.

**Verificado de facto, não só assumido** (venv limpo nesta rotina cloud):
- `pip install -r bridge/requirements_db.txt` — sucesso completo, todas as
  dependências instaladas (incluía já `fastapi`/`uvicorn`/`httpx` da API
  REST e `pytest`/`pytest-asyncio` dos testes).
- `python -m py_compile` sobre os 5 módulos do bridge (`ble_bridge.py`,
  `storage.py`, `storage_advanced.py`, `crypto_utils.py`, `api.py`) — sem
  erros de sintaxe.
- `cd bridge && python -m pytest tests/ -v` — **46/46 testes passam** (a
  suite cresceu desde os "16/16" registados na sessão que criou
  `test_storage_advanced.py": entretanto `test_crypto_utils.py` e
  `test_api.py` foram adicionados por sessões paralelas, também confirmados
  a passar aqui).

**Lacuna relacionada, também corrigida**: não existia nenhuma CI a correr
esta suite de testes Python — só o firmware C++ tinha CI real (`c-cpp.yml`,
corrigido numa sessão anterior no mesmo dia). Ou seja, todas as correções
recentes a `storage_advanced.py`, `crypto_utils.py`, `ble_bridge.py` e
`bridge/api.py` (várias delas nas 3 passagens de varredura de bugs de hoje)
dependiam inteiramente de cada rotina lembrar-se de instalar dependências e
correr `pytest` manualmente — sem nenhuma rede de segurança automática a
apanhar uma regressão futura num push. Adicionado
`.github/workflows/bridge-tests.yml` (novo, mesmo padrão do `c-cpp.yml`
já existente): instala `bridge/requirements_db.txt` com cache de pip e
corre `pytest tests/ -v` a partir de `bridge/`, em cada push/PR para
`main`. YAML validado com `yaml.safe_load` antes de commitar (mesma
prática já usada na correção da CI do firmware).

**Ainda por fazer** (fora do âmbito desta correção pontual): não existe CI
equivalente para `ml/` (os scripts de treino são pesados — TensorFlow,
vários minutos — e produzem artefactos versionados manualmente; correr o
pipeline completo em CI a cada push exigiria decidir o que testar de facto,
ex.: só `duration_detector.py`/`features.py` com `pytest`, sem retreinar os
modelos — não decidido nem implementado aqui). `storage_advanced.py`
continua sem ligação real ao streaming BLE (`ble_bridge.py` usa
`storage.py`, ver limitação já registada na secção da suite de testes,
acima) — este CI só protege contra regressões de import/lógica, não é um
teste de integração ponta a ponta.

**Confirmado a passar em CI real (2026-07-07, mesmo push)**: verificado via
`actions_get`/`get_workflow_run` da API do GitHub — a primeira execução do
novo workflow "Bridge Python tests" (`run_id=28888296568`, commit
`7e046fe`) terminou `completed`/`success`. Não é só um ficheiro YAML válido
sem sinal de vida (a mesma lição já registada para a correção da CI do
PlatformIO) — está mesmo a instalar `bridge/requirements_db.txt` e a correr
os 46 testes reais em cada push a `main`.

## Sessão de hardware real (2026-07-07, placa ligada e no pulso do utilizador)

**Bug crítico encontrado e corrigido — mismatch de chave AES fazia o
dashboard mostrar valores impossíveis em loop**: reportado pelo
utilizador como "FC/passos/aceleração malucos, a variar sem parar".
Diagnosticado amostrando registos reais do bridge (`ws://localhost:8765`):
`ax`/`ay`/`az`/`gx`/`gy`/`gz` na ordem de 10^20-10^30, `steps` na casa dos
milhares de milhão, `hr`/`spo2` negativos — sintoma clássico de decifrar
com a chave AES errada (XOR com keystream que não corresponde). Causa
confirmada: a chave em `bridge/device_key.env` já não batia certo com a
chave gravada na flash interna do dispositivo, e `aesKeyChar` só aceita a
**primeira** escrita por design (`aesKeyCallback()` em `Ble.cpp`) — um
reprovisionamento normal via `provision_key.py` era ignorado silenciosamente
pelo firmware.

Corrigido com um novo caminho de recuperação, mínimo e explícito:
- `Storage::removeAesKey()` (novo, `Storage.cpp`/`.h`) apaga só o ficheiro
  da chave em `InternalFS`, sem tocar no ring buffer QSPI (chip externo,
  módulo separado) nem exigir apagar toda a flash do dispositivo.
- Comando de debug `CLEARKEY` pela série (`main.cpp`, mesmo bypass já
  usado por `WAKE`/`SLEEP`/`SOS` enquanto o botão físico está partido) —
  chama `removeAesKey()` só quando pedido explicitamente, nunca automático.
- Fluxo usado e confirmado: parar o bridge (liberta a ligação BLE) → `WAKE`
  → `CLEARKEY` pela série → `provision_key.py` (grava a chave de
  `device_key.env`) → reiniciar o bridge → registos voltaram a valores
  fisicamente plausíveis (`ax≈-0.09, ay≈0.25, az≈0.96` ~1g, `steps=42`
  coerente, sem avisos de implausibilidade).
- **Rede de segurança adicionada** (`bridge/ble_bridge.py`,
  `is_plausible_full_plain()`): rejeita e regista um aviso único (não
  spam) para qualquer registo decifrado com aceleração/giroscópio/passos/
  FC/SpO2 fora de limites fisicamente possíveis, em vez de o encaminhar ao
  dashboard/BD. Não resolve a causa raiz de um futuro mismatch, mas evita
  que volte a aparecer como "dados malucos" na interface — aparece só o
  aviso no log do bridge.
- **Nota honesta**: nunca se confirmou COMO a chave da flash divergiu de
  `device_key.env` (reflash anterior? reprovisionamento a meio de um
  teste anterior?) — não há forma de prevenir isto sem uma app de
  provisioning adequada (limitação já documentada em `bridge/README.md`).

**Dashboard: countdown do "Medir agora" implementado** (pedido antigo,
nunca feito) — `web/dashboard/index.html`, `startForceReadingCountdown()`.
Mostra os segundos restantes (~15s, igual a
`DUMP_CTRL_FORCE_READING_SECONDS` em `bridge/ble_bridge.py`, os dois lados
não partilham este valor em runtime — manter sincronizado manualmente se
um mudar) enquanto espera o `command_result`; reativa o botão com aviso
honesto se a notificação BLE se perder no ar sem resposta dentro do tempo
esperado. Verificado com esprima (limitação conhecida do projeto: não
reconhece `??`/`?.`, usados noutras partes pré-existentes do ficheiro,
não relacionadas com esta alteração) + revisão manual do diff.

**Teste real de HR — primeira confirmação de que a correção do
`sampleAverage` funciona, mas com um problema novo encontrado**: com a
placa no pulso do utilizador e um "Medir agora" disparado com o bridge a
correr, capturada leitura série ao vivo pela primeira vez durante um
pedido de HR (nunca antes conseguido, ver "Próximas tarefas" acima — 0
registos com HR válido em toda a história). Resultado:
```
[PPG] HR stream ON
[PPG] HR beat -> 182 bpm
[PPG] HR beat -> 180 bpm
[PPG] HR beat -> 176 bpm
[PPG] HR beat -> 177 bpm
[PPG] HR beat -> 175 bpm
[PPG] HR beat -> 178 bpm
[PPG] HR beat -> 179 bpm
[PPG] HR beat -> 184 bpm
[PPG] HR beat -> 187 bpm
[PPG] HR stream OFF
```
**Positivo**: `sampleAverage=8→1` (correção de 2026-07-04) resolveu de
facto o desfasamento de taxa de amostragem — o pipeline de filtros agora
recebe deteções de batimento pela primeira vez.
**Problema novo, ainda por corrigir**: os valores (175-187 bpm sustidos)
são implausíveis para uma pessoa em repouso, e o padrão sugere que o
detetor está preso perto do limite superior do anti-rebote (`detectHeartbeat()`
em `Ppg.cpp`, 300ms = 200 BPM máx) — os intervalos entre batimentos
detetados rondam 320-340ms, não os ~600-1000ms esperados para uma FC de
repouso normal (60-100 bpm). Hipótese mais provável (não confirmada):
`detectHeartbeat()` deteta cruzamentos de zero da derivada sem exigir uma
amplitude mínima de pico — ruído/harmónicos do sinal filtrado (ou o LPF a
5Hz ainda deixar passar componentes acima da frequência cardíaca real)
podem estar a gerar cruzamentos de zero extra por cada batimento
verdadeiro, inflacionando a contagem. **Não corrigido nesta sessão** —
precisa de mais leitura série ao vivo (idealmente com o sinal PPG em bruto
exportado, não só o BPM final) para confirmar a hipótese antes de mexer no
algoritmo. Passa a ser o próximo item de maior prioridade para a rotina de
firmware.

**SpO2**: confirmado `spo2=100%` uma vez no mesmo teste (`[PPG] SPO2
minuto -> 100%`), consistente com o único registo válido já visto
anteriormente — continua com pouquíssimos dados para validar a fundo.

**Ainda por testar nesta ronda de hardware** (pedido do utilizador de
testar cada sensor/funcionalidade individualmente; ordem por confirmar com
ele antes de avançar, dado o risco/complexidade crescente de cada um):
LoRa (`test_lora_isolated`, nunca chegou a ler-se em série), gesto de
emergência de 3 cliques (`Emergency::triggerTestAlert()`/SOS físico,
corrigido no código mas nunca confirmado em hardware — dispara um alerta
real, avisar o utilizador antes), GPS (sem código ainda).

**Bug real corrigido — gráfico de FC com eixo Y fixo (50-105 bpm)
escondia os valores de HR com ruído** (`web/dashboard/index.html`,
`drawHrSeries()`): reportado pelo utilizador ("a área de visualização só
vai de 50 a 100, valores ficam de fora, não visíveis"). Com os 175-187
bpm confirmados no teste de HR acima, `yAt(187)` calculava uma coordenada
bem acima do topo do canvas — a linha do gráfico saía do desenho sem
qualquer indicação visual de que havia um valor cortado. Corrigido:
`min`/`max` do eixo passam a ser calculados a partir do `dataMin`/`dataMax`
reais da série (ao vivo ou de demonstração) com uma margem de 15%,
arredondados a múltiplos de 5, com um intervalo mínimo de 20 bpm (evita um
gráfico "achatado" quando a série é muito estável). Os 4 traços de grelha
horizontais passam a ser calculados proporcionalmente ao novo intervalo,
em vez dos valores fixos `[50,65,80,95]`.

**Bug real corrigido — countdown do "Medir agora" parecia não funcionar**
(reportado pelo utilizador logo depois de testar): a implementação do
countdown (ver "Dashboard: countdown do 'Medir agora' implementado" acima,
mesma sessão) parava assim que chegava o `command_result` do bridge — mas
o bridge envia esse `command_result` LOGO A SEGUIR à escrita GATT
(`handle_dashboard_command()` em `bridge/ble_bridge.py`), não depois da
janela real de 15s de medição no dispositivo. Na prática o countdown
desaparecia quase instantaneamente (~1s após o clique), dando a impressão
de nunca ter aparecido. Corrigido: o countdown agora corre sempre até ao
fim real de `FORCE_READING_SECONDS` (15s); `command_result` só o
interrompe cedo quando `ok=false` (falha confirmada, não há nada por onde
esperar) — em caso de sucesso, o botão só reativa quando o tempo real da
medição termina, com a mensagem "Leitura concluída — os valores devem
estar atualizados."

Verificação de ambas as correções: parser JS real (esprima) sobre o
`<script>` principal extraído — sem erros novos introduzidos (o único erro
reportado é a limitação já conhecida do esprima com `??`/`?.`, numa linha
pré-existente não relacionada); confirmado que não há definições
duplicadas das funções tocadas (`grep -c`, 1 ocorrência cada).

**Passos: também confirmados falsos positivos** (reportado pelo
utilizador com a placa no pulso). Lido `detectStep()` em `src/Imu/Imu.cpp`:
o detetor é um limiar simples sobre a magnitude da aceleração
("high-pass" contra uma média móvel lenta, `kStepRiseThreshold=0.20g`,
`kStepRefractoryMs=320ms`), sem validação cruzada com o giroscópio nem
plausibilidade de cadência — vulnerável a abanar o pulso, gestos com a
mão ou vibração, que produzem o mesmo pico de aceleração de um passo real
(fraqueza clássica de pedómetros só-de-acelerómetro). **Não corrigido
nesta sessão** — ver plano abaixo.

## Plano de testes de hardware pendentes (2026-07-07, a pedido do utilizador)

Este plano cobre os itens ainda por testar/corrigir com a placa real, por
ordem de prioridade e risco. Regra do utilizador (já registada em
"Próximas tarefas"): não começar pelo mais arriscado/complexo (LoRa).

### 1. HR — detetor a contar ruído como batimentos extra (prioridade alta, já em curso)
- **Estado**: `sampleAverage` corrigido (2026-07-04) resolveu o
  desfasamento de taxa; batimentos detetados pela primeira vez
  confirmados hoje, mas a ~175-187 bpm (implausível em repouso).
- **Hipótese**: `detectHeartbeat()` (`Ppg.cpp`) deteta cruzamento de zero
  da derivada sem exigir amplitude mínima de pico — pode estar a contar
  ruído/harmónicos como batimentos extra.
- **Próximo passo concreto**: capturar série ao vivo do sinal PPG em
  bruto (antes do filtro/derivada) durante um "Medir agora" real, para
  confirmar visualmente se há 1 pico por batimento ou vários. Se
  confirmado, adicionar um limiar de amplitude mínima ao
  `detectHeartbeat()` (não só o cruzamento de zero) e/ou alargar o
  refratário. **Não implementar a correção às cegas sem essa captura** —
  já houve uma hipótese anterior (`sampleAverage`) que só se confirmou
  com dados reais, não só leitura de código.

### 2. Passos — falsos positivos por movimento do pulso sem andar (prioridade alta, novo)
- **Estado**: confirmado hoje pelo utilizador ("contagem também está
  inconsistente, teve falsos positivos") com a placa no pulso.
- **Hipótese**: limiar simples de aceleração (`detectStep()`, `Imu.cpp`)
  sem cruzamento com o giroscópio nem verificação de cadência regular —
  qualquer gesto de pulso com magnitude de aceleração parecida a um passo
  conta como passo.
- **Próximo passo concreto**: repetir o teste com dois cenários
  controlados e comparados (contagem manual do utilizador vs. `steps`
  reportado): (a) andar um número conhecido de passos sem mexer muito o
  pulso; (b) ficar parado a abanar/gesticular com o pulso sem andar.
  Só depois de ver os números dos dois cenários decidir a correção
  (candidatos: exigir uma cadência mínima entre passos consecutivos
  dentro de um intervalo plausível, ou cruzar com o giroscópio para
  distinguir marcha de gesto).

### 3. SpO2 — dados insuficientes para validar (prioridade média)
- **Estado**: 1 leitura válida (100%) confirmada hoje, histórico com
  quase nenhum dado.
- **Próximo passo**: repetir "Medir agora" várias vezes com o sensor bem
  encostado à pele e comparar contra um oxímetro de referência, se
  disponível — sem isso não há como validar exatidão, só presença de
  leitura.

### 4. Emergência — gesto SOS de 3 cliques (prioridade média, ação sensível)
- **Estado**: corrigido no código (`delayPollingEmergency()`,
  `Emergency::triggerTestAlert()` via comando série `SOS`) mas **nunca
  testado em hardware real**.
- **Atenção**: disparar isto gera um alerta de emergência real no
  dashboard (`emergency_alert`) — **confirmar com o utilizador antes de
  correr**, para não ser uma surpresa se estiver a demonstrar o dashboard
  a alguém nesse momento.
- **Próximo passo**: com o bridge ligado e o dashboard aberto na vista de
  alertas, enviar `SOS` pela série (bypass, já que o botão físico está
  partido) e confirmar: (a) o alerta aparece no dashboard; (b) o fluxo de
  cancelamento por OTP funciona como esperado; (c) o firmware não
  bloqueia nada mais enquanto o alerta está pendente.

### 5. LoRa — nunca chegou a ler-se em série (prioridade baixa, mais arriscado)
- **Estado**: `test_lora_isolated` preparado desde 2026-07-03, nunca
  corrido/lido em série (sessão anterior ficou bloqueada antes de
  chegar aqui, ver "Próximas tarefas" acima).
- **Próximo passo**: só depois dos itens 1-4. Compilar e enviar o
  ambiente isolado (`pio run -e test_lora_isolated -t upload`), abrir
  série e capturar os 3 testes (TESTE 1/2/3) do ficheiro, um de cada vez,
  sem assumir que o pino NSS está certo — é precisamente isso que este
  teste deve confirmar/negar.

### 6. GPS — sem código ainda (fora do âmbito de "testar o que existe")
- Não há nada para testar; fica de fora deste plano até haver
  implementação (fora do âmbito desta rotina de testes de hardware).

## CI de testes para `ml/` (2026-07-07, rotina cloud)

Contexto: todos os itens de hardware pendentes acima (secção "Plano de
testes de hardware") exigem a placa física e captura de série ao vivo — a
própria secção do item 1 (HR) é explícita: "não implementar a correção às
cegas sem essa captura". Sem acesso a hardware nesta rotina cloud, e com as
Prioridades 0-2/6-8 bloqueadas (hardware/decisão do utilizador) e a
Prioridade 3 (app móvel) fora do âmbito de uma execução autónoma, revi o
estado da Prioridade 4 (BD SQL — já com ORM, Alembic, cifra real, API REST
e 46 testes com CI própria) e da Prioridade 5 (`ml/`, ver `ml/README.md`).
Encontrei aí um item concreto e já sinalizado como "ainda por fazer" na
secção que criou `bridge-tests.yml` mais cedo hoje: não existia nenhuma CI
para `ml/`, só para o firmware e para o bridge.

**Implementado**: `ml/tests/test_features.py` (7 testes) e
`ml/tests/test_duration_detector.py` (8 testes) — cobrem só a lógica pura e
determinística de `features.py` (`_zero_crossing_rate`, `extract_features`)
e `duration_detector.py` (`evaluate_block`, `evaluate_subject`), com
sinais/segmentos escritos à mão, **sem gerar dataset nem treinar nenhum
modelo** — deliberadamente scoped para não repetir o custo (TensorFlow,
minutos por execução) que já tinha adiado isto antes. `train_activity_classifier*.py`,
`train_lstm_autoencoder.py` e `measure_rf_footprint.py` continuam sem
cobertura de CI (registado como possível melhoria futura em `ml/README.md`
— um teste de fumo com dataset minúsculo, não uma validação de métricas).

Novo workflow `.github/workflows/ml-tests.yml` (mesmo padrão do
`bridge-tests.yml`): instala só `numpy`/`pandas`/`pytest` (não o
`ml/requirements.txt` completo, que traz TensorFlow/XGBoost/emlearn —
desnecessários para os módulos cobertos por esta suite) e corre
`pytest tests/ -v` a partir de `ml/`, em cada push/PR para `main`.

**Verificado de facto antes de commitar** (venv desta rotina cloud):
`cd ml && python -m pytest tests/ -v` → **15/15 testes passam**. YAML do
workflow validado com `yaml.safe_load`. Ver `ml/README.md`, secção "Testes
automáticos + CI", para o detalhe completo.

**Confirmado a passar em CI real (2026-07-07, mesmo push)**: verificado via
`actions_get`/`get_workflow_run` da API do GitHub — a primeira execução do
novo workflow "ML pipeline tests" (`run_id=28905082093`, commit `64b1606`)
terminou `completed`/`success`. Mesma prática já registada para
`bridge-tests.yml` — não é só um ficheiro YAML válido sem sinal de vida,
está mesmo a instalar `numpy`/`pandas`/`pytest` e a correr os 15 testes
reais em cada push a `main`.

**Ainda por fazer** (fora do âmbito desta correção pontual, já registado
acima em `ml/README.md`): cobertura de CI para `train_activity_classifier*.py`/
`train_lstm_autoencoder.py`/`measure_rf_footprint.py` (exigiria decidir um
teste de fumo leve, sem retreinar modelos de verdade — não implementado).

## Teste de fumo do treino do classificador (2026-07-08, rotina cloud)

Sessão sem novos commits de outras rotinas desde a última verificação
(`git fetch origin main` confirmou local e remoto já sincronizados em
`f05bf05`). Com as Prioridades 0-2/6-8 bloqueadas (hardware/decisão do
utilizador — sem placa nesta rotina cloud), a Prioridade 3 (app móvel) fora
do âmbito de uma execução autónoma, e a Prioridade 4 (BD SQL) já com um
protótipo funcional extenso (ORM, Alembic, cifra real dos campos sensíveis,
API REST, 47 testes com CI própria), revi a Prioridade 5 (`ml/`) e
encontrei o item explicitamente já sinalizado como "próximo passo possível"
pela secção anterior ("CI de testes para `ml/`", 2026-07-07): teste de fumo
dos scripts de treino, ainda sem cobertura nenhuma de CI.

**Implementado**: `ml/tests/test_train_smoke.py` (2 testes novos) — chama
diretamente as funções `train(df, feature_cols)` de
`train_activity_classifier.py` (XGBoost) e `train_activity_classifier_rf.py`
(Random Forest), já puras (só `main()` faz I/O de ficheiros, não tocado por
este teste), sobre um dataset minúsculo gerado em memória
(`generate_dataset(n_subjects=2, seed=7)`, **não** os 8 sujeitos/seed=42 de
produção — âmbito deliberadamente pequeno, confirma que o caminho de código
corre sem erro, não valida métricas de produção). Continua **sem cobrir**
`train_lstm_autoencoder.py`/`measure_rf_footprint.py` (TensorFlow/emlearn,
mesmo custo já documentado como motivo para os deixar de fora do CI leve).

`.github/workflows/ml-tests.yml` atualizado: `pip install` passou a incluir
`scikit-learn`/`xgboost` (instalação em segundos, não os minutos do
TensorFlow que continuam a justificar excluir o LSTM Autoencoder) — YAML
revalidado com `yaml.safe_load` depois da alteração.

**Verificado de facto antes de commitar** (venv nova desta rotina cloud,
`numpy`/`pandas`/`scikit-learn`/`xgboost`/`pytest`): `cd ml && python -m
pytest tests/ -v` → **17/17 testes passam** (15 já existentes + 2 novos),
~11s no total (dominado pela geração do dataset minúsculo, ~9s, medido
diretamente). `git status` confirmado limpo em `ml/models/`/`ml/reports/`
depois de correr a suite — as funções `train()` chamadas pelo teste não
escrevem nenhum ficheiro, nenhum artefacto já commitado foi tocado ou
substituído. Ver `ml/README.md`, secção "Teste de fumo do treino", para o
detalhe completo.

**Ainda por fazer** (âmbito explicitamente fora desta correção pontual):
teste de fumo equivalente para `train_lstm_autoencoder.py` (exigiria
TensorFlow em CI — decisão de custo, não implementada) e para
`measure_rf_footprint.py` (exige o toolchain ARM real, já usado localmente
por esta rotina só quando disponível — não está nesta sessão cloud).

**Confirmado a passar em CI real (2026-07-08, mesmo push)**: verificado via
`actions_get` da API do GitHub — `run_id=28912450462`, commit `8d71b8f`,
`completed`/`success`. Mesma prática já registada para `bridge-tests.yml`/
`ml-tests.yml` — o workflow instalou mesmo `scikit-learn`/`xgboost` e correu
os 17 testes reais (incluindo os 2 novos de `test_train_smoke.py`) neste
push a `main`, não só um YAML válido sem sinal de vida.

## Primeiro endpoint de escrita na API REST (`bridge/api.py`, 2026-07-08, rotina cloud)

`git fetch origin main` no início desta execução encontrou 2 commits novos
de uma rotina paralela horas antes (teste de fumo do treino, ver secção
imediatamente acima) — rebase limpo, sem conflitos. Com as Prioridades
0-2/6-8 bloqueadas (hardware/decisão do utilizador), a Prioridade 3 (app
móvel) fora do âmbito de uma execução autónoma, e a Prioridade 5 (`ml/`)
sem itens novos não bloqueados por hardware/decisão do utilizador (os
restantes "ainda por fazer" do roteiro dependem de dados reais, histórico
por pessoa via BD, ou TensorFlow/toolchain ARM indisponíveis nesta rotina),
revi a Prioridade 4 (BD SQL) e encontrei o item "endpoints de escrita",
explicitamente listado como "ainda por fazer" no cabeçalho de `bridge/api.py`
desde 2026-07-07 — a API REST tinha sido implementada só leitura (GET) de
propósito, com este item já registado como próximo passo natural.

**Implementado**: `POST /api/medications/{medication_id}/adherence`
(`bridge/api.py`) — regista ou atualiza se uma dose agendada de medicação
foi tomada, usando o modelo `MedicationAdherence` já existente em
`storage_advanced.py` (nenhum modelo/coluna novo necessário). Corpo JSON
`{scheduled_datetime, taken, method, notes}`, validado com um `BaseModel`
Pydantic (`method` restrito aos 3 valores já documentados no comentário do
modelo ORM — antes só uma convenção informal, agora aplicada). Escolhas de
desenho:

- **Idempotente por `(medication_id, scheduled_datetime)`**: um pedido
  repetido para a mesma dose atualiza o registo existente em vez de criar
  duplicados — mesmo comportamento que `markDoseTaken()` já tem no
  dashboard (clicar duas vezes não duplica a entrada em `localStorage`),
  aplicado aqui à versão persistida. Testado diretamente (`test_repeated_call_updates_instead_of_duplicating`).
- **`taken_at` derivado no servidor**, nunca aceite do cliente — evita que
  um pedido malformado grave uma hora de toma arbitrária; `taken=false`
  limpa `taken_at` (dose desmarcada), `taken=true` grava `datetime.utcnow()`
  no momento do pedido.
- **`AuditLog` em cada escrita** (ação `medication_adherence.write`,
  `resource_id`=medication_id, `details` com o corpo relevante,
  `ip_address` do pedido) — mesmo padrão de auditoria que o resto do schema
  já promete (`storage_advanced.py`, secção "Segurança/Compliance") mas que
  nenhum endpoint tinha ainda exercido de facto, porque a API era só
  leitura até agora.
- **Reutiliza a mesma autenticação `X-API-Key`/falha-fechada** já usada
  pelos endpoints GET — não foi criado um nível de autenticação separado
  para escrita nesta primeira versão (limitação já registada: chave
  estática única, sem por-utilizador nem rate-limiting).

**Ainda não integrado** (mesma limitação já documentada para toda a API
REST): nem `web/dashboard/index.html` (cujo botão "Marcar como tomado"
continua a escrever só em `localStorage`, `markDoseTaken()`) nem
`ble_bridge.py` chamam este endpoint — ligar qualquer um dos dois é uma
decisão de integração maior (qual serviço fica como fonte da verdade,
como lidar com o dashboard sem rede ao bridge local) fora do âmbito desta
alteração pontual, registada aqui como próximo passo concreto.

**Testado**: 6 testes novos em `bridge/tests/test_api.py`
(`TestRecordMedicationAdherence`) — chave de API em falta/errada (401),
medicamento inexistente (404), criação com `taken_at` preenchido, chamada
repetida não duplica linha nem cria mais de um registo de auditoria por
chamada nova, `taken=false` limpa `taken_at`, `method` fora do conjunto
permitido rejeitado pela validação Pydantic (422). **Suite completa do
bridge corrida localmente antes de commitar** (venv nova desta rotina
cloud): `cd bridge && python -m pytest tests/ -v` → **53/53 testes
passam** (47 já existentes + 6 novos), incluindo `test_storage_advanced.py`
e `test_crypto_utils.py` sem regressões. `python -m py_compile api.py` sem
erros; schema OpenAPI (`api.app.openapi()`) construído com sucesso e inclui
a nova rota — confirma que o endpoint está corretamente registado, não só
que o ficheiro tem sintaxe válida.

`bridge/README.md` atualizado (secção "API REST") com o novo endpoint e
exemplo de corpo JSON.

**Confirmado a passar em CI real (2026-07-08, mesmo push)**: verificado via
`actions_get` da API do GitHub — `run_id=28914716866`, commit `aef3e08`,
`completed`/`success` em `bridge-tests.yml`. Mesma prática já registada
para as outras suites deste projeto — os 53 testes reais correram no CI,
não só localmente.
