# CareWear — Estado de Segurança BLE (S04)

> Ficheiro da rotina de **Segurança BLE** (S04, mentalidade adversarial,
> assume-breach). Cobre pairing/bonding, proteção MITM, encriptação do
> link, permissões GATT, replay, privacy/MAC e whitelist do canal BLE
> wearable↔bridge. Ler `PROJECT_STATUS.md` (secções "Ble", "Cifra
> AES-CTR do modo de dados", "Bridge BLE ↔ WebSocket") e as secções
> irmãs abaixo (NFC, Privacidade/GDPR) antes de agir — não duplicar
> achados já registados lá. Ver também `seguranca/firmware-security`
> (PR #3, ainda não integrado em `main`), que já registou **FW-001**
> (corrigido) e **FW-002** (aberto) diretamente relacionados com esta
> auditoria — cross-referenciados abaixo em vez de repetidos.
>
> **Sem hardware físico nesta rotina cloud**: não é possível fazer
> sniffing/MITM real. Todos os achados abaixo vêm de leitura direta do
> código (`src/Ble/Ble.cpp`, `include/Ble/Ble.h`, `src/main.cpp`) e de
> pesquisa aplicada sobre vulnerabilidades BLE conhecidas — marcados
> como "hipótese"/"vetor" onde não há prova em hardware real.
>
> **Correção a um facto assumido no início desta rotina**: o prompt que
> lança esta rotina S04 descreve o streaming de sensores como indo "em
> texto simples (`FullPlain`)". Isso **já não é verdade** — resolvido em
> 2026-07-07 (ver `PROJECT_STATUS.md`, "Cifra AES-CTR do 'modo de
> dados'"): o payload de cada registo (39 bytes) vai cifrado com
> AES-CTR desde essa data. O que **continua** em claro é o resto do
> tráfego GATT — comandos, estado e, sobretudo, o alerta de emergência
> (ver BLE-002 abaixo) — que é precisamente o achado mais grave desta
> auditoria.

## Como ler este ficheiro

IDs `BLE-XXX` estáveis (não reutilizar números). Estados: **ABERTO**
(registado, sem correção), **MITIGADO** (correção contida aplicada,
risco residual descrito), **FECHADO**. Nenhuma correção estrutural
(pairing/bonding real) foi aplicada nesta execução — ver "Decisão desta
execução" no fim.

## Mapa de superfície GATT — tabela de characteristics

Convenção de permissões: `OPEN` = Security Mode 1 Level 1, sem
encriptação/autenticação nenhuma (Bluefruit `SECMODE_OPEN`);
`NO_ACCESS` = operação não permitida a nenhum central. **Nenhuma
characteristic deste dispositivo usa `SECMODE_ENC_NO_MITM` nem
`SECMODE_ENC_WITH_MITM`** — confirmado por leitura direta de
`Ble.cpp::begin()`, linha a linha.

| Characteristic | UUID | Properties (GATT) | Permissão leitura/notify | Permissão escrita | Exige encriptação/pairing? | Dados sensíveis expostos |
|---|---|---|---|---|---|---|
| `aesKeyChar` | `abcd1234-...-abcdef123456` | WRITE | — (sem READ) | `OPEN` | **Não** | A própria chave AES do streaming — mitigado só pela lógica applicacional (só aceita a 1ª escrita, `Storage::hasAesKey()`), não pelo GATT |
| `dumpCtrlChar` | `abcd1234-...-abcdef200001` | WRITE, WRITE_WO_RESP | — (sem READ) | `OPEN` | **Não** | Comandos de controlo, incl. `kDumpCtrlResetReadings` (0x04, destrutivo/irreversível) e `kDumpCtrlForceHr`/START/STOP |
| `dumpDataChar` | `abcd1234-...-abcdef200002` | NOTIFY, INDICATE | `OPEN` | `NO_ACCESS` | **Não** (link não cifrado) | Payload cifrado AES-CTR desde 2026-07-07 (ver nota acima) — mas cabeçalho `rec_seq`/`nonce`/`frag_idx` vai sempre em claro (metadados de ritmo/volume, não o conteúdo clínico em si) |
| `dumpStatusChar` | `abcd1234-...-abcdef200003` | NOTIFY, READ | `OPEN` | `NO_ACCESS` | **Não** | Estado do streaming + contagens (`sent_records`/`acked_records`) em claro — confirma que o dispositivo está a ser usado agora (metadado de presença/atividade) |
| `emergencyAlertChar` | `abcd1234-...-abcdef200004` | NOTIFY, READ | `OPEN` | `NO_ACCESS` | **Não** | **Tipo de alerta (SOS/queda) + timestamp UTC em claro**, legível por `READ` direto a qualquer momento por qualquer central, sem sequer precisar de esperar por uma notificação — ver BLE-002 |
| `currentTimeChar` (0x2A2B, padrão BT SIG) | `0x2A2B` | WRITE | — (sem READ) | `OPEN` | **Não** | Hora do sistema — mitigado parcialmente por **FW-001** (bloqueia escrita depois de `s_dataModeEnabled`), risco residual na janela de provisioning inicial |

## Riscos (BLE-XXX)

### BLE-001 — Nenhuma characteristic exige pairing/bonding/encriptação de link (`SECMODE_OPEN` em tudo) — ABERTO, mesmo risco de FW-002

**Gravidade: alta.** Confirma e detalha, do ponto de vista de "Security
Mode/Level" (âmbito específico desta rotina S04), o que
`seguranca/firmware-security` já tinha registado como **FW-002**: não
existe LE Secure Connections nem Legacy Pairing configurado — o link
BLE nunca é cifrado ao nível do GATT/link layer, independentemente de
haver ou não uma app legítima do outro lado.

**Correção de enquadramento importante**: a hipótese inicial desta
rotina ("a ligação atual parece Just Works") **não é precisa**. "Just
Works" é um *método de pairing* (Security Mode 1 Level 2,
`SECMODE_ENC_NO_MITM`) — ainda envolve troca de chaves e cifra do link,
só sem proteção MITM. Este dispositivo está um nível abaixo disso:
`SECMODE_OPEN` = Security Mode 1 **Level 1**, sem pairing nenhum. Na
prática isto significa que uma app/bridge legítima nem sequer recebe um
pedido de emparelhamento do SO (Android/iOS/`bleak`) ao ligar-se — a
ligação e todas as leituras/escritas GATT acontecem livremente, sem
qualquer diálogo de segurança.

**Vetor concreto**: qualquer dispositivo com rádio BLE dentro de
alcance (incl. um telemóvel comum com uma app genérica tipo nRF
Connect) consegue descobrir o serviço `wearableService`
(advertising público, UUID custom mas bem conhecido pelo próprio
firmware/bridge open-source) e ligar-se sem qualquer confirmação no
wearable. A partir daí tem acesso total às operações listadas na
tabela acima — ler o último alerta de emergência, escrever comandos de
controlo, e (se o dispositivo ainda não tiver sido provisionado)
definir a chave AES.

**Porque não é corrigido nesta execução**: subir para
`SECMODE_ENC_NO_MITM` (Just Works) ou `SECMODE_ENC_WITH_MITM`
(Numeric Comparison/Passkey, ver BLE-002) é uma mudança de protocolo —
o SoftDevice passa a exigir que o central inicie pairing antes de
aceder a essas characteristics, o que `bridge/ble_bridge.py` (via
`bleak`) hoje **não** faz (nenhum código de pairing/bonding em
`ble_bridge.py` — confirmado por leitura). Aplicar isto sem atualizar o
bridge no mesmo commit quebraria a ligação bridge↔wearable por
completo — proibido pelas regras desta rotina ("nunca alterar
protocolo/estruturas de pacote sem atualizar o bridge no mesmo
commit", e pairing/bonding é uma mudança de protocolo tão significativa
como uma mudança de layout de pacote). É também explicitamente o
âmbito que `seguranca/firmware-security` (FW-002) já delegou a esta
rotina S04 — delegar de volta sem desenho aceite seria dar voltas.

**Recomendação** (não decidida aqui, precisa de desenho + coordenação
com o bridge, ver BLE-002 para a escolha do método):
1. Mínimo viável: `SECMODE_ENC_NO_MITM` em `dumpCtrlChar` (impede
   comandos, incl. o reset destrutivo, de qualquer central que não
   tenha completado sequer um Just Works) e em `aesKeyChar` durante o
   primeiro provisioning.
2. Reforço (dado que o wearable tem ecrã OLED, ver BLE-002): Numeric
   Comparison para `SECMODE_ENC_WITH_MITM`, cobrindo também
   `emergencyAlertChar`/`dumpStatusChar` do lado da leitura.
3. Exige, em paralelo: `bleak` a chamar `pair()`/gerir bonding no
   `ble_bridge.py`, e testar o fluxo completo em hardware real (não
   disponível nesta rotina cloud).

---

### BLE-002 — Alerta de emergência (SOS/queda) transmitido e legível em claro, sem qualquer autenticação — ABERTO, achado novo desta execução

**Gravidade: crítica.** Este é o achado mais grave desta auditoria,
distinto de FW-002 (que é sobre comandos/hijack, não sobre este canal
específico) — não estava registado em nenhum `SECURITY_STATUS.md`
existente.

**Vetor concreto**: `emergencyAlertChar` (ver tabela acima) tem
`READ` com permissão `OPEN` — qualquer central consegue **ler o valor
diretamente**, a qualquer momento, mesmo sem estar ligado no momento
exato do alerta (o valor fica persistido na characteristic via
`.write()` local em `notifyEmergencyAlert()`, `Ble.cpp`), sem precisar
de sniffing passivo nem de esperar por uma notificação. Basta ligar-se
com qualquer app BLE genérica e ler o handle. O conteúdo
(`EmergencyAlertPacket`: `type` — SOS manual vs. queda+inatividade —
`seq`, `timestamp_utc`) vai **inteiramente em claro** — ao contrário do
streaming de sensores (`FullPlain`, cifrado AES-CTR desde 2026-07-07),
este canal nunca recebeu a mesma proteção.

**Porque isto é particularmente grave neste produto**: CareWear é um
wearable para pessoas com demência (`PROJECT_STATUS.md`, "Visão
geral"). Um observador com um leitor BLE barato, dentro de alcance
(casa, transporte público, espaço público), consegue confirmar **que
esta pessoa específica acabou de ter uma queda ou premiu o SOS**, sem
qualquer autenticação — informação de saúde crítica e sensível (Art. 9
RGPD, categoria especial de dados) exposta como se fosse pública. Isto
é mais grave do que expor sinais vitais em claro (já resolvido) porque
sinaliza diretamente um momento de vulnerabilidade extrema (queda,
possível incapacidade de pedir ajuda) a um estranho não autorizado, com
timestamp exato — útil tanto para um atacante oportunista (sabe que a
pessoa está momentaneamente incapacitada e sozinha) como para
vigilância indevida por terceiros.

**Não corrigido nesta execução** — a correção estrutural correta é a
mesma do BLE-001 (exigir encriptação/autenticação nesta
characteristic), não uma correção pontual isolada: cifrar só o payload
deste pacote (como se fez ao `FullPlain`) resolveria a confidencialidade
do *conteúdo*, mas não o facto de que **qualquer um pode ler ou ser
notificado sem autorização nenhuma** — a correção certa é ao nível do
GATT (BLE-001), não uma cifra aplicacional ad-hoc só para este pacote
(que duplicaria a lógica de chave/nonce já existente sem resolver o
problema de fundo). Registado aqui separadamente de BLE-001 por
gravidade própria — deve ser o **primeiro** caso a proteger quando a
rotina de pairing/bonding avançar, antes até de `dumpCtrlChar`.

**Recomendação imediata, sem mudança de protocolo** (avaliar antes da
próxima execução): considerar se `emergencyAlertChar` deveria sequer
ter a propriedade `READ` — hoje existe para a app poder "reconectar e
ver o último alerta perdido" (comentário em `Ble.h`), mas isso também é
o que permite a leitura anónima descrita acima. Não alterado nesta
execução por ser uma mudança de comportamento funcional (a app deixaria
de conseguir recuperar um alerta perdido ao reconectar) sem
coordenação com quem consome este canal (bridge/dashboard) — proposta,
não decisão.

---

### BLE-003 — Proteção MITM: nenhuma configurada; hardware permite Numeric Comparison/Passkey (ecrã OLED) — ABERTO, requisito de desenho

**Gravidade: média-alta**, condicional ao desenho de BLE-001 avançar.

Como não há pairing nenhum (BLE-001), a pergunta "Just Works vs.
Passkey Entry vs. Numeric Comparison" ainda não se aplica tecnicamente
— mas é a decisão certa a registar agora para quando `SECMODE_ENC_*`
for implementado. O hardware **tem** ecrã OLED SSD1351 já em uso
(`PROJECT_STATUS.md`, "Hardware atual") — o mesmo já usado para outros
fluxos de confirmação (ex.: mensagens durante `ensureAesKey()`/
`ensureTimeSync()`). Isto torna **Numeric Comparison** (LE Secure
Connections, `SECMODE_ENC_WITH_MITM`) tecnicamente viável sem hardware
novo — só Just Works (sem ecrã) seria justificável num dispositivo sem
display.

**Nota sobre o botão físico partido** (mesmo aviso já registado em
`SECURITY_STATUS.md`/NFC-002): Numeric Comparison tipicamente exige
confirmar "sim/não" no dispositivo — com o botão partido, o mesmo
mecanismo alternativo já sugerido para NFC-002 (bypass série
`WAKE`/`SLEEP`, ou gesto IMU) teria de servir também para esta
confirmação, até o botão ser substituído.

**Recomendação**: quando o desenho de pairing avançar (ver BLE-001),
preferir LE Secure Connections (ECDH P-256) com Numeric Comparison
sobre Legacy Pairing — LE Secure Connections evita a fraqueza histórica
de troca de chave TK por OOB/Legacy (16 bytes de entropia máxima e
vulnerável a brute-force offline do STK em Legacy Pairing) e está
disponível no SoftDevice S140 (suporta Bluetooth 5.x/LESC). Confirmar
compatibilidade exata da versão do SoftDevice em uso antes de
implementar (não confirmada nesta execução — sem hardware para
consultar `sd_ble_gap_sec_params_reply`/versão exata do SoftDevice
linkado pelo `platform-seeedboards`).

---

### BLE-004 — Replay de comandos: sem proteção específica, mas redundante com BLE-001 (acesso já é livre) — ABERTO, nota para desenho futuro

**Gravidade: média**, risco composto (não autónomo — depende de BLE-001
ser corrigido primeiro para passar a ser relevante).

**Situação atual**: `dumpCtrlChar` não tem nenhum nonce/contador de
frescura — um comando capturado (ex.: `kDumpCtrlResetReadings`, 0x04)
podia em teoria ser regravado mais tarde. **Mas hoje isto não
acrescenta risco real**: como o GATT é `SECMODE_OPEN` (BLE-001),
qualquer atacante já pode emitir o comando diretamente, a qualquer
momento, sem precisar de capturar e reproduzir nada — replay é uma
categoria de ataque que só faz sentido depois de existir autenticação a
contornar. `EmergencyAlertPacket.seq`/`DumpDataPacket.rec_seq`
incrementam e permitem à app **detetar** duplicados/perdas (não
confundir com anti-replay criptográfico), mas isso é deduplicação do
lado do bridge (`ble_bridge.py`), não uma proteção que impeça um
atacante de retransmitir um pacote pelo ar — confirma o que o prompt
desta rotina já assumia corretamente.

**Recomendação para quando BLE-001/BLE-003 avançarem**: não basta
cifrar/autenticar o link — `dumpCtrlChar`, sobretudo o comando
destrutivo 0x04, devia também levar um contador monotónico ou
challenge-response mínimo ao nível aplicacional (mesmo padrão já usado
para o nonce AES-CTR do streaming, `allocateNonce()`), para que nem um
central já emparelhado/bonded mas comprometido (ex.: telemóvel roubado
com bonding válido) consiga simplesmente regravar um comando antigo
capturado antes de ser revogado. Não implementado nesta execução — é
parte do mesmo desenho estrutural de BLE-001, não uma correção
isolada.

---

### BLE-005 — Privacy: endereço BLE provavelmente estático (não RPA) + nome de advertising identifica o dispositivo como "wearable" — ABERTO, a confirmar em hardware

**Gravidade: média**, relevante em particular por este ser um
dispositivo para pessoas com demência (risco de "wandering" —
`PROJECT_STATUS.md`).

**Situação encontrada**: `git grep` a `src/` e `include/` por
`setAddrType`/`BLEAddr`/qualquer chamada de API de privacidade
(`sd_ble_gap_privacy_set` ou equivalente Bluefruit) **não encontrou
nenhuma chamada** — nem em `Ble.cpp` nem em `main.cpp`
(`Bluefruit.begin(2, 0)` + `Bluefruit.setName("Wearable")` é toda a
configuração de identidade GAP existente). Pesquisa aplicada ao código
público da Adafruit_nRF52_Arduino (biblioteca subjacente ao Bluefruit
usado aqui) não confirmou de forma conclusiva, sem o binário/hardware
real para consultar `sd_ble_gap_addr_get()`, qual o tipo de endereço
exato usado por omissão — **registado como "a confirmar", não como
facto provado**. O que É seguro afirmar: sem qualquer chamada de API de
privacidade no código deste projeto, o endereço **não está a rodar
como RPA (Resolvable Private Address)** — API de privacidade do
SoftDevice (IRK, `ble_gap_privacy_params_t`) nunca é invocada. Na
melhor hipótese é um endereço aleatório estático (fixo, gerado uma vez,
nunca rodado) — funcionalmente equivalente a um MAC público fixo para
efeitos de rastreabilidade (nunca muda), mesmo que tecnicamente não
seja "público" no sentido do GAP.

**Vetor concreto**: um MAC/endereço que nunca roda permite a qualquer
scanner BLE passivo nas imediações (loja, transporte, casa de terceiro)
construir um histórico de presença desta pessoa específica ao longo do
tempo, cruzando avistamentos do mesmo endereço em locais/horas
diferentes — risco de localização/rastreamento relevante em contexto
de demência (a mesma preocupação já registada em `NFC-003` para o caso
do handover NFC, aqui aplicada ao advertising BLE em si, que já existe
e está ativo hoje, ao contrário do NFC que ainda é inerte).
Adicionalmente, o nome de advertising "Wearable" (visível no scan
response, `Bluefruit.ScanResponse.addName()`) não identifica a pessoa
mas confirma a **categoria do dispositivo** (wearable médico) a
qualquer scanner — combinado com um endereço fixo, facilita
correlacionar "este endereço = uma pessoa a usar um wearable médico",
uma informação em si sensível (Art. 9 RGPD, indício de condição de
saúde) mesmo sem saber o nome da pessoa.

**Não corrigido nesta execução**: ativar RPA (Resolvable Private
Address) exige gerar/guardar um IRK (Identity Resolving Key) e invocar
a API de privacidade do SoftDevice — tal como o pairing (BLE-001), isto
tipicamente acopla-se ao processo de bonding (o IRK é normalmente
trocado durante o pairing para o central conseguir resolver o endereço
rotativo do periférico), pelo que faz mais sentido desenhar em conjunto
com BLE-001 do que como correção isolada — e não há hardware nesta
rotina para validar que o SoftDevice S140/framework Arduino usado aqui
suporta a API de privacidade sem regressão (mesma cautela já registada
em NFC-003 para esta mesma pergunta). Renomear o advertising para algo
menos identificável foi considerado e **descartado** nesta execução:
o UUID custom do serviço (`12345678-...`) já é um identificador
distintivo por si só, tornando o ganho de privacidade de só mudar o
nome marginal, enquanto o risco de quebrar a deteção automática do
bridge (`ble_bridge.py` procura o dispositivo pelo nome "Wearable") é
real e exigiria coordenação/teste em hardware indisponível aqui.

**Recomendação**: avaliar RPA como parte do mesmo desenho de
pairing/bonding de BLE-001, não isoladamente.

---

### BLE-006 — Whitelist/filtro de ligação: aceita ligação de qualquer central — ABERTO, inerente ao desenho atual (sem bonding)

**Gravidade: baixa como item isolado** (é a mesma raiz de BLE-001, não
um risco independente).

Não existe filtro de aceitação de ligação (`Bluefruit.Advertising` não
configura nenhuma whitelist/bonded-only) — qualquer central pode
completar a ligação GAP. Isto é **estruturalmente necessário** hoje: o
dispositivo tem de aceitar ligações de centrais desconhecidos para o
provisioning inicial (a app legítima também começa como "desconhecida"
da perspetiva do wearable). Uma whitelist real só faz sentido **depois**
de existir bonding (aceitar só centrais já emparelhados, exceto durante
uma janela de provisioning explícita) — outra peça do mesmo desenho de
BLE-001, não uma correção isolada possível hoje.

---

### BLE-007 — Sniffing: com a cifra AES-CTR do streaming (resolvida 2026-07-07), o que resta exposto em claro é comandos + estado + emergência

**Gravidade: informativa** — resume o estado atual para orientar
prioridades, não é um risco novo.

Assumindo (correto, dado BLE-001) que tudo o que é transmitido é
legível por um sniffer BLE dentro de alcance:
- **Já protegido**: conteúdo dos registos de sensores (`FullPlain`,
  payload de 39 bytes) — AES-CTR desde 2026-07-07, sem MAC/integridade
  (limitação já documentada em `PROJECT_STATUS.md`).
- **Ainda em claro**: comandos (`dumpCtrlChar`), estado/contagens
  (`dumpStatusChar`), **alerta de emergência completo** (BLE-002,
  o mais grave), hora do sistema (`currentTimeChar`, mitigado
  parcialmente por FW-001), e os cabeçalhos de fragmento
  (`rec_seq`/`nonce`/`frag_idx`/`frag_total` de `DumpDataPacket`).
- Nenhum destes pacotes contém PII direta (nome, NIF) — mas
  `emergencyAlertChar` contém o equivalente a um evento clínico crítico
  com timestamp, o que já é dado de saúde sensível por si só (ver
  BLE-002).

---

## Pesquisa aplicada — vulnerabilidades Bluetooth conhecidas (verificadas 2026-07-10)

Pesquisa dirigida a esta execução, para além da já feita por
`seguranca/firmware-security` (2026-07-08, sem CVE específico ao
SoftDevice S140/Adafruit Bluefruit Arduino encontrado nessa altura —
não repetida aqui, ver esse ficheiro).

- **BLERP (BLE Re-Pairing Attacks and Defenses)** — Sacchetti &
  Antonioli (EURECOM), NDSS Symposium 2026. Identifica falhas de
  desenho no **re-pairing** BLE (re-emparelhamento de um dispositivo já
  bonded) — re-pairing não autenticado e downgrade do nível de
  segurança, permitindo impersonation/MITM com 0-1 clique, testado
  contra stacks Apple/Android/NimBLE. **Aplicabilidade a este
  projeto**: **não aplicável hoje** (não há pairing/bonding nenhum
  implementado — BLE-001 — logo não há re-pairing a atacar). **Relevante
  para o desenho futuro**: quando BLE-001 for implementado, o fluxo de
  bonding/re-pairing tem de seguir as mitigações do paper (não aceitar
  pedidos de re-pairing não autenticados nem permitir downgrade de
  security level num dispositivo já bonded) — registar como requisito
  de desenho, não retroativo a código que ainda não existe. Fonte:
  [NDSS Symposium — BLERP](https://www.ndss-symposium.org/ndss-paper/blerp-ble-re-pairing-attacks-and-defenses/).
- **BLUFFS** (Eurecom, 2023, continua ativamente citado em 2025-2026)
  — explora a derivação da chave de sessão (SKD) em Bluetooth
  Classic/BR-EDR (Secure Simple Pairing) para forçar uma chave fraca e
  permitir impersonation/MITM. **Aplicabilidade**: dirigido
  principalmente a Bluetooth Classic, não ao BLE puro usado por este
  wearable (SoftDevice S140 é só LE) — sem evidência de que se aplique
  diretamente a este firmware. Mantido na lista de vigilância porque a
  família de ataques "downgrade da derivação de chave" é uma classe
  relevante para quando LE Secure Connections for implementado (ver
  BLE-003 — preferir LESC/ECDH P-256 a Legacy Pairing evita exatamente
  esta classe de downgrade).
- **BLESA** (BLE Spoofing Attacks, USENIX WOOT 2020, ainda citado como
  referência-base em 2025-2026) — explora reconexão sem reautenticação
  suficiente da identidade do periférico, permitindo a um atacante
  fazer-se passar pelo dispositivo legítimo numa reconexão. **Relevante
  por analogia**: este firmware nem sequer autentica o central na
  ligação inicial (BLE-001), pelo que a classe de problema que BLESA
  descreve (falta de verificação de identidade em reconexão) já existe
  aqui de forma mais básica — não é uma vulnerabilidade nova a
  registar, é outra faceta de BLE-001.
- **SweynTooth** e sucessores — vulnerabilidades de implementação
  (crashes/deadlocks/bypass) em várias SDKs BLE, incl. Nordic
  historicamente. `seguranca/firmware-security` já pesquisou isto em
  2026-07-08 e não encontrou CVE específico à versão do SoftDevice
  S140/Adafruit Bluefruit Arduino usada aqui — não repetido, sem novo
  achado nesta execução.
- **KNOB** (Key Negotiation of Bluetooth, 2019) — downgrade do
  comprimento da chave de encriptação em Bluetooth Classic; a variante
  LE (KNOB sobre BLE) foi também demonstrada em alguns stacks. Não
  aplicável hoje a este firmware (sem encriptação de link nenhuma para
  haver algo a "negociar/downgrade" — BLE-001), mas mais um argumento
  para, ao implementar BLE-001, configurar explicitamente o
  comprimento mínimo de chave de encriptação aceite (`sd_ble_gap_
  sec_params_reply`) em vez de confiar em defaults do SoftDevice.

## Decisão desta execução — terminar sem alterações de código

Aplicando o critério "terminar sem alterações" já definido no prompt
desta rotina: o eixo BLE está agora **mapeado por inteiro** (tabela de
characteristics acima) com vetores concretos por risco, e as melhorias
reais identificadas (BLE-001 pairing/bonding, BLE-002 proteção do canal
de emergência, BLE-003 método MITM, BLE-005 privacy/RPA) são todas
**estruturais** — exigem desenho aceite e coordenação com
`bridge/ble_bridge.py` no mesmo commit (proibido fazer por iniciativa
própria sem esse desenho, e sem hardware nesta rotina cloud para
validar qualquer uma delas). Nenhum ficheiro de código foi alterado
nesta execução. `SECURITY_STATUS.md` é o entregável desta execução.

**Prioridade sugerida para quando o desenho de pairing avançar**
(ordem por gravidade real, não pela ordem em que os riscos foram
listados acima): 1) BLE-002 (emergência em claro/legível
anonimamente) e BLE-001 juntos (a correção é a mesma — exigir
`SECMODE_ENC_*`), 2) BLE-003 (escolher Numeric Comparison, dado o
ecrã OLED disponível), 3) BLE-005 (RPA, acoplado ao mesmo IRK do
bonding), 4) BLE-004/BLE-006 (reforços que só passam a fazer sentido
depois dos anteriores existirem).

---

# CareWear — Estado de Segurança do NFC

> Ficheiro da rotina de **Segurança NFC** (S05, mentalidade adversarial,
> assume-breach), distinta da rotina de **Desenvolvimento de NFC**
> (`rotina/nfc-development`, PR #2), que constrói o módulo do zero. Esta
> rotina audita o design à medida que avança e não escreve o driver
> principal. Ver também `PROJECT_STATUS.md`, secção "NFC", para o
> historial de decisões da rotina de desenvolvimento — os dois ficheiros
> devem ser lidos em conjunto.
>
> Existem outros `SECURITY_STATUS.md` (backend/API, firmware,
> frontend) em branches próprias (`seguranca/backend-api-security`,
> `seguranca/firmware-security`, `seguranca/frontend-security`, PRs
> #3-#5, ainda não integrados em `main`) — cada rotina de segurança
> mantém o seu, focado no seu domínio. Este cobre só NFC.
>
> Sem hardware físico nesta rotina cloud: não há tag/antena real para
> testar, nem confirmação de que existe sequer uma antena NFC ligada a
> P0.09/P0.10 no esquemático desta placa (ver `PROJECT_STATUS.md`,
> secção "NFC" — pergunta em aberto ao utilizador). Todos os achados
> abaixo são **requisitos de design/análise de ameaça**, não
> vulnerabilidades exploradas em hardware real.

## Como ler este ficheiro

Cada risco/requisito tem um ID `NFC-XXX` estável (não reutilizar
números). Estados: **REQUISITO** (a cumprir antes do NFC ser dado como
pronto — ainda não há código de hardware a validar), **ABERTO**
(vulnerabilidade real encontrada em código existente, sem correção),
**MITIGADO** (correção contida aplicada), **FECHADO**. Nesta fase
(esqueleto inerte, ver abaixo) todos os itens são **REQUISITO**.

## Estado do código NFC auditado nesta execução

`include/Nfc/Nfc.h` + `src/Nfc/Nfc.cpp` (branch `rotina/nfc-development`,
PR #2, commits `636ab46`/`a517873`) revistos por inteiro:

- `begin()` não toca em `UICR.NFCPINS` nem em nenhum registo do
  periférico NFCT, não faz `pinMode()` a P0.09/P0.10, não liga nenhuma
  biblioteca NFC. Devolve sempre `false`.
- `update()`/`isReady()` são no-ops/constante. Nenhum NDEF é montado,
  nenhum campo é emitido, nenhuma superfície de ataque nova existe hoje.
- Integração em `main.cpp` (`initNfc()`) segue o padrão "falha segura"
  já usado para `Lora` — não bloqueia o arranque, nada depende do
  sucesso.

**Conclusão desta auditoria**: o código atual não introduz nenhum risco
— é inerte por desenho, exatamente como documentado pela rotina de
desenvolvimento. **Sem correções de código nesta execução** (critério
"terminar sem alterações" do prompt desta rotina, secção "fase de
design"). O entregável desta execução é a lista de requisitos abaixo,
que a rotina de desenvolvimento tem de cumprir nas fases B/C/D antes de
qualquer ativação real de hardware ou emissão de NDEF.

## Caso de uso confirmado (âmbito desta auditoria)

Conforme `PROJECT_STATUS.md`: NFC serve **apenas** para iniciar/
emparelhar BLE por toque (tap-to-pair / handover OOB) e/ou identificar o
dispositivo — nunca para transportar dados clínicos ou PII. Todo o
checklist abaixo assume e reforça este âmbito; qualquer proposta futura
que o alargue (ex.: NFC como segunda via de comandos GATT-like) deve ser
travada por esta rotina antes de avançar (ver NFC-007).

## Requisitos de segurança NFC (a cumprir antes do NFC ser dado como pronto)

### NFC-001 — UID da tag NFC-A não pode ser usado como prova de identidade

**Gravidade: média-alta.** O periférico NFCT do nRF52840 responde com um
NFCID (UID) fixo, tipicamente derivado do identificador único de fábrica
do chip (FICR) — é lido em claro por qualquer leitor NFC compatível
(telemóvel comum, leitor ~10€) e é trivialmente clonável com hardware
barato (tags NFC programáveis, apps Android de "NFC clone", Proxmark).
**Vetor concreto**: se uma fase futura usar o UID sozinho para o
telemóvel "reconhecer" que está a falar com o wearable legítimo (ex.:
"só aceito handover de um UID já visto antes"), um atacante que
aproxime um leitor uma única vez consegue fabricar uma tag/emulador que
responde com o mesmo UID e se faz passar pelo wearable (ou engana o
wearable para aceitar um leitor impostor, consoante o sentido da
verificação).
**Requisito**: o UID nunca pode ser o único fator de confiança. A
autenticação real do par BLE tem de vir do próprio protocolo BLE
(bonding/LTK — ver risco já registado `FW-002` em
`seguranca/firmware-security`/`SECURITY_STATUS.md`, PR #3: hoje nenhuma
characteristic GATT exige pairing/bonding, o que agrava este ponto — o
NFC não pode ser usado para compensar essa lacuna, tem de esperar por
ela ou ser desenhado em conjunto).

### NFC-002 — Handover NFC→BLE sem confirmação no dispositivo permite emparelhamento silencioso

**Gravidade: alta** (wearable médico usado por pessoas com demência —
pode ser aproximado de um leitor em transporte público, sala de espera,
ou por um cuidador/terceiro sem que o utilizador perceba o que está a
acontecer).
**Vetor concreto**: o comentário já existente em `Nfc.h` refere
"deteccao de campo externo para o caso de uso tap-to-pair" como trabalho
futuro de `update()`. Se essa deteção de campo, por si só, disparar o
início (ou pior, a aceitação automática) do processo de pairing/bonding
BLE, qualquer aproximação de um leitor NFC — intencional, acidental, ou
um ataque de relay (ver NFC-006) — inicia o handover sem qualquer sinal
para o portador do dispositivo.
**Requisito**: qualquer transição "campo NFC detetado" → "iniciar/
aceitar pairing BLE" tem de passar por confirmação explícita e visível
no próprio wearable antes de aceitar a ligação — ex.: ecrã OLED a
mostrar "Emparelhar com [nome/endereço]? " com timeout curto e
comportamento por omissão de recusar (fail-closed), no mesmo espírito
do padrão de countdown já usado em "Medir agora" (`Ppg::requestManualHr`
via `kDumpCtrlForceHr`). Nota: o botão físico de confirmação está
**partido** neste dispositivo (ver `PROJECT_STATUS.md`, secção
"Riscos") — a rotina de desenvolvimento tem de decidir um mecanismo de
confirmação que não dependa exclusivamente desse botão (ex.: o mesmo
bypass série `WAKE`/`SLEEP` já usado, ou um gesto/movimento IMU, até o
botão ser substituído).

### NFC-003 — Conteúdo NDEF: superfície mínima, sem PII/dados clínicos

**Gravidade: alta se violado** (mas preventivo — nada foi implementado
ainda). Uma tag NFC passiva é legível por **qualquer** leitor a poucos
cm de distância, sem qualquer autenticação prévia — é a definição de
"sem controlo de acesso". Qualquer campo colocado no NDEF é
efetivamente público a quem tiver um telemóvel comum e uma oportunidade
de aproximação breve.
**Requisito**: o NDEF não pode conter nome do paciente, NIF, morada,
diagnóstico, nem qualquer campo já classificado como sensível noutras
partes do sistema (`bridge/crypto_utils.py` já cifra NIF/morada em
repouso — o NFC não pode reintroduzir esses dados em claro por outra
via). Conteúdo aceitável: o mínimo necessário ao handover BLE
(endereço/identificador do periférico, e se aplicável, dados OOB
efémeros — ver NFC-004).
**Consideração adicional de privacidade (rastreabilidade)**: se o
identificador exposto for o endereço BLE público fixo do dispositivo
(nunca rotativo), qualquer scanner NFC/BLE passivo ganha um
identificador estável para seguir a localização do portador ao longo do
tempo — um risco de privacidade distinto de PII clínica mas relevante
para um wearable de saúde. Recomenda-se preferir, quando o stack BLE
suportar, um endereço privado resolúvel (RPA) em vez do MAC público
fixo — a confirmar se está ao alcance do SoftDevice S140 já em uso
(`main.cpp`) sem reabrir o âmbito de outra rotina.

### NFC-004 — Dados OOB (se vierem a existir) têm de ser efémeros e ligados à sessão

**Gravidade: média-alta**, condicional a uma fase futura que ainda não
existe. Se o handover vier a incluir material OOB do tipo LE Secure
Connections (nonce + valor de confirmação, conforme "Bluetooth Secure
Simple Pairing over NFC", NFC Forum AD-BTSSP-1.3) para reforçar o
pairing contra MITM, esse material **tem de ser gerado de novo a cada
leitura/tentativa de pairing**, nunca gravado como valor estático e
fixo na tag. Um valor OOB estático reutilizável seria capturável (por
leitura direta ou por um relay de curto alcance, ver NFC-006) e
reproduzível mais tarde por um atacante para se autenticar como o par
legítimo — o mesmo princípio de "nonce nunca reutilizado" já aplicado
com sucesso ao streaming BLE cifrado neste projeto (ver
`PROJECT_STATUS.md`, secção "Cifra AES-CTR do modo de dados").
**Requisito**: se e quando esta fase avançar, o NDEF de OOB não pode ser
uma tag estática de conteúdo fixo — implica handover negociado (TNEP,
NFC Forum) com conteúdo dinâmico calculado a cada leitura, não o modo
"tag estática" mais simples referido em `Nfc.h`/`PROJECT_STATUS.md`
como opção de fase C. Registar esta implicação de desenho na fase C
antes de escolher entre handover estático vs. negociado — a escolha
tem impacto de segurança direto, não é só uma questão de simplicidade
de implementação.

### NFC-005 — Tag bloqueada como só-leitura após provisioning

**Gravidade: média.** Se o mecanismo de exposição de memória do NFCT
(a confirmar pela rotina de desenvolvimento — buffer RAM servido
dinamicamente vs. emulação de Tag Type 2/4 com memória persistente)
permitir escrita externa depois do provisioning inicial, um atacante
com acesso físico breve poderia reescrever o NDEF (ex.: redirecionar
para um URL de phishing, ou para um endereço BLE de um dispositivo
impostor, iniciando o handover NFC-002 contra o atacante em vez do
wearable real).
**Requisito**: bloquear a tag como só-leitura (equivalente ao lock
bit/CC read-only do NFC Forum Type Tag) assim que o conteúdo de
provisioning for definido — antes de qualquer uso em campo. **Pendente
de confirmação técnica**: o NFCT nativo do nRF52840, servido por RAM
controlada pelo firmware, pode não ter este conceito da mesma forma que
uma memória Tag Type externa — a rotina de desenvolvimento deve
confirmar e documentar como este requisito se aplica ao mecanismo real
escolhido antes de a fase C ser dada como concluída.

### NFC-006 — Relay attacks (retransmissão do campo NFC à distância)

**Gravidade: média**, risco residual aceite nesta fase de design (não
eliminável só com NFC). Ferramentas conhecidas (framework NFCGate e
derivados, técnica "Ghost Tap") já demonstradas para retransmitir
sinais NFC de pagamento entre dois telemóveis ligados à internet,
estendendo o alcance efetivo de "poucos cm" para qualquer distância com
cobertura de rede — o mesmo princípio aplica-se a qualquer troca NFC,
incluindo um handover tap-to-pair. Um atacante com um dispositivo perto
do wearable da vítima e outro perto do seu próprio telemóvel pode fazer
o telemóvel "ver" o wearable como presente à distância, disparando o
handover sem o portador saber.
**Fontes** (pesquisa aplicada 2026-07-08): [Zimperium — "Tap-and-Steal:
The Rise of NFC Relay Malware on Mobile Devices"](https://zimperium.com/blog/tap-and-steal-the-rise-of-nfc-relay-malware-on-mobile-devices);
[Kaspersky — "Direct and reverse NFC relay attacks being used to steal
money" (2026)](https://www.kaspersky.com/blog/nfc-gate-relay-attacks-2026/55116/);
[The Hacker News — "RatOn Android Malware ... NFC Relay and ATS Banking
Fraud" (set. 2025)](https://thehackernews.com/2025/09/raton-android-malware-detected-with-nfc.html).
**Mitigação**: como nenhum item acima (NFC-001 a NFC-004) permite que o
conteúdo da tag por si só autentique nada nem transporte segredos de
longa duração, o pior que um relay consegue nesta fase é iniciar o
processo de handover — que fica sempre dependente da confirmação
explícita no dispositivo (NFC-002). Se a fase C vier a implementar OOB
dinâmico (NFC-004), notar que um relay em tempo real quebraria
especificamente essa proteção se conseguir operar dentro da janela
temporal do handshake — LE Secure OOB não é imune a relay puro (só a
replay depois da janela expirar). Aceite como risco residual nesta
fase, a reavaliar quando a fase C for desenhada em detalhe.

### NFC-007 — NFC confinado a iniciar BLE, nunca uma segunda porta de dados

**Gravidade: preventivo.** Reforça explicitamente o requisito do
utilizador: o NFC não pode, em nenhuma fase futura, ganhar comandos
equivalentes aos já existentes via GATT (`dumpCtrlChar`, `aesKeyChar`,
leitura/escrita de registos clínicos). Qualquer proposta nesse sentido
deve ser travada por esta rotina antes de avançar — a superfície de
ataque de uma tag NFC passiva e sem autenticação é estruturalmente pior
para transportar comandos do que o BLE já autenticado (ainda que
imperfeitamente, ver FW-002) por bonding.

### NFC-008 — Se o dispositivo alguma vez passar a LER tags externas, tratar todo o NDEF como não confiável

**Gravidade: não aplicável hoje** (o design atual não inclui leitura de
tags — o wearable só as emite, ver `Nfc.h`). Registado como requisito
preventivo caso o âmbito mude no futuro: nunca agir sobre um NDEF lido
(URIs, `tel:`, deep links) sem confirmação explícita do utilizador;
parser NDEF blindado contra tamanho declarado inconsistente com o real
e tipos de registo desconhecidos (ignorar, não crashar/interpretar por
omissão). Sem código a auditar nesta vertente enquanto o âmbito não
mudar.

### NFC-009 — Nunca incluir registo URI/AAR dinâmico ou "reader-processável" no NDEF emitido

**Gravidade: preventiva**, reforçada por pesquisa nova (ver "Pesquisa
contínua" abaixo, 2026-07-10). Um NDEF Type-2/4 pode conter, além de um
identificador simples, registos `URI` (ex.: deep link, `tel:`, URL de
loja de app) ou um Android Application Record (AAR) que o telemóvel
leitor processa automaticamente ao aproximar-se — sem qualquer
confirmação do lado do wearable (NFC-002 protege o *wearable*, não o
telemóvel de quem o aproxima). Pesquisa 2026-07-10 confirma que esta
classe de ataque é ativa e atual: `CVE-2026-11108` (Chrome no Android,
divulgado 2026-06-04) é uma escalada de privilégios acionada por
conteúdo NFC processado pelo leitor via uma página HTML criada
propositadamente — ou seja, o *dispositivo que lê* uma tag NFC pode ser
comprometido pelo próprio conteúdo da tag, não só o dispositivo que a
emite. Como a tag do CareWear é passiva e sem autenticação (mesmo
princípio do NFC-003), qualquer conteúdo nela é, por definição,
"conteúdo não confiável" do ponto de vista de quem a lê — incluindo o
telemóvel do próprio cuidador/utilizador.
**Requisito**: o NDEF emitido pelo CareWear deve conter **apenas** o
mínimo necessário ao handover BLE (identificador/endereço + OOB efémero
se aplicável, ver NFC-003/NFC-004) — **nunca** um registo `URI` (mesmo
que aparentemente inofensivo, ex. link para app store) nem AAR, a menos
que explicitamente decidido e revisto por esta rotina antes de
implementar, precisamente porque esse tipo de registo é o que leitores
(navegadores/OS) processam de forma mais automática e é o vetor
demonstrado por `CVE-2026-11108`.

## Resumo para a rotina de desenvolvimento (NFC #2 e seguintes)

Antes de dar o NFC como "pronto" (qualquer fase que ative
`UICR.NFCPINS` ou emita um NDEF real), confirmar que:

1. NFC-001, NFC-002, NFC-007 estão cumpridos por desenho (autenticação
   nunca depende do UID/NFC sozinho; handover sempre confirmado no
   dispositivo; sem comandos de dados via NFC).
2. NFC-003/NFC-009 estão cumpridos no conteúdo exato do NDEF proposto
   (rever com esta rotina antes de commitar o primeiro NDEF real — ver
   Fase C em `PROJECT_STATUS.md`; sem registos `URI`/AAR).
3. NFC-004/NFC-005 foram avaliados e documentados (mesmo que a decisão
   seja "handover estático simples, sem OOB dinâmico, aceitando o risco
   residual X" — desde que seja uma decisão explícita, não omissão).
4. NFC-006 é um risco residual conhecido e aceite, não uma surpresa.

## Pesquisa contínua (ataques NFC/handover novos)

**2026-07-08** (fontes na secção NFC-006) — focada em relay attacks de
pagamento (aplicável por analogia ao handover, ver mitigação). Nenhuma
CVE específica ao periférico NFCT do nRF52840 ou ao SoftDevice S140
encontrada nesta pesquisa.

**2026-07-10** (2ª execução desta rotina — código NFC inalterado desde
a auditoria anterior, ver "Estado desta execução" abaixo; pesquisa
repetida conforme indicado):
- `CVE-2026-34126` (TP-Link Tapo, CVSS 7.5) — atacante em alcance
  Bluetooth pode fazer sniffing/MITM ao setup Bluetooth e manipular
  dados de configuração transmitidos durante a inicialização do
  dispositivo. Relevante por analogia a NFC-004: reforça que qualquer
  material OOB trocado via NFC para reforçar o pairing BLE tem de ser
  efémero e ligado à sessão — um setup/pairing "de fábrica" sem esse
  cuidado continua a ser explorado ativamente em produtos reais no
  mesmo espaço de produto (wearables/IoT). Fonte:
  [TheHackerWire — CVE-2026-34126](https://www.thehackerwire.com/vulnerability/CVE-2026-34126/).
- `CVE-2026-11108` (Chrome para Android, divulgado 2026-06-04) —
  escalada de privilégios via processamento de conteúdo NFC por uma
  página HTML criada propositadamente. Motivou o novo requisito
  **NFC-009** acima (nunca emitir registos `URI`/AAR no NDEF do
  CareWear). Fonte:
  [Windows Forum — CVE-2026-11108](https://windowsforum.com/threads/cve-2026-11108-chrome-on-android-nfc-privilege-escalation-fix-before-149-0-7827-53.424561/).
- `CVE-2026-31629` (kernel Linux, NFC LLCP, publicado 2026-04-24) —
  use-after-free no caminho de receção LLCP do kernel Linux. Não se
  aplica ao nRF52840 (é código de *leitor* Linux, não ao periférico
  NFCT do wearable), mas reforça em geral o requisito NFC-008: parsers
  NDEF/NFC são uma fonte real e recorrente de bugs de memória — caso o
  CareWear alguma vez passe a ler tags externas, o parser tem de ser
  escrito/escolhido com esse histórico em mente. Fonte:
  [Windows Forum — CVE-2026-31629](https://windowsforum.com/threads/cve-2026-31629-missing-return-in-linux-nfc-llcp-can-trigger-double-release-uaf.415223/).
- Continua sem CVE específica ao periférico NFCT do nRF52840 ou ao
  SoftDevice S140.

Repetir esta pesquisa em execuções futuras desta rotina, à medida que a
fase C avançar (termos sugeridos: "NFC Forum BTSSP vulnerability",
"TNEP negotiated handover security", CVEs Nordic nrfx/NFCT).

## Estado desta execução (2026-07-10)

Verificado via `git log -- src/Nfc/ include/Nfc/`: **nenhum commit novo
toca no módulo NFC desde `636ab46`/`a517873` (2026-07-08)**, já
auditados na execução anterior (PR #7, mesclado em `main`). A secção
"NFC" de `PROJECT_STATUS.md` também não tem entradas novas — a rotina
de desenvolvimento continua bloqueada pela mesma pergunta em aberto ao
utilizador (existência/pinout da antena NFC). Confirmado também que não
existe nenhuma branch `rotina/nfc-development` com trabalho por
publicar (`git log origin/main..origin/rotina/nfc-development` vazio).
**Sem correções de código nesta execução** — nada mudou para auditar
além da pesquisa contínua acima, que resultou no novo requisito
NFC-009. Entregável desta execução: NFC-009 + registo de pesquisa.

# CareWear — Estado de Segurança e Privacidade

> Ficheiro criado pela rotina de Segurança (Privacidade e conformidade
> RGPD/GDPR — ver `PROJECT_STATUS.md` para o estado funcional geral).
> Ler este ficheiro antes de começar qualquer execução desta rotina.

**Aviso importante**: nada neste ficheiro é aconselhamento jurídico. É
análise técnica de como o código atual se compara a princípios do
RGPD/GDPR, para apoiar decisões que cabem ao utilizador/responsável pelo
tratamento (retenção clínica real, base legal formal, DPIA, etc.). Nunca
apresentar isto como "conformidade certificada".

**Contexto do produto**: CareWear é um wearable médico de investigação
para pessoas com demência. Trata **categorias especiais de dados**
(dados de saúde, Art. 9 RGPD) de titulares particularmente vulneráveis,
muitos com capacidade de consentimento limitada. É um protótipo — o
objetivo desta rotina é alinhar por desenho, não certificar.

---

## Inventário de dados pessoais (documento central desta rotina)

Convenção: "Cifrado?" refere-se ao dado em repouso, no local onde fica
guardado de forma persistente.

| Dado | Onde é recolhido | Onde é guardado | Transmitido para | Cifrado? | Retenção |
|---|---|---|---|---|---|
| Sinais fisiológicos (FC, SpO₂) + movimento (accel/gyro, passos, freefall, inatividade) | Sensores do wearable (IMU/PPG) | Ring buffer QSPI (dispositivo) → `bridge/carewear_history.db` (`sensor_records`, `bridge/storage.py`) | BLE (wearable→bridge) → WebSocket `ws://localhost:8765` (bridge→dashboard) | **BLE: sim** (AES-CTR, ver nota 1) · **`.db`: não** · **WebSocket: não** (sem TLS, ver GDPR-004) | Configurável, 30 dias por omissão (`bridge/storage.py`, `get_retention_days()`/`set_retention_days()`) |
| Alertas de emergência (SOS/queda) | Firmware (`Emergency.cpp`) via BLE | `bridge/carewear_history.db` (`emergency_alerts`) | BLE → WebSocket (idem acima) | Idem acima | **Nunca apagado** (deliberado — ver GDPR-006) |
| Identidade do paciente (nome, idade) | Introduzida no dashboard (perfil/pacientes fictícios nesta fase) | `web/dashboard/index.html` (`localStorage`, dados em memória do protótipo) | Export FHIR (`buildFhirBundle()`) inclui nome+idade em claro | **Não** (localStorage é sempre texto simples) | Sem TTL — só apagado manualmente (ver correção desta execução, "Direito ao esquecimento" abaixo) |
| NIF, morada, telefone (perfil Utente/Família) | Formulário "Perfil" no dashboard | `localStorage` (`carewear_profile`) | Não transmitido a mais lado nenhum (confirmado — não aparece em `exportFhirSummary()`/`exportRealCsv()`) | **Não** (ver GDPR-002) | Idem acima |
| NIF, morada (modelo `Patient`, camada BD avançada) | `bridge/storage_advanced.py` (ORM, não integrado com o fluxo real ainda) | `carewear.db` (SQLite dev) / PostgreSQL (produção, via `DATABASE_URL`) | Não exposto pela API REST atual (só leitura de tendências/adesão, não de campos do paciente) | **Sim, se configurado** — AES-256-GCM, chave Argon2id (`bridge/crypto_utils.py`); **degrada para texto simples** se `CAREWEAR_DB_ENCRYPTION_KEY`/`_SALT_HEX` não estiverem definidas (só um aviso no arranque) | 365 dias (`sensor_records`), 5/7/10 anos (anomalias/alertas/emergências) — `DataRetention.cleanup()`, não ligado a scheduler real ainda |
| Consentimento de partilha (`shareVitals`/`shareRoutine`/`shareAlerts`) | Cartão "Consentimento e partilha de dados", vista Definições | `localStorage` (`carewear_consent`), por paciente | Não transmitido — só controla o que o próprio browser mostra/exporta | Não (não é dado clínico, risco baixo) | Sem TTL, sem histórico de versões (ver GDPR-001) |
| `consent_records` (tabela ORM GDPR/HIPAA: scope, granted, version, signed_at, expires_at) | — | `storage_advanced.py`, **desenhada mas nunca escrita/lida por nenhum fluxo real** | — | N/A (tabela vazia) | N/A |
| `audit_log` (ação, recurso, IP, detalhes JSONB) | Só 1 endpoint escreve aqui hoje: `POST /api/medications/{id}/adherence` (`bridge/api.py`, 2026-07-08) | `storage_advanced.py` | — | N/A | Sem política de retenção própria definida ainda |
| Medicação e adesão (nome, dosagem, `markDoseTaken()`) | Vista Medicação | `localStorage` (`carewear_medication_log`, `carewear_medications_registry`) + `carewear_adherence_analytics_<patientId>` (`medication-reminders.js`) | Não transmitido (protótipo local); endpoint `POST /api/medications/.../adherence` existe mas dashboard não lhe chama ainda | Não | Sem TTL |
| Notas de cuidadores, equipa de cuidadores, alertas lidos/apagados/silenciados | Várias vistas | `localStorage` (`carewear_caregiver_notes`, `carewear_caregiver_team`, `carewear_deleted_alerts`, `carewear_muted_alerts`, `carewear_read_alerts`, `carewear_alert_occurrences`) | Não transmitido | Não | Sem TTL |
| Export CSV (dados brutos de sensores) | Botão "Exportar dados" | Gerado em memória no bridge, descarregado como ficheiro local | Fica no computador de quem exporta — fora do controlo da app depois disso | N/A (ficheiro local do utilizador) | N/A |
| Export FHIR (Patient + Observations de alertas/anomalias) | Botão "Exportação clínica" | Idem acima | Idem acima (nome+idade do paciente incluídos em claro) | N/A | N/A |
| Chave AES do streaming BLE (`aesKeyChar`) | Trocada no provisioning | Flash interna do dispositivo; bridge via `CAREWEAR_AES_KEY_HEX` (variável de ambiente) | — | Sim (é a própria chave de cifra) | Vive enquanto o dispositivo não for reprovisionado |

**Nota 1** — cifra BLE: AES-CTR protege confidencialidade mas **não
autentica** (sem MAC/tag de integridade) — já documentado em
`PROJECT_STATUS.md` como limitação aceite por decisão de protocolo
(pacote de 20 bytes, MTU BLE por omissão).

---

## Lacunas / riscos identificados (GDPR-XXX)

### GDPR-001 — Consentimento por representante/procurador não previsto nem registado
**Princípio**: base legal do tratamento e condições de consentimento
(Art. 6/9 RGPD, por analogia com o regime de incapacidade do Art. 8) —
num contexto de demência, uma parte relevante dos titulares não tem
capacidade plena para prestar consentimento informado, pelo que o
sistema devia poder registar **quem** consentiu em nome de quem, e a que
título (o próprio titular vs. um representante legal/familiar com poder
de decisão).

**Situação atual**: `loadConsent()`/`setConsent()`
(`web/dashboard/index.html`) guardam só `{shareVitals, shareRoutine,
shareAlerts, lastChanged}` por paciente — nenhum campo identifica quem
alterou o consentimento, em que capacidade, nem existe qualquer
verificação de que quem o fez tem legitimidade para decidir pelo
titular. A tabela `consent_records` em `storage_advanced.py` já foi
desenhada com `granted`/`version`/`signed_at`/`expires_at`, mas **não
tem coluna para identificar o representante** e continua desligada de
qualquer fluxo real (nunca é escrita).

**Recomendação** (requisito, não decidido nesta execução — depende de
desenho de produto/decisão do responsável pelo tratamento): estender
`consent_records` com `granted_by_user_id`, `capacity` (`self` |
`representative`) e `representative_relationship`, e ligar
`setConsent()` no dashboard a esse registo (hoje é só um toggle
client-side, sem qualquer verificação de identidade de quem o
acciona — qualquer pessoa com acesso à conta Utente/Família pode
alterá-lo). Não decidido: que critérios tornam alguém um representante
legítimo — isso é uma política clínica/legal do utilizador.

---

### GDPR-002 — PII em `localStorage` sem cifra, sem TTL e (até esta execução) sem forma de apagar
**Princípio**: minimização (Art. 5.1.c), limitação da conservação
(Art. 5.1.e), direito ao esquecimento (Art. 17).

**Situação atual (antes desta execução)**: `carewear_profile`
(nome, email, telefone, **NIF**, **morada**, dados do cuidador) e todas
as outras chaves `carewear_*` (consentimento, medicação, notas,
histórico de alertas) ficavam em `localStorage` do browser — texto
simples, sem expiração, e **sem nenhuma forma de as apagar**:
`logout()` só trocava de ecrã, nunca limpava dados.

**Correção aplicada nesta execução** (`web/dashboard/index.html`):
- Nova função `eraseAllLocalData()` — varre `Object.keys(localStorage)`
  por prefixo `carewear_` (não uma lista fixa, para não ficar
  desatualizada quando surgir uma chave nova, ex.: sufixo dinâmico por
  paciente em `medication-reminders.js`) e remove tudo, com
  confirmação explícita do utilizador (irreversível).
- Novo botão "Apagar dados deste navegador…" na vista Definições →
  Zona de risco, ao lado do já existente "Repor leituras…", com texto
  claro sobre o que apaga e o que **não** apaga (histórico do bridge,
  registos no dispositivo).
- **Verificado**: teste Playwright real confirma que todas as chaves
  `carewear_*` são removidas após confirmar a ação; verificação de
  sintaxe (`node --check` sobre o bloco `<script>` extraído) sem erros
  novos; captura de ecrã confirma o botão renderizado corretamente na
  Zona de risco.

**Correção adicional (2026-07-17)** — TTL automático implementado:
`purgeExpiredLocalDataIfNeeded()`, chamada uma vez no arranque do script
(antes de qualquer outra leitura de `localStorage`), guarda
`carewear_last_activity` a cada visita; se passarem mais de
`LOCAL_DATA_TTL_DAYS` (30, mesmo prazo já usado na retenção do bridge)
dias sem nenhuma visita, todas as chaves `carewear_*` são apagadas
automaticamente. É uma janela deslizante de inatividade (reinicia a cada
visita), não um prazo fixo desde a criação — não apaga dados de quem usa
a app ativamente. Nota visível também na UI (Definições → Zona de perigo).
Verificado com Playwright: purga aos 31 dias de inatividade simulada,
preserva aos 10 dias.

**Ainda em aberto** (não resolvido, requisito/proposta):
- `localStorage` continua sem cifra (é sempre texto simples por
  natureza da API do browser) — cifrar exigiria uma chave/passphrase
  gerida pelo próprio utilizador no browser, uma mudança maior de UX
  não implementada aqui.
- Questionar se o **NIF** é sequer necessário para a finalidade deste
  dashboard (monitorização de rotina/saúde) — hoje é recolhido e
  tratado como "sensível" só para efeitos de aprovação de alteração,
  não porque sirva a finalidade declarada da app. Proposta: reavaliar
  necessidade de recolha do NIF (minimização por finalidade, Art.
  5.1.b/c) — decisão de produto, não tomada aqui.

---

### GDPR-003 — `audit_log`/`consent_records` desenhados mas maioritariamente não usados
**Princípio**: responsabilização (Art. 5.2) e segurança do tratamento
(Art. 32) — quem acede/altera dados de saúde deve ficar registado.

**Situação atual**: `audit_log` só é escrito por **um** endpoint
(`POST /api/medications/{id}/adherence`, adicionado 2026-07-08). Todo o
resto do sistema em produção real — ingestão de sensores via
`ble_bridge.py`→`storage.py` (a versão simples, não o ORM), exports
CSV/FHIR do dashboard, alterações de consentimento e de perfil — **não
gera nenhum registo de auditoria**. `consent_records` nunca é escrita.
O "consentimento" hoje é só um interruptor client-side (já documentado
honestamente no próprio código: "protótipo, aplica-se só a esta
conta/sessão") — um Médico/Técnico com acesso direto ao bridge ou à
BD contorna-o sem deixar rasto.

**Recomendação**: antes de qualquer uso com dados reais de utentes,
ligar `audit_log` aos pontos de acesso reais (leitura/exportação de
dados de um paciente, não só a única escrita já coberta), e considerar
que a aplicação do consentimento no lado do servidor (não só no
browser) é pré-requisito para o interruptor ter efeito real de
proteção, não só de interface.

---

### GDPR-004 — Cifra em trânsito: WebSocket bridge↔dashboard sem TLS
**Princípio**: segurança do tratamento (Art. 32) — dados de saúde em
trânsito.

**Situação atual**: BLE (wearable→bridge) já está cifrado (AES-CTR,
resolvido 2026-07-07 — ver nota 1 acima sobre falta de autenticação).
O canal bridge→dashboard usa `ws://localhost:8765` — texto simples,
sem TLS, sem autenticação (documentado no próprio `PROJECT_STATUS.md`:
"canal não autenticado — só deve ser exposto em localhost").
`WS_HOST` está de facto fixado em `"localhost"` no código (não
`0.0.0.0`), o que reduz o risco a outros processos/utilizadores da
mesma máquina — mas isso é uma suposição de implantação, não uma
garantia técnica reforçada pelo código (nada impede alguém de mudar
`WS_HOST` ao correr o bridge numa rede partilhada).

**Recomendação**: se este bridge alguma vez for exposto além de
`localhost` (ex.: acesso remoto a partir de outro dispositivo na rede
doméstica), `wss://` + autenticação por token tornam-se obrigatórios,
não opcionais. Enquanto for estritamente local/single-user, risco
aceite documentado, não uma correção pendente.

---

### GDPR-005 — `.db` sem cifra em repouso (fora dos 2 campos já cifrados)
**Princípio**: segurança do tratamento (Art. 32).

**Situação atual**: `bridge/carewear_history.db` (`storage.py`, em
produção real) não tem nenhuma cifra — ficheiro SQLite plano com
`sensor_records`/`emergency_alerts` (dados de saúde). Em
`storage_advanced.py` (protótipo não integrado), só `Patient.nif`/
`address` têm cifra real (AES-256-GCM via `crypto_utils.py`), e essa
cifra **degrada silenciosamente para texto simples** (só um aviso no
arranque) se as variáveis de ambiente de chave/sal não estiverem
configuradas — todos os outros campos de saúde do ORM (`SensorRecord`,
`MedicationAdherence`, `AnomalyDetection`, etc.) não têm nenhuma cifra
prevista.

**Não alterado nesta execução** (fora do que esta rotina pode decidir
— "NÃO PODE: implementar cifra de dados/gestão de chaves de raiz sem
desenho aceite"): cifra de disco completo (ex. LUKS/BitLocker) ou
cifra ao nível do SQLite (ex. SQLCipher) para produção real. Aceite
como estado atual de um protótipo local de desenvolvimento — registar
como bloqueador antes de qualquer utilização com dados reais de
utentes.

---

### GDPR-006 — Retenção "para sempre" de `emergency_alerts` sem justificação documentada
**Princípio**: limitação da conservação (Art. 5.1.e) — retenção
indefinida exige uma base/justificação (ex. obrigação legal, defesa de
direitos), não pode ser "porque sim".

**Situação atual**: `emergency_alerts` é deliberadamente excluído de
toda a limpeza automática (`storage.py`: nunca purgado; `storage_advanced.py`:
`DataRetention` documenta 10 anos como valor de referência, mas o
`cleanup()` também nunca o toca). A justificação registada até agora
(`PROJECT_STATUS.md`) é "histórico de segurança" — razoável em
princípio (segurança do titular, eventual apuramento de
responsabilidade em caso de queda/emergência real), mas não está
formalizada como decisão do responsável pelo tratamento, nem tem um
prazo definido (mesmo que muito longo).

**Recomendação** (não decidido aqui — é uma decisão clínica/legal do
utilizador): documentar explicitamente a base legal/finalidade que
justifica a retenção indefinida (ou substituir por um prazo finito,
ainda que longo, com justificação escrita).

---

## Verificação desta execução

- `eraseAllLocalData()` testada com Playwright real (Chromium local):
  popula `carewear_profile`/`carewear_consent`, confirma diálogo de
  confirmação, confirma que `Object.keys(localStorage)` fica vazio
  depois de chamar a função.
- Bloco `<script>` de `web/dashboard/index.html` extraído e validado
  com `node --check` — sem erros de sintaxe introduzidos (o erro
  pré-existente de `new Function()` sobre o ficheiro completo, testado
  antes desta alteração via `git stash`, já existia e não está
  relacionado — devido ao regex de teste capturar texto "&lt;script&gt;"
  dentro de comentários HTML, não ao código real).
- Captura de ecrã confirma o botão "Apagar dados deste navegador…"
  visível na Zona de risco, junto de "Repor leituras…".
- Nenhuma tabela/migração SQL alterada nesta execução; nenhuma decisão
  de retenção clínica ou de cifra de raiz foi tomada — só documentada
  como pendente (GDPR-001, 002 parcial, 003, 004, 005, 006).

## Estado por eixo (para a próxima execução desta rotina)

| Eixo do checklist | Estado |
|---|---|
| Base legal/consentimento (incl. representante) | Lacuna registada — GDPR-001 |
| Inventário de dados | Feito (tabela acima) — manter atualizado via `git log` |
| Minimização | Corrigido parcialmente (GDPR-002 — falta TTL/cifra/reavaliar NIF) |
| Anonimização/pseudonimização | Não avaliado a fundo nesta execução — próximo eixo sugerido (ex.: exports FHIR incluem nome em claro; sensor_records simples não tem identificador direto mas fica implicitamente ligado a um único dispositivo/paciente) |
| Retenção | Maioritariamente alinhado (`storage.py`/`storage_advanced.py` têm políticas) — GDPR-006 sobre a exceção de emergências |
| Direito ao esquecimento/portabilidade | Corrigido parcialmente esta execução (apagar local); falta apagar o lado do bridge/`.db` a pedido do titular |
| Logging/auditoria | Lacuna registada — GDPR-003 |
| Encriptação (repouso/trânsito) | Parcial — BLE e 2 campos ORM cifrados; `.db`, WebSocket, restante ORM por cifrar — GDPR-004, GDPR-005 |
| Transferências a terceiros | Nenhum provedor externo ligado ainda (Twilio bloqueado por credenciais) — sem requisito imediato, revisitar quando existir |

# CareWear — Estado de Segurança do Backend/API

> Ficheiro da rotina de **Segurança** (mentalidade adversarial, assume-breach),
> distinta da rotina de **Desenvolvimento** (`PROJECT_STATUS.md`). Lê sempre
> os dois antes de agir — `PROJECT_STATUS.md` documenta decisões e riscos
> conhecidos que não devem ser reabertos sem justificação nova.
>
> Esta é a rotina **S02 (backend/API)** — existe também uma rotina irmã
> **S04/firmware-security** (`seguranca/firmware-security`, IDs `FW-XXX`,
> ainda não integrada em `main` à data desta execução) que audita o
> firmware C++. Este ficheiro cobre só `bridge/` (WebSocket + SQLite +
> ORM + API REST). Não duplicar achados de dependências vulneráveis com a
> rotina S07 — aqui só se regista o que afeta diretamente o backend.

## Correção honesta ao enunciado desta rotina

O enunciado desta execução assumia que "NÃO existe ainda uma API HTTP/
REST/GraphQL". **Isso deixou de ser verdade em 2026-07-07/08** —
`bridge/api.py` (FastAPI + Uvicorn) já existe e está em produção neste
protótipo, com:

- `GET /health` (sem autenticação, só liveness).
- `GET /api/devices/{device_id}/heart-rate-trends`
- `GET /api/patients/{patient_id}/medication-adherence`
- `GET /api/devices/{device_id}/activity-distribution`
- `POST /api/medications/{medication_id}/adherence` (primeiro endpoint de
  escrita, 2026-07-08).

Autenticação: chave estática partilhada (`X-API-Key` / `CAREWEAR_API_KEY`),
falha fechada sem a variável configurada, comparação em tempo constante
(`hmac.compare_digest`, corrigido 2026-07-07 — ver `PROJECT_STATUS.md`).
**Ainda não integrada** com `web/dashboard/index.html` nem com
`ble_bridge.py`/`storage.py` (usa `storage_advanced.py`, o ORM avançado,
que continua paralelo/não ligado ao streaming BLE real).

Consequência prática para esta rotina: vários itens do checklist listados
no enunciado como "requisitos PRÉVIOS para a API planeada" **já são
aplicáveis hoje**, não só requisitos futuros — corrigido na secção
correspondente mais abaixo. O que continua a não existir: qualquer
integração real entre a API e o resto do sistema, e qualquer modelo de
autorização por papel (o ORM já modela `User.role` — utente/clínico/
admin — mas nenhum endpoint o usa ainda).

## Como ler este ficheiro

Cada risco tem um ID `API-XXX` estável (não reutilizar números, mesmo que
um risco seja fechado). Estados possíveis: **ABERTO** (registado, sem
correção aplicada — normalmente porque a correção exige uma decisão de
desenho que não compete a esta rotina tomar sozinha), **MITIGADO**
(correção contida aplicada nesta rotina, risco residual descrito),
**FECHADO** (resolvido por completo). Nada é declarado "resolvido" sem
prova (teste automatizado a passar).

## Checklist aplicável hoje — revisão desta execução (2026-07-08)

| Item do checklist | Aplica-se? | Resultado |
|---|---|---|
| Injeção SQL/NoSQL | Sim (`storage.py`, `storage_advanced.py`) | **Revisto, sem achados** — ver abaixo |
| Command Injection / Path Traversal | Sim (export CSV, caminho `.db`) | **Revisto, sem achados** — ver abaixo |
| Broken Authentication/Authorization — WebSocket | Sim (`ble_bridge.py`) | Risco conhecido, já documentado; ver "WS-001" abaixo |
| Broken Authorization / IDOR — API REST | **Sim, desde 2026-07-07/08** (contrariamente ao enunciado) | **API-002, ABERTO** — ver abaixo |
| Mass Assignment | Sim (`MedicationAdherenceIn`, comandos WS que escrevem em `settings`) | **Revisto, sem achados** — ver abaixo |
| Rate limiting em comandos de escrita | Sim (`reset_readings` etc., WebSocket) | **API-001, MITIGADO nesta execução** |
| Rate limiting na API REST (HTTP) | Sim (chave estática, sem throttling) | **API-003, ABERTO** — ver abaixo |
| SSRF/XXE | Procurado | **Sem parsing de URL/XML no backend — não aplicável** |

### API-001 — `reset_readings`/`force_reading`/`set_retention_days` sem limite de taxa (MITIGADO nesta execução)

**Gravidade**: média (disponibilidade/integridade do histórico do
dispositivo, não confidencialidade).

**Vetor concreto**: o WebSocket `ws://localhost:8765` (`bridge/
ble_bridge.py`) não é autenticado — qualquer processo capaz de abrir uma
ligação TCP a esse porto (qualquer software local, ou qualquer origem se
o bridge alguma vez for exposto além de `localhost`) pode enviar
`{"cmd":"reset_readings"}` em loop apertado. Antes desta correção, CADA
mensagem produzia de imediato uma escrita GATT em `dumpCtrlChar`
(`DUMP_CTRL_RESET_READINGS`), que o firmware traduz num
`QspiRingBuffer::format()` — **destrutivo e irreversível**: apaga todos
os registos ainda não exportados no dispositivo. Um script de 5 linhas
(`websocket.send('{"cmd":"reset_readings"}')` em loop) conseguia apagar o
histórico do wearable repetidamente, sem qualquer limite, sem autenticação
nenhuma. O mesmo canal também aceitava `set_retention_days` sem limite
(menos grave — só altera um valor de configuração — mas partilha o mesmo
desenho sem proteção).

**Correção aplicada** (`bridge/ble_bridge.py`):
- Novo `WRITE_COMMAND_MIN_INTERVAL_S = 2.0` + `_check_write_rate_limit()`
  — limite de taxa **global** (não por-cliente WebSocket, de propósito:
  vários separadores do dashboard partilham o mesmo dispositivo BLE
  físico, por isso um limite por-cliente seria trivial de contornar
  abrindo outra ligação).
- Aplicado em `send_command()` (cobre `force_reading` e `reset_readings`,
  cada um com a sua própria janela — contadores independentes por nome
  de comando) e em `handle_dashboard_command()` para `set_retention_days`.
- Uma tentativa rejeitada **não** empurra a janela de limite para a
  frente (só regista o instante em tentativas aceites) — evita que um
  cliente em loop apertado consiga manter o comando bloqueado
  indefinidamente para outros clientes legítimos.
- Resposta ao cliente inalterada na forma (`{"kind":"command_result",
  "cmd":..., "ok":false, "error":"..."}` / `{"kind":
  "retention_days_result", ...}`), só o texto de erro passa a poder ser
  "limite de taxa excedido, aguarde Xs" — confirmado que
  `web/dashboard/index.html` (`handleCommandResult`/
  `handleRetentionDaysSaveResult`) já mostra `msg.error` genericamente,
  sem código dependente do texto exato — **nenhuma alteração de
  dashboard necessária**, contrato preservado.

**Verificação**: `bridge/tests/test_ble_bridge_rate_limit.py` (5 testes
novos, cliente BLE/WebSocket falsos, sem hardware nem rede real) —
primeira chamada aceite, 19 chamadas seguintes em loop todas bloqueadas
com só 1 escrita GATT real registada, comandos diferentes com janelas
independentes, `set_retention_days` bloqueado sem alterar o valor
persistido, e a janela reabre depois de `WRITE_COMMAND_MIN_INTERVAL_S`
(tempo simulado via monkeypatch de `time.monotonic`, sem `sleep` real).
`python -m py_compile bridge/ble_bridge.py` sem erros. Suite completa do
bridge corrida localmente: **58/58 testes passam** (53 já existentes + 5
novos), sem regressões em `test_api.py`/`test_crypto_utils.py`/
`test_storage_advanced.py`. `bridge/requirements_db.txt` ganhou
`pycryptodome` (já estava em `requirements.txt` mas faltava no ficheiro
que a CI usa — `ble_bridge.py` importa `Crypto.Cipher.AES` a nível de
módulo, sem isto os novos testes não seriam sequer importáveis em CI).

**Risco residual**: 2 segundos ainda permite ~30 tentativas de
`reset_readings`/minuto — suficiente para incomodar mas não para um DoS
sustentado; um valor mais agressivo poderia interferir com uso legítimo
(ex.: um cuidador a corrigir um "Repor leituras" acidental rapidamente).
**Não resolve a causa raiz** (canal continua sem autenticação — ver
WS-001 abaixo, decisão de desenho fora do âmbito desta correção pontual).

### WS-001 — WebSocket do bridge sem autenticação (risco conhecido, reavaliado — sem alteração, ver justificação)

Já documentado em `PROJECT_STATUS.md` ("Canal não autenticado — só deve
ser exposto em localhost"). Reavaliação desta rotina, sem implementar
(regra do prompt: introduzir autenticação é decisão do utilizador — só
proponho):

- **Risco real hoje**: baixo-médio. O bridge só ouve em `localhost`
  (`WS_HOST = "localhost"`, `bridge/ble_bridge.py`), por isso um atacante
  precisa de já ter execução de código no mesmo computador — nesse
  cenário já haveria vetores mais diretos (aceder ao `.db`, à variável de
  ambiente da chave AES, etc.). Qualquer processo local (incluindo uma
  aba de browser maliciosa correndo JavaScript, se `localhost:8765`
  aceitar ligações de origem cruzada via WebSocket — **WebSocket não
  aplica Same-Origin Policy**, ao contrário de `fetch`/XHR) pode hoje
  emitir os comandos suportados: `force_reading`, `reset_readings`
  (agora com limite de taxa, ver API-001), `get_history`, `export_csv`,
  `get_daily_trend`, `get_retention_days`/`set_retention_days`.
- **O que muda se sair de localhost**: torna-se crítico de imediato — os
  mesmos comandos ficam acessíveis a qualquer dispositivo na mesma rede
  (ou na Internet, se exposto sem VPN/túnel), incluindo leitura de
  histórico de sensores de saúde (`get_history`/`export_csv`) sem
  qualquer controlo de acesso.
- **Proposta** (não implementada): antes de qualquer exposição além de
  `localhost`, introduzir pelo menos um token partilhado por ligação
  (mesmo padrão já usado em `bridge/api.py`, `X-API-Key`/
  `hmac.compare_digest`) — o WebSocket handshake HTTP suporta headers
  customizados, ou um token no primeiro frame da ligação. Decisão do
  utilizador quando/se este cenário se tornar real.

### API-002 — API REST: uma única chave partilhada, sem âmbito por-utente/paciente (ABERTO)

**Gravidade**: alta (dados de saúde — FC, aderência a medicação — e
agora também uma escrita clínica, expostos por rede a qualquer detentor
da chave, sem qualquer verificação de "este pedido pode ver/alterar
ESTE paciente?"). Diferente de WS-001: **este canal já é pensado para
sair de `localhost`** (é uma API HTTP com `Uvicorn`, propósito
explícito de servir a rede), por isso o risco aqui é o real, não
hipotético.

**Vetor concreto**: `bridge/api.py`, `_require_api_key()` só confirma que
o pedido tem *alguma* chave válida — não existe nenhum conceito de "esta
chave pertence a este clínico/família, autorizada só para o paciente X".
Um utilizador legítimo com a chave (ex.: um familiar com acesso ao
dashboard de um paciente) pode, com a MESMA chave:
- `GET /api/patients/{patient_id}/medication-adherence` para **qualquer**
  `patient_id` sequencial (1, 2, 3, ...) — não só o seu.
- `GET /api/devices/{device_id}/heart-rate-trends` para **qualquer**
  dispositivo — FC de outro utente.
- `POST /api/medications/{medication_id}/adherence` para **qualquer**
  `medication_id` — pode marcar (ou desmarcar) a medicação de outro
  paciente como tomada, um problema de **integridade clínica**, não só
  de confidencialidade.

Isto é exatamente o padrão IDOR do checklist ("um cliente pode pedir
dados de outro?") — a resposta é sim, hoje, para todos os endpoints.

**Por que não corrigido nesta execução**: o ORM (`storage_advanced.py`)
já modela `User.role` (família/clínico/admin) e a associação
paciente↔cuidador (`patient_caregivers`), mas **nenhum endpoint da API
usa isso** — a chave estática não está ligada a nenhum utilizador. Fechar
isto a sério exige desenhar como a API identifica "quem" está a fazer o
pedido (uma chave por-utilizador? JWT com claim de paciente(s)
autorizados? — ver requisitos da API planeada abaixo) — uma decisão de
arquitetura de autenticação, o mesmo tipo de decisão que o prompt desta
rotina pede para propor, não implementar por iniciativa própria.

**Proposta concreta** (não implementada): antes de qualquer exposição
real desta API além de testes locais, migrar de "uma chave global" para
"uma credencial por-utilizador" (mínimo: uma chave de API por linha em
`users`, já que o modelo existe) + verificação em cada endpoint de que o
`patient_id`/`device_id`/`medication_id` pedido pertence a um paciente
que esse utilizador está autorizado a ver (via `patient_caregivers` para
família, ou uma associação clínico↔paciente equivalente para clínicos).
Isto é o mesmo requisito já listado como "JWT + política de autorização
por papel" na secção seguinte — mover para aqui reflete que já não é um
requisito só do futuro, a API que precisa disto já está a correr.

### API-003 — Sem rate limiting nos pedidos HTTP da API REST (ABERTO)

**Gravidade**: média. `bridge/api.py` não tem nenhum limite de tentativas
por IP/cliente — nem para pedidos de autenticação (a chave estática podia
ser atacada por força bruta online, sem qualquer atraso ou bloqueio
depois de N falhas — a comparação em tempo constante impede o ataque de
temporização mas não a força bruta simples), nem para os endpoints de
leitura/escrita já autenticados. Comparar com API-001: o WebSocket já
ganhou um limite de taxa nesta execução; a API HTTP continua sem
equivalente.

**Não implementado nesta execução** (foco desta rotina foi o WebSocket,
ver API-001; adicionar rate limiting à API HTTP exigiria uma dependência
nova — ex. `slowapi` — ou um middleware ASGI escrito de propósito, mais
testes dedicados, âmbito maior do que uma correção contida). Registado
como próximo passo concreto para uma execução futura desta rotina.

### Injeção SQL/NoSQL — revisto, sem achados

- `bridge/storage.py`: todas as queries usam parâmetros posicionais
  (`?`) do `sqlite3` — nenhuma concatenação/f-string na construção de
  SQL (`insert_record`, `get_records_since`, `get_daily_summary`,
  `purge_old_sensor_records`, `get_retention_days`/`set_retention_days`,
  todas confirmadas linha a linha).
- `bridge/storage_advanced.py`: acesso exclusivamente via SQLAlchemy ORM
  (`db.query(...)`, `db.get(...)`, filtros por atributo de modelo) — sem
  nenhuma chamada a `.execute()`/`text()` com SQL construído por
  string/f-string/`.format()` em nenhum ponto do ficheiro (confirmado por
  pesquisa de padrão `execute\(|text\(|f"|% |\.format\(` — só ocorrência é
  `PRAGMA foreign_keys=ON`, uma constante fixa sem input externo).
- `bridge/api.py`: todos os parâmetros de rota (`device_id`, `patient_id`,
  `medication_id`) são tipados `int` pelo FastAPI/Pydantic — um valor
  não numérico é rejeitado antes de chegar à camada de BD (422), nunca
  chega a ser usado numa query.

### Command Injection / Path Traversal — revisto, sem achados

- Nenhum `subprocess`/`os.system`/`eval`/`exec` em `bridge/*.py`
  (confirmado por pesquisa).
- Exportação CSV (`storage.export_records_csv`/`export_emergency_alerts_csv`)
  escreve para um `io.StringIO` em memória — nunca cria/abre um ficheiro
  no disco do servidor a partir de um nome derivado de input do
  utilizador, por isso não há vetor de path traversal aqui.
- `DB_PATH` (`bridge/storage.py`) é uma constante fixa
  (`Path(__file__).parent / "carewear_history.db"`), nunca construída a
  partir de input externo.

### Mass Assignment — revisto, sem achados

- `MedicationAdherenceIn` (`bridge/api.py`) declara exatamente 4 campos
  (`scheduled_datetime`, `taken`, `method`, `notes`); `method` é
  `Literal[...]` restrito a 3 valores. `record_medication_adherence()`
  atribui campo a campo ao modelo ORM (`record.taken = body.taken`,
  etc.) — não usa `**body.dict()`/`setattr` genérico que pudesse
  escrever num campo não pretendido do modelo `MedicationAdherence`
  (ex.: `id`, `medication_id` continuam só sob controlo do servidor).
  `taken_at` é explicitamente **derivado no servidor**
  (`datetime.utcnow()`), nunca aceite do corpo do pedido — já documentado
  em `PROJECT_STATUS.md` como decisão deliberada.
- Comandos WebSocket que escrevem em `settings`
  (`set_retention_days`): só aceita o campo `days`, validado contra
  `MIN_RETENTION_DAYS`/`MAX_RETENTION_DAYS` em `storage.set_retention_days()`
  antes de gravar — sem campos adicionais possíveis (a tabela `settings`
  é genérica chave/valor, mas o comando só expõe esta chave).

### SSRF/XXE — confirmado não aplicável

Nenhum parsing de XML em `bridge/` (sem `xml.etree`/`lxml`/similar).
Nenhum pedido HTTP de saída a partir de input do utilizador (sem
`requests.get(<url do utilizador>)` nem equivalente) — `httpx` só
aparece em `bridge/tests/test_api.py`, como dependência do
`TestClient` do FastAPI, não código de produção.

## Requisitos de segurança da API planeada (checklist vivo)

**Atualizado nesta execução**: como `bridge/api.py` já existe e corre
hoje, vários itens abaixo passaram de "requisito prévio" para "gap
aplicável agora" — movidos para a secção de achados acima (API-002,
API-003) em vez de ficarem só aqui como checklist teórico. Os que
permanecem nesta secção são os que continuam genuinamente sem aplicação
prática ainda (a API não serve HTML nem usa cookies/sessões).

| Item | Estado | Nota |
|---|---|---|
| Autenticação por-utilizador + RBAC (JWT ou equivalente) | **Aplicável agora — ver API-002** | ORM já modela `User.role`/`patient_caregivers`, API não os usa |
| Rate limiting HTTP | **Aplicável agora — ver API-003** | — |
| HTTPS obrigatório + TLS | **Aplicável agora, ainda sem correção** | `uvicorn api:app` corre em HTTP simples nos exemplos documentados (`bridge/README.md`); `X-API-Key` viaja em texto simples se não houver TLS/reverse-proxy à frente. Mitigado hoje só por estar documentado para `127.0.0.1`/uso local — torna-se crítico se exposto além disso. Registado como requisito antes de qualquer deploy real. |
| CORS restritivo | Ainda não configurado, risco baixo por agora | FastAPI sem `CORSMiddleware` = comportamento por omissão (browsers bloqueiam cross-origin sem cabeçalhos `Access-Control-Allow-*`) — seguro por omissão; só passa a precisar de configuração explícita quando o dashboard for ligado a esta API a partir de uma origem diferente (ver "Ainda por fazer" em `PROJECT_STATUS.md`). |
| CSP | Não aplicável ainda | API serve só JSON, nunca HTML — sem superfície de XSS refletido/CSP enquanto isso não mudar. |
| XSS refletido | Não aplicável ainda | Idem — sem endpoints que reflitam input em HTML. |
| CSRF | Mitigado por desenho | Autenticação por cabeçalho customizado (`X-API-Key`), não por cookie/sessão — um browser não anexa este cabeçalho automaticamente a pedidos cross-site, o vetor clássico de CSRF não se aplica enquanto a autenticação continuar assim. Reavaliar se algum dia migrar para cookies de sessão. |
| Session fixation | Não aplicável | Sem sessões/cookies — autenticação stateless por chave em cada pedido. |
| Rotação/revogação de chave de API | **Aplicável agora, ainda sem correção** | Chave única, sem expiração nem forma de revogar sem reiniciar o processo com uma variável de ambiente nova — parte do mesmo trabalho de API-002 (uma vez que existam chaves por-utilizador, a rotação/revogação por linha torna-se natural). |

## Procura contínua

Sugestão de foco para a próxima execução desta rotina: **API-002**
(autorização por-utilizador/RBAC na API REST) é o risco mais grave em
aberto — mas é também o maior em âmbito (decisão de arquitetura de
autenticação), por isso pode valer a pena começar por **API-003** (rate
limiting HTTP, mais contido, ex.: `slowapi` ou um middleware simples de
contagem por IP) como próximo passo prático, deixando API-002 para uma
execução dedicada só a isso (ou para quando o utilizador decidir a
arquitetura de utilizadores/sessões da API). Verificar também no
`git log` se `storage_advanced.py` começou a ser integrado com
`ble_bridge.py`/o streaming real — nesse momento os riscos IDOR listados
aqui deixam de ser só teóricos sobre dados de demonstração/teste e
passam a valer para dados reais de utentes.

# CareWear — Estado de segurança (frontend)

Registo das rotinas de auditoria de segurança do dashboard
(`web/dashboard/index.html`, `web/dashboard/medication-reminders.js`).
Ver também `PROJECT_STATUS.md` para o estado geral do projeto (histórico
completo de bugs de XSS já corrigidos antes deste ficheiro existir: cartão
"Equipa de cuidadores", 5 pontos de nome de medicamento/campo de perfil
sensível, valores `hr`/`spo2`/`steps` do bridge).

## Riscos

### FE-001 — [CORRIGIDO] Stored XSS em `medication-reminders.js` (cartão de lembrete de medicação)

- **Data**: 2026-07-08 (S03 frontend-security)
- **Ficheiro**: `web/dashboard/medication-reminders.js`, método
  `MedicationReminder.showFallbackAlert()`.
- **Vetor**: `medication.name` e `medication.dose` são texto livre,
  introduzido no formulário "Gerir medicação" (Médico/Técnico,
  `addMedicationForPatient()` em `index.html`) e persistido em
  `localStorage` (`carewear_medications_registry`). O cartão de lembrete
  (mostrado quando as notificações do browser não estão disponíveis, ou
  sempre em paralelo com elas) montava um template de string com estes
  valores e escrevia-o via `div.innerHTML = content` **sem qualquer
  escaping** — ao contrário do padrão já usado para o mesmo campo dentro
  de `index.html` (tabela de doses de hoje / "Gerir medicação", ver
  `escapeHtml()`). Um nome de medicamento como
  `<img src=x onerror=...>` executaria a cada dose agendada, em qualquer
  sessão futura no mesmo browser (persistido).
- **Segundo vetor no mesmo ponto**: o campo `time` (também texto livre —
  "Horários" do mesmo formulário, ex. `"08:00, 20:00"`) era concatenado
  dentro de um atributo `onclick` entre aspas simples
  (`markDoseTaken('${patient.id}', '${medication.id}', '${time}')`). Um
  valor com uma aspa simples fugia da string JavaScript do atributo,
  permitindo injetar código independentemente de `name`/`dose` estarem ou
  não escapados para HTML — um vetor de "event injection" distinto do
  XSS em HTML.
- **Correção**: reescrito para construção DOM segura —
  `createElement`/`textContent`/`createTextNode` (nunca interpretam HTML)
  em vez de `innerHTML`, e `addEventListener` com uma closure (valores
  passados como argumentos reais de função, nunca concatenados numa
  string) em vez de `onclick="..."` inline. Elimina os dois vetores em
  simultâneo, sem depender de escaping manual.
- **Verificação**: `node --check` ao ficheiro completo (sintaxe válida) +
  Playwright real (Chromium, `ws://localhost:8765` propositadamente
  indisponível para isolar o teste): `medication.name`/`.dose` com
  `<img onerror=...>` e `<script>...</script>`, `time` com
  `"08:00'); window.__xss2=true; //"` — nenhum payload executou
  (`window.__xss1`/`__xss2` continuam `false`), o cartão mostra o texto
  literal (`&lt;img...`/`<script>` como texto, não como tags no DOM), e
  clicar em "Tomei agora" invoca `markDoseTaken('p1','m1',"08:00'); ...")`
  corretamente como argumento de string, sem executar o payload.
- **Risco residual**: nenhum conhecido neste ponto. Nota: o resto de
  `index.html` continua a usar `onclick="..."` inline nalguns pontos
  (mais o padrão `escapeHtml()` para esses casos) — ver proposta de CSP
  abaixo sobre o trade-off de manter `unsafe-inline`.

### FE-002 — [CORRIGIDO] DOM XSS em `renderRealTrendTable()` (tendência semanal real, dados do bridge)

- **Data**: 2026-07-08 (S03 frontend-security)
- **Ficheiro**: `web/dashboard/index.html`, função
  `renderRealTrendTable()`.
- **Vetor**: `liveState.realTrend` é populado diretamente de
  `msg.days_summary` (mensagem `daily_trend` recebida do bridge via
  `ws://localhost:8765` — canal **não autenticado e sem Same-Origin
  Policy em WebSocket**, ver `PROJECT_STATUS.md`), só validando que é um
  array (`Array.isArray`), sem validar os campos de cada elemento. `d.day`
  e `d.record_count` eram interpolados diretamente num template de string
  escrito com `body.innerHTML = ...`, ao contrário do padrão já aplicado a
  `hr`/`spo2`/`steps` na mesma função `handleBridgeMessage`
  (`toFiniteNumber()`, ver comentário junto à função). Um bridge malicioso
  ou comprometido (ou qualquer outro processo/página capaz de abrir uma
  ligação WebSocket a `localhost:8765`) podia injetar HTML/script na vista
  "Tendência semanal".
- **Correção**: `d.day` passa a ser escapado com `escapeHtml()` (mesma
  função usada nos outros pontos de texto livre do ficheiro);
  `record_count`, `hr_samples`, `avg_hr`, `max_steps`, `min_steps` passam
  todos por `toFiniteNumber()` antes de entrar no template, tal como
  `hr`/`spo2`/`steps` em `applyLiveVitals()`.
- **Verificação**: Playwright real — `liveState.realTrend` preenchido com
  `day: '<img src=x onerror="window.__xss3=true">'` e
  `record_count: '<script>window.__xss3=true</script>'`, seguido de
  `renderRealTrendTable()`. `window.__xss3` continua `false`; o
  `innerHTML` resultante mostra `&lt;img src=x onerror="..."&gt;` como
  texto e `record_count` cai para `0` (não é um número finito) — sem tags
  cruas no DOM.
- **Risco residual**: nenhum conhecido neste ponto. O canal
  `ws://localhost:8765` continua sem autenticação/TLS — aceitável para
  desenvolvimento local, mas a mitigar antes de qualquer exposição além de
  `localhost` (ver proposta abaixo).

## Inventário de sinks perigosos (`innerHTML`/`insertAdjacentHTML`/`document.write`/`outerHTML`)

Auditado nesta rotina (S03, eixo escolhido: "todos os `innerHTML`" do
dashboard). Estado de cada ocorrência atual no código:

| Ficheiro / função | Origem dos dados inseridos | Estado |
|---|---|---|
| `index.html` `populateLangSelect()` — `sel.innerHTML` | `LANG_NAMES` (constante fixa no código) | Seguro — sem entrada externa |
| `index.html` `updateLiveEmergencyBanner()` — `el.innerHTML` | SVG fixo + `meta.label` (tabela `EMERGENCY_ALERT_TYPE_TO_LOG` fixa, chave `msg.alert_name` só seleciona, não injeta valor) + `selectedPatient().name` (roster local) | Seguro |
| `index.html` `renderView()` — `c.innerHTML = TEMPLATES[view]()` | despacho fixo por nome de vista (whitelist `TEMPLATES`) | Seguro — cada template auditado individualmente nesta tabela |
| `index.html` `renderNightSummary()`, `renderPacingSummary()`, `renderActivityDetail()` — `host.innerHTML` | dados sintéticos gerados localmente (`buildNightRestlessness`, `buildPacingTrend`, `buildCategoryWeekly`) + `liveState.pacing` (já validado por `toFiniteNumber()`) | Seguro |
| `index.html` `renderCaregiverNotes()` — `host.innerHTML` | `n.text` (nota do cuidador, texto livre) | Seguro — escapado (`.replace(/</g,'&lt;')`, suficiente em contexto de texto dentro de `<p>`, sem atributos) |
| `index.html` `showTip()` (`ttipEl.innerHTML`) | chamadores usam `d.day`/`d.score`/`d.hr`/`when` de séries simuladas (`buildHr`, `buildPacingTrend`, etc.) ou `liveHrBuffer` (já validado — `hrNum = toFiniteNumber(msg.hr)`, `label = fmtClock(msg.ts)`) | Seguro |
| `index.html` `renderStorageWarningBanner()` — `el.innerHTML` | SVG + texto fixos, só o `flag` (já validado por `toFiniteNumber`) escolhe qual dos dois textos fixos mostrar | Seguro |
| `index.html` `applyLiveVitals()` — `setVal(...).innerHTML` | `liveState.hr`/`spo2`/`steps`/`inactivity`/`freefall` — todos validados (`toFiniteNumber()` ou booleano só usado em ternário de strings fixas) | Seguro — já corrigido em sessão anterior |
| `index.html` `renderRealTrendTable()` — `body.innerHTML` | `msg.days_summary` (bridge, `daily_trend`) | **FE-002 — corrigido nesta rotina** (ver acima) |
| `index.html` `exportClinicalPdf()` — `sheet.innerHTML` | dados do roster local (`p.name`, `p.deviceName`, `p.mac`, `p.lastSync`) + `currentAlerts()`/`currentAnomalyLog()` (dados de demonstração/roster, não formulário de texto livre) | Não auditado a fundo nesta rotina — candidato ao próximo eixo se `currentAlerts()`/`currentAnomalyLog()` alguma vez passarem a incluir texto livre editável |
| `medication-reminders.js` `showFallbackAlert()` — `div.innerHTML` | `medication.name`/`.dose` (texto livre, "Gerir medicação") + `time`/`patient.id`/`medication.id` num atributo `onclick` concatenado | **FE-001 — corrigido nesta rotina** (ver acima); já não usa `innerHTML` nem `onclick` inline |

Não foram encontrados usos de `document.write` ou `outerHTML` em nenhum
dos dois ficheiros.

## Propostas registadas (fora do âmbito desta rotina — sem alteração de código)

Verificado que o eixo "todos os `innerHTML`" está agora são (ambos os
riscos reais encontrados foram corrigidos). Os pontos abaixo ficam
registados como propostas para uma rotina futura dedicada a esse eixo,
conforme "Critérios para terminar sem alterações":

- **CSP**: não existe `Content-Security-Policy` (nem `<meta
  http-equiv="Content-Security-Policy">`, nem cabeçalho — o ficheiro é
  servido estaticamente). Proposta: `default-src 'none'; script-src
  'self'; style-src 'self' 'unsafe-inline'; connect-src ws://localhost:8765
  wss://localhost:8765; img-src 'self' data:; base-uri 'none';
  form-action 'none'`. **Trade-off a decidir**: o ficheiro usa `<script>`
  inline (o próprio código da aplicação, não só handlers) e vários
  `onclick="..."` inline espalhados pelas vistas — uma CSP sem
  `'unsafe-inline'` em `script-src` exigiria mover todo o JS inline para
  ficheiro(s) externo(s) e substituir todos os `onclick` por
  `addEventListener` (o padrão já usado na correção de FE-001). É uma
  refatoração de fundo, não um patch pontual — decisão maior a tomar
  numa rotina própria.
- **Clickjacking**: sem `X-Frame-Options`/`frame-ancestors` (não
  aplicável a um ficheiro aberto localmente sem servidor; relevante se o
  dashboard alguma vez for servido por um servidor real).
- **`ws://` vs `wss://`**: aceitável para `localhost` em desenvolvimento;
  a mitigar (TLS + autenticação do canal) antes de qualquer exposição
  além de `localhost` — já registado em `PROJECT_STATUS.md`.
- **Dados clínicos em `localStorage` em claro**: chaves `carewear_*`
  incluem `carewear_profile`/`carewear_profile_pending` (pode conter
  NIF/morada pendentes de aprovação), `carewear_caregiver_notes`,
  `carewear_medications_registry`, `carewear_medication_log`,
  `carewear_consent`, `carewear_caregiver_team`, `carewear_alert_*`,
  `carewear_selected_patient_id`. Tudo em claro, sem expiração, acessível
  a qualquer script no mesmo origin e a qualquer pessoa com acesso físico
  ao browser (dispositivo partilhado, ex. tablet na sala do
  utente/família). Proposta para rotina dedicada: minimizar o que fica
  persistido (ex. não persistir NIF/morada pendente além da sessão de
  aprovação), ou pelo menos documentar isto como risco aceite de
  protótipo sem backend de autenticação real.
- **Cookies**: não são usados (login é só de protótipo, sem sessão de
  servidor) — sem flags a rever.
- **Dependências**: confirmado que o dashboard é mesmo vanilla — nenhum
  `<script src="http...">`/CDN, nenhum `<link>` externo; o único
  `<script src="...">` é local (`medication-reminders.js`). Sem
  superfície de dependências de terceiros a auditar.

## Verificação (âmbito desta rotina)

- `node --check` ao `<script>` inline extraído de `index.html` (linhas
  849–4516) e a `medication-reminders.js` completo: sintaxe válida em
  ambos.
- Playwright real (Chromium, `/opt/pw-browsers/chromium`), servido via
  `python3 -m http.server` a partir de `web/dashboard/`: os dois vetores
  (FE-001, FE-002) confirmados exploráveis antes da correção (payload
  executava/HTML cru aparecia no DOM) e confirmados neutralizados depois
  — ver detalhe em cada risco acima. Consola sem erros de script (só o
  aviso esperado de falha de ligação a `ws://localhost:8765`, sem bridge
  a correr no ambiente de teste).
- Regra de ouro respeitada: nenhuma alteração tocou em rótulos de dados
  simulados vs. reais.

---

# CareWear — Vigilância Externa de Segurança (S09)

> Ficheiro/secção da rotina de **Investigação de Segurança — vigilância
> externa** (S09), branch `seguranca/security-research`. Distinta de
> todas as outras rotinas de segurança (NFC/S05, backend-api, firmware,
> frontend, dependências, BLE, privacidade/GDPR — cada uma com o seu
> `SECURITY_STATUS.md` em branch própria): esta rotina **não audita
> código do CareWear diretamente**. Vigia o panorama de ameaças e boas
> práticas externas (CVEs, novos ataques BLE/NFC/TinyML, frameworks
> OWASP/NIST/ENISA/MITRE, lado de vigilância de normas de dispositivo
> médico) e pergunta, todos os dias: "existe alguma recomendação que o
> CareWear ainda não implementa?". Quando uma lacuna cai no domínio de
> outra rotina de segurança já existente, é encaminhada para lá (sem
> duplicar) — só fica registada aqui a ligação entre a fonte externa e o
> risco. Ver `SECURITY_RESEARCH.md` para o compêndio de pesquisa

## Nota de manutenção (2026-07-10, fora do âmbito habitual desta rotina)

Ao atualizar esta branch com `origin/main` (necessário porque o
`SECURITY_STATUS.md` de `main` avançou entretanto com o merge das
rotinas backend-api-security e frontend-security), detetou-se que a
versão em `main` continha **marcadores de conflito de merge do git por
resolver, comitados literalmente no ficheiro** (`=======`, e uma linha solta com um fragmento de mensagem
de commit no fim do ficheiro) — introduzidos pelo commit `0b63462`
(mensagem de commit também anómala, é o próprio texto do achado, não
uma mensagem normal), entre as secções "Backend/API" e "frontend".
Corrigido nesta fusão: as 3 linhas de marcador e a linha solta foram
removidas, mantendo todo o conteúdo real de ambas as secções (nada foi
apagado, só o ruído do git). Não é um achado `RES-XXX` (não é uma
recomendação de segurança externa, é uma correção de higiene do próprio
repositório) — registado aqui só para rasto, já que esta rotina não
costuma tocar em conteúdo de outras secções. Recomenda-se à próxima
rotina que fizer merge para `main` confirmar visualmente o resultado
antes de comitar, em vez de assumir que a resolução automática do git
ficou completa.
> completo, organizado por tema, com todas as fontes.

## Como ler esta secção

Cada risco/recomendação tem um ID `RES-XXX` estável (não reutilizar
números). É **vigilância**, não uma auditoria de código nesta rotina —
por isso todo achado aqui é uma **recomendação encaminhada**, nunca uma
correção aplicada por esta rotina (ver regras do prompt: "não mexe no
código"). Estados: **NOVO** (recomendação desta execução, ainda não
avaliada pela rotina de destino), **REFORÇO** (não é uma lacuna nova —
uma fonte externa nova aumenta a confiança/prioridade de um risco já
registado noutra rotina), **ENCAMINHADO** (já passado à rotina/entidade
responsável, a aguardar decisão), **SEM AÇÃO** (avaliado e considerado
não aplicável ou já coberto, com justificação).

## Alvos pesquisados nesta execução (2026-07-10)

Primeira execução desta rotina (sem histórico anterior a repetir).
Rotação escolhida para hoje: **(1) novos ataques BLE** (famílias
BLUFFS/BLESA e sucessoras), **(2) CVEs recentes com aplicação direta ao
stack do bridge Python**, **(3) frameworks/normas — NIST IoT**. Antes de
registar qualquer achado, cada branch de segurança já existente
(`seguranca/ble-security`, `seguranca/nfc-security`,
`seguranca/backend-api-security`, `seguranca/dependency-security`) foi
consultada via `git show origin/<branch>:SECURITY_STATUS.md` para não
duplicar o que já lá está registado — dois achados de hoje (CVEs do
pacote `cryptography`) já estavam cobertos por `DEP-001`
(`seguranca/dependency-security`, avaliado como não explorável no uso
atual do CareWear) e não foram re-registados aqui, só referenciados.
**Fila para próximas execuções** (não cobertos hoje): ataques NFC além
de relay (já coberto por `seguranca/nfc-security`, NFC-006), OWASP API
Security Top 10 detalhado, OWASP Mobile Top 10 (app futura), ENISA
guidelines IoT/saúde, MITRE ATT&CK for ICS/embedded, IEC 62443/81001-5-1
(lado vigilância), ataques a TinyML além de model extraction (data
poisoning, adversarial examples — relevante quando/se `ml/` for
embarcado no firmware).

## Achados desta execução

### RES-001 — Emparelhamento BLE silencioso/forçado: evidência real de exploração em massa (2025-2026), reforça FW-002 e NFC-002

**Estado: REFORÇO** (não é uma lacuna nova — reforça prioridade de
riscos já registados).
**Fontes**: [Rescana — "WhisperPair Bluetooth Fast Pair Vulnerability
(CVE-2025-36911)"](https://www.rescana.com/post/whisperpair-bluetooth-fast-pair-vulnerability-cve-2025-36911-exposes-millions-of-audio-accessories);
[Malwarebytes — "WhisperPair exposes Bluetooth earbuds and headphones to
tracking and eavesdropping" (jan. 2026)](https://www.malwarebytes.com/blog/news/2026/01/whisperpair-exposes-bluetooth-earbuds-and-headphones-to-tracking-and-eavesdropping);
[BleepingComputer — "Critical WhisperPair flaw lets hackers track,
eavesdrop via Bluetooth audio devices"](https://www.bleepingcomputer.com/news/security/critical-whisperpair-flaw-lets-hackers-track-eavesdrop-via-bluetooth-audio-devices/);
CVE-2026-0097 (Android, elevação de privilégio no emparelhamento LE,
CVSS 3.1 = 8.0, corrigido no boletim de segurança Android 2026-06-01) —
[CVE Record](https://www.cve.org/CVERecord?id=CVE-2026-0097),
[DailyCVE](https://dailycve.com/android-bluetooth-logic-error-bypass-cve-2026-0097-critical-dc-jun2026-147/).
**Grau de confiança**: alto para WhisperPair (múltiplas fontes
jornalísticas de segurança independentes, PoC documentado, CVE atribuído);
médio-alto para CVE-2026-0097 (registo CVE oficial + reportagem técnica,
mas não li o boletim oficial Android linha a linha).
**O que é**: WhisperPair (CVE-2025-36911) explora o protocolo Google
Fast Pair para forçar emparelhamento com acessórios Bluetooth (Sony,
Jabra, JBL, Xiaomi, Google, etc.) **sem qualquer interação do
utilizador**, a até 14 metros, em ~10 segundos — dando acesso a
microfone/controlos e, nalguns casos, à rede "Find Hub" da Google para
seguir a localização do dispositivo indefinidamente. CVE-2026-0097 é
distinto (falha na stack Android, não no CareWear), mas do mesmo tema:
um pedido `ll_enc_req` fora de sequência engana a máquina de estados
`smp_command_processor` para saltar para "emparelhado" sem confirmação
do utilizador.
**Aplicabilidade ao CareWear**: o CareWear não usa Fast Pair nem é um
acessório de áudio — esta fonte não é uma CVE do próprio CareWear. Mas
é evidência concreta, à escala, de 2025-2026, de que "emparelhamento
BLE sem confirmação visível no dispositivo" deixou de ser um risco
teórico de checklist — está a ser ativamente explorado em produtos reais
para vigilância de localização e escuta, exatamente o tipo de dano mais
sensível para um wearable médico usado por pessoas com demência. Isto
**reforça diretamente** dois riscos já registados por outras rotinas,
sem os alterar:
- `FW-002` (`seguranca/ble-security`/`SECURITY_STATUS.md`): nenhuma
  characteristic GATT do CareWear exige hoje pairing/bonding.
- `NFC-002` (secção NFC acima, `seguranca/nfc-security`): handover
  NFC→BLE sem confirmação explícita no dispositivo, ainda em fase de
  requisito (não implementado).
**Encaminhamento**: `seguranca/ble-security` (prioridade de FW-002) e
`seguranca/nfc-security`/`rotina/nfc-development` (prioridade de
NFC-002) — recomenda-se citar esta fonte ao priorizar essas correntes de
trabalho, não uma ação nova desta rotina.

### RES-002 — Validação incompleta de origem em WebSocket (CVE-2026-21883, Bokeh): confirmação externa da classe de vulnerabilidade já registada em WS-001

**Estado: REFORÇO.**
**Fonte**: CVE-2026-21883 — [SentinelOne, "Bokeh Python Library CSRF
Vulnerability"](https://www.sentinelone.com/vulnerability-database/cve-2026-21883/)
(via GitHub Advisory Database, pesquisa aplicada 2026-07-10). **Grau de
confiança**: médio — resumo obtido por pesquisa/agregador, não a
advisory original lida linha a linha.
**O que é**: falha de validação incompleta de `Origin` no handshake
WebSocket da biblioteca Bokeh, permitindo contornar allowlists de
origem e sequestrar ligações WebSocket a partir de uma página maliciosa
(classe "Cross-Site WebSocket Hijacking", CSWSH) — o mesmo princípio de
"WebSocket não aplica Same-Origin Policy por omissão, ao contrário de
`fetch`/XHR" já documentado no CareWear.
**Aplicabilidade ao CareWear**: o CareWear não usa Bokeh — não é uma CVE
do próprio projeto. Mas confirma-se aqui, com um CVE de 2026 atribuído a
outro produto real, exatamente a mesma classe de falha já identificada e
registada por `seguranca/backend-api-security` como `WS-001`:
`bridge/ble_bridge.py` chama `websockets.serve(bridge.ws_handler,
WS_HOST, WS_PORT)` sem restringir `origins=`, por isso qualquer página
aberta no mesmo browser (não limitada por Same-Origin Policy em
WebSocket) pode abrir `new WebSocket('ws://localhost:8765')` e emitir
comandos (`force_reading`, `reset_readings` — já com limite de taxa,
`get_history`, `export_csv`, etc.), mesmo com `WS_HOST` fixo em
`localhost`. `WS-001` já está documentado como risco conhecido, avaliado
e propositadamente não corrigido nesta fase (decisão do utilizador
pendente, ver justificação nesse ficheiro) — não é uma lacuna nova.
**Encaminhamento**: `seguranca/backend-api-security` — citar esta fonte
como reforço de prioridade para `WS-001` (evidência de que a mesma
classe de falha, sem `origins=` explícito num servidor WebSocket, está a
gerar CVEs reais noutros produtos em 2026, não é uma preocupação
puramente teórica).

### RES-003 — NIST IR 8259 Revisão 1 (abril 2026): novo requisito de comunicação de ciclo de vida/EOL ao cliente — lacuna real, sem correspondência em nenhuma rotina existente

**Estado: NOVO.**
**Fonte**: [NIST — "NIST IR 8259 Revision 1, Foundational Cybersecurity
Activities for IoT Product Manufacturers" (publicado 2026-04-20)](https://csrc.nist.gov/pubs/ir/8425/final)
(página do projeto NIST Cybersecurity for IoT, pesquisa aplicada
2026-07-10 — nota: não consegui obter o texto completo da revisão em si
via fetch direto, HTTP 403; achado baseado no resumo da página do
programa NIST e cobertura relacionada, a confirmar com leitura completa
numa próxima execução). **Grau de confiança**: médio (fonte primária
NIST confirmada a existir e a data de publicação, mas resumo do conteúdo
via fonte secundária).
**O que é**: a revisão amplia o âmbito do NIST IR 8259 original de
"atividades pré-mercado" para cobrir **todo o ciclo de vida** do
produto IoT, incluindo comunicação ao cliente sobre manutenção, suporte
e fim de vida (EOL).
**Aplicabilidade ao CareWear**: verificado — **não existe hoje** nenhuma
secção em `PROJECT_STATUS.md`/`SECURITY_STATUS.md` (nenhuma branch de
segurança consultada, incluindo `seguranca/dependency-security`, que é a
mais próxima em tema) que documente (a) um inventário de materiais de
software (SBOM) do firmware/bridge, ou (b) uma política de fim de
vida/suporte comunicada a quem usa o dispositivo (ex.: até quando
correções de segurança continuam a ser lançadas, o que acontece aos
dados guardados no dispositivo/bridge se o projeto for descontinuado).
Isto é relevante em particular pela natureza do CareWear (protótipo de
investigação, wearable médico para pessoas vulneráveis) — um utilizador
real precisaria de saber estes limites antes de confiar dados de saúde
ao dispositivo a longo prazo. **Não é decisão desta rotina** (é uma
decisão de produto/comunicação do responsável pelo tratamento, não uma
correção de código).
**Recomendação/lacuna**: (1) considerar gerar um SBOM básico (ex.:
`pip-audit`/`cyclonedx-py` sobre os `requirements*.txt`, já que
`seguranca/dependency-security` já tem `pip-audit` na CI — gerar SBOM a
partir da mesma infraestrutura seria uma extensão pequena, não uma
rotina nova); (2) documentar, mesmo que informalmente por ser um
protótipo, uma política curta de suporte/EOL no `README.md` ou
`PROJECT_STATUS.md`.
**Encaminhamento**: `seguranca/dependency-security` (para a parte SBOM,
por já ter a infraestrutura `pip-audit`/CI mais próxima) e ao
utilizador/responsável pelo tratamento (para a decisão de política de
EOL/suporte, fora do âmbito de qualquer rotina de segurança).

## Recomendações pesquisadas mas sem achado aplicável hoje

- **OWASP IoT Top 10**: pesquisa não encontrou uma revisão 2026 da lista
  (a versão de referência continua a de 2018; há confusão em fontes
  secundárias com o "OWASP Top 10:2025" genérico, que é da aplicação
  web, não IoT — não confundir numa próxima execução). Sem atualização
  de framework para comparar hoje; o checklist OWASP IoT Top 10 2018
  já não coberto por esta execução fica na fila.
- **Nordic nRF52840 / SoftDevice S140**: pesquisa dedicada não encontrou
  nenhum CVE novo específico ao periférico BLE ou ao SoftDevice S140 em
  2026 (mesma conclusão já registada por `seguranca/nfc-security` em
  2026-07-08 para o periférico NFCT — o hardware Nordic usado por este
  projeto não aparece em avisos recentes). Repetir esta pesquisa
  periodicamente, não descartar de vez.
- **TinyML/model extraction**: pesquisa geral (arXiv, surveys 2024-2025)
  confirma que TinyML em dispositivos fisicamente acessíveis (como um
  wearable) é uma superfície real para extração de modelo e ataques
  adversariais/side-channel — mas **não aplicável hoje**: `ml/` treina
  XGBoost/LSTM/Random Forest offline (`ml/requirements.txt`), sem
  nenhum modelo embarcado no firmware nRF52840 ainda (confirmado por
  `get_skeleton`/leitura do roadmap em `PROJECT_STATUS.md` — TinyML
  embarcado é trabalho futuro, `emlearn` já está em `requirements.txt`
  mas sem uso confirmado no firmware). Registado como alvo a reativar
  assim que um modelo for de facto embarcado (ver "Procura contínua").
- **CVEs `cryptography` (pacote Python)**: as mesmas 4 advisories
  encontradas nesta pesquisa (`GHSA-p423-j2cm-9vmq`,
  `GHSA-537c-gmf6-5ccf`, e duas adicionais) já estão registadas e
  avaliadas em detalhe por `seguranca/dependency-security` como
  `DEP-001` — confirmado via `git show
  origin/seguranca/dependency-security:SECURITY_STATUS.md` antes de
  escrever esta secção, para não duplicar. Sem achado novo aqui.
- **Stealtooth ("Breaking Bluetooth Security Abusing Silent Automatic
  Pairing", arXiv 2507.00847)**: título sugere relevância direta a
  FW-002/NFC-002 (mesma classe de RES-001), mas o `WebFetch` ao PDF e à
  página de resumo devolveu HTTP 403 — **não consegui ler o conteúdo
  real do artigo nesta execução**, só o título nos resultados de
  pesquisa. Por regra desta rotina ("sem inventar ataques"), não
  registo isto como achado com fonte verificada — fica pendente,
  prioridade para a próxima execução (tentar via `arxiv.org/abs/` sem
  `.pdf`, ou um agregador que cite o conteúdo).

## Fila rotativa de alvos (para a próxima execução desta rotina)

| Alvo | Última passagem | Ângulo por cobrir |
|---|---|---|
| Novos ataques BLE (BLUFFS/BLESA/sucessoras) | 2026-07-10 | Ler Stealtooth por inteiro (bloqueado por 403 hoje); repetir busca por CVEs novos do SoftDevice S140 |
| CVEs stack Python do bridge | 2026-07-10 | `bleak`, `websockets`, `sqlalchemy`, `fastapi`, `uvicorn`, `argon2-cffi` — sem achado novo hoje além do já coberto por DEP-001; tentar fontes GHSA diretas em vez de agregadores na próxima vez |
| NIST IoT (8259/8425) | 2026-07-10 | Ler o texto completo do IR 8259 Rev.1 (bloqueado por 403 hoje) — confirmar detalhe dos novos requisitos além do resumo |
| OWASP IoT Top 10 | Não coberto | Comparar checklist completo 2018 item a item com o CareWear |
| OWASP API Security Top 10 | Não coberto | Focar em `bridge/api.py` |
| OWASP Mobile Top 10 | Não coberto | Só relevante quando a app móvel avançar (roadmap) |
| ENISA (guidelines IoT/saúde) | Não coberto | — |
| MITRE ATT&CK (ICS/embedded) | Não coberto | — |
| IEC 62443/81001-5-1/62304, ISO 14971, MDR, FDA (lado vigilância) | Não coberto | Só a vertente de vigilância externa — análise de conformidade aprofundada é de outra rotina |
| Ataques NFC além de relay | Coberto por `seguranca/nfc-security` (NFC-006, 2026-07-08) | Repetir quando a fase C do NFC avançar |
| Ataques a TinyML (poisoning, adversarial) | Não coberto | Reativar quando um modelo for embarcado no firmware (ver roadmap `ml/`) |

(segurança(frontend): corrige 2 XSS reais em innerHTML não auditados (bridge + lembretes de medicação))

---

# CareWear — Segurança de Dependências (S07)

> Ficheiro da rotina de **Segurança de Dependências**, distinta das
> rotinas de Segurança NFC/Privacidade acima (mesmo ficheiro,
> `SECURITY_STATUS.md`, secções concatenadas — ver nota no topo deste
> documento sobre múltiplos `SECURITY_STATUS.md` por domínio). Esta
> rotina cruza `platformio.ini`/`bridge/requirements*.txt`/
> `ml/requirements.txt` com advisories públicos (GitHub Advisory
> Database, OSV, NVD, changelogs de fornecedor). **Regra central: nunca
> atualiza versões — só regista e propõe.** A decisão/teste de qualquer
> atualização cabe às rotinas de desenvolvimento/utilizador.

## Metodologia desta execução (2026-07-10)

1. Nenhuma dependência do projeto está fixada a uma versão exata — todos
   os `requirements*.txt` usam `>=` (mínimo aberto) e `ml/requirements.txt`
   nem isso (sem qualquer restrição de versão); `platformio.ini` também
   não fixa versão em nenhuma `lib_deps`. Na prática, "a versão em uso"
   é "o que o `pip`/PlatformIO resolver como mais recente no momento do
   build" — ver DEP-005 abaixo, este facto é em si um achado.
2. **Evidência direta mais forte**: o workflow `security.yml` já corre
   `pip-audit` sobre os 3 ficheiros de requirements Python em cada push/PR
   e semanalmente (seg. 03:00 UTC). A execução mais recente sobre `main`
   (run `29065443827`, commit `dcf380f`, **hoje 2026-07-10T02:48 UTC**,
   ~1h30 antes desta rotina) devolveu **"No known vulnerabilities
   found"** para as três matrizes (`bridge/requirements.txt`,
   `bridge/requirements_db.txt`, `ml/requirements.txt`) — confirmado
   lendo os logs dos jobs diretamente via API do GitHub, não apenas o
   estado "success" do workflow. Isto cobre as versões realmente
   resolvidas nesse momento contra a base de dados OSV/PyPI.
3. Pesquisa adicional (WebSearch/WebFetch, dois agentes em paralelo:
   Python/pip e Firmware/Nordic) para cobrir advisories que o
   `pip-audit` de hoje não apanha por definição — versões mínimas
   permitidas pelo `>=` mas já ultrapassadas por CVEs antigas, bibliotecas
   Arduino/C++ (sem equivalente a `pip-audit`), e estado do Dependabot.
4. Para cada achado, verificado se o caminho vulnerável é
   **realmente exercitado** pelo código do CareWear (não só "o pacote
   está na lista") — ex.: `bridge/crypto_utils.py` só usa
   `cryptography.hazmat.primitives.ciphers.aead.AESGCM` (confirmado por
   leitura direta), não curvas elípticas nem `Hash.update()` com buffers
   não contíguos, o que desqualifica 2 das 4 CVEs de `cryptography`
   encontradas como não aplicáveis ao uso real, mesmo que a versão
   mínima declarada as permitisse.
5. **Dependabot**: `.github/dependabot.yml` não existe no repositório
   (confirmado, `find .github -iname "dependabot*"` sem resultados) e
   este MCP não expõe uma ferramenta de leitura de "Dependabot alerts"
   nativos do GitHub (procurado, não encontrado) — não foi possível
   confirmar/negar se os alertas nativos (que não dependem de ficheiro,
   só de um interruptor em Settings → Security) estão ligados. Ficheiro
   `dependabot.yml` proposto nesta execução abaixo (só deteção, ver
   nota).

## Tabela de dependências vulneráveis (DEP-XXX)

| ID | Componente | Versão em uso | CVE/GHSA | Gravidade | Versão-alvo recomendada | Exploitável no nosso contexto? | Estado da proposta |
|---|---|---|---|---|---|---|---|
| DEP-001 | `cryptography` (bridge/requirements_db.txt, `cryptography>=41.0.0`) | Piso `>=41.0.0`, sem teto — resolve para a mais recente (~49.0.0) num `pip install` normal; `pip-audit` de hoje não acusou nada porque a versão realmente instalada em CI já é recente | [GHSA-r6ph-v2qm-q3c2](https://github.com/pyca/cryptography/security/advisories/GHSA-r6ph-v2qm-q3c2) (curvas SECT, validação de subgrupo em falta, ≤46.0.4); [GHSA-p423-j2cm-9vmq](https://github.com/pyca/cryptography/security/advisories/GHSA-p423-j2cm-9vmq) (buffer overflow em `Hash.update()` com buffers não contíguos, ≥45.0.0 \<46.0.7); [GHSA-537c-gmf6-5ccf](https://github.com/pyca/cryptography/security/advisories/GHSA-537c-gmf6-5ccf) (OpenSSL vulnerável embutido nos wheels, \<48.0.1); [GHSA-m959-cc7f-wv43](https://github.com/pyca/cryptography/security/advisories/GHSA-m959-cc7f-wv43) (bypass de name-constraint X.509, ≤46.0.5) | Alta (CVSS alto nas 2 primeiras) mas **mitigada no nosso uso** | `cryptography>=48.0.1` (cobre as 4) | **Não** hoje — `bridge/crypto_utils.py` só usa `AESGCM` (confirmado por leitura direta), não curvas elípticas nem `Hash.update()` manual; risco só se o piso `>=41.0.0` alguma vez resolver para uma versão antiga real (lockfile futuro, mirror interno, ambiente offline) | **Proposto** — subir o piso mínimo declarado para reduzir a janela, sem alterar o ficheiro nesta execução (fora do âmbito) |
| DEP-002 | `rweather/Crypto` (platformio.ini, sem versão fixada; usa-se só a classe `AES` — `AES.h` — para AES-CTR do modo de dados, ver `src/Ble/Ble.cpp`) | Última disponível (~0.4.0), não fixada | [GHSA-gq7v-jr8c-mfr7](https://github.com/meshtastic/firmware/security/advisories/GHSA-gq7v-jr8c-mfr7) — CVE-2025-52464, crítico (CVSS 9.5) no firmware Meshtastic: a classe de RNG desta biblioteca não tinha entropia suficiente nalgumas plataformas, produzindo chaves fracas/duplicadas | Crítica no contexto Meshtastic; **baixa** no nosso | Sem "versão corrigida" formal (o bug era do lado do consumidor, não da biblioteca) — considerar migrar para o acelerador de hardware AES-128 ECB/CCM já disponível no nRF52840, opção já referida em comentário de `Ble.cpp` mas não implementada | **Não** — confirmado por leitura direta de `Ble.cpp`/`Storage.cpp`: a chave AES é gerada fora do dispositivo (na app) e só escrita via `aesKeyChar`; o CareWear não usa a classe RNG de `rweather/Crypto` para gerar chaves no dispositivo. Risco residual: biblioteca com baixa atividade de manutenção (sem GitHub Releases formais), pode não receber correções rápidas se surgir uma falha real na cifra AES em si | Registado como risco de manutenção a monitorizar, não uma vulnerabilidade ativa |
| DEP-003 | Nordic nRF5 SDK / SoftDevice S140 (via `Seeed-Studio/platform-seeedboards`, sem versão fixada) — pairing LE Secure Connections | Desconhecida (core third-party, versão exata não confirmável sem acesso ao pacote resolvido) | Nordic whitepaper [nWP-031 "Security Threat in Bluetooth LESC Pairing"](https://infocenter.nordicsemi.com/topic/nwp_031/WP/nwp_031/vulnerabilities.html) — CVE-2018-5383/CERT VU#304725: implementações de exemplo/software do nRF5 SDK anteriores à v15.0.0 não validavam a chave pública ECDH remota ("invalid curve attack") | Média-alta **condicional** | SDK ≥15.0.0 com módulos `ble_lesc` + `nrf_crypto` (não uma troca de versão simples — é uma escolha de API no código de pairing) | **Não aplicável hoje** — confirmado (`grep` por `pairing`/`bonding`/`ble_lesc`/`nrf_crypto` no código): nenhuma characteristic GATT usa `SECMODE` com pairing/bonding real, todas usam `SECMODE_OPEN` (já registado como `FW-002` no `SECURITY_STATUS.md` de firmware, PR #3) — logo o LESC pairing nem chegou a ser exercitado. **Mas torna-se diretamente relevante no dia em que `FW-002` for corrigido**: quem implementar bonding/pairing tem de confirmar que a plataforma Seeed-Studio usa os módulos modernos (`ble_lesc`/`nrf_crypto`), não um exemplo pré-v15 desatualizado | Registado como pré-requisito de segurança para a futura correção de `FW-002`, não uma ação isolada desta rotina |
| DEP-004 | `tensorflow-cpu` (ml/requirements.txt, **sem qualquer restrição de versão**) | Resolve para a mais recente (~2.21.0) num `pip install` normal | [CVE-2025-55559](https://nvd.nist.gov/vuln/detail/cve-2025-55559) — DoS via `Conv2D(padding='valid')` sob compilação XLA, CVSS 7.5, confirmado na TF 2.18.0; versão corrigida não confirmada nas fontes consultadas | Alta CVSS, mas **baixo impacto real** | Adicionar piso explícito (ex.: `tensorflow-cpu>=2.19.0`, a confirmar contra changelog oficial antes de propor ao ficheiro) | **Baixo** — só afeta o pipeline de treino local (dados/modelos próprios, não carregamento de modelos não confiáveis) e exige compilação XLA; pior caso é o treino falhar/travar, não execução de código arbitrário | Proposto reforçar o piso mínimo (hoje inexistente) |
| DEP-005 | **Todas as dependências do projeto** (`platformio.ini` `lib_deps`, `ml/requirements.txt` por inteiro, e os pisos abertos `>=` em `bridge/requirements*.txt`) | N/A (é a ausência de fixação que é o achado) | N/A — risco estrutural, não uma CVE | Média (risco de cadeia de fornecimento/reprodutibilidade) | Fixar versões exatas (ou pelo menos pisos que excluam ranges já conhecidos como vulneráveis, ver DEP-001/004) em todos os manifestos, incluindo `platformio.ini` que hoje não fixa nenhuma biblioteca | **Sim, estruturalmente** — um build de hoje e um build de amanhã podem instalar bibliotecas diferentes sem qualquer alteração de código; o próprio projeto já documenta este risco na prática (nota em `platformio.ini`/`PROJECT_STATUS.md` sobre o RadioLib ter mudado a API de `begin()` entre versões) — o mesmo mecanismo que já causou uma quebra de build pode um dia introduzir silenciosamente uma versão vulnerável | Proposto fixar versões em todos os manifestos — decisão/execução das rotinas de desenvolvimento (fora do que esta rotina pode alterar) |

## Pacotes verificados sem achado (2026-07-10)

Sem CVE/advisory publicado encontrado no período pesquisado (~18 meses)
para: `sqlalchemy` (2.0.51), `pydantic` (2.13.4, core — não confundir com
`pydantic-settings`, que tem GHSA-4xgf-cpjx-pc3j mas não é dependência
deste projeto), `fastapi` (0.139.0), `twilio` (9.10.9, página de
advisories do `twilio-python` confirma "There aren't any published
security advisories"), `bleak` (3.0.2), `websockets` (16.0, só CVEs
antigas já corrigidas há anos), `pycryptodome` (3.23.0, última CVE
conhecida é anterior a 2025 e já corrigida), `argon2-cffi` (25.1.0),
`alembic` (1.18.5), `psycopg2-binary` (2.9.12 — nota: PostgreSQL
*servidor*, não o driver, teve CVE-2025-1094 SQLi via `COPY TO PROGRAM`,
CVSS 8.1, corrigido no servidor 17.3/16.7/15.11/14.16/13.19 — relevante
só se/quando `DATABASE_URL` apontar para um servidor PostgreSQL real por
atualizar), `numpy`/`pandas`/`scikit-learn`/`xgboost`/`matplotlib`/
`joblib`/`emlearn` (sem CVE atual nas bibliotecas em si — o padrão de
risco conhecido é desserialização insegura de pickle/joblib de ficheiros
não confiáveis, não aplicável porque o pipeline ML do CareWear só carrega
os seus próprios modelos/dados). Bibliotecas de firmware sem achado:
`adafruit/Adafruit SPIFlash`, `seeed-studio/Seeed Arduino LSM6DS3`,
`sparkfun/SparkFun u-blox GNSS Arduino Library`, `sparkfun/SparkFun
MAX3010x Pulse and Proximity Sensor Library`, `adafruit/Adafruit SSD1351
library`, `adafruit/Adafruit GFX Library`, `jgromes/RadioLib` (página de
advisories do próprio repositório confirma "There aren't any published
security advisories" — risco conhecido do projeto é só quebra de API
entre versões, já documentado, não segurança).

## JavaScript/npm

Confirmado (`find` por `package.json`/`package-lock.json`/lockfiles no
repositório inteiro, sem resultados): o dashboard (`web/dashboard/`) e
`medication-reminders.js` são JavaScript vanilla, sem dependências npm
nem scripts de CDN externos identificados. **Sem superfície de dependências
JS a auditar nesta execução.** Reconfirmar em execuções futuras caso
alguma dependência seja introduzida.

## Proposta de infraestrutura de deteção: `.github/dependabot.yml`

Adicionado nesta execução (não altera nenhuma versão — só configura
deteção contínua, consistente com o que esta rotina pode fazer):
`updates:` para os ecossistemas `pip` (`/bridge` e `/ml`, os dois
diretórios com `requirements*.txt`) e `github-actions` (`/`, os
workflows em `.github/workflows/`), com `open-pull-requests-limit: 0`
em todas as entradas — isto é deliberado: desativa os PRs automáticos de
"version updates" do Dependabot (que, por omissão, propõem subir
versões), mantendo apenas a deteção/registo de dependências
desatualizadas visível no separador Insights → Dependency graph.

**Limitação importante a registar**: os **Dependabot alerts** (alertas
de segurança automáticos, a funcionalidade que esta rotina mais precisa)
são um interruptor em `Settings → Security → Dependabot alerts` do
GitHub, **independente deste ficheiro** — não podem ser ativados por
commit/PR, só por alguém com permissão de administração no repositório
através da interface web. Este `dependabot.yml` complementa mas não
substitui esse passo manual — recomenda-se ao utilizador confirmar esse
interruptor está ligado.

## Prioridade (gravidade × exploitabilidade, para as rotinas de dev)

1. **DEP-005** (estrutural, fixar versões) — não é uma CVE mas é o que
   mais reduz a incerteza de todos os outros achados desta e de futuras
   execuções; sem isto, "versão em uso" continua a ser uma suposição.
2. **DEP-001** (`cryptography`, subir piso para `>=48.0.1`) — protege a
   única cifra de PII em repouso do projeto; risco real baixo hoje mas
   correção barata (só um número no requirements).
3. **DEP-003** (LESC pairing) — sem ação own própria agora (bonding nem
   está implementado), mas registar como pré-requisito da correção de
   `FW-002` para não repetir o erro de 2018 num SDK desatualizado.
4. **DEP-004** (`tensorflow-cpu`, adicionar piso mínimo) — impacto
   limitado ao treino local, mas corrige também o DEP-005 estrutural
   para este pacote especificamente.
5. **DEP-002** (`rweather/Crypto`, risco de manutenção) — sem ação
   imediata, monitorizar.

## Verificação desta execução

- `git fetch`/checkout de `origin/main` mais recente (commit `dcf380f`)
  antes de qualquer leitura — sessão anterior estava presa num commit
  antigo (`HEAD` destacado, muito atrás de `main`).
- Logs reais dos 3 jobs `python-dependency-audit` do workflow `Security`
  (run `29065443827`, hoje) lidos via API do GitHub, não assumidos a
  partir do estado "success" — confirmada a linha literal `No known
  vulnerabilities found` nos 3.
- Duas pesquisas independentes em paralelo (Python/pip; Firmware/Nordic)
  via WebSearch/WebFetch contra GitHub Advisory Database, NVD,
  changelogs oficiais e páginas de advisories dos próprios projetos —
  nenhum CVE inventado, todos os IDs acima têm URL de fonte.
- Uso real de cada dependência de alto risco confirmado por leitura
  direta do código (`bridge/crypto_utils.py`, `src/Ble/Ble.cpp`,
  `src/Storage/Storage.cpp`) antes de classificar exploitabilidade —
  não apenas "está na lista de dependências".
- Nenhuma versão alterada em `platformio.ini`/`requirements*.txt`;
  único ficheiro de código tocado fora deste `SECURITY_STATUS.md` foi
  `.github/dependabot.yml` (novo, deteção apenas, `open-pull-requests-limit: 0`).

## Procura contínua

Repetir esta pesquisa diariamente por ecossistema (ver objetivos no
prompt desta rotina). Prioridade nas próximas execuções: (1) confirmar a
versão exata resolvida do Nordic nRF5 SDK/SoftDevice via
`Seeed-Studio/platform-seeedboards` para fechar a incerteza do DEP-003;
(2) confirmar se `Settings → Security → Dependabot alerts` foi ativado
pelo utilizador; (3) reverificar `cryptography`/`tensorflow-cpu` quando
novas CVEs saírem — são as duas dependências de maior impacto potencial
(PII em repouso; pipeline de treino); (4) quando o dashboard ganhar a
primeira dependência npm real, iniciar auditoria JS nesta rotina.
(segurança(deps): regista 5 advisories / propõe atualizações)
