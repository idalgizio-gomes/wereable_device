/* Registo e adesao a medicacao - extraido de pacientes-alertas-medicacao.js */

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
  // Regista o dia em AdherenceAnalytics (medication-reminders.js) com a
  // adesão de hoje recalculada — a classe já existia mas `recordDay()`
  // nunca era chamada de lado nenhum, por isso o seu histórico ficava
  // sempre vazio (ver PROJECT_STATUS.md). Só regista percentagens reais
  // (cliques reais do utilizador), nunca um valor simulado.
  if (window.adherenceAnalytics) {
    const patient = PATIENTS.find(p => p.id === patientId);
    if (patient) window.adherenceAnalytics.recordDay(patientId, todayAdherencePct(patient));
  }
  if (currentView) renderView(currentView);
}

// 'atrasado' = já passaram mais de 30 min da hora prevista e ainda não
// foi marcada como tomada; 'pendente' = ainda dentro da janela normal;
// 'tomado' = já confirmada hoje.
function doseStatus(patientId, medId, time){
  if (isDoseTakenToday(patientId, medId, time)) return 'tomado';
  const [h, m] = time.split(':').map(Number);
  const scheduled = new Date();
  scheduled.setHours(h, m, 0, 0);
  return Date.now() > scheduled.getTime() + 30 * 60 * 1000 ? 'atrasado' : 'pendente';
}

// Adesão de hoje (%) = doses já marcadas / total de doses agendadas para
// hoje, ao longo de todos os medicamentos deste paciente.
function todayAdherencePct(patient){
  const doses = patientMedications(patient).flatMap(med => med.times.map(time => ({medId: med.id, time})));
  if (!doses.length) return null;
  const taken = doses.filter(d => isDoseTakenToday(patient.id, d.medId, d.time)).length;
  return Math.round((taken / doses.length) * 100);
}

/* ------------------------------------------------------------
   GESTÃO DE MEDICAÇÃO PELO MÉDICO (2026-07-03, pedido do utilizador)
   ------------------------------------------------------------
   `PATIENTS[i].medications` é o registo de base (dados de exemplo,
   definidos no código). Isto adiciona/remove medicamentos por cima
   dessa base, persistido em localStorage por paciente — sem alterar
   diretamente o array `PATIENTS` (que é uma constante partilhada por
   toda a sessão). `patientMedications(patient)` é a função que todo o
   resto do código deve chamar em vez de `patient.medications`
   diretamente, para ver sempre a lista atualizada.
------------------------------------------------------------ */
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

// REGISTO DE DISPOSITIVO (2026-07-21, pedido do utilizador): "quero que
// este wearable seja geral... dê para entrar em qualquer conta e seja
// reconhecido". PATIENTS[].mac é um valor de demonstração fixo por conta;
// este registo (localStorage, mesmo padrão de MED_REGISTRY_KEY) permite ao
// ÚNICO wearable físico real usado nos testes ficar associado à conta que
// estiver selecionada no momento em que ele se liga — em vez de só
// reconhecer o paciente cujo mac de demonstração calhou de coincidir. Ver
// handleBridgeMessage('device_status') para onde a associação é gravada, e
// TEMPLATES.dispositivo para onde é lida.
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

// BUG CORRIGIDO (2026-07-17, reportado pelo utilizador): o botão
// "Aplicar intervalo" chamava fillRecurringTimesCustom(), que nunca
// chegou a ser definida — clicar não fazia nada (erro silencioso na
// consola, sem alerta visível). Lê #newMedCustomInterval e reutiliza a
// mesma lógica de fillRecurringTimes(), igual aos presets fixos acima.
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

// VALIDAÇÃO COM AVISO VISÍVEL (2026-07-21, reportado pelo utilizador):
// antes, faltar o nome ou o horário fazia "Adicionar" não fazer nada,
// sem qualquer explicação — o mesmo tipo de erro silencioso já corrigido
// no botão de intervalo (ver comentário 2026-07-17 acima). Agora diz
// exatamente o que falta e como preencher.
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
// Funciona tanto para medicamentos de base (marca como removido) como
// para medicamentos adicionados pelo médico (remove da lista de
// adicionados) — o registo de adesão já guardado para esse medicamento
// não é apagado, só deixa de ter novas doses agendadas.
function removeMedicationForPatient(medId){
  const reg = loadMedRegistry();
  reg[selectedPatientId] = reg[selectedPatientId] || { added: [], removedIds: [] };
  reg[selectedPatientId].removedIds.push(medId);
  reg[selectedPatientId].added = reg[selectedPatientId].added.filter(m => m.id !== medId);
  saveMedRegistry(reg);
  if (currentView) renderView(currentView);
}

