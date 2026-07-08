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
