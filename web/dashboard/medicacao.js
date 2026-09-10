const MEDICATION_LOG_KEY = 'carewear_medication_log';

function todayKey(){
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

function loadMedicationLog(){
  try {
    const raw = localStorage.getItem(MEDICATION_LOG_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  return {};
}
function saveMedicationLog(map){
  try { localStorage.setItem(MEDICATION_LOG_KEY, JSON.stringify(map)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
}
let medicationLog = loadMedicationLog();

function isDoseTakenToday(patientId, medId, time){
  const day = medicationLog[patientId] && medicationLog[patientId][todayKey()];
  return !!(day && day[`${medId}_${time}`]);
}

function markDoseTaken(patientId, medId, time){
  medicationLog[patientId] = medicationLog[patientId] || {};
  medicationLog[patientId][todayKey()] = medicationLog[patientId][todayKey()] || {};
  medicationLog[patientId][todayKey()][`${medId}_${time}`] = true;
  saveMedicationLog(medicationLog);
  if (window.adherenceAnalytics) {
    const patient = PATIENTS.find(p => p.id === patientId);
    if (patient) window.adherenceAnalytics.recordDay(patientId, todayAdherencePct(patient));
  }
  if (currentView) renderView(currentView);
}

//tomado = confirmada; atrasado = +30min sem confirmar; pendente = dentro da janela
function doseStatus(patientId, medId, time){
  if (isDoseTakenToday(patientId, medId, time)) return 'tomado';
  const [h, m] = time.split(':').map(Number);
  const scheduled = new Date();
  scheduled.setHours(h, m, 0, 0);
  return Date.now() > scheduled.getTime() + 30 * 60 * 1000 ? 'atrasado' : 'pendente';
}

//adesão de hoje (%) = doses marcadas / total agendado hoje
function todayAdherencePct(patient){
  const doses = patientMedications(patient).flatMap(med => med.times.map(time => ({medId: med.id, time})));
  if (!doses.length) return null;
  const taken = doses.filter(d => isDoseTakenToday(patient.id, d.medId, d.time)).length;
  return Math.round((taken / doses.length) * 100);
}

//gestão de medicação pelo médico: adiciona/remove por cima de PATIENTS[i].medications, persistido em localStorage
const MED_REGISTRY_KEY = 'carewear_medications_registry';

function loadMedRegistry(){
  try {
    const raw = localStorage.getItem(MED_REGISTRY_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  return {};
}
function saveMedRegistry(map){
  try { localStorage.setItem(MED_REGISTRY_KEY, JSON.stringify(map)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
}
function patientMedications(patient){
  const reg = loadMedRegistry()[patient.id] || { added: [], removedIds: [] };
  const base = patient.medications.filter(m => !reg.removedIds.includes(m.id));
  return [...base, ...reg.added];
}

function flashTimesFilled(times){
  const timesEl = document.getElementById('newMedTimes');
  const confirmEl = document.getElementById('newMedTimesConfirm');
  if (timesEl){
    const prevTransition = timesEl.style.transition;
    const prevBg = timesEl.style.backgroundColor;
    timesEl.style.transition = 'background-color 0.2s';
    timesEl.style.backgroundColor = 'var(--status-good-bg)';
    setTimeout(() => {
      timesEl.style.backgroundColor = prevBg;
      setTimeout(() => { timesEl.style.transition = prevTransition; }, 250);
    }, 900);
  }
  if (confirmEl){
    confirmEl.textContent = t('medicacao.timesFilledConfirm').replace('{times}', times);
    confirmEl.style.display = '';
    clearTimeout(confirmEl._hideTimer);
    confirmEl._hideTimer = setTimeout(() => { confirmEl.style.display = 'none'; }, 4000);
  }
}

function fillRecurringTimes(intervalHours){
  const startEl = document.getElementById('newMedStartTime');
  const timesEl = document.getElementById('newMedTimes');
  if (!startEl || !timesEl) return;
  const start = startEl.value || '08:00';
  const [h0, m0] = start.split(':').map(Number);
  const startMin = h0 * 60 + m0;
  const count = Math.max(1, Math.round(24 / intervalHours));
  const times = [];
  for (let i = 0; i < count; i++){
    const totalMin = (startMin + i * intervalHours * 60) % (24 * 60);
    const h = String(Math.floor(totalMin / 60)).padStart(2, '0');
    const m = String(totalMin % 60).padStart(2, '0');
    times.push(`${h}:${m}`);
  }
  timesEl.value = times.join(', ');
  flashTimesFilled(timesEl.value);
}

function fillRecurringTimesCustom(){
  const intervalEl = document.getElementById('newMedCustomInterval');
  if (!intervalEl) return;
  const interval = Number(intervalEl.value);
  if (!Number.isFinite(interval) || interval < 1 || interval > 24) {
    showMedFormError(t('medicacao.errIntervalInvalid'));
    return;
  }
  fillRecurringTimes(interval);
}

function showMedFormError(msg){
  const el = document.getElementById('newMedFormError');
  if (!el) return;
  el.textContent = msg;
  el.style.display = '';
}
function clearMedFormError(){
  const el = document.getElementById('newMedFormError');
  if (el) el.style.display = 'none';
}

function addMedicationForPatient(){
  const nameEl = document.getElementById('newMedName');
  const doseEl = document.getElementById('newMedDose');
  const timesEl = document.getElementById('newMedTimes');
  const name = nameEl.value.trim();
  const dose = doseEl.value.trim();
  const times = timesEl.value.split(',').map(s => s.trim()).filter(Boolean);
  if (!name) {
    showMedFormError(t('medicacao.errNameRequired'));
    nameEl.focus();
    return;
  }
  if (!times.length) {
    showMedFormError(t('medicacao.errTimesRequired'));
    timesEl.focus();
    return;
  }
  clearMedFormError();
  const reg = loadMedRegistry();
  reg[selectedPatientId] = reg[selectedPatientId] || { added: [], removedIds: [] };
  reg[selectedPatientId].added.push({ id: 'm' + Date.now(), name, dose, times });
  saveMedRegistry(reg);
  if (currentView) renderView(currentView);
}
//funciona para medicamentos de base (marca removido) e adicionados (remove da lista)
function removeMedicationForPatient(medId){
  const reg = loadMedRegistry();
  reg[selectedPatientId] = reg[selectedPatientId] || { added: [], removedIds: [] };
  reg[selectedPatientId].removedIds.push(medId);
  reg[selectedPatientId].added = reg[selectedPatientId].added.filter(m => m.id !== medId);
  saveMedRegistry(reg);
  if (currentView) renderView(currentView);
}

