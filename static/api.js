// Helper de llamadas a la API JSON — redirige a /login si la sesión caduca.
export async function api(metodo, ruta, cuerpo) {
  const opts = { method: metodo, headers: {} };
  if (cuerpo !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(cuerpo);
  }
  const r = await fetch(ruta, opts);
  if (r.status === 401) { window.location = '/login'; throw new Error('Sesión caducada'); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || ('Error ' + r.status));
  return data;
}

export function avisar(contenedor, texto, esError = true) {
  const div = document.getElementById(contenedor);
  if (!div) return;
  div.innerHTML = '';
  if (!texto) return;
  const d = document.createElement('div');
  d.className = esError ? 'aviso-err' : 'aviso-ok';
  d.textContent = texto;
  div.appendChild(d);
  if (esError) setTimeout(() => { if (div.contains(d)) d.remove(); }, 6000);
}

export function fmtMin(min) {
  if (min < 60) return min + ' min';
  const h = Math.floor(min / 60), m = min % 60;
  return m ? `${h} h ${m}` : `${h} h`;
}

export function fmtHora(min) {
  const h = Math.floor(min / 60), m = min % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}
