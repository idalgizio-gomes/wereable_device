function statTile(icon, label, value, unit, color, valueId, sim, hintId){
  const valAttr = valueId ? ` id="${valueId}"` : '';
  const simBadge = sim ? ` <span class="sim-flag" title="Classificação de rotina simulada — sem classificador HAR embarcado ainda">simulado</span>` : '';
  // hintId: linha extra escondida por omissão, mostrada por applyLiveVitals() quando não há leitura HR/SpO2 (checkFingerPresentBrief em Ppg.cpp); só usada nesses tiles
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

// fadiga de alerta (RPM): permite silenciar/adiar alertas já vistos. 'critical' nunca pode ser silenciado, só 'serious'/'warning'
const MUTED_ALERTS_KEY = 'carewear_muted_alerts';

// namespaced por paciente ("idPaciente::chaveAlerta") — evita dois pacientes partilharem estado pela mesma chave de alerta
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

// "Marcar como lida": botão explícito em vez do sino desligar sozinho ao abrir a vista; não afeta severidade/escalonamento
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

// atualiza o ponto vermelho da topbar; chamada após login, mudar de paciente, e marcar um alerta como lido
function updateNotificationBadge(){
  const dot = document.getElementById('notifBadgeDot');
  if (!dot) return;
  const hasUnread = unreadActiveAlerts().length > 0;
  dot.style.display = hasUnread ? '' : 'none';
  const bellBtn = document.getElementById('notifBellBtn');
  if (bellBtn) bellBtn.setAttribute('aria-label', hasUnread ? 'Alertas — há alertas por ler' : 'Alertas');
}

// escalonamento gradual por repetição: 'occurrences' (dado de exemplo, sem histórico persistido) sobe só 'warning'->'serious', nunca 'critical'
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

// valor guardado localmente (já ajustado por silenciamento) tem prioridade sobre o dado de exemplo
function occurrencesFor(a, fullKey){
  return (fullKey in alertOccurrences) ? alertOccurrences[fullKey] : (a.occurrences || 1);
}

function alertEscalation(a, fullKey){
  const count = occurrencesFor(a, fullKey);
  // 1) escalonamento por repetição: 'warning' -> 'serious', nunca mais alto
  let severity = a.sev;
  let porRepeticao = false;
  if (a.sev === 'warning' && count >= ALERT_ESCALATION_THRESHOLD) {
    severity = 'serious';
    porRepeticao = true;
  }

  // 2) escalonamento por tempo sem confirmação: alertas reais já chegam com o nível efetivo calculado pelo servidor — o browser não escala por cima
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
  // registo local equivalente a escalated_at/escalated_to_severity, não só mudar a cor
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

// re-renderiza periodicamente p/ o escalonamento por tempo aparecer sozinho, sem recarregar a página
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
  // silenciar reinicia a contagem de escalonamento (tem de se repetir p/ voltar a subir)
  alertOccurrences[fullKey] = 0;
  saveAlertOccurrences(alertOccurrences);
  if (currentView) renderView(currentView);
}
function unmuteAlert(fullKey){
  delete mutedAlerts[fullKey];
  saveMutedAlerts(mutedAlerts);
  if (currentView) renderView(currentView);
}

// RF-07: 4 níveis de severidade, motivo real vindo do bridge, confirmação trava escalonamento, escalonamento por tempo sobe até 'critical'
const ALERT_SEVERITY_LADDER = ['info', 'warning', 'serious', 'critical'];

const ALERT_SEV_PALETTE = {info:'good', warning:'warning', serious:'serious', critical:'critical'};
const ALERT_SEV_LABEL = {info:'Informação', warning:'Aviso', serious:'Sério', critical:'Crítico'};

function alertSevPaletteKey(sev){ return ALERT_SEV_PALETTE[sev] || sev; }
function alertSevLabel(sev){ return ALERT_SEV_LABEL[sev] || sev; }
function alertSeverityRank(sev){ return ALERT_SEVERITY_LADDER.indexOf(sev); }
function alertNextSeverity(sev){
  const i = ALERT_SEVERITY_LADDER.indexOf(sev);
  // falha fechada, igual a vital_alerts.next_severity(): valor desconhecido não escala
  return (i < 0 || i + 1 >= ALERT_SEVERITY_LADDER.length) ? null : ALERT_SEVERITY_LADDER[i + 1];
}

// igual a BleBridge.ALERT_ESCALATION_MINUTES (bridge/ble_bridge.py), sincronizado à mão
const ALERT_TIME_ESCALATION_MINUTES = 15;

// texto ao cuidador usa o valor real do servidor (bridgeEscalationMinutes); a constante acima só para alertas de demonstração
function effectiveEscalationMinutes(){
  return (typeof bridgeEscalationMinutes === 'number' && bridgeEscalationMinutes > 0)
    ? bridgeEscalationMinutes : ALERT_TIME_ESCALATION_MINUTES;
}

// instante em que o alerta foi visto pela 1ª vez por este browser; demonstração não tem carimbo comparável, reais usam created_at
const ALERT_FIRST_SEEN_KEY = 'carewear_alert_first_seen';
// escalonamentos registados (momento + nível), equivalente a escalated_at/escalated_to_severity do bridge
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

// reais: created_at do bridge; demonstração: 1ª vez que este browser os mostrou
function alertClockStart(a, fullKey){
  if (a && a.createdAtMs) return a.createdAtMs;
  if (!fullKey) return null;
  if (!(fullKey in alertFirstSeen)){
    alertFirstSeen[fullKey] = Date.now();
    saveAlertMap(ALERT_FIRST_SEEN_KEY, alertFirstSeen);
  }
  return alertFirstSeen[fullKey];
}

// RF-08: confirmação guarda ação (lista fechada, validada pelo bridge) + nota livre — nota passa SEMPRE por escapeHtml() (já houve XSS real por isto)
const ALERT_CONFIRMATIONS_KEY = 'carewear_alert_confirmations';
let alertConfirmations = loadAlertMap(ALERT_CONFIRMATIONS_KEY);

// valores TÊM de ser iguais a ALERT_RESOLUTION_ACTIONS em bridge/ble_bridge.py, senão o bridge recusa a confirmação
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

// embutido na linha, não é modal — ação de baixo risco, ao contrário do cancelamento de emergência (destrutivo)
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
  markAlertRead(fullKey); // confirmar implica ter visto

  // alertas reais (com uuid) confirmam-se também na BD, onde o escalonamento do servidor consulta se já houve resposta
  if (alertUuid && typeof sendWsCommandWithArgs === 'function'){
    sendWsCommandWithArgs('confirm_alert', {alert_uuid: alertUuid, action, note});
  }
  // cancela também o escalonamento ao contacto de emergência agendado pelo bridge
  const alertaEmergencia = liveEmergencyAlertIdFor(fullKey);
  if (alertaEmergencia && typeof sendWsCommandWithArgs === 'function'){
    sendWsCommandWithArgs('acknowledge_alert', {alert_id: alertaEmergencia});
  }
  if (currentView) renderView(currentView);
}

// reabre um alerta confirmado por engano — ação explícita, não efeito colateral
function reopenAlertConfirmation(fullKey){
  delete alertConfirmations[fullKey];
  saveAlertMap(ALERT_CONFIRMATIONS_KEY, alertConfirmations);
  if (currentView) renderView(currentView);
}

function alertConfirmControl(a, fullKey, idx){
  if (!fullKey) return '';
  const registada = alertConfirmationFor(fullKey);
  if (registada){
    const quando = new Date(registada.at).toLocaleString(currentLang, {
      day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit',
    });
    // escapeHtml() em tudo: nota é texto livre, nome do cuidador vem do perfil editável
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

// 'idx' dá um id único à caixa/botão de explicação em linguagem simples (toggleAlertPlain())
// texto de alerta REAL é sempre escapado (chega por WebSocket sem autenticação); demonstração passa como está (HTML escrito à mão)
function alertTextField(a, field){
  const valor = alertField(a, field);
  if (valor == null) return '';
  return a && a.live ? escapeHtml(valor) : valor;
}

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

  // nível + motivo sempre visíveis, não escondidos atrás de botão
  const sevPill = `<span class="pill ${paleta}" style="background:${SEV_BG[paleta]};color:${SEV_COLOR[paleta]}">${escapeHtml(alertSevLabel(esc.severity))}</span>`;
  const timeEscalationNote = esc.timeEscalated
    ? `<p class="alert-escalation-note">Escalado de "${escapeHtml(alertSevLabel(esc.originalSeverity))}" para "${escapeHtml(alertSevLabel(esc.severity))}"${esc.escalatedAtMs ? ` às ${new Date(esc.escalatedAtMs).toLocaleTimeString(currentLang, {hour:'2-digit', minute:'2-digit'})}` : ''} — sem confirmação ao fim de ${effectiveEscalationMinutes()} min.</p>`
    : '';
  const motivo = alertReasonText(a);
  // estilo em linha, não classe CSS nova: index.html está fora do âmbito desta alteração
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
        <div class="title">${alertTextField(a,'title')} ${sevPill}</div>
        <div class="desc">${alertTextField(a,'desc')}</div>
        ${reasonHtml}
        ${escalationNote}
        ${timeEscalationNote}
        ${explainBtn}
        <div class="alert-actions">${alertConfirmControl(a, fullKey, idx)}${readControl}${muteControl}</div>
      </div>
      <div class="time">${alertField(a,'time')}</div>
    </div>`;
}

// mostra/esconde a explicação simples e atualiza o texto do botão; 'btnEl' vem de 'this' no onclick
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

