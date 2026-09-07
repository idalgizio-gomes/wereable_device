
TEMPLATES.resumo = () => `
  <div class="stat-row">
    ${statTile('walk',t('resumo.movementCardTitle'),t('resumo.movementDemoValue'),'', 'var(--cat-atividade)', 'stat-movement')}
    ${statTile('moon',t('resumo.sleepCardTitle'),'7h 24m','', 'var(--cat-dormir)', '', true)}
    ${statTile('drop',t('resumo.nutritionCardTitle'),t('resumo.nutritionDemoValue'),'', 'var(--cat-alimentacao)', '', true)}
    ${statTile('heart',t('resumo.heartRateCardTitle'),'—','bpm', 'var(--status-good)', 'stat-hr')}
    ${statTile('zap',t('resumo.spo2CardTitle'),'—','%', 'var(--status-good)', 'stat-spo2', false, 'spo2-hint')}
  </div>

  <div class="card activity-live-card">
    <div class="card-head">
      <div><h3>${t('resumo.liveActivityCardTitle')} <span class="experimental-flag" title="${t('resumo.liveActivityDisclaimer')}">${t('resumo.experimentalBadge')}</span></h3><div class="card-sub">${t('resumo.liveActivityCardSubtitle')}</div></div>
    </div>
    <div id="liveActivityPanel"></div>
  </div>

  <div class="grid-2">
    <div class="card">
      <div class="card-head">
        <div><h3>${t('resumo.routineTodayCardTitle')} <span class="sim-flag">${t('resumo.simulatedDataBadge')}</span></h3><div class="card-sub">${t('resumo.routineTodayCardSubtitle')}</div></div>
      </div>
      <canvas id="cvRoutineToday" height="150" role="img" aria-label="${t('resumo.routineTodayChartAria')}"></canvas>
      <canvas id="cvRoutineAnom" height="150" style="margin-top:10px;" role="img" aria-label="${t('resumo.routineAnomalyChartAria')}"></canvas>
      ${legendHtml()}
    </div>

    <div class="card">
      <div class="card-head"><h3>${t('resumo.recentAlertsCardTitle')}</h3></div>
      ${unreadActiveAlerts().length ? unreadActiveAlerts().map((a,i) => alertRow(a,i)).join('') : `<p class="empty-hint">${t('resumo.recentAlertsEmpty')}</p>`}
    </div>
  </div>

  <div class="grid-2b">
    <div class="card">
      <div class="card-head"><div><h3>${t('resumo.weeklyActivityCardTitle')}</h3><div class="card-sub">${t('resumo.weeklyActivityCardSubtitle')}</div></div></div>
      <canvas id="cvHeatmap" height="180" role="img" aria-label="${t('resumo.heatmapChartAria')}"></canvas>
    </div>
    <div class="card">
      <div class="card-head"><div><h3>${t('resumo.trendCardTitle')}</h3><div class="card-sub">${t('resumo.trendCardSubtitle')}</div></div></div>
      <canvas id="cvTrend" height="180" role="img" aria-label="${t('resumo.trendChartAria')}"></canvas>
      <div class="legend">
        <span class="legend-item"><span class="legend-swatch" style="background:var(--cat-atividade)"></span>${t('resumo.legendSteps')}</span>
        <span class="legend-item"><span class="legend-swatch" style="background:var(--cat-dormir)"></span>${t('resumo.legendSleepHours')}</span>
        <span class="legend-item"><span class="legend-swatch" style="background:var(--status-critical)"></span>${t('resumo.legendAvgHr')}</span>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-head">
      <div><h3>${t('resumo.nightSummaryCardTitle')} <span class="sim-flag">${t('resumo.simulatedDataBadge')}</span></h3><div class="card-sub">${t('resumo.nightSummaryCardSubtitle')}</div></div>
    </div>
    <div id="nightSummary"></div>
  </div>
`;
AFTER_RENDER.resumo = () => {
  drawRoutineTimeline('cvRoutineToday', currentRoutineToday(), t('resumo.routineTodayChartLabel'));
  drawRoutineTimeline('cvRoutineAnom', currentRoutineAnomaly(), t('resumo.routineAnomalyChartLabel'));
  drawHeatmap('cvHeatmap');
  drawTrend('cvTrend');
  applyLiveVitals();
  renderNightSummary();
  renderLiveActivityPanel();
};

/* ------------------------------------------------------------
   RESUMO NOTURNO
   ------------------------------------------------------------
   Ideia da pesquisa: agitação/deambulação noturna ("sundowning") é uma
   das preocupações mais citadas por cuidadores de pessoas com demência,
   e é distinta da atividade diurna — merece o seu próprio resumo em vez
   de se perder dentro da timeline geral de 24h. Calculado a partir do
   bloco "dormir" noturno do currentRoutineToday() (dados simulados, ver aviso).
------------------------------------------------------------ */
function buildNightRestlessness(seed){
  const rnd = seedRand(seed);
  const count = Math.floor(rnd() * 3); // 0-2 episódios, plausível
  const events = [];
  for (let i = 0; i < count; i++){
    const minute = 22*60 + 30 + Math.floor(rnd() * (7*60 + 60 - 22*60 - 30)); // entre 22:30 e ~08:00 (cruza meia-noite em minutos "do dia")
    events.push({ time: fmtMin(minute % (24*60)), durationMin: 3 + Math.floor(rnd()*12) });
  }
  return events;
}
const NIGHT_EVENTS_BY_PATIENT = (typeof DEMO_NIGHT_EVENTS !== 'undefined') ? DEMO_NIGHT_EVENTS : {p1: buildNightRestlessness(19), p2: buildNightRestlessness(29), p3: buildNightRestlessness(39)};
function currentNightEvents(){ return NIGHT_EVENTS_BY_PATIENT[selectedPatientId] || NIGHT_EVENTS_BY_PATIENT.p1; }

function renderNightSummary(){
  const host = document.getElementById('nightSummary');
  if (!host) return;
  const bedBlock = currentRoutineToday().find(b => b.cat === 'dormir' && b.start >= 21*60);
  const wakeBlock = currentRoutineToday().find(b => b.cat === 'dormir' && b.start === 0);
  const bedTime = bedBlock ? fmtMin(bedBlock.start) : '—';
  const wakeTime = wakeBlock ? fmtMin(wakeBlock.end) : '—';
  const totalOutOfBedMin = currentNightEvents().reduce((s,e) => s + e.durationMin, 0);

  host.innerHTML = `
    <div class="activity-stat-row">
      <div class="activity-stat"><div class="n tabular">${bedTime}</div><div class="l">${t('resumo.nightBedTime')}</div></div>
      <div class="activity-stat"><div class="n tabular">${wakeTime}</div><div class="l">${t('resumo.nightWakeTime')}</div></div>
      <div class="activity-stat"><div class="n tabular">${currentNightEvents().length}</div><div class="l">${t('resumo.nightRestlessEpisodes')}</div></div>
      <div class="activity-stat"><div class="n tabular" style="color:${totalOutOfBedMin>15?'var(--status-warning)':'var(--status-good)'}">${totalOutOfBedMin} min</div><div class="l">${t('resumo.nightTimeOutOfBed')}</div></div>
    </div>
    ${currentNightEvents().length ? `
      <div class="activity-blocks-list">
        ${currentNightEvents().map(e => `
          <div class="activity-block-row">
            <span class="legend-swatch" style="background:var(--cat-dormir)"></span>
            <span class="tabular">${e.time}</span>
            <span>${t('resumo.nightRestlessEvent')}</span>
            <span class="activity-block-dur tabular">${e.durationMin} min</span>
          </div>`).join('')}
      </div>
    ` : `<p class="empty-hint">${t('resumo.nightNoEvents')}</p>`}
  `;
}

/* ------------------------------------------------------------
   PACING / DEAMBULAÇÃO (deteção precoce de wandering via giroscópio)
   ------------------------------------------------------------
   Ideia da pesquisa: uma métrica de "curvas apertadas"/pacing (mudanças
   de direção frequentes e de raio pequeno, medidas pelo giroscópio) é
   apontada na literatura como sinal precoce de deambulação (wandering),
   complementar ao geofencing por GPS — capta o padrão de "andar às
   voltas" mesmo dentro de casa, onde o GPS não distingue bem posições
   próximas. Índice diário (0-100, mais alto = mais voltas apertadas que
   o habitual).
   **Cálculo real implementado (2026-07-03)**: Imu::detectPacing() em
   src/Imu/Imu.cpp conta rajadas de rotação acima de um limiar na norma
   do giroscópio (janela de 1 minuto), reencaminhado via FullPlain/bridge
   até liveState.pacing (ver handleBridgeMessage abaixo). Quando o bridge
   está ligado, o valor "hoje" mostrado é este índice real, calculado a
   partir de gx/gy/gz do IMU — a TENDÊNCIA de 7 dias (buildPacingTrend)
   continua simulada, porque ainda não há histórico real acumulado (só
   existirá depois do serviço de persistência, ver PROJECT_STATUS.md,
   Prioridade 4 — Base de dados).
------------------------------------------------------------ */
function buildPacingTrend(seed){
  const rnd = seedRand(seed);
  const days = ['Seg','Ter','Qua','Qui','Sex','Sáb','Dom'];
  // Índice de base plausível (rotina calma) com uma pequena tendência a
  // subir nos últimos dias, para haver algo a comentar no cartão sem
  // exagerar (dados sintéticos, não uma alegação clínica).
  return days.map((day, i) => {
    const base = 22 + rnd() * 10;
    const drift = i >= 5 ? (i - 4) * 4 : 0; // sáb/dom ligeiramente mais altos
    return { day, score: Math.round(Math.min(100, base + drift)) };
  });
}
const PACING_TREND_BY_PATIENT = (typeof DEMO_PACING_TREND !== 'undefined') ? DEMO_PACING_TREND : {p1: buildPacingTrend(31), p2: buildPacingTrend(41), p3: buildPacingTrend(51)};
function currentPacingTrend(){ return PACING_TREND_BY_PATIENT[selectedPatientId] || PACING_TREND_BY_PATIENT.p1; }

function renderPacingSummary(){
  const host = document.getElementById('pacingSummary');
  if (!host) return;
  // Índice de "hoje": real (vindo do firmware via bridge) quando ligado e
  // já houver pelo menos uma janela de 1 minuto processada; caso
  // contrário cai para o último dia da série simulada (buildPacingTrend),
  // igual ao padrão já usado noutros cartões com dados ao vivo (ver
  // drawHrSeries/liveState.connected).
  const live = liveState.connected && liveState.pacing != null;
  const today = live ? liveState.pacing : currentPacingTrend()[currentPacingTrend().length - 1].score;
  const weekAvg = Math.round(currentPacingTrend().reduce((s,d) => s + d.score, 0) / currentPacingTrend().length);
  // Bug corrigido: o ramo "today >= 40 ? 'good' : 'good'" era morto (as
  // duas saídas eram idênticas) — só existem dois estados reais aqui,
  // como o próprio levelLabel (2 valores) já deixava claro.
  const level = today >= 60 ? 'warning' : 'good';
  const levelLabel = today >= 60 ? t('rotina.pacingAboveUsual') : t('rotina.pacingWithinUsual');
  const todayLabelSuffix = live ? ` — ${t('rotina.pacingLive')}` : ` — ${t('rotina.pacingDemo')}`;
  host.innerHTML = `
    <div class="activity-stat-row">
      <div class="activity-stat"><div class="n tabular" style="color:var(--status-${level})">${today}</div><div class="l">${t('rotina.pacingTodayIndex')}${todayLabelSuffix}</div></div>
      <div class="activity-stat"><div class="n tabular">${weekAvg}</div><div class="l">${t('rotina.pacingWeekAvg')} <span class="sim-flag">${t('rotina.simFlag')}</span></div></div>
      <div class="activity-stat"><div class="n">${pillHtml(level, levelLabel)}</div><div class="l">${t('rotina.pacingStateLabel')}</div></div>
    </div>
    <p class="empty-hint">${live ? t('rotina.pacingLiveHint') + ' ' : ''}${t('rotina.pacingExplainHint')}</p>
  `;
}
