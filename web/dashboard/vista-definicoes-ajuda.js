TEMPLATES.definicoes = () => `
  <div class="card">
    <div class="card-head"><h3>${t('definicoes.alertPrefsTitle')}</h3></div>
    <table class="data-table">
      <thead><tr><th>${t('definicoes.thAlert')}</th><th>${t('definicoes.thThreshold')}</th><th>${t('definicoes.thNotify')}</th></tr></thead>
      <tbody>
        <tr><td>${t('definicoes.alertHighHr')}</td><td class="num">&gt; 100 bpm</td><td>${pillHtml('good',t('definicoes.statusActive'))}</td></tr>
        <tr><td>${t('definicoes.alertLowSpo2')}</td><td class="num">&lt; 92%</td><td>${pillHtml('good',t('definicoes.statusActive'))}</td></tr>
        <tr><td>${t('definicoes.alertInactivity')}</td><td class="num">&gt; 3h</td><td>${pillHtml('good',t('definicoes.statusActive'))}</td></tr>
        <tr><td>${t('definicoes.alertFall')}</td><td class="num">${t('definicoes.thresholdImmediate')}</td><td>${pillHtml('good',t('definicoes.statusActive'))}</td></tr>
      </tbody>
    </table>
    <p class="empty-hint">${t('definicoes.alertPrefsEmpty')}</p>
  </div>

  ${(() => {
    const mode = getAlertMode();
    const b = computePersonalBaseline();
    const fcPersonal = b.fc.mean + PERSONAL_THRESHOLD_K * b.fc.sd;
    const sonoPersonalMin = Math.max(0, b.sono.mean - PERSONAL_THRESHOLD_K * b.sono.sd);
    const passosPersonalMin = Math.max(0, Math.round(b.passos.mean - PERSONAL_THRESHOLD_K * b.passos.sd));
    return `
  <div class="card">
    <div class="card-head"><div><h3>${t('definicoes.personalThresholdsTitle')} <span class="sim-flag">${t('definicoes.prototypeFlag')}</span></h3><div class="card-sub">${t('definicoes.personalThresholdsSubtitle')}</div></div></div>
    <div class="role-toggle" style="margin-bottom:14px;">
      <button type="button" aria-pressed="${mode === 'populacional'}" onclick="setAlertMode('populacional')">${t('definicoes.fixedThresholdsBtn')}</button>
      <button type="button" aria-pressed="${mode === 'personalizado'}" onclick="setAlertMode('personalizado')">${t('definicoes.personalThresholdsTitle')}</button>
    </div>
    <table class="data-table">
      <thead><tr><th>${t('definicoes.thMetric')}</th><th>${t('definicoes.thBaseline')}</th><th>${t('definicoes.thFixedThreshold')}</th><th>${t('definicoes.thPersonalThreshold')}</th><th>${t('definicoes.thInUse')}</th></tr></thead>
      <tbody>
        <tr>
          <td>${t('definicoes.metricRestingHr')}</td>
          <td class="num">${b.fc.mean.toFixed(0)} ± ${b.fc.sd.toFixed(1)} bpm</td>
          <td class="num">&gt; 100 bpm</td>
          <td class="num">&gt; ${fcPersonal.toFixed(0)} bpm</td>
          <td>${pillHtml(mode === 'personalizado' ? 'good' : 'neutral', mode === 'personalizado' ? t('definicoes.statusPersonal') : t('definicoes.statusFixed'))}</td>
        </tr>
        <tr>
          <td>${t('definicoes.metricSleepPerNight')}</td>
          <td class="num">${b.sono.mean.toFixed(1)} ± ${b.sono.sd.toFixed(1)} h</td>
          <td class="num">&lt; 5 h</td>
          <td class="num">&lt; ${sonoPersonalMin.toFixed(1)} h</td>
          <td>${pillHtml(mode === 'personalizado' ? 'good' : 'neutral', mode === 'personalizado' ? t('definicoes.statusPersonal') : t('definicoes.statusFixed'))}</td>
        </tr>
        <tr>
          <td>${t('definicoes.metricDailySteps')}</td>
          <td class="num">${Math.round(b.passos.mean)} ± ${Math.round(b.passos.sd)}</td>
          <td class="num">&lt; 1500</td>
          <td class="num">&lt; ${passosPersonalMin}</td>
          <td>${pillHtml(mode === 'personalizado' ? 'good' : 'neutral', mode === 'personalizado' ? t('definicoes.statusPersonal') : t('definicoes.statusFixed'))}</td>
        </tr>
      </tbody>
    </table>
    <p class="empty-hint">${t('definicoes.personalCalcPre')} ${PERSONAL_THRESHOLD_K}${t('definicoes.personalCalcPost')}</p>
  </div>`;
  })()}

  ${(() => {
    const c = loadConsent();
    return `
  <div class="card">
    <div class="card-head"><div><h3>${t('definicoes.consentTitle')}</h3><div class="card-sub">${t('definicoes.consentSubtitle')}</div></div></div>
    <table class="data-table">
      <thead><tr><th>${t('definicoes.thWhatShared')}</th><th>${t('definicoes.thWithWhom')}</th><th></th></tr></thead>
      <tbody>
        <tr>
          <td>${t('definicoes.consentVitals')}</td>
          <td>${t('definicoes.clinicalTeam')}</td>
          <td><label class="consent-toggle"><input type="checkbox" aria-label="${t('definicoes.shareVitalsAria')}" ${c.shareVitals?'checked':''} onchange="setConsent('shareVitals', this.checked)"><span></span></label></td>
        </tr>
        <tr>
          <td>${t('definicoes.consentRoutine')}</td>
          <td>${t('definicoes.clinicalTeam')}</td>
          <td><label class="consent-toggle"><input type="checkbox" aria-label="${t('definicoes.shareRoutineAria')}" ${c.shareRoutine?'checked':''} onchange="setConsent('shareRoutine', this.checked)"><span></span></label></td>
        </tr>
        <tr>
          <td>${t('definicoes.consentAlerts')}</td>
          <td>${t('definicoes.clinicalTeam')}</td>
          <td><label class="consent-toggle"><input type="checkbox" aria-label="${t('definicoes.shareAlertsAria')}" ${c.shareAlerts?'checked':''} onchange="setConsent('shareAlerts', this.checked)"><span></span></label></td>
        </tr>
      </tbody>
    </table>
    <p class="empty-hint">${c.lastChanged ? `${t('definicoes.lastChangedLabel')} ${new Date(c.lastChanged).toLocaleString('pt-PT')}.` : ''} ${t('definicoes.consentEmpty')}</p>
  </div>`;
  })()}

  ${(() => {
    const team = loadCaregiverTeam();
    return `
  <div class="card">
    <div class="card-head"><div><h3>${t('definicoes.caregiverTeamTitle')}</h3><div class="card-sub">${t('definicoes.caregiverTeamSubtitle')}</div></div></div>
    <table class="data-table">
      <thead><tr><th>${t('definicoes.thName')}</th><th>${t('definicoes.thRole')}</th><th>${t('definicoes.thSeesAlerts')}</th><th>${t('definicoes.thEditsNotes')}</th><th></th></tr></thead>
      <tbody>
        ${team.length ? team.map(m => `
          <tr>
            <td>${escapeHtml(m.name)}</td>
            <td>${m.role}</td>
            <td><label class="consent-toggle"><input type="checkbox" aria-label="${t('definicoes.allowTo')} ${escapeHtml(m.name)} ${t('definicoes.allowViewAlertsSuffix')}" ${m.canViewAlerts?'checked':''} onchange="setCaregiverPermission('${m.id}','canViewAlerts', this.checked)"><span></span></label></td>
            <td><label class="consent-toggle"><input type="checkbox" aria-label="${t('definicoes.allowTo')} ${escapeHtml(m.name)} ${t('definicoes.allowEditSuffix')}" ${m.canEdit?'checked':''} onchange="setCaregiverPermission('${m.id}','canEdit', this.checked)"><span></span></label></td>
            <td><button class="btn-secondary" onclick="removeCaregiver('${m.id}')">${t('definicoes.removeCaregiverBtn')}</button></td>
          </tr>
        `).join('') : `<tr><td colspan="5"><p class="empty-hint">${t('definicoes.caregiverTeamEmpty')}</p></td></tr>`}
      </tbody>
    </table>
    <div class="note-form" style="margin-top:12px;">
      <input id="newCaregiverName" type="text" placeholder="${t('definicoes.newCaregiverPlaceholder')}" style="flex:1;">
      <select id="newCaregiverRole">
        <option>${t('definicoes.roleFamilyOption')}</option>
        <option>${t('definicoes.rolePaidCaregiverOption')}</option>
      </select>
      <button class="btn-secondary" onclick="addCaregiver()">${t('definicoes.inviteBtn')}</button>
    </div>
    <p class="empty-hint">${t('definicoes.caregiverTeamNote')}</p>
  </div>`;
  })()}

  ${(() => {
    const schedule = loadCaregiverSchedule();
    const preset = currentSchedulePreset(schedule);
    const overrideActive = isScheduleOverrideActiveToday();
    const WEEKDAY_KEYS = ['definicoes.weekdayMon','definicoes.weekdayTue','definicoes.weekdayWed','definicoes.weekdayThu','definicoes.weekdayFri','definicoes.weekdaySat','definicoes.weekdaySun'];
    return `
  <div class="card">
    <div class="card-head"><div><h3>${t('definicoes.scheduleTitle')}</h3><div class="card-sub">${t('definicoes.scheduleSubtitle')}</div></div></div>
    <div class="role-toggle" style="margin-bottom:14px;">
      <button type="button" aria-pressed="${preset === 'weekdays'}" onclick="applyCaregiverSchedulePreset('weekdays')">${t('definicoes.scheduleWeekdaysBtn')}</button>
      <button type="button" aria-pressed="${preset === 'weekend'}" onclick="applyCaregiverSchedulePreset('weekend')">${t('definicoes.scheduleWeekendBtn')}</button>
      <button type="button" aria-pressed="${preset === 'custom'}" onclick="applyCaregiverSchedulePreset('custom')">${t('definicoes.scheduleCustomBtn')}</button>
    </div>
    <table class="data-table">
      <thead><tr><th>${t('definicoes.scheduleThDay')}</th><th></th><th>${t('definicoes.scheduleThStart')}</th><th>${t('definicoes.scheduleThEnd')}</th></tr></thead>
      <tbody>
        ${[0,1,2,3,4,5,6].map(wd => {
          const entry = schedule.find(w => w.weekday === wd);
          const enabled = !!entry;
          return `
        <tr>
          <td>${t(WEEKDAY_KEYS[wd])}</td>
          <td><label class="consent-toggle"><input type="checkbox" aria-label="${t('definicoes.scheduleDayAria')} ${t(WEEKDAY_KEYS[wd])}" ${enabled?'checked':''} onchange="setCaregiverScheduleDay(${wd},'enabled',this.checked)"><span></span></label></td>
          <td><input type="time" class="row-input" value="${entry ? entry.start : '09:00'}" ${enabled?'':'disabled'} onchange="setCaregiverScheduleDay(${wd},'start',this.value)"></td>
          <td><input type="time" class="row-input" value="${entry ? entry.end : '17:00'}" ${enabled?'':'disabled'} onchange="setCaregiverScheduleDay(${wd},'end',this.value)"></td>
        </tr>`;
        }).join('')}
      </tbody>
    </table>
    <div style="display:flex; align-items:center; gap:10px; margin-top:12px; padding-top:12px; border-top:1px solid var(--border-soft);">
      <label class="consent-toggle"><input type="checkbox" aria-label="${t('definicoes.scheduleOverrideAria')}" ${overrideActive?'checked':''} onchange="toggleScheduleOverrideToday(this.checked)"><span></span></label>
      <span style="font-size:13px;">${t('definicoes.scheduleOverrideLabel')}</span>
    </div>
    <p class="empty-hint">${t('definicoes.scheduleEmpty')}</p>
  </div>`;
  })()}

  ${(() => {
    const ec = loadEmergencyContact();
    return `
  <div class="card">
    <div class="card-head"><div><h3>${t('definicoes.emergencyContactTitle')}</h3><div class="card-sub">${t('definicoes.emergencyContactSubtitle')}</div></div></div>
    <div class="note-form">
      <input type="text" value="${escapeHtml(ec.name)}" placeholder="${t('definicoes.emergencyContactNamePlaceholder')}" style="flex:1;" onchange="updateEmergencyContactField('name', this.value)">
      <input type="tel" value="${escapeHtml(ec.phone)}" placeholder="${t('definicoes.emergencyContactPhonePlaceholder')}" onchange="updateEmergencyContactField('phone', this.value)">
      <input type="text" value="${escapeHtml(ec.relation)}" placeholder="${t('definicoes.emergencyContactRelationPlaceholder')}" onchange="updateEmergencyContactField('relation', this.value)">
    </div>
    <p class="empty-hint">${t('definicoes.emergencyContactNote')}</p>
  </div>`;
  })()}

  <div class="card danger-zone">
    <div class="card-head"><div><h3>${t('definicoes.dangerZoneTitle')}</h3><div class="card-sub">${t('definicoes.dangerZoneSubtitle')}</div></div></div>
    <div style="display:flex; align-items:center; justify-content:space-between; gap:14px; flex-wrap:wrap;">
      <div>
        <div style="font-weight:600; font-size:13px;">${t('definicoes.resetDeviceTitle')}</div>
        <p class="empty-hint" style="padding:2px 0 0;">${t('definicoes.resetDeviceDesc')}</p>
      </div>
      <button class="btn-danger" onclick="openResetModal()">${t('definicoes.resetDeviceBtn')}</button>
    </div>
    <div style="display:flex; align-items:center; justify-content:space-between; gap:14px; flex-wrap:wrap; margin-top:14px; padding-top:14px; border-top:1px solid var(--border-soft);">
      <div>
        <div style="font-weight:600; font-size:13px;">${t('definicoes.eraseDataTitle')}</div>
        <p class="empty-hint" style="padding:2px 0 0;">${t('definicoes.eraseDataDesc')}</p>
        <p class="empty-hint" style="padding:2px 0 0;">${t('definicoes.eraseDataAutoTtlNote')}</p>
      </div>
      <button class="btn-danger" onclick="if(confirm(t('definicoes.eraseDataConfirm'))) eraseAllLocalData();">${t('definicoes.eraseDataBtn')}</button>
    </div>
  </div>
`;

/* ============================================================
   TEMPLATES — AJUDA & SOBRE (comum aos dois perfis)
============================================================ */
const FAQ_ITEMS = [
  { q: 'ajuda.faqQ1', a: 'ajuda.faqA1' },
  { q: 'ajuda.faqQ2', a: 'ajuda.faqA2' },
  { q: 'ajuda.faqQ3', a: 'ajuda.faqA3' },
  { q: 'ajuda.faqQ4', a: 'ajuda.faqA4' },
  { q: 'ajuda.faqQ5', a: 'ajuda.faqA5' },
  { q: 'ajuda.faqQ6', a: 'ajuda.faqA6' },
  { q: 'ajuda.faqQ7', a: 'ajuda.faqA7' },
  { q: 'ajuda.faqQ8', a: 'ajuda.faqA8' },
  { q: 'ajuda.faqQ9', a: 'ajuda.faqA9' },
  { q: 'ajuda.faqQ10', a: 'ajuda.faqA10' },
  { q: 'ajuda.faqQ11', a: 'ajuda.faqA11' },
  { q: 'ajuda.faqQ12', a: 'ajuda.faqA12' },
  { q: 'ajuda.faqQ13', a: 'ajuda.faqA13' },
  { q: 'ajuda.faqQ14', a: 'ajuda.faqA14' },
  { q: 'ajuda.faqQ15', a: 'ajuda.faqA15' },
  { q: 'ajuda.faqQ16', a: 'ajuda.faqA16' },
  { q: 'ajuda.faqQ17', a: 'ajuda.faqA17' },
];

TEMPLATES.ajuda = () => `
  <div class="card">
    <div class="card-head"><div><h3 data-i18n="help.faqTitle">${t('help.faqTitle')}</h3></div></div>
    <div class="faq-list">
      ${FAQ_ITEMS.map((item, i) => `
        <details class="faq-item" ${i===0 ? 'open' : ''}>
          <summary>${t(item.q)}</summary>
          <p>${t(item.a)}</p>
        </details>
      `).join('')}
    </div>
  </div>

  <div class="card">
    <div class="card-head"><div><h3 data-i18n="help.devTitle">${t('help.devTitle')}</h3></div></div>
    <table class="data-table">
      <tbody>
        <tr><td style="width:180px; color:var(--text-muted);">${t('ajuda.aboutProjectLabel')}</td><td>${t('ajuda.aboutProjectValue')}</td></tr>
        <tr><td style="color:var(--text-muted);">${t('ajuda.aboutRepoLabel')}</td><td><a href="https://github.com/idalgizio-gomes/wereable_device" target="_blank" rel="noopener">github.com/idalgizio-gomes/wereable_device</a></td></tr>
        <tr><td style="color:var(--text-muted);">${t('ajuda.aboutHardwareLabel')}</td><td>Seeed Studio XIAO nRF52840 Sense Plus (IMU LSM6DS3, PPG MAX3010x, BLE)</td></tr>
        <tr><td style="color:var(--text-muted);">${t('ajuda.aboutAuthorLabel')}</td><td>Idalgízio Gomes</td></tr>
        <tr><td style="color:var(--text-muted);">${t('ajuda.aboutStatusLabel')}</td><td>${pillHtml('warning',t('ajuda.aboutStatusValue'))}</td></tr>
      </tbody>
    </table>
    <p class="empty-hint">${t('ajuda.aboutStatusHintPre')} <code>PROJECT_STATUS.md</code> ${t('ajuda.aboutStatusHintPost')}</p>
  </div>
`;
