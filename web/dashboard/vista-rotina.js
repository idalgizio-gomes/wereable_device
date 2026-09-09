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

  <!-- RF-09 (2026-09-07) — human-in-the-loop. Colocado na vista "Rotina
       diária" e não numa vista nova: é aqui que o cuidador já olha para o
       que o sistema CLASSIFICOU (blocos de rotina + anomalias logo
       acima), por isso é aqui que faz sentido contradizê-lo. Ver o módulo
       "ROTULAGEM HUMAN-IN-THE-LOOP" em bridge-exportacao.js. -->
  <div class="card" id="hitlReviewCard"></div>
`;
AFTER_RENDER.rotina = () => {
  drawRoutineTimeline('cvRoutineFull', currentRoutineToday(), '');
  drawRoutineTimeline('cvRoutineAnomFull', currentRoutineAnomaly(), '', true);
  renderActivityDetail(selectedActivityCat);
  renderCaregiverNotes();
  renderPacingSummary();
  drawPacingTrend('cvPacingTrend', currentPacingTrend());
  renderHitlReviewCard('hitlReviewCard');
};

/* ------------------------------------------------------------
   RF-09 — CARTÃO DE REVISÃO DE CLASSIFICAÇÕES (2026-09-07)
   ------------------------------------------------------------
   Duas metades, deliberadamente no mesmo cartão:
     1. O que ainda se PODE marcar — os alertas ativos deste paciente,
        cada um com um botão de "falso positivo" que alterna.
     2. O que JÁ foi marcado — a fila de rótulos acumulada, com quem a
        criou, se chegou ao bridge, e a exportação para o ciclo de
        retreino.
   Separá-las em dois cartões esconderia a consequência da ação: hoje o
   cuidador carrega no botão e não vê para onde é que aquilo vai. O
   requisito é precisamente que a marcação "fique disponível para o
   próximo ciclo de retreino" — mostrar a fila é o que torna isso
   verificável por quem usa, e não só por quem lê o código.
------------------------------------------------------------ */
function renderHitlReviewCard(hostId){
  const host = document.getElementById(hostId);
  if (!host) return;

  const alertas = typeof currentAlerts === 'function' ? currentAlerts() : [];
  const naFila = hitlCorrections.filter(c => c.patientId === selectedPatientId);
  const porSincronizar = naFila.filter(c => !c.sentToBridge).length;

  const listaAlertas = alertas.length ? alertas.map(a => {
    const marcado = isAlertMarkedFalsePositive(a.key);
    return `
      <div class="hitl-row">
        <div class="hitl-main">
          <div class="hitl-title">${escapeHtml(a.title)}</div>
          <div class="hitl-meta">${escapeHtml(a.time || '')} · gravidade: ${escapeHtml(a.sev)}</div>
          ${marcado ? '<div class="hitl-badge" style="margin-top:6px;">✎ Marcado como falso positivo</div>' : ''}
        </div>
        <div class="hitl-actions">
          <button type="button" class="btn-secondary"
                  aria-pressed="${marcado}"
                  onclick="toggleAlertFalsePositive('${escapeHtml(a.key)}', '${escapeHtml(a.title).replace(/'/g, '&#39;')}')">
            ${marcado ? 'Anular marcação' : 'Marcar como falso positivo'}
          </button>
        </div>
      </div>`;
  }).join('') : '<p class="empty-hint">Sem alertas ativos para rever neste paciente.</p>';

  const fila = naFila.length ? naFila.map(c => `
    <div class="hitl-queue-row">
      <span class="hitl-when">${new Date(c.ts).toLocaleString(currentLang, {dateStyle:'short', timeStyle:'short'})}</span>
      <span>${c.kind === 'alerta' ? 'Alerta' : 'Atividade'}: ${escapeHtml(c.targetLabel || c.target)}
        ${c.originalLabel && c.correctedLabel ? ` — ${escapeHtml(c.originalLabel)} → <b>${escapeHtml(c.correctedLabel)}</b>` : ''}</span>
      <span class="hitl-sync ${c.sentToBridge ? 'sent' : 'pending'}">${c.sentToBridge ? '✓ no bridge' : '● só local'}</span>
      <button type="button" class="btn-secondary" style="padding:4px 9px;"
              aria-label="Remover este rótulo da fila"
              onclick="removeHitlCorrection('${c.id}')">Remover</button>
    </div>
  `).join('') : '<p class="empty-hint">Ainda não corrigiste nenhuma classificação. As correções aparecem aqui.</p>';

  host.innerHTML = `
    <div class="card-head">
      <div>
        <h3>Rever classificações do sistema</h3>
        <div class="card-sub">Marcar um alerta como falso positivo cria um rótulo humano — o dado que falta para melhorar o modelo.</div>
      </div>
    </div>
    ${listaAlertas}

    <div class="card-sub" style="margin:18px 0 4px;">Rótulos registados por ti (${naFila.length})</div>
    ${fila}

    <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:14px;">
      <button type="button" class="btn-secondary" onclick="exportHitlCorrections()" ${naFila.length ? '' : 'disabled'}>
        Exportar para retreino (.jsonl)
      </button>
      <button type="button" class="btn-secondary" onclick="exportHitlCorrectionsCsv()" ${naFila.length ? '' : 'disabled'}>
        Exportar (.csv)
      </button>
    </div>
    <p class="empty-hint">
      As correções de <b>atividade</b> feitas com o bridge ligado são gravadas na base de dados do bridge
      (tabela <code>activity_corrections</code>) e ficam disponíveis para o próximo ciclo de retreino do
      classificador. As marcações de <b>falso positivo em alertas</b> ainda não têm comando equivalente no
      bridge: ficam guardadas neste browser e só entram no retreino através do ficheiro exportado acima.
      ${porSincronizar ? `<b>${porSincronizar}</b> rótulo(s) deste paciente ainda não chegaram ao bridge.` : ''}
    </p>
  `;
}

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
    ctx.fillStyle = resolveVar('--text-muted'); ctx.font = canvasFont(10); ctx.textBaseline='top';
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
    ctx.fillStyle = resolveVar('--text-muted'); ctx.font = canvasFont(10); ctx.textBaseline='top';
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
