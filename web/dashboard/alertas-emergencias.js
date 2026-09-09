/* Alertas, anomalias e emergencias - extraido de pacientes-alertas-medicacao.js */


// Cada alerta tem, além da descrição técnica ("desc"), um campo "plain":
// uma explicação em linguagem simples, pensada para um familiar sem
// formação clínica — o que aconteceu, se é normalmente preocupante, e o
// que costuma justificar este tipo de leitura. É mostrada/escondida pelo
// botão "O que significa isto?" em alertRow() (ver mais abaixo). Esta
// funcionalidade corresponde ao item nº1 do backlog de investigação em
// PROJECT_STATUS.md — a explicação em linguagem simples é o que a
// literatura revista aponta como o que traz mais valor percebido às
// famílias, mais do que apenas mostrar scores técnicos.
/* ------------------------------------------------------------
   PACIENTES DO MÉDICO/TÉCNICO (protótipo, dados fictícios)
   ------------------------------------------------------------
   Um médico/técnico tem, na realidade, vários pacientes/wearables
   emparelhados na mesma conta. Cada paciente tem os seus PRÓPRIOS
   alertas, registo de anomalias e estatísticas de dispositivo (bateria,
   ocupação do ring buffer) — BUG CORRIGIDO (2026-07-03, reportado pelo
   utilizador): antes, mudar de paciente na vista "Pacientes" não mudava
   os dados mostrados no "Registo de anomalias" nem em "Dispositivo &
   firmware", que continuavam sempre a mostrar os valores fixos da Maria
   Silva. Agora `alerts`/`anomalyLog`/estado do dispositivo vêm sempre de
   `selectedPatient()` (ver currentAlerts()/currentAnomalyLog() abaixo).
   NOTA HONESTA: RAM/flash de programa (.data/.bss e tamanho do binário)
   são os MESMOS para todos os pacientes de propósito — é o mesmo
   firmware instalado em todos os wearables, por isso esses dois valores
   não deviam variar por paciente (só bateria e ocupação do ring buffer,
   que dependem do uso real de cada dispositivo, fazem sentido variar).
   LIMITAÇÃO HONESTA (continua a aplicar-se): selecionar aqui muda a
   identidade/dados apresentados nesta conta, mas a ligação BLE real
   continua limitada a um único dispositivo físico de cada vez — o
   bridge (ble_bridge.py) ainda não suporta escolher/alternar entre
   vários dispositivos por MAC (ver PROJECT_STATUS.md, backlog).
------------------------------------------------------------ */
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
// Bug de permissão corrigido (2026-07-16): mesmo motivo dos guards em
// deleteAnomaly()/deleteEmergencyRecord() abaixo — apagar não é decisão
// do utente/família, só da equipa clínica.
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

/* ------------------------------------------------------------
   APAGAR ANOMALIAS E EMERGÊNCIAS INDIVIDUALMENTE (2026-07-15)
   ------------------------------------------------------------
   Mesmo padrão de "apagar por chave, filtrar na leitura" já usado para
   alertas (ver DELETED_ALERTS_KEY acima) — nunca se remove do array de
   dados de origem (PATIENTS/EMERGENCY_LOG ou demo-data.js), só se marca
   como apagado num mapa em localStorage, para sobreviver a
   re-renderizações e a trocas de dados diárias do simulador.
------------------------------------------------------------ */
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

// Bug de permissão corrigido (2026-07-16, reportado pelo utilizador):
// apagar o próprio registo de anomalias/emergências não é uma decisão
// que caiba ao utente/família — só a equipa clínica deve poder fazê-lo.
// Os botões já estavam escondidos para utente na maior parte dos casos
// (ver TEMPLATES.anomalias/emergencias), mas o botão individual de
// apagar emergência tinha escapado a essa guarda. Em vez de confiar só
// em esconder o botão, estas funções recusam agora diretamente
// (defesa em profundidade — mesmo padrão já usado em selectPatient()).
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
// "Limpar tudo" — mesmo padrão de clearAllAlertsForPatient(), pedido do
// utilizador depois de reparar que só existia para alertas. Emergências
// ativas ficam de fora (mesma regra de segurança de deleteEmergencyRecord:
// têm de ser canceladas primeiro, não apagadas diretamente).
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

/* ------------------------------------------------------------
   DIREITO AO ESQUECIMENTO (RGPD art. 17) — apagar dados locais
   ------------------------------------------------------------
   Antes desta função não havia nenhuma forma de apagar de uma vez os
   dados pessoais que este dashboard guarda no localStorage do browser
   (perfil com NIF/morada, consentimento, medicação, notas de
   cuidadores, alertas lidos/apagados/silenciados) — logout() só troca
   de ecrã, nunca limpou nada. Varre por prefixo em vez de listar
   chaves à mão, para não ficar desatualizada quando surgir uma chave
   nova (ex.: 'carewear_adherence_analytics_<patientId>', sufixo
   dinâmico por paciente, em medication-reminders.js). Âmbito
   deliberadamente limitado a este browser: não apaga o histórico do
   bridge (bridge/carewear_history.db, ver "Retenção de dados" na vista
   Exportar) nem os registos guardados no próprio dispositivo (ver
   "Repor leituras" abaixo) — quem quiser apagar tudo tem de usar as
   três opções.
------------------------------------------------------------ */
/* ------------------------------------------------------------
   RF-07 (2026-09-07) — ALERTAS REAIS VINDOS DA BASE DE DADOS
   ------------------------------------------------------------
   Até esta data, TODOS os alertas que este dashboard mostrava eram
   entradas fixas de demonstração (PATIENTS[i].alerts). O bridge grava
   agora cada alerta na tabela `alerts` (severidade, motivo, escalonamento
   e ação registada — ver ble_bridge.py, cmd "get_alerts"), e estes são
   fundidos com os de demonstração na MESMA lista, para não haver duas
   secções de alertas a competir pela atenção do cuidador.
   Distinguem-se por `live: true`, que também é o que faz `alertField()`
   escapar o texto (ver abaixo): o texto de demonstração é constante
   escrita neste repositório, o do bridge não é.
------------------------------------------------------------ */
let bridgeAlerts = [];

// Converte um alerta da base de dados para a forma que alertRow() e a
// tabela de histórico já sabem desenhar.
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

// Alertas reais + de demonstração, sem os apagados. Os reais vêm
// primeiro por serem sempre mais recentes do que as entradas fixas.
function currentAlerts(){
  const reais = bridgeAlerts.filter(a => !isAlertDeleted(patientAlertKey(selectedPatientId, a.key)));
  const demo = selectedPatient().alerts.filter(a => !isAlertDeleted(patientAlertKey(selectedPatientId, a.key)));
  return reais.concat(demo);
}

// RF-07 — "motivo textual legível que explique porque foi gerado".
// Para alertas reais é a frase composta pelo bridge com os números que
// dispararam a regra (vital_alerts.explain_vital_alert /
// explain_wear_state). Para os de demonstração, que não têm campo
// próprio, usa-se a descrição técnica, que é exatamente isso — o que foi
// medido e contra que referência.
function alertReasonText(a){
  if (!a) return '';
  if (a.reason) return a.reason;
  return alertField(a, 'desc') || '';
}

// Ponte entre um alerta desta lista e o `alert_id` que o
// EscalationManager do bridge usa ("{alert_type}-{seq}", ver
// _dispatch_emergency_notifications em ble_bridge.py). Só os alertas de
// emergência REAIS têm um — os restantes devolvem null e a confirmação
// fica-se pelo registo em `alerts`.
function liveEmergencyAlertIdFor(fullKey){
  if (!fullKey) return null;
  const chave = String(fullKey).split('::').pop();
  const entrada = currentEmergencyLog().find(e => e.live && `live-${e.liveSeq}` === chave);
  return entrada && entrada.liveSeq != null ? `${entrada.type}-${entrada.liveSeq}` : null;
}
function currentAnomalyLog(){
  return selectedPatient().anomalyLog.filter(a => !deletedAnomaliesMap[`${selectedPatientId}:${a.id}`]);
}

/* ------------------------------------------------------------
   TRADUÇÃO DE ALERTAS/ANOMALIAS/EMERGÊNCIAS (pedido do utilizador:
   "Quero que as mensagens de emergência e alertas sempre traduzidos")
   ------------------------------------------------------------
   Os dados de demonstração (PATIENTS[i].alerts/anomalyLog, EMERGENCY_LOG)
   continuam a guardar o texto em português tal como sempre existiu — é
   usado como fallback (ver t()) se faltar a entrada traduzida. As
   funções abaixo resolvem o texto de facto mostrado a partir de uma
   chave estável (a.key / a.id / e.type) num namespace I18N dedicado, em
   vez de ler os campos de texto diretamente.
------------------------------------------------------------ */
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

// Alertas ainda não lidos e não apagados — o que aparece em "Alertas
// recentes" (Resumo) e "Alertas por severidade" (Pacientes). Ler um
// alerta (markAlertRead()) remove-o desta lista; ele continua acessível
// em "Histórico de alertas" até ser apagado.
function unreadActiveAlerts(){
  return currentAlerts().filter(a => !isAlertRead(patientAlertKey(selectedPatientId, a.key)));
}

// Nº de alertas "ativos" de um paciente = não silenciados (ver
// muteAlert()) E não lidos, neste momento. Calculado, não guardado à
// parte, para nunca poder ficar dessincronizado do que a tabela de
// alertas mostra.
// BUG CORRIGIDO (2026-07-16, reportado pelo utilizador): antes só
// filtrava por "não silenciado" — um médico que já tinha lido/tratado
// todos os alertas de um paciente continuava a ver "N ativos" nesta
// pill (ex.: "4 ativos"), mas ao entrar em "Alertas por severidade"
// (que já filtrava por não-lido) via sempre a mensagem de vazio "Sem
// alertas novos" — parecia que a secção estava sempre vazia
// independentemente do paciente. As duas áreas usam agora o mesmo
// critério (não silenciado E não lido), para o número na tabela nunca
// prometer algo que a secção de severidade não mostra.
function activeAlertsCount(patient){
  return patient.alerts.filter(a =>
    !alertMutedUntil(patientAlertKey(patient.id, a.key)) &&
    !isAlertRead(patientAlertKey(patient.id, a.key))
  ).length;
}

// Chamada pelo botão "Selecionar" em cada linha da tabela de pacientes
// (TEMPLATES.pacientes). Atualiza a seleção, persiste em localStorage, e
// re-renderiza a vista + os rótulos ligados ao paciente selecionado (nav
// lateral). Ver limitação honesta no comentário acima — isto não troca a
// ligação BLE real.
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

// Mapeia EmergencyAlertType (Ble.h: 1=SOS manual, 2=queda+inatividade —
// ver alert_name já traduzido pelo bridge em ble_bridge.py) para as
// mesmas categorias usadas nas entradas de demonstração acima.
const EMERGENCY_ALERT_TYPE_TO_LOG = {
  sos_manual: { type: 'sos', label: 'SOS manual (cliques)' },
  fall_inactivity: { type: 'fall', label: 'Queda + inatividade prolongada' },
};

// Chamado por handleBridgeMessage() quando chega um alerta real via
// emergencyAlertChar (ver ble_bridge.py). Regista o evento no registo de
// emergências do paciente atualmente selecionado.
// LIMITAÇÃO HONESTA (já documentada para o seletor de paciente): o bridge
// só liga a UM dispositivo físico de cada vez — o alerta é sempre
// atribuído ao paciente selecionado na interface no momento em que chega,
// não a um paciente identificado pelo próprio hardware.
function onLiveEmergencyAlert(msg){
  const seq = toFiniteNumber(msg.seq);
  const p = selectedPatient();
  const log = EMERGENCY_LOG[p.id] || (EMERGENCY_LOG[p.id] = []);

  // A notificação BLE pode chegar duplicada (reconexão do bridge, retry
  // da pilha) — 'seq' incrementa no firmware a cada alerta novo, por
  // isso serve para deduplicar sem depender de tempo.
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
    // "explicação de alerta" (2026-08-05) — mecanismo de deteção composto
    // pelo bridge (ver EMERGENCY_ALERT_EXPLANATIONS em ble_bridge.py), não
    // um valor medido (o EmergencyAlertPacket não traz waveform/amplitude,
    // só o tipo já decidido pelo firmware).
    explanation: typeof msg.explanation === 'string' ? msg.explanation : null,
  });

  updateLiveEmergencyBanner();
  if (currentView === 'emergencias') renderView('emergencias');
}

// Mostra/esconde a barra crítica de emergência em direto consoante haja
// ou não alertas 'ativo' de origem real (live: true) para o paciente
// selecionado. Chamada ao chegar um alerta novo e ao cancelar/resolver um
// existente (ver confirmEmergencyCancel()), para desaparecer assim que já
// não há nenhuma emergência em direto por resolver.
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

// Estado do modal de cancelamento — o código gerado (6 dígitos) é
// guardado só em memória (nunca em localStorage), e nunca é enviado a
// lado nenhum: neste protótipo é mostrado na própria página, porque não
// existe (ainda) integração real com SMS/email (ver PROJECT_STATUS.md,
// decisão pendente do provedor). Serve para demonstrar o FLUXO de
// confirmação reforçada exigido, não uma verificação de posse de um
// segundo dispositivo real — isso só existiria com um provedor de SMS
// real a enviar o código para o telemóvel do responsável, fora do
// alcance desta sessão (precisa de credenciais do utilizador).
let emergencyCancelState = null;

// Constantes de segurança do código de confirmação — alinhadas com
// práticas reais de OTP por SMS (pesquisa 2026-07-03): TTL curto (aqui
// 5 min, valor comum na indústria — Twilio/Plivo) e limite de tentativas
// que efetivamente BLOQUEIA a ação (não é só uma mensagem de aviso; ver
// bug corrigido logo abaixo). Isto também segue o princípio de "break
// glass" de acesso de emergência em sistemas de saúde: exceção rara,
// nunca um bypass de rotina, e sempre com registo de quem/quando (ver
// 'resolvedNote' em confirmEmergencyCancel()).
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

/* ------------------------------------------------------------
   TIMELINE CORRELACIONADA POR EPISÓDIO (2026-08-05)
   ------------------------------------------------------------
   Pede ao bridge (cmd "get_episode_timeline") os sinais vitais/blocos de
   atividade/outros alertas à volta de UM alerta de emergência real (só
   disponível para entradas com liveSeq — as de demonstração não têm
   sequence_number nenhum na base de dados do bridge para pesquisar).
------------------------------------------------------------- */
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

// Junta sensor_summary + activity_blocks + nearby_emergency_alerts numa
// única lista ordenada no tempo, cada item com um "tipo" e um rótulo —
// mais fácil de ler numa timeline única do que 3 secções separadas.
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

  // BUG CORRIGIDO (2026-07-03, aplicando pesquisa sobre rate-limiting de
  // OTP): ao atingir o limite de tentativas, a versão anterior só
  // ACRESCENTAVA uma frase ao aviso, mas continuava a aceitar tentativas
  // novas indefinidamente — o "bloqueio" era só visual. Agora bloqueia
  // mesmo (return antes de validar o código), e o código também expira
  // ao fim de 5 min mesmo sem esgotar as tentativas, tal como um OTP real.
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

/* ------------------------------------------------------------
   LEMBRETES DE MEDICAÇÃO (item 9 do backlog de investigação)
   ------------------------------------------------------------
   `patient.medications` e `patient.adherenceHistory` (ver PATIENTS acima)
   são dados de exemplo por paciente (nome, dose, horários; adesão dos
   últimos dias). O que é real neste protótipo é o registo de toma de
   HOJE: cada clique em "Marcar como tomado" fica em localStorage
   (namespaced por paciente + dia + medicamento + horário), sobrevive a
   recarregar a página. Histórico anterior a hoje só existirá a sério
   depois do serviço de persistência (Prioridade 4, ver PROJECT_STATUS.md).
   "Correlacionado com atividade/vitais" (pedido no backlog): mostrado
   como uma nota simples que aponta os dias com adesão incompleta para
   serem comparados manualmente com a vista "Tendência semanal" — uma
   correspondência de datas, não uma análise estatística automática (não
   fabricamos uma correlação numérica sem dados reais para a sustentar).
------------------------------------------------------------ */
