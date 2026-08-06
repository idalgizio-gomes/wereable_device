/* ============================================================
   CANVAS — utilidades de tooltip + DPR
============================================================ */
const ttipEl = document.getElementById('ttip');
function showTip(x, y, html){
  ttipEl.innerHTML = html;
  ttipEl.style.left = (x+14)+'px';
  ttipEl.style.top = (y+14)+'px';
  ttipEl.classList.add('show');
}
function hideTip(){ ttipEl.classList.remove('show'); }

function setupCanvas(id, cssHeight){
  const cv = document.getElementById(id);
  if (!cv) return null;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const cssW = cv.parentElement.clientWidth - 34;
  cv.style.width = cssW + 'px';
  cv.style.height = cssHeight + 'px';
  cv.width = cssW * dpr;
  cv.height = cssHeight * dpr;
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr,0,0,dpr,0,0);
  return {cv, ctx, w:cssW, h:cssHeight};
}
function resolveVar(name){
  return getComputedStyle(document.documentElement).getPropertyValue(name.replace('var(','').replace(')','')).trim() || name;
}
function colorOf(token){
  if (token.startsWith('var(')) return resolveVar(token);
  return token;
}

/* ============================================================
   ROUTINE TIMELINE (gantt de 1 dia) — canvas + hover
============================================================ */
function drawRoutineTimeline(id, blocks, subtitle, dashedAnomaly){
  const S = setupCanvas(id, 96);
  if (!S) return;
  const {ctx, w, h} = S;
  ctx.clearRect(0,0,w,h);

  const padL = 4, padR = 4, top = 22, barH = 34;
  const plotW = w - padL - padR;
  const minToX = (m) => padL + (m/1440)*plotW;

  // eixo de horas
  ctx.strokeStyle = resolveVar('--border'); ctx.lineWidth = 1;
  ctx.fillStyle = resolveVar('--text-muted'); ctx.font = '10.5px ' + getComputedStyle(document.body).fontFamily;
  ctx.textBaseline = 'middle';
  for (let hh=0; hh<=24; hh+=6){
    const x = minToX(hh*60);
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top+barH); ctx.stroke();
    ctx.fillText(String(hh).padStart(2,'0')+':00', x - (hh===24?26:0), top-9);
  }

  // barra de fundo
  ctx.fillStyle = resolveVar('--bg-surface-2');
  roundRect(ctx, padL, top, plotW, barH, 6); ctx.fill();

  const catMap = Object.fromEntries(ROUTINE_CATS.map(c => [c.key, c]));
  S.blocks = [];
  blocks.forEach((b, i) => {
    const x0 = minToX(b.start), x1 = minToX(b.end);
    const col = colorOf(catMap[b.cat].color);
    ctx.fillStyle = col;
    const isAnom = dashedAnomaly && (i===2 || i===8 || i===10);
    ctx.globalAlpha = isAnom ? 0.55 : 1;
    ctx.fillRect(x0+1, top+2, Math.max(2,x1-x0-2), barH-4);
    ctx.globalAlpha = 1;
    if (isAnom){
      ctx.strokeStyle = resolveVar('--status-critical'); ctx.lineWidth = 1.6; ctx.setLineDash([3,2]);
      ctx.strokeRect(x0+1, top+2, Math.max(2,x1-x0-2), barH-4);
      ctx.setLineDash([]);
    }
    S.blocks.push({x0, x1, y0:top, y1:top+barH, b, isAnom});
  });

  if (subtitle){
    ctx.fillStyle = resolveVar('--text-secondary'); ctx.font = '600 11.5px ' + getComputedStyle(document.body).fontFamily;
    ctx.fillText(subtitle, padL, top+barH+16);
  }

  S.cv.onmousemove = (e) => {
    const r = S.cv.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const hit = S.blocks.find(bl => mx>=bl.x0 && mx<=bl.x1 && my>=bl.y0 && my<=bl.y1);
    if (!hit){ hideTip(); S.cv.style.cursor='default'; return; }
    S.cv.style.cursor='pointer';
    const catLabel = catMap[hit.b.cat].label;
    showTip(e.clientX, e.clientY, `
      <div class="tt-title">${catLabel}${hit.isAnom ? ' · anomalia' : ''}</div>
      <div class="tt-row"><span>Início</span><b>${fmtMin(hit.b.start)}</b></div>
      <div class="tt-row"><span>Fim</span><b>${fmtMin(hit.b.end)}</b></div>
      <div class="tt-row"><span>Duração</span><b>${hit.b.end-hit.b.start} min</b></div>
    `);
  };
  S.cv.onmouseleave = hideTip;
}

function roundRect(ctx,x,y,w,h,r){
  ctx.beginPath();
  ctx.moveTo(x+r,y); ctx.arcTo(x+w,y,x+w,y+h,r); ctx.arcTo(x+w,y+h,x,y+h,r);
  ctx.arcTo(x,y+h,x,y,r); ctx.arcTo(x,y,x+w,y,r); ctx.closePath();
}

/* ============================================================
   HEATMAP semanal
============================================================ */
/* ------------------------------------------------------------
   CONVERSÃO sRGB <-> OKLab/OKLCH (Björn Ottosson,
   https://bottosson.github.io/posts/oklab/) — usada para construir
   rampas sequenciais percetualmente uniformes (ex.: heatmap semanal) em
   vez de interpolação RGB ingénua, que produzia blocos de tom muito
   parecido, difíceis de distinguir (pedido do utilizador: "cores mais
   facilmente identificáveis"). Segue a orientação do skill dataviz:
   "Sequential = one hue, light→dark", ancorada na própria cor de fundo
   do tema ativo em vez de uma tabela fixa — funciona nos dois temas
   (claro/escuro) sem precisar de valores separados.
------------------------------------------------------------ */
function srgbToLinear(c){ return c <= 0.04045 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4); }
function linearToSrgb(c){ return c <= 0.0031308 ? c*12.92 : 1.055*Math.pow(c, 1/2.4) - 0.055; }
function hexToRgb01(hex){
  const n = parseInt(hex.replace('#',''), 16);
  return [(n>>16 & 255)/255, (n>>8 & 255)/255, (n & 255)/255];
}
function rgbToOklab(r,g,b){
  const lr=srgbToLinear(r), lg=srgbToLinear(g), lb=srgbToLinear(b);
  const l=0.4122214708*lr+0.5363325363*lg+0.0514459929*lb;
  const m=0.2119034982*lr+0.6806995451*lg+0.1073969566*lb;
  const s=0.0883024619*lr+0.2817188376*lg+0.6299787005*lb;
  const l_=Math.cbrt(l), m_=Math.cbrt(m), s_=Math.cbrt(s);
  return [
    0.2104542553*l_+0.7936177850*m_-0.0040720468*s_,
    1.9779984951*l_-2.4285922050*m_+0.4505937099*s_,
    0.0259040371*l_+0.7827717662*m_-0.8086757660*s_,
  ];
}
function oklabToRgb(L,a,b){
  const l_=L+0.3963377774*a+0.2158037573*b;
  const m_=L-0.1055613458*a-0.0638541728*b;
  const s_=L-0.0894841775*a-1.2914855480*b;
  const l=l_**3, m=m_**3, s=s_**3;
  const lr= 4.0767416621*l-3.3077115913*m+0.2309699292*s;
  const lg=-1.2684380046*l+2.6097574011*m-0.3413193965*s;
  const lb=-0.0041960863*l-0.7034186147*m+1.7076147010*s;
  return [lr,lg,lb].map(v => linearToSrgb(Math.max(0, Math.min(1, v))));
}
// Devolve uma função (t: 0..1) => "rgb(r,g,b)". t=0 fica ancorado na
// própria cor da superfície (funde-se no fundo, tal como uma rampa
// sequencial deve "recuar" para perto de zero); t=1 é a versão mais
// saturada/clara (escuro) ou mais saturada/escura (claro) do accent.
function buildSequentialRamp(accentHex, surfaceHex){
  const [ar,ag,ab] = hexToRgb01(accentHex);
  const [aL,aa,ab2] = rgbToOklab(ar,ag,ab);
  const aC = Math.sqrt(aa*aa + ab2*ab2), aH = Math.atan2(ab2, aa);
  const [sr,sg,sb] = hexToRgb01(surfaceHex);
  const [sL] = rgbToOklab(sr,sg,sb);
  const darkMode = sL < 0.5;
  const targetL = darkMode ? Math.min(0.92, aL + 0.13) : Math.max(0.50, aL);
  return function(t){
    t = Math.max(0, Math.min(1, t));
    const stepL = sL + t * (targetL - sL);
    const chromaScale = Math.pow(Math.sin(Math.PI * Math.min(1, 0.12 + t*0.86)), 0.5);
    const stepC = aC * (0.3 + 1.0 * chromaScale);
    const na = stepC * Math.cos(aH), nb = stepC * Math.sin(aH);
    const [r,g,b] = oklabToRgb(stepL, na, nb);
    return `rgb(${Math.round(r*255)}, ${Math.round(g*255)}, ${Math.round(b*255)})`;
  };
}

function drawHeatmap(id){
  const S = setupCanvas(id, 180);
  if (!S) return;
  const {ctx, w, h} = S;
  ctx.clearRect(0,0,w,h);

  const labelW = 34, top = 8, bottom = 20;
  const rows = currentHeatmapData().length, cols = 24;
  const gridW = w - labelW - 4, gridH = h - top - bottom;
  const cw = gridW/cols, ch = gridH/rows;

  ctx.font = '10px ' + getComputedStyle(document.body).fontFamily;
  ctx.fillStyle = resolveVar('--text-muted');
  ctx.textBaseline = 'middle';

  const seqRamp = buildSequentialRamp(resolveVar('--accent'), resolveVar('--bg-surface'));
  S.cells = [];
  currentHeatmapData().forEach((row,ri) => {
    ctx.fillText(row.day, 0, top + ri*ch + ch/2);
    row.hours.forEach((v,ci) => {
      const x = labelW + ci*cw, y = top + ri*ch;
      ctx.fillStyle = seqRamp(v);
      roundRect(ctx, x+0.5, y+0.5, cw-1.5, ch-1.5, 2); ctx.fill();
      S.cells.push({x,y,w:cw,h:ch, day:row.day, hour:ci, v});
    });
  });

  ctx.fillStyle = resolveVar('--text-muted');
  [0,6,12,18,23].forEach(hh => {
    ctx.fillText(String(hh).padStart(2,'0')+'h', labelW + hh*cw, top+gridH+12);
  });

  S.cv.onmousemove = (e) => {
    const r = S.cv.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const hit = S.cells.find(c => mx>=c.x && mx<=c.x+c.w && my>=c.y && my<=c.y+c.h);
    if (!hit){ hideTip(); return; }
    showTip(e.clientX, e.clientY, `
      <div class="tt-title">${hit.day} · ${String(hit.hour).padStart(2,'0')}:00</div>
      <div class="tt-row"><span>${t('resumo.heatmapIntensityLabel')}</span><b>${Math.round(hit.v*100)}%</b></div>
    `);
  };
  S.cv.onmouseleave = hideTip;
}

/* ============================================================
   TENDÊNCIA — linhas indexadas (3 métricas, escalas diferentes
   → cada série é normalizada ao seu próprio min/max, nunca um
   duplo eixo y) com crosshair + tooltip
============================================================ */
function drawTrend(id){
  const S = setupCanvas(id, 190);
  if (!S) return;
  const {ctx, w, h} = S;
  ctx.clearRect(0,0,w,h);

  const padL = 6, padR = 6, top = 14, bottom = 22;
  const plotW = w-padL-padR, plotH = h-top-bottom;
  const n = currentTrendData().length;
  const xAt = (i) => padL + (i/(n-1))*plotW;

  const series = [
    {key:'passos', color: colorOf('var(--cat-atividade)')},
    {key:'sono', color: colorOf('var(--cat-dormir)')},
    {key:'fc', color: colorOf('var(--status-critical)')},
  ];
  series.forEach(s => {
    const vals = currentTrendData().map(d => d[s.key]);
    s.min = Math.min(...vals); s.max = Math.max(...vals);
    s.yAt = (v) => top + plotH - ((v - s.min)/((s.max-s.min)||1))*plotH*0.86 - plotH*0.02;
  });

  // gridlines
  ctx.strokeStyle = resolveVar('--border-soft'); ctx.lineWidth = 1;
  for (let i=0;i<4;i++){ const y = top + (plotH/3)*i; ctx.beginPath(); ctx.moveTo(padL,y); ctx.lineTo(w-padR,y); ctx.stroke(); }

  series.forEach(s => {
    ctx.beginPath();
    currentTrendData().forEach((d,i) => { const x=xAt(i), y=s.yAt(d[s.key]); i===0?ctx.moveTo(x,y):ctx.lineTo(x,y); });
    ctx.strokeStyle = s.color; ctx.lineWidth = 2; ctx.lineJoin='round'; ctx.stroke();
    currentTrendData().forEach((d,i) => {
      const x=xAt(i), y=s.yAt(d[s.key]);
      ctx.beginPath(); ctx.arc(x,y,2.6,0,Math.PI*2);
      ctx.fillStyle = resolveVar('--bg-surface'); ctx.fill();
      ctx.lineWidth=2; ctx.strokeStyle = s.color; ctx.stroke();
    });
  });

  ctx.fillStyle = resolveVar('--text-muted'); ctx.font = '10px ' + getComputedStyle(document.body).fontFamily; ctx.textBaseline='top';
  currentTrendData().forEach((d,i) => ctx.fillText(d.day, xAt(i)-14, h-bottom+6));

  S.xAt = xAt;
  S.cv.onmousemove = (e) => {
    const r = S.cv.getBoundingClientRect();
    const mx = e.clientX - r.left;
    let idx = 0, best = Infinity;
    currentTrendData().forEach((d,i) => { const dx = Math.abs(xAt(i)-mx); if (dx<best){best=dx; idx=i;} });
    const d = currentTrendData()[idx];
    showTip(e.clientX, e.clientY, `
      <div class="tt-title">${d.day}</div>
      <div class="tt-row"><span style="color:${colorOf('var(--cat-atividade)')}">● Passos</span><b>${d.passos.toLocaleString('pt-PT')}</b></div>
      <div class="tt-row"><span style="color:${colorOf('var(--cat-dormir)')}">● Sono</span><b>${d.sono} h</b></div>
      <div class="tt-row"><span style="color:${colorOf('var(--status-critical)')}">● FC média</span><b>${d.fc} bpm</b></div>
    `);
  };
  S.cv.onmouseleave = hideTip;
}



function drawHrSeries(id){
  const S = setupCanvas(id, 190);
  if (!S) return;
  const {ctx,w,h} = S;
  ctx.clearRect(0,0,w,h);
  const padL=30, padR=8, top=14, bottom=22;
  const plotW=w-padL-padR, plotH=h-top-bottom;

  // Em modo "ao vivo" (bridge ligado) desenha o buffer real recebido por
  // WebSocket (liveHrBuffer); caso contrário, cai para a série simulada
  // currentHrSeries() — mantém o gráfico sempre com algo para mostrar.
  const live = liveState.connected && liveHrBuffer.length >= 2;
  const series = live ? liveHrBuffer : currentHrSeries();
  const label = document.getElementById('hrChartLabel');
  if (label) label.textContent = live ? '— ao vivo' : '— últimas 24h (demonstração)';

  // Escala do eixo Y calculada a partir dos valores reais da série (fixo
  // em 50-105 antes disto) — um valor fora desse intervalo fixo (ex.:
  // deteção de HR com ruído, ver "Sessão de hardware real" no
  // PROJECT_STATUS.md, 175-187 bpm) ficava desenhado fora do canvas,
  // invisível, sem qualquer indicação de que algo estava a ser cortado.
  const values = series.map(d => d.hr);
  const dataMin = Math.min(...values), dataMax = Math.max(...values);
  const pad = Math.max(5, (dataMax - dataMin) * 0.15);
  let min = Math.floor((dataMin - pad) / 5) * 5;
  let max = Math.ceil((dataMax + pad) / 5) * 5;
  if (max - min < 20) { const mid = (max + min) / 2; min = mid - 10; max = mid + 10; }

  const xAt = (i) => padL + (i/(series.length-1))*plotW;
  const yAt=(v)=> top + plotH - ((v-min)/(max-min))*plotH;

  ctx.strokeStyle=resolveVar('--border-soft'); ctx.fillStyle=resolveVar('--text-muted'); ctx.font='10px '+getComputedStyle(document.body).fontFamily;
  const tickCount = 4;
  Array.from({length: tickCount + 1}, (_, i) => Math.round(min + (i * (max - min) / tickCount)))
    .forEach(v=>{ const y=yAt(v); ctx.beginPath(); ctx.moveTo(padL,y); ctx.lineTo(w-padR,y); ctx.stroke(); ctx.fillText(v, 2, y-4); });

  // área
  const grad = ctx.createLinearGradient(0,top,0,top+plotH);
  grad.addColorStop(0, 'rgba(12,163,12,0.28)'); grad.addColorStop(1,'rgba(12,163,12,0.02)');
  ctx.beginPath(); ctx.moveTo(xAt(0), yAt(series[0].hr));
  series.forEach((d,i)=> ctx.lineTo(xAt(i), yAt(d.hr)));
  ctx.lineTo(xAt(series.length-1), top+plotH); ctx.lineTo(xAt(0), top+plotH); ctx.closePath();
  ctx.fillStyle = grad; ctx.fill();

  ctx.beginPath(); series.forEach((d,i)=>{ const x=xAt(i),y=yAt(d.hr); i===0?ctx.moveTo(x,y):ctx.lineTo(x,y); });
  ctx.strokeStyle = resolveVar('--status-good'); ctx.lineWidth=2; ctx.stroke();

  if (live){
    // Eixo com o instante (hh:mm) de cada amostra recebida.
    const step = Math.max(1, Math.floor(series.length/6));
    series.forEach((d,i)=>{
      if (i % step !== 0 && i !== series.length-1) return;
      ctx.fillStyle=resolveVar('--text-muted');
      ctx.fillText(d.label, xAt(i)-14, h-bottom+8);
    });
  } else {
    [0,6,12,18,23].forEach(hh=>{ const i = hh*2; ctx.fillStyle=resolveVar('--text-muted'); ctx.fillText(String(hh).padStart(2,'0')+'h', xAt(i)-8, h-bottom+8); });
  }

  S.cv.onmousemove = (e)=>{
    const r=S.cv.getBoundingClientRect(); const mx=e.clientX-r.left;
    let idx=0,best=Infinity; series.forEach((d,i)=>{ const dx=Math.abs(xAt(i)-mx); if(dx<best){best=dx; idx=i;} });
    const d = series[idx];
    const when = live ? d.label : `${String(Math.floor(d.t)).padStart(2,'0')}:${d.t%1?'30':'00'}`;
    showTip(e.clientX, e.clientY, `<div class="tt-title">${when}</div><div class="tt-row"><span>FC</span><b>${d.hr} bpm</b></div>`);
  };
  S.cv.onmouseleave = hideTip;
}
