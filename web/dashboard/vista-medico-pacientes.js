/* ============================================================
   TEMPLATES — VISTA "MÉDICO / TÉCNICO"
============================================================ */
TEMPLATES.pacientes = () => {
  const mine = accessiblePatients();
  const unassigned = isAdminUser() ? [] : PATIENTS.filter(p => !mine.some(m => m.id === p.id));
  return `
  <div class="card">
    <div class="card-head"><div><h3>${t('pacientes.monitoredCardTitle')}</h3><div class="card-sub">${mine.length} ${t(mine.length===1 ? 'pacientes.assignedSingular' : 'pacientes.assignedPlural')} ${t('pacientes.assignedToAccountSuffix')}${isAdminUser() ? ' ' + t('pacientes.adminFullAccessNote') : ''} · ${t('pacientes.chooseViewingHint')}</div></div></div>
    ${mine.length ? `
    <table class="data-table">
      <thead><tr><th>${t('pacientes.thPatient')}</th><th>${t('pacientes.thDevice')}</th><th>${t('pacientes.thLastSync')}</th><th>${t('pacientes.thStatus')}</th><th>${t('pacientes.thActiveAlerts')}</th><th></th></tr></thead>
      <tbody>
        ${mine.map(p => {
          // Estado ao vivo (2026-07-21, pedido do utilizador: "o estado do
          // wearable do paciente também deve de ser detetado na página
          // médica") — mesmo mecanismo já usado em TEMPLATES.dispositivo
          // (registeredMacFor/liveState.deviceMac): quando o dispositivo
          // real ligado corresponde ao reconhecido para este paciente,
          // mostra sempre "ligado" e "agora", em vez do status/lastSync de
          // demonstração, que ficariam desatualizados face à ligação real.
          const isLive = liveState.connected && liveState.deviceMac && registeredMacFor(p.id, p.mac) === liveState.deviceMac;
          const statusPill = isLive
            ? pillHtml('good', t('pacientes.statusConnected'))
            : pillHtml(p.status==='good'?'good':p.status==='warn'?'warning':'critical', p.status==='good'?t('pacientes.statusConnected'):p.status==='warn'?t('pacientes.statusUnstable'):t('pacientes.statusDisconnected'));
          return `
          <tr${p.id===selectedPatientId ? ' style="background:color-mix(in srgb, var(--accent) 10%, transparent);"' : ''}>
            <td><b>${p.name}</b> · ${p.age} anos${isLive ? ` ${pillHtml('good', t('dispositivo.liveDataBadge'))}` : ''}</td>
            <td class="num">${p.deviceName} · ${registeredMacFor(p.id, p.mac)}</td>
            <td class="num">${isLive ? t('pacientes.lastSyncLiveNow') : p.lastSync}</td>
            <td>${statusPill}</td>
            <td>${(() => {
              // Bug corrigido: esta contagem ignorava o consentimento do
              // próprio paciente (loadConsent(p.id).shareAlerts) — o
              // tooltip do cartão de consentimento promete explicitamente
              // esconder alertas/anomalias da conta Médico/Técnico quando
              // desligado, mas esta pill continuava a mostrar a contagem e
              // severidade reais mesmo assim.
              if (!loadConsent(p.id).shareAlerts) return `<span class="empty-hint" style="padding:0;">${t('pacientes.statusNoConsent')}</span>`;
              const n = activeAlertsCount(p);
              return n > 0 ? pillHtml('critical', n + ' ' + t(n>1 ? 'pacientes.activeAlertsPlural' : 'pacientes.activeAlertsSingular')) : pillHtml('good',t('pacientes.statusNone'));
            })()}</td>
            <td>${p.id===selectedPatientId
              ? `<span class="empty-hint" style="padding:0;">${t('pacientes.statusSelected')}</span>`
              : `<button class="btn-secondary" onclick="selectPatient('${p.id}')">${t('pacientes.selectPatientBtn')}</button>`}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>
    ` : `<p class="empty-hint">${t('pacientes.noPatientsEmpty')} ${unassigned.length ? t('pacientes.seeListBelowEmpty') : ''}</p>`}
    <p class="empty-hint">${t('pacientes.bleNoteEmpty')}</p>
    ${unassigned.length ? `
    <div class="card-head" style="margin-top:18px;"><div><h3>${t('pacientes.otherPatientsCardTitle')}</h3><div class="card-sub">${t('pacientes.otherPatientsCardSubtitle1')} (${ADMIN_EMAIL}) ${t('pacientes.otherPatientsCardSubtitle2')}</div></div></div>
    <table class="data-table">
      <thead><tr><th>${t('pacientes.thPatient')}</th><th>${t('pacientes.thDevice')}</th><th></th></tr></thead>
      <tbody>
        ${unassigned.map(p => `
          <tr>
            <td>${p.name} · ${p.age} anos</td>
            <td class="num">${p.deviceName}</td>
            <td><button class="btn-secondary" onclick="assignPatientToCurrentUser('${p.id}'); renderView('pacientes');">${t('pacientes.assignToMeBtn')}</button></td>
          </tr>`).join('')}
      </tbody>
    </table>
    ` : ''}
  </div>
  ${mine.length ? `
  <div class="card">
    <div class="card-head"><h3>${t('pacientes.alertsBySeverityCardTitle')} ${selectedPatient().name}</h3></div>
    ${!loadConsent().shareAlerts
      ? `<p class="empty-hint">${t('pacientes.noConsentAlertsEmpty')}</p>`
      : (unreadActiveAlerts().length ? unreadActiveAlerts().map((a,i) => alertRow(a,i)).join('') : `<p class="empty-hint">${t('pacientes.noNewAlertsEmpty')}</p>`)}
  </div>
  ` : ''}
`;
};

TEMPLATES.dispositivo = () => `
  ${(() => {
    const p = selectedPatient();
    const ringPct = ((p.ringBufferUsed / p.ringBufferTotal) * 100).toFixed(1);
    // Dados reais (2026-07-21): quando o wearable realmente ligado por BLE
    // (liveState.deviceMac, ver ble_bridge.py::connected_device_mac) é o
    // reconhecido para o paciente selecionado — ver registeredMacFor():
    // o mac de demonstração fixo (PATIENTS[].mac) só é usado enquanto
    // nenhuma ligação real ainda associou este paciente a um dispositivo
    // (handleBridgeMessage('device_status') grava essa associação assim
    // que o wearable real se liga, para a conta que estiver selecionada
    // nesse momento — "reconhecido em qualquer conta"). Mostra a bateria
    // ao vivo em vez do valor de demonstração. O ring buffer continua a
    // ser valor de demonstração mesmo neste caso — o bridge ainda não
    // reenvia a contagem real (só a firmware a imprime em série, ver
    // storageTask em main.cpp) — em vez de fingir um dado que não temos,
    // mostra-se uma nota honesta (dispositivo.ringBufferDemoNote).
    const recognizedMac = registeredMacFor(p.id, p.mac);
    const isLive = liveState.connected && liveState.deviceMac && recognizedMac === liveState.deviceMac;
    const batteryPct = isLive && liveState.batteryPercent != null ? liveState.batteryPercent : p.battery;
    // RAM/Flash/stack são propriedades do FIRMWARE instalado (o mesmo
    // binário em todos os wearables desta frota), não do dispositivo
    // físico individual — BUG CORRIGIDO (2026-07-03, reportado pelo
    // utilizador): "os estados e folga do stack mostram sempre os mesmos
    // dados" ao mudar de paciente. Bateria e ring buffer agora variam por
    // paciente (dados reais de utilização de cada dispositivo); RAM/
    // Flash/stack continuam iguais de propósito, com uma nota explícita
    // do motivo, para não parecer um esquecimento.
    return `
  <div class="grid-2b">
    <div class="card">
      <div class="card-head"><div><h3>${t('dispositivo.deviceStatusCardTitle')} ${p.name}${isLive ? ` ${pillHtml('good', t('dispositivo.liveDataBadge'))}` : ''}</h3><div class="card-sub">XIAO nRF52840 Sense Plus · firmware BLE_GATT_DUMP_V1 · ${recognizedMac}</div></div></div>
      <div class="device-meter" style="gap:14px;">
        <div>
          <div class="meter-row"><span>${t('dispositivo.batteryLabel')}</span><span class="tabular">${batteryPct}%</span></div>
          <div class="meter-track"><div class="meter-fill" style="width:${batteryPct}%; background:${batteryPct<15?'var(--status-critical)':batteryPct<40?'var(--status-warning)':undefined}"></div></div>
        </div>
        <div>
          <div class="meter-row"><span>${t('dispositivo.ringBufferLabel')}</span><span class="tabular">${p.ringBufferUsed.toLocaleString('pt-PT')} / ${p.ringBufferTotal.toLocaleString('pt-PT')} ${t('dispositivo.recordsUnit')}</span></div>
          <div class="meter-track"><div class="meter-fill" style="width:${ringPct}%; background:var(--link)"></div></div>
          ${isLive ? `<p class="empty-hint">${t('dispositivo.ringBufferDemoNote')}</p>` : ''}
        </div>
        <div>
          <div class="meter-row"><span>${t('dispositivo.staticRamLabel')}</span><span class="tabular">17 056 / 237 568 bytes</span></div>
          <div class="meter-track"><div class="meter-fill" style="width:7.2%; background:var(--cat-alimentacao)"></div></div>
        </div>
        <div>
          <div class="meter-row"><span>${t('dispositivo.programFlashLabel')}</span><span class="tabular">173 304 / 811 008 bytes</span></div>
          <div class="meter-track"><div class="meter-fill" style="width:21.4%; background:var(--cat-higiene)"></div></div>
        </div>
      </div>
      <p class="empty-hint">${t('dispositivo.ramFlashSameNoteEmpty')}</p>
    </div>
    <div class="card">
      <div class="card-head"><div><h3>${t('dispositivo.stackSlackCardTitle')}</h3><div class="card-sub">uxTaskGetStackHighWaterMark — ${t('dispositivo.stackSlackCardSubtitle')}</div></div></div>
      <table class="data-table">
        <thead><tr><th>${t('dispositivo.thTask')}</th><th>${t('dispositivo.thReserved')}</th><th>${t('dispositivo.thStatus')}</th></tr></thead>
        <tbody>
          <tr><td>imu_task</td><td class="num">768 words</td><td>${pillHtml('neutral',t('dispositivo.statusPendingConfirmation'))}</td></tr>
          <tr><td>ppg_task</td><td class="num">640 words</td><td>${pillHtml('neutral',t('dispositivo.statusPendingConfirmation'))}</td></tr>
          <tr><td>storage_task</td><td class="num">768 words</td><td>${pillHtml('neutral',t('dispositivo.statusPendingConfirmation'))}</td></tr>
          <tr><td>ble_gatt_dump_task</td><td class="num">1 280 words</td><td>${pillHtml('neutral',t('dispositivo.statusPendingConfirmation'))}</td></tr>
        </tbody>
      </table>
      <p class="empty-hint">${t('dispositivo.optimizationNoteEmpty')}</p>
    </div>
  </div>`;
  })()}
`;

TEMPLATES.anomalias = () => {
  const isUtente = currentRole === 'utente';
  const anomalyLog = currentAnomalyLog();
  return `
  <div class="card">
    <div class="card-head">
      <div><h3>${t('nav.anomalies')} — ${selectedPatient().name}</h3><div class="card-sub">${t('anomalias.detectionSubtitle')}</div></div>
      ${!isUtente && anomalyLog.length ? `<button class="btn-danger" onclick="if(confirm('${t('anomalias.clearAllConfirm')}')) clearAllAnomaliesForPatient();">${t('anomalias.clearAllBtn')}</button>` : ''}
    </div>
    <p class="empty-hint">${t('anomalias.honestLimitationText')}</p>
    ${!loadConsent().shareAlerts ? `
    <p class="empty-hint">${t('anomalias.consentMissingText')}</p>
    ` : `
    <table class="data-table">
      <thead><tr><th>${t('anomalias.thId')}</th><th>${t('anomalias.thType')}</th><th>${t('anomalias.thDetail')}</th><th>${t('anomalias.thDetector')}</th><th>${t('anomalias.thConfidence')}</th><th>${t('anomalias.thSeverity')}</th><th>${t('anomalias.thWhen')}</th>${!isUtente ? '<th></th>' : ''}</tr></thead>
      <tbody>
        ${currentAnomalyLog().length ? currentAnomalyLog().map(a => `
          <tr>
            <td class="num">${a.id}</td>
            <td>${anomalyTypeText(a)}</td>
            <td>${anomalyDetailText(a)}</td>
            <td>${anomalyDetectorText(a)}</td>
            <td class="num">${a.conf}</td>
            <td>${pillHtml(a.sev, a.sev==='critical'?t('anomalias.severityCritical'):a.sev==='serious'?t('anomalias.severitySerious'):t('anomalias.severityWarning'))}</td>
            <td class="num">${a.time}</td>
            ${!isUtente ? `<td><button class="btn-secondary" onclick="if(confirm('${t('anomalias.deleteConfirmPrefix')} (${a.id})${t('anomalias.deleteConfirmSuffix')}')) deleteAnomaly('${a.id}')">${t('anomalias.deleteBtn')}</button></td>` : ''}
          </tr>
        `).join('') : `<tr><td colspan="${isUtente ? 7 : 8}"><p class="empty-hint">${t('anomalias.listEmpty')}</p></td></tr>`}
      </tbody>
    </table>
    `}
  </div>
`;
};

TEMPLATES.alertas = () => {
  const isUtente = currentRole === 'utente';
  const all = currentAlerts(); // inclui lidos, exclui apagados
  return `
  <div class="card">
    <div class="card-head">
      <div><h3>${t('nav.alertHistory')} — ${selectedPatient().name}</h3><div class="card-sub">${t('alertas.allAlertsSubtitle')}</div></div>
      ${!isUtente && all.length ? `<button class="btn-danger" onclick="if(confirm('${t('alertas.clearAllConfirm')}')) clearAllAlertsForPatient();">${t('alertas.clearAllBtn')}</button>` : ''}
    </div>
    <p class="empty-hint">${t('alertas.honestLimitationText')}</p>
    ${all.length ? `
    <table class="data-table">
      <thead><tr><th>${t('alertas.thTitle')}</th><th>${t('alertas.thDetail')}</th><th>${t('alertas.thSeverity')}</th><th>${t('alertas.thWhen')}</th><th>${t('alertas.thStatus')}</th>${!isUtente ? '<th></th>' : ''}</tr></thead>
      <tbody>
        ${all.map(a => {
          const fullKey = patientAlertKey(selectedPatientId, a.key);
          const read = isAlertRead(fullKey);
          return `
          <tr>
            <td>${alertField(a,'title')}</td>
            <td>${alertField(a,'desc')}</td>
            <td>${pillHtml(a.sev, a.sev==='critical'?t('alertas.severityCritical'):a.sev==='serious'?t('alertas.severitySerious'):t('alertas.severityWarning'))}</td>
            <td class="num">${alertField(a,'time')}</td>
            <td>${read ? pillHtml('neutral',t('alertas.statusRead')) : pillHtml('warning',t('alertas.statusNew'))}</td>
            ${!isUtente ? `<td><button class="btn-secondary" onclick="deleteAlert('${fullKey}')">${t('alertas.deleteBtn')}</button></td>` : ''}
          </tr>`;
        }).join('')}
      </tbody>
    </table>
    ` : `<p class="empty-hint">${t('alertas.listEmpty')}</p>`}
  </div>
`;
};

TEMPLATES.emergencias = () => {
  const isUtente = currentRole === 'utente';
  const emergencyLog = currentEmergencyLog();
  const hasClearable = emergencyLog.some(e => e.status !== 'ativo');
  return `
  <div class="card">
    <div class="card-head">
      <div><h3>${t('nav.emergencies')} — ${selectedPatient().name}</h3><div class="card-sub">${t('emergencias.detectionSubtitle')}</div></div>
      ${!isUtente && hasClearable ? `<button class="btn-danger" onclick="if(confirm('${t('emergencias.clearAllConfirm')}')) clearAllEmergenciesForPatient();">${t('emergencias.clearAllBtn')}</button>` : ''}
    </div>
    <p class="empty-hint">${t('emergencias.bridgeInfoText')}</p>
    ${currentEmergencyLog().length ? `
    <table class="data-table">
      <thead><tr><th>${t('emergencias.thId')}</th><th>${t('emergencias.thType')}</th><th>${t('emergencias.thWhen')}</th><th>${t('emergencias.thSource')}</th><th>${t('emergencias.thStatus')}</th><th>${t('emergencias.thNote')}</th><th></th></tr></thead>
      <tbody>
        ${currentEmergencyLog().map(e => `
          <tr>
            <td class="num">${e.id}</td>
            <td>${emergencyLabelText(e)}${e.explanation ? `<div class="table-subtext">${escapeHtml(e.explanation)}</div>` : ''}</td>
            <td class="num">${e.time}</td>
            <td>${e.live ? pillHtml('critical',t('emergencias.sourceLive')) : pillHtml('neutral',t('emergencias.sourceDemo'))}</td>
            <td>${pillHtml(e.status==='ativo'?'critical':e.status==='cancelado'?'neutral':'good', e.status==='ativo'?t('emergencias.statusActive'):e.status==='cancelado'?t('emergencias.statusCancelled'):t('emergencias.statusResolved'))}</td>
            <td>${emergencyNoteText(e) || '—'}</td>
            <td style="display:flex; gap:6px; flex-wrap:wrap;">
              ${e.status==='ativo' ? `<button class="btn-danger" onclick="openEmergencyCancelModal('${e.id}')">${t('emergencias.cancelAlertBtn')}</button>` : ''}
              ${e.liveSeq != null ? `<button class="btn-secondary" onclick="openEpisodeTimelineModal(${e.liveSeq})">${t('episodio.viewBtn')}</button>` : ''}
              ${!isUtente && e.status!=='ativo' ? `<button class="btn-secondary" onclick="if(confirm('${t('emergencias.deleteConfirmPrefix')} (${e.id})${t('emergencias.deleteConfirmSuffix')}')) deleteEmergencyRecord('${e.id}')">${t('emergencias.deleteBtn')}</button>` : ''}
            </td>
          </tr>
        `).join('')}
      </tbody>
    </table>
    ` : `<p class="empty-hint">${t('emergencias.listEmpty')}</p>`}
    <p class="empty-hint">${t('emergencias.cancelInfoText')}</p>
  </div>
`;
};
