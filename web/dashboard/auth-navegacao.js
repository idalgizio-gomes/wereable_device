// Navegação entre ecrãs (login vs app; utente vs médico/técnico)
let currentRole = 'utente';
let privilegedAccessExpiresAt = null; //ISO string de /api/auth/me, só para admin_clinical
let privilegedAccessReason = null; //motivo capturado uma vez por sessão, enviado em todo pedido clínico (ver withPrivilegedReason em api-client.js)

function setLoginRole(role){
  currentRole = role;
  document.getElementById('roleBtnUtente').setAttribute('aria-pressed', role==='utente');
  document.getElementById('roleBtnClinico').setAttribute('aria-pressed', role==='clinico');
  document.getElementById('roleBtnAdmin').setAttribute('aria-pressed', role==='admin');
  document.getElementById('roleBtnAdminClinical').setAttribute('aria-pressed', role==='admin_clinical');
  const labelKey = role==='utente' ? 'login.role.utente' : role==='admin' ? 'login.role.admin'
    : role==='admin_clinical' ? 'login.role.adminClinical' : 'login.role.clinico';
  document.getElementById('loginRoleLabel').textContent = t(labelKey);

  // Email pré-preenchido muda consoante o perfil (ver ADMIN_EMAIL/DEFAULT_CLINICIAN_EMAIL/ADMIN_CLINICAL_EMAIL)
  const emailEl = document.getElementById('loginEmail');
  if (emailEl) {
    emailEl.value = role==='utente' ? 'maria.silva@exemplo.pt'
      : role==='admin' ? ADMIN_EMAIL
      : role==='admin_clinical' ? ADMIN_CLINICAL_EMAIL
      : DEFAULT_CLINICIAN_EMAIL;
  }
}

// Inscrição de novos utilizadores — protótipo, sem backend real
let signupRole = 'utente';

function showSignup(){
  document.getElementById('loginPanel').style.display = 'none';
  document.getElementById('signupPanel').style.display = 'block';
}
function showLogin(){
  document.getElementById('signupPanel').style.display = 'none';
  document.getElementById('loginPanel').style.display = 'block';
}

function setSignupRole(role){
  signupRole = role;
  document.getElementById('signupRoleBtnUtente').setAttribute('aria-pressed', role==='utente');
  document.getElementById('signupRoleBtnClinico').setAttribute('aria-pressed', role==='clinico');
  document.getElementById('signupFieldsUtente').style.display = role==='utente' ? 'block' : 'none';
  document.getElementById('signupFieldsClinico').style.display = role==='clinico' ? 'block' : 'none';
}

function submitSignup(){
  const name = document.getElementById('signupName').value.trim();
  const email = document.getElementById('signupEmail').value.trim();
  const pass = document.getElementById('signupPass').value;

  if (!name || !email || !pass){
    alert(t('signup.errMissingFields'));
    return;
  }
  if (signupRole === 'utente'){
    const patient = document.getElementById('signupPatient').value.trim();
    const patientAge = document.getElementById('signupPatientAge').value;
    if (!patient){
      alert(t('signup.errMissingPatientName'));
      return;
    }
    if (!patientAge || parseInt(patientAge, 10) <= 0){
      alert(t('signup.errMissingPatientAge'));
      return;
    }
    // Único sítio da app que cria um paciente do zero; área clínica só associa a um existente
    const record = registerOwnPatient(patient, patientAge, email);
    if (!record){
      alert(t('signup.errPatientRegisterFailed'));
      return;
    }
  } else {
    const institution = document.getElementById('signupInstitution').value.trim();
    const license = document.getElementById('signupLicense').value.trim();
    if (!license){
      alert(t('signup.errMissingLicense'));
      return;
    }
    // registerClinicianAccount() definida em admin-view.js
    registerClinicianAccount(email, name, institution, license);
  }

  // Só o paciente (perfil Utente/Família) fica de facto registado; a conta em si continua sem backend
  const roleLabel = t(signupRole === 'utente' ? 'login.role.utente' : 'login.role.clinico');
  const suffix = t(signupRole === 'utente' ? 'signup.demoCreatedUtenteSuffix' : 'signup.demoCreatedClinicoSuffix');
  alert(`${t('signup.demoCreatedPrefix', {name, email, role: roleLabel})}\n\n${suffix}`);
  setLoginRole(signupRole);
  document.getElementById('loginEmail').value = email;
  showLogin();
}

// Perfil do próprio utilizador (nome/contacto), separado dos dados clínicos do paciente — persistido por perfil em localStorage
const PROFILE_KEY = 'carewear_profile';

// Campos sensíveis (NIF, morada) ficam pendentes de aprovação clínica em vez de aplicados de imediato; só se aplica ao perfil Utente/Família
const SENSITIVE_PROFILE_FIELDS = { utente: ['nif', 'address'], clinico: [] };

function loadProfile(){
  const defaults = {
    utente: {
      name:'Maria Silva', email:'maria.silva@exemplo.pt', phone:'912 345 678',
      nif:'123 456 789', address:'Rua das Flores, 12, 4750-000 Barcelos',
      caregiverName:'João Silva', caregiverPhone:'913 000 111', caregiverRelation:'Filho',
    },
    clinico: {
      name:'Dr. Ricardo Alves', email:'ricardo.alves@exemplo.pt', phone:'914 222 333',
      nif:'234 567 890', institution:'Centro de Saúde de Barcelos', license:'OM-12345',
    },
    // Perfil próprio do Administrador — ver applyProfileToAvatar()
    admin: {
      name:'Dra. Ana Correia', email: ADMIN_EMAIL, phone:'915 333 444',
      nif:'', institution:'CareWear — Administração', license:'',
    },
  };
  try {
    const raw = localStorage.getItem(PROFILE_KEY);
    if (raw) {
      const saved = JSON.parse(raw);
      return {
        utente: {...defaults.utente, ...saved.utente},
        clinico: {...defaults.clinico, ...saved.clinico},
        admin: {...defaults.admin, ...saved.admin},
      };
    }
  } catch (e) { /* dados corrompidos - usa omissão */ }
  return defaults;
}
function saveProfileField(role, field, value){
  const p = loadProfile();
  p[role][field] = value;
  try { localStorage.setItem(PROFILE_KEY, JSON.stringify(p)); }
  catch (e) { /* quota excedida - fica só em memória */ }
}

// Alteração a um campo sensível fica pendente até a equipa clínica aprovar/rejeitar na vista "Perfil"
const PENDING_PROFILE_KEY = 'carewear_profile_pending';

function loadPendingProfileChanges(){
  try {
    const raw = localStorage.getItem(PENDING_PROFILE_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* dados corrompidos - ignora */ }
  return {};
}
function savePendingProfileChanges(map){
  try { localStorage.setItem(PENDING_PROFILE_KEY, JSON.stringify(map)); }
  catch (e) { /* quota excedida - fica só em memória */ }
}
function requestProfileFieldChange(role, field, newValue){
  const pending = loadPendingProfileChanges();
  pending[role] = pending[role] || {};
  pending[role][field] = { newValue, requestedAt: Date.now() };
  savePendingProfileChanges(pending);
}
function approveProfileFieldChange(role, field){
  const pending = loadPendingProfileChanges();
  if (pending[role] && pending[role][field]) {
    saveProfileField(role, field, pending[role][field].newValue);
    delete pending[role][field];
    savePendingProfileChanges(pending);
  }
  if (currentView) renderView(currentView);
}
function rejectProfileFieldChange(role, field){
  const pending = loadPendingProfileChanges();
  if (pending[role]) delete pending[role][field];
  savePendingProfileChanges(pending);
  if (currentView) renderView(currentView);
}

function submitProfileForm(){
  const isUtente = currentRole === 'utente';
  const role = isUtente ? 'utente' : 'clinico';
  const current = loadProfile()[role];
  const sensitive = SENSITIVE_PROFILE_FIELDS[role] || [];
  let pendingCount = 0;

  // Campos diretos: sempre aplicados de imediato, sem aprovação
  const directFields = isUtente
    ? ['name', 'email', 'phone', 'caregiverName', 'caregiverPhone', 'caregiverRelation']
    : ['name', 'email', 'phone', 'institution', 'license'];
  directFields.forEach(field => {
    const el = document.getElementById('profile_' + field);
    if (el) saveProfileField(role, field, el.value.trim());
  });

  // Campos sensíveis só ficam pendentes se o valor realmente mudou
  sensitive.forEach(field => {
    const el = document.getElementById('profile_' + field);
    if (!el) return;
    const newValue = el.value.trim();
    if (newValue && newValue !== current[field]) {
      requestProfileFieldChange(role, field, newValue);
      pendingCount++;
    }
  });

  applyProfileToAvatar();
  const status = document.getElementById('profileSaveStatus');
  if (status) {
    status.className = 'modal-status ok';
    status.textContent = pendingCount > 0
      ? `Restantes alterações guardadas. ${pendingCount} campo${pendingCount>1?'s':''} sensível${pendingCount>1?'eis':''} ficou${pendingCount>1?'ram':''} pendente${pendingCount>1?'s':''} de aprovação pela equipa clínica.`
      : 'Alterações guardadas.';
  }
  if (currentView) renderView(currentView);
}

// Reflete o perfil guardado no cartão de avatar da topbar
function applyProfileToAvatar(){
  const isUtente = currentRole === 'utente';
  const isAdmin = currentRole === 'admin' || currentRole === 'admin_clinical'; //admin_clinical reutiliza o cartão de perfil do admin, com rótulo próprio
  const profileKey = isUtente ? 'utente' : isAdmin ? 'admin' : 'clinico';
  const p = loadProfile()[profileKey];
  const initials = p.name.split(' ').filter(Boolean).slice(0,2).map(w => w[0].toUpperCase()).join('') || '—';
  document.getElementById('avatarInitials').textContent = initials;
  document.getElementById('avatarName').textContent = p.name;
  const roleLabelKey = isUtente ? 'login.role.utente' : currentRole === 'admin_clinical' ? 'login.role.adminClinical' : isAdmin ? 'login.role.admin' : 'login.role.clinico';
  document.getElementById('avatarRole').textContent = 'Perfil: ' + t(roleLabelKey);
}

const API_ROLE_TO_DASHBOARD_ROLE = { family: 'utente', clinician: 'clinico', admin: 'admin', admin_clinical: 'admin_clinical' };

const MIN_PRIVILEGED_REASON_LENGTH = 8; //espelha MIN_ACCESS_REASON_LENGTH em bridge/api.py

function confirmPrivilegedReason(){
  const input = document.getElementById('privilegedReasonInput');
  const value = input ? input.value.trim() : '';
  if (value.length < MIN_PRIVILEGED_REASON_LENGTH) return;
  privilegedAccessReason = value;
  renderPrivilegedAccessBanner();
  if (currentView) renderView(currentView); //releitura dos dados clínicos agora com ?reason= incluído
}

function renderPrivilegedAccessBanner(){
  const el = document.getElementById('privilegedAccessBanner');
  if (!el) return;
  if (currentRole !== 'admin_clinical'){ el.style.display = 'none'; return; }

  if (!privilegedAccessReason){
    el.style.display = 'block';
    el.innerHTML = `
      <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
        <span>${t('adminClinical.reasonPrompt')}</span>
        <input id="privilegedReasonInput" type="text" minlength="${MIN_PRIVILEGED_REASON_LENGTH}" placeholder="${t('adminClinical.reasonPlaceholder')}" style="flex:1; min-width:220px;">
        <button class="btn-secondary" onclick="confirmPrivilegedReason()">${t('adminClinical.reasonConfirm')}</button>
      </div>`;
    return;
  }

  if (!privilegedAccessExpiresAt){
    el.style.display = 'block';
    el.textContent = t('adminClinical.noGrant');
    return;
  }
  const remainingMs = new Date(privilegedAccessExpiresAt).getTime() - Date.now();
  if (remainingMs <= 0){
    el.style.display = 'block';
    el.textContent = t('adminClinical.grantExpired');
    return;
  }
  const h = Math.floor(remainingMs / 3600000);
  const m = Math.floor((remainingMs % 3600000) / 60000);
  el.style.display = 'block';
  el.textContent = t('adminClinical.grantExpiresIn', { h, m });
}

async function login(){
  const emailEl = document.getElementById('loginEmail');
  const passEl = document.getElementById('loginPass');
  const errEl = document.getElementById('loginError');
  const email = emailEl ? emailEl.value : '';
  const pass = passEl ? passEl.value : '';

  const result = await apiLogin(email, pass);
  if (!result.ok){
    if (errEl){
      errEl.textContent = result.error === 'credenciais'
        ? 'Email ou palavra-passe incorretos.'
        : 'Não foi possível ligar à API (bridge/api.py) — confirma que está a correr em localhost:8766.';
      errEl.style.display = 'block';
    }
    return;
  }
  if (errEl) errEl.style.display = 'none';
  currentRole = API_ROLE_TO_DASHBOARD_ROLE[result.user.role] || 'utente';
  // Densidade visual: perfis profissionais (leem vários pacientes em sequência) ficam mais compactos
  document.documentElement.style.setProperty('--density-scale', (currentRole === 'clinico' || currentRole === 'admin_clinical') ? '0.78' : '1');
  privilegedAccessExpiresAt = result.user.privileged_access_expires_at || null;
  privilegedAccessReason = null; //pedido de novo a cada login, nunca reaproveitado de uma sessão anterior

  document.getElementById('view-login').style.display = 'none';
  document.getElementById('view-app').classList.add('active');

  currentUserEmail = result.user.email;

  // Admin nunca herda navClinico nem acesso ao dossiê clínico completo — só navega entre páginas
  selectedPatientId = loadSelectedPatientId();

  const isUtente = currentRole === 'utente';
  const isAdmin = currentRole === 'admin';
  const isAdminClinical = currentRole === 'admin_clinical';
  const isClinico = currentRole === 'clinico';
  document.getElementById('navUtente').style.display = isUtente ? 'flex' : 'none';
  document.getElementById('navClinico').style.display = (isClinico || isAdminClinical) ? 'flex' : 'none';
  document.getElementById('navAdmin').style.display = isAdmin ? 'flex' : 'none';

  // Ligação/bateria/armazenamento na topbar só para Utente/Família; clínico/admin veem em "Dispositivo & firmware"
  const deviceGroup = document.getElementById('topbarDeviceStatusGroup');
  if (deviceGroup) deviceGroup.style.display = isUtente ? 'flex' : 'none';
  renderStorageWarningBanner();
  renderPrivilegedAccessBanner();

  updateClinicoPatientLabel();
  const pill = document.getElementById('sidebarRolePill');
  const pillLabelKey = isUtente ? 'login.role.utente' : isAdminClinical ? 'login.role.adminClinical' : isAdmin ? 'login.role.admin' : 'login.role.clinico';
  pill.textContent = t(pillLabelKey);
  pill.className = 'sidebar-role-pill ' + (isUtente ? 'utente' : isAdminClinical ? 'admin-clinical' : isAdmin ? 'admin' : 'clinico');

  applyProfileToAvatar();

  const defaultView = isUtente ? 'resumo' : isAdmin ? 'admin' : 'pacientes'; //clinico e admin_clinical caem em 'pacientes'

  // Fragmento (#rotina) só pode ser honrado depois da autenticação; inválido ou fora do perfil cai na vista por omissão
  let vistaInicial = defaultView;
  if (vistaPedidaNoArranque && viewPermitidaParaPerfil(vistaPedidaNoArranque, currentRole)) {
    vistaInicial = vistaPedidaNoArranque;
  }
  vistaPedidaNoArranque = null;

  sincronizarMenu(vistaInicial);

  // replaceState em vez de pushState: "retroceder" logo a seguir a entrar não deve deixar um ecrã vazio
  history.replaceState({ view: vistaInicial }, '', '#' + vistaInicial);
  renderView(vistaInicial, false);
  updateNotificationBadge();
}

async function logout(){
  await apiLogout();
  document.getElementById('view-app').classList.remove('active');
  document.getElementById('view-login').style.display = 'grid';
  currentUserEmail = '';
  selectedPatientId = null;
  // Sem isto, reentrar na mesma vista por omissão (ex: "resumo") não dispararia deteção de mudança em renderView()
  currentView = null;
  history.replaceState(null, '', '#');
}

// Ativa visualmente um nav-item e mostra a vista correspondente
function activateNavItem(item){
  if (!item) return;
  const view = item.dataset.view;
  if (!view) return;
  const group = item.closest('.nav-group');
  if (group) group.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
  item.classList.add('active');
  renderView(view);
}

// Marca o botão do menu correspondente a uma vista, sem passar por um clique (renderView() não mexe em .active)
// Restrito ao grupo do perfil atual: botões dos outros perfis continuam no DOM escondidos por display:none
function sincronizarMenu(view){
  document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
  const grupo = currentRole === 'utente' ? 'navUtente'
              : currentRole === 'admin'  ? 'navAdmin'
              : 'navClinico';
  const btn = document.querySelector(`#${grupo} .nav-item[data-view="${view}"]`);
  if (btn) btn.classList.add('active');
  // Vistas "perfil" e "ajuda" não pertencem a nenhum grupo: nenhum botão fica marcado, é o comportamento correto
}

// Sino de alertas na topbar — decide a vista pelo perfil atual (currentRole), não por seletor CSS genérico
function onNotificationBellClick(){
  if (currentRole === 'utente') {
    // Sem vista "Anomalias" dedicada: garante Resumo ativo e faz scroll até ao cartão de alertas
    const navItem = document.querySelector('#navUtente .nav-item[data-view="resumo"]');
    activateNavItem(navItem);
    requestAnimationFrame(() => {
      const alertsCard = document.querySelector('#view-app .card .alert-row');
      if (alertsCard) alertsCard.closest('.card').scrollIntoView({behavior:'smooth', block:'center'});
    });
  } else {
    const navItem = document.querySelector('#navClinico .nav-item[data-view="anomalias"]');
    activateNavItem(navItem);
  }
}

document.addEventListener('click', (e) => {
  const item = e.target.closest('.nav-item');
  if (!item) return;
  if (!item.dataset.view) return; // botões nav-item sem view associada (ex: "Ajuda") tratam o próprio onclick
  activateNavItem(item);
});

// Chaves i18n por vista, traduzidas via t() para o título atualizar ao mudar de idioma
const VIEW_TITLE_KEYS = {
  resumo:'nav.summary', rotina:'nav.routine', vitais:'nav.vitals', tendencia:'nav.trend', definicoes:'nav.settings',
  pacientes:'nav.patients', dispositivo:'nav.device', anomalias:'nav.anomalies', limites:'nav.limits', exportar:'nav.export',
  ajuda:'nav.help', emergencias:'nav.emergencies', perfil:'nav.profile', medicacao:'nav.medication',
  alertas:'nav.alertHistory', admin:'nav.admin', timeline:'nav.timeline',
};

// Guarda a vista ativa, usada para re-renderizar "onde o utilizador estiver" (ex: silenciar um alerta)
let currentView = null;

// registarNoHistorico distingue navegação do utilizador de re-desenho interno (evita ciclo com popstate)
function renderView(view, registarNoHistorico = true){
  const mudouDeVista = view !== currentView;
  currentView = view;
  document.getElementById('topbarTitle').textContent = VIEW_TITLE_KEYS[view] ? t(VIEW_TITLE_KEYS[view]) : 'CareWear';
  const c = document.getElementById('content');
  c.innerHTML = TEMPLATES[view] ? TEMPLATES[view]() : '<div class="empty-hint">Vista em construção.</div>';
  requestAnimationFrame(() => AFTER_RENDER[view] && AFTER_RENDER[view]());

  if (registarNoHistorico && mudouDeVista) {
    // Fragmento (#vista) em vez de caminho: pushState com caminho lança SecurityError em file://
    history.pushState({ view }, '', '#' + view);
  }
}

// AVISO: isto não é controlo de acesso, só UI — renderView('admin') na consola ignora isto (código corre no cliente).
// Acesso real: REST via _authorize_patient() em bridge/api.py; WebSocket via WS_COMMAND_ROLES + sessão
// resolvida no handshake (ws_transport.py::process_request — sessão válida obrigatória por omissão).
// admin_clinical partilha as vistas do clinico — a fronteira real (leitura/motivo/expiração) é imposta no backend.
const VIEW_ROLES = {
  resumo:      ['utente'],
  rotina:      ['utente'],
  vitais:      ['utente'],
  tendencia:   ['utente'],
  definicoes:  ['utente'],
  pacientes:   ['clinico', 'admin_clinical'],
  dispositivo: ['clinico', 'admin_clinical'],
  anomalias:   ['clinico', 'admin_clinical'],
  limites:     ['clinico', 'admin_clinical'],
  exportar:    ['clinico', 'admin_clinical'],
  alertas:     ['utente', 'clinico', 'admin_clinical'],
  timeline:    ['utente', 'clinico', 'admin_clinical'],
  emergencias: ['utente', 'clinico', 'admin_clinical'],
  medicacao:   ['utente', 'clinico', 'admin_clinical'],
  admin:       ['admin'],
  perfil:      ['utente', 'clinico', 'admin', 'admin_clinical'],
  ajuda:       ['utente', 'clinico', 'admin', 'admin_clinical'],
};

function viewPermitidaParaPerfil(view, role){
  const perfis = VIEW_ROLES[view];
  return Array.isArray(perfis) && perfis.includes(role);
}

function vistaPorOmissao(role){
  return role === 'utente' ? 'resumo' : role === 'admin' ? 'admin' : 'pacientes'; //clinico e admin_clinical caem ambos em 'pacientes'
}

// Lido uma única vez no arranque; só consumido em login() porque o fragmento pode chegar antes de haver perfil
let vistaPedidaNoArranque = (location.hash || '').replace(/^#/, '') || null;

window.addEventListener('popstate', (e) => {
  // Sem sessão ativa não desenha nada — evita expor dados clínicos via "retroceder" num computador partilhado
  if (!document.getElementById('view-app').classList.contains('active')) return;

  const view = (e.state && e.state.view) || (location.hash || '').replace(/^#/, '');
  if (!view) return;

  if (!viewPermitidaParaPerfil(view, currentRole)) {
    const fallback = vistaPorOmissao(currentRole);
    history.replaceState({ view: fallback }, '', '#' + fallback);
    sincronizarMenu(fallback);
    renderView(fallback, false);
    return;
  }

  sincronizarMenu(view);
  renderView(view, false);
});

