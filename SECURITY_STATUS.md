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

**Ainda em aberto** (não resolvido nesta execução, requisito/proposta):
- Sem TTL automático — os dados continuam a viver indefinidamente até
  alguém carregar no botão novo. Implementar expiração automática
  exigiria decidir um prazo (decisão do utilizador/responsável, fora
  do âmbito desta correção contida).
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
