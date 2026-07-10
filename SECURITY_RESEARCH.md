# CareWear — Compêndio de Pesquisa de Segurança (vigilância externa, S09)

> Registo datado de toda a pesquisa aplicada feita pela rotina de
> **Investigação de Segurança — vigilância externa** (`seguranca/security-research`).
> Esta rotina não altera código — o objetivo deste ficheiro é evitar
> repetir pesquisa já feita e manter as fontes organizadas por tema. Os
> achados com aplicabilidade direta ao CareWear (gaps `RES-XXX`) estão
> registados em `SECURITY_STATUS.md`, secção "Vigilância Externa de
> Segurança (S09)" — este ficheiro é o histórico/detalhe de apoio, não
> duplica os achados, só a pesquisa que os originou.

## Como ler este ficheiro

Organizado por tema (não por data) para facilitar não repetir uma
pesquisa já feita. Cada entrada indica a data da última passagem, as
fontes consultadas, e se gerou algum `RES-XXX` em `SECURITY_STATUS.md`.

---

## Tema: Ataques BLE (famílias BLUFFS/BLESA/SweynTooth e sucessoras)

**Última passagem**: 2026-07-10.

- **BLUFFS** (CVE-2023-24023, Bluetooth Core Spec 4.2-5.4) e **BLESA**
  (spoofing na reconexão BLE) — vulnerabilidades já conhecidas
  (2020/2023), não novas nesta pesquisa, revisitadas só como contexto.
  Fontes: [The Hacker News](https://thehackernews.com/2023/12/new-bluffs-bluetooth-attack-expose.html),
  [USENIX WOOT'20 — BLESA](https://www.usenix.org/conference/woot20/presentation/wu).
- **CVE-2026-0097** (Android, emparelhamento LE, CVSS 3.1 = 8.0,
  crítico, corrigido no boletim Android 2026-06-01): pedido `ll_enc_req`
  fora de sequência engana `smp_command_processor` para saltar para
  "emparelhado" sem confirmação do utilizador — falha na stack Android
  (central), não no CareWear diretamente. Fontes:
  [CVE Record oficial](https://www.cve.org/CVERecord?id=CVE-2026-0097),
  [OffSeq Threat Radar](https://radar.offseq.com/threat/cve-2026-0097-elevation-of-privilege-in-google-and-1b8b1488),
  [DailyCVE](https://dailycve.com/android-bluetooth-logic-error-bypass-cve-2026-0097-critical-dc-jun2026-147/)
  (este último devolveu HTTP 403 ao `WebFetch` direto — lido só via
  resumo de pesquisa, não o artigo completo).
- **WhisperPair (CVE-2025-36911)**: falha no protocolo Google Fast Pair
  permite emparelhamento forçado sem interação do utilizador, até 14m,
  ~10s, com acesso a microfone/controlos e rastreamento de localização
  via rede "Find Hub". Afeta acessórios de várias marcas (Sony, Jabra,
  JBL, Xiaomi, Google, etc.), corrigido do lado Android em jan. 2026.
  Fontes: [Rescana](https://www.rescana.com/post/whisperpair-bluetooth-fast-pair-vulnerability-cve-2025-36911-exposes-millions-of-audio-accessories),
  [Malwarebytes](https://www.malwarebytes.com/blog/news/2026/01/whisperpair-exposes-bluetooth-earbuds-and-headphones-to-tracking-and-eavesdropping),
  [BleepingComputer](https://www.bleepingcomputer.com/news/security/critical-whisperpair-flaw-lets-hackers-track-eavesdrop-via-bluetooth-audio-devices/),
  [Kaspersky](https://www.kaspersky.com/blog/whisperpair-blueooth-headset-location-tracking/55162/),
  [whisperpair.eu](https://whisperpair.eu/) (site dos investigadores).
- **Stealtooth** ("Breaking Bluetooth Security Abusing Silent Automatic
  Pairing", arXiv 2507.00847, jul. 2025): título sugere ataque que
  abusa de emparelhamento automático silencioso — tema com potencial
  aplicação direta a FW-002/NFC-002, mas **não confirmado**: `WebFetch`
  a `arxiv.org/pdf/2507.00847` e `arxiv.org/abs/2507.00847` devolveu
  HTTP 403 nas duas tentativas (2026-07-10). **Pendente de leitura
  completa numa próxima execução** — não gerar `RES-XXX` sobre isto
  sem ler o conteúdo real (regra "sem inventar ataques").
- **BlueSWAT** (arXiv 2405.17987) e "Securing Bluetooth Low Energy: A
  Literature Review" (arXiv 2404.16846) — encontrados na pesquisa,
  não lidos em detalhe nesta execução (fora do orçamento de tempo de
  hoje); candidatos para leitura numa próxima passagem sobre este tema.
- **Nordic nRF52840 / SoftDevice S140**: pesquisa dedicada
  (`site` genéricos + `docs.nordicsemi.com`) não encontrou nenhum CVE
  novo específico ao periférico BLE ou ao SoftDevice S140 em 2026.
  Mesma conclusão já registada por `seguranca/nfc-security` em
  2026-07-08 para o periférico NFCT. Nota técnica encontrada (não uma
  CVE): "a implementação LE Secure Connection para dispositivos Nordic
  reside no 'espaço' do SDK, não no SoftDevice (stack)" — relevante se
  algum dia o CareWear vier a usar LE Secure Connections OOB (NFC-004).
  Fonte: [docs.nordicsemi.com — vulnerabilities in nRF5 SDK versions](https://docs.nordicsemi.com/bundle/nwp_031/page/WP/nwp_031/vulnerabilities.html).

**Gerou**: `RES-001` (reforço de FW-002/NFC-002) em `SECURITY_STATUS.md`.
**Fila para a próxima passagem**: ler Stealtooth por inteiro; ler
BlueSWAT/literature review; repetir busca de CVEs Nordic (base muda com
o tempo).

---

## Tema: CVEs — bibliotecas Python do bridge

**Última passagem**: 2026-07-10. Alvo: `bleak`, `websockets`,
`sqlalchemy`, `cryptography`, `argon2-cffi`, `fastapi`, `uvicorn`,
`pycryptodome` (ver versões exatas em `bridge/requirements.txt`/
`bridge/requirements_db.txt`, lidos antes da pesquisa para não
adivinhar).

- **`bleak`**: sem CVE encontrado. Biblioteca ativa (release mais
  recente citada nos resultados de pesquisa: 3.0.1). Sem achado.
- **`websockets`** (não confundir com o pacote distinto
  `websocket-server`, que teve CVE-2025-66902 em jan. 2026 — **não é o
  pacote usado pelo CareWear**, confirmado em
  `bridge/requirements.txt`/`requirements_db.txt`, ambos listam
  `websockets`, não `websocket-server`): advisories históricas
  (CVE-2021-33880 timing attack em HTTP Basic Auth; CVE-2018-1000518
  DoS por exaustão de memória) — nenhuma nova para 2025-2026 encontrada
  para o pacote `websockets` propriamente dito. Fonte: [GitHub Advisory
  Database — pip/websockets](https://github.com/advisories?query=type%3Areviewed+ecosystem%3Apip+websockets).
- **`sqlalchemy`**: nenhuma advisory reviewed encontrada no GitHub
  Advisory Database para 2025/2026 nesta pesquisa.
- **`cryptography`**: 4 advisories encontradas
  (`GHSA-p423-j2cm-9vmq`/CVE-2026-39892, buffer overflow em
  `Hash.update()` com buffers não contíguos, afeta ≥45.0.0 \<46.0.7;
  `GHSA-537c-gmf6-5ccf`, OpenSSL vulnerável embutido nos wheels, afeta
  \<48.0.1; mais duas identificadas por `seguranca/dependency-security`,
  ver nota abaixo). **Já registadas e avaliadas em detalhe por
  `seguranca/dependency-security` como `DEP-001`** — confirmado via
  `git show origin/seguranca/dependency-security:SECURITY_STATUS.md`
  antes de fechar esta pesquisa, para não duplicar. Conclusão dessa
  rotina (não desta): não explorável no uso atual (`crypto_utils.py` só
  usa `AESGCM`, sem `Hash.update()` manual nem curvas elípticas), risco
  só se o piso `>=41.0.0` resolver para uma versão antiga real. Sem
  achado novo aqui — só referência cruzada.
- **`fastapi`/`uvicorn`/`argon2-cffi`/`pycryptodome`**: sem CVE
  específico encontrado nesta pesquisa.

**Gerou**: nada de novo em `SECURITY_STATUS.md` (achado de
`cryptography` já coberto por `DEP-001`, só referenciado em "sem ação"
na secção S09).
**Fila para a próxima passagem**: tentar fontes GHSA diretas por pacote
em vez de agregadores genéricos (Snyk devolveu 403 ao `WebFetch` direto
nesta execução — usar `github.com/advisories?query=...` funcionou
melhor); repetir para `tensorflow-cpu`/`scikit-learn`/`xgboost`
(`ml/requirements.txt`, não coberto hoje — ver nota de
`seguranca/dependency-security` sobre `tensorflow-cpu` a reverificar).

---

## Tema: OWASP (IoT / API / Mobile Top 10)

**Última passagem**: 2026-07-10 (só IoT, pesquisa preliminar).

- **OWASP IoT Top 10**: pesquisa não encontrou uma revisão 2026 — a
  versão de referência publicamente disponível continua a de 2018.
  Nota importante para não confundir numa próxima execução: existe um
  "OWASP Top 10:2025" (`owasp.org/Top10/2025/`), mas é da categoria
  **aplicações web**, não IoT — encontrado nos resultados de pesquisa
  por proximidade de nome, não é o mesmo documento. Fonte:
  [owasp.org/www-project-internet-of-things](https://owasp.org/www-project-internet-of-things/).
- **OWASP API Security Top 10** e **OWASP Mobile Top 10**: não
  pesquisados nesta execução (fila para a próxima, ver rotação).

**Gerou**: nada (sem revisão de framework nova para comparar).
**Fila**: comparar checklist OWASP IoT Top 10 2018 item a item com o
CareWear (I1 Weak Passwords, I2 Insecure Network Services, ..., I9
Insecure Default Settings, I10 Lack of Physical Hardening) — nenhuma
rotina de segurança parece ter feito esta comparação sistemática ainda,
candidato a próxima execução prioritária.

---

## Tema: NIST Cybersecurity Framework / NIST IoT (8259/8425)

**Última passagem**: 2026-07-10.

- **NIST IR 8259 Revisão 1** ("Foundational Cybersecurity Activities
  for IoT Product Manufacturers"), publicada 2026-04-20: amplia o
  âmbito do 8259 original para cobrir todo o ciclo de vida do produto,
  incluindo comunicação ao cliente sobre manutenção/suporte/fim de
  vida. **Não consegui ler o texto completo** (`WebFetch` a
  `csrc.nist.gov/pubs/ir/8259/r1/final` devolveu HTTP 403) — achado
  baseado no resumo da página do programa NIST Cybersecurity for IoT e
  cobertura relacionada. Fonte: [csrc.nist.gov — NIST Cybersecurity for
  IoT Program](https://www.nist.gov/itl/applied-cybersecurity/nist-cybersecurity-iot-program).
- **NIST IR 8425/8425A**: perfil de baseline de consumidor IoT (base do
  "US Cyber Trust Mark"); 8425A é a especialização para routers —
  **não diretamente aplicável ao CareWear** (não é um router), revisto
  só por completude.

**Gerou**: `RES-003` (gap de SBOM/política EOL) em `SECURITY_STATUS.md`.
**Fila**: ler o texto completo da Revisão 1 do 8259 (bloqueado por 403
hoje — tentar via `nvlpubs.nist.gov` diretamente, ou um PDF em vez do
HTML da página `csrc.nist.gov`, numa próxima execução); NIST
Cybersecurity Framework (CSF) genérico, ainda não comparado ao CareWear.

---

## Tema: ENISA / MITRE ATT&CK / normas de dispositivo médico (lado vigilância)

**Última passagem**: nunca — não coberto ainda por esta rotina.

**Fila**: ENISA guidelines IoT/saúde; MITRE ATT&CK for ICS/embedded;
lado de vigilância (não a análise de conformidade aprofundada, que é de
outra rotina) de IEC 62443, IEC 81001-5-1, IEC 62304, ISO 14971, MDR
(UE), guias de cibersegurança de dispositivos médicos da FDA.

---

## Tema: TinyML / ML — model extraction, poisoning, adversarial examples

**Última passagem**: 2026-07-10 (pesquisa geral, sem CVE específico —
tema ainda não aplicável ao firmware real do CareWear).

- Confirmado por pesquisa (surveys arXiv 2024-2025: 2407.11599,
  2411.07114, 2502.16065, 2508.15031): dispositivos TinyML fisicamente
  acessíveis (wearables incluídos) são uma superfície real para
  extração de modelo (queries → modelo funcionalmente equivalente) e
  ataques de canal lateral (consumo de energia, emissões
  eletromagnéticas, timing).
- **Aplicabilidade ao CareWear confirmada como ainda não real**: `ml/`
  (XGBoost/LSTM/Random Forest, `ml/requirements.txt`) treina modelos
  **offline**, sem nenhum modelo embarcado no firmware nRF52840 até à
  data desta pesquisa (roadmap em `PROJECT_STATUS.md` — TinyML
  embarcado é fase futura; `emlearn` já está listado em
  `requirements.txt` mas sem uso confirmado no firmware `src/`).

**Gerou**: nada (registado em "sem achado aplicável hoje" em
`SECURITY_STATUS.md`, para reativar quando um modelo for de facto
embarcado).
**Fila**: reativar esta pesquisa (com foco em mitigação: quantização,
ofuscação de modelo, rate-limiting de queries se houver uma API de
inferência) assim que `rotina/ml-review` ou equivalente confirmar um
modelo embarcado real.

---

## Notas de processo desta rotina

- **Verificação cruzada obrigatória antes de registar qualquer
  achado**: esta execução confirmou, via `git show
  origin/<branch>:SECURITY_STATUS.md`, o conteúdo de
  `seguranca/nfc-security`, `seguranca/backend-api-security` e
  `seguranca/dependency-security` antes de escrever qualquer `RES-XXX`
  — para não duplicar achados já cobertos (aconteceu com as CVEs de
  `cryptography`, já em `DEP-001`, e com o gap de validação de Origin
  em WebSocket, já em `WS-001`). Recomenda-se manter este passo em toda
  execução futura desta rotina, à medida que mais branches de segurança
  forem criadas.
- **Fontes bloqueadas (HTTP 403) nesta execução**: `arxiv.org/pdf/...`
  e `arxiv.org/abs/...` (Stealtooth), `dailycve.com` (via `WebFetch`
  direto, contornado via resultados de `WebSearch`), `security.snyk.io`
  (via `WebFetch` direto — `github.com/advisories` funcionou como
  alternativa), `csrc.nist.gov/pubs/ir/8259/r1/final`. Não é um
  problema de rede geral (`WebSearch` funcionou normalmente) — parece
  ser bloqueio pontual por site a pedidos automatizados. Registar como
  nota operacional, não como limitação de conteúdo.
- Este é o **primeiro registo** desta rotina — não havia
  `SECURITY_RESEARCH.md` nem branch `seguranca/security-research`
  anteriores (confirmado via `git ls-remote --heads origin` antes de
  começar).
