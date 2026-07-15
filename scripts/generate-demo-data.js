#!/usr/bin/env node
'use strict';
/*
 * scripts/generate-demo-data.js
 * ---------------------------------------------------------------
 * Gera web/dashboard/demo-data.js com dados SIMULADOS novos, determinísticos
 * por dia (seed = data UTC de execução, ou DEMO_DATA_DATE para testes/
 * reprodutibilidade). NUNCA lê nem escreve bridge/carewear_history.db nem
 * qualquer dado real — ver PROJECT_STATUS.md, "Regra de ouro" (nunca
 * misturar dados reais e simulados).
 *
 * PORQUÊ ESTE FICHEIRO EXISTE (ver relatório de motivação, artifact
 * publicado nesta sessão): os literais de demonstração em
 * web/dashboard/index.html (hrSeries, trendData, heatmapData, anomalyLog
 * de cada paciente, etc.) foram escritos uma vez com datas fixas
 * ("02/07", "27/06"...) e nunca mais atualizados — ficam desatualizados a
 * cada dia que passa. Este script regenera-os, mantendo os mesmos
 * critérios de plausibilidade já usados no projeto (ver
 * ml/synthetic_data.py::CLASS_PARAMS para os intervalos de FC por
 * categoria de atividade, e as próprias funções build*() de index.html,
 * replicadas aqui byte a byte).
 *
 * Corre via `node scripts/generate-demo-data.js` (local) ou pelo cron
 * .github/workflows/demo-data.yml (diário, 04:15 UTC).
 */

const fs = require('fs');
const path = require('path');

// ------------------------------------------------------------
// RNG determinístico — MESMO LCG que seedRand() em web/dashboard/index.html
// (linha ~1095), para manter o estilo de "seed fixa por execução" já
// estabelecido no projeto (mesmo padrão de ml/synthetic_data.py).
// ------------------------------------------------------------
function seedRand(seed) {
  let s = seed;
  return () => {
    s = (s * 1103515245 + 12345) & 0x7fffffff;
    return s / 0x7fffffff;
  };
}

// Deriva um inteiro de seed a partir de uma string "YYYY-MM-DD" — hash
// simples (djb2), suficiente para não repetir o mesmo padrão em dias
// consecutivos, sem precisar de nenhuma dependência externa.
function seedFromDateString(dateStr) {
  let hash = 5381;
  for (let i = 0; i < dateStr.length; i++) {
    hash = ((hash << 5) + hash + dateStr.charCodeAt(i)) & 0x7fffffff;
  }
  return hash || 1;
}

function todayUTC() {
  const d = new Date();
  return d.toISOString().slice(0, 10); // "YYYY-MM-DD"
}

// Formata uma data ISO "YYYY-MM-DD" para "dd/mm", o formato já usado em
// todo o dashboard (trendData, adherenceHistory, heatmapData).
function fmtDDMM(isoDate) {
  const [, m, d] = isoDate.split('-');
  return `${d}/${m}`;
}

// Devolve os últimos N dias (formato "dd/mm"), terminando em endIsoDate
// (inclusive). endOffsetDays=0 -> termina em endIsoDate; usado para
// "últimos 7 dias terminando hoje" (trendData) vs. "terminando ontem"
// (adherenceHistory, que cobre dias já fechados/completos).
function lastNDays(n, endIsoDate, endOffsetDays = 0) {
  const end = new Date(`${endIsoDate}T00:00:00Z`);
  end.setUTCDate(end.getUTCDate() - endOffsetDays);
  const out = [];
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(end);
    d.setUTCDate(d.getUTCDate() - i);
    out.push(fmtDDMM(d.toISOString().slice(0, 10)));
  }
  return out;
}

// Como fmtDDMM(), mas com ano — "dd/mm/yyyy". Usado no registo de
// anomalias (a data completa é necessária para distinguir eventos de
// anos diferentes); os gráficos/tendências continuam a usar "dd/mm" só,
// que é suficiente como rótulo de eixo.
function fmtDDMMYYYY(isoDate) {
  const [y, m, d] = isoDate.split('-');
  return `${d}/${m}/${y}`;
}
function lastNDaysFull(n, endIsoDate, endOffsetDays = 0) {
  const end = new Date(`${endIsoDate}T00:00:00Z`);
  end.setUTCDate(end.getUTCDate() - endOffsetDays);
  const out = [];
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(end);
    d.setUTCDate(d.getUTCDate() - i);
    out.push(fmtDDMMYYYY(d.toISOString().slice(0, 10)));
  }
  return out;
}

// Formata minutos-do-dia (0-1439) em "HH:MM" — réplica de fmtMin() já
// usada em index.html para os episódios de agitação noturna.
function fmtMin(totalMin) {
  const h = Math.floor(totalMin / 60) % 24;
  const m = totalMin % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

// ------------------------------------------------------------
// Réplicas das funções build*() de web/dashboard/index.html — mesma
// lógica, só a fonte de "hoje" muda (data de execução em vez de uma
// data fixa no código).
// ------------------------------------------------------------

// buildHr() — index.html:3782. Intervalos day/night (72/58 bpm de base)
// são os mesmos já usados no dashboard; não são um valor novo inventado
// aqui.
function buildHr(rnd) {
  return Array.from({ length: 48 }, (_, i) => {
    const hour = i / 2;
    const night = hour < 7 || hour > 22;
    return { t: hour, hr: Math.round((night ? 58 : 72) + rnd() * 10 + Math.sin(i * 0.4) * 4) };
  });
}

// buildTrend() — index.html:1118. Termina HOJE (data de execução), 7 dias.
function buildTrend(rnd, dateStr) {
  const days = lastNDays(7, dateStr, 0);
  return days.map((d, i) => ({
    day: d,
    passos: Math.round(3200 + rnd() * 3600 + Math.sin(i * 0.9) * 900),
    sono: +(5.6 + rnd() * 2.4).toFixed(1),
    fc: Math.round(64 + rnd() * 14),
  }));
}

// buildHeatmap() — index.html:1278.
function buildHeatmap(rnd) {
  const days = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom'];
  return days.map((d) => ({
    day: d,
    hours: Array.from({ length: 24 }, (_, h) => {
      const wake = h >= 7 && h <= 21;
      const base = wake ? 0.35 + rnd() * 0.55 : 0.02 + rnd() * 0.12;
      return Math.min(1, base);
    }),
  }));
}

// buildRoutine() — index.html:1097. O template de 24h é fixo por desenho
// (alinhado ao template clínico do artigo de base, ver PROJECT_STATUS.md)
// — só a variante "anomalous" injeta os 3 desvios já documentados. Não há
// aleatoriedade real aqui (o `rnd` do original também não é consumido);
// mantido por fidelidade ao código-fonte replicado.
function buildRoutine(rnd, anomalous) {
  const template = [
    ['dormir', 0, 7 * 60], ['atividade', 7 * 60, 7 * 60 + 8], ['higiene', 7 * 60 + 8, 7 * 60 + 22],
    ['atividade', 7 * 60 + 22, 7 * 60 + 50], ['descanso', 7 * 60 + 50, 8 * 60 + 15], ['alimentacao', 8 * 60 + 15, 8 * 60 + 35],
    ['descanso', 8 * 60 + 35, 9 * 60 + 30], ['descanso', 9 * 60 + 30, 12 * 60], ['alimentacao', 12 * 60 + 30, 12 * 60 + 50],
    ['descanso', 13 * 60, 17 * 60], ['atividade', 17 * 60, 17 * 60 + 40], ['descanso', 17 * 60 + 40, 19 * 60],
    ['alimentacao', 19 * 60, 19 * 60 + 25], ['descanso', 19 * 60 + 25, 21 * 60 + 30], ['higiene', 21 * 60 + 30, 21 * 60 + 40],
    ['dormir', 22 * 60, 24 * 60],
  ];
  const blocks = template.map(([cat, s, e]) => ({ cat, start: s, end: e }));
  if (anomalous) {
    blocks[2].end += 46;
    blocks[8].cat = 'atividade';
    blocks[10].end -= 25;
  }
  return blocks;
}

// buildNightRestlessness() — index.html:2266.
function buildNightRestlessness(rnd) {
  const count = Math.floor(rnd() * 3);
  const events = [];
  for (let i = 0; i < count; i++) {
    const minute = 22 * 60 + 30 + Math.floor(rnd() * (7 * 60 + 60 - 22 * 60 - 30));
    events.push({ time: fmtMin(minute % (24 * 60)), durationMin: 3 + Math.floor(rnd() * 12) });
  }
  return events;
}

// buildPacingTrend() — index.html:2328.
function buildPacingTrend(rnd) {
  const days = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom'];
  return days.map((day, i) => {
    const base = 22 + rnd() * 10;
    const drift = i >= 5 ? (i - 4) * 4 : 0;
    return { day, score: Math.round(Math.min(100, base + drift)) };
  });
}

// ------------------------------------------------------------
// Campos "datados" por paciente (alerts/anomalyLog/adherenceHistory) —
// o texto/conteúdo clínico (título, descrição, explicação em linguagem
// simples) é preservado tal como já revisto em index.html; só as
// datas/horas relativas são recalculadas para "hoje" (data de execução).
// Critérios de plausibilidade (intervalos de FC, padrão de adesão) réplica
// direta de ml/synthetic_data.py::CLASS_PARAMS e do que já estava em
// index.html — não são números novos inventados nesta tarefa.
// ------------------------------------------------------------
function buildPatientDynamic(rnd, dateStr) {
  const anomalyDays = lastNDaysFull(2, dateStr, 0); // [ontem, hoje] — data completa (com ano)
  const adherenceDays = lastNDays(6, dateStr, 1); // 6 dias terminando ontem

  return {
    p1: {
      alerts: [
        { key: 'hr-alta', sev: 'critical', title: 'Frequência cardíaca elevada', desc: '92 bpm sustentados durante 6 min em repouso (referência: 58–78 bpm).', time: 'há 6 min',
          plain: 'O coração esteve a bater mais depressa do que o normal para uma pessoa em repouso, e manteve-se assim durante vários minutos seguidos (não foi só um pico rápido). Pode acontecer por esforço recente, dor, ansiedade, febre ou desidratação — mas também pode não ter causa aparente. Vale a pena verificar como a pessoa está agora e, se se mantiver ou vier acompanhado de outros sintomas, contactar o médico.' },
        { key: 'inatividade-prolongada', sev: 'serious', title: 'Inatividade prolongada', desc: 'Sem movimento detetado desde as 14:35 (3h12min) — acima do limite configurado.', time: 'há 41 min',
          plain: 'O dispositivo não deteta movimento há mais tempo do que o habitual para esta hora do dia. Muitas vezes é só a pessoa a descansar ou a dormir uma sesta — mas se não for essa a rotina esperada a esta hora, pode valer a pena ir verificar em pessoa.' },
        { key: 'rotina-alterada', sev: 'warning', title: 'Bloco de rotina alterado', desc: '"Atividade" da tarde substituída por padrão sedentário — fora do esperado pelo template diário.', time: 'há 2h',
          plain: 'A pessoa costuma estar mais ativa a esta hora do dia, mas hoje ficou mais tempo parada/sentada do que é habitual. Isto sozinho não é necessariamente preocupante (pode ser só um dia mais cansativo), mas é um desvio à rotina normal que vale a pena ter em conta, especialmente se se repetir em dias seguidos.', occurrences: 3 },
        { key: 'spo2-limite', sev: 'warning', title: 'SpO₂ no limite', desc: 'Leitura de 93% às 03:14 — uma amostra isolada, sem tendência de queda.', time: 'há 9h',
          plain: 'O nível de oxigénio no sangue teve uma leitura um pouco abaixo do intervalo normal (95–100%), mas foi só uma vez, sem se manter baixo nas leituras seguintes. Isto acontece com frequência por mau contacto do sensor durante o sono (ex.: mão fora da posição) e normalmente não é motivo de alarme quando é um valor isolado — mas se voltar a acontecer de forma repetida, vale a pena falar com o médico.', occurrences: 1 },
      ],
      anomalyLog: [
        { id: 'A-1042', type: 'Duração', detail: 'Higiene 46 min acima do limite (d_max × 3.0)', detector: 'Regra de duração', conf: '—', sev: 'serious', time: `${anomalyDays[1]} 07:22` },
        { id: 'A-1041', type: 'Comportamental', detail: 'Substituição contextual: "Atividade" às 09:30 (era "Descanso")', detector: 'LSTM Autoencoder', conf: '0.91', sev: 'warning', time: `${anomalyDays[1]} 09:31` },
        { id: 'A-1039', type: 'Duração', detail: 'Bloco de atividade truncado (25 min abaixo do mínimo)', detector: 'Regra de duração', conf: '—', sev: 'warning', time: `${anomalyDays[0]} 17:12` },
        { id: 'A-1035', type: 'Fisiológica', detail: 'FC 92 bpm sustentada em repouso', detector: 'Limiar clínico', conf: '—', sev: 'critical', time: `${anomalyDays[0]} 21:04` },
      ],
      adherenceHistory: adherenceDays.map((day, i) => ({ day, pct: i === 2 ? 67 : 100 })),
    },
    p2: {
      alerts: [
        { key: 'sono-curto', sev: 'warning', title: 'Sono abaixo do habitual', desc: '4h20min de sono estimado esta noite (média das últimas 2 semanas: 6h50min).', time: 'há 3h',
          plain: 'A pessoa dormiu bastante menos do que é habitual para ela. Uma noite isolada mais curta não é necessariamente grave, mas se se repetir vale a pena perceber a causa (dor, desconforto, mudança de rotina).' },
      ],
      anomalyLog: [
        { id: 'A-0982', type: 'Duração', detail: 'Sono 4h20min, abaixo de d_min × 0.30', detector: 'Regra de duração', conf: '—', sev: 'warning', time: `${anomalyDays[1]} 06:10` },
        { id: 'A-0975', type: 'Fisiológica', detail: 'Bateria do dispositivo abaixo de 40%', detector: 'Diagnóstico do dispositivo', conf: '—', sev: 'warning', time: `${anomalyDays[0]} 22:40` },
      ],
      adherenceHistory: adherenceDays.map((day, i) => ({ day, pct: (i === 1 || i === 4) ? 0 : 100 })),
    },
    p3: {
      alerts: [],
      anomalyLog: [
        { id: 'A-0810', type: 'Fisiológica', detail: 'Dispositivo desligado/sem sincronização há mais de 12h', detector: 'Diagnóstico do dispositivo', conf: '—', sev: 'serious', time: `${anomalyDays[0]} 09:15` },
      ],
      adherenceHistory: adherenceDays.map((day) => ({ day, pct: 100 })),
    },
  };
}

function serializeConst(varName, value) {
  return `const ${varName} = ${JSON.stringify(value, null, 2)};\n`;
}

function main() {
  const dateStr = process.env.DEMO_DATA_DATE || todayUTC();
  const seed = seedFromDateString(dateStr);
  const rnd = seedRand(seed);

  const hrSeries = buildHr(rnd);
  const trendData = buildTrend(rnd, dateStr);
  const heatmapData = buildHeatmap(rnd);
  const routineToday = buildRoutine(rnd, false);
  const routineAnomaly = buildRoutine(rnd, true);
  const nightEvents = buildNightRestlessness(rnd);
  const pacingTrend = buildPacingTrend(rnd);
  const patientDynamic = buildPatientDynamic(rnd, dateStr);

  const out = [
    '// web/dashboard/demo-data.js',
    '// GERADO AUTOMATICAMENTE por scripts/generate-demo-data.js — NAO EDITAR A MAO.',
    `// Gerado em: ${new Date().toISOString()}`,
    `// Seed do dia: "${dateStr}" (seed=${seed})`,
    '// Nunca contem dados reais de pacientes — ver PROJECT_STATUS.md, "Regra de ouro".',
    '// Consumido por web/dashboard/index.html com fallback defensivo (ver as',
    '// consts DEMO_* la definidas) caso este ficheiro falte ou esteja desatualizado.',
    '',
    serializeConst('DEMO_HR_SERIES', hrSeries),
    serializeConst('DEMO_TREND_DATA', trendData),
    serializeConst('DEMO_HEATMAP_DATA', heatmapData),
    serializeConst('DEMO_ROUTINE_TODAY', routineToday),
    serializeConst('DEMO_ROUTINE_ANOMALY', routineAnomaly),
    serializeConst('DEMO_NIGHT_EVENTS', nightEvents),
    serializeConst('DEMO_PACING_TREND', pacingTrend),
    serializeConst('DEMO_PATIENT_DYNAMIC', patientDynamic),
  ].join('\n');

  const outPath = path.join(__dirname, '..', 'web', 'dashboard', 'demo-data.js');
  fs.writeFileSync(outPath, out);
  console.log(`demo-data.js gerado para ${dateStr} (seed=${seed}) em ${outPath}`);
}

main();
