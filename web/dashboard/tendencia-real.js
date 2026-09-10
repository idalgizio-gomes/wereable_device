function requestRealTrend(days){
  const status = document.getElementById('realTrendStatus');
  if (!liveState.connected){
    if (status) status.textContent = 'Sem ligação ao bridge — liga o dispositivo para veres histórico real.';
    return;
  }
  const sent = sendWsCommandWithArgs('get_daily_trend', { days });
  if (!sent){
    if (status) status.textContent = 'Não foi possível contactar o bridge.';
    return;
  }
  if (status) status.textContent = 'A carregar histórico real…';
}

function renderRealTrendTable(){
  const body = document.getElementById('realTrendBody');
  const status = document.getElementById('realTrendStatus');
  if (!body) return; // vista "Tendência semanal" não está ativa agora
  const rows = liveState.realTrend || [];
  if (!rows.length){
    body.innerHTML = '';
    if (status){
      status.textContent = liveState.connected
        ? t('tendencia.realHistoryNotEnoughData')
        : t('tendencia.realHistoryNotConnected');
    }
    return;
  }
  if (status) status.textContent = '';
  //dados do bridge (canal não autenticado): d.day escapado, numéricos via toFiniteNumber()
  body.innerHTML = rows.map(d => {
    const hrSamples = toFiniteNumber(d.hr_samples) || 0;
    const avgHr = toFiniteNumber(d.avg_hr) || 0;
    const hrText = hrSamples > 0 ? `${Math.round(avgHr)} bpm (${hrSamples} ${t('tendencia.realHistoryReadings')})` : t('tendencia.realHistoryNoReadings');
    const maxSteps = toFiniteNumber(d.max_steps), minSteps = toFiniteNumber(d.min_steps);
    const stepsText = (maxSteps != null && minSteps != null) ? `${maxSteps - minSteps}` : '—';
    const recordCount = toFiniteNumber(d.record_count) || 0;
    return `<tr><td>${escapeHtml(d.day)}</td><td class="num">${recordCount}</td><td>${hrText}</td><td class="num">${stepsText}</td></tr>`;
  }).join('');
}

//folha de impressão (#clinicalPrintSheet, @media print) via window.print(), sem libs externas de PDF
