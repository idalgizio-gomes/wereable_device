//Dados de exemplo — vitais seguem o payload real do firmware (ImuPpgPayloadV1); rotina é simulada (classificador HAR ainda não embarcado)
const ROUTINE_CATS = [
  {key:'dormir',      label:'Dormir',       color:'var(--cat-dormir)'},
  {key:'descanso',    label:'Descanso',     color:'var(--cat-descanso)'},
  {key:'atividade',   label:'Atividade',    color:'var(--cat-atividade)'},
  {key:'alimentacao', label:'Alimentação',  color:'var(--cat-alimentacao)'},
  {key:'higiene',     label:'Higiene',      color:'var(--cat-higiene)'},
];

function seedRand(seed){ let s = seed; return () => { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff; }; }

function buildRoutine(seed, anomalous){
  const rnd = seedRand(seed);
  const template = [
    ['dormir',0,7*60],['atividade',7*60,7*60+8],['higiene',7*60+8,7*60+22],
    ['atividade',7*60+22,7*60+50],['descanso',7*60+50,8*60+15],['alimentacao',8*60+15,8*60+35],
    ['descanso',8*60+35,9*60+30],['descanso',9*60+30,12*60],['alimentacao',12*60+30,12*60+50],
    ['descanso',13*60,17*60],['atividade',17*60,17*60+40],['descanso',17*60+40,19*60],
    ['alimentacao',19*60,19*60+25],['descanso',19*60+25,21*60+30],['higiene',21*60+30,21*60+40],
    ['dormir',22*60,24*60],
  ];
  const blocks = template.map(([cat,s,e]) => ({cat, start:s, end:e}));
  if (anomalous){
    blocks[2].end += 46;               // higiene prolongada (duche demorado)
    blocks[8].cat = 'atividade';        // substituição contextual estranha
    blocks[10].end -= 25;               // atividade truncada
  }
  return blocks;
}
//Usa demo-data.js se disponível, senão cai para build*() locais; mapa por paciente (chave PATIENTS[i].id), resolvido via current*()
const ROUTINE_TODAY_BY_PATIENT = (typeof DEMO_ROUTINE_TODAY !== 'undefined') ? DEMO_ROUTINE_TODAY : {p1: buildRoutine(7, false), p2: buildRoutine(17, false), p3: buildRoutine(27, false)};
const ROUTINE_ANOMALY_BY_PATIENT = (typeof DEMO_ROUTINE_ANOMALY !== 'undefined') ? DEMO_ROUTINE_ANOMALY : {p1: buildRoutine(7, true), p2: buildRoutine(17, true), p3: buildRoutine(27, true)};
function currentRoutineToday(){ return ROUTINE_TODAY_BY_PATIENT[selectedPatientId] || ROUTINE_TODAY_BY_PATIENT.p1; }
function currentRoutineAnomaly(){ return ROUTINE_ANOMALY_BY_PATIENT[selectedPatientId] || ROUTINE_ANOMALY_BY_PATIENT.p1; }

function buildTrend(seed){
  const rnd = seedRand(seed);
  const days=['08/07','09/07','10/07','11/07','12/07','13/07','14/07'];
  return days.map((d,i)=>({
    day:d,
    passos: Math.round(3200 + rnd()*3600 + Math.sin(i*0.9)*900),
    sono: +(5.6 + rnd()*2.4).toFixed(1),
    fc: Math.round(64 + rnd()*14),
  }));
}
const TREND_DATA_BY_PATIENT = (typeof DEMO_TREND_DATA !== 'undefined') ? DEMO_TREND_DATA : {p1: buildTrend(3), p2: buildTrend(13), p3: buildTrend(23)};
function currentTrendData(){ return TREND_DATA_BY_PATIENT[selectedPatientId] || TREND_DATA_BY_PATIENT.p1; }

//48 amostras de meia em meia hora, base 72 bpm de dia / 58 à noite
function buildHrSeries(seed){
  const rnd = seedRand(seed);
  return Array.from({length: 48}, (_, i) => {
    const hour = i / 2;
    const night = hour < 7 || hour > 22;
    return { t: hour, hr: Math.round((night ? 58 : 72) + rnd() * 10 + Math.sin(i * 0.4) * 4) };
  });
}
const HR_SERIES_BY_PATIENT = (typeof DEMO_HR_SERIES !== 'undefined') ? DEMO_HR_SERIES : {p1: buildHrSeries(5), p2: buildHrSeries(15), p3: buildHrSeries(25)};
function currentHrSeries(){ return HR_SERIES_BY_PATIENT[selectedPatientId] || HR_SERIES_BY_PATIENT.p1; }

//Limiares personalizados: baseline (média+desvio-padrão) a partir de currentTrendData(), hoje sintético; não é ainda ML treinado por pessoa
function mean(arr){ return arr.reduce((s,v) => s+v, 0) / arr.length; }
function stdDev(arr){
  const m = mean(arr);
  return Math.sqrt(mean(arr.map(v => (v-m)**2)));
}
const PERSONAL_THRESHOLD_K = 2; //média ± 2×desvio-padrão ~ 95% dos dias (aprox. normal)
function computePersonalBaseline(){
  const fc = currentTrendData().map(d => d.fc);
  const sono = currentTrendData().map(d => d.sono);
  const passos = currentTrendData().map(d => d.passos);
  return {
    fc:     { mean: mean(fc),     sd: stdDev(fc) },
    sono:   { mean: mean(sono),   sd: stdDev(sono) },
    passos: { mean: mean(passos), sd: stdDev(passos) },
  };
}
//Chamar computePersonalBaseline() de imediato dava TDZ em selectedPatientId; cada leitura chama-a de novo (lazy getter)

function getAlertMode(){
  return localStorage.getItem('carewear_alert_mode') || 'populacional';
}
function setAlertMode(mode){
  localStorage.setItem('carewear_alert_mode', mode);
  if (currentView) renderView(currentView);
}

//Consentimento e partilha de dados — controlo do Utente/Família sobre o que a equipa clínica vê; só nesta conta/navegador (sem backend)
const CONSENT_KEY = 'carewear_consent';

//Namespaced por paciente (objeto indexado por patientId em localStorage)
