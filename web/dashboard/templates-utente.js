/* ============================================================
   AUXILIARES DE UI
============================================================ */
function iconFor(type){
  const M = {
    heart:'<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8z"/>',
    walk:'<circle cx="13" cy="4" r="2"/><path d="M9 21l2-6 2 2 3 4M9 15l1-5-3-2 3-6 4 2 4-1"/>',
    warn:'<path d="M10.3 3.9 1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    drop:'<path d="M12 2s7 8.2 7 12.5A7 7 0 1 1 5 14.5C5 10.2 12 2 12 2z"/>',
    moon:'<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>',
    zap:'<path d="M13 2 3 14h7l-1 8 11-13h-7l1-7z"/>',
  };
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${M[type]||M.warn}</svg>`;
}
const SEV_COLOR = {critical:'var(--status-critical)', serious:'var(--status-serious)', warning:'var(--status-warning)', good:'var(--status-good)'};
const SEV_BG = {critical:'var(--status-critical-bg)', serious:'var(--status-serious-bg)', warning:'var(--status-warning-bg)', good:'var(--status-good-bg)'};

function pillHtml(sev, label){
  return `<span class="pill ${sev}">${iconFor(sev==='critical'?'warn':sev==='warning'?'warn':'zap')}${label}</span>`;
}

// Escapa texto livre introduzido pelo utilizador (nome de medicamento,
// valor de campo de perfil, etc.) antes de o inserir em innerHTML — mesmo
// padrão já usado em renderCaregiverNotes()/nome de cuidador, extraído
// aqui para reutilizar nos pontos que ainda inseriam texto livre sem
// escaping (bug de XSS real, corrigido 2026-07-07).
function escapeHtml(str){
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function fmtMin(m){
  const h = Math.floor(m/60), mm = String(m%60).padStart(2,'0');
  return `${String(h).padStart(2,'0')}:${mm}`;
}

// Formata uma duração em minutos como "Xh Ym" (ou só "Ym" se < 1h).
function fmtDuration(totalMinutes){
  const h = Math.floor(totalMinutes/60), m = totalMinutes%60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

/* ============================================================
   TEMPLATES — VISTA "UTENTE / FAMÍLIA"
============================================================ */
const TEMPLATES = {};
const AFTER_RENDER = {};

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

let selectedActivityCat = ROUTINE_CATS[2].key; // 'atividade' por omissão

TEMPLATES.rotina = () => `
  <div class="card">
    <div class="card-head">
      <div><h3>${t('nav.routine')} — ${t('common.today')} <span class="sim-flag">${t('rotina.simFlag')}</span></h3><div class="card-sub">${t('rotina.timelineSubtitle')}</div></div>
    </div>
    <canvas id="cvRoutineFull" height="180" role="img" aria-label="${t('rotina.timelineSubtitle')} (${t('rotina.simFlag')})"></canvas>
    ${legendHtml()}
  </div>

  <div class="card">
    <div class="card-head">
      <div><h3>${t('rotina.pacingTitle')} <span class="sim-flag">${t('rotina.simTrend')}</span></h3><div class="card-sub">${t('rotina.pacingSubtitle')}</div></div>
    </div>
    <div id="pacingSummary"></div>
    <canvas id="cvPacingTrend" height="110" role="img" aria-label="${t('rotina.pacingTrendAria')} (${t('rotina.simFlag')})"></canvas>
  </div>

  <div class="card">
    <div class="card-head">
      <div><h3>${t('rotina.notesTitle')}</h3><div class="card-sub">${t('rotina.notesSubtitle')}</div></div>
    </div>
    <div class="note-form">
      <textarea id="noteInput" placeholder="${t('rotina.notePlaceholder')}" rows="2"></textarea>
      <button class="btn-secondary" onclick="addCaregiverNote()">${t('rotina.addNoteBtn')}</button>
    </div>
    <div class="note-list" id="noteList"></div>
  </div>

  <div class="card">
    <div class="card-head">
      <div><h3>${t('rotina.activityAnalysisTitle')} <span class="sim-flag">${t('rotina.simFlag')}</span></h3><div class="card-sub">${t('rotina.activityAnalysisSubtitle')}</div></div>
    </div>
    <div class="activity-chips" id="activityChips">
      ${ROUTINE_CATS.map(c => `
        <button class="activity-chip${c.key===selectedActivityCat?' active':''}" data-cat="${c.key}" style="--chip-color:${c.color}">
          <span class="chip-dot" style="background:${c.color}"></span>${c.label}
        </button>`).join('')}
    </div>
    <div id="activityDetail"></div>
  </div>

  <div class="card">
    <div class="card-head"><h3>${t('rotina.anomSimTitle')}</h3></div>
    <canvas id="cvRoutineAnomFull" height="180" role="img" aria-label="${t('rotina.anomTimelineAria')} (${t('rotina.simFlag')})"></canvas>
    ${legendHtml()}
    <p class="empty-hint">${t('rotina.anomHint')}</p>
  </div>
`;
AFTER_RENDER.rotina = () => {
  drawRoutineTimeline('cvRoutineFull', currentRoutineToday(), '');
  drawRoutineTimeline('cvRoutineAnomFull', currentRoutineAnomaly(), '', true);
  renderActivityDetail(selectedActivityCat);
  renderCaregiverNotes();
  renderPacingSummary();
  drawPacingTrend('cvPacingTrend', currentPacingTrend());
};

/* ------------------------------------------------------------
   NOTAS DO CUIDADOR
   ------------------------------------------------------------
   Ideia vinda diretamente da pesquisa de plataformas semelhantes
   (CarePredict e outras): a funcionalidade mais pedida em quase todas
   as fontes revistas é um diário/notas do cuidador ligado à timeline —
   fecha o fosso entre os dados passivos dos sensores e o contexto
   humano que só uma pessoa consegue registar ("recusou o almoço",
   "esteve agitada"). Protótipo: persistido em localStorage (só neste
   browser) — passar para a base de dados SQL quando essa existir.
------------------------------------------------------------ */
const NOTES_STORAGE_KEY = 'carewear_caregiver_notes';

function loadCaregiverNotes(){
  try {
    const raw = localStorage.getItem(NOTES_STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  // Notas de exemplo, só na primeira utilização (nada ainda guardado).
  return [
    {text: 'Recusou o pequeno-almoço, comeu só uma torrada.', authorKey: 'rotina.noteAuthorFamily', ts: Date.parse('2026-07-02T08:35:00')},
    {text: 'Esteve mais agitada do que o habitual antes do jantar.', authorKey: 'rotina.noteAuthorCaregiver', ts: Date.parse('2026-07-02T18:50:00')},
  ];
}
function saveCaregiverNotes(notes){
  try { localStorage.setItem(NOTES_STORAGE_KEY, JSON.stringify(notes)); }
  catch (e) { /* quota excedida ou localStorage indisponível - nota fica só em memória */ }
}
let caregiverNotes = loadCaregiverNotes();

function addCaregiverNote(){
  const input = document.getElementById('noteInput');
  const text = input.value.trim();
  if (!text) return;
  caregiverNotes.unshift({text, authorKey: currentRole === 'utente' ? 'rotina.noteAuthorFamily' : 'rotina.noteAuthorClinician', ts: Date.now()});
  saveCaregiverNotes(caregiverNotes);
  input.value = '';
  renderCaregiverNotes();
}

function renderCaregiverNotes(){
  const host = document.getElementById('noteList');
  if (!host) return;
  if (!caregiverNotes.length){
    host.innerHTML = `<p class="empty-hint">${t('rotina.notesEmpty')}</p>`;
    return;
  }
  host.innerHTML = caregiverNotes.map(n => `
    <div class="note-row">
      <div class="note-meta"><b>${t(n.authorKey || 'rotina.noteAuthorFamily')}</b><span class="tabular">${new Date(n.ts).toLocaleString(currentLang, {dateStyle:'short', timeStyle:'short'})}</span></div>
      <p>${n.text.replace(/</g,'&lt;')}</p>
    </div>
  `).join('');
}

document.addEventListener('click', (e) => {
  const chip = e.target.closest('.activity-chip');
  if (!chip) return;
  selectedActivityCat = chip.dataset.cat;
  document.querySelectorAll('.activity-chip').forEach(b => b.classList.toggle('active', b.dataset.cat === selectedActivityCat));
  renderActivityDetail(selectedActivityCat);
});

// Gera minutos totais por dia (7 dias) para uma categoria, de forma
// reprodutível (mesma categoria -> mesma "semente" -> mesmos valores),
// centrado num valor tipico plausivel por categoria.
function buildCategoryWeekly(catKey){
  const typical = {dormir: 480, descanso: 300, atividade: 90, alimentacao: 60, higiene: 25}[catKey] || 60;
  let seed = 0; for (const ch of catKey) seed += ch.charCodeAt(0);
  const rnd = seedRand(seed * 17 + 3);
  const days = ['Seg','Ter','Qua','Qui','Sex','Sáb','Dom'];
  return days.map(d => ({day: d, minutes: Math.max(5, Math.round(typical * (0.75 + rnd()*0.5)))}));
}

function renderActivityDetail(catKey){
  const host = document.getElementById('activityDetail');
  if (!host) return;
  const cat = ROUTINE_CATS.find(c => c.key === catKey);
  const blocks = currentRoutineToday().filter(b => b.cat === catKey);
  const totalMin = blocks.reduce((s,b) => s + (b.end - b.start), 0);
  const count = blocks.length;
  const avgMin = count ? Math.round(totalMin / count) : 0;
  const weekly = buildCategoryWeekly(catKey);
  const weekAvg = Math.round(weekly.reduce((s,d) => s + d.minutes, 0) / weekly.length);
  const deltaPct = weekAvg ? Math.round(((totalMin - weekAvg) / weekAvg) * 100) : 0;

  host.innerHTML = `
    <div class="activity-stat-row">
      <div class="activity-stat"><div class="n tabular">${fmtDuration(totalMin)}</div><div class="l">${t('rotina.timeToday')}</div></div>
      <div class="activity-stat"><div class="n tabular">${count}</div><div class="l">${t('rotina.occurrencesToday')}</div></div>
      <div class="activity-stat"><div class="n tabular">${avgMin} min</div><div class="l">${t('rotina.avgBlockDuration')}</div></div>
      <div class="activity-stat"><div class="n tabular" style="color:${deltaPct>=0?'var(--status-good)':'var(--status-warning)'}">${deltaPct>=0?'+':''}${deltaPct}%</div><div class="l">${t('rotina.vsWeeklyAvg')} (${weekAvg} ${t('rotina.minPerDay')})</div></div>
    </div>
    <div class="activity-blocks-list">
      ${blocks.length ? blocks.map(b => `
        <div class="activity-block-row">
          <span class="legend-swatch" style="background:${cat.color}"></span>
          <span class="tabular">${fmtMin(b.start)} – ${fmtMin(b.end)}</span>
          <span class="activity-block-dur tabular">${b.end-b.start} min</span>
        </div>`).join('') : `<p class="empty-hint">${t('rotina.noBlocksBefore')}"${cat.label}"${t('rotina.noBlocksAfter')}</p>`}
    </div>
    <div class="card-sub" style="margin:14px 0 8px;">${t('rotina.weeklyTrendSubtitle')}</div>
    <canvas id="cvActivityWeekly" height="120" role="img" aria-label="${t('rotina.weeklyTrendAriaBefore')}${cat.label}${t('rotina.weeklyTrendAriaAfter')} (${t('rotina.simFlag')})"></canvas>
  `;
  drawCategoryWeeklyBar('cvActivityWeekly', weekly, cat.color);
}

function drawCategoryWeeklyBar(id, weekly, color){
  const S = setupCanvas(id, 120);
  if (!S) return;
  const {ctx, w, h} = S;
  ctx.clearRect(0,0,w,h);
  const padL=6, padR=6, top=8, bottom=20;
  const plotW=w-padL-padR, plotH=h-top-bottom;
  const max = Math.max(...weekly.map(d=>d.minutes)) * 1.15;
  const barW = plotW / weekly.length;
  const col = colorOf(color);

  S.bars = [];
  weekly.forEach((d,i) => {
    const bh = (d.minutes/max) * plotH;
    const x = padL + i*barW + barW*0.18;
    const bw = barW*0.64;
    const y = top + plotH - bh;
    ctx.fillStyle = col;
    roundRect(ctx, x, y, bw, bh, 3); ctx.fill();
    ctx.fillStyle = resolveVar('--text-muted'); ctx.font = '10px ' + getComputedStyle(document.body).fontFamily; ctx.textBaseline='top';
    ctx.fillText(d.day, x + bw/2 - 8, h-bottom+5);
    S.bars.push({x, y, w:bw, h:bh, d});
  });

  S.cv.onmousemove = (e) => {
    const r = S.cv.getBoundingClientRect();
    const mx = e.clientX-r.left, my = e.clientY-r.top;
    const hit = S.bars.find(b => mx>=b.x && mx<=b.x+b.w && my>=b.y && my<=top+plotH);
    if (!hit){ hideTip(); return; }
    showTip(e.clientX, e.clientY, `<div class="tt-title">${hit.d.day}</div><div class="tt-row"><span>${t('rotina.minutesLabel')}</span><b>${hit.d.minutes}</b></div>`);
  };
  S.cv.onmouseleave = hideTip;
}

// Linha simples com pontos + tooltip, no mesmo estilo de setupCanvas/
// colorOf já usado em drawCategoryWeeklyBar — usada para a tendência de
// 7 dias do índice de pacing (ver buildPacingTrend()).
function drawPacingTrend(id, data){
  const S = setupCanvas(id, 110);
  if (!S) return;
  const {ctx, w, h} = S;
  ctx.clearRect(0,0,w,h);
  const padL=6, padR=6, top=10, bottom=20;
  const plotW=w-padL-padR, plotH=h-top-bottom;
  const max = 100; // índice é sempre 0-100, eixo fixo para leitura consistente entre visitas
  const stepX = plotW / (data.length - 1);
  const col = colorOf('var(--accent)');

  const pts = data.map((d,i) => ({
    x: padL + i*stepX,
    y: top + plotH - (d.score/max)*plotH,
    d,
  }));

  ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.beginPath();
  pts.forEach((p,i) => i===0 ? ctx.moveTo(p.x,p.y) : ctx.lineTo(p.x,p.y));
  ctx.stroke();

  pts.forEach(p => {
    ctx.fillStyle = col;
    ctx.beginPath(); ctx.arc(p.x, p.y, 3, 0, Math.PI*2); ctx.fill();
    ctx.fillStyle = resolveVar('--text-muted'); ctx.font = '10px ' + getComputedStyle(document.body).fontFamily; ctx.textBaseline='top';
    ctx.fillText(p.d.day, p.x - 8, h-bottom+5);
  });

  S.pts = pts;
  S.cv.onmousemove = (e) => {
    const r = S.cv.getBoundingClientRect();
    const mx = e.clientX-r.left, my = e.clientY-r.top;
    const hit = S.pts.find(p => Math.abs(mx-p.x) < stepX/2 && my < top+plotH+8);
    if (!hit){ hideTip(); return; }
    showTip(e.clientX, e.clientY, `<div class="tt-title">${hit.d.day}</div><div class="tt-row"><span>${t('rotina.pacingIndexLabel')}</span><b>${hit.d.score}</b></div>`);
  };
  S.cv.onmouseleave = hideTip;
}

TEMPLATES.vitais = () => `
  <div class="card" style="padding:14px 17px;">
    <div style="display:flex; align-items:center; justify-content:space-between; gap:14px; flex-wrap:wrap;">
      <p class="empty-hint" id="forceReadingHint" style="padding:0; margin:0;">${t('vitais.forceReadingHint')}</p>
      <div style="display:flex; gap:8px; flex-wrap:wrap;">
        <button class="btn-secondary" id="forceReadingBtn" onclick="onForceReadingClick()">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8z"/></svg>
          ${t('vitais.measureNowBtn')}
        </button>
        <button class="${continuousHrIntervalId != null ? 'btn-primary' : 'btn-secondary'}" id="continuousHrBtn" aria-pressed="${continuousHrIntervalId != null}" onclick="toggleContinuousHr()" title="${t('vitais.continuousHrHint')}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M3 12h4l2-7 4 14 2-7h6"/></svg>
          ${continuousHrIntervalId != null ? t('vitais.continuousHrStopBtn') : t('vitais.continuousHrStartBtn')}
        </button>
      </div>
    </div>
  </div>

  <div class="stat-row">
    ${statTile('heart',t('vitais.hrCardTitle'),'—','bpm', 'var(--status-good)', 'stat-hr-2')}
    ${statTile('zap',t('vitais.spo2CardTitle'),'—','%', 'var(--status-good)', 'stat-spo2-2', false, 'spo2-hint-2')}
    ${statTile('walk',t('vitais.stepsCardTitle'),'—','', 'var(--cat-atividade)', 'stat-steps-2')}
    ${statTile('warn',t('vitais.fallsCardTitle'),'—','', 'var(--status-good)', 'stat-falls-2')}
  </div>
  <div id="vitalAlertsPanel"></div>
  <div class="card">
    <div class="card-head">
      <div><h3>${t('vitais.hrCardTitle')} <span id="hrChartLabel">${t('vitais.hrChartDemo')}</span></h3><div class="card-sub">${t('vitais.hrCardSubtitle')}</div></div>
    </div>
    <canvas id="cvHr" height="170" role="img" aria-label="${t('vitais.hrChartAria')}"></canvas>
  </div>

  <div class="card print-hide">
    <div class="card-head"><div><h3>${t('vitais.baselineTitle')}</h3><div class="card-sub">${t('vitais.baselineSubtitle')}</div></div></div>
    <div style="display:flex; gap:16px; flex-wrap:wrap; align-items:flex-end;">
      <div>
        <label for="thresholdHrMin" style="display:block; font-size:12px; color:var(--text-secondary); margin-bottom:4px;">${t('vitais.hrMinLabel')}</label>
        <input id="thresholdHrMin" type="number" min="20" max="150" style="width:90px;" class="row-input">
      </div>
      <div>
        <label for="thresholdHrMax" style="display:block; font-size:12px; color:var(--text-secondary); margin-bottom:4px;">${t('vitais.hrMaxLabel')}</label>
        <input id="thresholdHrMax" type="number" min="40" max="220" style="width:90px;" class="row-input">
      </div>
      <div>
        <label for="thresholdSpo2Min" style="display:block; font-size:12px; color:var(--text-secondary); margin-bottom:4px;">${t('vitais.spo2MinLabel')}</label>
        <input id="thresholdSpo2Min" type="number" min="70" max="100" style="width:90px;" class="row-input">
      </div>
      <button class="btn-secondary" onclick="saveThresholds()">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/></svg>
        ${t('common.save')}
      </button>
      <span class="empty-hint" id="thresholdsHint" style="margin:0;"></span>
    </div>
    <p class="empty-hint">${t('vitais.baselineNoteEmpty')}</p>
  </div>
`;
AFTER_RENDER.vitais = () => { drawHrSeries('cvHr'); applyLiveVitals(); requestThresholds(); renderVitalAlertsPanel(); };

TEMPLATES.tendencia = () => `
  <div class="card">
    <div class="card-head"><div><h3>${t('tendencia.trendCardTitle')}</h3></div></div>
    <canvas id="cvTrend2" height="220" role="img" aria-label="${t('tendencia.trendChartAria')}"></canvas>
    <div class="legend">
      <span class="legend-item"><span class="legend-swatch" style="background:var(--cat-atividade)"></span>${t('tendencia.legendSteps')}</span>
      <span class="legend-item"><span class="legend-swatch" style="background:var(--cat-dormir)"></span>${t('tendencia.legendSleep')}</span>
      <span class="legend-item"><span class="legend-swatch" style="background:var(--status-critical)"></span>${t('tendencia.legendAvgHr')}</span>
    </div>
  </div>
  <div class="card">
    <div class="card-head"><div><h3>${t('tendencia.weeklyPatternCardTitle')}</h3></div></div>
    <canvas id="cvHeatmap2" height="200" role="img" aria-label="${t('tendencia.heatmapChartAria')}"></canvas>
  </div>
  <div class="card">
    <div class="card-head">
      <div><h3>${t('tendencia.realHistoryCardTitle')}</h3>
      <div class="card-sub">${t('tendencia.realHistoryCardSubtitle')}</div></div>
      <button class="btn-secondary" onclick="requestRealTrend(7)">${t('tendencia.refreshBtn')}</button>
    </div>
    <table class="data-table">
      <thead><tr><th>${t('tendencia.thDay')}</th><th>${t('tendencia.thRecords')}</th><th>${t('tendencia.thAvgHr')}</th><th>${t('tendencia.thStepsDelta')}</th></tr></thead>
      <tbody id="realTrendBody"></tbody>
    </table>
    <p class="empty-hint" id="realTrendStatus"></p>
  </div>
`;
AFTER_RENDER.tendencia = () => { drawTrend('cvTrend2'); drawHeatmap('cvHeatmap2'); renderRealTrendTable(); requestRealTrend(7); };
AFTER_RENDER.exportar = () => { requestRetentionSettings(); requestConsentStatus(); requestModelVersions(); };

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

