/* ============================================================
   DADOS DE EXEMPLO
   ------------------------------------------------------------
   As leituras "vitais" (FC, SpO2, passos, quedas, inatividade)
   correspondem exatamente ao payload que o firmware grava
   (ImuPpgPayloadV1: steps, freefall, inactivity, spo2, hr_x10)
   e por isso são apresentadas como reais/plausíveis.

   As categorias de rotina diária (Dormir/Descanso/Atividade/
   Alimentação/Higiene) seguem o template de 21 passos descrito
   no artigo científico do projeto (classificador XGBoost +
   deteção de anomalias por LSTM Autoencoder + regras de duração)
   — esse classificador ainda não está embarcado no firmware,
   por isso estes blocos são DADOS SIMULADOS, claramente
   assinalados, e servem de maquete para quando o pipeline de
   HAR estiver disponível.
============================================================ */
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
// Preferem os dados regenerados diariamente (demo-data.js, ver <script src>
// acima) quando disponíveis; caem para as funções build*() originais
// (sempre presentes neste ficheiro) se demo-data.js faltar ou for antigo —
// nunca parte a página, só deixa de ter dados "frescos".
// BUG CORRIGIDO (2026-07-15, reportado pelo utilizador): antes disto,
// currentRoutineToday()/currentRoutineAnomaly() (e as 5 séries irmãs mais abaixo — currentTrendData(),
// currentHeatmapData(), currentNightEvents(), currentPacingTrend(), currentHrSeries()) eram UMA SÓ constante
// global, igual para os 3 pacientes — trocar de paciente em "Pacientes"
// atualizava o nome/perfil mostrado mas os gráficos continuavam a mostrar
// sempre a mesma série, dando a impressão de "dados trocados" entre
// pacientes. Agora cada série é um mapa por paciente (chave = PATIENTS[i].id)
// e as vistas leem-na através de current*() (ver mais abaixo), que resolve
// sempre pelo selectedPatientId atual.
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

/* ============================================================
   LIMIARES PERSONALIZADOS POR PESSOA (protótipo)
   ------------------------------------------------------------
   Backlog de investigação #3 (PROJECT_STATUS.md). A literatura revista
   (Iaboni et al. 2022, "Wearable multimodal sensors... personalized
   machine learning models"; revisões de "adaptive reference ranges" em
   monitorização remota) mostra consistentemente que um limiar de alerta
   igual para todos gera mais falsos positivos/negativos do que um
   limiar calculado a partir da própria linha de base da pessoa.
   Esta secção calcula essa linha de base (média + desvio-padrão) a
   partir do histórico de tendência disponível (`currentTrendData()`, 7 dias) —
   hoje sintético, porque ainda não existe o serviço de persistência
   (ver PROJECT_STATUS.md, "Base de dados"); quando esse serviço
   existir, a mesma função passa a receber histórico real sem precisar
   de alterações. NÃO é ainda um modelo de ML treinado por pessoa (isso
   exige histórico real acumulado) — é o primeiro passo honesto nessa
   direção: um limiar estatístico adaptado ao indivíduo, em vez de um
   valor de referência populacional fixo.
============================================================ */
function mean(arr){ return arr.reduce((s,v) => s+v, 0) / arr.length; }
function stdDev(arr){
  const m = mean(arr);
  return Math.sqrt(mean(arr.map(v => (v-m)**2)));
}
const PERSONAL_THRESHOLD_K = 2; // média + 2×desvio-padrão ~ 95% dos dias dentro do intervalo (aprox. normal)
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
// BUG CORRIGIDO (2026-07-16, regressão introduzida ao tornar trendData
// por-paciente): isto era `const personalBaseline = computePersonalBaseline();`,
// avaliado imediatamente à leitura do <script> — funcionava enquanto
// currentTrendData() dependia só de dados já disponíveis nesse ponto do
// ficheiro. Passou a chamar selectedPatientId, que só é declarado muito
// mais abaixo (~linha 1434) — TDZ (ReferenceError "Cannot access
// 'selectedPatientId' before initialization"), que interrompia TODO o
// resto da execução do <script> a meio, partindo a navegação da app
// inteira. computePersonalBaseline() já existia como função; só faltava
// não a chamar de imediato — cada leitura chama-a de novo (mesmo padrão
// "lazy getter" já usado por currentTrendData()/currentAnomalyLog()).

function getAlertMode(){
  return localStorage.getItem('carewear_alert_mode') || 'populacional';
}
function setAlertMode(mode){
  localStorage.setItem('carewear_alert_mode', mode);
  // Ver o mesmo bug corrigido em applyI18n() (2026-07-17): currentView é
  // fiável para qualquer vista, ao contrário do lookup por .nav-item.active.
  if (currentView) renderView(currentView);
}

/* ------------------------------------------------------------
   CONSENTIMENTO E PARTILHA DE DADOS (item nº8 do backlog de investigação)
   ------------------------------------------------------------
   A capacidade de consentir é eticamente sensível em cuidados de
   demência (ver PMC11990963 no backlog de investigação) — este cartão
   dá ao Utente/Família controlo explícito sobre o que a equipa clínica
   vê. Persistido em localStorage, com timestamp da última alteração
   (para uma auditoria mínima de quando o consentimento mudou).
   LIMITAÇÃO HONESTA: aplica-se só a esta conta/navegador (protótipo sem
   backend) — um sistema real precisaria de aplicar isto também do lado
   do servidor, não só esconder na interface.
------------------------------------------------------------ */
const CONSENT_KEY = 'carewear_consent';

// Namespaced por paciente (bug corrigido: antes era uma única chave global,
// por isso o consentimento de um paciente aplicava-se a TODOS na vista
// Médico/Técnico multi-paciente — trocar de paciente não mudava o
// resultado do bloqueio de partilha). Mesma convenção já usada para
// medicationLog/alertOccurrences: um único item em localStorage guardando
// um objeto indexado por patientId.
