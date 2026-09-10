//Retenção, consentimento e limiares personalizados

function requestRetentionSettings(){
  const hint = document.getElementById('retentionHint');
  if (!bridgeWs || bridgeWs.readyState !== WebSocket.OPEN){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = 'Sem ligação ao bridge — a mostrar o valor por omissão do protótipo (30 dias).'; }
    return;
  }
  sendWsCommand('get_retention_days');
}

function handleRetentionDaysResult(msg){
  const input = document.getElementById('retentionDaysInput');
  const hint = document.getElementById('retentionHint');
  if (input && msg.days != null) input.value = msg.days;
  if (input && msg.min_days != null) input.min = msg.min_days;
  if (input && msg.max_days != null) input.max = msg.max_days;
  if (hint){ hint.style.color = ''; hint.textContent = ''; }
}

function saveRetentionDays(){
  const input = document.getElementById('retentionDaysInput');
  const hint = document.getElementById('retentionHint');
  const days = input ? Number(input.value) : NaN;
  if (!Number.isFinite(days) || days <= 0){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = 'Introduz um número de dias válido (maior que 0).'; }
    return;
  }
  const sent = sendWsCommandWithArgs('set_retention_days', { days });
  if (!sent){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = 'Sem ligação ao bridge — não é possível guardar agora.'; }
    return;
  }
  if (hint){ hint.style.color = ''; hint.textContent = 'A guardar…'; }
}

function handleRetentionDaysSaveResult(msg){
  const hint = document.getElementById('retentionHint');
  if (!hint) return;
  if (msg.ok){
    hint.style.color = 'var(--status-good)';
    hint.textContent = `Guardado — a manter registos dos últimos ${msg.days} dias.`;
  } else {
    hint.style.color = 'var(--status-warning)';
    hint.textContent = `Falhou: ${msg.error || 'erro desconhecido'}.`;
  }
}

//Consentimento granular por âmbito (RGPD) — grava ConsentRecord na BD do bridge (storage_advanced.py::CONSENT_SCOPES)
const CONSENT_SCOPES_UI = [
  { scope: 'sensor_data', labelKey: 'consentimento.scopeSensorData', descKey: 'consentimento.scopeSensorDataDesc' },
  { scope: 'analytics', labelKey: 'consentimento.scopeAnalytics', descKey: 'consentimento.scopeAnalyticsDesc' },
  { scope: 'export', labelKey: 'consentimento.scopeExport', descKey: 'consentimento.scopeExportDesc' },
  { scope: 'research', labelKey: 'consentimento.scopeResearch', descKey: 'consentimento.scopeResearchDesc' },
];

liveState.consentStatus = null; //null = sem resposta ainda; {} = respondeu sem âmbitos decididos

function requestConsentStatus(){
  const hint = document.getElementById('consentHint');
  renderConsentScopes();
  if (!bridgeWs || bridgeWs.readyState !== WebSocket.OPEN){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = t('consentimento.noBridgeConnectionHint'); }
    return;
  }
  sendWsCommand('get_consent_status');
}

function handleConsentStatusResult(msg){
  const hint = document.getElementById('consentHint');
  liveState.consentStatus = (msg.status && typeof msg.status === 'object') ? msg.status : {};
  if (hint){
    if (msg.error){ hint.style.color = 'var(--status-warning)'; hint.textContent = msg.error; }
    else { hint.style.color = ''; hint.textContent = ''; }
  }
  renderConsentScopes();
}

function setConsentScope(scope, granted){
  const hint = document.getElementById('consentHint');
  const repNameInput = document.getElementById('consentRepName');
  const representativeName = repNameInput && repNameInput.value.trim() ? repNameInput.value.trim() : undefined;
  const sent = sendWsCommandWithArgs('set_consent', { scope, granted, representative_name: representativeName });
  if (!sent){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = t('consentimento.noBridgeConnectionHint'); }
    renderConsentScopes(); //repõe o toggle (o pedido nem saiu)
    return;
  }
  if (hint){ hint.style.color = ''; hint.textContent = t('consentimento.savingHint'); }
}

function handleConsentResultMessage(msg){
  const hint = document.getElementById('consentHint');
  if (!msg.ok){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = `Falhou: ${msg.error || 'erro desconhecido'}.`; }
    renderConsentScopes(); //repõe o toggle (gravação falhou)
    return;
  }
  if (hint){ hint.style.color = 'var(--status-good)'; hint.textContent = 'Guardado.'; }
  sendWsCommand('get_consent_status'); //BD é a fonte de verdade, não compor localmente
}

function renderConsentScopes(){
  const list = document.getElementById('consentScopesList');
  if (!list) return; //vista "exportar" não está aberta
  const status = liveState.consentStatus;
  list.innerHTML = CONSENT_SCOPES_UI.map(({ scope, labelKey, descKey }) => {
    const entry = status ? status[scope] : null;
    const granted = !!(entry && entry.granted);
    let statusText, statusColor;
    if (!entry){ statusText = t('consentimento.statusNeverDecided'); statusColor = 'var(--text-secondary)'; }
    else if (entry.granted){ statusText = t('consentimento.statusGranted'); statusColor = 'var(--status-good)'; }
    else { statusText = t('consentimento.statusRevoked'); statusColor = 'var(--status-warning)'; }
    return `
      <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
        <label class="consent-toggle"><input type="checkbox" aria-label="${t(labelKey)}" ${granted ? 'checked' : ''} ${status ? '' : 'disabled'} onchange="setConsentScope('${scope}', this.checked)"><span></span></label>
        <div style="min-width:180px;">
          <div style="font-size:13px; font-weight:600;">${t(labelKey)}</div>
          <div style="font-size:12px; color:var(--text-secondary);">${t(descKey)}</div>
        </div>
        <span style="font-size:12px; font-weight:600; color:${statusColor};">${statusText}</span>
      </div>`;
  }).join('');
}

//Baseline comportamental — lê/grava PersonalizedThreshold (storage_advanced.py); só expõe os 3 campos com avaliação em tempo real ligada
function requestThresholds(){
  const hint = document.getElementById('thresholdsHint');
  if (!bridgeWs || bridgeWs.readyState !== WebSocket.OPEN){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = t('vitais.noBridgeConnectionHint'); }
    return;
  }
  sendWsCommand('get_thresholds');
}

function handleThresholdsResult(msg){
  const th = msg.thresholds || {};
  const hrMin = document.getElementById('thresholdHrMin');
  const hrMax = document.getElementById('thresholdHrMax');
  const spo2Min = document.getElementById('thresholdSpo2Min');
  if (hrMin && th.heart_rate_min != null) hrMin.value = th.heart_rate_min;
  if (hrMax && th.heart_rate_max != null) hrMax.value = th.heart_rate_max;
  if (spo2Min && th.spo2_min != null) spo2Min.value = th.spo2_min;
  const hint = document.getElementById('thresholdsHint');
  if (hint){
    hint.style.color = '';
    hint.textContent = th.is_default ? t('vitais.baselineIsDefaultHint') : '';
  }
}

function saveThresholds(){
  const hint = document.getElementById('thresholdsHint');
  const hrMin = Number(document.getElementById('thresholdHrMin')?.value);
  const hrMax = Number(document.getElementById('thresholdHrMax')?.value);
  const spo2Min = Number(document.getElementById('thresholdSpo2Min')?.value);
  if (![hrMin, hrMax, spo2Min].every(Number.isFinite)){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = t('vitais.baselineInvalidHint'); }
    return;
  }
  const sent = sendWsCommandWithArgs('set_thresholds', {
    heart_rate_min: hrMin, heart_rate_max: hrMax, spo2_min: spo2Min,
  });
  if (!sent){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = t('vitais.noBridgeConnectionHint'); }
    return;
  }
  if (hint){ hint.style.color = ''; hint.textContent = t('consentimento.savingHint'); }
}

function handleThresholdsSaveResult(msg){
  const hint = document.getElementById('thresholdsHint');
  if (!hint) return;
  if (msg.ok){
    hint.style.color = 'var(--status-good)';
    hint.textContent = t('vitais.baselineSavedHint');
    handleThresholdsResult({ thresholds: msg.thresholds });
  } else {
    hint.style.color = 'var(--status-warning)';
    hint.textContent = `${t('vitais.baselineSaveFailedPrefix')} ${msg.error || t('vitais.baselineUnknownError')}.`;
  }
}

liveState.vitalAlerts = { hr: null, spo2: null }; //payload do alerta ativo por sinal, ou null

function renderVitalAlertsPanel(){
  const host = document.getElementById('vitalAlertsPanel');
  if (!host) return;
  const active = Object.values(liveState.vitalAlerts).filter(Boolean);
  if (!active.length){ host.innerHTML = ''; return; }
  host.innerHTML = active.map(a => `
    <div class="activity-live-flag anomaly" style="margin-bottom:8px;">⚠ ${escapeHtml(a.explanation)}</div>
  `).join('');
}

//Versionamento/rollback do modelo ML — cmds list_model_versions/activate_model_version em ble_bridge.py
liveState.modelVersions = null; //null = ainda não chegou resposta

