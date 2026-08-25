// Kanban — /tablero
import { api, avisar, fmtMin } from './api.js';

const cont = document.getElementById('tablero');
const modal = document.getElementById('modal-tarea');
let estado = { columns: [], tasks: [], projects: [] };
let editando = null;          // id de la tarea abierta en el modal, o null si es nueva
let columnaNueva = null;      // columna destino al crear desde «＋ Añadir tarea»
let sortables = [];

async function cargar() {
  estado = await api('GET', '/api/board');
  render();
  const hash = window.location.hash.match(/^#task-(\d+)$/);
  if (hash) {
    const t = estado.tasks.find(x => x.id === Number(hash[1]));
    if (t) abrirModal(t);
    history.replaceState(null, '', '/tablero');
  }
}

function proyectoDe(t) {
  return estado.projects.find(p => p.id === t.project_id) || null;
}

function tarjeta(t) {
  const div = document.createElement('div');
  div.className = 'tarjeta' + (t.estado === 'hecha' ? ' hecha' : '');
  div.dataset.id = t.id;
  const p = proyectoDe(t);
  div.style.borderLeftColor = p ? p.color : 'var(--light)';

  const titulo = document.createElement('div');
  titulo.className = 'titulo';
  titulo.textContent = t.titulo;
  div.appendChild(titulo);

  const meta = document.createElement('div');
  meta.className = 'meta';
  if (p) {
    const chip = document.createElement('span');
    chip.className = 'chip';
    const punto = document.createElement('span');
    punto.className = 'punto';
    punto.style.background = p.color;
    chip.append(punto, document.createTextNode(p.nombre));
    meta.appendChild(chip);
  }
  const dur = document.createElement('span');
  dur.className = 'badge';
  dur.textContent = fmtMin(t.estimado_min);
  meta.appendChild(dur);
  if (t.fecha_limite) {
    const lim = document.createElement('span');
    const hoy = new Date().toISOString().slice(0, 10);
    lim.className = 'badge' + (t.fecha_limite < hoy && t.estado !== 'hecha' ? ' tarde' : '');
    const [, m, d] = t.fecha_limite.split('-');
    lim.textContent = '⏰ ' + Number(d) + '/' + Number(m);
    meta.appendChild(lim);
  }
  if (t.proximo_bloque) {
    const plan = document.createElement('span');
    plan.className = 'badge plan';
    const f = new Date(t.proximo_bloque);
    plan.textContent = '📅 ' + ['dom','lun','mar','mié','jue','vie','sáb'][f.getDay()] + ' ' +
      String(f.getHours()).padStart(2, '0') + ':' + String(f.getMinutes()).padStart(2, '0');
    meta.appendChild(plan);
  }
  if (meta.children.length) div.appendChild(meta);
  div.addEventListener('click', () => abrirModal(t));
  return div;
}

function render() {
  sortables.forEach(s => s.destroy());
  sortables = [];
  cont.innerHTML = '';
  for (const col of estado.columns) {
    const tareas = estado.tasks.filter(t => t.column_id === col.id);
    const divCol = document.createElement('div');
    divCol.className = 'columna';
    divCol.dataset.id = col.id;

    const cab = document.createElement('div');
    cab.className = 'columna-cab';
    const h3 = document.createElement('h3');
    h3.textContent = col.nombre + (col.es_hecho ? ' ✓' : '');
    h3.title = 'Doble clic para renombrar';
    h3.addEventListener('dblclick', () => renombrarColumna(col));
    const contador = document.createElement('span');
    contador.className = 'contador';
    contador.textContent = tareas.length;
    cab.append(h3, contador);
    if (!tareas.length && !col.es_hecho) {
      const x = document.createElement('button');
      x.className = 'btn-mini btn-gris';
      x.textContent = '✕';
      x.title = 'Eliminar columna';
      x.addEventListener('click', () => borrarColumna(col));
      cab.appendChild(x);
    }
    divCol.appendChild(cab);

    const lista = document.createElement('div');
    lista.className = 'col-lista';
    lista.dataset.col = col.id;
    tareas.forEach(t => lista.appendChild(tarjeta(t)));
    divCol.appendChild(lista);

    const add = document.createElement('button');
    add.className = 'btn-add-tarjeta';
    add.textContent = '＋ Añadir tarea';
    add.addEventListener('click', () => { columnaNueva = col.id; abrirModal(null); });
    divCol.appendChild(add);

    cont.appendChild(divCol);

    sortables.push(new Sortable(lista, {
      group: 'tareas', animation: 150, delay: 150, delayOnTouchOnly: true,
      onEnd: async (ev) => {
        const id = Number(ev.item.dataset.id);
        const colDestino = Number(ev.to.dataset.col);
        try {
          await api('POST', `/api/tasks/${id}/move`, { column_id: colDestino, posicion: ev.newIndex });
          await cargar();
        } catch (err) { avisar('aviso-tablero', err.message); cargar(); }
      },
    }));
  }
  sortables.push(new Sortable(cont, {
    animation: 150, draggable: '.columna', handle: '.columna-cab h3', delay: 200,
    onEnd: async () => {
      const orden = [...cont.querySelectorAll('.columna')].map(c => Number(c.dataset.id));
      try { await api('POST', '/api/columns/reorder', { orden }); } catch (err) { avisar('aviso-tablero', err.message); }
    },
  }));
}

async function renombrarColumna(col) {
  const nombre = prompt('Nombre de la columna:', col.nombre);
  if (!nombre || nombre.trim() === col.nombre) return;
  try { await api('PATCH', `/api/columns/${col.id}`, { nombre }); await cargar(); }
  catch (err) { avisar('aviso-tablero', err.message); }
}

async function borrarColumna(col) {
  if (!confirm(`¿Eliminar la columna «${col.nombre}»?`)) return;
  try { await api('DELETE', `/api/columns/${col.id}`); await cargar(); }
  catch (err) { avisar('aviso-tablero', err.message); }
}

document.getElementById('btn-nueva-columna').addEventListener('click', async () => {
  const nombre = prompt('Nombre de la nueva columna:');
  if (!nombre || !nombre.trim()) return;
  try { await api('POST', '/api/columns', { nombre }); await cargar(); }
  catch (err) { avisar('aviso-tablero', err.message); }
});

// ---------------- modal de tarea ----------------

const campos = {
  titulo: document.getElementById('t-titulo'),
  notas: document.getElementById('t-notas'),
  proyecto: document.getElementById('t-proyecto'),
  estimado: document.getElementById('t-estimado'),
  limite: document.getElementById('t-limite'),
  columna: document.getElementById('t-columna'),
  nuevoProyecto: document.getElementById('t-nuevo-proyecto'),
  proyectoNombre: document.getElementById('t-proyecto-nombre'),
  proyectoColor: document.getElementById('t-proyecto-color'),
};

function abrirModal(t) {
  editando = t ? t.id : null;
  document.getElementById('modal-titulo').textContent = t ? 'Editar tarea' : 'Nueva tarea';
  campos.titulo.value = t ? t.titulo : '';
  campos.notas.value = t ? t.notas : '';
  campos.limite.value = t && t.fecha_limite ? t.fecha_limite : '';
  campos.estimado.value = String(t ? t.estimado_min : 30);
  if (![...campos.estimado.options].some(o => o.value === campos.estimado.value)) {
    campos.estimado.value = '30';
  }
  campos.proyecto.innerHTML = '';
  const sin = new Option('— Sin proyecto —', '');
  campos.proyecto.add(sin);
  estado.projects.forEach(p => campos.proyecto.add(new Option(p.nombre, p.id)));
  campos.proyecto.add(new Option('➕ Nuevo proyecto…', '__nuevo'));
  campos.proyecto.value = t && t.project_id ? String(t.project_id) : '';
  campos.nuevoProyecto.classList.add('oculto');
  campos.proyectoNombre.value = '';
  campos.columna.innerHTML = '';
  estado.columns.forEach(c => campos.columna.add(new Option(c.nombre, c.id)));
  campos.columna.value = String(t ? t.column_id : columnaNueva);
  document.getElementById('t-archivar').classList.toggle('oculto', !t);
  document.getElementById('t-borrar').classList.toggle('oculto', !t);
  modal.showModal();
  campos.titulo.focus();
}

campos.proyecto.addEventListener('change', () => {
  campos.nuevoProyecto.classList.toggle('oculto', campos.proyecto.value !== '__nuevo');
  if (campos.proyecto.value === '__nuevo') campos.proyectoNombre.focus();
});

document.getElementById('t-cancelar').addEventListener('click', () => modal.close());

document.getElementById('form-tarea').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  let projectId = campos.proyecto.value ? campos.proyecto.value : null;
  try {
    if (projectId === '__nuevo') {
      const nombre = campos.proyectoNombre.value.trim();
      if (!nombre) { campos.proyectoNombre.focus(); return; }
      const p = await api('POST', '/api/projects', { nombre, color: campos.proyectoColor.value });
      projectId = p.id;
    }
    const datos = {
      titulo: campos.titulo.value.trim(),
      notas: campos.notas.value,
      estimado_min: Number(campos.estimado.value),
      fecha_limite: campos.limite.value || null,
      project_id: projectId ? Number(projectId) : null,
    };
    const colElegida = Number(campos.columna.value);
    if (editando) {
      const t = estado.tasks.find(x => x.id === editando);
      await api('PATCH', `/api/tasks/${editando}`, datos);
      if (t && t.column_id !== colElegida) {
        await api('POST', `/api/tasks/${editando}/move`, { column_id: colElegida, posicion: 0 });
      }
    } else {
      await api('POST', '/api/tasks', { ...datos, column_id: colElegida });
    }
    modal.close();
    await cargar();
  } catch (err) { avisar('aviso-tablero', err.message); }
});

document.getElementById('t-archivar').addEventListener('click', async () => {
  try { await api('POST', `/api/tasks/${editando}/archive`); modal.close(); await cargar(); }
  catch (err) { avisar('aviso-tablero', err.message); }
});

document.getElementById('t-borrar').addEventListener('click', async () => {
  if (!confirm('¿Borrar la tarea definitivamente? También se quitarán sus bloques futuros del calendario.')) return;
  try { await api('DELETE', `/api/tasks/${editando}`); modal.close(); await cargar(); }
  catch (err) { avisar('aviso-tablero', err.message); }
});

cargar().catch(err => avisar('aviso-tablero', err.message));
