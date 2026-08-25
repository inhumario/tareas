// Vista semana + time blocking — /semana
import { api, avisar, fmtHora, fmtMin } from './api.js';

const MINPX = 0.8, INICIO = 7 * 60, FIN = 22 * 60;
const ALTO = (FIN - INICIO) * MINPX;

const rejilla = document.getElementById('rejilla');
const cabecera = document.getElementById('rejilla-cabecera');
const tabs = document.getElementById('dias-tabs');
const bandejaLista = document.getElementById('bandeja-lista');
const modalPlan = document.getElementById('modal-planificar');

let datos = null;
let diaActivo = '';

function esMovil() { return window.matchMedia('(max-width: 860px)').matches; }

async function cargar(start) {
  avisar('aviso-semana', '');
  datos = await api('GET', '/api/week' + (start ? '?start=' + start : ''));
  if (!diaActivo || !datos.dias.some(d => d.fecha === diaActivo)) {
    diaActivo = datos.dias.some(d => d.fecha === datos.hoy) ? datos.hoy : datos.dias[0].fecha;
  }
  render();
}

function snap(min) { return Math.round(min / 15) * 15; }

// ---------------- render ----------------

function render() {
  const l = new Date(datos.lunes);
  document.getElementById('semana-titulo').textContent =
    'Semana del ' + l.getDate() + ' de ' +
    ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'][l.getMonth()];
  if (!datos.sincronizado && datos.aviso) avisar('aviso-semana', datos.aviso);

  // cabecera (escritorio) y pestañas (móvil)
  cabecera.innerHTML = '';
  cabecera.appendChild(document.createElement('div'));
  tabs.innerHTML = '';
  tabs.classList.remove('oculto');
  for (const d of datos.dias) {
    const cab = document.createElement('div');
    cab.className = 'dia-cab' + (d.fecha === datos.hoy ? ' hoy' : '');
    cab.textContent = d.label;
    for (const ev of datos.eventos.filter(e => e.tipo === 'dia_completo' && e.dia === d.fecha)) {
      const s = document.createElement('div');
      s.className = 'dia-completo-strip';
      s.textContent = '▸ ' + ev.titulo;
      cab.appendChild(s);
    }
    cabecera.appendChild(cab);

    const tab = document.createElement('button');
    tab.type = 'button';
    tab.textContent = d.label + (d.fecha === datos.hoy ? ' ·' : '');
    tab.className = d.fecha === diaActivo ? 'activa' : '';
    tab.addEventListener('click', () => { diaActivo = d.fecha; render(); });
    tabs.appendChild(tab);
  }

  // rejilla
  rejilla.innerHTML = '';
  const horas = document.createElement('div');
  horas.className = 'horas-col';
  horas.style.height = ALTO + 'px';
  for (let h = 8; h <= 21; h++) {
    const lbl = document.createElement('span');
    lbl.className = 'hora-label';
    lbl.style.top = ((h * 60 - INICIO) * MINPX) + 'px';
    lbl.textContent = String(h).padStart(2, '0') + ':00';
    horas.appendChild(lbl);
  }
  rejilla.appendChild(horas);

  for (const d of datos.dias) {
    const col = document.createElement('div');
    col.className = 'dia-col' + (d.fecha === diaActivo ? ' dia-activo' : '');
    col.dataset.dia = d.fecha;
    col.style.height = ALTO + 'px';
    for (let h = 8; h <= 21; h++) {
      const linea = document.createElement('div');
      linea.className = 'hora-linea';
      linea.style.top = ((h * 60 - INICIO) * MINPX) + 'px';
      col.appendChild(linea);
      const media = document.createElement('div');
      media.className = 'media-linea';
      media.style.top = ((h * 60 - 30 - INICIO) * MINPX) + 'px';
      col.appendChild(media);
    }
    if (d.fecha === datos.hoy && datos.ahora_min > INICIO && datos.ahora_min < FIN) {
      const ahora = document.createElement('div');
      ahora.className = 'linea-ahora';
      ahora.style.top = ((datos.ahora_min - INICIO) * MINPX) + 'px';
      col.appendChild(ahora);
    }
    for (const ev of datos.eventos.filter(e => e.dia === d.fecha && e.tipo !== 'dia_completo')) {
      col.appendChild(evento(ev));
    }
    rejilla.appendChild(col);
  }

  // bandeja
  bandejaLista.innerHTML = '';
  const pendientes = datos.bandeja.filter(t => !t.planificada);
  if (!pendientes.length) {
    const p = document.createElement('p');
    p.className = 'nota';
    p.textContent = datos.bandeja.length
      ? 'Todo lo abierto tiene ya su hueco reservado 🎉'
      : 'No hay tareas abiertas — crea alguna en el tablero.';
    bandejaLista.appendChild(p);
  }
  for (const t of pendientes) {
    const div = document.createElement('div');
    div.className = 'tarjeta';
    div.style.borderLeftColor = t.color;
    const titulo = document.createElement('div');
    titulo.className = 'titulo';
    titulo.textContent = t.titulo;
    div.appendChild(titulo);
    const meta = document.createElement('div');
    meta.className = 'meta';
    const dur = document.createElement('span');
    dur.className = 'badge';
    dur.textContent = fmtMin(t.estimado_min);
    meta.appendChild(dur);
    if (t.proyecto) {
      const chip = document.createElement('span');
      chip.textContent = t.proyecto;
      meta.appendChild(chip);
    }
    div.appendChild(meta);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn-mini btn-gris btn-planificar';
    btn.textContent = 'Planificar…';
    btn.addEventListener('click', (e) => { e.stopPropagation(); abrirPlanificar(t); });
    div.appendChild(btn);
    dragDesdeBandeja(div, t);
    bandejaLista.appendChild(div);
  }
}

function evento(ev) {
  const div = document.createElement('div');
  const ini = Math.max(ev.min_ini, INICIO), fin = Math.min(Math.max(ev.min_fin, ini + 15), FIN);
  div.style.top = ((ini - INICIO) * MINPX) + 'px';
  div.style.height = ((fin - ini) * MINPX) + 'px';
  const titulo = document.createElement('div');
  titulo.className = 'ev-titulo';
  titulo.textContent = ev.titulo;
  const hora = document.createElement('div');
  hora.className = 'ev-hora';
  hora.textContent = fmtHora(ev.min_ini) + '–' + fmtHora(ev.min_fin);
  div.append(titulo, hora);
  if (ev.tipo === 'bloque') {
    div.className = 'evento bloque' + (ev.hecha ? ' hecha' : '');
    div.style.borderLeftColor = ev.color;
    const borrar = document.createElement('button');
    borrar.type = 'button';
    borrar.className = 'ev-borrar';
    borrar.textContent = '✕';
    borrar.title = 'Quitar el bloque (borra el evento del calendario)';
    borrar.addEventListener('pointerdown', e => e.stopPropagation());
    borrar.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm('¿Quitar este bloque? El evento se borrará de Google Calendar.')) return;
      try { await api('DELETE', '/api/blocks/' + ev.block_id); await cargar(datos.lunes); }
      catch (err) { avisar('aviso-semana', err.message); }
    });
    div.appendChild(borrar);
    const asa = document.createElement('div');
    asa.className = 'asa-resize';
    div.appendChild(asa);
    dragBloque(div, ev);
    resizeBloque(asa, div, ev);
    div.addEventListener('click', (e) => {
      if (div.__arrastro || asa.__arrastro) return;
      if (e.target.closest('.ev-borrar') || e.target.closest('.asa-resize')) return;
      abrirMover(ev);
    });
  } else {
    div.className = 'evento ocupado';
  }
  return div;
}

// ---------------- utilidades de arrastre ----------------

function puntoAdiaMin(x, y, durMin) {
  for (const c of rejilla.querySelectorAll('.dia-col')) {
    if (esMovil() && !c.classList.contains('dia-activo')) continue;
    const r = c.getBoundingClientRect();
    if (x >= r.left && x <= r.right && y >= r.top - 30 && y <= r.bottom + 30) {
      let min = INICIO + (y - r.top) / MINPX;
      min = Math.max(INICIO, Math.min(FIN - durMin, snap(min)));
      return { dia: c.dataset.dia, min, col: c };
    }
  }
  return null;
}

let fantasma = null;
function pintarFantasma(destino, durMin, etiqueta) {
  if (!fantasma) {
    fantasma = document.createElement('div');
    fantasma.className = 'fantasma';
  }
  if (fantasma.parentElement !== destino.col) destino.col.appendChild(fantasma);
  fantasma.style.top = ((destino.min - INICIO) * MINPX) + 'px';
  fantasma.style.height = (durMin * MINPX) + 'px';
  fantasma.textContent = etiqueta ? ' ' + fmtHora(destino.min) : '';
}
function quitarFantasma() { if (fantasma) { fantasma.remove(); fantasma = null; } }

function alPointer(el, alMover, alSoltar, umbral = 6) {
  el.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0 && ev.pointerType === 'mouse') return;
    const x0 = ev.clientX, y0 = ev.clientY;
    let activo = false;
    const mover = (e) => {
      if (!activo && Math.hypot(e.clientX - x0, e.clientY - y0) < umbral) return;
      if (!activo) { activo = true; el.classList.add('arrastrando'); }
      e.preventDefault();
      alMover(e);
    };
    const soltar = async (e) => {
      window.removeEventListener('pointermove', mover);
      window.removeEventListener('pointerup', soltar);
      window.removeEventListener('pointercancel', soltar);
      el.classList.remove('arrastrando');
      quitarFantasma();
      if (activo) {
        el.__arrastro = true;                       // suprime el click que sigue al drag
        setTimeout(() => { el.__arrastro = false; }, 250);
        await alSoltar(e);
      }
    };
    window.addEventListener('pointermove', mover, { passive: false });
    window.addEventListener('pointerup', soltar);
    window.addEventListener('pointercancel', soltar);
  });
}

function dragBloque(el, ev) {
  const dur = ev.min_fin - ev.min_ini;
  let destino = null;
  alPointer(el, (e) => {
    destino = puntoAdiaMin(e.clientX, e.clientY, dur);
    if (destino) pintarFantasma(destino, dur, true);
  }, async () => {
    if (!destino) return;
    if (destino.dia === ev.dia && destino.min === ev.min_ini) return;
    try {
      await api('PATCH', '/api/blocks/' + ev.block_id, { dia: destino.dia, min_ini: destino.min, dur_min: dur });
      await cargar(datos.lunes);
    } catch (err) { avisar('aviso-semana', err.message); cargar(datos.lunes); }
  });
}

function resizeBloque(asa, el, ev) {
  let nuevaDur = null;
  alPointer(asa, (e) => {
    const col = el.parentElement.getBoundingClientRect();
    const finMin = INICIO + (e.clientY - col.top) / MINPX;
    nuevaDur = Math.max(15, snap(finMin) - ev.min_ini);
    el.style.height = (Math.min(nuevaDur, FIN - ev.min_ini) * MINPX) + 'px';
  }, async () => {
    if (nuevaDur === null || nuevaDur === ev.min_fin - ev.min_ini) return;
    try {
      await api('PATCH', '/api/blocks/' + ev.block_id, { dur_min: nuevaDur });
      await cargar(datos.lunes);
    } catch (err) { avisar('aviso-semana', err.message); cargar(datos.lunes); }
  }, 3);
}

function dragDesdeBandeja(el, tarea) {
  let destino = null;
  alPointer(el, (e) => {
    destino = puntoAdiaMin(e.clientX, e.clientY, tarea.estimado_min);
    if (destino) pintarFantasma(destino, tarea.estimado_min, true);
  }, async () => {
    if (!destino) return;
    await crearBloque(tarea, destino.dia, destino.min);
  });
}

async function crearBloque(tarea, dia, minIni) {
  try {
    await api('POST', '/api/blocks',
      { task_id: tarea.id, dia, min_ini: minIni, dur_min: tarea.estimado_min });
    await cargar(datos.lunes);
  } catch (err) { avisar('aviso-semana', err.message); }
}

// ---------------- modal «Planificar…» (huecos libres) ----------------

function huecosLibres(durMin, excluirBlockId = null) {
  const huecos = [];
  for (const d of datos.dias) {
    if (d.fecha < datos.hoy) continue;
    const ocupados = datos.eventos
      .filter(e => e.dia === d.fecha && e.tipo !== 'dia_completo'
                   && !(excluirBlockId && e.block_id === excluirBlockId))
      .map(e => [e.min_ini, e.min_fin]).sort((a, b) => a[0] - b[0]);
    let cursor = d.fecha === datos.hoy ? Math.max(INICIO, snap(datos.ahora_min + 14)) : INICIO;
    for (const [ini, fin] of ocupados) {
      if (ini - cursor >= durMin) huecos.push({ dia: d.fecha, label: d.label, min: cursor });
      cursor = Math.max(cursor, fin);
    }
    if (FIN - cursor >= durMin) huecos.push({ dia: d.fecha, label: d.label, min: cursor });
    if (huecos.length >= 14) break;
  }
  return huecos.slice(0, 14);
}

function abrirSelectorHuecos({ titulo, durMin, excluirBlockId, alElegir, alQuitar }) {
  document.getElementById('p-titulo').textContent = titulo;
  document.getElementById('p-dur').textContent = fmtMin(durMin);
  const cont = document.getElementById('p-huecos');
  cont.innerHTML = '';
  const huecos = huecosLibres(durMin, excluirBlockId);
  if (!huecos.length) {
    const p = document.createElement('p');
    p.className = 'nota';
    p.textContent = 'No quedan huecos libres esta semana — pasa a la siguiente con ▶.';
    cont.appendChild(p);
  }
  for (const h of huecos) {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = `${h.label} · ${fmtHora(h.min)}–${fmtHora(h.min + durMin)}`;
    b.addEventListener('click', async () => { modalPlan.close(); await alElegir(h); });
    cont.appendChild(b);
  }
  const quitar = document.getElementById('p-quitar');
  quitar.classList.toggle('oculto', !alQuitar);
  quitar.onclick = alQuitar ? async () => { modalPlan.close(); await alQuitar(); } : null;
  modalPlan.showModal();
}

function abrirPlanificar(tarea) {
  abrirSelectorHuecos({
    titulo: tarea.titulo,
    durMin: tarea.estimado_min,
    alElegir: (h) => crearBloque(tarea, h.dia, h.min),
  });
}

function abrirMover(ev) {
  const dur = ev.min_fin - ev.min_ini;
  abrirSelectorHuecos({
    titulo: ev.titulo,
    durMin: dur,
    excluirBlockId: ev.block_id,
    alElegir: async (h) => {
      try {
        await api('PATCH', '/api/blocks/' + ev.block_id, { dia: h.dia, min_ini: h.min, dur_min: dur });
        await cargar(datos.lunes);
      } catch (err) { avisar('aviso-semana', err.message); }
    },
    alQuitar: async () => {
      if (!confirm('¿Quitar este bloque? El evento se borrará de Google Calendar.')) return;
      try { await api('DELETE', '/api/blocks/' + ev.block_id); await cargar(datos.lunes); }
      catch (err) { avisar('aviso-semana', err.message); }
    },
  });
}

document.getElementById('p-cancelar').addEventListener('click', () => modalPlan.close());

// ---------------- navegación ----------------

function sumarDias(iso, n) {
  const d = new Date(iso + 'T12:00:00');
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

document.getElementById('btn-prev').addEventListener('click', () => cargar(sumarDias(datos.lunes, -7)));
document.getElementById('btn-next').addEventListener('click', () => cargar(sumarDias(datos.lunes, 7)));
document.getElementById('btn-hoy').addEventListener('click', () => { diaActivo = ''; cargar(''); });

cargar('').catch(err => avisar('aviso-semana', err.message));
