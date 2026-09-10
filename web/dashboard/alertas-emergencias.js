/* Alertas, anomalias e emergencias - extraido de pacientes-alertas-medicacao.js */

// alerta.plain = explicação em linguagem simples p/ familiares, mostrada por "O que significa isto?" em alertRow()
// alerts/anomalyLog/estado do dispositivo vêm sempre de selectedPatient() (currentAlerts()/currentAnomalyLog())
// LIMITAÇÃO: selecionar paciente aqui não troca a ligação BLE real, que serve um único dispositivo de cada vez (ble_bridge.py)
const DELETED_ALERTS_KEY = 'carewear_deleted_alerts';

function loadDeletedAlerts(){
  try {
    const raw = localStorage.getItem(DELETED_ALERTS_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  return {};
}
function saveDeletedAlerts(map){
  try { localStorage.setItem(DELETED_ALERTS_KEY, JSON.stringify(map)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
}
let deletedAlertsMap = loadDeletedAlerts();
function isAlertDeleted(fullKey){ return !!deletedAlertsMap[fullKey]; }
// apagar não é decisão do utente/família, só da equipa clínica
function deleteAlert(fullKey){
  if (currentRole === 'utente') return;
  deletedAlertsMap[fullKey] = true;
  saveDeletedAlerts(deletedAlertsMap);
  updateNotificationBadge();
  if (currentView) renderView(currentView);
}
function clearAllAlertsForPatient(){
  if (currentRole === 'utente') return;
  currentAlerts().forEach(a => { deletedAlertsMap[patientAlertKey(selectedPatientId, a.key)] = true; });
  saveDeletedAlerts(deletedAlertsMap);
  updateNotificationBadge();
  if (currentView) renderView(currentView);
}

// apagar anomalias/emergências: mesmo padrão de "marcar em localStorage" de DELETED_ALERTS_KEY, nunca remove dos dados de origem
const DELETED_ANOMALIES_KEY = 'carewear_deleted_anomalies';
const DELETED_EMERGENCIES_KEY = 'carewear_deleted_emergencies';

function loadDeletedMap(key){
  try {
    const raw = localStorage.getItem(key);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  return {};
}
function saveDeletedMap(key, map){
  try { localStorage.setItem(key, JSON.stringify(map)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
}
let deletedAnomaliesMap = loadDeletedMap(DELETED_ANOMALIES_KEY);
let deletedEmergenciesMap = loadDeletedMap(DELETED_EMERGENCIES_KEY);

// defesa em profundidade: recusa diretamente, não confia só em esconder o botão
function deleteAnomaly(anomalyId){
  if (currentRole === 'utente') return;
  const fullKey = `${selectedPatientId}:${anomalyId}`;
  deletedAnomaliesMap[fullKey] = true;
  saveDeletedMap(DELETED_ANOMALIES_KEY, deletedAnomaliesMap);
  if (currentView) renderView(currentView);
}
function deleteEmergencyRecord(emergencyId){
  if (currentRole === 'utente') return;
  const fullKey = `${selectedPatientId}:${emergencyId}`;
  deletedEmergenciesMap[fullKey] = true;
  saveDeletedMap(DELETED_EMERGENCIES_KEY, deletedEmergenciesMap);
  if (currentView) renderView(currentView);
}
// mesmo padrão de clearAllAlertsForPatient(); emergências ativas ficam de fora, têm de ser canceladas primeiro
function clearAllAnomaliesForPatient(){
  if (currentRole === 'utente') return;
  currentAnomalyLog().forEach(a => { deletedAnomaliesMap[`${selectedPatientId}:${a.id}`] = true; });
  saveDeletedMap(DELETED_ANOMALIES_KEY, deletedAnomaliesMap);
  if (currentView) renderView(currentView);
}
function clearAllEmergenciesForPatient(){
  if (currentRole === 'utente') return;
  currentEmergencyLog().filter(e => e.status !== 'ativo').forEach(e => { deletedEmergenciesMap[`${selectedPatientId}:${e.id}`] = true; });
  saveDeletedMap(DELETED_EMERGENCIES_KEY, deletedEmergenciesMap);
  if (currentView) renderView(currentView);
}

// RGPD art. 17: apaga dados locais por prefixo (não afeta bridge/carewear_history.db nem o próprio dispositivo)

// RF-07: alertas reais da tabela `alerts` do bridge (cmd "get_alerts"), fundidos com os de demonstração; distinguem-se por live:true
let bridgeAlerts = [];

// converte um alerta da BD para a forma que alertRow()/tabela de histórico desenham
function bridgeAlertToRow(raw){
  const criadoMs = raw.created_at ? Date.parse(raw.created_at + 'Z') : null;
  const escaladoMs = raw.escalated_at ? Date.parse(raw.escalated_at + 'Z') : null;
  return {
    key: `live-${raw.uuid}`,
    alertUuid: raw.uuid,
    live: true,
    sev: raw.severity,
    effectiveSeverity: raw.effective_severity || raw.severity,
    escalatedAt: raw.escalated_at || null,
    escalatedAtMs: Number.isFinite(escaladoMs) ? escaladoMs : null,
    createdAtMs: Number.isFinite(criadoMs) ? criadoMs : null,
    title: raw.title || 'Alerta',
    desc: raw.reason || '',
    reason: raw.reason || '',
    resolutionNote: raw.resolution_note || null,
    resolvedAt: raw.resolved_at || null,
    time: Number.isFinite(criadoMs)
      ? new Date(criadoMs).toLocaleString(currentLang, {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'})
      : '',
  };
}

// reais + demonstração, sem apagados; reais primeiro por serem mais recentes
function currentAlerts(){
  const reais = bridgeAlerts.filter(a => !isAlertDeleted(patientAlertKey(selectedPatientId, a.key)));
  const demo = selectedPatient().alerts.filter(a => !isAlertDeleted(patientAlertKey(selectedPatientId, a.key)));
  return reais.concat(demo);
}

// alertas reais: frase composta pelo bridge (explain_vital_alert/explain_wear_state); demonstração: usa a descrição técnica
function alertReasonText(a){
  if (!a) return '';
  if (a.reason) return a.reason;
  return alertField(a, 'desc') || '';
}

// mapeia para o alert_id ("{alert_type}-{seq}") do EscalationManager do bridge; só emergências reais têm um
function liveEmergencyAlertIdFor(fullKey){
  if (!fullKey) return null;
  const chave = String(fullKey).split('::').pop();
  const entrada = currentEmergencyLog().find(e => e.live && `live-${e.liveSeq}` === chave);
  return entrada && entrada.liveSeq != null ? `${entrada.type}-${entrada.liveSeq}` : null;
}
function currentAnomalyLog(){
  return selectedPatient().anomalyLog.filter(a => !deletedAnomaliesMap[`${selectedPatientId}:${a.id}`]);
}

// tradução de alertas/anomalias/emergências: resolve texto via chave estável (a.key/a.id/e.type) num namespace i18n, com fallback para o PT-fixo dos dados de demonstração
const ALERT_KEY_TO_I18N_SEGMENT = {
  'hr-alta': 'hrAlta', 'inatividade-prolongada': 'inatividadeProlongada', 'rotina-alterada': 'rotinaAlterada',
  'spo2-limite': 'spo2Limite', 'sono-curto': 'sonoCurto',
};
function alertField(a, field){
  const seg = ALERT_KEY_TO_I18N_SEGMENT[a.key];
  if (!seg) return a[field];
  const i18nKey = `alertData.${seg}.${field}`;
  const val = t(i18nKey);
  return val === i18nKey ? a[field] : val;
}
const ANOMALY_TYPE_TO_I18N_SEGMENT = { 'Duração': 'duracao', 'Comportamental': 'comportamental', 'Fisiológica': 'fisiologica' };
const ANOMALY_DETECTOR_TO_I18N_SEGMENT = {
  'Regra de duração': 'regraDuracao', 'LSTM Autoencoder': 'lstm', 'Limiar clínico': 'limiarClinico', 'Diagnóstico do dispositivo': 'diagnosticoDispositivo',
};
function anomalyTypeText(a){
  const seg = ANOMALY_TYPE_TO_I18N_SEGMENT[a.type];
  return seg ? t(`anomalyType.${seg}`) : a.type;
}
function anomalyDetectorText(a){
  const seg = ANOMALY_DETECTOR_TO_I18N_SEGMENT[a.detector];
  return seg ? t(`anomalyDetector.${seg}`) : a.detector;
}
function anomalyDetailText(a){
  const i18nKey = `anomalyDetail.a${String(a.id).replace(/^A-/i, '').toLowerCase()}`;
  const val = t(i18nKey);
  return val === i18nKey ? a.detail : val;
}
function emergencyLabelText(e){
  const i18nKey = e.type === 'sos' ? 'emergencyType.sos' : e.type === 'fall' ? 'emergencyType.fall' : 'emergencyType.unknown';
  const val = t(i18nKey);
  return val === i18nKey ? (e.label || val) : val;
}
function emergencyNoteText(e){
  const i18nKey = `emergencyNote.e${String(e.id).replace(/^E-/i, '').toLowerCase()}`;
  const val = t(i18nKey);
  return val === i18nKey ? e.resolvedNote : val;
}

// alertas não lidos/não apagados — usado em "Alertas recentes" e "Alertas por severidade"
function unreadActiveAlerts(){
  return currentAlerts().filter(a => !isAlertRead(patientAlertKey(selectedPatientId, a.key)));
}

// nº de alertas "ativos" = não silenciado E não lido; calculado, não guardado, para nunca dessincronizar da tabela
function activeAlertsCount(patient){
  return patient.alerts.filter(a =>
    !alertMutedUntil(patientAlertKey(patient.id, a.key)) &&
    !isAlertRead(patientAlertKey(patient.id, a.key))
  ).length;
}

const EMERGENCY_LOG = {
  p1: [
    {id:'E-204', type:'fall', label:'Queda + inatividade prolongada', time:'02/07/2026 21:14', status:'resolvido', resolvedNote:'Confirmado falso alarme pela família por telefone.'},
    {id:'E-198', type:'sos', label:'SOS manual (3 cliques)', time:'28/06/2026 11:02', status:'resolvido', resolvedNote:'Utente pediu ajuda para se levantar, sem gravidade.'},
  ],
  p2: [],
  p3: [
    {id:'E-150', type:'fall', label:'Queda + inatividade prolongada', time:'25/06/2026 03:40', status:'ativo'},
  ],
};

function currentEmergencyLog(){
  return (EMERGENCY_LOG[selectedPatientId] || []).filter(e => !deletedEmergenciesMap[`${selectedPatientId}:${e.id}`]);
}

// mapeia EmergencyAlertType (Ble.h: 1=SOS manual, 2=queda+inatividade) para as categorias de demonstração
const EMERGENCY_ALERT_TYPE_TO_LOG = {
  sos_manual: { type: 'sos', label: 'SOS manual (cliques)' },
  fall_inactivity: { type: 'fall', label: 'Queda + inatividade prolongada' },
};

// chamado por handleBridgeMessage() ao chegar alerta via emergencyAlertChar; atribuído ao paciente selecionado (limitação: 1 dispositivo físico)
function onLiveEmergencyAlert(msg){
  const seq = toFiniteNumber(msg.seq);
  const p = selectedPatient();
  const log = EMERGENCY_LOG[p.id] || (EMERGENCY_LOG[p.id] = []);

  // dedup por 'seq' (incrementa no firmware), não por tempo — notificação BLE pode chegar duplicada
  if (seq != null && log.some(e => e.liveSeq === seq)) return;

  const meta = EMERGENCY_ALERT_TYPE_TO_LOG[msg.alert_name]
    || { type: 'unknown', label: t('emergencyType.unknown') };
  const ts = toFiniteNumber(msg.timestamp_utc);
  const timeLabel = ts
    ? new Date(ts * 1000).toLocaleString(currentLang, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
    : t('emergencyLive.nowLabel');

  log.unshift({
    id: `E-LIVE-${seq != null ? seq : Date.now()}`,
    type: meta.type,
    label: meta.label,
    time: timeLabel,
    status: 'ativo',
    liveSeq: seq,
    live: true,
    // explicação composta pelo bridge (EMERGENCY_ALERT_EXPLANATIONS), não um valor medido
    explanation: typeof msg.explanation === 'string' ? msg.explanation : null,
  });

  updateLiveEmergencyBanner();
  if (currentView === 'emergencias') renderView('emergencias');
}

// mostra/esconde a barra de emergência em direto consoante alertas 'ativo' reais do paciente selecionado
function updateLiveEmergencyBanner(){
  const el = document.getElementById('emergencyLiveBanner');
  if (!el) return;
  const active = currentEmergencyLog().filter(e => e.live && e.status === 'ativo');
  if (!active.length) { el.style.display = 'none'; return; }
  const label = emergencyLabelText(active[0]);
  el.style.display = 'flex';
  el.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.3 3.9 1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg>
    <span><b>${t('emergencyLive.bannerTitle')}</b> — ${label} (${escapeHtml(selectedPatient().name)}). <a href="#" onclick="renderView('emergencias'); return false;">${t('emergencyLive.viewLogLink')}</a></span>`;
}

// código de 6 dígitos só em memória, nunca enviado — não há integração SMS/email real, demonstra só o FLUXO de confirmação reforçada
let emergencyCancelState = null;

// TTL curto + limite de tentativas que bloqueia mesmo (padrão OTP por SMS)
const EMERGENCY_CODE_TTL_MS = 5 * 60 * 1000;
const EMERGENCY_MAX_ATTEMPTS = 3;

function openEmergencyCancelModal(emergencyId){
  const code = String(Math.floor(100000 + Math.random() * 900000));
  emergencyCancelState = { emergencyId, code, attempts: 0, expiresAt: Date.now() + EMERGENCY_CODE_TTL_MS };
  document.getElementById('emergencyCancelCodeDisplay').textContent = code;
  document.getElementById('emergencyCancelPassword').value = '';
  document.getElementById('emergencyCancelCodeInput').value = '';
  document.getElementById('emergencyCancelStatus').textContent = '';
  document.getElementById('emergencyCancelStatus').className = 'modal-status';
  document.getElementById('emergencyCancelOverlay').style.display = 'flex';
}
function closeEmergencyCancelModal(){
  document.getElementById('emergencyCancelOverlay').style.display = 'none';
  emergencyCancelState = null;
}

// pede ao bridge (cmd "get_episode_timeline") sinais vitais/atividade/alertas à volta de UM alerta real (só entradas com liveSeq)
function openEpisodeTimelineModal(sequenceNumber){
  const body = document.getElementById('episodeTimelineBody');
  document.getElementById('episodeTimelineOverlay').style.display = 'flex';
  if (!bridgeWs || bridgeWs.readyState !== WebSocket.OPEN){
    body.innerHTML = `<p class="empty-hint">${t('episodio.noBridgeConnectionHint')}</p>`;
    return;
  }
  body.innerHTML = `<p class="empty-hint">${t('episodio.loadingHint')}</p>`;
  sendWsCommandWithArgs('get_episode_timeline', { sequence_number: sequenceNumber, window_minutes: 30 });
}

function closeEpisodeTimelineModal(){
  document.getElementById('episodeTimelineOverlay').style.display = 'none';
}

function handleEpisodeTimelineResult(msg){
  const body = document.getElementById('episodeTimelineBody');
  if (!body || document.getElementById('episodeTimelineOverlay').style.display === 'none') return;
  if (!msg.timeline){
    body.innerHTML = `<p class="empty-hint">${t('episodio.errorPrefix')} ${escapeHtml(msg.error || t('episodio.unknownError'))}.</p>`;
    return;
  }
  renderEpisodeTimeline(msg.timeline, body);
}

// junta sensor_summary + activity_blocks + nearby_emergency_alerts numa lista única ordenada no tempo
function renderEpisodeTimeline(timeline, body){
  const centerLabel = new Date(timeline.center_ts * 1000).toLocaleString(currentLang, {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
  const fmtTime = ts => new Date(ts * 1000).toLocaleTimeString(currentLang, { hour: '2-digit', minute: '2-digit' });

  const items = [];
  (timeline.sensor_summary || []).forEach(p => {
    const parts = [];
    if (p.hr != null) parts.push(`${p.hr} bpm`);
    if (p.spo2 != null) parts.push(`SpO₂ ${p.spo2}%`);
    if (parts.length) items.push({ ts: p.ts, kind: 'vital', label: parts.join(' · ') });
  });
  (timeline.activity_blocks || []).forEach(b => {
    items.push({
      ts: b.start_ts_approx, kind: 'activity',
      label: `${escapeHtml(b.category)} (${Math.round(b.duration_minutes || 0)} min)`,
    });
  });
  (timeline.nearby_emergency_alerts || []).forEach(a => {
    items.push({ ts: a.timestamp_utc, kind: 'alert', label: escapeHtml(a.alert_type) });
  });
  items.sort((a, b) => a.ts - b.ts);

  const badgeStyle = {
    vital: 'background:var(--bg-surface-2); color:var(--text-secondary);',
    activity: 'background:color-mix(in srgb, var(--accent) 18%, transparent); color:var(--accent);',
    alert: 'background:var(--status-critical-bg); color:var(--status-critical);',
  };
  const badgeLabel = { vital: t('episodio.badgeVital'), activity: t('episodio.badgeActivity'), alert: t('episodio.badgeAlert') };

  const listHtml = items.length ? items.map(it => `
    <div class="episode-timeline-row">
      <span class="et-time">${fmtTime(it.ts)}</span>
      <span class="et-badge" style="${badgeStyle[it.kind]}">${badgeLabel[it.kind]}</span>
      <span>${it.label}</span>
    </div>
  `).join('') : `<p class="empty-hint">${t('episodio.emptyHint')}</p>`;

  body.innerHTML = `
    <p class="empty-hint" style="margin:0 0 10px;">${t('episodio.centeredOnPrefix')} <b>${centerLabel}</b> (±${timeline.window_minutes} min)</p>
    <div class="episode-timeline-section">${listHtml}</div>
  `;
}
function confirmEmergencyCancel(){
  const status = document.getElementById('emergencyCancelStatus');
  if (!emergencyCancelState) return;

  // bloqueia mesmo (return antes de validar) — não só um aviso visual
  if (emergencyCancelState.attempts >= EMERGENCY_MAX_ATTEMPTS) {
    status.className = 'modal-status err';
    status.textContent = 'Demasiadas tentativas incorretas — fecha e reabre para gerar um novo código.';
    return;
  }
  if (Date.now() > emergencyCancelState.expiresAt) {
    status.className = 'modal-status err';
    status.textContent = 'Código expirado (validade de 5 minutos) — fecha e reabre para gerar um novo.';
    return;
  }

  const password = document.getElementById('emergencyCancelPassword').value;
  const codeInput = document.getElementById('emergencyCancelCodeInput').value.trim();

  if (!password) {
    status.className = 'modal-status err';
    status.textContent = 'Introduz a tua palavra-passe para confirmar a tua identidade.';
    return;
  }
  if (codeInput !== emergencyCancelState.code) {
    emergencyCancelState.attempts++;
    const remaining = EMERGENCY_MAX_ATTEMPTS - emergencyCancelState.attempts;
    status.className = 'modal-status err';
    status.textContent = remaining > 0
      ? `Código incorreto (${remaining} tentativa${remaining>1?'s':''} restante${remaining>1?'s':''}). Confirma o código mostrado acima.`
      : 'Código incorreto. Sem mais tentativas — fecha e reabre para gerar um novo código.';
    return;
  }

  const entry = currentEmergencyLog().find(e => e.id === emergencyCancelState.emergencyId);
  if (entry) {
    entry.status = 'cancelado';
    entry.resolvedNote = `Cancelado manualmente por ${document.getElementById('avatarName').textContent} em ${new Date().toLocaleString('pt-PT')}, após confirmação reforçada.`;
  }
  status.className = 'modal-status ok';
  status.textContent = 'Alerta cancelado e registado.';
  updateLiveEmergencyBanner();
  setTimeout(() => { closeEmergencyCancelModal(); if (currentView) renderView(currentView); }, 1200);
}

// medications/adherenceHistory são exemplo; só a toma de HOJE é real (localStorage, namespaced por paciente+dia+medicamento+horário)

// embrulha handleBridgeMessage() (declarada em bridge-exportacao.js) com os kinds novos do RF-05/07/08, em vez de editar esse ficheiro
// só pode embrulhar depois de 'load': este script corre antes de bridge-exportacao.js, handleBridgeMessage ainda não existe

// cadência de reconsulta ao bridge; serve também de recuperação após queda de ligação
const BRIDGE_ALERTS_REFRESH_MS = 30000;

// minutos até o bridge escalar por falta de confirmação; o servidor manda, isto é só o valor assumido antes de ele responder
let bridgeEscalationMinutes = null;

function requestBridgeAlerts(){
  if (typeof sendWsCommandWithArgs !== 'function') return false;
  // limite explícito em vez de depender do default do bridge (ALERT_LIST_MAX)
  return sendWsCommandWithArgs('get_alerts', {limit: 100});
}

// substitui/insere um alerta local a partir da forma serializada pelo bridge (_alert_to_dict)
function upsertBridgeAlert(raw){
  if (!raw || !raw.uuid) return;
  const linha = bridgeAlertToRow(raw);
  const i = bridgeAlerts.findIndex(a => a.alertUuid === raw.uuid);
  if (i >= 0) bridgeAlerts[i] = linha; else bridgeAlerts.unshift(linha);
}

// RF-05: bridge difunde {kind:"wear_status",...} só quando o estado MUDA (evento, não polling) — guardado aqui p/ sobreviver a re-renderização
// distinção 'removed' (recolocar) vs 'link_lost' (aproximar/carregar) é intencional: ações diferentes p/ o cuidador
let wearState = null;

// estado -> apresentação; paleta é a de templates-core.js (SEV_COLOR/SEV_BG usam 'good', não 'info')
const WEAR_STATE_UI = {
  worn:      {palette:'good',    icon:'heart', label:'Dispositivo a ser usado'},
  removed:   {palette:'warning', icon:'warn',  label:'Dispositivo removido'},
  link_lost: {palette:'serious', icon:'zap',   label:'Ligação ao wearable perdida'},
  unknown:   {palette:'good',    icon:'zap',   label:'Estado de uso ainda desconhecido'},
};

function wearStateUi(state){ return WEAR_STATE_UI[state] || WEAR_STATE_UI.unknown; }

// texto p/ o cuidador (o que fazer); `explanation` do bridge diz o que foi medido — os dois aparecem juntos
const WEAR_STATE_HINT = {
  worn: 'Há sinal cutâneo e/ou movimento — a monitorização está a decorrer normalmente.',
  removed: 'O wearable continua ligado ao bridge, por isso não é uma falha de comunicação: está a comunicar mas não está no pulso. Volte a colocá-lo para retomar a monitorização de sinais vitais.',
  link_lost: 'Não é o mesmo que "removido": aqui o wearable deixou simplesmente de comunicar, e sem dados não é possível saber se está ou não no pulso. Verifique a distância ao bridge, a bateria e se o Bluetooth está ligado.',
  unknown: 'Ainda não há observações suficientes desde o arranque para classificar o estado de uso.',
};

function renderWearStatusCard(){
  const host = document.getElementById('wearStatusPanel');
  if (!host) return;
  const estado = wearState ? wearState.state : 'unknown';
  const ui = wearStateUi(estado);
  const cor = SEV_COLOR[ui.palette], fundo = SEV_BG[ui.palette];

  // escapado: 'explanation' vem do bridge, canal sem autenticação
  const motivo = wearState && wearState.explanation
    ? '<div class="alert-reason" style="font-size:.82rem;color:var(--text-secondary);margin-top:6px;"><b>Motivo:</b> ' + escapeHtml(wearState.explanation) + '</div>'
    : '';

  const desde = wearState && Number.isFinite(wearState.atMs)
    ? '<div class="alert-mute-note">Desde ' + escapeHtml(new Date(wearState.atMs).toLocaleString(currentLang, {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'})) + '</div>'
    : '';

  // contadores só existem em 'removed'; em 'link_lost' o bridge manda null (sem amostras)
  const semPele = wearState && wearState.secondsWithoutSkin != null
    ? Math.round(wearState.secondsWithoutSkin / 60) : null;
  const semMovimento = wearState && wearState.secondsWithoutMotion != null
    ? Math.round(wearState.secondsWithoutMotion / 60) : null;
  const contadores = (semPele != null || semMovimento != null)
    ? '<div class="activity-stat-row">'
      + '<div class="activity-stat"><div class="n tabular">' + (semPele != null ? semPele + ' min' : '—') + '</div><div class="l">Sem sinal cutâneo (PPG/SpO2)</div></div>'
      + '<div class="activity-stat"><div class="n tabular">' + (semMovimento != null ? semMovimento + ' min' : '—') + '</div><div class="l">Sem movimento (acelerómetro)</div></div>'
      + '</div>'
    : '';

  host.innerHTML =
    '<div class="alert-row ' + ui.palette + '">'
    + '<span class="alert-icon" style="background:' + fundo + ';color:' + cor + '">' + iconFor(ui.icon) + '</span>'
    + '<div class="body">'
    + '<div class="title">' + escapeHtml(ui.label) + ' <span class="pill ' + ui.palette + '" style="background:' + fundo + ';color:' + cor + '">' + escapeHtml(ui.label) + '</span></div>'
    + '<div class="desc">' + escapeHtml(WEAR_STATE_HINT[estado] || WEAR_STATE_HINT.unknown) + '</div>'
    + motivo + contadores + desde
    + '</div></div>';
}

// separada de renderWearStatusCard() para poder ser chamada/testada sem DOM
function applyWearStatus(msg){
  const estados = ['worn', 'removed', 'link_lost', 'unknown'];
  // canal sem autenticação: valida a forma antes de confiar
  if (!msg || estados.indexOf(msg.state) === -1) return;
  wearState = {
    state: msg.state,
    previousState: estados.indexOf(msg.previous_state) >= 0 ? msg.previous_state : null,
    explanation: typeof msg.explanation === 'string' ? msg.explanation : '',
    secondsWithoutSkin: toFiniteNumber(msg.seconds_without_skin),
    secondsWithoutMotion: toFiniteNumber(msg.seconds_without_motion),
    alertUuid: typeof msg.alert_uuid === 'string' ? msg.alert_uuid : null,
    atMs: Date.now(),
  };
  renderWearStatusCard();
}

// RF-08: histórico consultável sem voltar ao alerta de origem; reúne alertas reais resolvidos (bridge) + de demonstração (alertConfirmations)

// separa ação/nota: o bridge grava ambas na mesma coluna com prefixo "[ação] " (_format_resolution_note)
function parseResolutionNote(nota){
  if (!nota) return {action: null, note: ''};
  const m = /^\[([a-z_]+)\]\s?([\s\S]*)$/.exec(String(nota));
  if (!m) return {action: null, note: String(nota)};
  return {action: m[1], note: m[2] || ''};
}

// todas as ações registadas, mais recentes primeiro
function alertActionsHistory(){
  const entradas = [];
  bridgeAlerts.forEach(a => {
    if (!a.resolvedAt || !a.resolutionNote) return;
    const partes = parseResolutionNote(a.resolutionNote);
    const ms = Date.parse(a.resolvedAt + 'Z');
    entradas.push({
      title: a.title, live: true, action: partes.action, note: partes.note,
      atMs: Number.isFinite(ms) ? ms : null, by: '',
    });
  });
  // chave inclui o paciente — filtra pelo selecionado para não misturar históricos
  const prefixo = selectedPatientId + '::';
  Object.keys(alertConfirmations || {}).forEach(fullKey => {
    if (fullKey.indexOf(prefixo) !== 0) return;
    const c = alertConfirmations[fullKey];
    const chave = fullKey.slice(prefixo.length);
    if (chave.indexOf('live-') === 0) return; // já entrou acima pela BD, evita duplicar
    const alerta = (selectedPatient().alerts || []).find(a => a.key === chave);
    entradas.push({
      title: alerta ? alertField(alerta, 'title') : chave,
      live: false, action: c.action, note: c.note || '',
      atMs: c.at || null, by: c.by || '',
    });
  });
  return entradas.sort((a, b) => (b.atMs || 0) - (a.atMs || 0));
}

function renderAlertActionsHistory(){
  const host = document.getElementById('alertActionsHistory');
  if (!host) return;
  const entradas = alertActionsHistory();
  if (!entradas.length){
    host.innerHTML = '<p class="empty-hint">Ainda não foi registada nenhuma ação. Assim que confirmar um alerta indicando o que fez, fica aqui.</p>';
    return;
  }
  // escapeHtml() em tudo: nota é texto livre, título vem do bridge (ou de i18n, mas escapa-se na mesma)
  host.innerHTML =
    '<div class="activity-blocks-list">'
    + entradas.map(e =>
        '<div class="activity-block-row" style="align-items:flex-start;">'
        + '<span class="tabular">' + (e.atMs ? escapeHtml(new Date(e.atMs).toLocaleString(currentLang, {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'})) : '—') + '</span>'
        + '<span style="flex:1;">'
        + '<b>' + escapeHtml(e.title || 'Alerta') + '</b> — ' + escapeHtml(alertActionLabel(e.action))
        + (e.live ? '' : ' <span class="sim-flag">demonstração</span>')
        + (e.note ? '<div class="alert-mute-note">' + escapeHtml(e.note) + '</div>' : '')
        + (e.by ? '<div class="alert-mute-note">Registado por ' + escapeHtml(e.by) + '</div>' : '')
        + '</span></div>').join('')
    + '</div>';
}

// só re-renderiza a vista inteira se estiver montada — mensagens do bridge chegam a qualquer momento
function refreshAlertViews(){
  renderWearStatusCard();
  renderAlertActionsHistory();
  if (typeof updateNotificationBadge === 'function') updateNotificationBadge();
  if (typeof currentView !== 'undefined' && currentView && typeof renderView === 'function') renderView(currentView);
}

// ramos novos de handleBridgeMessage
function handleAlertsBridgeMessage(msg){
  if (!msg) return false;

  if (msg.kind === 'alerts'){
    // substitui, não funde — a BD é fonte de verdade, um alerta apagado (deleted_at) tem de desaparecer daqui
    bridgeAlerts = Array.isArray(msg.alerts) ? msg.alerts.map(bridgeAlertToRow) : [];
    const min = toFiniteNumber(msg.escalation_minutes);
    if (min != null) bridgeEscalationMinutes = min;
    refreshAlertViews();
    return true;
  }

  if (msg.kind === 'alert_escalated'){
    // decidido no servidor (periodic_alert_escalation_task) — aqui só reflete escalated_to_severity/escalated_at
    upsertBridgeAlert(msg.alert);
    refreshAlertViews();
    return true;
  }

  if (msg.kind === 'confirm_alert_result'){
    // numa recusa NÃO se toca no estado local — melhor não mostrar nada do que mostrar confirmado algo recusado
    if (msg.ok && msg.alert){
      upsertBridgeAlert(msg.alert);
      refreshAlertViews();
    } else if (!msg.ok){
      console.warn('[CareWear] confirmacao de alerta recusada pelo bridge:', msg.error);
    }
    return true;
  }

  if (msg.kind === 'wear_status'){
    applyWearStatus(msg);
    return true;
  }

  if (msg.kind === 'vital_alert' || msg.kind === 'emergency_alert'){
    // já tratados pelo handler original; falta só repedir a lista p/ a linha em `alerts` (não se fabrica uuid aqui)
    requestBridgeAlerts();
    return false;
  }

  if (msg.kind === 'device_status' && msg.connected){
    requestBridgeAlerts();
    return false;
  }

  return false;
}

// flag evita instalar duas vezes: 'load' pode disparar mais que uma vez com bfcache
let alertsBridgeHookInstalled = false;

function installAlertsBridgeHook(){
  if (alertsBridgeHookInstalled) return;
  if (typeof handleBridgeMessage !== 'function') return;
  alertsBridgeHookInstalled = true;
  const original = handleBridgeMessage;
  handleBridgeMessage = function(msg){
    // handler original corre sempre primeiro — um alerta mal formado não pode afetar os dados ao vivo
    original(msg);
    try { handleAlertsBridgeMessage(msg); }
    catch (e) { console.warn('[CareWear] erro a tratar alerta do bridge:', e); }
  };
  requestBridgeAlerts();
  setInterval(requestBridgeAlerts, BRIDGE_ALERTS_REFRESH_MS);
}

window.addEventListener('load', installAlertsBridgeHook);
