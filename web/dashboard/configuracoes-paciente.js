/* Retencao, consentimento e limiares personalizados - extraido de bridge-exportacao.js */

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

/* ------------------------------------------------------------
   CONSENTIMENTO GRANULAR POR ÂMBITO (RGPD, 2026-08-05)
   ------------------------------------------------------------
   Distinto do `.consent-toggle` de "definicoes" (partilha com
   cuidadores, guardado em localStorage) — isto lê/grava
   `ConsentRecord` real na base de dados do bridge (ver
   bridge/storage_advanced.py::CONSENT_SCOPES), por isso a função
   chama-se setConsentScope() e não setConsent(), para não colidir com
   a já existente. Cada âmbito é independente: sensor_data, analytics,
   export, research (mesma ordem de sa.CONSENT_SCOPES).
------------------------------------------------------------- */
const CONSENT_SCOPES_UI = [
  { scope: 'sensor_data', labelKey: 'consentimento.scopeSensorData', descKey: 'consentimento.scopeSensorDataDesc' },
  { scope: 'analytics', labelKey: 'consentimento.scopeAnalytics', descKey: 'consentimento.scopeAnalyticsDesc' },
  { scope: 'export', labelKey: 'consentimento.scopeExport', descKey: 'consentimento.scopeExportDesc' },
  { scope: 'research', labelKey: 'consentimento.scopeResearch', descKey: 'consentimento.scopeResearchDesc' },
];

// null = ainda não chegou nenhuma resposta do bridge (distinto de "{}" =
// chegou mas nenhum âmbito foi decidido, o que get_consent_status também
// pode devolver por scope).
liveState.consentStatus = null;

function requestConsentStatus(){
  const hint = document.getElementById('consentHint');
  renderConsentScopes(); // desenha a lista já (estado "a carregar/sem ligação")
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
    renderConsentScopes(); // repõe o toggle visualmente (o pedido nem saiu)
    return;
  }
  if (hint){ hint.style.color = ''; hint.textContent = t('consentimento.savingHint'); }
}

function handleConsentResultMessage(msg){
  const hint = document.getElementById('consentHint');
  if (!msg.ok){
    if (hint){ hint.style.color = 'var(--status-warning)'; hint.textContent = `Falhou: ${msg.error || 'erro desconhecido'}.`; }
    renderConsentScopes(); // repõe o toggle para o estado real (a gravação falhou)
    return;
  }
  if (hint){ hint.style.color = 'var(--status-good)'; hint.textContent = 'Guardado.'; }
  // Pede o estado completo de novo em vez de compor localmente — a base de
  // dados (signed_at/version reais) é sempre a fonte de verdade, o mesmo
  // raciocínio de handleRetentionDaysSaveResult acima.
  sendWsCommand('get_consent_status');
}

function renderConsentScopes(){
  const list = document.getElementById('consentScopesList');
  if (!list) return; // vista "exportar" não está aberta
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

/* ------------------------------------------------------------
   BASELINE COMPORTAMENTAL PERSONALIZADA (2026-08-05)
   ------------------------------------------------------------
   Lê/grava PersonalizedThreshold real (ver bridge/storage_advanced.py) e
   mostra alertas de FC/SpO2 em tempo real (bridge/vital_alerts.py) quando
   uma leitura sai dos limiares definidos. Só expõe no formulário os 3
   campos com avaliação em tempo real de facto ligada (heart_rate_min/max,
   spo2_min) — o esquema tem mais 4 campos (inactivity_threshold_seconds,
   sleep/activity_target_minutes, steps_target_daily) sem nenhuma rotina a
   avaliá-los ainda; não expor um controlo que não faz nada é a mesma
   disciplina já aplicada ao resto do dashboard (ex.: ACTIVITY_ML_DISCLAIMER).
------------------------------------------------------------- */
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

// Estado ao vivo dos alertas de sinal vital (ver kind "vital_alert" em
// handleBridgeMessage) — indexado por sinal ('hr'/'spo2'), cada entrada é
// o payload do alerta ativo ou null (dentro dos limiares).
liveState.vitalAlerts = { hr: null, spo2: null };

function renderVitalAlertsPanel(){
  const host = document.getElementById('vitalAlertsPanel');
  if (!host) return;
  const active = Object.values(liveState.vitalAlerts).filter(Boolean);
  if (!active.length){ host.innerHTML = ''; return; }
  host.innerHTML = active.map(a => `
    <div class="activity-live-flag anomaly" style="margin-bottom:8px;">⚠ ${escapeHtml(a.explanation)}</div>
  `).join('');
}

/* ------------------------------------------------------------
   VERSIONAMENTO E ROLLBACK DO MODELO ML (2026-08-05)
   ------------------------------------------------------------
   Lista as versões registadas do classificador de atividade
   (bridge/storage_advanced.py::MlModelVersion) e permite ativar
   (rollback/promoção) uma delas em runtime, sem reiniciar o bridge — ver
   cmds "list_model_versions"/"activate_model_version" em ble_bridge.py.
------------------------------------------------------------- */
liveState.modelVersions = null; // null = ainda não chegou nenhuma resposta

