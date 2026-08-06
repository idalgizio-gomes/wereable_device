const LANG_NAMES = {pt:'Português', en:'English', zh:'中文', es:'Español', fr:'Français', de:'Deutsch', it:'Italiano'};

/* ============================================================
   TTL AUTOMÁTICO DE DADOS LOCAIS (GDPR-002, ver SECURITY_STATUS.md)
   ------------------------------------------------------------
   'carewear_last_activity' guarda a última vez que esta app correu
   neste browser. Se passarem mais de LOCAL_DATA_TTL_DAYS dias sem
   nenhuma visita, todas as chaves 'carewear_*' (perfil com NIF/morada,
   consentimento, medicação, notas, histórico de alertas) são apagadas
   automaticamente antes de qualquer outra leitura de localStorage —
   janela deslizante de inatividade, não um prazo fixo desde a
   criação, para não apagar dados de quem usa a app ativamente ao
   longo de meses. Mesmo prazo por omissão (30 dias) já usado na
   retenção do bridge (ver retentionDaysInput), por consistência.
   Purga manual continua disponível em Definições → Zona de risco
   (eraseAllLocalData()), para quando o utilizador não quiser esperar.
============================================================ */
const LOCAL_DATA_TTL_DAYS = 30;
(function purgeExpiredLocalDataIfNeeded(){
  const TS_KEY = 'carewear_last_activity';
  const last = Number(localStorage.getItem(TS_KEY));
  const now = Date.now();
  if (last && (now - last) > LOCAL_DATA_TTL_DAYS * 24 * 60 * 60 * 1000) {
    Object.keys(localStorage).filter(k => k.startsWith('carewear_')).forEach(k => localStorage.removeItem(k));
  }
  localStorage.setItem(TS_KEY, String(now));
})();

let currentLang = localStorage.getItem('carewear_lang') || 'pt';

function t(key, vars){
  const dict = I18N[currentLang] || I18N.pt;
  let s = dict[key] ?? I18N.pt[key] ?? key;
  if (vars) Object.keys(vars).forEach(k => { s = s.replace(new RegExp('\\{' + k + '\\}', 'g'), vars[k]); });
  return s;
}

function applyI18n(){
  document.title = t('app.title');
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  // Reaplica o título da vista atual (VIEW_TITLES é traduzido dinamicamente, ver renderView).
  // BUG CORRIGIDO (2026-07-17): usava document.querySelector('.nav-item.active[data-view]')
  // para saber "qual vista está aberta" — mas os botões "Ajuda" e "Terminar sessão" chamam
  // renderView() diretamente por onclick, sem passar por nenhum handler que atualize a
  // classe .active dos itens de navegação. Resultado: ao trocar de idioma estando na vista
  // Ajuda, o querySelector encontrava o item .active MAIS ANTIGO (ex. "Resumo", nunca
  // desmarcado) e renderView('resumo') substituía silenciosamente o conteúdo da Ajuda pelo
  // Resumo — a vista aberta "desaparecia" sem qualquer erro. currentView (ver renderView())
  // já rastreia isto corretamente para qualquer vista, incluindo as que não têm nav-item.
  if (currentView && document.getElementById('view-app').classList.contains('active')) renderView(currentView);
  updateDeviceStatusUI();
  updateBatteryUI();
  updateLiveEmergencyBanner();
}

function setLanguage(lang){
  currentLang = I18N[lang] ? lang : 'pt';
  localStorage.setItem('carewear_lang', currentLang);
  applyI18n();
}

function populateLangSelect(){
  const sel = document.getElementById('langSelect');
  if (!sel) return;
  sel.innerHTML = Object.entries(LANG_NAMES).map(([code, name]) => `<option value="${code}">${name}</option>`).join('');
  sel.value = currentLang;
}

/* ============================================================
   TEMA CLARO/ESCURO
   ------------------------------------------------------------
   Persistido em localStorage; por omissão segue a preferência do
   sistema operativo (prefers-color-scheme) na primeira visita.
============================================================ */
function applyTheme(theme){
  document.documentElement.setAttribute('data-theme', theme);
  const moon = document.getElementById('themeIconMoon');
  const sun = document.getElementById('themeIconSun');
  if (moon && sun){
    moon.style.display = theme === 'dark' ? '' : 'none';
    sun.style.display = theme === 'light' ? '' : 'none';
  }
}
function toggleTheme(){
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  const next = current === 'dark' ? 'light' : 'dark';
  localStorage.setItem('carewear_theme', next);
  applyTheme(next);
}
(function initThemeAndLang(){
  const savedTheme = localStorage.getItem('carewear_theme');
  const systemPrefersLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
  applyTheme(savedTheme || (systemPrefersLight ? 'light' : 'dark'));
})();

