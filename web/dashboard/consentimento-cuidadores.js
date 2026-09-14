// 2026-09-14: partilha com a equipa clínica deixou de ser opcional, a pedido da
// utilizadora — um utente com défice cognitivo/demência a decidir esconder sinais
// vitais, rotina ou alertas da equipa clínica anula o propósito de segurança do
// projeto. loadConsent() devolve sempre tudo=true, independentemente do que possa
// existir em localStorage de sessões anteriores a esta mudança; setConsent() foi
// removida (nenhuma UI chama isto — ver vista-definicoes-ajuda.js). Isto é distinto
// do consentimento RGPD formal em configuracoes-paciente.js (scopes sensor_data/
// analytics/export/research, gravado no backend) — esse mantém-se, é sobre outra
// coisa (finalidades de tratamento de dados, não segurança do utente).
function loadConsent(){
  return { shareVitals:true, shareRoutine:true, shareAlerts:true, lastChanged:null };
}

//Equipa de cuidadores — múltiplos membros com permissões por papel
const CAREGIVER_TEAM_KEY = 'carewear_caregiver_team';

//Namespaced por paciente (evita partilhar equipa entre pacientes na vista multi-paciente)
function loadAllCaregiverTeams(){
  try {
    const raw = localStorage.getItem(CAREGIVER_TEAM_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* dados corrompidos - ignora */ }
  return {};
}
function loadCaregiverTeam(patientId = selectedPatient().id){
  const all = loadAllCaregiverTeams();
  if (all[patientId]) return all[patientId];
  //Exemplo inicial, só na primeira utilização
  return [
    { id:'cg1', name:'João Silva', role:'Familiar', canViewAlerts:true, canEdit:true },
  ];
}
function saveCaregiverTeam(team, patientId = selectedPatient().id){
  const all = loadAllCaregiverTeams();
  all[patientId] = team;
  try { localStorage.setItem(CAREGIVER_TEAM_KEY, JSON.stringify(all)); }
  catch (e) { /* fica só em memória */ }
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
//Remoção com efeito imediato
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

//Horário de indisponibilidade do cuidador (ver notifications.py::ScheduleWindow) — janela {weekday, start, end} por dia; weekday 0=segunda, igual a datetime.weekday()
const CAREGIVER_SCHEDULE_KEY = 'carewear_caregiver_schedule';
function loadAllCaregiverSchedules(){
  try {
    const raw = localStorage.getItem(CAREGIVER_SCHEDULE_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* dados corrompidos - ignora */ }
  return {};
}
//Sem janela definida, cuidador é tratado como sempre contactável
function loadCaregiverSchedule(patientId = selectedPatient().id){
  const all = loadAllCaregiverSchedules();
  return all[patientId] || [];
}
function saveCaregiverSchedule(schedule, patientId = selectedPatient().id){
  const all = loadAllCaregiverSchedules();
  all[patientId] = schedule;
  try { localStorage.setItem(CAREGIVER_SCHEDULE_KEY, JSON.stringify(all)); }
  catch (e) { /* fica só em memória */ }
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
//Deriva o preset ativo a partir dos dias marcados (evita dessincronizar horário/modo)
function currentSchedulePreset(schedule){
  const days = new Set(schedule.map(w => w.weekday));
  if (days.size === 5 && [0,1,2,3,4].every(d => days.has(d))) return 'weekdays';
  if (days.size === 2 && [5,6].every(d => days.has(d))) return 'weekend';
  return 'custom';
}
//'custom' limpa a seleção para o utilizador escolher os dias manualmente
function applyCaregiverSchedulePreset(preset){
  const weekdays = preset === 'weekdays' ? [0,1,2,3,4] : preset === 'weekend' ? [5,6] : [];
  saveCaregiverSchedule(weekdays.map(weekday => ({ weekday, start: '09:00', end: '17:00' })));
  if (currentView) renderView(currentView);
}

//Suspensão pontual do horário só para hoje, sem alterar as janelas configuradas; expira sozinha no dia seguinte
const SCHEDULE_OVERRIDE_KEY = 'carewear_schedule_override_date';
function loadAllScheduleOverrides(){
  try {
    const raw = localStorage.getItem(SCHEDULE_OVERRIDE_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* dados corrompidos - ignora */ }
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
  catch (e) { /* fica só em memória */ }
  if (currentView) renderView(currentView);
}

//Contacto de emergência — notificado se o cuidador não confirmar um alerta (ver notifications.py, EmergencyContact)
const EMERGENCY_CONTACT_KEY = 'carewear_emergency_contact';
function loadAllEmergencyContacts(){
  try {
    const raw = localStorage.getItem(EMERGENCY_CONTACT_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* dados corrompidos - ignora */ }
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
  catch (e) { /* fica só em memória */ }
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
