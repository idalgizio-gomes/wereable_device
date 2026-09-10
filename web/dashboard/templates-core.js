function iconFor(type){
  const M = {
    heart:'<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8z"/>',
    walk:'<circle cx="13" cy="4" r="2"/><path d="M9 21l2-6 2 2 3 4M9 15l1-5-3-2 3-6 4 2 4-1"/>',
    warn:'<path d="M10.3 3.9 1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    drop:'<path d="M12 2s7 8.2 7 12.5A7 7 0 1 1 5 14.5C5 10.2 12 2 12 2z"/>',
    moon:'<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>',
    zap:'<path d="M13 2 3 14h7l-1 8 11-13h-7l1-7z"/>',
  };
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${M[type]||M.warn}</svg>`;
}
const SEV_COLOR = {critical:'var(--status-critical)', serious:'var(--status-serious)', warning:'var(--status-warning)', good:'var(--status-good)'};
const SEV_BG = {critical:'var(--status-critical-bg)', serious:'var(--status-serious-bg)', warning:'var(--status-warning-bg)', good:'var(--status-good-bg)'};

function pillHtml(sev, label){
  return `<span class="pill ${sev}">${iconFor(sev==='critical'?'warn':sev==='warning'?'warn':'zap')}${label}</span>`;
}

//escapa texto livre do utilizador antes de inserir em innerHTML
function escapeHtml(str){
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function fmtMin(m){
  const h = Math.floor(m/60), mm = String(m%60).padStart(2,'0');
  return `${String(h).padStart(2,'0')}:${mm}`;
}

function fmtDuration(totalMinutes){
  const h = Math.floor(totalMinutes/60), m = totalMinutes%60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

const TEMPLATES = {};
const AFTER_RENDER = {};
