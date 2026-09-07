/* Tendencia semanal real - extraido de bridge-exportacao.js */

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
  // Bug de segurança corrigido (S03 frontend-security): `d.day` e
  // `d.record_count` vêm de `msg.days_summary` (mensagem `daily_trend` do
  // bridge via WebSocket, canal não autenticado — ver toFiniteNumber()
  // acima) e entravam diretamente em innerHTML sem qualquer validação,
  // ao contrário do padrão já usado para hr/spo2/steps em applyLiveVitals().
  // `d.day` passa a ser escapado (texto livre do ponto de vista do
  // browser) e os campos numéricos a passar por toFiniteNumber(), tal como
  // os outros valores vindos do bridge.
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

// Monta a folha de impressão (#clinicalPrintSheet, só visível em
// @media print — ver CSS) com o resumo clínico atual, e chama
// window.print(). É o browser que trata da conversão para PDF (a maioria
// tem "Guardar como PDF" no diálogo de impressão) — evita depender de
// bibliotecas externas de geração de PDF, que a CSP do Artifact bloqueia
// de qualquer forma.
// Folha de impressão melhorada (2026-07-03, pedido do utilizador): cabeçalho
// com marca "CW" (a mesma da barra lateral, para consistência visual),
// rodapé com nota de confidencialidade + numeração de página (via
// contador CSS em @page, ver .print-only mais abaixo — suportado pelo
// motor de impressão do Chromium; noutros motores a página simplesmente
// não mostra o número, sem quebrar o resto do documento), e tabelas com
// alinhamento e espaçamento consistentes (células com padding, cabeçalhos
// com fundo, linhas alternadas para facilitar a leitura).
