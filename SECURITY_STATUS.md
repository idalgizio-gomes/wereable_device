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

## Resumo para a rotina de desenvolvimento (NFC #2 e seguintes)

Antes de dar o NFC como "pronto" (qualquer fase que ative
`UICR.NFCPINS` ou emita um NDEF real), confirmar que:

1. NFC-001, NFC-002, NFC-007 estão cumpridos por desenho (autenticação
   nunca depende do UID/NFC sozinho; handover sempre confirmado no
   dispositivo; sem comandos de dados via NFC).
2. NFC-003 está cumprido no conteúdo exato do NDEF proposto (rever com
   esta rotina antes de commitar o primeiro NDEF real — ver Fase C em
   `PROJECT_STATUS.md`).
3. NFC-004/NFC-005 foram avaliados e documentados (mesmo que a decisão
   seja "handover estático simples, sem OOB dinâmico, aceitando o risco
   residual X" — desde que seja uma decisão explícita, não omissão).
4. NFC-006 é um risco residual conhecido e aceite, não uma surpresa.

## Pesquisa contínua (ataques NFC/handover novos)

Pesquisa feita em 2026-07-08 (fontes acima, NFC-006) — focada em relay
attacks de pagamento (aplicável por analogia ao handover, ver
mitigação). Nenhuma CVE específica ao periférico NFCT do nRF52840 ou ao
SoftDevice S140 encontrada nesta pesquisa. Repetir esta pesquisa em
execuções futuras desta rotina, à medida que a fase C avançar (termos
sugeridos: "NFC Forum BTSSP vulnerability", "TNEP negotiated handover
security", CVEs Nordic nrfx/NFCT).
