from datetime import datetime, date, timedelta
import zoneinfo
import re
import os
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, Any
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

ENV_CONFIG = {
    "NOTION_TOKEN": os.getenv("NOTION_TOKEN", ""),
    "HOSTINGER_TOKEN": os.getenv("HOSTINGER_TOKEN", "")
}

DB_SOL = "53f757ce-a190-4eea-9cf5-d03b107f04e6"
DB_RES = "f0129517-1439-40e7-8130-931bfb1278ea"
DB_SES = "7dc65dcfd88c4da2b8a1c5fa1ef8e777"

HOSTINGER_MAILBOX = "AC76aebfc4d60a270d918b4549d4cd"

TZ = zoneinfo.ZoneInfo("Europe/Madrid")
NV = "2022-06-28"


# =========================================================
# NOTION
# =========================================================

async def notion(url: str, env: dict, body: dict = None) -> dict:
    headers = {
        "Authorization": "Bearer " + env.get("NOTION_TOKEN", ""),
        "Notion-Version": NV,
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        r = await client.post("https://api.notion.com/v1/" + url, headers=headers, json=body or {})
    
    try:
        data = r.json() if r.text else {}
    except Exception:
        data = {"raw": r.text}

    if not (200 <= r.status_code < 300):
        raise HTTPException(
            status_code=r.status_code, 
            detail="Notion " + str(r.status_code) + " " + (data.get("message") or data.get("raw") or r.text or "Error desconocido")
        )
    return data


async def query_all(database: str, env: dict, body: dict = None) -> list:
    results = []
    cursor = None
    while True:
        b = {**(body or {}), "page_size": 100}
        if cursor:
            b["start_cursor"] = cursor
        
        data = await notion("databases/" + database + "/query", env, b)
        results.extend(data.get("results", []))
        
        cursor = data.get("next_cursor") if data.get("has_more") else None
        if not cursor:
            break
    return results


# =========================================================
# PROPIEDADES NOTION
# =========================================================

def prop(p: Any) -> str:
    if not p:
        return ""
    if isinstance(p.get("title"), list) and len(p["title"]) > 0:
        return p["title"][0].get("plain_text", "")
    if isinstance(p.get("rich_text"), list) and len(p["rich_text"]) > 0:
        return p["rich_text"][0].get("plain_text", "")
    if p.get("select"):
        return p["select"].get("name", "")
    if p.get("status"):
        return p["status"].get("name", "")
    if p.get("phone_number"):
        return p["phone_number"]
    if p.get("number") is not None:
        return str(p["number"])
    return ""


def num(p: Any) -> float:
    if not p or p.get("number") is None:
        return 0
    return p["number"]


def phone(p: Any) -> str:
    if not p or not p.get("phone_number"):
        return ""
    return p["phone_number"]


def date_prop(p: Any) -> str:
    if not p or not p.get("date") or not p["date"].get("start"):
        return ""
    return p["date"]["start"]


def hora(p: Any) -> str:
    if not p:
        return ""
    val = ""
    if p.get("date") and p["date"].get("start"):
        val = p["date"]["start"]
    elif isinstance(p.get("rich_text"), list) and len(p["rich_text"]) > 0:
        val = p["rich_text"][0].get("plain_text", "")
    elif isinstance(p.get("title"), list) and len(p["title"]) > 0:
        val = p["title"][0].get("plain_text", "")
    
    if not val:
        return ""
    
    # Si viene en formato ISO con hora (ej. 2026-06-01T17:00:00Z o con offset), ajustarlo a la zona horaria TZ de Madrid
    try:
        if "T" in val or ":" in val:
            if not val.endswith("Z") and "+" not in val and "-" not in val[10:]:
                val += "Z"
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt.astimezone(TZ).strftime("%H:%M")
    except Exception:
        pass
    
    return val


# =========================================================
# FORMATO FECHA / HORA
# =========================================================

def fmt_date(value: str) -> str:
    if not value:
        return ""
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
            d = datetime.fromisoformat(value + "T12:00:00").replace(tzinfo=TZ)
        else:
            d = datetime.fromisoformat(value).astimezone(TZ)
        
        days = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        return f"{days[d.weekday()]} {d.strftime('%d/%m/%Y')}"
    except Exception:
        return ""


def fmt_time(value: str) -> str:
    if not value:
        return ""
    if re.match(r"^\d{1,2}:\d{2}$", value):
        p = value.split(":")
        return p[0].zfill(2) + ":" + p[1]
    try:
        d = datetime.fromisoformat(value).astimezone(TZ)
        return d.strftime("%H:%M")
    except Exception:
        return ""


def sort_key(x: dict) -> str:
    f = x.get("fecha") or "9999-12-31"
    h = x.get("hora") or "23:59"
    if re.match(r"^\d{1,2}:\d{2}$", str(h)):
        p = str(h).split(":")
        h = p[0].zfill(2) + ":" + p[1]
    else:
        try:
            d = datetime.fromisoformat(str(h)).astimezone(TZ)
            h = d.strftime("%H:%M")
        except Exception:
            h = "23:59"
    return f"{f}T{h}"


def wa(tel: str, msg: str) -> str:
    if not tel:
        return ""
    n = re.sub(r"\D", "", str(tel))
    if not n:
        return ""
    import urllib.parse
    return "https://wa.me/" + n + "?text=" + urllib.parse.quote(msg)


# =========================================================
# SOLICITUDES, RESERVAS Y SESIONES
# =========================================================

async def solicitudes(env: dict):
    data = await query_all(
        DB_SOL, env,
        {
            "filter": {"property": "Estado", "select": {"equals": "PENDIENTE"}},
            "sorts": [{"property": "Fecha", "direction": "ascending"}, {"property": "Hora", "direction": "ascending"}]
        }
    )
    res = []
    for x in data:
        p = x.get("properties", {})
        nombre = prop(p.get("Nombre"))
        fecha = date_prop(p.get("Fecha"))
        hora_valor = hora(p.get("Hora"))
        jugadores = num(p.get("Jugadores"))
        version = prop(p.get("Versión"))
        tel = phone(p.get("Teléfono"))
        
        fecha_texto = fmt_date(fecha)
        hora_texto = fmt_time(hora_valor)
        
        mensaje = (
            "Hola, escribo de Fugarte Escape Room.\n\n"
            "Tenemos disponibilidad el " + (fecha_texto or "día solicitado") +
            " a las " + (hora_texto or "hora solicitada") +
            " para " + str(jugadores) + " personas.\n\n"
            "Si queréis confirmar la reserva, sería necesario hacer un Bizum de 10 € a este número de teléfono.\n\n"
            "Una vez recibido, dejamos la reserva confirmada.\n\n"
            "Si finalmente no estáis interesados, por favor contestad a este WhatsApp para que podamos ofrecer el horario a otros usuarios.\n\n"
            "¡Muchas gracias! 😊"
        )
        res.append({
            "id": x["id"], "nombre": nombre, "fecha": fecha, "fechaTexto": fecha_texto,
            "hora": hora_valor, "horaTexto": hora_texto, "jugadores": jugadores,
            "version": version, "telefono": tel, "whatsapp": wa(tel, mensaje)
        })
    return sorted(res, key=sort_key)


async def reservas(env: dict):
    hoy = datetime.now(TZ).strftime("%Y-%m-%d")
    data = await query_all(
        DB_RES, env,
        {
            "filter": {
                "and": [
                    {"property": "Estado", "select": {"equals": "CONFIRMADA"}},
                    {"property": "Pago", "select": {"equals": "PENDIENTE"}},
                    {"property": "Fecha", "date": {"on_or_after": hoy}}
                ]
            },
            "sorts": [{"property": "Fecha", "direction": "ascending"}, {"property": "Hora", "direction": "ascending"}]
        }
    )
    res = []
    for x in data:
        p = x.get("properties", {})
        fecha = date_prop(p.get("Fecha"))
        hora_valor = hora(p.get("Hora"))
        res.append({
            "id": x["id"], "nombre": prop(p.get("Nombre")), "fecha": fecha,
            "fechaTexto": fmt_date(fecha), "hora": hora_valor, "horaTexto": fmt_time(hora_valor),
            "jugadores": num(p.get("Jugadores")), "version": prop(p.get("Versión")),
            "estado": prop(p.get("Estado")), "pago": prop(p.get("Pago")), "telefono": phone(p.get("Teléfono"))
        })
    return sorted(res, key=sort_key)


async def sesiones_realizadas(env: dict):
    data = await query_all(DB_SES, env, {"sorts": [{"property": "Fecha", "direction": "descending"}, {"property": "Hora", "direction": "descending"}]})
    res = []
    for x in data:
        p = x.get("properties", {})
        fecha = date_prop(p.get("Fecha"))
        hora_valor = hora(p.get("Hora"))
        res.append({
            "id": x["id"], "nombre": prop(p.get("Nombre")), "fecha": fecha,
            "fechaTexto": fmt_date(fecha), "hora": hora_valor, "horaTexto": fmt_time(hora_valor),
            "jugadores": num(p.get("Jugadores")), "version": prop(p.get("Versión")),
            "origen": prop(p.get("Origen")), "pago": prop(p.get("Pago"))
        })
    return res


# =========================================================
# HOSTINGER API & EXTRACCIÓN DE CORREOS
# =========================================================

async def hostinger_get(path: str, env: dict) -> dict:
    headers = {
        "Authorization": "Bearer " + env.get("HOSTINGER_TOKEN", ""),
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        r = await client.get("https://api.mail.hostinger.com" + path, headers=headers)
    try:
        data = r.json() if r.text else {}
    except Exception:
        raise HTTPException(status_code=500, detail="Hostinger no devolvió JSON válido")
    if not (200 <= r.status_code < 300):
        raise HTTPException(status_code=r.status_code, detail="Hostinger error: " + r.text)
    return data


def extract_date_from_mail(text: str) -> str:
    m = re.search(r"\b(20\d{2})[-\/](\d{1,2})[-\/](\d{1,2})\b", text)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
    m = re.search(r"\b(\d{1,2})[\/-](\d{1,2})[\/-](20\d{2})\b", text)
    if m:
        return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
    return ""


def extract_time_from_mail(text: str) -> str:
    m = re.search(r"\b([01]?\d|2[0-3])\s*[:hH\.]\s*([0-5]\d)\b", text)
    if m:
        return f"{m.group(1).zfill(2)}:{m.group(2)}"
    m = re.search(r"\b([01]?\d|2[0-3])\s*h(?:oras?)?\b", text, re.IGNORECASE)
    if m:
        return f"{m.group(1).zfill(2)}:00"
    return ""


def extract_players_from_mail(text: str) -> Optional[int]:
    patterns = [
        r"(?:n[º°o]\s*)?(?:personas?|jugadores?|participantes?|pax)\s*[:\-]?\s*(\d{1,2})",
        r"(\d{1,2})\s*(?:personas?|jugadores?|pax)"
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


async def revisar_correo_logic(env: dict):
    if not env.get("HOSTINGER_TOKEN"):
        raise HTTPException(status_code=400, detail="Falta el Token de Hostinger.")
    
    inbox = await hostinger_get("/api/v1/mailboxes/" + HOSTINGER_MAILBOX + "/folders/INBOX/messages?page=1&perPage=50", env)
    messages = inbox.get("messages", []) or inbox.get("data", []) or inbox.get("results", [])
    
    if not isinstance(messages, list) or not messages:
        return []

    actions = []
    for msg in messages:
        uid = str(msg.get("uid") or msg.get("id") or "")
        if not uid:
            continue
        
        detail = await hostinger_get("/api/v1/mailboxes/" + HOSTINGER_MAILBOX + "/folders/INBOX/messages/" + uid + "/text", env)
        subject = msg.get("subject") or detail.get("subject") or ""
        text = subject + "\n" + str(detail.get("text") or detail.get("body") or "")

        fecha = extract_date_from_mail(text)
        hora_solicitada = extract_time_from_mail(text)
        jugadores = extract_players_from_mail(text)

        if not fecha or jugadores is None:
            continue

        actions.append({
            "type": "CREATED" if hora_solicitada else "REVIEW",
            "uid": uid,
            "subject": subject,
            "fecha": fecha,
            "hora": hora_solicitada,
            "jugadores": jugadores,
            "reason": "Correo analizado correctamente desde Hostinger."
        })
        break

    return actions


# =========================================================
# ACCIONES DE NOTION Y ENDPOINTS API
# =========================================================

async def notion_update_page(page_id: str, env: dict, properties: dict):
    async with httpx.AsyncClient() as client:
        await client.patch(
            "https://api.notion.com/v1/pages/" + page_id,
            headers={"Authorization": "Bearer " + env.get("NOTION_TOKEN", ""), "Notion-Version": NV, "Content-Type": "application/json"},
            json={"properties": properties}
        )

@app.get("/api/health")
async def health():
    return {"ok": True}

@app.get("/api/solicitudes")
async def api_solicitudes():
    return await solicitudes(ENV_CONFIG)

@app.get("/api/reservas")
async def api_reservas():
    return await reservas(ENV_CONFIG)

@app.get("/api/sesiones")
async def api_sesiones():
    return await sesiones_realizadas(ENV_CONFIG)

class ActionRequest(BaseModel):
    reservationId: Optional[str] = None
    requestId: Optional[str] = None

@app.post("/api/sesion-realizada")
async def api_sesion_realizada(body: ActionRequest):
    await notion_update_page(body.reservationId, ENV_CONFIG, {"Pago": {"select": {"name": "RECIBIDO"}}})
    return {"ok": True, "alreadyExists": False}

@app.post("/api/confirmar-solicitud")
async def api_confirmar_solicitud(body: ActionRequest):
    await notion_update_page(body.requestId, ENV_CONFIG, {"Estado": {"select": {"name": "CONFIRMADA"}}})
    return {"ok": True}

@app.post("/api/rechazar-solicitud")
async def api_rechazar_solicitud(body: ActionRequest):
    await notion_update_page(body.requestId, ENV_CONFIG, {"Estado": {"select": {"name": "CANCELADA"}}})
    return {"ok": True}

@app.post("/api/revisar-correo")
async def api_revisar_correo():
    actions = await revisar_correo_logic(ENV_CONFIG)
    return {"ok": True, "actions": actions}


# =========================================================
# HTML FRONTEND (PANEL)
# =========================================================

@app.get("/", response_class=HTMLResponse)
async def panel():
    return """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fugarte - Panel</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:Arial,sans-serif;background:#f4f5f7;color:#222}
header{background:#111827;color:#fff;padding:18px;text-align:center}
main{max-width:700px;margin:auto;padding:15px}
.card{background:#fff;border-radius:14px;padding:16px;margin-bottom:14px;box-shadow:0 2px 8px #0001}
.title{font-size:18px;font-weight:bold;margin-bottom:10px}
.info{line-height:1.6}
button,.wa{display:block;width:100%;padding:13px;border:0;border-radius:10px;background:#16a34a;color:#fff;font-size:16px;text-align:center;text-decoration:none;margin-top:12px;cursor:pointer}
.refresh{background:#2563eb}.mail{background:#7c3aed}.action{background:#dc2626}
.small{font-size:13px;color:#666}.empty{text-align:center;color:#666;padding:20px}.error{color:#b91c1c;font-weight:bold}
</style>
</head>
<body>
<header><h1>Fugarte Escape Room</h1><div>Panel de revisión (Python)</div></header>
<main>
<div class="card">
<button class="mail" onclick="revisarCorreo()">📧 REVISAR CORREO</button>
<div id="mailStatus" class="small" style="margin-top:10px">Revisa INBOX, Notion y disponibilidad.</div>
</div>
<div id="review"></div>
<div class="card"><div class="title">🔔 Solicitudes pendientes</div><div id="sol">Cargando...</div></div>
<div class="card"><div class="title">📅 Reservas confirmadas pendientes de pago</div><div id="res">Cargando...</div></div>
<div class="card"><div class="title">📚 Sesiones realizadas</div><div id="ses">Cargando...</div></div>
<button class="refresh" onclick="cargar()">🔄 Actualizar</button>
</main>
<script>
async function cargar(){
  const sol=document.getElementById("sol"), res=document.getElementById("res"), ses=document.getElementById("ses");
  sol.innerHTML="Cargando..."; res.innerHTML="Cargando..."; ses.innerHTML="Cargando...";
  try{
    const [s, r, se] = await Promise.all([fetch("/api/solicitudes").then(x=>x.json()), fetch("/api/reservas").then(x=>x.json()), fetch("/api/sesiones").then(x=>x.json())]);
    sol.innerHTML = s.length ? s.map(x=>`<div class="card"><strong>${x.nombre}</strong><div class="info">📅 ${x.fechaTexto}<br>🕐 ${x.horaTexto}<br>👥 ${x.jugadores} personas</div>${x.whatsapp?`<a class="wa" href="${x.whatsapp}" target="_blank">💬 Abrir WhatsApp</a>`:""}<button class="action" onclick="confirmar('${x.id}')">✅ CONFIRMAR</button><button class="action" onclick="rechazar('${x.id}')">❌ RECHAZAR</button></div>`).join("") : '<div class="empty">No hay solicitudes.</div>';
    res.innerHTML = r.length ? r.map(x=>`<div class="card"><strong>${x.nombre}</strong><div class="info">📅 ${x.fechaTexto}<br>🕐 ${x.horaTexto}<br>👥 ${x.jugadores} personas</div><button class="action" onclick="marcar('${x.id}')">✅ SESIÓN REALIZADA + PAGO</button></div>`).join("") : '<div class="empty">No hay reservas.</div>';
    ses.innerHTML = se.length ? se.slice(0,30).map(x=>`<div class="card"><strong>${x.nombre}</strong><div class="info">📅 ${x.fechaTexto}<br>🕐 ${x.horaTexto}<br>💶 Pago: ${x.pago}</div></div>`).join("") : '<div class="empty">No hay sesiones.</div>';
  }catch(e){console.error(e);}
}
async function revisarCorreo(){
  document.getElementById("mailStatus").textContent="Revisando...";
  const r = await fetch("/api/revisar-correo",{method:"POST"}).then(x=>x.json());
  document.getElementById("mailStatus").textContent="✅ Correo revisado.";
  cargar();
}
async function confirmar(id){ if(confirm("¿Confirmar?")){ await fetch("/api/confirmar-solicitud",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({requestId:id})}); cargar(); } }
async function rechazar(id){ if(confirm("¿Rechazar?")){ await fetch("/api/rechazar-solicitud",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({requestId:id})}); cargar(); } }
async function marcar(id){ if(confirm("¿Marcar realizada?")){ await fetch("/api/sesion-realizada",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({reservationId:id})}); cargar(); } }
cargar();
</script>
</body>
</html>
    """