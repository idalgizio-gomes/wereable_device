/* ============================================================
   PEÇAS REUTILIZÁVEIS
============================================================ */
function statTile(icon, label, value, unit, color, valueId, sim, hintId){
  const valAttr = valueId ? ` id="${valueId}"` : '';
  const simBadge = sim ? ` <span class="sim-flag" title="Classificação de rotina simulada — sem classificador HAR embarcado ainda">simulado</span>` : '';
  // hintId (2026-08-06, pedido da utilizadora): linha extra, escondida por
  // omissão (display:none inline — sem depender de nenhuma classe CSS
  // nova), que applyLiveVitals() mostra quando não há leitura de HR/SpO2
  // (placa fora do pulso ou pressão insuficiente contra a pele — ver
  // checkFingerPresentBrief() em Ppg.cpp, é exatamente esse sinal que
  // liveState.hr/spo2==null reflete agora). Só usado pelos tiles de
  // HR/SpO2 (hintId omitido nos restantes, que não precisam disto).
  const hintHtml = hintId
    ? `<div class="stat-hint" id="${hintId}" style="display:none;font-size:.75rem;color:var(--status-warning);margin-top:2px;"></div>`
    : '';
  return `
    <div class="stat-tile">
      <div class="top">
        <span class="stat-icon" style="background:color-mix(in srgb, ${color} 18%, transparent); color:${color}">${iconFor(icon)}</span>
      </div>
      <div class="label">${label}${simBadge}</div>
      <div class="value"${valAttr}>${value}${unit ? `<span class="unit">${unit}</span>` : ''}</div>
      ${hintHtml}
    </div>`;
}

/* ------------------------------------------------------------
   FADIGA DE ALERTA — silenciar alertas não-críticos temporariamente
   ------------------------------------------------------------
   Ideia da pesquisa: a literatura de monitorização remota (RPM) aponta a
   "fadiga de alerta" (alert fatigue) como um risco real — demasiadas
   notificações repetidas para a mesma situação já reconhecida levam a
   que os cuidadores comecem a ignorar TODOS os alertas, incluindo os que
   importam. A mitigação recomendada é permitir silenciar/adiar alertas
   já vistos, com escalonamento gradual antes de reforçar.
   DECISÃO DE SEGURANÇA (não negociável, tomada sem esperar confirmação
   por ser uma salvaguarda e não uma redução de segurança): alertas
   'critical' NUNCA podem ser silenciados — só 'serious' e 'warning'. Um
   alerta silenciado continua visível na lista (não desaparece), só fica
   com uma nota clara de até quando está silenciado, com opção de
   reativar a qualquer momento.
------------------------------------------------------------ */
const MUTED_ALERTS_KEY = 'carewear_muted_alerts';

// As chaves guardadas em localStorage (silenciados, ocorrências, lidos)
// são sempre namespaced por paciente ("idPaciente::chaveAlerta") — sem
// isto, dois pacientes diferentes com a mesma chave de alerta (ex.: se
// ambos tivessem um alerta 'spo2-limite') partilhariam acidentalmente o
// mesmo estado de silêncio/leitura.
function patientAlertKey(patientId, alertKey){
  return `${patientId}::${alertKey}`;
}

function loadMutedAlerts(){
  try {
    const raw = localStorage.getItem(MUTED_ALERTS_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  return {};
}
function saveMutedAlerts(map){
  try { localStorage.setItem(MUTED_ALERTS_KEY, JSON.stringify(map)); }
  catch (e) { /* quota excedida ou localStorage indisponível - silêncio fica só em memória */ }
}
let mutedAlerts = loadMutedAlerts();

function alertMutedUntil(key){
  const until = mutedAlerts[key];
  if (!until) return null;
  if (until <= Date.now()) { delete mutedAlerts[key]; saveMutedAlerts(mutedAlerts); return null; }
  return until;
}

/* ------------------------------------------------------------
   LEITURA DE ALERTAS — "Marcar como lida"
   ------------------------------------------------------------
   Pedido do utilizador (2026-07-03): em vez de o indicador do sino
   (badge-dot vermelho na topbar) desligar sozinho só por abrir a vista de
   alertas — o que é fácil de disparar sem querer e não regista uma
   confirmação real — cada alerta tem um botão explícito "Marcar como
   lida". Só isso desliga o indicador (quando já não há nenhum alerta por
   ler do paciente selecionado). Ao contrário de silenciar (que pausa o
   alerta por um período), marcar como lida não afeta a severidade nem o
   escalonamento — só regista que o cuidador já viu esta informação.
------------------------------------------------------------ */
const READ_ALERTS_KEY = 'carewear_read_alerts';

function loadReadAlerts(){
  try {
    const raw = localStorage.getItem(READ_ALERTS_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  return {};
}
function saveReadAlerts(map){
  try { localStorage.setItem(READ_ALERTS_KEY, JSON.stringify(map)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
}
let readAlerts = loadReadAlerts();

function isAlertRead(key){
  return !!readAlerts[key];
}
function markAlertRead(key){
  readAlerts[key] = true;
  saveReadAlerts(readAlerts);
  updateNotificationBadge();
  if (currentView) renderView(currentView);
}

// Atualiza a visibilidade do ponto vermelho de notificação na topbar,
// consoante existam ou não alertas por ler do paciente atualmente
// selecionado (ver selectedPatient()). Chamada depois de login, de mudar
// de paciente, e de marcar um alerta como lido.
function updateNotificationBadge(){
  const dot = document.getElementById('notifBadgeDot');
  if (!dot) return;
  const hasUnread = unreadActiveAlerts().length > 0;
  dot.style.display = hasUnread ? '' : 'none';
  const bellBtn = document.getElementById('notifBellBtn');
  if (bellBtn) bellBtn.setAttribute('aria-label', hasUnread ? 'Alertas — há alertas por ler' : 'Alertas');
}

/* ------------------------------------------------------------
   ESCALONAMENTO GRADUAL — subir a prioridade de um alerta 'warning'
   que se repete, em vez de o cuidador ver sempre a mesma prioridade
   baixa para uma condição que já ocorreu várias vezes.
   ------------------------------------------------------------
   `occurrences` em cada alerta (ver array `alerts` acima) é o nº de
   vezes que essa condição ocorreu nas últimas 24h — dado de exemplo
   nesta versão protótipo (sem histórico persistido ainda, ver
   Prioridade 4 / serviço de persistência SQLite no PROJECT_STATUS.md).
   `alertOccurrences` (localStorage) permite ao cuidador "resolver" a
   contagem ao silenciar o alerta, tornando a demonstração interativa.
   DECISÃO DE SEGURANÇA (mesmo raciocínio da mitigação de silenciamento
   acima): este mecanismo só sobe 'warning' para 'serious' — nunca gera
   'critical' automaticamente a partir de uma simples contagem de
   repetições, isso continua reservado a deteções clínicas reais. */
const ALERT_OCCURRENCES_KEY = 'carewear_alert_occurrences';
const ALERT_ESCALATION_THRESHOLD = 3; // nº de ocorrências em 24h para subir de 'warning' a 'serious'

function loadAlertOccurrences(){
  try {
    const raw = localStorage.getItem(ALERT_OCCURRENCES_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  return {};
}
function saveAlertOccurrences(map){
  try { localStorage.setItem(ALERT_OCCURRENCES_KEY, JSON.stringify(map)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
}
let alertOccurrences = loadAlertOccurrences();

// Nº de ocorrências a usar para este alerta: o valor guardado localmente
// (já ajustado por um silenciamento anterior) tem prioridade sobre o
// dado de exemplo do próprio alerta. 'fullKey' já vem namespaced por
// paciente (ver patientAlertKey()).
function occurrencesFor(a, fullKey){
  return (fullKey in alertOccurrences) ? alertOccurrences[fullKey] : (a.occurrences || 1);
}

function alertEscalation(a, fullKey){
  const count = occurrencesFor(a, fullKey);
  // 1) Escalonamento por REPETIÇÃO (o que já existia): 'warning' -> 'serious'
  //    e nunca mais alto, ver a decisão de segurança documentada acima.
  let severity = a.sev;
  let porRepeticao = false;
  if (a.sev === 'warning' && count >= ALERT_ESCALATION_THRESHOLD) {
    severity = 'serious';
    porRepeticao = true;
  }

  // 2) Escalonamento por TEMPO SEM CONFIRMAÇÃO (RF-07, 2026-09-07).
  //    Alertas reais vindos do bridge já chegam com o nível efetivo
  //    calculado do lado do servidor (a.effectiveSeverity/a.escalatedAt,
  //    colunas escalated_to_severity/escalated_at) — a base de dados é a
  //    autoridade e o browser não volta a escalar por cima disso, senão
  //    dois separadores abertos escalavam o mesmo alerta duas vezes.
  if (a.live){
    return {
      severity: a.effectiveSeverity || a.sev,
      count, escalated: porRepeticao,
      timeEscalated: !!a.escalatedAt,
      escalatedAtMs: a.escalatedAtMs || null,
      originalSeverity: a.sev,
    };
  }

  const inicio = isAlertConfirmed(fullKey) ? null : alertClockStart(a, fullKey);
  let escalatedAtMs = null;
  if (inicio){
    const minutos = (Date.now() - inicio) / 60000;
    const saltos = Math.floor(minutos / ALERT_TIME_ESCALATION_MINUTES);
    for (let i = 0; i < saltos; i++){
      const seguinte = alertNextSeverity(severity);
      if (!seguinte) break;  // 'critical' é o topo — não escala para sempre
      severity = seguinte;
      escalatedAtMs = inicio + (i + 1) * ALERT_TIME_ESCALATION_MINUTES * 60000;
    }
  }
  // O requisito pede que o escalonamento fique REGISTADO, não só que a
  // cor mude — o equivalente local de escalated_at/escalated_to_severity.
  if (escalatedAtMs && fullKey){
    const registado = alertEscalations[fullKey];
    if (!registado || registado.toSeverity !== severity){
      alertEscalations[fullKey] = {toSeverity: severity, at: escalatedAtMs, fromSeverity: a.sev};
      saveAlertMap(ALERT_ESCALATIONS_KEY, alertEscalations);
    }
  }
  return {
    severity, count, escalated: porRepeticao,
    timeEscalated: !!escalatedAtMs,
    escalatedAtMs,
    originalSeverity: a.sev,
  };
}

// Re-renderiza a vista atual periodicamente para que um escalonamento por
// tempo apareça sozinho, sem o cuidador ter de recarregar a página — um
// alerta que sobe de nível só quando alguém clica noutro sítio não estaria
// a cumprir o requisito. Meio período do prazo de escalonamento é
// suficiente e é barato (a renderização é só string -> innerHTML).
let alertEscalationTimer = null;
function startAlertEscalationTimer(){
  if (alertEscalationTimer) return;
  alertEscalationTimer = setInterval(() => {
    if (!currentView) return;
    const temPendentes = (typeof currentAlerts === 'function' ? currentAlerts() : [])
      .some(a => !isAlertConfirmed(patientAlertKey(selectedPatientId, a.key)));
    if (temPendentes) renderView(currentView);
  }, ALERT_TIME_ESCALATION_MINUTES * 30000);
}

function muteAlert(fullKey, hours){
  mutedAlerts[fullKey] = Date.now() + hours * 3600 * 1000;
  saveMutedAlerts(mutedAlerts);
  // silenciar = o cuidador reconheceu o alerta, por isso a contagem que
  // alimenta o escalonamento reinicia (tem de se repetir outra vez para
  // voltar a subir de prioridade)
  alertOccurrences[fullKey] = 0;
  saveAlertOccurrences(alertOccurrences);
  if (currentView) renderView(currentView);
}
function unmuteAlert(fullKey){
  delete mutedAlerts[fullKey];
  saveMutedAlerts(mutedAlerts);
  if (currentView) renderView(currentView);
}

/* ------------------------------------------------------------
   RF-07 (2026-09-07) — GRAVIDADE, MOTIVO, CONFIRMAÇÃO E
   ESCALONAMENTO POR TEMPO
   ------------------------------------------------------------
   O que já existia antes desta data e é REUTILIZADO aqui, não
   reescrito: a paleta das 4 severidades (SEV_COLOR/SEV_BG/pillHtml em
   templates-core.js), o silenciamento (muteAlert), o "marcar como lida"
   (markAlertRead) e o escalonamento por REPETIÇÃO (alertEscalation).
   O que faltava para cumprir o critério de aceitação:

     1. os quatro níveis. A paleta usa a chave 'good' onde a base de
        dados usa 'info' (ver alerts.severity em bridge/schema.sql), por
        isso é preciso traduzir entre os dois vocabulários — ALERT_SEV_*
        abaixo. Sem isto um alerta 'info' vindo do bridge era pintado com
        undefined (fundo transparente, texto herdado);
     2. o MOTIVO. "plain" é uma explicação em linguagem simples do que a
        condição significa para a família; o requisito pede outra coisa —
        porque é que ESTE alerta disparou, com os números reais. O bridge
        já compõe essa frase (vital_alerts.explain_vital_alert /
        explain_wear_state) e ela chega no campo `reason`;
     3. a CONFIRMAÇÃO, que é diferente de "marcar como lida": ler é dizer
        "vi isto", confirmar é dizer "vi isto E fiz X" (ver RF-08 abaixo).
        É a confirmação — não a leitura — que trava o escalonamento;
     4. o escalonamento POR TEMPO. O que existia subia de nível quando a
        mesma condição se repetia N vezes; o requisito pede que suba
        quando ninguém confirma dentro de N minutos, que é o caso
        perigoso (um alerta a que ninguém responde).

   NOTA sobre a "DECISÃO DE SEGURANÇA" documentada mais acima (o
   escalonamento por repetição nunca gera 'critical' a partir de uma
   contagem): continua a valer tal e qual para esse mecanismo. O
   escalonamento por TEMPO percorre a escada toda até 'critical' porque
   é isso que o requisito pede e porque o sinal é outro — não é "esta
   condição já aconteceu 3 vezes", é "ninguém respondeu a este alerta
   durante 45 minutos", que é precisamente a situação que deve acabar
   por chegar ao nível máximo.
------------------------------------------------------------ */
// Ordem de gravidade. Igual a vital_alerts.SEVERITY_LADDER no bridge —
// os dois lados TÊM de concordar sobre qual é o nível a seguir a
// 'warning', senão o dashboard mostra um nível e a base de dados guarda
// outro.
const ALERT_SEVERITY_LADDER = ['info', 'warning', 'serious', 'critical'];

// Tradução entre o vocabulário da base de dados (info/warning/serious/
// critical) e as chaves da paleta em templates-core.js, que usa 'good'
// no lugar de 'info'. Só o nome difere; a cor de 'good' é exatamente a
// cor neutra/informativa que 'info' precisa.
const ALERT_SEV_PALETTE = {info:'good', warning:'warning', serious:'serious', critical:'critical'};
const ALERT_SEV_LABEL = {info:'Informação', warning:'Aviso', serious:'Sério', critical:'Crítico'};

function alertSevPaletteKey(sev){ return ALERT_SEV_PALETTE[sev] || sev; }
function alertSevLabel(sev){ return ALERT_SEV_LABEL[sev] || sev; }
function alertSeverityRank(sev){ return ALERT_SEVERITY_LADDER.indexOf(sev); }
function alertNextSeverity(sev){
  const i = ALERT_SEVERITY_LADDER.indexOf(sev);
  // Falha fechada, igual a vital_alerts.next_severity(): um valor
  // desconhecido não escala, em vez de saltar para 'critical'.
  return (i < 0 || i + 1 >= ALERT_SEVERITY_LADDER.length) ? null : ALERT_SEVERITY_LADDER[i + 1];
}

// Minutos sem confirmação até subir um nível. Igual a
// BleBridge.ALERT_ESCALATION_MINUTES (bridge/ble_bridge.py) — mantido em
// sincronia à mão, os dois lados não partilham este valor em tempo de
// execução (mesma limitação já documentada para
// DUMP_CTRL_FORCE_READING_SECONDS).
const ALERT_TIME_ESCALATION_MINUTES = 15;

// Instante em que cada alerta foi visto pela primeira vez por este
// browser. Necessário porque os alertas de demonstração só têm um tempo
// textual ("há 6 min"), sem carimbo comparável — para os alertas reais
// vindos do bridge usa-se o created_at do próprio alerta, que é a fonte
// certa (ver alertEscalation()).
const ALERT_FIRST_SEEN_KEY = 'carewear_alert_first_seen';
// Escalonamentos já registados, com o momento e o nível para que
// subiram — o requisito pede explicitamente que isto FIQUE REGISTADO,
// não só que a cor mude no ecrã (colunas escalated_at/
// escalated_to_severity do lado do bridge).
const ALERT_ESCALATIONS_KEY = 'carewear_alert_escalations';

function loadAlertMap(key){
  try {
    const raw = localStorage.getItem(key);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  return {};
}
function saveAlertMap(key, map){
  try { localStorage.setItem(key, JSON.stringify(map)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
}
let alertFirstSeen = loadAlertMap(ALERT_FIRST_SEEN_KEY);
let alertEscalations = loadAlertMap(ALERT_ESCALATIONS_KEY);

// Momento a partir do qual se conta o prazo de confirmação deste alerta.
// Para alertas reais é o created_at do bridge; para os de demonstração é
// a primeira vez que este browser os mostrou (registada aqui).
function alertClockStart(a, fullKey){
  if (a && a.createdAtMs) return a.createdAtMs;
  if (!fullKey) return null;
  if (!(fullKey in alertFirstSeen)){
    alertFirstSeen[fullKey] = Date.now();
    saveAlertMap(ALERT_FIRST_SEEN_KEY, alertFirstSeen);
  }
  return alertFirstSeen[fullKey];
}

/* ------------------------------------------------------------
   RF-08 (2026-09-07) — REGISTO DA AÇÃO TOMADA APÓS ALERTA
   ------------------------------------------------------------
   Cada confirmação guarda a ação escolhida (lista fechada, a mesma
   validada pelo bridge em ALERT_RESOLUTION_ACTIONS) e uma NOTA LIVRE.
   A nota é texto escrito pelo utilizador e vai parar a innerHTML no
   histórico — passa SEMPRE por escapeHtml() (já houve um XSS real neste
   projeto por causa exatamente disto, ver o cabeçalho de escapeHtml()
   em templates-core.js).
------------------------------------------------------------ */
const ALERT_CONFIRMATIONS_KEY = 'carewear_alert_confirmations';
let alertConfirmations = loadAlertMap(ALERT_CONFIRMATIONS_KEY);

// Valor guardado <-> rótulo mostrado. Os valores TÊM de ser iguais aos
// de ALERT_RESOLUTION_ACTIONS em bridge/ble_bridge.py, senão o bridge
// recusa a confirmação com "acao desconhecida".
const ALERT_ACTION_LABELS = {
  contactei_o_utente: 'Contactei o utente',
  verifiquei_presencialmente: 'Fui verificar presencialmente',
  contactei_a_equipa_clinica: 'Contactei a equipa clínica',
  chamei_emergencia_medica: 'Chamei emergência médica',
  ajustei_o_dispositivo: 'Ajustei/recoloquei o dispositivo',
  falso_alarme: 'Falso alarme',
  sem_acao_necessaria: 'Sem ação necessária',
};

function alertActionLabel(action){ return ALERT_ACTION_LABELS[action] || action; }
function alertConfirmationFor(fullKey){ return fullKey ? (alertConfirmations[fullKey] || null) : null; }
function isAlertConfirmed(fullKey){ return !!alertConfirmationFor(fullKey); }

// Abre/fecha o formulário de confirmação de um alerta. Fica embutido na
// própria linha (não é um modal) porque a ação é de baixo risco e o
// cuidador precisa de continuar a ver o motivo do alerta enquanto
// escreve a nota — ao contrário do cancelamento de emergência, que é
// destrutivo e por isso tem modal + confirmação reforçada.
function toggleAlertConfirmForm(idx){
  const box = document.getElementById(`alertConfirm-${idx}`);
  if (!box) return;
  box.style.display = box.style.display === 'none' ? 'block' : 'none';
}

function submitAlertConfirm(fullKey, idx, alertUuid){
  const actionEl = document.getElementById(`alertConfirmAction-${idx}`);
  const noteEl = document.getElementById(`alertConfirmNote-${idx}`);
  if (!actionEl || !noteEl) return;
  const action = actionEl.value;
  const note = noteEl.value.slice(0, 500); // igual a ALERT_RESOLUTION_NOTE_MAX_CHARS no bridge
  if (!Object.prototype.hasOwnProperty.call(ALERT_ACTION_LABELS, action)) return;

  alertConfirmations[fullKey] = {
    action, note,
    at: Date.now(),
    by: (document.getElementById('avatarName') || {}).textContent || '',
  };
  saveAlertMap(ALERT_CONFIRMATIONS_KEY, alertConfirmations);
  // Confirmar implica ter visto — sem isto o alerta continuava a contar
  // como "por ler" no sino da topbar depois de já ter sido tratado.
  markAlertRead(fullKey);

  // Alertas REAIS (vindos do bridge, com uuid) são confirmados também na
  // base de dados: é lá que ficam resolved_by_user_id/resolved_at/
  // resolution_note, e é lá que o escalonamento do lado do servidor
  // consulta se alguém já respondeu. Os de demonstração não têm uuid e
  // ficam só no localStorage deste browser.
  if (alertUuid && typeof sendWsCommandWithArgs === 'function'){
    sendWsCommandWithArgs('confirm_alert', {alert_uuid: alertUuid, action, note});
  }
  // Alertas de emergência em curso: cancela também o escalonamento
  // automático ao contacto de emergência agendado pelo bridge (cmd que
  // já existia em ble_bridge.py e que o dashboard nunca chamava).
  const alertaEmergencia = liveEmergencyAlertIdFor(fullKey);
  if (alertaEmergencia && typeof sendWsCommandWithArgs === 'function'){
    sendWsCommandWithArgs('acknowledge_alert', {alert_id: alertaEmergencia});
  }
  if (currentView) renderView(currentView);
}

// Reabre um alerta confirmado por engano. Não apaga o registo anterior
// em silêncio — o histórico de ações (ver renderAlertActionsHistory)
// deixa de o listar, mas a reabertura é uma ação explícita do cuidador,
// não um efeito colateral de outra coisa.
function reopenAlertConfirmation(fullKey){
  delete alertConfirmations[fullKey];
  saveAlertMap(ALERT_CONFIRMATIONS_KEY, alertConfirmations);
  if (currentView) renderView(currentView);
}

// HTML do formulário de confirmação + do resumo da ação já registada.
function alertConfirmControl(a, fullKey, idx){
  if (!fullKey) return '';
  const registada = alertConfirmationFor(fullKey);
  if (registada){
    const quando = new Date(registada.at).toLocaleString(currentLang, {
      day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit',
    });
    // escapeHtml() em TUDO o que veio do utilizador: a nota é texto livre
    // e o nome do cuidador vem do perfil, também editável.
    const nota = registada.note
      ? `<div class="alert-explain-box" style="display:block;">${escapeHtml(registada.note)}</div>` : '';
    return `
      <div class="alert-confirmed-summary" style="display:flex;flex-direction:column;gap:4px;align-items:flex-start;">
        <span class="alert-read-badge">Confirmado — ${escapeHtml(alertActionLabel(registada.action))}</span>
        <span class="alert-mute-note">${quando}${registada.by ? ` · ${escapeHtml(registada.by)}` : ''}</span>
        ${nota}
        <button type="button" class="alert-explain-btn" onclick="reopenAlertConfirmation('${fullKey}')">Reabrir</button>
      </div>`;
  }
  const opcoes = Object.keys(ALERT_ACTION_LABELS)
    .map(v => `<option value="${v}">${escapeHtml(ALERT_ACTION_LABELS[v])}</option>`).join('');
  const uuidArg = a && a.alertUuid ? `'${a.alertUuid}'` : 'null';
  return `
    <button type="button" class="alert-explain-btn" onclick="toggleAlertConfirmForm(${idx})">Confirmar alerta</button>
    <div class="alert-explain-box" id="alertConfirm-${idx}" style="display:none;">
      <label style="display:block;margin-bottom:6px;">Ação tomada
        <select id="alertConfirmAction-${idx}" style="width:100%;margin-top:4px;">${opcoes}</select>
      </label>
      <label style="display:block;margin-bottom:6px;">Nota (opcional)
        <textarea id="alertConfirmNote-${idx}" rows="2" maxlength="500" style="width:100%;margin-top:4px;"
          placeholder="O que observou e o que fez"></textarea>
      </label>
      <button type="button" class="alert-explain-btn"
        onclick="submitAlertConfirm('${fullKey}', ${idx}, ${uuidArg})">Registar confirmação</button>
    </div>`;
}

// 'idx' identifica este alerta dentro do array de alertas do paciente
// atual (ver chamadas currentAlerts().map((a,i) => alertRow(a,i))), usado
// para dar um id único à caixa de explicação em linguagem simples e ao
// botão que a mostra/esconde (ver toggleAlertPlain()). Se 'a.plain' não
// existir (ex.: alertas vindos de outra fonte no futuro, sem explicação
// escrita ainda), o botão simplesmente não é mostrado em vez de mostrar
// uma caixa vazia.
function alertRow(a, idx){
  const fullKey = a.key ? patientAlertKey(selectedPatientId, a.key) : null;
  const esc = alertEscalation(a, fullKey);
  const paleta = alertSevPaletteKey(esc.severity);
  const icon = esc.severity==='critical' ? 'heart' : esc.severity==='serious' ? 'warn' : 'zap';
  const explainId = `alertPlain-${idx}`;
  const explainBtn = a.plain
    ? `<button type="button" class="alert-explain-btn" data-i18n="alert.explainShow" onclick="toggleAlertPlain('${explainId}', this)">${t('alert.explainShow')}</button>
       <div class="alert-explain-box" id="${explainId}" style="display:none;">${alertField(a,'plain')}</div>`
    : '';

  const escalationNote = esc.escalated
    ? `<p class="alert-escalation-note">${t('alertRow.escalationNote', {n: esc.count})}</p>`
    : '';

  // RF-07 — nível + motivo, sempre visíveis (não escondidos atrás de um
  // botão como a explicação em linguagem simples): são os dois elementos
  // que o critério de aceitação exige que cada alerta tenha.
  const sevPill = `<span class="pill ${paleta}" style="background:${SEV_BG[paleta]};color:${SEV_COLOR[paleta]}">${escapeHtml(alertSevLabel(esc.severity))}</span>`;
  const timeEscalationNote = esc.timeEscalated
    ? `<p class="alert-escalation-note">Escalado de "${escapeHtml(alertSevLabel(esc.originalSeverity))}" para "${escapeHtml(alertSevLabel(esc.severity))}"${esc.escalatedAtMs ? ` às ${new Date(esc.escalatedAtMs).toLocaleTimeString(currentLang, {hour:'2-digit', minute:'2-digit'})}` : ''} — sem confirmação ao fim de ${ALERT_TIME_ESCALATION_MINUTES} min.</p>`
    : '';
  const motivo = alertReasonText(a);
  // Estilo em linha e não uma classe CSS nova: index.html (onde vive todo
  // o CSS desta página) está fora do âmbito desta alteração, e uma classe
  // sem regra definida não teria aspeto nenhum.
  const reasonHtml = motivo
    ? `<div class="alert-reason" style="font-size:.82rem;color:var(--text-secondary);margin-top:4px;"><b>Motivo:</b> ${a.live ? escapeHtml(motivo) : motivo}</div>`
    : '';

  const mutedUntil = fullKey ? alertMutedUntil(fullKey) : null;
  let muteControl = '';
  if (a.sev === 'critical') {
    muteControl = `<p class="alert-mute-note">${t('alertRow.criticalNoMute')}</p>`;
  } else if (mutedUntil) {
    const untilStr = new Date(mutedUntil).toLocaleTimeString(currentLang, {hour:'2-digit', minute:'2-digit'});
    muteControl = `<p class="alert-mute-note">${t('alertRow.mutedUntilPrefix')} ${untilStr} — <button type="button" class="alert-explain-btn" onclick="unmuteAlert('${fullKey}')">${t('alertRow.reactivateNow')}</button></p>`;
  } else if (fullKey) {
    muteControl = `<button type="button" class="alert-explain-btn" onclick="muteAlert('${fullKey}', 4)">${t('alertRow.muteBtn')}</button>`;
  }

  const isRead = fullKey ? isAlertRead(fullKey) : false;
  const readControl = !fullKey ? '' : isRead
    ? `<span class="alert-read-badge">${t('alertRow.readBadge')}</span>`
    : `<button type="button" class="alert-explain-btn" onclick="markAlertRead('${fullKey}')">${t('alertRow.markReadBtn')}</button>`;

  return `
    <div class="alert-row ${paleta}${mutedUntil ? ' muted' : ''}">
      <span class="alert-icon" style="background:${SEV_BG[paleta]}; color:${SEV_COLOR[paleta]}">${iconFor(icon)}</span>
      <div class="body">
        <div class="title">${alertField(a,'title')} ${sevPill}</div>
        <div class="desc">${alertField(a,'desc')}</div>
        ${reasonHtml}
        ${escalationNote}
        ${timeEscalationNote}
        ${explainBtn}
        <div class="alert-actions">${alertConfirmControl(a, fullKey, idx)}${readControl}${muteControl}</div>
      </div>
      <div class="time">${alertField(a,'time')}</div>
    </div>`;
}

// Mostra/esconde a caixa de explicação em linguagem simples de um alerta,
// e atualiza o texto do próprio botão ("O que significa isto?" <->
// "Esconder explicação") para refletir o novo estado. 'btnEl' é o próprio
// botão clicado (passado via 'this' no onclick), para não ter de o
// procurar outra vez no DOM.
function toggleAlertPlain(explainId, btnEl){
  const box = document.getElementById(explainId);
  if (!box) return;
  const nowVisible = box.style.display === 'none';
  box.style.display = nowVisible ? 'block' : 'none';
  btnEl.textContent = t(nowVisible ? 'alert.explainHide' : 'alert.explainShow');
  btnEl.setAttribute('data-i18n', nowVisible ? 'alert.explainHide' : 'alert.explainShow');
}

function legendHtml(){
  return `<div class="legend">${ROUTINE_CATS.map(c => `<span class="legend-item"><span class="legend-swatch" style="background:${c.color}"></span>${c.label}</span>`).join('')}</div>`;
}

