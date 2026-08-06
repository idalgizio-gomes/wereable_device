
// Cada alerta tem, além da descrição técnica ("desc"), um campo "plain":
// uma explicação em linguagem simples, pensada para um familiar sem
// formação clínica — o que aconteceu, se é normalmente preocupante, e o
// que costuma justificar este tipo de leitura. É mostrada/escondida pelo
// botão "O que significa isto?" em alertRow() (ver mais abaixo). Esta
// funcionalidade corresponde ao item nº1 do backlog de investigação em
// PROJECT_STATUS.md — a explicação em linguagem simples é o que a
// literatura revista aponta como o que traz mais valor percebido às
// famílias, mais do que apenas mostrar scores técnicos.
/* ------------------------------------------------------------
   PACIENTES DO MÉDICO/TÉCNICO (protótipo, dados fictícios)
   ------------------------------------------------------------
   Um médico/técnico tem, na realidade, vários pacientes/wearables
   emparelhados na mesma conta. Cada paciente tem os seus PRÓPRIOS
   alertas, registo de anomalias e estatísticas de dispositivo (bateria,
   ocupação do ring buffer) — BUG CORRIGIDO (2026-07-03, reportado pelo
   utilizador): antes, mudar de paciente na vista "Pacientes" não mudava
   os dados mostrados no "Registo de anomalias" nem em "Dispositivo &
   firmware", que continuavam sempre a mostrar os valores fixos da Maria
   Silva. Agora `alerts`/`anomalyLog`/estado do dispositivo vêm sempre de
   `selectedPatient()` (ver currentAlerts()/currentAnomalyLog() abaixo).
   NOTA HONESTA: RAM/flash de programa (.data/.bss e tamanho do binário)
   são os MESMOS para todos os pacientes de propósito — é o mesmo
   firmware instalado em todos os wearables, por isso esses dois valores
   não deviam variar por paciente (só bateria e ocupação do ring buffer,
   que dependem do uso real de cada dispositivo, fazem sentido variar).
   LIMITAÇÃO HONESTA (continua a aplicar-se): selecionar aqui muda a
   identidade/dados apresentados nesta conta, mas a ligação BLE real
   continua limitada a um único dispositivo físico de cada vez — o
   bridge (ble_bridge.py) ainda não suporta escolher/alternar entre
   vários dispositivos por MAC (ver PROJECT_STATUS.md, backlog).
------------------------------------------------------------ */
const PATIENTS = [
  {
    id:'p1', name:'Maria Silva', age:72, deviceName:'Wearable', mac:'E6:ED:42:57:1F:20', lastSync:'há 4 min', status:'good',
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
    id:'p2', name:'António Ferreira', age:79, deviceName:'Wearable', mac:'C1:4A:9B:02:D3:6E', lastSync:'há 3h', status:'warn',
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
    id:'p3', name:'Isabel Costa', age:68, deviceName:'Wearable', mac:'A8:2F:11:9C:44:B7', lastSync:'há 1 dia', status:'off',
    battery:8, ringBufferUsed:16384, ringBufferTotal:16384,
    alerts: [],
    anomalyLog: [
      {id:'A-0810', type:'Fisiológica', detail:'Dispositivo desligado/sem sincronização há mais de 12h', detector:'Diagnóstico do dispositivo', conf:'—', sev:'serious', time:'01/07/2026 09:15'},
    ],
    medications: [
      {id:'m1', name:'Quetiapina', dose:'25 mg', times:['21:00']},
    ],
    // CONTRADIÇÃO REAL corrigida (2026-07-21, reportada pelo utilizador):
      // '01/07' aqui e o anomalyLog acima ('01/07/2026 09:15', dispositivo
      // desligado 12h+) eram a mesma data com pct:100 — mesmo bug do
      // gerador (ver withDeviceOffGaps() em scripts/generate-demo-data.js),
      // corrigido aqui também porque este array é o fallback usado antes
      // de demo-data.js carregar.
    adherenceHistory: [
      {day:'27/06', pct:100}, {day:'28/06', pct:100}, {day:'29/06', pct:100},
      {day:'30/06', pct:100}, {day:'01/07', pct:null, deviceOff:true}, {day:'02/07', pct:100},
    ],
  },
];

// Sobrepõe alerts/anomalyLog/adherenceHistory (os campos "datados") com a
// versão regenerada diariamente (demo-data.js, ver <script src> no topo do
// ficheiro), quando disponível. Os literais acima em PATIENTS ficam como
// fallback natural (não vazio) se demo-data.js faltar/estiver desatualizado
// — mais seguro do que remover os campos e depender só do ficheiro gerado.
if (typeof DEMO_PATIENT_DYNAMIC !== 'undefined') {
  PATIENTS.forEach(p => {
    const dyn = DEMO_PATIENT_DYNAMIC[p.id];
    if (dyn) Object.assign(p, dyn);
  });
}

/* ------------------------------------------------------------
   ADICIONAR PACIENTE (2026-07-15, pedido do utilizador — área de
   técnico)
   ------------------------------------------------------------
   Antes não havia nenhuma forma de acrescentar um paciente novo à conta
   — PATIENTS era uma lista fixa de 3. addPatient() constrói um objeto
   com a mesma forma dos 3 pacientes de demonstração (alerts/anomalyLog
   vazios, adesão vazia) e faz PATIENTS.push() diretamente — como
   PATIENTS é `const` mas continua um array mutável, todo o código
   existente que já faz PATIENTS.find()/PATIENTS.map() passa a ver o
   novo paciente sem precisar de nenhum outro ajuste. Persistido em
   localStorage (só a lista mínima de campos, não o objeto completo, para
   não duplicar dados quando as séries geradas diariamente mudam) e
   "replay" ao carregar a página, logo a seguir a este bloco.
------------------------------------------------------------ */
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
// BUG DE MODELO CORRIGIDO (2026-07-16, reportado pelo utilizador): a
// área clínica deixou de poder inventar pacientes do zero — um médico/
// técnico só pode "registar" (associar-se a) um utente que já se
// registou como Utente/Família (ver submitSignup() → registerOwnPatient()
// abaixo). addPatient() passa a ser só o primitivo de criação (usado
// unicamente pelo signup); devolve o registo criado (ou null se
// inválido) em vez de true/false, para o autor da chamada decidir a
// que conta ligar o paciente novo — deixou de decidir isso sozinho
// (antes atribuía sempre à conta clínica atual, o que já não faz
// sentido sem o formulário "Adicionar paciente").
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
// Replay dos pacientes adicionados em sessões anteriores — corre uma
// única vez ao carregar o script, depois de PATIENTS estar definido.
loadAddedPatients().forEach(record => PATIENTS.push(buildPatientRecord(record)));

/* ------------------------------------------------------------
   ATRIBUIÇÃO PACIENTE ↔ CONTA CLÍNICA (2026-07-16, pedido do utilizador)
   ------------------------------------------------------------
   Bug de acesso reportado: qualquer conta Médico/Técnico conseguia ver
   e trocar livremente entre TODOS os pacientes, sem nenhuma associação
   entre a conta e os pacientes de que é responsável — bastava trocar o
   "paciente selecionado" na lista para consultar/diagnosticar qualquer
   um. Não existe backend real (login aceita qualquer email/password,
   ver login()), por isso a "conta" aqui é só o email escrito no login.
   ADMIN_EMAIL identifica a conta de Administrador (2026-08-06,
   corrigido a pedido explícito da utilizadora: a administradora é a
   Dra. Ana Correia — antes este email tinha o nome "Ana Silva", sem
   nenhum perfil próprio, e o botão de login de admin nem existia; ver
   também DEFAULT_CLINICIAN_EMAIL abaixo para o Dr. Ricardo, que é
   médico, não administrador — os dois papéis tinham ficado confundidos
   num turno anterior desta sessão). Só entra com o botão de login
   "Administrador" (ver setLoginRole()/login() em auth-navegacao.js).
   Qualquer outro email de médico só vê os pacientes atribuídos a ele —
   atribuição guardada em localStorage (protótipo, sem backend), com
   auto-atribuição ao criar um paciente novo (addPatient()) para o
   técnico que o criou não ficar sem acesso ao que acabou de registar.
------------------------------------------------------------ */
const ADMIN_EMAIL = 'ana.correia@carewear.pt';

// Conta clínica "principal" de demonstração (Dr. Ricardo Alves, ver
// loadProfile() em auth-navegacao.js) — usada só para dar um valor por
// omissão sensato a loadClinicianAssignments()/allCliniciansList() (ver
// mais abaixo e admin-view.js) num browser onde ninguém ainda se
// registou como médico: sem isto, a vista de Administração mostrava
// sempre "0 médicos" e "todos os pacientes sem médico", o que não
// refletia a realidade da demonstração (reportado pela utilizadora).
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
  // Valor por omissão (2026-08-06, ver comentário junto de
  // DEFAULT_CLINICIAN_EMAIL acima): sem nenhuma atribuição gravada ainda
  // (browser novo), o Dr. Ricardo fica associado a todos os pacientes de
  // demonstração — evita a vista de Administração mostrar "0 médicos"/
  // "todos sem médico" só porque ninguém preencheu isto à mão ainda.
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
// Pacientes que a conta atual pode ver/selecionar — usar sempre isto em
// vez de PATIENTS diretamente nas vistas da área clínica.
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

/* ------------------------------------------------------------
   LIGAÇÃO UTENTE ↔ PRÓPRIO PACIENTE (2026-07-16, pedido do utilizador)
   ------------------------------------------------------------
   Distinto da atribuição clínico↔paciente acima: um utente/família não
   "escolhe" entre vários pacientes, é sempre o mesmo — o que criou ao
   registar-se (ver registerOwnPatient(), chamada por submitSignup()).
   Guardado à parte (não reaproveita CLINICIAN_ASSIGNMENTS_KEY) porque
   são conceitos diferentes: um é "que pacientes este clínico pode
   consultar", o outro é "qual é o paciente desta conta utente".
------------------------------------------------------------ */
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
// Chamada só por submitSignup() (perfil Utente/Família) — cria o
// paciente real (deixa de ser só uma simulação de "conta criada") e
// liga-o ao email indicado no registo, para login() conseguir
// resolver qual é "o meu paciente" da próxima vez que esta conta entrar.
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
  // Utente: vê sempre o paciente ligado à própria conta (se já se
  // registou assim — ver registerOwnPatient()); sem ligação, cai no
  // primeiro paciente de demonstração, para o login sem signup prévio
  // (ex.: a conta admin de demonstração) continuar a funcionar como
  // sempre funcionou.
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

// Placeholder devolvido por selectedPatient() quando a conta atual não
// tem NENHUM paciente atribuído — sem isto, o antigo fallback final
// "|| PATIENTS[0]" mostrava sempre os dados reais da Maria Silva a
// qualquer conta sem atribuições assim que navegasse para uma vista
// além de "Pacientes" (essa vista já tinha guarda própria, as outras
// não). Todos os campos usados nas várias vistas ficam vazios/neutros
// em vez de ausentes, para nenhum template rebentar a tentar ler
// p.name/p.alerts/etc.
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

/* ------------------------------------------------------------
   APAGAR ALERTAS — histórico/página de alertas (2026-07-03, pedido do
   utilizador)
   ------------------------------------------------------------
   Antes, "marcar como lida" só trocava o botão por um ✓ mas o alerta
   continuava visível na mesma lista. Agora: ler um alerta remove-o da
   área "Alertas recentes"/"Alertas por severidade" (ver
   unreadActiveAlerts()) e ele passa a viver só na vista "Histórico de
   alertas", onde o médico pode apagá-lo individualmente ou limpar tudo
   (mesma lógica do Registo de emergências).
------------------------------------------------------------ */
const DELETED_ALERTS_KEY = 'carewear_deleted_alerts';

function loadDeletedAlerts(){
  try {
    const raw = localStorage.getItem(DELETED_ALERTS_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  return {};
}
function saveDeletedAlerts(map){
  try { localStorage.setItem(DELETED_ALERTS_KEY, JSON.stringify(map)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
}
let deletedAlertsMap = loadDeletedAlerts();
function isAlertDeleted(fullKey){ return !!deletedAlertsMap[fullKey]; }
// Bug de permissão corrigido (2026-07-16): mesmo motivo dos guards em
// deleteAnomaly()/deleteEmergencyRecord() abaixo — apagar não é decisão
// do utente/família, só da equipa clínica.
function deleteAlert(fullKey){
  if (currentRole === 'utente') return;
  deletedAlertsMap[fullKey] = true;
  saveDeletedAlerts(deletedAlertsMap);
  updateNotificationBadge();
  if (currentView) renderView(currentView);
}
function clearAllAlertsForPatient(){
  if (currentRole === 'utente') return;
  currentAlerts().forEach(a => { deletedAlertsMap[patientAlertKey(selectedPatientId, a.key)] = true; });
  saveDeletedAlerts(deletedAlertsMap);
  updateNotificationBadge();
  if (currentView) renderView(currentView);
}

/* ------------------------------------------------------------
   APAGAR ANOMALIAS E EMERGÊNCIAS INDIVIDUALMENTE (2026-07-15)
   ------------------------------------------------------------
   Mesmo padrão de "apagar por chave, filtrar na leitura" já usado para
   alertas (ver DELETED_ALERTS_KEY acima) — nunca se remove do array de
   dados de origem (PATIENTS/EMERGENCY_LOG ou demo-data.js), só se marca
   como apagado num mapa em localStorage, para sobreviver a
   re-renderizações e a trocas de dados diárias do simulador.
------------------------------------------------------------ */
const DELETED_ANOMALIES_KEY = 'carewear_deleted_anomalies';
const DELETED_EMERGENCIES_KEY = 'carewear_deleted_emergencies';

function loadDeletedMap(key){
  try {
    const raw = localStorage.getItem(key);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  return {};
}
function saveDeletedMap(key, map){
  try { localStorage.setItem(key, JSON.stringify(map)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
}
let deletedAnomaliesMap = loadDeletedMap(DELETED_ANOMALIES_KEY);
let deletedEmergenciesMap = loadDeletedMap(DELETED_EMERGENCIES_KEY);

// Bug de permissão corrigido (2026-07-16, reportado pelo utilizador):
// apagar o próprio registo de anomalias/emergências não é uma decisão
// que caiba ao utente/família — só a equipa clínica deve poder fazê-lo.
// Os botões já estavam escondidos para utente na maior parte dos casos
// (ver TEMPLATES.anomalias/emergencias), mas o botão individual de
// apagar emergência tinha escapado a essa guarda. Em vez de confiar só
// em esconder o botão, estas funções recusam agora diretamente
// (defesa em profundidade — mesmo padrão já usado em selectPatient()).
function deleteAnomaly(anomalyId){
  if (currentRole === 'utente') return;
  const fullKey = `${selectedPatientId}:${anomalyId}`;
  deletedAnomaliesMap[fullKey] = true;
  saveDeletedMap(DELETED_ANOMALIES_KEY, deletedAnomaliesMap);
  if (currentView) renderView(currentView);
}
function deleteEmergencyRecord(emergencyId){
  if (currentRole === 'utente') return;
  const fullKey = `${selectedPatientId}:${emergencyId}`;
  deletedEmergenciesMap[fullKey] = true;
  saveDeletedMap(DELETED_EMERGENCIES_KEY, deletedEmergenciesMap);
  if (currentView) renderView(currentView);
}
// "Limpar tudo" — mesmo padrão de clearAllAlertsForPatient(), pedido do
// utilizador depois de reparar que só existia para alertas. Emergências
// ativas ficam de fora (mesma regra de segurança de deleteEmergencyRecord:
// têm de ser canceladas primeiro, não apagadas diretamente).
function clearAllAnomaliesForPatient(){
  if (currentRole === 'utente') return;
  currentAnomalyLog().forEach(a => { deletedAnomaliesMap[`${selectedPatientId}:${a.id}`] = true; });
  saveDeletedMap(DELETED_ANOMALIES_KEY, deletedAnomaliesMap);
  if (currentView) renderView(currentView);
}
function clearAllEmergenciesForPatient(){
  if (currentRole === 'utente') return;
  currentEmergencyLog().filter(e => e.status !== 'ativo').forEach(e => { deletedEmergenciesMap[`${selectedPatientId}:${e.id}`] = true; });
  saveDeletedMap(DELETED_EMERGENCIES_KEY, deletedEmergenciesMap);
  if (currentView) renderView(currentView);
}

/* ------------------------------------------------------------
   DIREITO AO ESQUECIMENTO (RGPD art. 17) — apagar dados locais
   ------------------------------------------------------------
   Antes desta função não havia nenhuma forma de apagar de uma vez os
   dados pessoais que este dashboard guarda no localStorage do browser
   (perfil com NIF/morada, consentimento, medicação, notas de
   cuidadores, alertas lidos/apagados/silenciados) — logout() só troca
   de ecrã, nunca limpou nada. Varre por prefixo em vez de listar
   chaves à mão, para não ficar desatualizada quando surgir uma chave
   nova (ex.: 'carewear_adherence_analytics_<patientId>', sufixo
   dinâmico por paciente, em medication-reminders.js). Âmbito
   deliberadamente limitado a este browser: não apaga o histórico do
   bridge (bridge/carewear_history.db, ver "Retenção de dados" na vista
   Exportar) nem os registos guardados no próprio dispositivo (ver
   "Repor leituras" abaixo) — quem quiser apagar tudo tem de usar as
   três opções.
------------------------------------------------------------ */
function eraseAllLocalData(){
  Object.keys(localStorage)
    .filter(k => k.startsWith('carewear_'))
    .forEach(k => localStorage.removeItem(k));
  location.reload();
}

// Substituem as antigas constantes globais 'alerts'/'anomalyLog' (fixas
// na Maria Silva) — todas as vistas devem chamar estas funções, nunca
// usar dados de um paciente diretamente, para mudar de paciente refletir
// sempre em todo o lado. 'currentAlerts()' exclui alertas apagados (mas
// inclui lidos, para a página de Histórico de alertas); 'unreadActiveAlerts()'
// (ver mais abaixo) é o que "Alertas recentes"/"Alertas por severidade"
// devem usar.
function currentAlerts(){
  return selectedPatient().alerts.filter(a => !isAlertDeleted(patientAlertKey(selectedPatientId, a.key)));
}
function currentAnomalyLog(){
  return selectedPatient().anomalyLog.filter(a => !deletedAnomaliesMap[`${selectedPatientId}:${a.id}`]);
}

/* ------------------------------------------------------------
   TRADUÇÃO DE ALERTAS/ANOMALIAS/EMERGÊNCIAS (pedido do utilizador:
   "Quero que as mensagens de emergência e alertas sempre traduzidos")
   ------------------------------------------------------------
   Os dados de demonstração (PATIENTS[i].alerts/anomalyLog, EMERGENCY_LOG)
   continuam a guardar o texto em português tal como sempre existiu — é
   usado como fallback (ver t()) se faltar a entrada traduzida. As
   funções abaixo resolvem o texto de facto mostrado a partir de uma
   chave estável (a.key / a.id / e.type) num namespace I18N dedicado, em
   vez de ler os campos de texto diretamente.
------------------------------------------------------------ */
const ALERT_KEY_TO_I18N_SEGMENT = {
  'hr-alta': 'hrAlta', 'inatividade-prolongada': 'inatividadeProlongada', 'rotina-alterada': 'rotinaAlterada',
  'spo2-limite': 'spo2Limite', 'sono-curto': 'sonoCurto',
};
function alertField(a, field){
  const seg = ALERT_KEY_TO_I18N_SEGMENT[a.key];
  if (!seg) return a[field];
  const i18nKey = `alertData.${seg}.${field}`;
  const val = t(i18nKey);
  return val === i18nKey ? a[field] : val;
}
const ANOMALY_TYPE_TO_I18N_SEGMENT = { 'Duração': 'duracao', 'Comportamental': 'comportamental', 'Fisiológica': 'fisiologica' };
const ANOMALY_DETECTOR_TO_I18N_SEGMENT = {
  'Regra de duração': 'regraDuracao', 'LSTM Autoencoder': 'lstm', 'Limiar clínico': 'limiarClinico', 'Diagnóstico do dispositivo': 'diagnosticoDispositivo',
};
function anomalyTypeText(a){
  const seg = ANOMALY_TYPE_TO_I18N_SEGMENT[a.type];
  return seg ? t(`anomalyType.${seg}`) : a.type;
}
function anomalyDetectorText(a){
  const seg = ANOMALY_DETECTOR_TO_I18N_SEGMENT[a.detector];
  return seg ? t(`anomalyDetector.${seg}`) : a.detector;
}
function anomalyDetailText(a){
  const i18nKey = `anomalyDetail.a${String(a.id).replace(/^A-/i, '').toLowerCase()}`;
  const val = t(i18nKey);
  return val === i18nKey ? a.detail : val;
}
function emergencyLabelText(e){
  const i18nKey = e.type === 'sos' ? 'emergencyType.sos' : e.type === 'fall' ? 'emergencyType.fall' : 'emergencyType.unknown';
  const val = t(i18nKey);
  return val === i18nKey ? (e.label || val) : val;
}
function emergencyNoteText(e){
  const i18nKey = `emergencyNote.e${String(e.id).replace(/^E-/i, '').toLowerCase()}`;
  const val = t(i18nKey);
  return val === i18nKey ? e.resolvedNote : val;
}

// Alertas ainda não lidos e não apagados — o que aparece em "Alertas
// recentes" (Resumo) e "Alertas por severidade" (Pacientes). Ler um
// alerta (markAlertRead()) remove-o desta lista; ele continua acessível
// em "Histórico de alertas" até ser apagado.
function unreadActiveAlerts(){
  return currentAlerts().filter(a => !isAlertRead(patientAlertKey(selectedPatientId, a.key)));
}

// Nº de alertas "ativos" de um paciente = não silenciados (ver
// muteAlert()) E não lidos, neste momento. Calculado, não guardado à
// parte, para nunca poder ficar dessincronizado do que a tabela de
// alertas mostra.
// BUG CORRIGIDO (2026-07-16, reportado pelo utilizador): antes só
// filtrava por "não silenciado" — um médico que já tinha lido/tratado
// todos os alertas de um paciente continuava a ver "N ativos" nesta
// pill (ex.: "4 ativos"), mas ao entrar em "Alertas por severidade"
// (que já filtrava por não-lido) via sempre a mensagem de vazio "Sem
// alertas novos" — parecia que a secção estava sempre vazia
// independentemente do paciente. As duas áreas usam agora o mesmo
// critério (não silenciado E não lido), para o número na tabela nunca
// prometer algo que a secção de severidade não mostra.
function activeAlertsCount(patient){
  return patient.alerts.filter(a =>
    !alertMutedUntil(patientAlertKey(patient.id, a.key)) &&
    !isAlertRead(patientAlertKey(patient.id, a.key))
  ).length;
}

// Chamada pelo botão "Selecionar" em cada linha da tabela de pacientes
// (TEMPLATES.pacientes). Atualiza a seleção, persiste em localStorage, e
// re-renderiza a vista + os rótulos ligados ao paciente selecionado (nav
// lateral). Ver limitação honesta no comentário acima — isto não troca a
// ligação BLE real.
function selectPatient(id){
  // Defesa em profundidade: mesmo que o botão "Selecionar" de um paciente
  // não atribuído nunca devesse aparecer na UI (ver TEMPLATES.pacientes),
  // esta função continua a recusar a troca — não depende só de esconder
  // o botão. Ver comentário em accessiblePatients() acima.
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

/* ------------------------------------------------------------
   REGISTO DE EMERGÊNCIAS — SOS manual / queda+inatividade
   ------------------------------------------------------------
   Corresponde ao módulo firmware `Emergency` (src/Emergency/) e ao
   alerta BLE `emergencyAlertChar` — ver PROJECT_STATUS.md. O bridge
   ainda não escuta essa characteristic (backlog pendente), por isso os
   eventos aqui são dados de demonstração, tal como o resto do
   dashboard antes de haver dados reais ligados.
   Pedido do utilizador (2026-07-03): um alerta de emergência ativo tem
   de poder ser cancelado (ex.: se o relógio ficar sem resposta a meio de
   um falso positivo), mas isso é uma ação de segurança crítica — exige
   uma confirmação reforçada, ao estilo de verificação em duas etapas,
   antes de ser aceite. Ver openEmergencyCancelModal()/confirmEmergencyCancel().
------------------------------------------------------------ */
const EMERGENCY_LOG = {
  p1: [
    {id:'E-204', type:'fall', label:'Queda + inatividade prolongada', time:'02/07/2026 21:14', status:'resolvido', resolvedNote:'Confirmado falso alarme pela família por telefone.'},
    {id:'E-198', type:'sos', label:'SOS manual (3 cliques)', time:'28/06/2026 11:02', status:'resolvido', resolvedNote:'Utente pediu ajuda para se levantar, sem gravidade.'},
  ],
  p2: [],
  p3: [
    {id:'E-150', type:'fall', label:'Queda + inatividade prolongada', time:'25/06/2026 03:40', status:'ativo'},
  ],
};

function currentEmergencyLog(){
  return (EMERGENCY_LOG[selectedPatientId] || []).filter(e => !deletedEmergenciesMap[`${selectedPatientId}:${e.id}`]);
}

// Mapeia EmergencyAlertType (Ble.h: 1=SOS manual, 2=queda+inatividade —
// ver alert_name já traduzido pelo bridge em ble_bridge.py) para as
// mesmas categorias usadas nas entradas de demonstração acima.
const EMERGENCY_ALERT_TYPE_TO_LOG = {
  sos_manual: { type: 'sos', label: 'SOS manual (cliques)' },
  fall_inactivity: { type: 'fall', label: 'Queda + inatividade prolongada' },
};

// Chamado por handleBridgeMessage() quando chega um alerta real via
// emergencyAlertChar (ver ble_bridge.py). Regista o evento no registo de
// emergências do paciente atualmente selecionado.
// LIMITAÇÃO HONESTA (já documentada para o seletor de paciente): o bridge
// só liga a UM dispositivo físico de cada vez — o alerta é sempre
// atribuído ao paciente selecionado na interface no momento em que chega,
// não a um paciente identificado pelo próprio hardware.
function onLiveEmergencyAlert(msg){
  const seq = toFiniteNumber(msg.seq);
  const p = selectedPatient();
  const log = EMERGENCY_LOG[p.id] || (EMERGENCY_LOG[p.id] = []);

  // A notificação BLE pode chegar duplicada (reconexão do bridge, retry
  // da pilha) — 'seq' incrementa no firmware a cada alerta novo, por
  // isso serve para deduplicar sem depender de tempo.
  if (seq != null && log.some(e => e.liveSeq === seq)) return;

  const meta = EMERGENCY_ALERT_TYPE_TO_LOG[msg.alert_name]
    || { type: 'unknown', label: t('emergencyType.unknown') };
  const ts = toFiniteNumber(msg.timestamp_utc);
  const timeLabel = ts
    ? new Date(ts * 1000).toLocaleString(currentLang, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
    : t('emergencyLive.nowLabel');

  log.unshift({
    id: `E-LIVE-${seq != null ? seq : Date.now()}`,
    type: meta.type,
    label: meta.label,
    time: timeLabel,
    status: 'ativo',
    liveSeq: seq,
    live: true,
    // "explicação de alerta" (2026-08-05) — mecanismo de deteção composto
    // pelo bridge (ver EMERGENCY_ALERT_EXPLANATIONS em ble_bridge.py), não
    // um valor medido (o EmergencyAlertPacket não traz waveform/amplitude,
    // só o tipo já decidido pelo firmware).
    explanation: typeof msg.explanation === 'string' ? msg.explanation : null,
  });

  updateLiveEmergencyBanner();
  if (currentView === 'emergencias') renderView('emergencias');
}

// Mostra/esconde a barra crítica de emergência em direto consoante haja
// ou não alertas 'ativo' de origem real (live: true) para o paciente
// selecionado. Chamada ao chegar um alerta novo e ao cancelar/resolver um
// existente (ver confirmEmergencyCancel()), para desaparecer assim que já
// não há nenhuma emergência em direto por resolver.
function updateLiveEmergencyBanner(){
  const el = document.getElementById('emergencyLiveBanner');
  if (!el) return;
  const active = currentEmergencyLog().filter(e => e.live && e.status === 'ativo');
  if (!active.length) { el.style.display = 'none'; return; }
  const label = emergencyLabelText(active[0]);
  el.style.display = 'flex';
  el.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.3 3.9 1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg>
    <span><b>${t('emergencyLive.bannerTitle')}</b> — ${label} (${selectedPatient().name}). <a href="#" onclick="renderView('emergencias'); return false;">${t('emergencyLive.viewLogLink')}</a></span>`;
}

// Estado do modal de cancelamento — o código gerado (6 dígitos) é
// guardado só em memória (nunca em localStorage), e nunca é enviado a
// lado nenhum: neste protótipo é mostrado na própria página, porque não
// existe (ainda) integração real com SMS/email (ver PROJECT_STATUS.md,
// decisão pendente do provedor). Serve para demonstrar o FLUXO de
// confirmação reforçada exigido, não uma verificação de posse de um
// segundo dispositivo real — isso só existiria com um provedor de SMS
// real a enviar o código para o telemóvel do responsável, fora do
// alcance desta sessão (precisa de credenciais do utilizador).
let emergencyCancelState = null;

// Constantes de segurança do código de confirmação — alinhadas com
// práticas reais de OTP por SMS (pesquisa 2026-07-03): TTL curto (aqui
// 5 min, valor comum na indústria — Twilio/Plivo) e limite de tentativas
// que efetivamente BLOQUEIA a ação (não é só uma mensagem de aviso; ver
// bug corrigido logo abaixo). Isto também segue o princípio de "break
// glass" de acesso de emergência em sistemas de saúde: exceção rara,
// nunca um bypass de rotina, e sempre com registo de quem/quando (ver
// 'resolvedNote' em confirmEmergencyCancel()).
const EMERGENCY_CODE_TTL_MS = 5 * 60 * 1000;
const EMERGENCY_MAX_ATTEMPTS = 3;

function openEmergencyCancelModal(emergencyId){
  const code = String(Math.floor(100000 + Math.random() * 900000));
  emergencyCancelState = { emergencyId, code, attempts: 0, expiresAt: Date.now() + EMERGENCY_CODE_TTL_MS };
  document.getElementById('emergencyCancelCodeDisplay').textContent = code;
  document.getElementById('emergencyCancelPassword').value = '';
  document.getElementById('emergencyCancelCodeInput').value = '';
  document.getElementById('emergencyCancelStatus').textContent = '';
  document.getElementById('emergencyCancelStatus').className = 'modal-status';
  document.getElementById('emergencyCancelOverlay').style.display = 'flex';
}
function closeEmergencyCancelModal(){
  document.getElementById('emergencyCancelOverlay').style.display = 'none';
  emergencyCancelState = null;
}

/* ------------------------------------------------------------
   TIMELINE CORRELACIONADA POR EPISÓDIO (2026-08-05)
   ------------------------------------------------------------
   Pede ao bridge (cmd "get_episode_timeline") os sinais vitais/blocos de
   atividade/outros alertas à volta de UM alerta de emergência real (só
   disponível para entradas com liveSeq — as de demonstração não têm
   sequence_number nenhum na base de dados do bridge para pesquisar).
------------------------------------------------------------- */
function openEpisodeTimelineModal(sequenceNumber){
  const body = document.getElementById('episodeTimelineBody');
  document.getElementById('episodeTimelineOverlay').style.display = 'flex';
  if (!bridgeWs || bridgeWs.readyState !== WebSocket.OPEN){
    body.innerHTML = `<p class="empty-hint">${t('episodio.noBridgeConnectionHint')}</p>`;
    return;
  }
  body.innerHTML = `<p class="empty-hint">${t('episodio.loadingHint')}</p>`;
  sendWsCommandWithArgs('get_episode_timeline', { sequence_number: sequenceNumber, window_minutes: 30 });
}

function closeEpisodeTimelineModal(){
  document.getElementById('episodeTimelineOverlay').style.display = 'none';
}

function handleEpisodeTimelineResult(msg){
  const body = document.getElementById('episodeTimelineBody');
  if (!body || document.getElementById('episodeTimelineOverlay').style.display === 'none') return;
  if (!msg.timeline){
    body.innerHTML = `<p class="empty-hint">${t('episodio.errorPrefix')} ${escapeHtml(msg.error || t('episodio.unknownError'))}.</p>`;
    return;
  }
  renderEpisodeTimeline(msg.timeline, body);
}

// Junta sensor_summary + activity_blocks + nearby_emergency_alerts numa
// única lista ordenada no tempo, cada item com um "tipo" e um rótulo —
// mais fácil de ler numa timeline única do que 3 secções separadas.
function renderEpisodeTimeline(timeline, body){
  const centerLabel = new Date(timeline.center_ts * 1000).toLocaleString(currentLang, {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
  const fmtTime = ts => new Date(ts * 1000).toLocaleTimeString(currentLang, { hour: '2-digit', minute: '2-digit' });

  const items = [];
  (timeline.sensor_summary || []).forEach(p => {
    const parts = [];
    if (p.hr != null) parts.push(`${p.hr} bpm`);
    if (p.spo2 != null) parts.push(`SpO₂ ${p.spo2}%`);
    if (parts.length) items.push({ ts: p.ts, kind: 'vital', label: parts.join(' · ') });
  });
  (timeline.activity_blocks || []).forEach(b => {
    items.push({
      ts: b.start_ts_approx, kind: 'activity',
      label: `${escapeHtml(b.category)} (${Math.round(b.duration_minutes || 0)} min)`,
    });
  });
  (timeline.nearby_emergency_alerts || []).forEach(a => {
    items.push({ ts: a.timestamp_utc, kind: 'alert', label: escapeHtml(a.alert_type) });
  });
  items.sort((a, b) => a.ts - b.ts);

  const badgeStyle = {
    vital: 'background:var(--bg-surface-2); color:var(--text-secondary);',
    activity: 'background:color-mix(in srgb, var(--accent) 18%, transparent); color:var(--accent);',
    alert: 'background:var(--status-critical-bg); color:var(--status-critical);',
  };
  const badgeLabel = { vital: t('episodio.badgeVital'), activity: t('episodio.badgeActivity'), alert: t('episodio.badgeAlert') };

  const listHtml = items.length ? items.map(it => `
    <div class="episode-timeline-row">
      <span class="et-time">${fmtTime(it.ts)}</span>
      <span class="et-badge" style="${badgeStyle[it.kind]}">${badgeLabel[it.kind]}</span>
      <span>${it.label}</span>
    </div>
  `).join('') : `<p class="empty-hint">${t('episodio.emptyHint')}</p>`;

  body.innerHTML = `
    <p class="empty-hint" style="margin:0 0 10px;">${t('episodio.centeredOnPrefix')} <b>${centerLabel}</b> (±${timeline.window_minutes} min)</p>
    <div class="episode-timeline-section">${listHtml}</div>
  `;
}
function confirmEmergencyCancel(){
  const status = document.getElementById('emergencyCancelStatus');
  if (!emergencyCancelState) return;

  // BUG CORRIGIDO (2026-07-03, aplicando pesquisa sobre rate-limiting de
  // OTP): ao atingir o limite de tentativas, a versão anterior só
  // ACRESCENTAVA uma frase ao aviso, mas continuava a aceitar tentativas
  // novas indefinidamente — o "bloqueio" era só visual. Agora bloqueia
  // mesmo (return antes de validar o código), e o código também expira
  // ao fim de 5 min mesmo sem esgotar as tentativas, tal como um OTP real.
  if (emergencyCancelState.attempts >= EMERGENCY_MAX_ATTEMPTS) {
    status.className = 'modal-status err';
    status.textContent = 'Demasiadas tentativas incorretas — fecha e reabre para gerar um novo código.';
    return;
  }
  if (Date.now() > emergencyCancelState.expiresAt) {
    status.className = 'modal-status err';
    status.textContent = 'Código expirado (validade de 5 minutos) — fecha e reabre para gerar um novo.';
    return;
  }

  const password = document.getElementById('emergencyCancelPassword').value;
  const codeInput = document.getElementById('emergencyCancelCodeInput').value.trim();

  if (!password) {
    status.className = 'modal-status err';
    status.textContent = 'Introduz a tua palavra-passe para confirmar a tua identidade.';
    return;
  }
  if (codeInput !== emergencyCancelState.code) {
    emergencyCancelState.attempts++;
    const remaining = EMERGENCY_MAX_ATTEMPTS - emergencyCancelState.attempts;
    status.className = 'modal-status err';
    status.textContent = remaining > 0
      ? `Código incorreto (${remaining} tentativa${remaining>1?'s':''} restante${remaining>1?'s':''}). Confirma o código mostrado acima.`
      : 'Código incorreto. Sem mais tentativas — fecha e reabre para gerar um novo código.';
    return;
  }

  const entry = currentEmergencyLog().find(e => e.id === emergencyCancelState.emergencyId);
  if (entry) {
    entry.status = 'cancelado';
    entry.resolvedNote = `Cancelado manualmente por ${document.getElementById('avatarName').textContent} em ${new Date().toLocaleString('pt-PT')}, após confirmação reforçada.`;
  }
  status.className = 'modal-status ok';
  status.textContent = 'Alerta cancelado e registado.';
  updateLiveEmergencyBanner();
  setTimeout(() => { closeEmergencyCancelModal(); if (currentView) renderView(currentView); }, 1200);
}

/* ------------------------------------------------------------
   LEMBRETES DE MEDICAÇÃO (item 9 do backlog de investigação)
   ------------------------------------------------------------
   `patient.medications` e `patient.adherenceHistory` (ver PATIENTS acima)
   são dados de exemplo por paciente (nome, dose, horários; adesão dos
   últimos dias). O que é real neste protótipo é o registo de toma de
   HOJE: cada clique em "Marcar como tomado" fica em localStorage
   (namespaced por paciente + dia + medicamento + horário), sobrevive a
   recarregar a página. Histórico anterior a hoje só existirá a sério
   depois do serviço de persistência (Prioridade 4, ver PROJECT_STATUS.md).
   "Correlacionado com atividade/vitais" (pedido no backlog): mostrado
   como uma nota simples que aponta os dias com adesão incompleta para
   serem comparados manualmente com a vista "Tendência semanal" — uma
   correspondência de datas, não uma análise estatística automática (não
   fabricamos uma correlação numérica sem dados reais para a sustentar).
------------------------------------------------------------ */
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
// Devolve o MAC "reconhecido" para este paciente: o que foi registado por
// uma ligação real anterior, se existir, senão o mac de demonstração fixo
// do próprio PATIENTS[].
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

