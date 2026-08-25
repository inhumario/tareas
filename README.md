# Tareas — tablero kanban + time blocking (Inhumario)

Tablero de tareas tipo Trello combinado con Google Calendar: las tarjetas cambian de
estado en `/tablero` y en `/semana` se arrastran a huecos del calendario, creando
**eventos reales** en el Google Calendar del usuario (time blocking).

- Multi-usuario: alta con código de invitación; cada usuario conecta su propio
  Google Calendar por OAuth web. Refresh tokens cifrados en reposo (Fernet).
- Sin build step: FastAPI + Jinja2 + SQLite + vanilla JS (SortableJS vendorizado).
- Producción: https://tareas.inhumario.com (EasyPanel `travelia/tareas`, volumen `/data`).

## Variables de entorno

| Variable | Uso |
|---|---|
| `SECRET_KEY` | firma de cookies de sesión |
| `ENCRYPT_KEY` | clave Fernet (cifra refresh tokens) — **si se pierde, los usuarios deben reconectar su calendar** |
| `ALTA_CODIGO` | código de invitación del alta |
| `ADMIN_EMAIL` | acceso a /admin |
| `DB_PATH` | sqlite (`/data/tareas.db` en prod) |
| `BASE_URL` | URL pública (redirect URI OAuth = `BASE_URL/oauth/gcal/callback`) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth client **tipo Web** con Calendar API habilitada |

Secretos en Infisical, carpeta `tareas`.

## Desarrollo local

```bash
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```

Sin `SECRET_KEY`/`ENCRYPT_KEY` se generan efímeras (solo desarrollo). Para probar el
OAuth en local, añade `http://localhost:8000/oauth/gcal/callback` como redirect URI
del client de Google y exporta `BASE_URL=http://localhost:8000`.

## Deploy

Push a `main` **no** redespliega: llamar a `services.app.deployService` de EasyPanel
(tRPC, token en `~/.config/aromas/easypanel.env`).
