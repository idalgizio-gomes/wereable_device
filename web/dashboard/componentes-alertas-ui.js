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
  if (a.sev === 'warning' && count >= ALERT_ESCALATION_THRESHOLD) {
    return {severity:'serious', count, escalated:true};
  }
  return {severity:a.sev, count, escalated:false};
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
  const icon = esc.severity==='critical' ? 'heart' : esc.severity==='serious' ? 'warn' : 'zap';
  const explainId = `alertPlain-${idx}`;
  const explainBtn = a.plain
    ? `<button type="button" class="alert-explain-btn" data-i18n="alert.explainShow" onclick="toggleAlertPlain('${explainId}', this)">${t('alert.explainShow')}</button>
       <div class="alert-explain-box" id="${explainId}" style="display:none;">${alertField(a,'plain')}</div>`
    : '';

  const escalationNote = esc.escalated
    ? `<p class="alert-escalation-note">${t('alertRow.escalationNote', {n: esc.count})}</p>`
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
    <div class="alert-row ${esc.severity}${mutedUntil ? ' muted' : ''}">
      <span class="alert-icon" style="background:${SEV_BG[esc.severity]}; color:${SEV_COLOR[esc.severity]}">${iconFor(icon)}</span>
      <div class="body">
        <div class="title">${alertField(a,'title')}</div>
        <div class="desc">${alertField(a,'desc')}</div>
        ${escalationNote}
        ${explainBtn}
        <div class="alert-actions">${readControl}${muteControl}</div>
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

