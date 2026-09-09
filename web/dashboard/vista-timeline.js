/* ============================================================
   RF-13 (Should) — TIMELINE UNIFICADA MULTIMODAL (2026-09-07)
   ------------------------------------------------------------
   Critério de aceitação: uma vista única que cruza atividade, sinais
   vitais, alertas e medicação sobre o MESMO eixo temporal, com zoom por
   período.

   PORQUE É QUE ISTO EXISTE: na revisão de literatura do projeto, a
   dimensão "Visualização" está a 0 de 20 estudos — nenhum trabalho do
   corpus apresenta visualização integrada dos vários sinais. Todos
   mostram um sinal de cada vez. É o ponto de diferenciação do CareWear,
   e é também o que um cuidador precisa para responder à pergunta que as
   vistas atuais não respondem: "a frequência cardíaca disparou às 15h —
   o que é que ela estava a fazer, e tinha tomado a medicação?".

   ESTADO ANTERIOR: cada sinal tinha a sua vista isolada — Rotina diária
   (blocos), Sinais vitais (FC), Histórico de alertas (lista), Medicação
   (lista). Nenhuma partilhava eixo com outra, e as listas nem sequer
   tinham posição temporal desenhada.

   DECISÕES DE DESENHO
   -------------------
   1. Canvas 2D, no mesmo padrão de canvas-graficos.js (setupCanvas/
      showTip/resolveVar/canvasFont). Nenhuma biblioteca externa: a CSP
      do index.html é `default-src 'none'` com script-src 'self', e nada
      vindo de fora carrega — nem sequer falharia de forma visível.
   2. Quatro faixas empilhadas, NÃO quatro eixos y sobrepostos. Passos,
      bpm, "houve alerta" e "tomou o comprimido" não partilham unidade
      nenhuma; desenhá-los na mesma área obrigaria a normalizações que
      sugerem correlações que os dados não suportam. O que se partilha —
      e é o ponto todo do requisito — é o eixo X.
   3. O zoom é uma JANELA sobre o dia (início + largura em minutos), não
      uma transformação de escala do canvas. Assim os rótulos, a
      espessura das linhas e os marcadores mantêm o tamanho legível em
      qualquer nível de ampliação, em vez de ficarem gigantes ou
      microscópicos (que é o que acontece com ctx.scale()).
   4. Tudo o que o canvas mostra existe também em texto, na tabela por
      baixo (RNF-08 / WCAG SC 1.1.1): um canvas é, para um leitor de
      ecrã, um retângulo vazio.
============================================================ */

/* ------------------------------------------------------------
   ESTADO DA JANELA TEMPORAL
   ------------------------------------------------------------
   Módulo com âmbito global partilhado (são classic scripts): todos os
   nomes daqui são prefixados `tl`/`TL_` para não colidirem com os das
   outras vistas.
------------------------------------------------------------ */
const TL_DAY_MINUTES = 1440;
// Larguras de janela oferecidas, em minutos. "Dia inteiro" primeiro
// porque é o enquadramento por omissão: só depois de ver o dia todo é
// que faz sentido ampliar uma hora concreta.
const TL_WINDOWS = [
  {min: 1440, label: 'Dia inteiro'},
  {min: 720,  label: '12 h'},
  {min: 360,  label: '6 h'},
  {min: 180,  label: '3 h'},
  {min: 60,   label: '1 h'},
];
let tlWindowMin = TL_DAY_MINUTES;   // largura da janela visível
let tlWindowStart = 0;              // minuto do dia onde a janela começa
// Bloco de atividade selecionado para correção (RF-09) — null quando o
// seletor de categoria está fechado.
let tlSelectedBlock = null;
// Estado do arrasto para deslocar a janela com o rato.
let tlDragFrom = null;

const TL_CANVAS_ID = 'cvTimelineUnificada';
const TL_HEIGHT = 268;

/* ------------------------------------------------------------
   ADAPTADORES DE DADOS
   ------------------------------------------------------------
   Cada faixa lê a MESMA fonte que a vista isolada correspondente já
   usava — nenhuma série nova foi inventada para esta vista. O trabalho
   aqui é só converter cada uma para "minuto do dia", que é a unidade
   comum do eixo X.
------------------------------------------------------------ */

// Converte as várias formas de "quando" que os dados de demonstração
// usam num minuto do dia (0-1439), ou null se o evento não for de hoje.
//   "há 6 min" / "há 41 min"  -> relativo, em minutos
//   "há 2h" / "há 3h12min"    -> relativo, em horas (+minutos)
//   "há 1 dia"                -> não é de hoje -> null
//   "07/09/2026 07:22"        -> absoluto; só conta se a data for hoje
//   "10:57"                   -> hora do dia, direto
// NOTA HONESTA: as horas relativas dos alertas de demonstração são
// relativas ao momento em que a página é aberta, por isso a posição
// destes marcadores muda ao longo do dia. É o comportamento correto para
// dados de demonstração assim datados — não é um bug de posicionamento.
function tlToMinuteOfDay(quando){
  if (!quando) return null;
  const s = String(quando).trim();

  const agora = new Date();
  const minutoAgora = agora.getHours() * 60 + agora.getMinutes();

  if (/dia/i.test(s)) return null;                    // "há 1 dia" e afins

  let m = s.match(/h[áa]\s*(\d+)\s*min/i);
  if (m) return tlClampDay(minutoAgora - Number(m[1]));

  m = s.match(/h[áa]\s*(\d+)\s*h(?:\s*(\d+))?/i);
  if (m) return tlClampDay(minutoAgora - (Number(m[1]) * 60 + Number(m[2] || 0)));

  m = s.match(/^(\d{2})\/(\d{2})\/(\d{4})\s+(\d{1,2}):(\d{2})$/);
  if (m){
    const d = new Date(Number(m[3]), Number(m[2]) - 1, Number(m[1]));
    const hoje = new Date();
    const mesmoDia = d.getFullYear() === hoje.getFullYear()
      && d.getMonth() === hoje.getMonth() && d.getDate() === hoje.getDate();
    return mesmoDia ? Number(m[4]) * 60 + Number(m[5]) : null;
  }

  m = s.match(/^(\d{1,2}):(\d{2})$/);
  if (m) return Number(m[1]) * 60 + Number(m[2]);

  return null;
}
function tlClampDay(min){
  // Um evento "há 3h" às 01:00 cai no dia anterior — fica preso às 00:00
  // em vez de aparecer no fim do dia, que seria uma leitura errada.
  return Math.max(0, Math.min(TL_DAY_MINUTES - 1, min));
}

function tlActivityBlocks(){
  return (typeof currentRoutineToday === 'function' ? currentRoutineToday() : []) || [];
}

// Sinais vitais: buffer REAL do bridge quando há ligação ao vivo (mesma
// regra já usada por drawHrSeries em canvas-graficos.js), série simulada
// caso contrário. A distinção nunca é escondida — é dita no subtítulo do
// cartão e no rótulo da faixa.
function tlVitals(){
  const aoVivo = typeof liveState !== 'undefined' && liveState.connected
    && typeof liveHrBuffer !== 'undefined' && liveHrBuffer.length >= 2;
  if (aoVivo){
    return {
      live: true,
      pontos: liveHrBuffer.map(d => {
        const dt = new Date(d.t * 1000);
        return {min: dt.getHours() * 60 + dt.getMinutes(), hr: d.hr};
      }),
    };
  }
  const serie = typeof currentHrSeries === 'function' ? currentHrSeries() : [];
  return {live: false, pontos: serie.map(d => ({min: Math.round(d.t * 60), hr: d.hr}))};
}

function tlAlerts(){
  const lista = typeof currentAlerts === 'function' ? currentAlerts() : [];
  return lista
    .map(a => ({...a, min: tlToMinuteOfDay(a.time)}))
    .filter(a => a.min != null)
    .sort((x, y) => x.min - y.min);
}

function tlMedicationDoses(){
  if (typeof selectedPatient !== 'function' || typeof patientMedications !== 'function') return [];
  const p = selectedPatient();
  if (!p) return [];
  return patientMedications(p).flatMap(med => (med.times || []).map(hora => ({
    medId: med.id,
    nome: med.name,
    dose: med.dose,
    hora,
    min: tlToMinuteOfDay(hora),
    estado: typeof doseStatus === 'function' ? doseStatus(p.id, med.id, hora) : 'pendente',
  }))).filter(d => d.min != null).sort((x, y) => x.min - y.min);
}

const TL_DOSE_COLOR = {
  tomado: 'var(--status-good)',
  atrasado: 'var(--status-critical)',
  pendente: 'var(--text-muted)',
};

/* ------------------------------------------------------------
   ZOOM E DESLOCAMENTO
   ------------------------------------------------------------
   Todas as alterações de janela passam por tlSetWindow(), que é o único
   sítio que valida os limites — ampliar junto à meia-noite não pode
   deixar a janela sair do dia.
------------------------------------------------------------ */
function tlSetWindow(start, largura){
  tlWindowMin = Math.max(30, Math.min(TL_DAY_MINUTES, Math.round(largura)));
  tlWindowStart = Math.max(0, Math.min(TL_DAY_MINUTES - tlWindowMin, Math.round(start)));
  tlRefresh();
}

// Amplia/reduz mantendo fixo o instante que estiver debaixo do cursor
// (ou o centro da janela, quando vem de um botão) — sem isto, ampliar
// "perde" o sítio que se estava a olhar, que é a queixa clássica de
// gráficos com zoom por botão.
function tlZoomTo(larguraNova, ancoraMin){
  const ancora = ancoraMin != null ? ancoraMin : tlWindowStart + tlWindowMin / 2;
  const fracao = (ancora - tlWindowStart) / tlWindowMin;
  tlSetWindow(ancora - fracao * larguraNova, larguraNova);
}

function tlPan(fracaoDaJanela){
  tlSetWindow(tlWindowStart + tlWindowMin * fracaoDaJanela, tlWindowMin);
}

function tlResetWindow(){
  tlSetWindow(0, TL_DAY_MINUTES);
}

/* ------------------------------------------------------------
   DESENHO
------------------------------------------------------------ */
// Espaçamento das linhas verticais de grelha, escolhido para nunca haver
// mais de ~12 marcas no eixo seja qual for o nível de ampliação.
function tlGridStep(){
  if (tlWindowMin > 720) return 180;
  if (tlWindowMin > 360) return 120;
  if (tlWindowMin > 180) return 60;
  if (tlWindowMin > 90) return 30;
  return 10;
}

function drawUnifiedTimeline(){
  const S = setupCanvas(TL_CANVAS_ID, TL_HEIGHT);
  if (!S) return;
  const {ctx, w, h} = S;
  ctx.clearRect(0, 0, w, h);

  // Goteira à esquerda para os nomes das faixas — escritos no canvas e
  // não em HTML ao lado, para ficarem sempre alinhados com as faixas
  // mesmo quando a janela do browser muda de tamanho.
  const padL = 92, padR = 12;
  const plotW = Math.max(40, w - padL - padR);
  const xAt = (min) => padL + ((min - tlWindowStart) / tlWindowMin) * plotW;
  const minAt = (x) => tlWindowStart + ((x - padL) / plotW) * tlWindowMin;

  const faixas = {
    atividade: {y: 28,  h: 28, nome: 'Atividade'},
    vitais:    {y: 68,  h: 84, nome: 'FC (bpm)'},
    alertas:   {y: 164, h: 24, nome: 'Alertas'},
    medicacao: {y: 200, h: 24, nome: 'Medicação'},
  };
  const eixoY = 240;

  const corTexto = resolveVar('--text-muted');
  const corSec = resolveVar('--text-secondary');
  const corGrelha = resolveVar('--border-soft');

  S.tlHits = [];

  /* --- grelha vertical + rótulos de hora, comuns a TODAS as faixas.
         É esta grelha única que materializa "o mesmo eixo temporal": as
         quatro faixas são cortadas exatamente pelas mesmas linhas. --- */
  const passo = tlGridStep();
  const primeira = Math.ceil(tlWindowStart / passo) * passo;
  ctx.font = canvasFont(10);
  ctx.textBaseline = 'top';
  for (let m = primeira; m <= tlWindowStart + tlWindowMin; m += passo){
    const x = xAt(m);
    ctx.strokeStyle = corGrelha;
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, faixas.atividade.y - 8); ctx.lineTo(x, eixoY - 6); ctx.stroke();
    ctx.fillStyle = corTexto;
    // fmtMin(1440) devolve "24:00", que é o rótulo certo para o fim do
    // dia — não é preciso reduzir a 00:00.
    ctx.fillText(fmtMin(m), x - 14, eixoY);
  }

  /* --- nomes das faixas --- */
  ctx.textBaseline = 'middle';
  ctx.font = canvasFont(11, '600');
  Object.values(faixas).forEach(f => {
    ctx.fillStyle = corSec;
    ctx.fillText(f.nome, 0, f.y + f.h / 2);
  });

  /* --- FAIXA 1: atividade (blocos de rotina classificados) --- */
  const catMap = Object.fromEntries(ROUTINE_CATS.map(c => [c.key, c]));
  ctx.fillStyle = resolveVar('--bg-surface-2');
  roundRect(ctx, padL, faixas.atividade.y, plotW, faixas.atividade.h, 5); ctx.fill();
  tlActivityBlocks().forEach(b => {
    if (b.end < tlWindowStart || b.start > tlWindowStart + tlWindowMin) return;
    const x0 = Math.max(padL, xAt(b.start));
    const x1 = Math.min(padL + plotW, xAt(b.end));
    if (x1 - x0 < 0.5) return;
    const cat = catMap[b.cat];
    ctx.fillStyle = colorOf(cat ? cat.color : 'var(--text-muted)');
    ctx.fillRect(x0, faixas.atividade.y + 2, Math.max(2, x1 - x0), faixas.atividade.h - 4);
    // Nome da categoria dentro do bloco, mas só quando lá cabe — a partir
    // de ~6h de janela os blocos ficam largos e o texto ajuda; no dia
    // inteiro ficaria tudo sobreposto e ilegível.
    if (x1 - x0 > 64 && cat){
      ctx.fillStyle = resolveVar('--bg-page');
      ctx.font = canvasFont(10, '600');
      ctx.fillText(cat.label, x0 + 6, faixas.atividade.y + faixas.atividade.h / 2);
    }
    S.tlHits.push({
      x0, x1, y0: faixas.atividade.y, y1: faixas.atividade.y + faixas.atividade.h,
      tipo: 'atividade', dados: b,
    });
  });

  /* --- FAIXA 2: sinais vitais (frequência cardíaca) --- */
  const vitais = tlVitals();
  const visiveis = vitais.pontos.filter(p => p.min >= tlWindowStart - 30 && p.min <= tlWindowStart + tlWindowMin + 30);
  if (visiveis.length >= 2){
    const valores = visiveis.map(p => p.hr);
    // Escala calculada a partir dos valores REALMENTE visíveis, e não do
    // dia inteiro: ao ampliar uma hora, uma escala fixa achataria a linha
    // numa reta e o zoom não mostraria nada de novo. Mesma lição já
    // aprendida em drawHrSeries() (eixo fixo 50-105 escondia leituras).
    let vmin = Math.min(...valores), vmax = Math.max(...valores);
    const folga = Math.max(4, (vmax - vmin) * 0.2);
    vmin = Math.floor((vmin - folga) / 5) * 5;
    vmax = Math.ceil((vmax + folga) / 5) * 5;
    if (vmax - vmin < 15){ const meio = (vmax + vmin) / 2; vmin = meio - 8; vmax = meio + 8; }
    const yAt = (v) => faixas.vitais.y + faixas.vitais.h - ((v - vmin) / (vmax - vmin)) * faixas.vitais.h;

    ctx.strokeStyle = corGrelha; ctx.lineWidth = 1;
    ctx.font = canvasFont(9.5);
    ctx.textBaseline = 'middle';
    [vmin, Math.round((vmin + vmax) / 2), vmax].forEach(v => {
      const y = yAt(v);
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + plotW, y); ctx.stroke();
      ctx.fillStyle = corTexto;
      ctx.fillText(String(v), padL + plotW + 3, y);
    });

    ctx.beginPath();
    visiveis.forEach((p, i) => {
      const x = xAt(p.min), y = yAt(p.hr);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.strokeStyle = resolveVar('--status-good');
    ctx.lineWidth = 2; ctx.lineJoin = 'round';
    ctx.save();
    // Recorte para a linha não escapar para a goteira dos nomes quando a
    // janela corta a série a meio.
    ctx.beginPath(); ctx.rect(padL, faixas.vitais.y - 2, plotW, faixas.vitais.h + 4); ctx.clip();
    ctx.stroke();
    ctx.restore();

    S.tlVitais = {visiveis, yAt, vmin, vmax};
  } else {
    ctx.fillStyle = corTexto;
    ctx.font = canvasFont(11);
    ctx.fillText('Sem leituras de FC neste período.', padL + 6, faixas.vitais.y + faixas.vitais.h / 2);
    S.tlVitais = null;
  }

  /* --- FAIXA 3: alertas (losangos, cor = gravidade) --- */
  ctx.strokeStyle = corGrelha; ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padL, faixas.alertas.y + faixas.alertas.h / 2);
  ctx.lineTo(padL + plotW, faixas.alertas.y + faixas.alertas.h / 2);
  ctx.stroke();
  tlAlerts().forEach(a => {
    if (a.min < tlWindowStart || a.min > tlWindowStart + tlWindowMin) return;
    const x = xAt(a.min), y = faixas.alertas.y + faixas.alertas.h / 2;
    const falso = typeof isAlertMarkedFalsePositive === 'function' && isAlertMarkedFalsePositive(a.key);
    ctx.save();
    ctx.translate(x, y); ctx.rotate(Math.PI / 4);
    // Marcado como falso positivo (RF-09) fica em contorno vazado: o
    // alerta continua desenhado — apagá-lo esconderia que o sistema o
    // gerou —, mas deixa de competir visualmente com os que valem.
    if (falso){
      ctx.strokeStyle = colorOf(SEV_COLOR[a.sev] || 'var(--text-muted)');
      ctx.lineWidth = 1.6; ctx.setLineDash([2, 2]);
      ctx.strokeRect(-6, -6, 12, 12);
      ctx.setLineDash([]);
    } else {
      ctx.fillStyle = colorOf(SEV_COLOR[a.sev] || 'var(--text-muted)');
      ctx.fillRect(-6, -6, 12, 12);
    }
    ctx.restore();
    S.tlHits.push({x0: x - 9, x1: x + 9, y0: y - 9, y1: y + 9, tipo: 'alerta', dados: a});
  });

  /* --- FAIXA 4: medicação (círculos, cor = estado da dose) --- */
  ctx.strokeStyle = corGrelha;
  ctx.beginPath();
  ctx.moveTo(padL, faixas.medicacao.y + faixas.medicacao.h / 2);
  ctx.lineTo(padL + plotW, faixas.medicacao.y + faixas.medicacao.h / 2);
  ctx.stroke();
  tlMedicationDoses().forEach(d => {
    if (d.min < tlWindowStart || d.min > tlWindowStart + tlWindowMin) return;
    const x = xAt(d.min), y = faixas.medicacao.y + faixas.medicacao.h / 2;
    ctx.beginPath(); ctx.arc(x, y, 6, 0, Math.PI * 2);
    ctx.fillStyle = colorOf(TL_DOSE_COLOR[d.estado] || 'var(--text-muted)');
    ctx.fill();
    if (d.estado === 'pendente'){
      // Dose ainda por tomar: só contorno, para não parecer confirmada.
      ctx.fillStyle = resolveVar('--bg-surface');
      ctx.beginPath(); ctx.arc(x, y, 3.4, 0, Math.PI * 2); ctx.fill();
    }
    S.tlHits.push({x0: x - 9, x1: x + 9, y0: y - 9, y1: y + 9, tipo: 'medicacao', dados: d});
  });

  /* --- interação --- */
  S.cv.onmousemove = (e) => {
    const r = S.cv.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;

    if (tlDragFrom != null){
      const deslocado = (tlDragFrom.x - mx) / plotW * tlWindowMin;
      tlSetWindow(tlDragFrom.start + deslocado, tlWindowMin);
      return;
    }

    const alvo = S.tlHits.find(hit => mx >= hit.x0 && mx <= hit.x1 && my >= hit.y0 && my <= hit.y1);
    if (alvo){
      S.cv.style.cursor = alvo.tipo === 'atividade' ? 'pointer' : 'default';
      showTip(e.clientX, e.clientY, tlTooltipHtml(alvo));
      return;
    }
    // Fora de qualquer marcador, mas dentro da faixa de sinais vitais:
    // mostra a leitura mais próxima no tempo. Sem isto, a única faixa
    // contínua da vista era a única sem informação ao passar o rato.
    if (S.tlVitais && my >= faixas.vitais.y && my <= faixas.vitais.y + faixas.vitais.h && mx >= padL){
      const alvoMin = minAt(mx);
      let melhor = null, dist = Infinity;
      S.tlVitais.visiveis.forEach(p => {
        const d = Math.abs(p.min - alvoMin);
        if (d < dist){ dist = d; melhor = p; }
      });
      if (melhor && dist <= tlWindowMin * 0.06){
        S.cv.style.cursor = 'crosshair';
        showTip(e.clientX, e.clientY, `
          <div class="tt-title">${fmtMin(melhor.min)}</div>
          <div class="tt-row"><span>FC</span><b>${melhor.hr} bpm</b></div>
          <div class="tt-row"><span>Origem</span><b>${vitais.live ? 'sensor' : 'demonstração'}</b></div>
        `);
        return;
      }
    }
    S.cv.style.cursor = tlWindowMin < TL_DAY_MINUTES ? 'grab' : 'default';
    hideTip();
  };
  S.cv.onmouseleave = () => { hideTip(); tlDragFrom = null; };

  S.cv.onmousedown = (e) => {
    if (tlWindowMin >= TL_DAY_MINUTES) return; // nada para deslocar
    const r = S.cv.getBoundingClientRect();
    tlDragFrom = {x: e.clientX - r.left, start: tlWindowStart};
    S.cv.style.cursor = 'grabbing';
  };
  window.addEventListener('mouseup', tlEndDrag);

  S.cv.onclick = (e) => {
    const r = S.cv.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const alvo = S.tlHits.find(hit => mx >= hit.x0 && mx <= hit.x1 && my >= hit.y0 && my <= hit.y1);
    if (alvo && alvo.tipo === 'atividade'){
      tlSelectedBlock = alvo.dados;
      tlRenderBlockCorrection();
    }
  };

  // Roda do rato = ampliar/reduzir centrado no cursor. passive:false
  // porque é preciso preventDefault para a página não deslizar ao mesmo
  // tempo; onwheel (propriedade) seria registado como passivo em alguns
  // motores e o preventDefault seria ignorado.
  S.cv.addEventListener('wheel', (e) => {
    e.preventDefault();
    const r = S.cv.getBoundingClientRect();
    const ancora = minAt(e.clientX - r.left);
    tlZoomTo(e.deltaY > 0 ? tlWindowMin * 1.35 : tlWindowMin / 1.35, ancora);
  }, {passive: false});

  // TECLADO (RNF-08, SC 2.1.1): o canvas é focável (tabindex="0" no
  // template) e responde às mesmas ações que o rato. Sem isto, o zoom e
  // o deslocamento — a parte que torna esta vista útil — só existiam
  // para quem usa rato.
  S.cv.onkeydown = (e) => {
    const teclas = {
      ArrowLeft:  () => tlPan(-0.25),
      ArrowRight: () => tlPan(0.25),
      ArrowUp:    () => tlZoomTo(tlWindowMin / 1.5),
      ArrowDown:  () => tlZoomTo(tlWindowMin * 1.5),
      '+':        () => tlZoomTo(tlWindowMin / 1.5),
      '-':        () => tlZoomTo(tlWindowMin * 1.5),
      Home:       () => tlResetWindow(),
    };
    const acao = teclas[e.key];
    if (!acao) return;
    e.preventDefault();
    acao();
    S.cv.focus();
  };
}

function tlEndDrag(){ tlDragFrom = null; }

function tlTooltipHtml(hit){
  if (hit.tipo === 'atividade'){
    const cat = ROUTINE_CATS.find(c => c.key === hit.dados.cat);
    return `
      <div class="tt-title">${cat ? cat.label : hit.dados.cat}</div>
      <div class="tt-row"><span>Início</span><b>${fmtMin(hit.dados.start)}</b></div>
      <div class="tt-row"><span>Fim</span><b>${fmtMin(hit.dados.end)}</b></div>
      <div class="tt-row"><span>Duração</span><b>${hit.dados.end - hit.dados.start} min</b></div>
      <div class="tt-row"><span>Clicar</span><b>corrigir classificação</b></div>
    `;
  }
  if (hit.tipo === 'alerta'){
    const falso = typeof isAlertMarkedFalsePositive === 'function' && isAlertMarkedFalsePositive(hit.dados.key);
    return `
      <div class="tt-title">${escapeHtml(hit.dados.title)}</div>
      <div class="tt-row"><span>Hora</span><b>${fmtMin(hit.dados.min)}</b></div>
      <div class="tt-row"><span>Gravidade</span><b>${escapeHtml(hit.dados.sev)}</b></div>
      ${falso ? '<div class="tt-row"><span>Marcado</span><b>falso positivo</b></div>' : ''}
    `;
  }
  return `
    <div class="tt-title">${escapeHtml(hit.dados.nome)}</div>
    <div class="tt-row"><span>Hora</span><b>${escapeHtml(hit.dados.hora)}</b></div>
    <div class="tt-row"><span>Dose</span><b>${escapeHtml(hit.dados.dose || '—')}</b></div>
    <div class="tt-row"><span>Estado</span><b>${escapeHtml(hit.dados.estado)}</b></div>
  `;
}

/* ------------------------------------------------------------
   RF-09 dentro da timeline — corrigir a classificação de um bloco
   ------------------------------------------------------------
   Clicar num bloco de atividade abre o seletor das 5 categorias. A
   correção fica na fila de rotulagem (ver bridge-exportacao.js).

   PORQUE É QUE ISTO **NÃO** ENVIA "correct_activity" AO BRIDGE: esse
   comando corrige a classificação do momento ATUAL — o bridge lê
   activity_inference.current_category() como categoria original e grava
   o par (original, corrigida) com o carimbo temporal da receção. Enviá-lo
   para um bloco das 09:30 gravaria esse rótulo como se fosse sobre o que
   a pessoa está a fazer AGORA, corrompendo os dados de retreino. Um
   comando novo com carimbo temporal explícito é o que falta do lado do
   bridge; está descrito no relatório da tarefa.
------------------------------------------------------------ */
function tlRenderBlockCorrection(){
  const host = document.getElementById('tlBlockCorrection');
  if (!host) return;
  if (!tlSelectedBlock){ host.innerHTML = ''; return; }
  const b = tlSelectedBlock;
  const cat = ROUTINE_CATS.find(c => c.key === b.cat);
  host.innerHTML = `
    <div class="hitl-row" style="border-bottom:none;">
      <div class="hitl-main">
        <div class="hitl-title">Bloco ${fmtMin(b.start)}–${fmtMin(b.end)} classificado como
          <span style="color:${cat ? cat.color : 'var(--text-primary)'}">${cat ? cat.label : b.cat}</span></div>
        <div class="hitl-meta">Se não corresponde ao que aconteceu, escolhe a categoria certa. Fica registada como rótulo humano.</div>
        <div class="activity-chips" style="margin:10px 0 0;">
          ${ACTIVITY_CORRECTION_CATEGORIES.map(c => `
            <button type="button" class="activity-chip" onclick="tlCorrectSelectedBlock('${c}')">${c}</button>
          `).join('')}
        </div>
      </div>
      <div class="hitl-actions">
        <button type="button" class="btn-secondary" onclick="tlCloseBlockCorrection()">Fechar</button>
      </div>
    </div>
  `;
}

function tlCloseBlockCorrection(){
  tlSelectedBlock = null;
  tlRenderBlockCorrection();
}

function tlCorrectSelectedBlock(categoria){
  if (!tlSelectedBlock) return;
  const b = tlSelectedBlock;
  const cat = ROUTINE_CATS.find(c => c.key === b.cat);
  recordHitlCorrection({
    kind: 'atividade',
    target: `bloco-${b.start}-${b.end}`,
    targetLabel: `Bloco ${fmtMin(b.start)}–${fmtMin(b.end)}`,
    originalLabel: cat ? cat.label : b.cat,
    correctedLabel: categoria,
    falsePositive: true,
    sentToBridge: false, // ver o comentário do bloco acima
  });
  tlSelectedBlock = null;
  tlRefresh();
  tlRenderBlockCorrection();
}

/* ------------------------------------------------------------
   ALTERNATIVA TEXTUAL (RNF-08 / WCAG SC 1.1.1)
   ------------------------------------------------------------
   O canvas não expõe nada a um leitor de ecrã. Esta tabela lista os
   mesmos eventos da janela visível, em texto, e é atualizada em conjunto
   com o desenho. Não está escondida com .sr-only de propósito: para um
   cuidador que prefere ler horas exatas em vez de interpretar um
   gráfico, é a leitura principal, não uma versão "para deficientes".
------------------------------------------------------------ */
function tlRenderEventsTable(){
  const host = document.getElementById('tlEventsBody');
  if (!host) return;
  const fim = tlWindowStart + tlWindowMin;
  const dentro = (m) => m >= tlWindowStart && m <= fim;

  const linhas = [];
  tlActivityBlocks().forEach(b => {
    if (b.end < tlWindowStart || b.start > fim) return;
    const cat = ROUTINE_CATS.find(c => c.key === b.cat);
    linhas.push({
      min: b.start,
      cor: cat ? cat.color : 'var(--text-muted)',
      faixa: 'Atividade',
      quando: `${fmtMin(b.start)}–${fmtMin(b.end)}`,
      descricao: `${cat ? cat.label : b.cat} (${b.end - b.start} min)`,
    });
  });
  tlAlerts().forEach(a => {
    if (!dentro(a.min)) return;
    const falso = typeof isAlertMarkedFalsePositive === 'function' && isAlertMarkedFalsePositive(a.key);
    linhas.push({
      min: a.min,
      cor: SEV_COLOR[a.sev] || 'var(--text-muted)',
      faixa: 'Alerta',
      quando: fmtMin(a.min),
      descricao: escapeHtml(a.title) + (falso ? ' <b>(marcado como falso positivo)</b>' : ''),
    });
  });
  tlMedicationDoses().forEach(d => {
    if (!dentro(d.min)) return;
    linhas.push({
      min: d.min,
      cor: TL_DOSE_COLOR[d.estado] || 'var(--text-muted)',
      faixa: 'Medicação',
      quando: fmtMin(d.min),
      descricao: `${escapeHtml(d.nome)} ${escapeHtml(d.dose || '')} — ${d.estado}`,
    });
  });

  const vitais = tlVitals();
  const hrJanela = vitais.pontos.filter(p => dentro(p.min)).map(p => p.hr);
  const resumoFc = hrJanela.length
    ? `${Math.min(...hrJanela)}–${Math.max(...hrJanela)} bpm (média ${Math.round(hrJanela.reduce((s, v) => s + v, 0) / hrJanela.length)})`
    : 'sem leituras neste período';

  linhas.sort((a, b) => a.min - b.min);

  host.innerHTML = `
    <tr>
      <td class="num">${fmtMin(tlWindowStart)}–${fmtMin(fim % TL_DAY_MINUTES === 0 && fim !== 0 ? 1439 : fim)}</td>
      <td><span class="tl-ev-dot" style="background:var(--status-good)"></span>Sinais vitais</td>
      <td>Frequência cardíaca ${resumoFc} · fonte: ${vitais.live ? 'sensor ao vivo' : 'demonstração'}</td>
    </tr>
    ${linhas.map(l => `
      <tr>
        <td class="num">${l.quando}</td>
        <td><span class="tl-ev-dot" style="background:${l.cor}"></span>${l.faixa}</td>
        <td>${l.descricao}</td>
      </tr>
    `).join('')}
  `;
}

/* ------------------------------------------------------------
   REDESENHO
   ------------------------------------------------------------
   tlRefresh() atualiza SÓ o que depende da janela (canvas, rótulo,
   estado dos botões, tabela). Não chama renderView(): isso reconstruiria
   a vista inteira, perdia o scroll da página a cada passo de zoom, e —
   mais importante — renderView() é o caminho que escreve no histórico de
   navegação. A regra do histórico é "só quando a vista muda"; ampliar um
   gráfico não é mudar de vista.
------------------------------------------------------------ */
function tlRefresh(){
  drawUnifiedTimeline();
  const lbl = document.getElementById('tlWindowLabel');
  if (lbl){
    const fim = tlWindowStart + tlWindowMin;
    lbl.textContent = `${fmtMin(tlWindowStart)} – ${fmtMin(fim >= TL_DAY_MINUTES ? 1439 : fim)}`;
  }
  TL_WINDOWS.forEach((op, i) => {
    const btn = document.getElementById('tlZoom' + i);
    if (btn) btn.setAttribute('aria-pressed', String(op.min === tlWindowMin));
  });
  const btnEsq = document.getElementById('tlPanLeft');
  const btnDir = document.getElementById('tlPanRight');
  if (btnEsq) btnEsq.disabled = tlWindowStart <= 0;
  if (btnDir) btnDir.disabled = tlWindowStart + tlWindowMin >= TL_DAY_MINUTES;
  tlRenderEventsTable();
}

/* ------------------------------------------------------------
   TEMPLATE DA VISTA
------------------------------------------------------------ */
TEMPLATES.timeline = () => `
  <div class="card">
    <div class="card-head">
      <div>
        <h3>Timeline unificada <span class="sim-flag">rotina e alertas simulados</span></h3>
        <div class="card-sub">
          Atividade, frequência cardíaca, alertas e medicação do mesmo dia, no mesmo eixo de tempo.
          A frequência cardíaca vem do sensor quando o bridge está ligado; as restantes faixas são,
          para já, dados de demonstração.
        </div>
      </div>
    </div>

    <div class="tl-controls" role="group" aria-label="Período e zoom da timeline">
      ${TL_WINDOWS.map((op, i) => `
        <button type="button" class="tl-zoom-btn" id="tlZoom${i}" aria-pressed="${op.min === tlWindowMin}"
                onclick="tlZoomTo(${op.min})">${op.label}</button>
      `).join('')}
      <span class="tl-sep" aria-hidden="true"></span>
      <button type="button" class="tl-zoom-btn" id="tlPanLeft" aria-label="Recuar no tempo" onclick="tlPan(-0.25)">←</button>
      <button type="button" class="tl-zoom-btn" id="tlPanRight" aria-label="Avançar no tempo" onclick="tlPan(0.25)">→</button>
      <button type="button" class="tl-zoom-btn" onclick="tlResetWindow()">Dia todo</button>
      <span class="tl-window-label" id="tlWindowLabel" aria-live="polite"></span>
    </div>

    <div class="tl-canvas-wrap">
      <!-- tabindex="0" + role="img": focável por teclado (as teclas estão
           em S.cv.onkeydown, ver drawUnifiedTimeline) e anunciado como
           imagem com descrição, já que o conteúdo em si é pixels. A
           descrição completa está na tabela por baixo. -->
      <canvas id="${TL_CANVAS_ID}" height="${TL_HEIGHT}" tabindex="0" role="img"
              aria-label="Timeline unificada: atividade, frequência cardíaca, alertas e medicação no mesmo eixo temporal. Use as setas para deslocar e ampliar; a lista de eventos do período está na tabela abaixo."></canvas>
    </div>

    <div class="tl-lane-legend">
      <span class="legend-item"><b>Atividade:</b></span>
      ${ROUTINE_CATS.map(c => `<span class="legend-item"><span class="legend-swatch" style="background:${c.color}"></span>${c.label}</span>`).join('')}
      <span class="legend-item"><span class="legend-swatch" style="background:var(--status-good)"></span>FC (bpm)</span>
      <span class="legend-item"><b>Alertas:</b> losango, cor conforme gravidade</span>
      <span class="legend-item"><b>Medicação:</b> círculo cheio = tomada, vazado = pendente, vermelho = atrasada</span>
    </div>

    <p class="tl-hint">
      Rato: roda para ampliar, arrastar para deslocar, clicar num bloco de atividade para corrigir a classificação.
      Teclado: <kbd>Tab</kbd> até ao gráfico, depois <kbd>←</kbd> <kbd>→</kbd> para deslocar,
      <kbd>↑</kbd> <kbd>↓</kbd> (ou <kbd>+</kbd> <kbd>−</kbd>) para ampliar e <kbd>Home</kbd> para ver o dia todo.
    </p>

    <div id="tlBlockCorrection"></div>
  </div>

  <div class="card">
    <div class="card-head">
      <div>
        <h3>Eventos do período visível</h3>
        <div class="card-sub">A mesma informação do gráfico, em texto — acompanha o zoom.</div>
      </div>
    </div>
    <table class="data-table tl-events-table">
      <thead><tr><th>Hora</th><th>Faixa</th><th>Descrição</th></tr></thead>
      <tbody id="tlEventsBody"></tbody>
    </table>
  </div>
`;

AFTER_RENDER.timeline = () => {
  tlSelectedBlock = null;
  tlRefresh();
  tlRenderBlockCorrection();
};

// Redesenha ao redimensionar a janela: setupCanvas() calcula a largura a
// partir do elemento pai, por isso um canvas desenhado antes de o
// utilizador maximizar a janela ficava com o conteúdo esticado. Só age se
// a vista aberta for esta.
window.addEventListener('resize', () => {
  if (typeof currentView !== 'undefined' && currentView === 'timeline') tlRefresh();
});
