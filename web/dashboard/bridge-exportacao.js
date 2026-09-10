// Liga por WebSocket a bridge/ble_bridge.py (fala BLE com o dispositivo e reencaminha JSON já descodificado).
// Sem bridge a correr, a ligação falha silenciosamente e o dashboard fica nos dados simulados.
// TLS opcional (GDPR-004): bridge com CAREWEAR_WS_TLS=1 + aceitar o certificado em https://localhost:8765
// + localStorage.setItem('carewear_ws_tls','1') aqui — sem os 3 passos a ligação wss:// falha silenciosamente.
function wsUrl(){
  const scheme = localStorage.getItem('carewear_ws_tls') === '1' ? 'wss' : 'ws';
  const base = scheme + '://localhost:8765';
  const session = typeof getAuthToken === 'function' ? getAuthToken() : null;
  return session ? base + '?session=' + encodeURIComponent(session) : base;
}
const LIVE_HR_WINDOW = 60; // amostras mantidas no gráfico de FC ao vivo

const liveState = {
  connected: false,
  blePaused: false, // desligado manualmente pelo utilizador (toggleBleConnection), distinto de "a tentar ligar"
  deviceMac: null, // MAC realmente ligado por BLE (device_status), usado por TEMPLATES.dispositivo
  hr: null, spo2: null, steps: null, freefall: false, inactivity: false,
  pacing: null, // 0-100 "pacing"/curvas apertadas (Imu::detectPacing); 0 é válido, null = sem leitura ainda
  lastRecordAt: 0,
  dataLossFlag: 0, // 0=normal, 1=ring buffer quase cheio, 2=já a substituir dados não consumidos
  sentRecords: null, // contagem cumulativa de registos transferidos nesta sessão
  ringCount: null, // registos por enviar agora no ring buffer; com sentRecords dá % real de progresso
  batteryPercent: null, // não reinicia ao perder ligação — é o último valor conhecido
  realTrend: [], // histórico real por dia (storage.get_daily_summary), distinto de currentTrendData() sintético
  currentActivity: null, // {category, confidence, session, receivedAt} — modelo só treinado com dados sintéticos
  lastActivityDurationFlag: null, // veredito do detetor de duração (ml/duration_detector.py)
  // correção manual do cuidador à classificação da IA — mostrada ao lado, nunca substitui; não realimenta o modelo
  activityCorrection: null, // {category, originalCategory, correctedAtEpochS}
};
const liveHrBuffer = []; // {t: epoch_s, hr, label: "HH:MM:SS"}

function fmtClock(epochSeconds){
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleTimeString('pt-PT', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
}

// Aviso de armazenamento (liveState.dataLossFlag): 1=ring buffer quase cheio, 2=já a substituir dados não consumidos.
function renderStorageWarningBanner(){
  const el = document.getElementById('storageWarningBanner');
  if (!el) return;
  if (currentRole !== 'utente') { el.style.display = 'none'; return; } // só relevante para Utente/Família, ver index.html
  const flag = liveState.dataLossFlag;
  if (!flag) { el.style.display = 'none'; el.className = 'storage-warning-banner print-hide'; return; }
  el.className = `storage-warning-banner print-hide level-${flag}`;
  el.style.display = 'flex';
  // % real de progresso: sentRecords / (sentRecords + ringCount)
  const haveBoth = liveState.sentRecords != null && liveState.ringCount != null;
  const totalKnown = haveBoth ? liveState.sentRecords + liveState.ringCount : null;
  const pctDone = haveBoth && totalKnown > 0 ? Math.round((liveState.sentRecords / totalKnown) * 100) : null;
  const progressText = liveState.sentRecords != null
    ? ` ${t('topbar.storageDrainProgressPrefix')} ${liveState.sentRecords.toLocaleString(currentLang)}${t('topbar.storageDrainProgressSuffix')}`
    : '';
  const progressBar = pctDone != null ? `
    <div class="meter-track" style="margin-top:6px;"><div class="meter-fill" style="width:${pctDone}%; background:var(--status-good)"></div></div>
    <span class="tabular" style="font-size:12px;">${pctDone}% ${t('topbar.storageDrainPctSuffix')} (${liveState.ringCount.toLocaleString(currentLang)} ${t('topbar.storageDrainRemainingSuffix')})</span>
  ` : '';
  el.innerHTML = flag === 2
    ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.3 3.9 1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg>
       <span>${t('topbar.storageFullWarning')}${progressText}${progressBar}</span>`
    : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.3 3.9 1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg>
       <span>${t('topbar.storageAlmostFullWarning')}${progressText}${progressBar}</span>`;
}

function updateDeviceStatusUI(){
  const dot = document.getElementById('deviceDot');
  const text = document.getElementById('deviceStatusText');
  if (dot && text){
    if (liveState.connected){
      dot.classList.remove('warn');
      text.textContent = t('topbar.connected');
    } else if (liveState.blePaused){
      dot.classList.add('warn');
      text.textContent = t('topbar.pausedManual');
    } else {
      dot.classList.add('warn');
      text.textContent = t('topbar.disconnected');
    }
  }
  updateBleToggleUI();
}

// Botão que pede ao bridge para largar/retomar a ligação BLE (set_ble_enabled) — não fecha o WebSocket, só o BLE.
function updateBleToggleUI(){
  const btn = document.getElementById('bleToggleBtn');
  const btnText = document.getElementById('bleToggleBtnText');
  if (!btn || !btnText) return;
  const enabled = !liveState.blePaused;
  btn.setAttribute('aria-pressed', String(enabled));
  btn.title = t('topbar.bleToggleHint');
  btnText.textContent = enabled ? t('topbar.bleDisconnect') : t('topbar.bleConnect');
}

function updateBatteryUI(){
  const text = document.getElementById('batteryText');
  const chip = document.getElementById('batteryChip');
  if (!text || !chip) return;
  const pct = liveState.batteryPercent;
  text.textContent = pct != null ? `${pct}%` : '—';
  chip.title = pct != null ? t('topbar.batteryTitle', {pct}) : t('topbar.batteryUnknown');
}

// Painel "Atividade detetada (IA)": classificação em tempo real + veredito de duração do último bloco.
// Aviso "não validado clinicamente" sempre visível junto ao resultado (ACTIVITY_ML_DISCLAIMER no bridge).
let activityCorrectionPickerOpen = false; // estado efémero de UI, não em liveState — não vem do bridge

function renderLiveActivityPanel(){
  const host = document.getElementById('liveActivityPanel');
  if (!host) return;

  if (!liveState.currentActivity){
    host.innerHTML = `<p class="activity-live-empty">${t('resumo.liveActivityWaiting')}</p>`;
    return;
  }

  const a = liveState.currentActivity;
  const catLabel = escapeHtml(a.category); // categorias não traduzidas em lado nenhum do dashboard, por consistência
  const pct = Math.round(a.confidence * 100);

  // indicador de incerteza: só aparece se o bridge o enviar (is_uncertain); compatível com bridge mais antigo
  let uncertaintyHtml = '';
  if (a.isUncertain && a.runnerUpCategory){
    const runnerUpPct = a.runnerUpConfidence != null ? Math.round(a.runnerUpConfidence * 100) : null;
    uncertaintyHtml = `<div class="activity-live-flag uncertain">${t('resumo.liveActivityUncertainIcon')} ${t('resumo.liveActivityUncertain', {cat: escapeHtml(a.runnerUpCategory), pct: runnerUpPct})}</div>`;
  }

  let flagHtml = '';
  const flag = liveState.lastActivityDurationFlag;
  if (flag){
    const flagCatLabel = escapeHtml(flag.category);
    // explicação (frase já composta no bridge com números reais) só se o bridge a enviar
    const explanationHtml = flag.explanation
      ? `<div class="activity-live-flag-reason">${escapeHtml(flag.explanation)}</div>` : '';
    flagHtml = flag.isAnomaly
      ? `<div class="activity-live-flag anomaly">⚠ ${t('resumo.liveActivityDurationAnomaly', {cat: flagCatLabel, min: flag.durationMin})}</div>${explanationHtml}`
      : `<div class="activity-live-flag normal">✓ ${t('resumo.liveActivityDurationNormal', {cat: flagCatLabel, min: flag.durationMin})}</div>${explanationHtml}`;
  }

  // Correção do cuidador é o valor PRINCIPAL enquanto "fresca" (< ACTIVITY_CORRECTION_OVERRIDE_S);
  // a IA fica em segundo plano, nunca escondida — não há retreino em tempo real, só fica guardada em storage.py.
  const corr = liveState.activityCorrection;
  const correctionAgeS = corr && corr.correctedAtEpochS != null
    ? (Date.now() / 1000) - corr.correctedAtEpochS : null;
  const correctionActive = corr && correctionAgeS != null && correctionAgeS < ACTIVITY_CORRECTION_OVERRIDE_S;

  let correctionHtml = '';
  if (corr){
    const timeLabel = corr.correctedAtEpochS != null
      ? new Date(corr.correctedAtEpochS * 1000).toLocaleTimeString(currentLang, {hour: '2-digit', minute: '2-digit'})
      : '';
    correctionHtml = correctionActive
      ? `<div class="activity-live-correction ai-note">${t('resumo.liveActivityAiSaysNow', {cat: catLabel, pct})}</div>`
      : `<div class="activity-live-correction">✎ ${t('resumo.liveActivityCorrectedTo', {cat: escapeHtml(corr.category)})}${timeLabel ? ` · ${timeLabel}` : ''} — ${t('resumo.liveActivityCorrectionExpired')}</div>`;
  }

  const pickerHtml = activityCorrectionPickerOpen ? `
    <div class="activity-chips" style="margin:8px 0 0;">
      ${ACTIVITY_CORRECTION_CATEGORIES.map(cat => `
        <button type="button" class="activity-chip" style="border-color:${categoryColorVar(cat)}" onclick="submitActivityCorrection('${cat}')">${escapeHtml(cat)}</button>
      `).join('')}
    </div>
  ` : '';

  // linha principal: correção ativa em destaque, senão a IA
  const mainCat = correctionActive ? escapeHtml(corr.category) : catLabel;
  const mainColor = correctionActive ? categoryColorVar(corr.category) : categoryColorVar(a.category);
  const mainSuffix = correctionActive
    ? `<span class="conf">${t('resumo.liveActivityConfirmedByCaregiver')}</span>`
    : `<span class="conf">${t('resumo.liveActivityConfidence', {pct})}</span>`;

  host.innerHTML = `
    <div class="activity-live-now">
      <span class="cat" style="color:${mainColor}">${mainCat}</span>
      ${mainSuffix}
      ${liveState.connected ? `<button type="button" class="btn-link activity-correct-btn" onclick="toggleActivityCorrectionPicker()">${t('resumo.liveActivityCorrectBtn')}</button>` : ''}
    </div>
    ${correctionHtml}
    ${uncertaintyHtml}
    ${pickerHtml}
    <p id="activityCorrectionStatus" class="activity-live-flag anomaly" style="display:none;"></p>
    ${flagHtml}
    <p class="activity-live-empty">${t('resumo.liveActivityDisclaimer')}</p>
  `;
}

const ACTIVITY_CORRECTION_OVERRIDE_S = 1800; // 30 min — janela em que a correção do cuidador fica em destaque

// Mesmo conjunto de ACTIVITY_CLASS_COLOR_VAR, como array (ordem = CLASS_TO_DB_CATEGORY no bridge)
const ACTIVITY_CORRECTION_CATEGORIES = ['Dormir', 'Descanso', 'Atividade', 'Alimentação', 'Higiene'];

function toggleActivityCorrectionPicker(){
  activityCorrectionPickerOpen = !activityCorrectionPickerOpen;
  renderLiveActivityPanel();
}

function submitActivityCorrection(category){
  activityCorrectionPickerOpen = false;
  const original = liveState.currentActivity ? liveState.currentActivity.category : null;
  const sent = sendWsCommandWithArgs('correct_activity', {category});
  // RF-09: registada sempre na fila local, mesmo se o bridge estiver em baixo — senão o juízo do cuidador desaparecia sem rasto
  recordHitlCorrection({
    kind: 'atividade',
    target: 'classificacao_ao_vivo',
    targetLabel: 'Classificação de atividade em tempo real',
    originalLabel: original,
    correctedLabel: category,
    falsePositive: original != null && original !== category,
    sentToBridge: sent,
  });
  renderLiveActivityPanel();
}

// RF-09 — rotulagem human-in-the-loop: cuidador marca alerta/classificação como falso positivo,
// fica disponível para o próximo ciclo de retreino (escassez de dados rotulados = principal travão do HAR).
// cmd "correct_activity" já existia no bridge só para classificação ao vivo; faltava: cobrir alertas,
// persistir sem bridge, permitir ver/desfazer, e exportar num formato consumível por ml/.
// Sem comando de bridge equivalente para alertas — essas marcações vivem só em localStorage até exportação manual.
const HITL_CORRECTIONS_KEY = 'carewear_hitl_corrections';
const HITL_MAX_ENTRIES = 500; // localStorage é partilhado com o resto do protótipo, evita estourar a quota

function loadHitlCorrections(){
  try {
    const raw = localStorage.getItem(HITL_CORRECTIONS_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (Array.isArray(parsed)) return parsed;
  } catch (e) { /* localStorage indisponível ou dados corrompidos — começa vazio */ }
  return [];
}
function saveHitlCorrections(list){
  try { localStorage.setItem(HITL_CORRECTIONS_KEY, JSON.stringify(list)); }
  catch (e) { /* quota excedida — a fila fica só em memória nesta sessão */ }
}
let hitlCorrections = loadHitlCorrections();

// patientId gravado na entrada (não inferido na leitura) — o cuidador pode trocar de paciente entretanto
function recordHitlCorrection(entry){
  const registo = {
    id: 'hitl-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 7),
    ts: Date.now(),
    patientId: typeof selectedPatientId !== 'undefined' ? selectedPatientId : null,
    role: typeof currentRole !== 'undefined' ? currentRole : null,
    kind: entry.kind,
    target: entry.target,
    targetLabel: entry.targetLabel || '',
    originalLabel: entry.originalLabel != null ? entry.originalLabel : null,
    correctedLabel: entry.correctedLabel != null ? entry.correctedLabel : null,
    falsePositive: !!entry.falsePositive,
    sentToBridge: !!entry.sentToBridge,
  };
  hitlCorrections.unshift(registo);
  if (hitlCorrections.length > HITL_MAX_ENTRIES) hitlCorrections.length = HITL_MAX_ENTRIES;
  saveHitlCorrections(hitlCorrections);
  return registo;
}

// chave composta paciente+alerta — o key do alerta repete-se entre pacientes (mesma convenção de patientAlertKey)
function hitlAlertMatches(c, patientId, alertKey){
  return c.kind === 'alerta' && c.target === alertKey && c.patientId === patientId;
}
function isAlertMarkedFalsePositive(alertKey, patientId){
  const pid = patientId || (typeof selectedPatientId !== 'undefined' ? selectedPatientId : null);
  return hitlCorrections.some(c => hitlAlertMatches(c, pid, alertKey) && c.falsePositive);
}

// alterna (não só marca) — um clique por engano tem de poder ser desfeito
function toggleAlertFalsePositive(alertKey, alertTitle){
  const pid = typeof selectedPatientId !== 'undefined' ? selectedPatientId : null;
  if (isAlertMarkedFalsePositive(alertKey, pid)){
    hitlCorrections = hitlCorrections.filter(c => !hitlAlertMatches(c, pid, alertKey));
    saveHitlCorrections(hitlCorrections);
  } else {
    recordHitlCorrection({
      kind: 'alerta',
      target: alertKey,
      targetLabel: alertTitle || alertKey,
      originalLabel: 'alerta_gerado',
      correctedLabel: 'falso_positivo',
      falsePositive: true,
      sentToBridge: false, // sem comando de bridge para isto — fica sempre por sincronizar
    });
  }
  if (typeof currentView !== 'undefined' && currentView) renderView(currentView);
}

function removeHitlCorrection(id){
  hitlCorrections = hitlCorrections.filter(c => c.id !== id);
  saveHitlCorrections(hitlCorrections);
  if (typeof currentView !== 'undefined' && currentView) renderView(currentView);
}

// JSONL (uma linha por rótulo); campos seguem activity_corrections do bridge para poder concatenar sem tradução de esquema
function buildHitlJsonl(){
  return hitlCorrections.map(c => JSON.stringify({
    id: c.id,
    received_at: new Date(c.ts).toISOString(),
    patient_id: c.patientId,
    corrected_by_role: c.role,
    kind: c.kind,
    target: c.target,
    original_category: c.originalLabel,
    corrected_category: c.correctedLabel,
    false_positive: c.falsePositive,
    synced_to_bridge: c.sentToBridge,
  })).join('\n');
}

function exportHitlCorrections(){
  if (!hitlCorrections.length) return;
  // Blob + <a download> à mão (MIME/extensão diferentes de downloadJson/downloadCsvText em export-clinico.js)
  const blob = new Blob([buildHitlJsonl() + '\n'], {type: 'application/x-ndjson'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `carewear-rotulos-cuidador-${new Date().toISOString().slice(0,10)}.jsonl`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function exportHitlCorrectionsCsv(){
  if (!hitlCorrections.length) return;
  const cab = 'id,received_at,patient_id,corrected_by_role,kind,target,original_category,corrected_category,false_positive,synced_to_bridge';
  const linhas = hitlCorrections.map(c => [
    c.id,
    new Date(c.ts).toISOString(),
    c.patientId, c.role, c.kind, c.target,
    c.originalLabel, c.correctedLabel,
    c.falsePositive, c.sentToBridge,
  ].map(v => {
    const s = v == null ? '' : String(v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }).join(','));
  downloadCsvText('carewear-rotulos-cuidador', [cab, ...linhas].join('\n'));
}

// classe do classificador -> variável CSS de cor, mesma paleta de catMap (timeline simulada)
const ACTIVITY_CLASS_COLOR_VAR = {
  'Dormir': 'var(--cat-dormir)',
  'Descanso': 'var(--cat-descanso)',
  'Atividade': 'var(--cat-atividade)',
  'Alimentação': 'var(--cat-alimentacao)',
  'Higiene': 'var(--cat-higiene)',
};
function categoryColorVar(category){
  return ACTIVITY_CLASS_COLOR_VAR[category] || 'var(--text-primary)';
}

function toggleBleConnection(){
  const nextEnabled = liveState.blePaused; // estava pausado -> vamos ligar
  const sent = sendWsCommandWithArgs('set_ble_enabled', {enabled: nextEnabled});
  if (!sent) return; // sem ligação ao bridge — nada a pedir
  // otimista: reflete já o pedido na UI, sem esperar pelo próximo device_status (~1s)
  liveState.blePaused = !nextEnabled;
  if (!nextEnabled) liveState.connected = false;
  updateDeviceStatusUI();
}

// Valor não confiável do bridge -> número finito ou null. `v == null` tem de vir ANTES de Number(v):
// Number(null) === 0 em JS, e reintroduziria o zero que o bridge já converteu para null propositadamente.
function toFiniteNumber(v){
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

// Escreve os últimos valores de liveState nos cartões de sinais vitais (chamado em registo novo e em AFTER_RENDER)
function applyLiveVitals(){
  if (!liveState.connected) return; // mantém os valores de demonstração já no HTML

  const setVal = (id, value) => { const el = document.getElementById(id); if (el && value != null) el.innerHTML = value; };
  const hrText = liveState.hr != null ? `${liveState.hr}<span class="unit">bpm</span>` : '—<span class="unit">bpm</span>';
  const spo2Text = liveState.spo2 != null ? `${liveState.spo2}<span class="unit">%</span>` : '—<span class="unit">%</span>';

  setVal('stat-hr', hrText); setVal('stat-hr-2', hrText);
  setVal('stat-spo2', spo2Text); setVal('stat-spo2-2', spo2Text);

  // HR chegando mas SpO2 nulo: sinal ainda instável para SpO2 (mais sensível a movimento que HR) — só nesta combinação
  const spo2NeedsHint = liveState.hr != null && liveState.spo2 == null;
  const spo2HintText = spo2NeedsHint
    ? 'A ler HR mas sem SpO2 ainda — tenta manter a placa bem encostada ao pulso, sem mover a mão.'
    : '';
  ['spo2-hint', 'spo2-hint-2'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = spo2HintText;
    el.style.display = spo2NeedsHint ? '' : 'none';
  });

  // repõe o hint ao texto normal quando a ligação volta (ficava preso ao texto de "sem ligação"), só se não houver countdown em curso
  const forceHintEl = document.getElementById('forceReadingHint');
  const forceBtnEl = document.getElementById('forceReadingBtn');
  const noCountdownActive = forceReadingIntervalId == null && continuousHrIntervalId == null;
  if (forceHintEl && forceBtnEl && !forceBtnEl.disabled && noCountdownActive) {
    forceHintEl.style.color = '';
    forceHintEl.textContent = t('vitais.forceReadingHint');
  }

  setVal('stat-steps-2', liveState.steps != null ? liveState.steps.toLocaleString(currentLang) : '—');
  setVal('stat-movement', liveState.inactivity ? t('resumo.movementStill') : t('resumo.movementActive'));
  setVal('stat-falls-2', liveState.freefall ? `⚠ ${t('resumo.fallsRecentDetection')}` : '0');
}

function handleBridgeMessage(msg){
  if (msg.kind === 'device_status'){
    liveState.connected = !!msg.connected;
    liveState.blePaused = !!msg.paused;
    // canal não autenticado — valida a forma antes de confiar, nunca guarda msg.mac tal como vem
    liveState.deviceMac = (typeof msg.mac === 'string' && /^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$/.test(msg.mac))
      ? msg.mac.toUpperCase() : null;
    // associa o wearable físico à conta atualmente selecionada, para reconhecê-lo também noutras contas
    if (liveState.deviceMac && selectedPatientId) {
      const reg = loadDeviceRegistry();
      if (reg[selectedPatientId] !== liveState.deviceMac) {
        reg[selectedPatientId] = liveState.deviceMac;
        saveDeviceRegistry(reg);
      }
    }
    updateDeviceStatusUI();
    applyLiveVitals();
    return;
  }
  if (msg.kind === 'command_result'){
    handleCommandResult(msg);
    return;
  }
  if (msg.kind === 'status'){
    liveState.dataLossFlag = toFiniteNumber(msg.data_loss_flag) || 0;
    liveState.sentRecords = toFiniteNumber(msg.sent_records);
    liveState.ringCount = toFiniteNumber(msg.ring_count);
    renderStorageWarningBanner();
    return;
  }
  if (msg.kind === 'battery'){
    liveState.batteryPercent = toFiniteNumber(msg.percent);
    updateBatteryUI();
    return;
  }
  if (msg.kind === 'activity_classification'){
    // category valida contra enum fechado (ACTIVITY_CLASS_COLOR_VAR); resto do msg é ignorado se não bater certo
    const conf = toFiniteNumber(msg.confidence);
    if (Object.prototype.hasOwnProperty.call(ACTIVITY_CLASS_COLOR_VAR, msg.category) && conf != null){
      const runnerUpCat = Object.prototype.hasOwnProperty.call(ACTIVITY_CLASS_COLOR_VAR, msg.runner_up_category)
        ? msg.runner_up_category : null;
      liveState.currentActivity = {
        category: msg.category, confidence: conf,
        runnerUpCategory: runnerUpCat,
        runnerUpConfidence: toFiniteNumber(msg.runner_up_confidence),
        confidenceMargin: toFiniteNumber(msg.confidence_margin),
        isUncertain: !!msg.is_uncertain,
      };
      renderLiveActivityPanel();
    }
    return;
  }
  if (msg.kind === 'activity_correction'){
    // difundido pelo bridge a todos os dashboards, mesma allowlist de "activity_classification"
    if (Object.prototype.hasOwnProperty.call(ACTIVITY_CLASS_COLOR_VAR, msg.category)){
      liveState.activityCorrection = {
        category: msg.category,
        originalCategory: Object.prototype.hasOwnProperty.call(ACTIVITY_CLASS_COLOR_VAR, msg.original_category) ? msg.original_category : null,
        correctedAtEpochS: toFiniteNumber(msg.corrected_at),
      };
      renderLiveActivityPanel();
    }
    return;
  }
  if (msg.kind === 'activity_duration_flag'){
    const durationMin = toFiniteNumber(msg.duration_min);
    if (Object.prototype.hasOwnProperty.call(ACTIVITY_CLASS_COLOR_VAR, msg.cls) && durationMin != null){
      liveState.lastActivityDurationFlag = {
        category: msg.cls,
        durationMin: Math.round(durationMin),
        isAnomaly: !!msg.is_anomaly,
        // frase composta pelo bridge (explain_block); entra só via escapeHtml() na renderização
        explanation: typeof msg.explanation === 'string' ? msg.explanation : null,
      };
      renderLiveActivityPanel();
    }
    return;
  }
  if (msg.kind === 'emergency_alert'){
    onLiveEmergencyAlert(msg);
    return;
  }
  if (msg.kind === 'csv_export'){
    handleCsvExportResult(msg);
    return;
  }
  if (msg.kind === 'daily_trend'){
    liveState.realTrend = Array.isArray(msg.days_summary) ? msg.days_summary : [];
    renderRealTrendTable();
    return;
  }
  if (msg.kind === 'retention_days'){
    handleRetentionDaysResult(msg);
    return;
  }
  if (msg.kind === 'retention_days_result'){
    handleRetentionDaysSaveResult(msg);
    return;
  }
  if (msg.kind === 'consent_status'){
    handleConsentStatusResult(msg);
    return;
  }
  if (msg.kind === 'consent_result'){
    handleConsentResultMessage(msg);
    return;
  }
  if (msg.kind === 'thresholds'){
    handleThresholdsResult(msg);
    return;
  }
  if (msg.kind === 'thresholds_result'){
    handleThresholdsSaveResult(msg);
    return;
  }
  if (msg.kind === 'episode_timeline'){
    handleEpisodeTimelineResult(msg);
    return;
  }
  if (msg.kind === 'model_versions'){
    handleModelVersionsResult(msg);
    return;
  }
  if (msg.kind === 'model_version_result'){
    handleModelVersionResult(msg);
    return;
  }
  if (msg.kind === 'vital_alert'){
    // 'vital' valida contra par fechado; resto passa por escapeHtml() na renderização
    if (msg.vital === 'hr' || msg.vital === 'spo2'){
      liveState.vitalAlerts[msg.vital] = msg.cleared ? null : {
        level: msg.level, value: toFiniteNumber(msg.value), limit: toFiniteNumber(msg.limit),
        explanation: typeof msg.explanation === 'string' ? msg.explanation : '',
      };
      renderVitalAlertsPanel();
    }
    return;
  }
  if (msg.kind === 'live_record' || msg.kind === 'record'){
    applySensorRecordFields(msg, {isLive: msg.kind === 'live_record'});
    return;
  }
}

// Aplica campos de sensores (hr/spo2/steps/freefall/inactivity/pacing) a liveState.
// 'live_record' (liveSnapshotChar): instantâneo mais recente, nunca atrasado — única fonte da UI ao vivo.
// 'record' (dumpDataChar): fluxo histórico cronológico, pode estar minutos atrasado se gravado sem BLE — chega mas
// já não afeta a UI (só live_record atualiza liveState/gráfico), para nunca competir com o instantâneo ao vivo.
function applySensorRecordFields(msg, {isLive}){
  // spo2/hr vêm a 0 no wire format quando a amostra não trouxe leitura nova; o bridge já converte para null
  // (decode_full_plain). toFiniteNumber preserva esse null (Number(null)===0 seria um bug). hasNewHr/hasNewSpo2
  // exigem também >0 como rede de segurança extra — 0 nunca é uma leitura fisiológica real.
  const hrNum = toFiniteNumber(msg.hr);
  const spo2Num = toFiniteNumber(msg.spo2);
  const stepsNum = toFiniteNumber(msg.steps);
  const pacingNum = toFiniteNumber(msg.pacing_index);
  const hasNewHr = hrNum != null && hrNum > 0;
  const hasNewSpo2 = spo2Num != null && spo2Num > 0;

  if (!isLive) return; // 'record' (histórico) chega mas não toca na UI

  liveState.lastRecordAt = Date.now();
  if (hasNewHr) liveState.hr = hrNum;
  if (hasNewSpo2) liveState.spo2 = spo2Num;
  liveState.steps = stepsNum;
  liveState.freefall = !!msg.freefall;
  liveState.inactivity = !!msg.inactivity;
  if (pacingNum != null) liveState.pacing = pacingNum;

  if (hasNewHr && msg.ts){
    liveHrBuffer.push({t: msg.ts, hr: hrNum, label: fmtClock(msg.ts)});
    if (liveHrBuffer.length > LIVE_HR_WINDOW) liveHrBuffer.shift();
    // Só redesenha o gráfico se a vista "Sinais vitais" estiver ativa.
    if (document.getElementById('cvHr')) drawHrSeries('cvHr');
  }
  // Só re-renderiza o cartão de pacing se a vista "Rotina diária" estiver ativa.
  if (pacingNum != null && document.getElementById('pacingSummary')) renderPacingSummary();
  applyLiveVitals();
}

let bridgeWs = null; // ligação WebSocket ativa (ou null), usada pelos botões de comando

function connectBridge(){
  let ws;
  try {
    ws = new WebSocket(wsUrl());
  } catch (e) {
    scheduleReconnect();
    return;
  }

  ws.onopen = () => { bridgeWs = ws; };
  ws.onmessage = (ev) => {
    try { handleBridgeMessage(JSON.parse(ev.data)); }
    catch (e) { /* mensagem invalida do bridge - ignora */ }
  };
  ws.onclose = () => {
    if (bridgeWs === ws) bridgeWs = null;
    liveState.connected = false;
    updateDeviceStatusUI();
    resetPendingCommandButtons();
    // sem ligação, a última classificação deixa de ser fiável — limpa em vez de ficar preso a uma leitura antiga
    liveState.currentActivity = null;
    liveState.lastActivityDurationFlag = null;
    liveState.activityCorrection = null;
    renderLiveActivityPanel();
    scheduleReconnect();
  };
  ws.onerror = () => { ws.close(); };
}

function scheduleReconnect(){
  setTimeout(connectBridge, 4000); // cobre tanto "bridge ainda não arrancou" como "bridge caiu e voltou"
}

// Envia {"cmd":"..."} ao bridge (traduz para escrita BLE em dumpCtrlChar). Devolve false sem ligação.
function sendWsCommand(cmd){
  if (!bridgeWs || bridgeWs.readyState !== WebSocket.OPEN) return false;
  bridgeWs.send(JSON.stringify({cmd}));
  return true;
}

// variante com campos extra além de "cmd" (ex.: {cmd:"export_csv", hours:24})
function sendWsCommandWithArgs(cmd, extra){
  if (!bridgeWs || bridgeWs.readyState !== WebSocket.OPEN) return false;
  bridgeWs.send(JSON.stringify({cmd, ...extra}));
  return true;
}

// Comandos ao vivo: "Medir agora" (FC+SpO2 forçados) e "Repor leituras" (destrutivo, com modal).
// Countdown corre até FORCE_READING_SECONDS real, não até o command_result chegar — o bridge envia
// esse ack logo a seguir à escrita GATT, muito antes da medição de 15s terminar no dispositivo.
// Duração deve ficar em sincronia manual com DUMP_CTRL_FORCE_READING_SECONDS em bridge/ble_bridge.py.
const FORCE_READING_SECONDS = 15;
let forceReadingIntervalId = null;
// Leitura contínua: reenvia force_reading antes de cada janela de 15s expirar, sem mudar firmware/bridge.
// Firmware limita cada pedido a 30s (Ppg.cpp kManualHrMaxDurationMs) — reenviar respeita esse limite.
let continuousHrIntervalId = null;

function stopForceReadingCountdown(){
  if (forceReadingIntervalId != null){ clearInterval(forceReadingIntervalId); forceReadingIntervalId = null; }
}

function finishForceReadingCountdown(message, isWarning){
  stopForceReadingCountdown();
  const btn = document.getElementById('forceReadingBtn');
  const hint = document.getElementById('forceReadingHint');
  if (btn) btn.disabled = false;
  if (hint){
    hint.style.color = isWarning ? 'var(--status-warning)' : 'var(--status-good)';
    hint.textContent = message;
  }
}

function startForceReadingCountdown(){
  stopForceReadingCountdown();
  const hint = document.getElementById('forceReadingHint');
  let remaining = FORCE_READING_SECONDS;
  const render = () => { if (hint) hint.textContent = `${t('vitais.measuringHint')} (~${remaining}${t('vitais.secondsRemainingSuffix')})`; };
  if (hint) hint.style.color = '';
  render();
  forceReadingIntervalId = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0){ // só chamado por onForceReadingClick() — modo contínuo não usa este countdown
      finishForceReadingCountdown(t('vitais.measurementDoneHint'), false);
      return;
    }
    render();
  }, 1000);
}

function toggleContinuousHr(){
  const btn = document.getElementById('continuousHrBtn');
  const hint = document.getElementById('forceReadingHint');
  const forceBtn = document.getElementById('forceReadingBtn');
  if (continuousHrIntervalId != null){
    clearInterval(continuousHrIntervalId);
    continuousHrIntervalId = null;
    stopForceReadingCountdown();
    if (btn){ btn.className = 'btn-secondary'; btn.setAttribute('aria-pressed', 'false'); btn.textContent = ''; btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M3 12h4l2-7 4 14 2-7h6"/></svg> ${t('vitais.continuousHrStartBtn')}`; }
    if (forceBtn) forceBtn.disabled = false;
    if (hint){ hint.style.color = ''; hint.textContent = t('vitais.forceReadingHint'); }
    return;
  }
  if (!liveState.connected){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = t('vitais.noBridgeConnectionHint'); }
    return;
  }
  // modo contínuo mostra indicador fixo, não repete o countdown a cada reenvio (~14s)
  const fire = () => {
    sendWsCommand('force_reading');
  };
  if (btn){ btn.className = 'btn-primary'; btn.setAttribute('aria-pressed', 'true'); btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M3 12h4l2-7 4 14 2-7h6"/></svg> ${t('vitais.continuousHrStopBtn')}`; }
  if (forceBtn) forceBtn.disabled = true;
  stopForceReadingCountdown();
  if (hint){ hint.style.color = 'var(--status-good)'; hint.textContent = t('vitais.continuousHrActiveHint'); }
  fire();
  continuousHrIntervalId = setInterval(fire, (FORCE_READING_SECONDS - 1) * 1000);
}

function resetPendingCommandButtons(){
  const forceBtn = document.getElementById('forceReadingBtn');
  const forceHint = document.getElementById('forceReadingHint');
  if (forceBtn && forceBtn.disabled){
    stopForceReadingCountdown();
    forceBtn.disabled = false;
    if (forceHint){
      forceHint.style.color = 'var(--status-warning)';
      forceHint.textContent = 'Ligação ao bridge perdida antes de haver resposta. Tenta novamente.';
    }
  }
  const resetBtn = document.getElementById('resetConfirmBtn');
  const resetStatus = document.getElementById('resetModalStatus');
  if (resetBtn && resetBtn.disabled){
    resetBtn.disabled = false;
    if (resetStatus){
      resetStatus.className = 'modal-status err';
      resetStatus.textContent = 'Ligação ao bridge perdida antes de haver resposta. Tenta novamente.';
    }
  }
}

function onForceReadingClick(){
  const btn = document.getElementById('forceReadingBtn');
  const hint = document.getElementById('forceReadingHint');
  if (!liveState.connected){
    hint.textContent = 'Sem ligação ao bridge — liga o dispositivo primeiro.';
    hint.style.color = 'var(--status-warning)';
    return;
  }
  const sent = sendWsCommand('force_reading');
  if (!sent){
    hint.textContent = 'Não foi possível contactar o bridge. Tenta novamente.';
    hint.style.color = 'var(--status-warning)';
    return;
  }
  btn.disabled = true;
  startForceReadingCountdown();
}

function handleCommandResult(msg){
  if (msg.cmd === 'force_reading'){
    // só ok=false interrompe o countdown cedo — ok=true só confirma envio, a medição continua no dispositivo
    if (!msg.ok){
      finishForceReadingCountdown(`Falhou: ${msg.error || 'erro desconhecido'}.`, true);
    }
  }
  if (msg.cmd === 'correct_activity'){
    // sucesso não precisa de tratamento — o bridge difunde "activity_correction" que já atualiza o painel
    const status = document.getElementById('activityCorrectionStatus');
    if (status && !msg.ok){
      status.textContent = t('resumo.liveActivityCorrectionError', {error: msg.error || '—'});
      status.style.display = '';
      setTimeout(() => { status.style.display = 'none'; }, 4000);
    }
    // sendWsCommandWithArgs() só garante que o comando SAIU — o bridge pode recusá-lo a seguir; sem isto a fila
    // marcava "enviado" um rótulo nunca gravado, e a exportação contaria dados duplicados.
    if (!msg.ok){
      const ultima = hitlCorrections.find(c => c.kind === 'atividade' && c.sentToBridge);
      if (ultima){ ultima.sentToBridge = false; saveHitlCorrections(hitlCorrections); }
    }
  }
  if (msg.cmd === 'reset_readings'){
    const status = document.getElementById('resetModalStatus');
    const confirmBtn = document.getElementById('resetConfirmBtn');
    if (confirmBtn) confirmBtn.disabled = false;
    if (status){
      status.className = 'modal-status ' + (msg.ok ? 'ok' : 'err');
      status.textContent = msg.ok ? 'Leituras apagadas com sucesso.' : `Falhou: ${msg.error || 'erro desconhecido'}.`;
    }
    if (msg.ok) setTimeout(closeResetModal, 1600);
  }
}

function openResetModal(){
  const status = document.getElementById('resetModalStatus');
  status.className = 'modal-status'; status.textContent = '';
  document.getElementById('resetConfirmBtn').disabled = false;
  document.getElementById('resetModalOverlay').style.display = 'flex';
}
function closeResetModal(){
  document.getElementById('resetModalOverlay').style.display = 'none';
}
function confirmReset(){
  const status = document.getElementById('resetModalStatus');
  const confirmBtn = document.getElementById('resetConfirmBtn');
  if (!liveState.connected){
    status.className = 'modal-status err';
    status.textContent = 'Sem ligação ao bridge — não é possível repor agora.';
    return;
  }
  const sent = sendWsCommand('reset_readings');
  if (!sent){
    status.className = 'modal-status err';
    status.textContent = 'Não foi possível contactar o bridge.';
    return;
  }
  confirmBtn.disabled = true;
  status.className = 'modal-status';
  status.textContent = 'A apagar…';
}

// Exportação clínica FHIR/PDF (item nº7 do backlog) — cobre só os dados desta sessão, não a BD.
let resizeRedrawTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(resizeRedrawTimer);
  resizeRedrawTimer = setTimeout(() => {
    const active = document.querySelector('.nav-item.active');
    if (active && document.getElementById('view-app').classList.contains('active')){
      AFTER_RENDER[active.dataset.view] && AFTER_RENDER[active.dataset.view]();
    }
  }, 150);
});
