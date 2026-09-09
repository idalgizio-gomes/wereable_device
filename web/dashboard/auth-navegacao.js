/* ============================================================
   NAVEGAÇÃO ENTRE ECRÃS (login vs app; utente vs médico/técnico)
============================================================ */
let currentRole = 'utente';

function setLoginRole(role){
  currentRole = role;
  document.getElementById('roleBtnUtente').setAttribute('aria-pressed', role==='utente');
  document.getElementById('roleBtnClinico').setAttribute('aria-pressed', role==='clinico');
  document.getElementById('roleBtnAdmin').setAttribute('aria-pressed', role==='admin');
  const labelKey = role==='utente' ? 'login.role.utente' : role==='admin' ? 'login.role.admin' : 'login.role.clinico';
  document.getElementById('loginRoleLabel').textContent = t(labelKey);

  // Troca o email pré-preenchido para o da conta de demonstração certa
  // (2026-08-06, ver ADMIN_EMAIL/DEFAULT_CLINICIAN_EMAIL em
  // pacientes-alertas-medicacao.js) — antes ficava sempre com o mesmo
  // email fixo, o que confundia qual conta se estava mesmo a usar ao
  // trocar de perfil no ecrã de login.
  const emailEl = document.getElementById('loginEmail');
  if (emailEl) {
    emailEl.value = role==='utente' ? 'maria.silva@exemplo.pt'
      : role==='admin' ? ADMIN_EMAIL
      : DEFAULT_CLINICIAN_EMAIL;
  }
}

/* ------------------------------------------------------------
   INSCRIÇÃO DE NOVOS UTILIZADORES (protótipo, sem backend)
   ------------------------------------------------------------
   Alterna entre o painel de login e o de registo, e ajusta os campos
   pedidos consoante o perfil escolhido (Utente/Família vs Médico/
   Técnico). O "Criar conta" não persiste nada — apenas valida os campos
   obrigatórios e devolve ao login com o email já preenchido, como
   demonstração do fluxo até existir um backend real (ver roadmap: BD SQL).
------------------------------------------------------------ */
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
    alert('Preenche nome, email e palavra-passe para continuar.');
    return;
  }
  if (signupRole === 'utente'){
    const patient = document.getElementById('signupPatient').value.trim();
    const patientAge = document.getElementById('signupPatientAge').value;
    if (!patient){
      alert('Indica o nome do utente monitorizado.');
      return;
    }
    if (!patientAge || parseInt(patientAge, 10) <= 0){
      alert('Indica a idade do utente monitorizado.');
      return;
    }
    // BUG DE MODELO CORRIGIDO (2026-07-16, reportado pelo utilizador):
    // "Criar conta" como Utente/Família deixou de ser só uma simulação
    // que não guardava nada — regista mesmo um paciente novo, ligado a
    // este email (ver registerOwnPatient()). É o único sítio da app que
    // pode criar um paciente do zero; a área clínica só pode associar-se
    // a um que já exista (ver "Atribuir-me este paciente" em Pacientes).
    const record = registerOwnPatient(patient, patientAge, email);
    if (!record){
      alert('Não foi possível registar o utente monitorizado — confirma o nome e a idade.');
      return;
    }
  } else {
    const institution = document.getElementById('signupInstitution').value.trim();
    const license = document.getElementById('signupLicense').value.trim();
    if (!license){
      alert('Indica o número de cédula profissional.');
      return;
    }
    // NOVO (2026-08-06, vista de Administrador): este branch validava a
    // cédula mas não persistia nada sobre o médico/técnico — o admin não
    // tinha como saber que contas clínicas existiam. registerClinicianAccount()
    // está definida em admin-view.js (carregado depois deste <script>, mas
    // isso não importa aqui: só corre quando o botão "Criar conta" é
    // clicado, muito depois de todos os scripts já terem carregado).
    registerClinicianAccount(email, name, institution, license);
  }

  // Protótipo: a CONTA em si (nome/email/password de quem está a criar
  // o login) continua sem backend real — só o paciente monitorizado
  // (perfil Utente/Família) passa a ficar de facto registado, porque é
  // o que a área clínica precisa de poder consultar depois.
  alert(`Conta criada (demonstração): ${name} <${email}> como ${signupRole === 'utente' ? 'Utente/Família' : 'Médico/Técnico'}.\n\n${signupRole === 'utente' ? 'O utente monitorizado ficou registado e já pode ser consultado pela equipa clínica.' : 'A conta em si continua a ser só uma simulação — falta backend real para autenticação.'}`);
  setLoginRole(signupRole);
  document.getElementById('loginEmail').value = email;
  showLogin();
}

/* ------------------------------------------------------------
   PERFIL DO UTILIZADOR — editável a qualquer momento
   ------------------------------------------------------------
   Pedido do utilizador (2026-07-03): uma aba onde a pessoa a usar a
   conta (utente/família OU médico/técnico) possa atualizar os SEUS
   PRÓPRIOS dados (nome, contacto), não os dados clínicos do paciente
   monitorizado (isso continua em "Pacientes"/"Definições"). Persistido
   em localStorage, por perfil (utente vs clínico guardam-se em separado,
   já que a mesma conta pode ter sido usada nos dois papéis em sessões
   diferentes deste protótipo).
------------------------------------------------------------ */
const PROFILE_KEY = 'carewear_profile';

// Campos "sensíveis" (2026-07-03, pedido do utilizador): alguns dados não
// devem poder ser alterados com facilidade — em particular a morada, mas
// também o NIF (identificador fiscal), porque erros/fraude nestes campos
// têm consequências fora do próprio perfil (correspondência, faturação).
// Alterar um destes campos não aplica de imediato — fica pendente até um
// membro da equipa clínica aprovar (ver requestProfileFieldChange()/
// approveProfileFieldChange() abaixo). Só se aplica ao perfil Utente/
// Família — o perfil Médico/Técnico não representa um paciente, por isso
// os seus dados não têm o mesmo risco associado.
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
    // Perfil próprio do Administrador (2026-08-06, pedido explícito da
    // utilizadora: "a administradora vai ser a doutora Ana Correia" —
    // antes não existia nenhum perfil separado para este papel, e o
    // avatar da topbar mostrava sempre o nome do Dr. Ricardo (o perfil
    // 'clinico' acima), mesmo quando quem tinha entrado era o admin —
    // ver applyProfileToAvatar() mais abaixo, que agora usa esta chave.
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
  } catch (e) { /* localStorage indisponível ou dados corrompidos - usa omissão */ }
  return defaults;
}
function saveProfileField(role, field, value){
  const p = loadProfile();
  p[role][field] = value;
  try { localStorage.setItem(PROFILE_KEY, JSON.stringify(p)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
}

/* ------------------------------------------------------------
   APROVAÇÃO DE ALTERAÇÕES A DADOS SENSÍVEIS DO PERFIL
   ------------------------------------------------------------
   Uma alteração a um campo sensível (ver SENSITIVE_PROFILE_FIELDS) fica
   "pendente" em vez de aplicada de imediato — só a equipa clínica
   (perfil Médico/Técnico) pode aprovar ou rejeitar, na própria vista
   "Perfil". Protótipo: sem notificação real a ninguém, só o estado
   guardado em localStorage.
------------------------------------------------------------ */
const PENDING_PROFILE_KEY = 'carewear_profile_pending';

function loadPendingProfileChanges(){
  try {
    const raw = localStorage.getItem(PENDING_PROFILE_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* localStorage indisponível ou dados corrompidos - ignora */ }
  return {};
}
function savePendingProfileChanges(map){
  try { localStorage.setItem(PENDING_PROFILE_KEY, JSON.stringify(map)); }
  catch (e) { /* quota excedida ou localStorage indisponível - fica só em memória */ }
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

  // Campos diretos (sem aprovação): sempre aplicados de imediato.
  const directFields = isUtente
    ? ['name', 'email', 'phone', 'caregiverName', 'caregiverPhone', 'caregiverRelation']
    : ['name', 'email', 'phone', 'institution', 'license'];
  directFields.forEach(field => {
    const el = document.getElementById('profile_' + field);
    if (el) saveProfileField(role, field, el.value.trim());
  });

  // Campos sensíveis: só ficam pendentes se o valor realmente mudou.
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

// Reflete o perfil guardado no cartão de avatar da topbar — chamado no
// login e sempre que o perfil é atualizado, para não ficar desatualizado
// enquanto se navega pelo resto da app.
function applyProfileToAvatar(){
  // Bug real corrigido aqui (2026-08-06, reportado pela utilizadora): o
  // admin caía sempre no perfil 'clinico' (Dr. Ricardo), porque só
  // existiam 2 chaves. Agora loadProfile() tem uma chave 'admin' própria
  // (Dra. Ana Correia) — ver comentário completo lá.
  const isUtente = currentRole === 'utente';
  const isAdmin = currentRole === 'admin';
  const profileKey = isUtente ? 'utente' : isAdmin ? 'admin' : 'clinico';
  const p = loadProfile()[profileKey];
  const initials = p.name.split(' ').filter(Boolean).slice(0,2).map(w => w[0].toUpperCase()).join('') || '—';
  document.getElementById('avatarInitials').textContent = initials;
  document.getElementById('avatarName').textContent = p.name;
  document.getElementById('avatarRole').textContent = 'Perfil: ' + t(isUtente ? 'login.role.utente' : isAdmin ? 'login.role.admin' : 'login.role.clinico');
}

const API_ROLE_TO_DASHBOARD_ROLE = { family: 'utente', clinician: 'clinico', admin: 'admin' };

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

  document.getElementById('view-login').style.display = 'none';
  document.getElementById('view-app').classList.add('active');

  currentUserEmail = result.user.email;

  // CORREÇÃO (2026-08-06, pedido explícito da utilizadora): a decisão
  // anterior desta mesma sessão — "admin" entrar por baixo do botão
  // "Médico/Técnico" e herdar as vistas clínicas completas — foi
  // revertida por dois motivos que a utilizadora identificou: (1) não
  // ter um botão de login próprio tornava o perfil confuso/escondido;
  // (2) dar ao admin acesso aos dados clínicos completos de qualquer
  // paciente/médico é uma invasão de privacidade — o admin deve poder
  // NAVEGAR entre as páginas (ver quem são os médicos, os pacientes, as
  // atribuições), nunca abrir o dossiê clínico de ninguém. Por isso:
  // currentRole já vem diretamente do botão de login explícito
  // (setLoginRole('admin')), sem promoção silenciosa via isAdminUser();
  // e navClinico deixa de ser concedido ao admin (só isUtente=false E
  // isAdmin=false, ou seja só 'clinico' puro, continua a vê-lo).
  selectedPatientId = loadSelectedPatientId();

  const isUtente = currentRole === 'utente';
  const isAdmin = currentRole === 'admin';
  const isClinico = currentRole === 'clinico';
  document.getElementById('navUtente').style.display = isUtente ? 'flex' : 'none';
  document.getElementById('navClinico').style.display = isClinico ? 'flex' : 'none';
  document.getElementById('navAdmin').style.display = isAdmin ? 'flex' : 'none';

  // CORREÇÃO (2026-08-06, pedido explícito da utilizadora, ver captura
  // de ecrã anexa): ligação/bateria/armazenamento do wearable só fazem
  // sentido em destaque na topbar para Utente/Família — para Médico/
  // Técnico e Administrador fica escondido aqui, disponível só na
  // página "Dispositivo & firmware" (ver comentário completo junto de
  // #topbarDeviceStatusGroup em index.html). renderStorageWarningBanner()
  // (bridge-exportacao.js) tem a mesma verificação de currentRole no
  // topo, para o banner de armazenamento cheio não reaparecer sozinho
  // na próxima mensagem 'status' do bridge.
  const deviceGroup = document.getElementById('topbarDeviceStatusGroup');
  if (deviceGroup) deviceGroup.style.display = isUtente ? 'flex' : 'none';
  renderStorageWarningBanner();

  updateClinicoPatientLabel();
  const pill = document.getElementById('sidebarRolePill');
  pill.textContent = t(isUtente ? 'login.role.utente' : isAdmin ? 'login.role.admin' : 'login.role.clinico');
  pill.className = 'sidebar-role-pill ' + (isUtente ? 'utente' : isAdmin ? 'admin' : 'clinico');

  applyProfileToAvatar();

  // BUG CORRIGIDO (2026-07-03, reportado pelo utilizador): trocar de
  // perfil (Utente/Família ↔ Médico/Técnico) mostrava a vista correta
  // (via renderView()), mas o destaque "ativo" nos botões da barra
  // lateral ficava parado no que tinha sido clicado numa sessão
  // anterior — renderView() nunca tocava nas classes .active dos
  // nav-items, só no conteúdo. Corrigido: limpa o estado ativo de todos
  // os botões e marca explicitamente o botão da vista por omissão do
  // perfil que entrou agora.
  const defaultView = isUtente ? 'resumo' : isAdmin ? 'admin' : 'pacientes';

  // Se a página foi aberta/recarregada com um fragmento (ex.: #rotina), só
  // agora é possível honrá-lo: o fragmento é lido no arranque mas o perfil
  // só existe depois da autenticação. Fragmento inválido, desconhecido ou
  // de uma vista que não pertence a este perfil cai na vista por omissão.
  let vistaInicial = defaultView;
  if (vistaPedidaNoArranque && viewPermitidaParaPerfil(vistaPedidaNoArranque, currentRole)) {
    vistaInicial = vistaPedidaNoArranque;
  }
  vistaPedidaNoArranque = null;

  sincronizarMenu(vistaInicial);

  // currentView pode ter ficado com a vista da sessão anterior (logout()
  // limpa-o, mas a primeira sessão da página começa a null de qualquer
  // forma). replaceState em vez de pushState: a entrada da aplicação
  // substitui a entrada atual, para que carregar em "retroceder" logo a
  // seguir a entrar não deixe um ecrã vazio anterior à aplicação.
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
  // Sem isto, sair na vista "resumo" e voltar a entrar como Utente (cuja
  // vista de omissão é também "resumo") faria com que renderView() não
  // detetasse mudança de vista e o fragmento do endereço ficasse preso na
  // sessão anterior. Ver a regra "só regista se a vista mudou".
  currentView = null;
  history.replaceState(null, '', '#');
}

// Ativa visualmente um nav-item e mostra a vista correspondente — partilhado
// entre o clique direto num item do menu e outros atalhos (ex: sino de
// alertas na topbar, ver switchNav abaixo).
function activateNavItem(item){
  if (!item) return;
  const view = item.dataset.view;
  if (!view) return;
  const group = item.closest('.nav-group');
  if (group) group.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
  item.classList.add('active');
  renderView(view);
}

// Marca o botão do menu correspondente a uma vista, sem passar por um clique.
// Necessário porque renderView() nunca mexe nas classes .active — quem o faz é
// activateNavItem(), que só é invocada a partir do clique. Sem isto, navegar
// pelo botão "retroceder" do browser mostrava o conteúdo certo com o botão
// errado ainda iluminado (a mesma família de bug corrigida em 2026-07-03).
//
// Procura restringida ao grupo do perfil atual, e não a document.querySelector
// global: os botões dos outros perfis continuam no DOM (escondidos por
// display:none) e um seletor global devolveria o primeiro por ordem de
// documento, não o visível — foi exatamente essa a causa do bug do sino.
function sincronizarMenu(view){
  document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
  const grupo = currentRole === 'utente' ? 'navUtente'
              : currentRole === 'admin'  ? 'navAdmin'
              : 'navClinico';
  const btn = document.querySelector(`#${grupo} .nav-item[data-view="${view}"]`);
  if (btn) btn.classList.add('active');
  // Vistas "perfil" e "ajuda" não pertencem a nenhum grupo de navegação:
  // nenhum botão fica marcado, e isso é o comportamento correto.
}

// Sino de alertas na topbar (ver botão em #view-app .topbar-right).
// BUG CORRIGIDO (2026-07-03): a versão anterior usava um seletor CSS
// (querySelector com vários seletores separados por vírgula) que, na
// prática, resolvia sempre para o botão "Resumo" dentro de #navUtente
// — esse botão existe no DOM (mesmo escondido por display:none quando o
// perfil é Médico/Técnico) e aparece antes do botão "Anomalias" na ordem
// do documento, e querySelector devolve sempre o primeiro nó que
// corresponda a QUALQUER um dos seletores da lista, por ordem no DOM, não
// por ordem de preferência. Resultado: clicar no sino, estando já na
// vista "Resumo" (o caso mais comum, perfil Utente/Família), re-renderizava
// a mesma vista — parecia não fazer nada. Corrigido com uma função
// dedicada que decide explicitamente pelo perfil atual (currentRole).
function onNotificationBellClick(){
  if (currentRole === 'utente') {
    // Perfil Utente/Família não tem uma vista de "Anomalias" dedicada —
    // os alertas já aparecem no cartão "Alertas recentes" da vista
    // Resumo. Garante que essa vista está ativa e, se já estava, dá
    // scroll até ao cartão de alertas para ficar visível que algo
    // aconteceu (em vez de parecer que o clique não teve efeito).
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

// Chaves i18n por vista — traduzido dinamicamente via t(), em vez de um
// dicionário fixo, para que mudar de idioma atualize também o título da
// vista atualmente aberta (ver applyI18n()).
const VIEW_TITLE_KEYS = {
  resumo:'nav.summary', rotina:'nav.routine', vitais:'nav.vitals', tendencia:'nav.trend', definicoes:'nav.settings',
  pacientes:'nav.patients', dispositivo:'nav.device', anomalias:'nav.anomalies', limites:'nav.limits', exportar:'nav.export',
  ajuda:'nav.help', emergencias:'nav.emergencies', perfil:'nav.profile', medicacao:'nav.medication',
  alertas:'nav.alertHistory', admin:'nav.admin',
};

// Guarda a vista atualmente ativa — usado por ações que precisam de
// re-renderizar "onde quer que o utilizador esteja" (ex.: silenciar um
// alerta em muteAlert()/unmuteAlert(), já que os cartões de alertas
// aparecem em mais do que uma vista).
let currentView = null;

// O segundo parâmetro distingue navegação iniciada pelo utilizador (que deve
// entrar no histórico) de re-desenho iniciado pelo próprio código — seja um
// renderView(currentView) depois de silenciar um alerta, seja o redesenho que
// o handler de popstate faz para responder ao botão "retroceder". Sem essa
// distinção, o popstate voltaria a escrever no histórico aquilo que acabou de
// ler, e entrava em ciclo.
//
// As 28 chamadas espalhadas pelos outros ficheiros não passam o segundo
// argumento e ficam com o valor por omissão true. Não precisam de ser
// alteradas por causa da regra abaixo: 24 dessas 28 têm a forma
// renderView(currentView), e nessas a vista não muda, logo nada é escrito no
// histórico. Silenciar três alertas seguidos não cria três entradas falsas.
function renderView(view, registarNoHistorico = true){
  const mudouDeVista = view !== currentView;
  currentView = view;
  document.getElementById('topbarTitle').textContent = VIEW_TITLE_KEYS[view] ? t(VIEW_TITLE_KEYS[view]) : 'CareWear';
  const c = document.getElementById('content');
  c.innerHTML = TEMPLATES[view] ? TEMPLATES[view]() : '<div class="empty-hint">Vista em construção.</div>';
  requestAnimationFrame(() => AFTER_RENDER[view] && AFTER_RENDER[view]());

  if (registarNoHistorico && mudouDeVista) {
    // Fragmento (#vista) e não caminho: history.pushState com um caminho
    // diferente lança SecurityError quando o dashboard é aberto por duplo
    // clique no index.html (protocolo file://). Com fragmento funciona nos
    // dois casos. Alterar só o fragmento por pushState não dispara
    // 'hashchange', pelo que basta tratar 'popstate'.
    history.pushState({ view }, '', '#' + view);
  }
}

/* ============================================================
   HISTÓRICO DE NAVEGAÇÃO
   Acrescentado em 2026-09-07. Antes disto o dashboard não tinha
   histórico nem endereço por vista: o botão "retroceder" do browser saía
   da aplicação e nenhuma vista era partilhável por ligação.
============================================================ */

// Que perfis podem abrir cada vista. Esta informação já existia, mas apenas
// implícita na estrutura dos grupos #navUtente / #navClinico / #navAdmin no
// index.html. Aqui fica explícita e testável, sem depender do DOM.
//
// AVISO, e tem de ser lido antes de se confiar nesta constante: isto NÃO é
// controlo de acesso. Serve para um endereço colado ou desatualizado não levar
// o utilizador a uma vista vazia ou partida — é robustez de interface. Na
// consola do browser, renderView('admin') continua a abrir a vista de
// administração seja qual for o perfil, porque este código corre no cliente e
// o cliente está sob controlo de quem o usa.
//
// Estado real do controlo de acesso do lado do servidor, verificado em
// 2026-09-07 (uma versão anterior deste comentário afirmava que não existia
// nenhum, o que estava errado):
//   - REST (bridge/api.py): IMPLEMENTADO. _authorize_patient() distingue
//     cuidador, clínico e admin, e é chamado nos 4 endpoints que devolvem ou
//     alteram dados clínicos, com _audit_read() a registar cada acesso.
//   - WebSocket (bridge/ble_bridge.py): EM FALTA, e é o caminho principal dos
//     dados. handle_dashboard_command() despacha 16 comandos — incluindo
//     get_history, export_csv, set_consent, set_retention_days e o destrutivo
//     reset_readings — sem consultar identidade nem perfil. A sessão é
//     resolvida em ws_transport.py mas só alimenta o campo user_id do registo
//     de auditoria: é auditoria, não autorização. CAREWEAR_WS_TOKEN está
//     desligado por omissão.
// É esta metade em falta que o requisito RF-02 (Must) cobre.
const VIEW_ROLES = {
  resumo:      ['utente'],
  rotina:      ['utente'],
  vitais:      ['utente'],
  tendencia:   ['utente'],
  definicoes:  ['utente'],
  pacientes:   ['clinico'],
  dispositivo: ['clinico'],
  anomalias:   ['clinico'],
  limites:     ['clinico'],
  exportar:    ['clinico'],
  alertas:     ['utente', 'clinico'],
  emergencias: ['utente', 'clinico'],
  medicacao:   ['utente', 'clinico'],
  admin:       ['admin'],
  perfil:      ['utente', 'clinico', 'admin'],
  ajuda:       ['utente', 'clinico', 'admin'],
};

function viewPermitidaParaPerfil(view, role){
  const perfis = VIEW_ROLES[view];
  return Array.isArray(perfis) && perfis.includes(role);
}

function vistaPorOmissao(role){
  return role === 'utente' ? 'resumo' : role === 'admin' ? 'admin' : 'pacientes';
}

// Lido uma única vez, no arranque, e só consumido em login() — o fragmento
// pode chegar antes de haver perfil, e sem perfil não é possível validá-lo.
let vistaPedidaNoArranque = (location.hash || '').replace(/^#/, '') || null;

window.addEventListener('popstate', (e) => {
  // Sem sessão ativa não se desenha nada. Isto é uma proteção de privacidade e
  // não uma verificação de conveniência: as variáveis do módulo mantêm os
  // dados do paciente depois de logout(), pelo que responder a um "retroceder"
  // sem esta guarda mostraria dados clínicos a quem não iniciou sessão — num
  // computador partilhado, bastaria uma tecla. O histórico do browser não pode
  // ser apagado por JavaScript, por isso o problema tem de ser tratado aqui.
  // Mesmo padrão já usado em i18n-theme.js.
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

