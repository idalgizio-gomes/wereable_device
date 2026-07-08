# CareWear — Estado de Segurança do Firmware

> Ficheiro da rotina de **Segurança** (mentalidade adversarial, assume-breach),
> distinta da rotina de **Desenvolvimento** (`PROJECT_STATUS.md`). Lê sempre
> os dois antes de agir — `PROJECT_STATUS.md` documenta decisões e riscos
> conhecidos que não devem ser reabertos sem justificação nova.
>
> Sem acesso a hardware físico nesta rotina cloud (mesma limitação já
> documentada em `PROJECT_STATUS.md`): nenhum achado aqui é um "exploit
> confirmado" — usa-se "hipótese"/"vetor" quando não há prova em hardware
> real. Toolchain ARM também bloqueada pelo proxy do ambiente nesta
> execução (`files.seeedstudio.com` → 403), mesma limitação já registada
> pela rotina de desenvolvimento — build validado por revisão manual +
> verificação de balanceamento de chavetas/parênteses/colchetes.

## Como ler este ficheiro

Cada risco tem um ID `FW-XXX` estável (não reutilizar números, mesmo que
um risco seja fechado). Estados possíveis: **ABERTO** (registado, sem
correção aplicada), **MITIGADO** (correção contida aplicada nesta
rotina, risco residual descrito), **FECHADO** (resolvido por completo,
raro em firmware sem hardware para confirmar). Nada é declarado
"resolvido" sem prova — ver critérios de aceitação no prompt da rotina.

## Rotação de eixos do checklist

Eixos (do prompt da rotina), auditados um por execução, com rotação:

1. Corrupção de memória
2. Concorrência (race conditions/deadlocks FreeRTOS + BLE/ISR)
3. **Validação de inputs externos (GATT writes, série, IMU/PPG)** ← auditado nesta execução (S01, 2026-07-08)
4. BLE security (parte de firmware: geração/guarda de chaves — permissões/pairing detalhados ficam para a rotina S04)
5. Criptografia (chave AES morta/qualidade de aleatoriedade)
6. Gestão de memória (heap dinâmico, fragmentação, use-after-free)
7. OTA (roadmap — ainda não existe)
8. Watchdog
9. Secure Boot
10. Debug interfaces (SWD/JTAG, APPROTECT)
11. Exposição de informação por Serial (flags de debug)

**Próxima execução (S01)**: sugerido eixo 2 (Concorrência) — já há uma
corrida conhecida e não resolvida em `QspiRingBuffer::format()` (ver
`PROJECT_STATUS.md`, secção "Reset de leituras") que merece uma análise
dedicada para avaliar se uma correção contida (mutex/secção crítica
dentro do próprio `QspiRingBuffer`) é viável sem tocar no protocolo BLE.
Se não, eixo 1 (Corrupção de memória) como alternativa.

## Registo por data — auditorias e achados

### 2026-07-08 (S01 firmware-security) — eixo: Validação de inputs externos

**Âmbito revisto por inteiro**: todas as characteristics GATT com
`setWriteCallback()` em `src/Ble/Ble.cpp` (`aesKeyChar`, `dumpCtrlChar`,
`currentTimeChar`/0x2A2B) + o parser de comandos série
(`pollSerialLine()`/`serialCommandReceived()` em `src/main.cpp`, usado
pelos comandos de debug `WAKE`/`SLEEP`/`SOS`) + o caminho que decodifica
dados vindos da flash externa para dentro dos pacotes BLE
(`QspiRingBuffer::decodeSlot()`, `Ble.cpp::mapRingRecordToFull()`), por
serem a fronteira onde bytes de origem externa (app/bridge via BLE, ou
dados persistidos que podem estar corrompidos) entram no firmware.

**Achados**:

- **Validação de comprimento/conteúdo em si — sem bugs de corrupção de
  memória encontrados.** `aesKeyCallback` restringe a exatamente 16/24/32
  bytes (bug de um comprimento "válido mas inútil" já corrigido em
  2026-07-07, ver `PROJECT_STATUS.md`); `dumpCtrlCallback` verifica
  `len < 1 || data == nullptr` antes de ler `data[0]` e `len >= 3` antes
  de ler `data[1..2]` (comando `kDumpCtrlForceHr`); `timestampCallback`
  exige `len == 10` e `ctsToEpochUtc()` valida ano/mês/dia/hora/min/seg
  campo a campo antes de aceitar. `QspiRingBuffer::decodeSlot()` rejeita
  `in.len > kPayloadSize` antes de copiar para `Record::payload` (buffer
  fixo de 44 bytes) — sem overflow possível mesmo com um slot de flash
  corrompido. Todas as characteristics têm `setMaxLen()`/`setFixedLen()`
  coerente com o struct associado. Não há aqui, portanto, um vetor de
  buffer overflow/OOB write como o checklist pede para procurar — eixo
  revisto por inteiro, sem achado corrigível nesta categoria específica.

- **FW-001 — Current Time (0x2A2B) reescrevível durante o streaming
  ativo, sem autenticação — CORRIGIDO nesta execução.**
  **Gravidade: média** (integridade dos dados/registo forense, não
  confidencialidade nem disponibilidade).
  **Vetor concreto**: `currentTimeChar` usa `SECMODE_OPEN` (ver
  `Ble.h`, nota de design already conhecida) e continua registada no
  servidor GATT mesmo depois de `startBroadcast()` deixar de a incluir
  no *advertising* — `startBroadcast()` só recria o pacote de
  advertising, não remove characteristics já criadas em `begin()`. Como
  `Bluefruit.begin(2, 0)` (`main.cpp`) permite até 2 centrais ligados em
  simultâneo, um segundo dispositivo BLE qualquer (não precisa de ser o
  bridge/telemóvel legítimo) pode ligar-se durante o streaming normal,
  fazer descoberta de serviços, encontrar o handle de 0x2A2B (UUID
  padrão do Bluetooth SIG, previsível) e escrever um payload de 10 bytes
  válido a qualquer momento — o antigo `timestampCallback` aceitava-o
  sempre, sem verificar se o dispositivo já estava operacional. Efeito:
  `Clock::setUtc()` é a única fonte de hora usada em todo o firmware
  (confirmado por grep — `Emergency.cpp` usa `Clock::nowUtc()` para o
  timestamp dos alertas de SOS/queda, `Ble.cpp` para o timestamp de cada
  registo de sensores), por isso um atacante ligado conseguia falsificar
  retroativamente/prospectivamente a hora de registos clínicos e de
  alertas de emergência gravados a partir desse momento — quebra da
  integridade forense/clínica dos dados de um wearable médico, sem
  precisar de quebrar a cifra AES-CTR nem de conhecer a chave.
  **Correção aplicada** (`src/Ble/Ble.cpp`, `timestampCallback`):
  rejeita qualquer escrita em 0x2A2B assim que `s_dataModeEnabled` for
  `true` (mesmo padrão de "só aceita uma vez"/"só numa fase" já usado em
  `aesKeyCallback`). `ensureTimeSync()` corre sempre **antes** de
  `startBroadcast()` (confirmado em `main.cpp`, ordem
  `Ble::begin()` → `ensureAesKey()` → `ensureTimeSync()` →
  `startBroadcast()`), por isso o fluxo normal de provisioning fica
  intacto — só fecha a janela depois disso.
  **Verificação de que não quebra o bridge legítimo**: `ble_bridge.py`
  (`_maybe_send_time()`) já trata a falha desta escrita como não-fatal
  ("normal se já sincronizada", `try/except` a toda a volta) e é chamada
  em **todo** reconnect, incluindo reconexões durante o modo de dados —
  com a correção, essas tentativas passam a falhar (esperado, já
  tratado) em vez de silenciosamente reescrever o relógio; não foi
  necessário alterar `bridge/` no mesmo commit porque o comportamento
  já era tolerante a esta falha.
  **Verificação de build**: sem toolchain ARM disponível nesta rotina
  cloud (proxy bloqueia `files.seeedstudio.com`, 403 — mesma limitação
  documentada em `PROJECT_STATUS.md`). Revisão manual do diff (sintaxe
  C++ simples, `if` + `return` antes da lógica já existente) + script
  Python a confirmar que o ficheiro inteiro ganhou exatamente o mesmo
  número de chavetas/parênteses/colchetes a abrir e a fechar (128/127,
  928/935, 118/118 — o desequilíbrio 128/127 já existia antes desta
  edição, herdado de comentários com parênteses aninhados, documentado
  por outra rotina em 2026-07-07; a minha edição não alterou essa
  diferença, só acrescentou pares equilibrados). **Não testado em
  hardware real.**
  **Risco residual**: a janela de escrita **antes** do primeiro sync
  (fase de provisioning) continua sem qualquer autenticação — ver
  FW-002 abaixo. Um atacante que se ligasse durante essa janela inicial
  ainda poderia interferir com o primeiro sync de hora (ou hijack da
  chave AES). Isso é uma limitação de desenho mais ampla (falta de
  pairing/bonding em toda a fase de provisioning), não algo que esta
  correção pontual resolva.

- **FW-002 — Nenhuma characteristic GATT exige pairing/bonding
  (`SECMODE_OPEN` em todas) — RISCO REGISTADO, sem correção nesta
  execução.**
  **Gravidade: alta** (integridade + disponibilidade dos dados de um
  wearable médico de pessoas vulneráveis).
  **Vetor concreto** (dois sub-casos, ambos já visíveis só de ler
  `Ble.cpp::begin()`):
  1. **Hijack da chave AES no provisioning**: `aesKeyChar` só recusa
     escritas *depois* de já existir uma chave em flash
     (`Storage::hasAesKey()`). Num dispositivo ainda não provisionado
     (equipamento novo, ou depois de um `clearAll()`), **qualquer**
     central BLE que ganhe a corrida para se ligar e escrever primeiro
     define a chave AES real usada para cifrar o streaming — não há
     forma de a app/cuidador legítimo confirmar que foi mesmo ele a
     definir a chave.
  2. **Comandos destrutivos/de controlo sem autenticação em modo de
     dados**: `dumpCtrlChar` aceita de qualquer central ligado (até 2
     simultâneos, `Bluefruit.begin(2, 0)`) os comandos
     `kDumpCtrlResetReadings` (0x04 — apaga **todo** o histórico de
     leituras do ring buffer, destrutivo e irreversível, já assinalado
     como tal no próprio código) e `kDumpCtrlForceHr`/`kDumpCtrlStop`
     (podem interferir com medições em curso ou parar o streaming para
     o cuidador legítimo). O advertising ("Wearable", serviço custom
     bem conhecido) é público e conectável por desenho (é assim que a
     app encontra o dispositivo) — não há passo de autorização entre
     "descobrir o dispositivo" e "poder emitir estes comandos".
  **Porque não foi corrigido nesta execução**: uma correção real (exigir
  `SECMODE_ENC_NO_MITM`/bonding, ou um segredo de sessão trocado durante
  o provisioning) é uma mudança de protocolo BLE — exige atualizar
  `bridge/ble_bridge.py` (fluxo de pairing/bonding com `bleak`) no mesmo
  commit, o que as regras desta rotina proíbem sem justificação e
  coordenação mais ampla. É também o âmbito explícito da rotina **S04**
  (BLE security — Security Mode/Level, pairing, bonding, permissões das
  characteristics). Registado aqui como o achado mais grave desta
  auditoria para dar contexto de firmware a essa rotina: os dados
  ficam cifrados em trânsito (AES-CTR, ver histórico em
  `PROJECT_STATUS.md`), mas **quem pode ligar-se e emitir comandos**
  continua completamente aberto.
  **Recomendação**: quando a rotina S04 avançar, considerar como
  mínimo: (a) migrar `dumpCtrlChar`/`aesKeyChar` para
  `SECMODE_ENC_NO_MITM` (encriptação de link sem MITM — não exige UI de
  confirmação, compatível com bonding "Just Works", suficiente para
  parar um atacante passivo/oportunista, embora não pare um MITM ativo
  dedicado); (b) só depois, se necessário, subir para
  `SECMODE_ENC_WITH_MITM` com um mecanismo de confirmação (ex.: código
  no ecrã OLED do próprio dispositivo, que já existe fisicamente).
  Qualquer destas opções exige que `bleak` (bridge) e uma futura app
  móvel suportem o fluxo de bonding do SO — validar viabilidade antes
  de mudar o firmware.

- **Comandos série de debug (`WAKE`/`SLEEP`/`SOS`, `pollSerialLine()`)
  — sem achado novo.** Buffer de 16 bytes com bound check
  (`len < sizeof(buf) - 1`) antes de cada escrita — sem overflow.
  Requerem acesso físico por USB (não é um vetor remoto); já
  documentados como flags de debug ativas por decisão do utilizador
  (botão físico avariado) — **não desativados aqui**, por instrução
  explícita da rotina (decisão do utilizador, fora do âmbito desta
  auditoria).

**CVEs/avisos verificados (com fonte, sem inventar)**: pesquisa aplicada
nesta execução não encontrou nenhum CVE novo (2025-2026) publicado
especificamente contra o SoftDevice S140 ou o core Arduino
Adafruit-nRF52/Bluefruit usados por este projeto (`platformio.ini`:
`framework = arduino`, board Seeed XIAO nRF52840 Sense Plus — **não**
Zephyr/nRF Connect SDK). A vulnerabilidade histórica mais relevante
documentada pela Nordic para a família nRF5 SDK/SoftDevice é a falta de
validação da chave pública remota no LESC (Low Energy Secure
Connections) durante o pairing, corrigida desde a SDK 15.0.0 — **não
aplicável ao estado atual deste firmware**, que não usa pairing/LESC de
todo (ver FW-002, `SECMODE_OPEN` em tudo) — ironicamente, a ausência de
pairing significa que esta classe de CVE não pode ocorrer aqui, mas às
custas do problema maior descrito em FW-002. Vulnerabilidades do
subsistema Bluetooth do Zephyr (buffer overflows com asserts
desativados, integer underflow em `gatt_find_info_rsp`, OOB write em
HCI-over-SPI) foram encontradas na pesquisa mas dizem respeito à stack
BLE do **Zephyr/nRF Connect SDK**, não à stack Adafruit Arduino/Bluefruit
usada aqui — relevantes apenas para uma eventual migração futura do
projeto para Zephyr (mencionada como possibilidade no roadmap), não para
o firmware atual. Fontes: [Vulnerabilities in nRF5 SDK versions —
Nordic](https://docs.nordicsemi.com/bundle/nwp_031/page/WP/nwp_031/vulnerabilities.html),
[Zephyr Project — Vulnerabilities](https://docs.zephyrproject.org/latest/security/vulnerabilities.html).

**Ficheiros alterados nesta execução**: `src/Ble/Ble.cpp` (bloqueio em
`timestampCallback`, ver FW-001). Nenhuma alteração a `bridge/`, `web/`
nem `ml/`.

## Riscos herdados de `PROJECT_STATUS.md` (referência, não re-auditados nesta execução)

Listados aqui só para visibilidade cruzada — a análise detalhada destes
fica para o eixo do checklist correspondente numa execução futura, não
duplicada agora:

- Corrida conhecida em `QspiRingBuffer::format()` chamado a partir do
  contexto BLE (`kDumpCtrlResetReadings`) enquanto `storageTask`
  escreve e `gattDumpTask` lê o mesmo ring buffer — mitigado
  parcialmente (para o streaming + espera de 100ms), não eliminado. Ver
  `PROJECT_STATUS.md`, secção "Reset de leituras". Candidato ao próximo
  eixo "Concorrência".
- AES-CTR sem autenticação (sem MAC/tag de integridade) — decisão já
  documentada e justificada em `PROJECT_STATUS.md` (limitação honesta,
  trade-off de tamanho de pacote). Sem achado novo nesta execução.
- Sem Secure Boot, interface SWD/JTAG presumivelmente aberta
  (`APPROTECT` por omissão do core Adafruit normalmente **não** está
  ativo) — ainda por confirmar/endurecer, candidato aos eixos 9/10.
  Não verificado nesta execução (fora do eixo escolhido).
- Sem watchdog confirmado no código revisto até agora — candidato ao
  eixo 8. Não verificado nesta execução.
