const API_BASE = 'http://127.0.0.1:8766';
const AUTH_TOKEN_KEY = 'carewear_auth_token';

function getAuthToken(){
  return sessionStorage.getItem(AUTH_TOKEN_KEY);
}
function setAuthToken(token){
  sessionStorage.setItem(AUTH_TOKEN_KEY, token);
}
function clearAuthToken(){
  sessionStorage.removeItem(AUTH_TOKEN_KEY);
}

async function apiFetch(path, opts = {}){
  const headers = Object.assign({}, opts.headers);
  const token = getAuthToken();
  if (token) headers['Authorization'] = 'Bearer ' + token;
  if (opts.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  const res = await fetch(API_BASE + path, Object.assign({}, opts, { headers }));
  return res;
}

async function apiLogin(email, password){
  try {
    const res = await fetch(API_BASE + '/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) return { ok: false, error: res.status === 401 ? 'credenciais' : 'servidor' };
    const data = await res.json();
    setAuthToken(data.token);
    const me = await apiFetch('/api/auth/me');
    if (!me.ok) return { ok: false, error: 'servidor' };
    const user = await me.json();
    return { ok: true, user };
  } catch (e) {
    return { ok: false, error: 'offline' };
  }
}

async function apiLogout(){
  try {
    await apiFetch('/api/auth/logout', { method: 'POST' });
  } catch (e) {
    // sem rede — a sessão local é limpa de qualquer forma abaixo
  }
  clearAuthToken();
}
