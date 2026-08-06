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

TEMPLATES.medicacao = () => {
  const p = selectedPatient();
  const isUtente = currentRole === 'utente';
  const meds = patientMedications(p);
  const todayPct = todayAdherencePct(p);
  // CONTRADIÇÃO REAL corrigida (2026-07-21, reportada pelo utilizador): um
  // dia com d.pct===null (ver withDeviceOffGaps() em generate-demo-data.js)
  // significa "sem dados fiáveis" (dispositivo desligado nesse dia), não
  // "toma em falta" — nunca deve entrar em missedDays nem ser tratado como
  // 0%/100%. `d.pct < 100` sozinho incluiria null por engano (null < 100
  // é true em JS, null coage para 0), por isso filtra explicitamente.
  const missedDays = p.adherenceHistory.filter(d => d.pct != null && d.pct < 100);

  // hasAnyPendingDose (2026-08-06, pedido da utilizadora): se todas as
  // doses de hoje já estão "tomado", a coluna Ação nunca teria nada para
  // mostrar (era sempre "—" em todas as linhas) — deixa de fazer sentido
  // mostrar a coluna. Calculado ANTES de construir doseRows para poder
  // decidir a estrutura da tabela (thead + cada <tr>) de forma
  // consistente — as duas partes têm de concordar no número de colunas.
  const doseStatuses = meds.flatMap(med => med.times.map(time => doseStatus(p.id, med.id, time)));
  const hasAnyPendingDose = doseStatuses.some(s => s !== 'tomado');

  const doseRows = meds.flatMap(med => med.times.map(time => {
    const status = doseStatus(p.id, med.id, time);
    const pill = status === 'tomado' ? pillHtml('good',t('medicacao.statusTaken'))
      : status === 'atrasado' ? pillHtml('warning',t('medicacao.statusLate'))
      : pillHtml('neutral',t('medicacao.statusPending'));
    // Célula de ação só é emitida quando a coluna existe (hasAnyPendingDose)
    // — com a coluna escondida, nunca chega a haver nada para "—" ou para
    // o botão substituírem, então nem sequer se gera essa <td>.
    const actionCell = hasAnyPendingDose
      ? `<td>${status === 'tomado' ? '—' : `<button type="button" class="alert-explain-btn" onclick="markDoseTaken('${p.id}','${med.id}','${time}')">${t('medicacao.markTakenBtn')}</button>`}</td>`
      : '';
    return `<tr><td>${escapeHtml(med.name)}</td><td class="num">${escapeHtml(med.dose)}</td><td class="num">${time}</td><td>${pill}</td>${actionCell}</tr>`;
  })).join('');

  const historyRows = p.adherenceHistory.map(d => d.pct == null ? `
    <div class="meter-row"><span>${d.day} <span class="sim-flag">simulado</span></span><span class="num">—</span></div>
    <p class="empty-hint" style="margin:2px 0 8px;">${t('medicacao.adherenceUnknownDeviceOff')}</p>
  ` : `
    <div class="meter-row"><span>${d.day} <span class="sim-flag">simulado</span></span><span class="num">${d.pct}%</span></div>
    <div class="meter-track"><div class="meter-fill" style="width:${d.pct}%; background:${d.pct < 80 ? 'var(--status-warning)' : 'var(--status-good)'}"></div></div>
  `).join('');

  const correlationNote = missedDays.length
    ? `<p class="empty-hint"><span class="sim-flag">${t('rotina.simFlag')}</span> ${t('medicacao.correlationPrefix')} (${missedDays.map(d => d.day).join(', ')}), ${t('medicacao.correlationSuffix')}</p>`
    : `<p class="empty-hint">${t('medicacao.noIncompleteEmpty')}</p>`;

  // Cartão "Análise de adesão" (window.adherenceAnalytics,
  // medication-reminders.js) — distinto do cartão "Adesão — últimos 6
  // dias" acima: aquele usa `p.adherenceHistory`, dados de EXEMPLO fixos
  // no código; este usa cliques reais de "Marcar como tomado" neste
  // browser (localStorage, só a partir de hoje) — nunca misturados na
  // mesma série, mesma regra já seguida no resto do dashboard.
  const analyticsSummary = window.adherenceAnalytics ? window.adherenceAnalytics.getWeekSummary(p.id) : null;
  const analyticsRecs = window.adherenceAnalytics ? window.adherenceAnalytics.getRecommendations(p.id, p) : [];
  const analyticsCard = analyticsSummary ? `
  <div class="card">
    <div class="card-head"><div><h3>${t('medicacao.analyticsTitle')} <span class="sim-flag">${t('medicacao.analyticsRealDataFlag')}</span></h3><div class="card-sub">${t('medicacao.analyticsSubtitle')}</div></div></div>
    ${analyticsSummary.entries && analyticsSummary.entries.length ? `
      <p class="empty-hint">${t('medicacao.avgPrefix')} ${analyticsSummary.entries.length} ${t('medicacao.avgDaysMid')} <b>${analyticsSummary.avg_adherence}%</b> — ${escapeHtml(analyticsSummary.alert)}</p>
      <p class="empty-hint">${escapeHtml(analyticsSummary.patterns)}</p>
      ${analyticsRecs.length ? `<ul class="empty-hint" style="margin:4px 0 0 18px; padding:0;">${analyticsRecs.map(r => `<li>${escapeHtml(r)}</li>`).join('')}</ul>` : ''}
    ` : `<p class="empty-hint">${escapeHtml(analyticsSummary.alert || t('medicacao.noHistoryYet'))} — ${t('medicacao.analyticsHint')}</p>`}
    <p class="empty-hint">${t('medicacao.prototypeNote2')}</p>
  </div>` : '';

  return `
  <div class="card">
    <div class="card-head"><div><h3>${t('nav.medication')} — ${p.name} <span class="sim-flag">protótipo</span></h3><div class="card-sub">${t('medicacao.card1Subtitle')}</div></div></div>
    ${meds.length ? `
    <table class="data-table">
      <thead><tr><th>${t('medicacao.thMed')}</th><th>${t('medicacao.thDose')}</th><th>${t('medicacao.thTime')}</th><th>${t('medicacao.thStatus')}</th>${hasAnyPendingDose ? `<th>${t('medicacao.thAction')}</th>` : ''}</tr></thead>
      <tbody>${doseRows}</tbody>
    </table>
    ` : `<p class="empty-hint">${t('medicacao.noMedsEmpty')}</p>`}
    ${todayPct !== null ? `<p class="empty-hint">${t('medicacao.todayAdherencePrefix')} <b>${todayPct}%</b> ${t('medicacao.todayAdherenceSuffix')}</p>` : ''}
    <p class="empty-hint">${t('medicacao.prototypeNote1')}</p>
  </div>

  ${analyticsCard}

  ${!isUtente ? `
  <div class="card">
    <div class="card-head"><div><h3>${t('medicacao.manageTitle')}</h3><div class="card-sub">${t('medicacao.manageSubtitle')} ${p.name}</div></div></div>
    ${meds.length ? `
    <table class="data-table">
      <thead><tr><th>${t('medicacao.thMed')}</th><th>${t('medicacao.thDose')}</th><th>${t('medicacao.thTimes')}</th><th></th></tr></thead>
      <tbody>
        ${meds.map(med => `
          <tr>
            <td>${escapeHtml(med.name)}</td>
            <td class="num">${escapeHtml(med.dose || '—')}</td>
            <td class="num">${med.times.join(', ')}</td>
            <td><button class="btn-secondary" onclick="removeMedicationForPatient('${med.id}')">${t('common.delete')}</button></td>
          </tr>
        `).join('')}
      </tbody>
    </table>
    ` : ''}
    <div class="note-form" style="margin-top:12px;">
      <input id="newMedName" type="text" placeholder="${t('medicacao.medNamePlaceholder')}" style="flex:1;">
      <input id="newMedDose" type="text" placeholder="${t('medicacao.dosePlaceholder')}" style="max-width:120px;">
      <input id="newMedTimes" type="text" placeholder="${t('medicacao.timesPlaceholder')}" style="flex:1;">
      <button class="btn-secondary" onclick="addMedicationForPatient()">${t('common.add')}</button>
    </div>
    <p id="newMedFormError" class="empty-hint" style="display:none; color:var(--status-warning);"></p>
    <p class="empty-hint">${t('medicacao.autoFillHint')}</p>
    <div class="note-form" style="margin-top:8px; align-items:center;">
      <span class="empty-hint" style="padding:0;">${t('medicacao.recurringFromLabel')}</span>
      <input id="newMedStartTime" type="time" value="08:00" style="max-width:110px;">
      <button class="btn-secondary" onclick="fillRecurringTimes(8)">${t('medicacao.every8hBtn')}</button>
      <button class="btn-secondary" onclick="fillRecurringTimes(12)">${t('medicacao.every12hBtn')}</button>
      <button class="btn-secondary" onclick="fillRecurringTimes(6)">${t('medicacao.every6hBtn')}</button>
      <button class="btn-secondary" onclick="fillRecurringTimes(24)">${t('medicacao.onceDailyBtn')}</button>
    </div>
    <div class="note-form" style="margin-top:8px; align-items:center;">
      <span class="empty-hint" style="padding:0;">${t('medicacao.customIntervalLabel')}</span>
      <input id="newMedCustomInterval" type="number" min="1" max="24" step="1" placeholder="${t('medicacao.intervalPlaceholder')}" style="max-width:80px;">
      <button class="btn-secondary" onclick="fillRecurringTimesCustom()">${t('medicacao.applyIntervalBtn')}</button>
      <span id="newMedTimesConfirm" class="empty-hint" style="display:none; color:var(--status-good); padding:0;"></span>
    </div>
    <p class="empty-hint">${t('medicacao.manageNote')}</p>
  </div>` : ''}

  <div class="card">
    <div class="card-head"><div><h3>${t('medicacao.historyTitle')}</h3></div></div>
    <div class="device-meter">${historyRows}</div>
    ${correlationNote}
  </div>
`;
};

// Constrói o campo HTML de um dado sensível (morada, NIF): se houver uma
// alteração pendente de aprovação, mostra-a claramente em vez do campo
// editável normal, para não parecer que a alteração já foi aplicada.
function sensitiveProfileFieldHtml(role, field, label, currentValue, inputType){
  const pending = loadPendingProfileChanges();
  const pendingChange = pending[role] && pending[role][field];
  if (pendingChange) {
    return `
    <div class="field">
      <label>${label}</label>
      <div class="empty-hint" style="padding:8px 10px; background:var(--status-warning-bg); border-radius:var(--radius-sm); color:var(--status-warning);">
        ${t('perfil.currentValueLabel')} <b>${escapeHtml(currentValue)}</b><br>
        ${t('perfil.pendingChangeLabel')} <b>${escapeHtml(pendingChange.newValue)}</b>
      </div>
    </div>`;
  }
  return `
    <div class="field">
      <label for="profile_${field}">${label} <span class="empty-hint" style="padding:0; display:inline;">— ${t('perfil.requiresApprovalNote')}</span></label>
      <input id="profile_${field}" type="${inputType || 'text'}" value="${currentValue}">
    </div>`;
}

TEMPLATES.perfil = () => {
  const isUtente = currentRole === 'utente';
  const role = isUtente ? 'utente' : 'clinico';
  const p = loadProfile()[role];
  return `
  <div class="card">
    <div class="card-head"><div><h3>${t('perfil.myProfileTitle')}</h3><div class="card-sub">${t('perfil.myProfileSubtitle')}</div></div></div>
    <div class="field">
      <label for="profile_name">${t('perfil.nameLabel')}</label>
      <input id="profile_name" type="text" value="${p.name}">
    </div>
    <div class="field">
      <label for="profile_email">${t('login.email')}</label>
      <input id="profile_email" type="email" value="${p.email}">
    </div>
    <div class="field">
      <label for="profile_phone">${t('perfil.phoneLabel')}</label>
      <input id="profile_phone" type="tel" value="${p.phone || ''}">
    </div>
    ${sensitiveProfileFieldHtml(role, 'nif', t('perfil.nifLabel'), p.nif || '')}
    ${isUtente ? sensitiveProfileFieldHtml(role, 'address', t('perfil.addressLabel'), p.address || '') : ''}
    ${!isUtente ? `
    <div class="field">
      <label for="profile_institution">${t('perfil.institutionLabel')}</label>
      <input id="profile_institution" type="text" value="${p.institution || ''}">
    </div>
    <div class="field">
      <label for="profile_license">${t('perfil.licenseLabel')}</label>
      <input id="profile_license" type="text" value="${p.license || ''}">
    </div>` : ''}
    <button class="btn-primary" onclick="submitProfileForm()" style="width:auto; padding:8px 20px;">${t('perfil.saveChangesBtn')}</button>
    <p class="modal-status" id="profileSaveStatus"></p>
    <p class="empty-hint">${t('perfil.prototypeStorageEmpty')}</p>
  </div>

  ${isUtente ? `
  <div class="card">
    <div class="card-head"><div><h3>${t('perfil.emergencyContactTitle')}</h3><div class="card-sub">${t('perfil.emergencyContactSubtitle')}</div></div></div>
    <div class="field">
      <label for="profile_caregiverName">${t('perfil.caregiverNameLabel')}</label>
      <input id="profile_caregiverName" type="text" value="${p.caregiverName || ''}">
    </div>
    <div class="field">
      <label for="profile_caregiverPhone">${t('perfil.caregiverPhoneLabel')}</label>
      <input id="profile_caregiverPhone" type="tel" value="${p.caregiverPhone || ''}">
    </div>
    <div class="field">
      <label for="profile_caregiverRelation">${t('perfil.caregiverRelationLabel')}</label>
      <input id="profile_caregiverRelation" type="text" value="${p.caregiverRelation || ''}">
    </div>
    <button class="btn-primary" onclick="submitProfileForm()" style="width:auto; padding:8px 20px;">${t('perfil.saveChangesBtn')}</button>
  </div>` : ''}

  ${!isUtente ? (() => {
    const pending = loadPendingProfileChanges();
    const utentePending = pending.utente || {};
    const entries = Object.entries(utentePending);
    const fieldLabels = { nif: t('perfil.nifLabel'), address: t('perfil.addressLabel') };
    return `
  <div class="card">
    <div class="card-head"><div><h3>${t('perfil.pendingApprovalsTitle')}</h3><div class="card-sub">${t('perfil.pendingApprovalsSubtitle')}</div></div></div>
    ${entries.length ? `
    <table class="data-table">
      <thead><tr><th>${t('perfil.thField')}</th><th>${t('perfil.thRequestedValue')}</th><th>${t('perfil.thRequestedAt')}</th><th></th></tr></thead>
      <tbody>
        ${entries.map(([field, change]) => `
          <tr>
            <td>${fieldLabels[field] || field}</td>
            <td>${escapeHtml(change.newValue)}</td>
            <td class="num">${new Date(change.requestedAt).toLocaleString('pt-PT')}</td>
            <td style="display:flex; gap:8px;">
              <button class="btn-secondary" onclick="approveProfileFieldChange('utente','${field}')">${t('perfil.approveBtn')}</button>
              <button class="btn-secondary" onclick="rejectProfileFieldChange('utente','${field}')">${t('perfil.rejectBtn')}</button>
            </td>
          </tr>
        `).join('')}
      </tbody>
    </table>
    ` : `<p class="empty-hint">${t('perfil.pendingEmpty')}</p>`}
  </div>`;
  })() : ''}
`;
};

TEMPLATES.limites = () => `
  <div class="card">
    <div class="card-head"><div><h3>${t('limites.templateTitle')}</h3><div class="card-sub">${t('limites.templateSubtitle')}</div></div></div>
    <table class="data-table" id="limitsTable">
      <thead><tr><th>${t('limites.thStep')}</th><th>${t('limites.thTime')}</th><th>${t('limites.thActivity')}</th><th>${t('limites.thDmin')}</th><th>${t('limites.thDmax')}</th></tr></thead>
      <tbody>
        ${[
          [1,'00:00','Deitado (sono)',60,120],[2,'07:00','De pé',1,2],[3,'07:06','Higiene oral',1,3],
          [4,'07:18','Duche',5,10],[5,'07:48','Caminhar',2,5],[6,'08:00','Sentado',10,20],
          [7,'08:30','Comer à mão',5,10],[9,'09:30','Sentado',60,90],[13,'13:30','Sentado',60,90],
          [17,'19:00','Comer com talheres',10,20],[21,'22:00','Deitado (sono)',60,120],
        ].map(([step,time,act,dmin,dmax]) => `
          <tr>
            <td class="num">${step}</td><td class="num">${time}</td><td>${act}</td>
            <td class="num"><input class="row-input" type="number" value="${dmin}"></td>
            <td class="num"><input class="row-input" type="number" value="${dmax}"></td>
          </tr>
        `).join('')}
      </tbody>
    </table>
    <p class="empty-hint">${t('limites.prototypeEmpty')}</p>
  </div>
`;

TEMPLATES.exportar = () => `
  <div class="card print-hide">
    <div class="card-head"><div><h3>${t('exportar.fhirCardTitle')}</h3><div class="card-sub">${t('exportar.fhirCardSubtitle')}</div></div></div>
    <div style="display:flex; gap:10px; flex-wrap:wrap;">
      <button class="btn-secondary" onclick="exportFhirSummary()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
        ${t('exportar.exportFhirBtn')}
      </button>
      <button class="btn-secondary" onclick="exportClinicalPdf()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M6 9V2h9l5 5v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-2M6 14h4M6 18h2"/></svg>
        ${t('exportar.printPdfBtn')}
      </button>
    </div>
    <p class="empty-hint">${t('exportar.fhirNoteEmpty')}</p>
  </div>

  <div class="card print-hide">
    <div class="card-head"><div><h3>${t('exportar.realCsvTitle')}</h3><div class="card-sub">${t('exportar.realCsvSubtitle')}</div></div></div>
    <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center;">
      <button class="btn-secondary" onclick="exportRealCsv(24)">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
        ${t('exportar.export24hBtn')}
      </button>
      <button class="btn-secondary" onclick="exportRealCsv(24*7)">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
        ${t('exportar.export7dBtn')}
      </button>
      <button class="btn-secondary" onclick="exportRealCsv(EXPORT_ALL_HOURS)">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
        ${t('exportar.exportAllBtn')}
      </button>
      <span class="empty-hint" id="csvExportHint" style="margin:0;"></span>
    </div>
    <p class="empty-hint">${t('exportar.csvRequiresBridgeEmpty')}</p>
    <p class="empty-hint">${t('exportar.exportAllNote')}</p>
  </div>

  <div class="card print-hide">
    <div class="card-head"><div><h3>${t('exportar.retentionTitle')}</h3><div class="card-sub">${t('exportar.retentionSubtitle')}</div></div></div>
    <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center;">
      <label for="retentionDaysInput" style="font-size:13px; color:var(--text-secondary);">${t('exportar.retentionDaysLabel')}</label>
      <input id="retentionDaysInput" type="number" min="1" max="3650" step="1" style="width:90px;" class="row-input" placeholder="—">
      <button class="btn-secondary" onclick="saveRetentionDays()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/></svg>
        ${t('common.save')}
      </button>
      <span class="empty-hint" id="retentionHint" style="margin:0;"></span>
    </div>
    <p class="empty-hint">${t('exportar.retentionNoteEmpty')}</p>
  </div>

  <div class="card print-hide">
    <div class="card-head"><div><h3>${t('consentimento.title')}</h3><div class="card-sub">${t('consentimento.subtitle')}</div></div></div>
    <div id="consentScopesList" style="display:flex; flex-direction:column; gap:10px;"></div>
    <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-top:10px;">
      <label for="consentRepName" style="font-size:13px; color:var(--text-secondary);">${t('consentimento.repNameLabel')}</label>
      <input id="consentRepName" type="text" style="width:220px;" class="row-input" placeholder="${t('consentimento.repNamePlaceholder')}">
      <span class="empty-hint" id="consentHint" style="margin:0;"></span>
    </div>
    <p class="empty-hint">${t('consentimento.noteEmpty')}</p>
  </div>

  <div class="card print-hide">
    <div class="card-head"><div><h3>${t('modeloVersao.title')}</h3><div class="card-sub">${t('modeloVersao.subtitle')}</div></div></div>
    <div id="modelVersionsList"></div>
    <span class="empty-hint" id="modelVersionsHint" style="margin:0;"></span>
    <p class="empty-hint">${t('modeloVersao.noteEmpty')}</p>
  </div>

  <div class="print-only" id="clinicalPrintSheet"></div>
`;

