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
