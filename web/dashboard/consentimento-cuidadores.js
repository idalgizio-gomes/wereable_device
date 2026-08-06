function loadAllConsent(){
  try {
    const raw = localStorage.getItem(CONSENT_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  return {};
}
function loadConsent(patientId = selectedPatient().id){
  const defaults = { shareVitals:true, shareRoutine:true, shareAlerts:true, lastChanged:null };
  return { ...defaults, ...(loadAllConsent()[patientId] || {}) };
}
function setConsent(field, value, patientId = selectedPatient().id){
  const all = loadAllConsent();
  const c = loadConsent(patientId);
  c[field] = value;
  c.lastChanged = Date.now();
  all[patientId] = c;
  try { localStorage.setItem(CONSENT_KEY, JSON.stringify(all)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
  if (currentView) renderView(currentView);
}

/* ------------------------------------------------------------
   EQUIPA DE CUIDADORES — múltiplos cuidadores com permissões por papel
   (item nº10 do backlog de investigação)
   ------------------------------------------------------------
   Pesquisa (2026-07-03): apps de referência (Caring Village, Jointly)
   dão a cada membro da equipa um papel com permissões próprias (ex.:
   restringir edição de medicação/notas privadas por papel) e permitem
   remover alguém da equipa com efeito imediato — replicado aqui.
------------------------------------------------------------ */
const CAREGIVER_TEAM_KEY = 'carewear_caregiver_team';

// Namespaced por paciente (mesmo bug/correção de loadConsent() acima): uma
// única lista global de cuidadores fazia todos os pacientes partilharem a
// mesma equipa na vista Médico/Técnico multi-paciente.
function loadAllCaregiverTeams(){
  try {
    const raw = localStorage.getItem(CAREGIVER_TEAM_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  return {};
}
function loadCaregiverTeam(patientId = selectedPatient().id){
  const all = loadAllCaregiverTeams();
  if (all[patientId]) return all[patientId];
  // Exemplo inicial, só na primeira utilização deste paciente.
  return [
    { id:'cg1', name:'João Silva', role:'Familiar', canViewAlerts:true, canEdit:true },
  ];
}
function saveCaregiverTeam(team, patientId = selectedPatient().id){
  const all = loadAllCaregiverTeams();
  all[patientId] = team;
  try { localStorage.setItem(CAREGIVER_TEAM_KEY, JSON.stringify(all)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
}
function addCaregiver(){
  const nameInput = document.getElementById('newCaregiverName');
  const name = nameInput.value.trim();
  if (!name) return;
  const role = document.getElementById('newCaregiverRole').value;
  const team = loadCaregiverTeam();
  team.push({ id:'cg' + Date.now(), name, role, canViewAlerts:true, canEdit: role === 'Familiar' });
  saveCaregiverTeam(team);
  if (currentView) renderView(currentView);
}
// Remoção com efeito imediato — recomendação explícita da pesquisa: um
// familiar/cuidador tem de poder ser removido da equipa sem demora (ex.:
// fim de contrato de um cuidador pago, ou conflito familiar).
function removeCaregiver(id){
  saveCaregiverTeam(loadCaregiverTeam().filter(m => m.id !== id));
  if (currentView) renderView(currentView);
}
function setCaregiverPermission(id, field, value){
  const team = loadCaregiverTeam();
  const member = team.find(m => m.id === id);
  if (member) member[field] = value;
  saveCaregiverTeam(team);
  if (currentView) renderView(currentView);
}

/* ------------------------------------------------------------
   HORÁRIO DE INDISPONIBILIDADE DO CUIDADOR (Fase 5, ver
   bridge/notifications.py::ScheduleWindow/caregiver_unavailable_now())
   ------------------------------------------------------------
   Pedido explícito do utilizador: cuidadores podem estar incontactáveis
   durante o trabalho — um único horário igual para os 7 dias não chega
   (pode trabalhar seg-sex mas não ao fim de semana, por exemplo), por
   isso cada dia guarda a sua própria janela {weekday, start, end}, tal
   como o backend espera (weekday: 0=segunda...6=domingo, igual a
   datetime.weekday() em Python). Namespaced por paciente, mesmo padrão
   de loadCaregiverTeam()/loadConsent() acima.
------------------------------------------------------------ */
const CAREGIVER_SCHEDULE_KEY = 'carewear_caregiver_schedule';
function loadAllCaregiverSchedules(){
  try {
    const raw = localStorage.getItem(CAREGIVER_SCHEDULE_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  return {};
}
// Sem nenhuma janela definida, o cuidador é tratado como sempre
// contactável (mesma decisão conservadora de notifications.py: sem
// horário declarado, nunca escala automaticamente).
function loadCaregiverSchedule(patientId = selectedPatient().id){
  const all = loadAllCaregiverSchedules();
  return all[patientId] || [];
}
function saveCaregiverSchedule(schedule, patientId = selectedPatient().id){
  const all = loadAllCaregiverSchedules();
  all[patientId] = schedule;
  try { localStorage.setItem(CAREGIVER_SCHEDULE_KEY, JSON.stringify(all)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
}
function setCaregiverScheduleDay(weekday, field, value){
  const schedule = loadCaregiverSchedule();
  const entry = schedule.find(w => w.weekday === weekday);
  if (field === 'enabled') {
    if (value && !entry) schedule.push({ weekday, start: '09:00', end: '17:00' });
    else if (!value && entry) schedule.splice(schedule.indexOf(entry), 1);
  } else if (entry) {
    entry[field] = value;
  }
  saveCaregiverSchedule(schedule);
  if (currentView) renderView(currentView);
}
// Deriva o preset ativo a partir dos dias marcados, em vez de guardar um
// campo à parte — evita o horário e o "modo" ficarem dessincronizados se
// o utilizador editar um dia manualmente depois de escolher um preset.
function currentSchedulePreset(schedule){
  const days = new Set(schedule.map(w => w.weekday));
  if (days.size === 5 && [0,1,2,3,4].every(d => days.has(d))) return 'weekdays';
  if (days.size === 2 && [5,6].every(d => days.has(d))) return 'weekend';
  return 'custom';
}
// 'custom' não aplica nenhum padrão pré-definido — limpa a seleção para
// o utilizador escolher os dias um a um nas checkboxes abaixo (o botão
// "Escolher dias" pedido explicitamente, ao lado de "Dias úteis"/"Fim de
// semana", à semelhança dos presets de horário recorrente da medicação).
function applyCaregiverSchedulePreset(preset){
  const weekdays = preset === 'weekdays' ? [0,1,2,3,4] : preset === 'weekend' ? [5,6] : [];
  saveCaregiverSchedule(weekdays.map(weekday => ({ weekday, start: '09:00', end: '17:00' })));
  if (currentView) renderView(currentView);
}

// Suspensão pontual (pedido explícito do utilizador): em vez de editar o
// horário semanal por causa de uma exceção de um dia (ex.: hoje o
// cuidador está de folga, ou ao contrário, hoje não vai conseguir estar
// contactável mesmo fora do horário habitual), esta chave desliga
// TEMPORARIAMENTE — só para a data de hoje — a leitura do horário
// semanal, sem apagar nem alterar as janelas já configuradas. Expira
// sozinha no dia seguinte (comparação de data, sem necessidade de limpar
// nada explicitamente).
const SCHEDULE_OVERRIDE_KEY = 'carewear_schedule_override_date';
function loadAllScheduleOverrides(){
  try {
    const raw = localStorage.getItem(SCHEDULE_OVERRIDE_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  return {};
}
function isScheduleOverrideActiveToday(patientId = selectedPatient().id){
  const all = loadAllScheduleOverrides();
  return all[patientId] === new Date().toISOString().slice(0, 10);
}
function toggleScheduleOverrideToday(active, patientId = selectedPatient().id){
  const all = loadAllScheduleOverrides();
  if (active) all[patientId] = new Date().toISOString().slice(0, 10);
  else delete all[patientId];
  try { localStorage.setItem(SCHEDULE_OVERRIDE_KEY, JSON.stringify(all)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
  if (currentView) renderView(currentView);
}

/* ------------------------------------------------------------
   CONTACTO DE EMERGÊNCIA (Fase 5) — pessoa notificada se o cuidador não
   confirmar um alerta dentro do horário acima (ver notifications.py,
   EmergencyContact). Namespaced por paciente, mesmo padrão de
   loadCaregiverSchedule() acima.
------------------------------------------------------------ */
const EMERGENCY_CONTACT_KEY = 'carewear_emergency_contact';
function loadAllEmergencyContacts(){
  try {
    const raw = localStorage.getItem(EMERGENCY_CONTACT_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  return {};
}
function loadEmergencyContact(patientId = selectedPatient().id){
  const all = loadAllEmergencyContacts();
  return all[patientId] || { name:'', phone:'', relation:'' };
}
function saveEmergencyContact(contact, patientId = selectedPatient().id){
  const all = loadAllEmergencyContacts();
  all[patientId] = contact;
  try { localStorage.setItem(EMERGENCY_CONTACT_KEY, JSON.stringify(all)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
}
function updateEmergencyContactField(field, value){
  const contact = loadEmergencyContact();
  contact[field] = value;
  saveEmergencyContact(contact);
}

function buildHeatmap(seed){
  const rnd = seedRand(seed);
  const days=['Seg','Ter','Qua','Qui','Sex','Sáb','Dom'];
  return days.map(d => ({ day:d, hours: Array.from({length:24}, (_,h) => {
    const wake = h>=7 && h<=21;
    const base = wake ? 0.35 + rnd()*0.55 : 0.02 + rnd()*0.12;
    return Math.min(1, base);
  })}));
}
const HEATMAP_DATA_BY_PATIENT = (typeof DEMO_HEATMAP_DATA !== 'undefined') ? DEMO_HEATMAP_DATA : {p1: buildHeatmap(11), p2: buildHeatmap(21), p3: buildHeatmap(31)};
function currentHeatmapData(){ return HEATMAP_DATA_BY_PATIENT[selectedPatientId] || HEATMAP_DATA_BY_PATIENT.p1; }
