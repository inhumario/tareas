"""
Cliente REST de Google Calendar — app «Tareas» (Inhumario)
==========================================================

Sin google-api-python-client: solo requests contra la API v3.
El refresh token es por usuario (viene descifrado desde app.py); el access
token se cachea en memoria hasta 60 s antes de su caducidad.

Variables de entorno:
  GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET — OAuth client tipo Web
"""

import os
import time
import urllib.parse

import requests

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
API = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar"

_access_cache: dict[int, tuple[str, float]] = {}  # user_id -> (access_token, expiry)


class GcalError(Exception):
    pass


class TokenRevocado(GcalError):
    """El refresh token ya no vale (usuario revocó el acceso)."""


# ---------------------------------------------------------------- OAuth web

def auth_redirect_url(redirect_uri: str, state: str) -> str:
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_code(code: str, redirect_uri: str) -> dict:
    """Cambia el code del callback por tokens. Devuelve el JSON de Google."""
    r = requests.post(TOKEN_URL, data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }, timeout=15)
    if r.status_code != 200:
        raise GcalError(f"Intercambio de código fallido ({r.status_code}): {r.text[:200]}")
    return r.json()


def revoke(refresh_token: str) -> None:
    try:
        requests.post("https://oauth2.googleapis.com/revoke",
                      params={"token": refresh_token}, timeout=10)
    except requests.RequestException:
        pass  # si falla la revocación remota, el token local se borra igual


# ---------------------------------------------------------------- access token

def access_token(user_id: int, refresh_token: str) -> str:
    cached = _access_cache.get(user_id)
    if cached and cached[1] > time.time():
        return cached[0]
    r = requests.post(TOKEN_URL, data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }, timeout=15)
    if r.status_code != 200:
        if "invalid_grant" in r.text:
            raise TokenRevocado("El acceso a Google Calendar ha caducado o fue revocado")
        raise GcalError(f"Refresh de token fallido ({r.status_code}): {r.text[:200]}")
    data = r.json()
    tok = data["access_token"]
    _access_cache[user_id] = (tok, time.time() + int(data.get("expires_in", 3600)) - 60)
    return tok


def olvidar_cache(user_id: int) -> None:
    _access_cache.pop(user_id, None)


# ---------------------------------------------------------------- llamadas API

def _call(method: str, path: str, token: str, **kwargs):
    r = requests.request(method, API + path, timeout=20,
                         headers={"Authorization": f"Bearer {token}"}, **kwargs)
    if r.status_code == 401:
        raise TokenRevocado("Google rechazó el token de acceso")
    if r.status_code == 410:
        raise GcalError("gone")
    if r.status_code >= 400 and r.status_code != 404:
        raise GcalError(f"Google Calendar {method} {path} → {r.status_code}: {r.text[:200]}")
    if r.status_code == 404:
        return None
    return r.json() if r.content else {}


def events_list(token: str, calendar_id: str, time_min: str, time_max: str) -> list[dict]:
    items, page = [], None
    while True:
        params = {"timeMin": time_min, "timeMax": time_max, "singleEvents": "true",
                  "orderBy": "startTime", "maxResults": 250, "showDeleted": "false"}
        if page:
            params["pageToken"] = page
        data = _call("GET", f"/calendars/{urllib.parse.quote(calendar_id)}/events",
                     token, params=params)
        items += data.get("items", [])
        page = data.get("nextPageToken")
        if not page:
            return items


def event_get(token: str, calendar_id: str, event_id: str) -> dict | None:
    return _call("GET", f"/calendars/{urllib.parse.quote(calendar_id)}/events/{event_id}", token)


def event_insert(token: str, calendar_id: str, body: dict) -> dict:
    return _call("POST", f"/calendars/{urllib.parse.quote(calendar_id)}/events", token, json=body)


def event_patch(token: str, calendar_id: str, event_id: str, body: dict) -> dict | None:
    return _call("PATCH", f"/calendars/{urllib.parse.quote(calendar_id)}/events/{event_id}",
                 token, json=body)


def event_delete(token: str, calendar_id: str, event_id: str) -> None:
    _call("DELETE", f"/calendars/{urllib.parse.quote(calendar_id)}/events/{event_id}", token)


def calendar_list(token: str) -> list[dict]:
    data = _call("GET", "/users/me/calendarList", token, params={"maxResults": 100})
    return [{"id": c["id"], "nombre": c.get("summary", c["id"]), "primario": c.get("primary", False)}
            for c in data.get("items", []) if c.get("accessRole") in ("owner", "writer")]
