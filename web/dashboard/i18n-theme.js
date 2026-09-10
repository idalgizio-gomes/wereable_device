const LANG_NAMES = {pt:'Português', en:'English', zh:'中文', es:'Español', fr:'Français', de:'Deutsch', it:'Italiano'};

// TTL automático de dados locais (GDPR-002) — apaga chaves 'carewear_*' após LOCAL_DATA_TTL_DAYS dias de inatividade
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
  // Reaplica a vista atual via currentView, não via .nav-item.active (esse fica desatualizado em vistas sem nav-item, ex. Ajuda)
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

// Tema claro/escuro — persistido em localStorage, por omissão segue prefers-color-scheme
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

