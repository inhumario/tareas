"""
Tareas — tablero kanban + time blocking con Google Calendar (Inhumario)
=======================================================================

- Kanban en /tablero (columnas configurables, drag & drop, proyectos con color).
- Time blocking en /semana: eventos reales de Google Calendar de fondo y
  bloques de tareas que crean/actualizan eventos reales.
- OAuth web por usuario: cada cuenta conecta su propio Google Calendar.
- Multi-usuario desde el día 1 (alta con código de invitación, admin).

Variables de entorno:
  SECRET_KEY    — firma de cookies de sesión (obligatoria en producción)
  ENCRYPT_KEY   — clave Fernet para cifrar refresh tokens (obligatoria en prod)
  ALTA_CODIGO   — código de invitación para el alta
  ADMIN_EMAIL   — email con acceso a /admin
  DB_PATH       — sqlite, por defecto ./data/tareas.db (en prod /data/tareas.db)
  BASE_URL      — URL pública, por defecto http://localhost:8000
  GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET — OAuth client web (ver gcal.py)
"""

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import gcal

VERSION = "0.2.2"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SECRET_KEY = os.environ.get("SECRET_KEY", "")
ENCRYPT_KEY = os.environ.get("ENCRYPT_KEY", "")
ALTA_CODIGO = os.environ.get("ALTA_CODIGO", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "data", "tareas.db"))
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")

if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    print("⚠️  SECRET_KEY no definida — usando una efímera (solo desarrollo)")
if not ENCRYPT_KEY:
    ENCRYPT_KEY = Fernet.generate_key().decode()
    print("⚠️  ENCRYPT_KEY no definida — usando una efímera (solo desarrollo)")

fernet = Fernet(ENCRYPT_KEY.encode())
TZ = ZoneInfo("Europe/Madrid")

COLUMNAS_SEMILLA = ["Backlog", "Esta semana", "Hoy", "En curso", "Esperando", "Hecho"]


# ---------------------------------------------------------------- cifrado

def enc(value: str) -> str:
    if not value:
        return ""
    return "enc:" + fernet.encrypt(value.encode()).decode()


def dec(value: str) -> str:
    if not value:
        return ""
    if value.startswith("enc:"):
        return fernet.decrypt(value[4:].encode()).decode()
    return value


# ---------------------------------------------------------------- base de datos

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                nombre TEXT DEFAULT '',
                pass_hash TEXT NOT NULL,
                tz TEXT DEFAULT 'Europe/Madrid',
                gcal_refresh_token TEXT DEFAULT '',
                gcal_calendar_id TEXT DEFAULT 'primary',
                gcal_auth_kind TEXT DEFAULT '',
                activo INTEGER DEFAULT 1,
                created TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS columns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                nombre TEXT NOT NULL,
                posicion INTEGER NOT NULL DEFAULT 0,
                es_hecho INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                nombre TEXT NOT NULL,
                color TEXT DEFAULT '#FF8080',
                archivado INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                column_id INTEGER NOT NULL,
                project_id INTEGER,
                titulo TEXT NOT NULL,
                notas TEXT DEFAULT '',
                estimado_min INTEGER DEFAULT 30,
                fecha_limite TEXT,
                posicion INTEGER NOT NULL DEFAULT 0,
                estado TEXT DEFAULT 'abierta',
                hecha_en TEXT,
                created TEXT DEFAULT (datetime('now')),
                updated TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS ix_tasks_user_col ON tasks(user_id, column_id, estado)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                task_id INTEGER NOT NULL,
                gcal_event_id TEXT NOT NULL,
                gcal_calendar_id TEXT NOT NULL DEFAULT 'primary',
                start TEXT NOT NULL,
                end TEXT NOT NULL,
                etag TEXT DEFAULT '',
                gcal_updated TEXT DEFAULT '',
                estado TEXT DEFAULT 'ok',
                last_synced TEXT DEFAULT (datetime('now')),
                UNIQUE(gcal_event_id, gcal_calendar_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS ix_blocks_user ON blocks(user_id, start)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                task_id INTEGER NOT NULL,
                texto TEXT NOT NULL,
                hecho INTEGER DEFAULT 0,
                posicion INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS ix_checklist_task ON checklist(task_id)")


def user_by(field: str, value) -> sqlite3.Row | None:
    with db() as conn:
        return conn.execute(f"SELECT * FROM users WHERE {field} = ?", (value,)).fetchone()


def sembrar_columnas(conn: sqlite3.Connection, user_id: int) -> None:
    for i, nombre in enumerate(COLUMNAS_SEMILLA):
        conn.execute("INSERT INTO columns (user_id, nombre, posicion, es_hecho) VALUES (?,?,?,?)",
                     (user_id, nombre, i, 1 if nombre == "Hecho" else 0))


def crear_usuario(email: str, nombre: str, password: str) -> None:
    with db() as conn:
        cur = conn.execute("INSERT INTO users (email, nombre, pass_hash) VALUES (?,?,?)",
                           (email, nombre.strip(), hash_password(password)))
        sembrar_columnas(conn, cur.lastrowid)


# ---------------------------------------------------------------- auth helpers

_login_fails: dict[str, list] = {}  # email -> [n_fallos, bloqueado_hasta]


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
    return f"{salt}${h.hex()}"


def check_password(password: str, stored: str) -> bool:
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(hash_password(password, salt), stored)


def _sign(payload: str) -> str:
    return hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session(email: str) -> str:
    payload = f"{email}|{int(time.time()) + 30 * 86400}"
    return f"{payload}|{_sign(payload)}"


def read_session(request: Request) -> sqlite3.Row | None:
    parts = request.cookies.get("sesion", "").split("|")
    if len(parts) != 3:
        return None
    email, exp, sig = parts
    if not hmac.compare_digest(sig, _sign(f"{email}|{exp}")) or int(exp) < time.time():
        return None
    row = user_by("email", email)
    return row if row and row["activo"] else None


def api_user(request: Request) -> sqlite3.Row:
    row = read_session(request)
    if not row:
        raise HTTPException(status_code=401, detail="Sesión caducada — recarga la página")
    return row


def es_admin(row) -> bool:
    return bool(row) and ADMIN_EMAIL and row["email"] == ADMIN_EMAIL


def set_session_cookie(resp: RedirectResponse, email: str) -> RedirectResponse:
    resp.set_cookie("sesion", make_session(email), httponly=True, secure=True,
                    samesite="lax", max_age=30 * 86400)
    return resp


# ---------------------------------------------------------------- fechas

def rfc3339(dia: str, minutos: int) -> str:
    d = date.fromisoformat(dia)
    return (datetime(d.year, d.month, d.day, tzinfo=TZ) + timedelta(minutes=minutos)).isoformat()


def parse_gcal_dt(valor: str) -> datetime:
    return datetime.fromisoformat(valor.replace("Z", "+00:00")).astimezone(TZ)


def dia_y_min(dt: datetime) -> tuple[str, int]:
    return dt.date().isoformat(), dt.hour * 60 + dt.minute


def lunes_de(fecha: date) -> date:
    return fecha - timedelta(days=fecha.weekday())


# ---------------------------------------------------------------- FastAPI

app = FastAPI()


@app.middleware("http")
async def cache_estaticos(request: Request, call_next):
    """Los estáticos se revalidan siempre (ETag→304): un deploy nunca deja JS viejo cacheado."""
    response = await call_next(request)
    if request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.globals["version"] = VERSION
init_db()


def render(request: Request, tpl: str, **ctx):
    user = ctx.pop("user", None)
    return templates.TemplateResponse(request, tpl, {
        "user": user, "logged": bool(user), "admin": es_admin(user), **ctx,
    })


# ---------------------------------------------------------------- páginas

@app.get("/")
def home(request: Request):
    return RedirectResponse("/tablero" if read_session(request) else "/login", status_code=302)


@app.get("/salud")
def salud():
    return {"ok": True, "version": VERSION}


@app.get("/privacidad")
def privacidad(request: Request):
    return render(request, "privacidad.html", user=read_session(request))


@app.get("/login")
def login_form(request: Request, error: str = ""):
    if read_session(request):
        return RedirectResponse("/tablero", status_code=302)
    return render(request, "login.html", error=error)


@app.post("/login")
def login(email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    fails = _login_fails.get(email, [0, 0])
    if fails[1] > time.time():
        return RedirectResponse("/login?error=Demasiados intentos, espera un minuto", status_code=302)
    row = user_by("email", email)
    if not row or not row["activo"] or not check_password(password, row["pass_hash"]):
        fails[0] += 1
        if fails[0] >= 5:
            fails = [0, time.time() + 60]
        _login_fails[email] = fails
        return RedirectResponse("/login?error=Email o contraseña incorrectos", status_code=302)
    _login_fails.pop(email, None)
    return set_session_cookie(RedirectResponse("/tablero", status_code=302), email)


@app.get("/alta")
def alta_form(request: Request, error: str = ""):
    return render(request, "alta.html", error=error)


@app.post("/alta")
def alta(nombre: str = Form(...), email: str = Form(...), password: str = Form(...),
         codigo: str = Form(...)):
    if not ALTA_CODIGO or codigo.strip() != ALTA_CODIGO:
        return RedirectResponse("/alta?error=Código de invitación no válido", status_code=302)
    email = email.strip().lower()
    if user_by("email", email):
        return RedirectResponse("/alta?error=Ya existe una cuenta con ese email", status_code=302)
    crear_usuario(email, nombre, password)
    return set_session_cookie(RedirectResponse("/tablero", status_code=302), email)


@app.get("/salir")
def salir():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("sesion")
    return resp


@app.get("/tablero")
def tablero(request: Request):
    user = read_session(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return render(request, "tablero.html", user=user)


@app.get("/semana")
def semana(request: Request):
    user = read_session(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return render(request, "semana.html", user=user)


@app.get("/ajustes")
def ajustes(request: Request, ok: str = "", error: str = ""):
    user = read_session(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return render(request, "ajustes.html", user=user, ok=ok, error=error,
                  gcal_conectado=bool(user["gcal_refresh_token"]))


# ---------------------------------------------------------------- API tablero

def _task_json(t: sqlite3.Row, bloques: dict, checklist: list | None = None) -> dict:
    b = bloques.get(t["id"], {})
    return {"id": t["id"], "column_id": t["column_id"], "project_id": t["project_id"],
            "titulo": t["titulo"], "notas": t["notas"], "estimado_min": t["estimado_min"],
            "fecha_limite": t["fecha_limite"], "posicion": t["posicion"], "estado": t["estado"],
            "n_bloques": b.get("n", 0), "proximo_bloque": b.get("proximo"),
            "checklist": checklist or []}


@app.get("/api/board")
def api_board(request: Request):
    user = api_user(request)
    hace_2sem = (datetime.now(TZ) - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
    ahora_iso = datetime.now(TZ).isoformat()
    with db() as conn:
        columnas = [dict(r) for r in conn.execute(
            "SELECT * FROM columns WHERE user_id=? ORDER BY posicion", (user["id"],))]
        proyectos = [dict(r) for r in conn.execute(
            "SELECT * FROM projects WHERE user_id=? AND archivado=0 ORDER BY nombre", (user["id"],))]
        tareas = conn.execute(
            "SELECT * FROM tasks WHERE user_id=? AND estado != 'archivada' "
            "AND (estado='abierta' OR hecha_en > ?) ORDER BY column_id, posicion",
            (user["id"], hace_2sem)).fetchall()
        bloques: dict[int, dict] = {}
        for b in conn.execute(
                "SELECT task_id, COUNT(*) n, MIN(CASE WHEN start > ? THEN start END) proximo "
                "FROM blocks WHERE user_id=? AND estado='ok' GROUP BY task_id",
                (ahora_iso, user["id"])):
            bloques[b["task_id"]] = {"n": b["n"], "proximo": b["proximo"]}
        listas: dict[int, list] = {}
        for c in conn.execute("SELECT * FROM checklist WHERE user_id=? ORDER BY task_id, posicion",
                              (user["id"],)):
            listas.setdefault(c["task_id"], []).append(
                {"id": c["id"], "texto": c["texto"], "hecho": bool(c["hecho"])})
    return {"columns": columnas, "projects": proyectos,
            "tasks": [_task_json(t, bloques, listas.get(t["id"])) for t in tareas]}


@app.post("/api/tasks")
async def api_task_crear(request: Request):
    user = api_user(request)
    p = await request.json()
    titulo = (p.get("titulo") or "").strip()
    if not titulo:
        raise HTTPException(400, "Falta el título")
    with db() as conn:
        col = conn.execute("SELECT id FROM columns WHERE id=? AND user_id=?",
                           (p.get("column_id"), user["id"])).fetchone()
        if not col:
            raise HTTPException(400, "Columna no válida")
        pos = conn.execute("SELECT COALESCE(MAX(posicion),-1)+1 FROM tasks WHERE column_id=?",
                           (col["id"],)).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO tasks (user_id, column_id, project_id, titulo, notas, estimado_min, "
            "fecha_limite, posicion) VALUES (?,?,?,?,?,?,?,?)",
            (user["id"], col["id"], p.get("project_id"), titulo, p.get("notas", ""),
             int(p.get("estimado_min") or 30), p.get("fecha_limite") or None, pos))
        t = conn.execute("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,)).fetchone()
    return _task_json(t, {})


def _propia(conn, tabla: str, id_: int, user_id: int) -> sqlite3.Row:
    row = conn.execute(f"SELECT * FROM {tabla} WHERE id=? AND user_id=?", (id_, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "No encontrado")
    return row


@app.patch("/api/tasks/{tid}")
async def api_task_editar(tid: int, request: Request):
    user = api_user(request)
    p = await request.json()
    campos, valores = [], []
    for campo in ("titulo", "notas", "estimado_min", "fecha_limite", "project_id"):
        if campo in p:
            v = p[campo]
            if campo == "titulo":
                v = (v or "").strip()
                if not v:
                    raise HTTPException(400, "Falta el título")
            if campo == "estimado_min":
                v = max(15, int(v or 30))
            if campo in ("fecha_limite", "project_id") and not v:
                v = None
            campos.append(f"{campo}=?")
            valores.append(v)
    if not campos:
        raise HTTPException(400, "Nada que actualizar")
    with db() as conn:
        _propia(conn, "tasks", tid, user["id"])
        conn.execute(f"UPDATE tasks SET {', '.join(campos)}, updated=datetime('now') WHERE id=?",
                     (*valores, tid))
        t = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    return _task_json(t, {})


@app.post("/api/tasks/{tid}/move")
async def api_task_mover(tid: int, request: Request):
    user = api_user(request)
    p = await request.json()
    with db() as conn:
        _propia(conn, "tasks", tid, user["id"])
        col = _propia(conn, "columns", int(p["column_id"]), user["id"])
        orden = [r["id"] for r in conn.execute(
            "SELECT id FROM tasks WHERE column_id=? AND estado != 'archivada' AND id != ? "
            "ORDER BY posicion", (col["id"], tid))]
        orden.insert(max(0, min(int(p.get("posicion", 0)), len(orden))), tid)
        for i, id_ in enumerate(orden):
            conn.execute("UPDATE tasks SET column_id=?, posicion=? WHERE id=?", (col["id"], i, id_))
        if col["es_hecho"]:
            conn.execute("UPDATE tasks SET estado='hecha', hecha_en=?, updated=datetime('now') "
                         "WHERE id=? AND estado='abierta'",
                         (datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"), tid))
        else:
            conn.execute("UPDATE tasks SET estado='abierta', hecha_en=NULL, "
                         "updated=datetime('now') WHERE id=? AND estado='hecha'", (tid,))
        t = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    return _task_json(t, {})


@app.post("/api/tasks/{tid}/archive")
def api_task_archivar(tid: int, request: Request):
    user = api_user(request)
    with db() as conn:
        _propia(conn, "tasks", tid, user["id"])
        conn.execute("UPDATE tasks SET estado='archivada', updated=datetime('now') WHERE id=?", (tid,))
    return {"ok": True}


@app.delete("/api/tasks/{tid}")
def api_task_borrar(tid: int, request: Request):
    """Borra la tarea y sus bloques; intenta borrar los eventos futuros en Google."""
    user = api_user(request)
    with db() as conn:
        _propia(conn, "tasks", tid, user["id"])
        bloques = conn.execute("SELECT * FROM blocks WHERE task_id=? AND estado='ok'", (tid,)).fetchall()
    token = _gcal_token(user)
    ahora = datetime.now(TZ)
    if token:
        for b in bloques:
            if parse_gcal_dt(b["start"]) > ahora:
                try:
                    gcal.event_delete(token, b["gcal_calendar_id"], b["gcal_event_id"])
                except gcal.GcalError:
                    pass
    with db() as conn:
        conn.execute("DELETE FROM blocks WHERE task_id=?", (tid,))
        conn.execute("DELETE FROM checklist WHERE task_id=?", (tid,))
        conn.execute("DELETE FROM tasks WHERE id=?", (tid,))
    return {"ok": True}


# ---------------------------------------------------------------- API checklist

@app.post("/api/tasks/{tid}/checklist")
async def api_check_crear(tid: int, request: Request):
    user = api_user(request)
    p = await request.json()
    texto = (p.get("texto") or "").strip()
    if not texto:
        raise HTTPException(400, "Falta el texto")
    with db() as conn:
        _propia(conn, "tasks", tid, user["id"])
        pos = conn.execute("SELECT COALESCE(MAX(posicion),-1)+1 FROM checklist WHERE task_id=?",
                           (tid,)).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO checklist (user_id, task_id, texto, posicion) VALUES (?,?,?,?)",
            (user["id"], tid, texto[:300], pos))
    return {"id": cur.lastrowid, "texto": texto[:300], "hecho": False}


@app.patch("/api/checklist/{cid}")
async def api_check_editar(cid: int, request: Request):
    user = api_user(request)
    p = await request.json()
    with db() as conn:
        _propia(conn, "checklist", cid, user["id"])
        if "texto" in p and (p["texto"] or "").strip():
            conn.execute("UPDATE checklist SET texto=? WHERE id=?", (p["texto"].strip()[:300], cid))
        if "hecho" in p:
            conn.execute("UPDATE checklist SET hecho=? WHERE id=?", (1 if p["hecho"] else 0, cid))
        c = conn.execute("SELECT * FROM checklist WHERE id=?", (cid,)).fetchone()
    return {"id": c["id"], "texto": c["texto"], "hecho": bool(c["hecho"])}


@app.delete("/api/checklist/{cid}")
def api_check_borrar(cid: int, request: Request):
    user = api_user(request)
    with db() as conn:
        _propia(conn, "checklist", cid, user["id"])
        conn.execute("DELETE FROM checklist WHERE id=?", (cid,))
    return {"ok": True}


# ---------------------------------------------------------------- API columnas y proyectos

@app.post("/api/columns")
async def api_col_crear(request: Request):
    user = api_user(request)
    p = await request.json()
    nombre = (p.get("nombre") or "").strip()
    if not nombre:
        raise HTTPException(400, "Falta el nombre")
    with db() as conn:
        pos = conn.execute("SELECT COALESCE(MAX(posicion),-1)+1 FROM columns WHERE user_id=?",
                           (user["id"],)).fetchone()[0]
        cur = conn.execute("INSERT INTO columns (user_id, nombre, posicion) VALUES (?,?,?)",
                           (user["id"], nombre, pos))
        return dict(conn.execute("SELECT * FROM columns WHERE id=?", (cur.lastrowid,)).fetchone())


@app.patch("/api/columns/{cid}")
async def api_col_editar(cid: int, request: Request):
    user = api_user(request)
    p = await request.json()
    with db() as conn:
        _propia(conn, "columns", cid, user["id"])
        if "nombre" in p and (p["nombre"] or "").strip():
            conn.execute("UPDATE columns SET nombre=? WHERE id=?", (p["nombre"].strip(), cid))
        if "es_hecho" in p:
            conn.execute("UPDATE columns SET es_hecho=? WHERE id=?", (1 if p["es_hecho"] else 0, cid))
        return dict(conn.execute("SELECT * FROM columns WHERE id=?", (cid,)).fetchone())


@app.post("/api/columns/reorder")
async def api_col_reordenar(request: Request):
    user = api_user(request)
    p = await request.json()
    with db() as conn:
        for i, cid in enumerate(p.get("orden", [])):
            conn.execute("UPDATE columns SET posicion=? WHERE id=? AND user_id=?",
                         (i, int(cid), user["id"]))
    return {"ok": True}


@app.delete("/api/columns/{cid}")
def api_col_borrar(cid: int, request: Request):
    user = api_user(request)
    with db() as conn:
        _propia(conn, "columns", cid, user["id"])
        n = conn.execute("SELECT COUNT(*) FROM tasks WHERE column_id=? AND estado != 'archivada'",
                         (cid,)).fetchone()[0]
        if n:
            raise HTTPException(400, "La columna no está vacía")
        conn.execute("DELETE FROM columns WHERE id=?", (cid,))
    return {"ok": True}


@app.post("/api/projects")
async def api_proj_crear(request: Request):
    user = api_user(request)
    p = await request.json()
    nombre = (p.get("nombre") or "").strip()
    if not nombre:
        raise HTTPException(400, "Falta el nombre")
    with db() as conn:
        cur = conn.execute("INSERT INTO projects (user_id, nombre, color) VALUES (?,?,?)",
                           (user["id"], nombre, p.get("color") or "#FF8080"))
        return dict(conn.execute("SELECT * FROM projects WHERE id=?", (cur.lastrowid,)).fetchone())


@app.patch("/api/projects/{pid}")
async def api_proj_editar(pid: int, request: Request):
    user = api_user(request)
    p = await request.json()
    with db() as conn:
        _propia(conn, "projects", pid, user["id"])
        for campo in ("nombre", "color", "archivado"):
            if campo in p:
                conn.execute(f"UPDATE projects SET {campo}=? WHERE id=?", (p[campo], pid))
        return dict(conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())


# ---------------------------------------------------------------- Google Calendar

def _gcal_token(user: sqlite3.Row) -> str | None:
    rt = dec(user["gcal_refresh_token"])
    if not rt:
        return None
    return gcal.access_token(user["id"], rt)


def _oauth_redirect_uri() -> str:
    return f"{BASE_URL}/oauth/gcal/callback"


def _oauth_state(email: str) -> str:
    payload = f"oauth|{email}|{int(time.time())}"
    return f"{payload}|{_sign(payload)}"


def _oauth_state_ok(state: str, email: str) -> bool:
    parts = state.split("|")
    if len(parts) != 4 or parts[0] != "oauth" or parts[1] != email:
        return False
    if not hmac.compare_digest(parts[3], _sign("|".join(parts[:3]))):
        return False
    return int(parts[2]) > time.time() - 600


@app.get("/oauth/gcal/start")
def oauth_start(request: Request):
    user = read_session(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if not gcal.CLIENT_ID:
        return RedirectResponse("/ajustes?error=Falta configurar GOOGLE_CLIENT_ID en el servidor",
                                status_code=302)
    return RedirectResponse(
        gcal.auth_redirect_url(_oauth_redirect_uri(), _oauth_state(user["email"])), status_code=302)


@app.get("/oauth/gcal/callback")
def oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    user = read_session(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if error:
        return RedirectResponse(f"/ajustes?error=Google devolvió: {error}", status_code=302)
    if not code or not _oauth_state_ok(state, user["email"]):
        return RedirectResponse("/ajustes?error=Autorización no válida, inténtalo de nuevo",
                                status_code=302)
    try:
        tokens = gcal.exchange_code(code, _oauth_redirect_uri())
    except gcal.GcalError as exc:
        return RedirectResponse(f"/ajustes?error={str(exc)[:150]}", status_code=302)
    refresh = tokens.get("refresh_token", "")
    if not refresh:
        return RedirectResponse("/ajustes?error=Google no devolvió permiso permanente — "
                                "inténtalo de nuevo", status_code=302)
    with db() as conn:
        conn.execute("UPDATE users SET gcal_refresh_token=?, gcal_auth_kind='oauth_web' WHERE id=?",
                     (enc(refresh), user["id"]))
    gcal.olvidar_cache(user["id"])
    return RedirectResponse("/ajustes?ok=Google Calendar conectado", status_code=302)


@app.post("/oauth/gcal/disconnect")
def oauth_disconnect(request: Request):
    user = read_session(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    rt = dec(user["gcal_refresh_token"])
    if rt:
        gcal.revoke(rt)
    with db() as conn:
        conn.execute("UPDATE users SET gcal_refresh_token='', gcal_auth_kind='' WHERE id=?",
                     (user["id"],))
    gcal.olvidar_cache(user["id"])
    return RedirectResponse("/ajustes?ok=Google Calendar desconectado", status_code=302)


@app.get("/api/gcal/status")
def api_gcal_status(request: Request):
    user = api_user(request)
    return {"conectado": bool(user["gcal_refresh_token"]),
            "calendar_id": user["gcal_calendar_id"]}


@app.get("/api/gcal/calendars")
def api_gcal_calendars(request: Request):
    user = api_user(request)
    token = _gcal_token(user)
    if not token:
        raise HTTPException(400, "Google Calendar no está conectado")
    return {"calendars": gcal.calendar_list(token), "actual": user["gcal_calendar_id"]}


@app.post("/ajustes/calendar")
def ajustes_calendar(request: Request, calendar_id: str = Form(...)):
    user = read_session(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    with db() as conn:
        conn.execute("UPDATE users SET gcal_calendar_id=? WHERE id=?",
                     (calendar_id.strip() or "primary", user["id"]))
    return RedirectResponse("/ajustes?ok=Calendario guardado", status_code=302)


# ---------------------------------------------------------------- API semana

def _sync_semana(user: sqlite3.Row, lunes: date) -> tuple[list[dict], bool, str]:
    """Pull de Google: devuelve (eventos_google, sincronizado, aviso) y
    reconcilia la tabla blocks (Google manda en fechas/horas)."""
    token = None
    try:
        token = _gcal_token(user)
    except gcal.TokenRevocado:
        return [], False, "El acceso a Google Calendar caducó — reconéctalo en Ajustes"
    except gcal.GcalError as exc:
        return [], False, f"Sin sincronizar: {str(exc)[:120]}"
    if not token:
        return [], False, "Google Calendar no está conectado (Ajustes)"

    cal = user["gcal_calendar_id"] or "primary"
    ini = datetime(lunes.year, lunes.month, lunes.day, tzinfo=TZ)
    fin = ini + timedelta(days=7)
    try:
        eventos = gcal.events_list(token, cal, ini.isoformat(), fin.isoformat())
    except gcal.GcalError as exc:
        return [], False, f"Sin sincronizar: {str(exc)[:120]}"

    por_id = {ev["id"]: ev for ev in eventos}
    with db() as conn:
        bloques = conn.execute(
            "SELECT * FROM blocks WHERE user_id=? AND estado='ok' AND gcal_calendar_id=?",
            (user["id"], cal)).fetchall()
        for b in bloques:
            b_ini = parse_gcal_dt(b["start"])
            en_ventana = ini <= b_ini < fin
            ev = por_id.get(b["gcal_event_id"])
            if ev is None and en_ventana:
                try:
                    ev = gcal.event_get(token, cal, b["gcal_event_id"])
                except gcal.GcalError:
                    continue
                if ev is None or ev.get("status") == "cancelled":
                    conn.execute("UPDATE blocks SET estado='cancelado', "
                                 "last_synced=datetime('now') WHERE id=?", (b["id"],))
                    continue
            if ev is None:
                continue
            if ev.get("status") == "cancelled":
                conn.execute("UPDATE blocks SET estado='cancelado', "
                             "last_synced=datetime('now') WHERE id=?", (b["id"],))
                continue
            nuevo_ini = ev["start"].get("dateTime")
            nuevo_fin = ev["end"].get("dateTime")
            if nuevo_ini and (nuevo_ini != b["start"] or nuevo_fin != b["end"]
                              or ev.get("etag", "") != b["etag"]):
                conn.execute(
                    "UPDATE blocks SET start=?, end=?, etag=?, gcal_updated=?, "
                    "last_synced=datetime('now') WHERE id=?",
                    (nuevo_ini, nuevo_fin, ev.get("etag", ""), ev.get("updated", ""), b["id"]))
    return eventos, True, ""


@app.get("/api/week")
def api_week(request: Request, start: str = ""):
    user = api_user(request)
    hoy = datetime.now(TZ).date()
    lunes = lunes_de(date.fromisoformat(start)) if start else lunes_de(hoy)
    eventos_google, sincronizado, aviso = _sync_semana(user, lunes)

    with db() as conn:
        bloques = {b["gcal_event_id"]: dict(b) for b in conn.execute(
            "SELECT * FROM blocks WHERE user_id=? AND estado='ok'", (user["id"],))}
        tareas = conn.execute(
            "SELECT t.*, c.es_hecho, c.posicion col_pos, p.color, p.nombre proyecto "
            "FROM tasks t JOIN columns c ON c.id = t.column_id "
            "LEFT JOIN projects p ON p.id = t.project_id "
            "WHERE t.user_id=? AND t.estado='abierta' AND c.es_hecho=0 "
            "ORDER BY c.posicion DESC, t.posicion", (user["id"],)).fetchall()
        info_tarea = {t["id"]: t for t in tareas}
        hechas = {r["id"] for r in conn.execute(
            "SELECT id FROM tasks WHERE user_id=? AND estado='hecha'", (user["id"],))}

    salida = []
    for ev in eventos_google:
        if ev.get("status") == "cancelled":
            continue
        b = bloques.get(ev["id"])
        if ev["start"].get("date"):  # evento de día completo
            d0 = date.fromisoformat(ev["start"]["date"])
            d1 = date.fromisoformat(ev["end"]["date"])
            d = d0
            while d < d1:
                salida.append({"tipo": "dia_completo", "dia": d.isoformat(),
                               "titulo": ev.get("summary", "(sin título)")})
                d += timedelta(days=1)
            continue
        dia_e, min_ini = dia_y_min(parse_gcal_dt(ev["start"]["dateTime"]))
        dia_f, min_fin = dia_y_min(parse_gcal_dt(ev["end"]["dateTime"]))
        if dia_f != dia_e:
            min_fin = 24 * 60
        item = {"dia": dia_e, "min_ini": min_ini, "min_fin": max(min_fin, min_ini + 15),
                "titulo": ev.get("summary", "(sin título)")}
        if b:
            t = info_tarea.get(b["task_id"])
            item.update({"tipo": "bloque", "block_id": b["id"], "task_id": b["task_id"],
                         "color": (t["color"] if t and t["color"] else "#FF8080"),
                         "hecha": b["task_id"] in hechas})
        else:
            item["tipo"] = "ocupado"
        salida.append(item)

    planificadas = {b["task_id"] for b in bloques.values()
                    if parse_gcal_dt(b["start"]) > datetime.now(TZ)}
    bandeja = [{"id": t["id"], "titulo": t["titulo"], "estimado_min": t["estimado_min"],
                "fecha_limite": t["fecha_limite"], "color": t["color"] or "#B5B5B5",
                "proyecto": t["proyecto"] or "", "planificada": t["id"] in planificadas}
               for t in tareas]

    dias = [{"fecha": (lunes + timedelta(days=i)).isoformat(),
             "label": ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"][i]
             + f" {(lunes + timedelta(days=i)).day}"} for i in range(7)]
    ahora = datetime.now(TZ)
    return {"lunes": lunes.isoformat(), "dias": dias, "hoy": hoy.isoformat(),
            "ahora_min": ahora.hour * 60 + ahora.minute, "eventos": salida,
            "bandeja": bandeja, "sincronizado": sincronizado, "aviso": aviso}


# ---------------------------------------------------------------- API bloques

def _crear_evento_body(user, tarea, dia: str, min_ini: int, dur_min: int) -> dict:
    desc = (tarea["notas"] + "\n\n" if tarea["notas"] else "") + f"{BASE_URL}/tablero#task-{tarea['id']}"
    return {
        "summary": tarea["titulo"],
        "description": desc,
        "start": {"dateTime": rfc3339(dia, min_ini), "timeZone": "Europe/Madrid"},
        "end": {"dateTime": rfc3339(dia, min_ini + dur_min), "timeZone": "Europe/Madrid"},
        "extendedProperties": {"private": {"app": "tareas", "task_id": str(tarea["id"]),
                                           "user_id": str(user["id"])}},
    }


@app.post("/api/blocks")
async def api_block_crear(request: Request):
    user = api_user(request)
    p = await request.json()
    token = _gcal_token(user)
    if not token:
        raise HTTPException(400, "Conecta Google Calendar en Ajustes antes de planificar")
    with db() as conn:
        tarea = _propia(conn, "tasks", int(p["task_id"]), user["id"])
    dia, min_ini = p["dia"], int(p["min_ini"])
    dur = max(15, int(p.get("dur_min") or tarea["estimado_min"] or 30))
    cal = user["gcal_calendar_id"] or "primary"
    try:
        ev = gcal.event_insert(token, cal, _crear_evento_body(user, tarea, dia, min_ini, dur))
    except gcal.TokenRevocado:
        raise HTTPException(400, "El acceso a Google Calendar caducó — reconéctalo en Ajustes")
    except gcal.GcalError as exc:
        raise HTTPException(502, f"Google Calendar rechazó el evento: {str(exc)[:150]}")
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO blocks (user_id, task_id, gcal_event_id, gcal_calendar_id, start, end, "
            "etag, gcal_updated) VALUES (?,?,?,?,?,?,?,?)",
            (user["id"], tarea["id"], ev["id"], cal, ev["start"]["dateTime"],
             ev["end"]["dateTime"], ev.get("etag", ""), ev.get("updated", "")))
        bid = cur.lastrowid
    return {"block_id": bid, "task_id": tarea["id"], "dia": dia,
            "min_ini": min_ini, "min_fin": min_ini + dur}


@app.patch("/api/blocks/{bid}")
async def api_block_editar(bid: int, request: Request):
    user = api_user(request)
    p = await request.json()
    token = _gcal_token(user)
    if not token:
        raise HTTPException(400, "Google Calendar no está conectado")
    with db() as conn:
        b = _propia(conn, "blocks", bid, user["id"])
    actual_ini = parse_gcal_dt(b["start"])
    actual_fin = parse_gcal_dt(b["end"])
    dia = p.get("dia") or actual_ini.date().isoformat()
    min_ini = int(p["min_ini"]) if "min_ini" in p else actual_ini.hour * 60 + actual_ini.minute
    dur = int(p["dur_min"]) if "dur_min" in p else int((actual_fin - actual_ini).total_seconds() // 60)
    dur = max(15, dur)
    body = {"start": {"dateTime": rfc3339(dia, min_ini), "timeZone": "Europe/Madrid"},
            "end": {"dateTime": rfc3339(dia, min_ini + dur), "timeZone": "Europe/Madrid"}}
    try:
        ev = gcal.event_patch(token, b["gcal_calendar_id"], b["gcal_event_id"], body)
    except gcal.GcalError as exc:
        raise HTTPException(502, f"No se pudo mover el evento: {str(exc)[:150]}")
    if ev is None:
        with db() as conn:
            conn.execute("UPDATE blocks SET estado='cancelado' WHERE id=?", (bid,))
        raise HTTPException(409, "El evento ya no existe en Google Calendar")
    with db() as conn:
        conn.execute("UPDATE blocks SET start=?, end=?, etag=?, gcal_updated=?, "
                     "last_synced=datetime('now') WHERE id=?",
                     (ev["start"]["dateTime"], ev["end"]["dateTime"], ev.get("etag", ""),
                      ev.get("updated", ""), bid))
    return {"block_id": bid, "dia": dia, "min_ini": min_ini, "min_fin": min_ini + dur}


@app.delete("/api/blocks/{bid}")
def api_block_borrar(bid: int, request: Request):
    user = api_user(request)
    token = _gcal_token(user)
    with db() as conn:
        b = _propia(conn, "blocks", bid, user["id"])
    if token:
        try:
            gcal.event_delete(token, b["gcal_calendar_id"], b["gcal_event_id"])
        except gcal.GcalError:
            pass
    with db() as conn:
        conn.execute("DELETE FROM blocks WHERE id=?", (bid,))
    return {"ok": True}


# ---------------------------------------------------------------- back office

@app.get("/admin")
def admin(request: Request, ok: str = "", error: str = ""):
    user = read_session(request)
    if not es_admin(user):
        return RedirectResponse("/login", status_code=302)
    with db() as conn:
        usuarios = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        stats = {r["user_id"]: r["n"] for r in conn.execute(
            "SELECT user_id, COUNT(*) n FROM tasks WHERE estado != 'archivada' GROUP BY user_id")}
    return render(request, "admin.html", user=user, ok=ok, error=error,
                  usuarios=usuarios, stats=stats, alta_codigo=ALTA_CODIGO, base_url=BASE_URL)


@app.post("/admin/crear")
def admin_crear(request: Request, nombre: str = Form(...), email: str = Form(...),
                password: str = Form(...)):
    if not es_admin(read_session(request)):
        return RedirectResponse("/login", status_code=302)
    email = email.strip().lower()
    if user_by("email", email):
        return RedirectResponse("/admin?error=Ya existe una cuenta con ese email", status_code=302)
    crear_usuario(email, nombre, password)
    return RedirectResponse(f"/admin?ok=Usuario {email} creado", status_code=302)


@app.post("/admin/toggle")
def admin_toggle(request: Request, uid: int = Form(...)):
    if not es_admin(read_session(request)):
        return RedirectResponse("/login", status_code=302)
    with db() as conn:
        conn.execute("UPDATE users SET activo = 1 - activo WHERE id=?", (uid,))
    return RedirectResponse("/admin?ok=Estado cambiado", status_code=302)


@app.post("/admin/borrar")
def admin_borrar(request: Request, uid: int = Form(...)):
    user = read_session(request)
    if not es_admin(user):
        return RedirectResponse("/login", status_code=302)
    if user["id"] == uid:
        return RedirectResponse("/admin?error=No puedes borrarte a ti mismo", status_code=302)
    with db() as conn:
        conn.execute("DELETE FROM blocks WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM checklist WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM tasks WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM columns WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM projects WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
    return RedirectResponse("/admin?ok=Usuario borrado", status_code=302)
