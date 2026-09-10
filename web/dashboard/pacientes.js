/* Registo, acesso e atribuicao de pacientes - extraido de pacientes-alertas-medicacao.js */

const PATIENTS = [
  {
    id:'p1', dbUuid:null, name:'Maria Silva', age:72, deviceName:'Wearable', mac:'E6:ED:42:57:1F:20', lastSync:'há 4 min', status:'good',
    battery:92, ringBufferUsed:2843, ringBufferTotal:16384,
    alerts: [
      {key:'hr-alta', sev:'critical', title:'Frequência cardíaca elevada', desc:'92 bpm sustentados durante 6 min em repouso (referência: 58–78 bpm).', time:'há 6 min',
       plain:'O coração esteve a bater mais depressa do que o normal para uma pessoa em repouso, e manteve-se assim durante vários minutos seguidos (não foi só um pico rápido). Pode acontecer por esforço recente, dor, ansiedade, febre ou desidratação — mas também pode não ter causa aparente. Vale a pena verificar como a pessoa está agora e, se se mantiver ou vier acompanhado de outros sintomas, contactar o médico.'},
      {key:'inatividade-prolongada', sev:'serious',  title:'Inatividade prolongada', desc:'Sem movimento detetado desde as 14:35 (3h12min) — acima do limite configurado.', time:'há 41 min',
       plain:'O dispositivo não deteta movimento há mais tempo do que o habitual para esta hora do dia. Muitas vezes é só a pessoa a descansar ou a dormir uma sesta — mas se não for essa a rotina esperada a esta hora, pode valer a pena ir verificar em pessoa.'},
      {key:'rotina-alterada', sev:'warning',  title:'Bloco de rotina alterado', desc:'"Atividade" da tarde substituída por padrão sedentário — fora do esperado pelo template diário.', time:'há 2h',
       plain:'A pessoa costuma estar mais ativa a esta hora do dia, mas hoje ficou mais tempo parada/sentada do que é habitual. Isto sozinho não é necessariamente preocupante (pode ser só um dia mais cansativo), mas é um desvio à rotina normal que vale a pena ter em conta, especialmente se se repetir em dias seguidos.',
       occurrences:3},
      {key:'spo2-limite', sev:'warning',  title:'SpO₂ no limite', desc:'Leitura de 93% às 03:14 — uma amostra isolada, sem tendência de queda.', time:'há 9h',
       plain:'O nível de oxigénio no sangue teve uma leitura um pouco abaixo do intervalo normal (95–100%), mas foi só uma vez, sem se manter baixo nas leituras seguintes. Isto acontece com frequência por mau contacto do sensor durante o sono (ex.: mão fora da posição) e normalmente não é motivo de alarme quando é um valor isolado — mas se voltar a acontecer de forma repetida, vale a pena falar com o médico.',
       occurrences:1},
    ],
    anomalyLog: [
      {id:'A-1042', type:'Duração', detail:'Higiene 46 min acima do limite (d_max × 3.0)', detector:'Regra de duração', conf:'—', sev:'serious', time:'02/07/2026 07:22'},
      {id:'A-1041', type:'Comportamental', detail:'Substituição contextual: "Atividade" às 09:30 (era "Descanso")', detector:'LSTM Autoencoder', conf:'0.91', sev:'warning', time:'02/07/2026 09:31'},
      {id:'A-1039', type:'Duração', detail:'Bloco de atividade truncado (25 min abaixo do mínimo)', detector:'Regra de duração', conf:'—', sev:'warning', time:'01/07/2026 17:12'},
      {id:'A-1035', type:'Fisiológica', detail:'FC 92 bpm sustentada em repouso', detector:'Limiar clínico', conf:'—', sev:'critical', time:'01/07/2026 21:04'},
    ],
    medications: [
      {id:'m1', name:'Donepezilo', dose:'5 mg', times:['08:00']},
      {id:'m2', name:'Memantina', dose:'10 mg', times:['08:00','20:00']},
    ],
    adherenceHistory: [
      {day:'27/06', pct:100}, {day:'28/06', pct:100}, {day:'29/06', pct:67},
      {day:'30/06', pct:100}, {day:'01/07', pct:100}, {day:'02/07', pct:100},
    ],
  },
  {
    id:'p2', dbUuid:null, name:'António Ferreira', age:79, deviceName:'Wearable', mac:'C1:4A:9B:02:D3:6E', lastSync:'há 3h', status:'warn',
    battery:34, ringBufferUsed:9120, ringBufferTotal:16384,
    alerts: [
      {key:'sono-curto', sev:'warning', title:'Sono abaixo do habitual', desc:'4h20min de sono estimado esta noite (média das últimas 2 semanas: 6h50min).', time:'há 3h',
       plain:'A pessoa dormiu bastante menos do que é habitual para ela. Uma noite isolada mais curta não é necessariamente grave, mas se se repetir vale a pena perceber a causa (dor, desconforto, mudança de rotina).'},
    ],
    anomalyLog: [
      {id:'A-0982', type:'Duração', detail:'Sono 4h20min, abaixo de d_min × 0.30', detector:'Regra de duração', conf:'—', sev:'warning', time:'02/07/2026 06:10'},
      {id:'A-0975', type:'Fisiológica', detail:'Bateria do dispositivo abaixo de 40%', detector:'Diagnóstico do dispositivo', conf:'—', sev:'warning', time:'01/07/2026 22:40'},
    ],
    medications: [
      {id:'m1', name:'Rivastigmina (adesivo)', dose:'4.6 mg', times:['09:00']},
    ],
    adherenceHistory: [
      {day:'27/06', pct:100}, {day:'28/06', pct:0}, {day:'29/06', pct:100},
      {day:'30/06', pct:100}, {day:'01/07', pct:0}, {day:'02/07', pct:100},
    ],
  },
  {
    id:'p3', dbUuid:null, name:'Isabel Costa', age:68, deviceName:'Wearable', mac:'A8:2F:11:9C:44:B7', lastSync:'há 1 dia', status:'off',
    battery:8, ringBufferUsed:16384, ringBufferTotal:16384,
    alerts: [],
    anomalyLog: [
      {id:'A-0810', type:'Fisiológica', detail:'Dispositivo desligado/sem sincronização há mais de 12h', detector:'Diagnóstico do dispositivo', conf:'—', sev:'serious', time:'01/07/2026 09:15'},
    ],
    medications: [
      {id:'m1', name:'Quetiapina', dose:'25 mg', times:['21:00']},
    ],
    // '01/07' com pct:null/deviceOff: coerente com o anomalyLog acima (dispositivo desligado 12h+ nesse dia)
    adherenceHistory: [
      {day:'27/06', pct:100}, {day:'28/06', pct:100}, {day:'29/06', pct:100},
      {day:'30/06', pct:100}, {day:'01/07', pct:null, deviceOff:true}, {day:'02/07', pct:100},
    ],
  },
];

// ids 'p1'/'p2'/'p3' são de demonstração; ligação à BD real é via `dbUuid` (Patient.uuid), nunca adivinhada do número do id.
// `dbId` é DERIVADO do uuid por resolvePatientDbIds() via GET /api/patients/directory, nunca escrito à mão.
// hoje dbUuid é sempre null (sem BD real por trás), por isso dbId fica indefinido e o relatório semanal usa dados locais — deliberado.
async function resolvePatientDbIds(){
  if (typeof apiFetch !== 'function') return 0;
  let directory;
  try {
    const res = await apiFetch('/api/patients/directory');
    if (!res.ok) return 0;
    directory = await res.json();
  } catch (e) {
    return 0;  // API em baixo: fica-se nos dados locais, como antes
  }
  const byUuid = new Map();
  (directory && directory.patients ? directory.patients : []).forEach(row => {
    if (row && row.uuid) byUuid.set(String(row.uuid), row);
  });
  let resolved = 0;
  PATIENTS.forEach(p => {
    if (!p.dbUuid) { delete p.dbId; return; } // sem dbUuid não há resolução — nunca cai para o número do id
    const row = byUuid.get(String(p.dbUuid));
    if (!row) { delete p.dbId; return; }  // uuid desconhecido nesta base
    p.dbId = row.id;
    p.dbPseudonym = row.pseudonym || null;
    resolved++;
  });
  return resolved;
}

// sobrepõe campos "datados" com a versão regenerada diariamente (demo-data.js); os literais acima ficam como fallback
if (typeof DEMO_PATIENT_DYNAMIC !== 'undefined') {
  PATIENTS.forEach(p => {
    const dyn = DEMO_PATIENT_DYNAMIC[p.id];
    if (dyn) Object.assign(p, dyn);
  });
}

// addPatient() faz PATIENTS.push() (array mutável, apesar do const) — persiste só campos mínimos em localStorage, "replay" ao carregar
const ADDED_PATIENTS_KEY = 'carewear_added_patients';

function loadAddedPatients(){
  try {
    const raw = localStorage.getItem(ADDED_PATIENTS_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  return [];
}
function saveAddedPatients(list){
  try { localStorage.setItem(ADDED_PATIENTS_KEY, JSON.stringify(list)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
}
function buildPatientRecord({id, name, age, deviceName, mac}){
  return {
    id, name, age, deviceName, mac, lastSync:'Nunca sincronizado', status:'off',
    battery:100, ringBufferUsed:0, ringBufferTotal:16384,
    alerts: [], anomalyLog: [],
    medications: [],
    adherenceHistory: [],
  };
}
// área clínica não inventa pacientes; addPatient() é o primitivo usado só pelo signup (submitSignup() -> registerOwnPatient()), devolve o registo (ou null) p/ o chamador decidir a atribuição
function addPatient(name, age, deviceName, mac){
  name = (name || '').trim();
  deviceName = (deviceName || 'Wearable').trim();
  mac = (mac || '—').trim();
  const ageNum = parseInt(age, 10);
  if (!name || !Number.isFinite(ageNum) || ageNum <= 0) return null;
  const record = {id: 'p_added_' + Date.now(), name, age: ageNum, deviceName, mac};
  const added = loadAddedPatients();
  added.push(record);
  saveAddedPatients(added);
  PATIENTS.push(buildPatientRecord(record));
  if (currentView) renderView(currentView);
  return record;
}
// replay dos pacientes adicionados em sessões anteriores
loadAddedPatients().forEach(record => PATIENTS.push(buildPatientRecord(record)));

// atribuição paciente <-> conta clínica: sem backend real, "conta" é só o email do login (localStorage, protótipo)
// ADMIN_EMAIL = Dra. Ana Correia, entra só pelo botão "Administrador" (setLoginRole()/login() em auth-navegacao.js)
const ADMIN_EMAIL = 'ana.correia@carewear.pt';

// conta clínica de demonstração (Dr. Ricardo) usada como valor por omissão para loadClinicianAssignments()/allCliniciansList()
const DEFAULT_CLINICIAN_EMAIL = 'ricardo.alves@exemplo.pt';
const CLINICIAN_ASSIGNMENTS_KEY = 'carewear_clinician_assignments';
let currentUserEmail = '';

function isAdminUser(){
  return !!currentUserEmail && currentUserEmail.trim().toLowerCase() === ADMIN_EMAIL;
}
function loadClinicianAssignments(){
  try {
    const raw = localStorage.getItem(CLINICIAN_ASSIGNMENTS_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  // sem atribuição gravada ainda, o Dr. Ricardo fica associado a todos — evita "0 médicos" na vista de Administração
  return { [DEFAULT_CLINICIAN_EMAIL]: PATIENTS.map(p => p.id) };
}
function saveClinicianAssignments(map){
  try { localStorage.setItem(CLINICIAN_ASSIGNMENTS_KEY, JSON.stringify(map)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
}
function assignedPatientIds(){
  if (isAdminUser()) return PATIENTS.map(p => p.id);
  if (!currentUserEmail) return [];
  const map = loadClinicianAssignments();
  return map[currentUserEmail.trim().toLowerCase()] || [];
}
// usar sempre isto em vez de PATIENTS diretamente nas vistas da área clínica
function accessiblePatients(){
  if (isAdminUser()) return PATIENTS;
  const ids = assignedPatientIds();
  return PATIENTS.filter(p => ids.includes(p.id));
}
function assignPatientToCurrentUser(patientId){
  if (isAdminUser() || !currentUserEmail) return; // admin já vê tudo; sem email não há a quem atribuir
  const key = currentUserEmail.trim().toLowerCase();
  const map = loadClinicianAssignments();
  map[key] = map[key] || [];
  if (!map[key].includes(patientId)) map[key].push(patientId);
  saveClinicianAssignments(map);
}

// ligação utente <-> próprio paciente: distinto da atribuição clínico<->paciente, guardado à parte por serem conceitos diferentes
const UTENTE_PATIENT_LINK_KEY = 'carewear_utente_patient_link';

function loadUtentePatientLink(){
  try {
    const raw = localStorage.getItem(UTENTE_PATIENT_LINK_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  return {};
}
function saveUtentePatientLink(map){
  try { localStorage.setItem(UTENTE_PATIENT_LINK_KEY, JSON.stringify(map)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
}
// chamada só por submitSignup(); cria o paciente e liga-o ao email p/ login() resolver "o meu paciente" depois
function registerOwnPatient(name, age, email){
  const record = addPatient(name, age, 'Wearable', '—');
  if (!record || !email) return record;
  const map = loadUtentePatientLink();
  map[email.trim().toLowerCase()] = record.id;
  saveUtentePatientLink(map);
  return record;
}

const SELECTED_PATIENT_KEY = 'carewear_selected_patient_id';

function loadSelectedPatientId(){
  // utente: vê sempre o paciente ligado à própria conta; sem ligação, cai no primeiro de demonstração
  if (currentRole === 'utente') {
    const link = loadUtentePatientLink();
    const linked = currentUserEmail ? link[currentUserEmail.trim().toLowerCase()] : null;
    if (linked && PATIENTS.some(p => p.id === linked)) return linked;
    return PATIENTS.length ? PATIENTS[0].id : null;
  }
  const accessible = accessiblePatients();
  try {
    const saved = localStorage.getItem(SELECTED_PATIENT_KEY);
    if (saved && accessible.some(p => p.id === saved)) return saved;
  } catch (e) { /* localStorage indisponível - usa omissão */ }
  return accessible.length ? accessible[0].id : null;
}
let selectedPatientId = null; // resolvido em login(), depois de se saber currentUserEmail/currentRole

// placeholder de selectedPatient() quando a conta não tem paciente atribuído; evita o fallback "|| PATIENTS[0]" vazar dados da Maria Silva
const NO_ACCESS_PATIENT = {
  id: 'none', name: 'Nenhum paciente atribuído', age: 0,
  deviceName: '—', mac: '—', lastSync: '—', status: 'off',
  battery: 0, ringBufferUsed: 0, ringBufferTotal: 16384,
  alerts: [], anomalyLog: [], medications: [], adherenceHistory: [],
};
function selectedPatient(){
  const found = PATIENTS.find(p => p.id === selectedPatientId);
  if (found) return found;
  const accessible = accessiblePatients();
  return accessible.length ? accessible[0] : NO_ACCESS_PATIENT;
}

// ler um alerta remove-o de "Alertas recentes"/"por severidade" (unreadActiveAlerts()); passa a viver só no Histórico
function eraseAllLocalData(){
  Object.keys(localStorage)
    .filter(k => k.startsWith('carewear_'))
    .forEach(k => localStorage.removeItem(k));
  location.reload();
}

function selectPatient(id){
  // defesa em profundidade: recusa a troca mesmo que o botão nunca devesse aparecer na UI
  if (!accessiblePatients().some(p => p.id === id)) return;
  selectedPatientId = id;
  try { localStorage.setItem(SELECTED_PATIENT_KEY, id); }
  catch (e) { /* quota excedida ou localStorage indisponível - seleção fica só nesta sessão */ }
  updateClinicoPatientLabel();
  updateNotificationBadge();
  updateLiveEmergencyBanner();
  renderView('pacientes');
}

function updateClinicoPatientLabel(){
  const p = selectedPatient();
  const label = document.getElementById('navClinicoLabel');
  if (label) label.textContent = `${p.name} · ${p.age} anos`;
}

// registo de emergências (SOS/queda+inatividade) corresponde ao módulo firmware Emergency + emergencyAlertChar BLE
// cancelar emergência ativa exige confirmação reforçada — ver openEmergencyCancelModal()/confirmEmergencyCancel()
const DEVICE_REGISTRY_KEY = 'carewear_device_registry';
function loadDeviceRegistry(){
  try {
    const raw = localStorage.getItem(DEVICE_REGISTRY_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  return {};
}
function saveDeviceRegistry(map){
  try { localStorage.setItem(DEVICE_REGISTRY_KEY, JSON.stringify(map)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
}
// MAC registado por uma ligação real anterior, senão o de demonstração fixo
function registeredMacFor(patientId, demoMac){
  const reg = loadDeviceRegistry();
  return reg[patientId] || demoMac;
}
/* ------------------------------------------------------------
   HORÁRIOS RECORRENTES (2026-07-15, pedido do utilizador)
   ------------------------------------------------------------
   Antes, medicações de horário fixo (ex.: de 8 em 8h) obrigavam a
   escrever à mão "08:00, 16:00, 00:00" no campo de texto livre —
   trabalhoso e propenso a erro (esquecer uma dose, hora mal calculada).
   Estes botões pré-calculam as horas a partir de uma hora de início e
   de um intervalo em horas, e escrevem o resultado no mesmo campo
   #newMedTimes — o utilizador pode sempre editar à mão antes de
   "Adicionar", o campo de texto continua a ser a fonte de verdade.
------------------------------------------------------------ */
// FEEDBACK VISUAL (2026-07-21, reportado pelo utilizador: "não percebi o
// funcionamento/seleção" ao usar os botões de horário recorrente): antes,
// clicar preenchia #newMedTimes silenciosamente — sem nada a confirmar que
// resultou, era fácil não notar (o campo fica visualmente igual a um campo
// só preenchido à mão). Agora realça brevemente o campo e mostra uma
// confirmação textual junto do botão de intervalo personalizado.
