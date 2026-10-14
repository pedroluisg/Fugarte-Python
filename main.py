from datetime import datetime, date, timedelta
import zoneinfo
import re
import os
import httpx
import urllib.parse
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
DB_NO_REALIZADAS = "e694fcc30a19483fb5fc068cfa91ee87"

HOSTINGER_MAILBOX = "AC76aebfc4d60a270d918b4549d4cd"

TZ = zoneinfo.ZoneInfo("Europe/Madrid")
NV = "2022-06-28"


# =========================================================
# NOTION
# =========================================================

async def notion(url: str, env: dict, body: dict = None, method: str = "POST") -> dict:
    headers = {
        "Authorization": "Bearer " + env.get("NOTION_TOKEN", ""),
        "Notion-Version": NV,
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        if method == "PATCH":
            r = await client.patch("https://api.notion.com/v1/" + url, headers=headers, json=body or {})
        else:
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
        
        try:
            data = await notion("databases/" + database + "/query", env, b)
            results.extend(data.get("results", []))
            cursor = data.get("next_cursor") if data.get("has_more") else None
            if not cursor:
                break
        except Exception:
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


async def no_realizadas(env: dict):
    data = await query_all(DB_NO_REALIZADAS, env, {"sorts": [{"property": "Fecha", "direction": "descending"}, {"property": "Hora", "direction": "descending"}]})
    res = []
    for x in data:
        p = x.get("properties", {})
        fecha = date_prop(p.get("Fecha"))
        hora_valor = hora(p.get("Hora"))
        res.append({
            "id": x["id"], "nombre": prop(p.get("Nombre")), "fecha": fecha,
            "fechaTexto": fmt_date(fecha), "hora": hora_valor, "horaTexto": fmt_time(hora_valor),
            "jugadores": num(p.get("Jugadores")), "version": prop(p.get("Versión")),
            "tipo": prop(p.get("Tipo")), "motivo": prop(p.get("Motivo")), "senal": prop(p.get("Señal"))
        })
    return res


# =========================================================
# HOSTINGER API & EXTRACCIÓN INTELIGENTE DE CORREOS
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


async def hostinger_move(env: dict, uid: str) -> bool:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.mail.hostinger.com/api/v1/mailboxes/" + HOSTINGER_MAILBOX + "/folders/INBOX/messages/" + urllib.parse.quote(uid) + "/move",
            headers={"Authorization": "Bearer " + env.get("HOSTINGER_TOKEN", ""), "Content-Type": "application/json"},
            json={"targetFolder": "INBOX.Aceptadas"}
        )
    if not (200 <= r.status_code < 300):
        async with httpx.AsyncClient() as client2:
            await client2.post(
                "https://api.mail.hostinger.com/api/v1/mailboxes/" + HOSTINGER_MAILBOX + "/folders/INBOX/messages/" + urllib.parse.quote(uid) + "/move",
                headers={"Authorization": "Bearer " + env.get("HOSTINGER_TOKEN", ""), "Content-Type": "application/json"},
                json={"targetFolder": "INBOX.Aceptadas"}
            )
    return True


def mail_text(data: Any) -> str:
    if isinstance(data, str):
        return data
    content = data.get("data") or data or {}
    return "\n".join([
        content.get("text", ""),
        content.get("body", ""),
        content.get("content", ""),
        content.get("html", "")
    ])


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


def extract_after_from_mail(text: str) -> str:
    m = re.search(r"(?:a partir de|desde|después de|a las)\s*([01]?\d|2[0-3])(?:[:h\.]([0-5]\d))?", text, re.IGNORECASE)
    if m:
        return f"{m.group(1).zfill(2)}:{m.group(2) or '00'}"
    return ""


def extract_before_from_mail(text: str) -> str:
    m = re.search(r"(?:antes de|hasta|no más tarde de)\s*([01]?\d|2[0-3])(?:[:h\.]([0-5]\d))?", text, re.IGNORECASE)
    if m:
        return f"{m.group(1).zfill(2)}:{m.group(2) or '00'}"
    return ""


def minutes_of(time_str: str) -> Optional[int]:
    m = re.match(r"^(\d{2}):(\d{2})$", time_str or "")
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def resolver_hora_flexible(text: str, after: str, before: str) -> str:
    texto_lower = text.lower()
    es_tarde = any(w in texto_lower for w in ["tarde", "noches", "vesprada", "pm", "17:", "18:", "19:", "20:", "21:"])
    
    objetivo_minutos = 18 * 60 + 30 if es_tarde else 12 * 60
    
    min_m = minutes_of(after) if after else (16 * 60 if es_tarde else 9 * 60)
    max_m = minutes_of(before) if before else (22 * 60 if es_tarde else 15 * 60)
    
    mejor_slot = None
    menor_distancia = 999999
    
    for h in range(0, 24):
        for m in range(0, 60, 30):
            total_m = h * 60 + m
            if min_m <= total_m <= max_m:
                dist = abs(total_m - objetivo_minutos)
                if dist < menor_distancia:
                    menor_distancia = dist
                    mejor_slot = f"{str(h).zfill(2)}:{str(m).zfill(2)}"
                    
    return mejor_slot or ("18:30" if es_tarde else "12:00")


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


def extract_phone_from_mail(text: str) -> str:
    if not text:
        return ""
    limpio = re.sub(r"<[^>]*>", " ", text)
    patron = r"(?:\+34|0034)?[\s().-]*[6789](?:[\s().-]*\d){8}(?!\d)"
    candidatos = re.findall(patron, limpio)
    for c in candidatos:
        digitos = re.sub(r"\D", "", c)
        if digitos.startswith("0034") and len(digitos) == 13:
            digitos = digitos[4:]
        elif digitos.startswith("34") and len(digitos) == 11:
            digitos = digitos[2:]
        if len(digitos) == 9 and re.match(r"^[6789]\d{8}$", digitos):
            return "+34" + digitos
    return ""


def extract_version_from_mail(text: str) -> str:
    contenido = re.sub(r"[\u0300-\u036f]", "", text or "").upper()
    if re.search(r"\bPRO\b", contenido):
        return "PRO"
    return "NORMAL"


def extract_name_from_mail(text: str, subject: str) -> str:
    m = re.search(r"Reserva[^:]*:\s*20\d{2}-\d{2}-\d{2}\s*-\s*(.+)$", subject, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:nombre|name|equipo|grupo)\s*[:\-]\s*([^\r\n]+)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


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
        text = subject + "\n" + mail_text(detail)

        fecha = extract_date_from_mail(text)
        hora_solicitada = extract_time_from_mail(text)
        after = extract_after_from_mail(text)
        before = extract_before_from_mail(text)
        
        if not hora_solicitada and (after or before or "tarde" in text.lower() or "mañana" in text.lower() or "10" in text or "11" in text or "17" in text):
            hora_solicitada = resolver_hora_flexible(text, after, before)

        jugadores = extract_players_from_mail(text)
        version = extract_version_from_mail(text)
        telefono = extract_phone_from_mail(text)
        nombre = extract_name_from_mail(text, subject)

        if not fecha or jugadores is None or not hora_solicitada:
            continue

        dt_base = datetime.fromisoformat(fecha + "T" + hora_solicitada + ":00").replace(tzinfo=TZ)
        hora_utc = dt_base.astimezone(zoneinfo.ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")

        created = await notion("pages", env, {
            "parent": {"database_id": DB_SOL},
            "properties": {
                "Nombre": {"title": [{"text": {"content": nombre or "Solicitud web"}}]},
                "Estado": {"select": {"name": "PENDIENTE"}},
                "Pago": {"select": {"name": "PENDIENTE"}},
                "Fecha": {"date": {"start": fecha}},
                "Hora": {"date": {"start": hora_utc}},
                "Jugadores": {"number": jugadores},
                "Versión": {"select": {"name": version}},
                "Origen": {"select": {"name": "WEB"}},
                "Teléfono": {"phone_number": telefono} if telefono else {"phone_number": None},
                "Notas": {"rich_text": [{"text": {"content": f"Solicitud recibida por Hostinger INBOX. UID {uid}."}}]}
            }
        })

        try:
            await hostinger_move(env, uid)
        except Exception:
            pass

        mensaje = (
            f"Hola{((' ' + nombre) if nombre else '')}, escribo de Fugarte Escape Room.\n\n"
            f"Tenemos disponibilidad el {fmt_date(fecha)} a las {hora_solicitada} para {jugadores} personas.\n\n"
            "Si queréis confirmar la reserva, sería necesario hacer un Bizum de 10 € a este número de teléfono.\n\n"
            "Una vez recibido, dejamos la reserva confirmada.\n\n"
            "Si finalmente no estáis interesados, por favor contestad a este WhatsApp para que podamos ofrecer el horario a otros usuarios.\n\n"
            "¡Muchas gracias! 😊"
        )

        actions.append({
            "type": "CREATED",
            "uid": uid,
            "subject": subject,
            "requestId": created["id"],
            "nombre": nombre,
            "fecha": fecha,
            "hora": hora_solicitada,
            "jugadores": jugadores,
            "version": version,
            "telefono": telefono,
            "whatsapp": wa(telefono, mensaje),
            "reason": "Solicitud creada en Notion y correo movido a INBOX.Aceptadas."
        })
        break

    return actions


# =========================================================
# ACCIONES DE NOTION Y ENDPOINTS API
# =========================================================

async def notion_update_page(page_id: str, env: dict, properties: dict):
    await notion("pages/" + page_id, env, {"properties": properties}, method="PATCH")

async def notion_archive_page(page_id: str, env: dict):
    await notion("pages/" + page_id, env, {"archived": True}, method="PATCH")

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

@app.get("/api/no-realizadas")
async def api_no_realizadas():
    return await no_realizadas(ENV_CONFIG)

class ActionRequest(BaseModel):
    reservationId: Optional[str] = None
    requestId: Optional[str] = None
    motivo: Optional[str] = None
    senal: Optional[str] = None

@app.post("/api/sesion-realizada")
async def api_sesion_realizada(body: ActionRequest):
    reservation = await notion("pages/" + body.reservationId, ENV_CONFIG)
    p = reservation.get("properties", {})
    
    await notion("pages", ENV_CONFIG, {
        "parent": {"database_id": DB_SES},
        "properties": {
            "Nombre": {"title": [{"text": {"content": prop(p.get("Nombre")) or "Sesión"}}]},
            "Fecha": {"date": {"start": date_prop(p.get("Fecha"))}},
            "Hora": {"date": {"start": hora(p.get("Hora"))}},
            "Jugadores": {"number": num(p.get("Jugadores"))},
            "Versión": {"select": {"name": prop(p.get("Versión")) or "NORMAL"}},
            "Origen": {"select": {"name": prop(p.get("Origen")) or "WEB"}},
            "Pago": {"select": {"name": "RECIBIDO"}},
            "Fecha registro": {"date": {"start": datetime.now(TZ).isoformat()}},
            "Reserva original": {"relation": [{"id": body.reservationId}]}
        }
    })
    await notion_update_page(body.reservationId, ENV_CONFIG, {"Pago": {"select": {"name": "RECIBIDO"}}})
    return {"ok": True, "alreadyExists": False}

@app.post("/api/suspender-reserva")
async def api_suspender_reserva(body: ActionRequest):
    if not body.reservationId:
        raise HTTPException(status_code=400, detail="Falta el ID de la reserva a suspender.")
        
    reservation = await notion("pages/" + body.reservationId, ENV_CONFIG)
    p = reservation.get("properties", {})
    
    props = {
        "Nombre": {"title": [{"text": {"content": prop(p.get("Nombre")) or "Reserva suspendida"}}]},
        "Fecha": {"date": {"start": date_prop(p.get("Fecha"))}},
        "Hora": {"date": {"start": hora(p.get("Hora"))}},
        "Jugadores": {"number": num(p.get("Jugadores"))},
        "Versión": {"select": {"name": prop(p.get("Versión")) or "NORMAL"}},
        "Teléfono": {"phone_number": phone(p.get("Teléfono"))} if phone(p.get("Teléfono")) else {"phone_number": None}
    }
    if body.motivo:
        props["Motivo"] = {"select": {"name": body.motivo}}
    if body.senal:
        props["Señal"] = {"select": {"name": body.senal}}
    try:
        props["Tipo"] = {"select": {"name": "SUSPENDIDA"}}
    except Exception:
        pass

    await notion("pages", ENV_CONFIG, {
        "parent": {"database_id": DB_NO_REALIZADAS},
        "properties": props
    })
    
    try:
        await notion_archive_page(body.reservationId, ENV_CONFIG)
    except Exception:
        await notion_update_page(body.reservationId, ENV_CONFIG, {"Estado": {"select": {"name": "CANCELADA"}}})

    return {"ok": True}

@app.post("/api/confirmar-solicitud")
async def api_confirmar_solicitud(body: ActionRequest):
    r = await notion("pages/" + body.requestId, ENV_CONFIG)
    p = r.get("properties", {})
    fecha = date_prop(p.get("Fecha"))
    h_val = hora(p.get("Hora"))
    
    dt_base = datetime.fromisoformat(fecha + "T" + (h_val if len(h_val)==5 else "10:00") + ":00").replace(tzinfo=TZ)
    h_utc = dt_base.astimezone(zoneinfo.ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")

    created = await notion("pages", ENV_CONFIG, {
        "parent": {"database_id": DB_RES},
        "properties": {
            "Nombre": {"title": [{"text": {"content": prop(p.get("Nombre")) or "Reserva"}}]},
            "Estado": {"select": {"name": "CONFIRMADA"}},
            "Pago": {"select": {"name": "PENDIENTE"}},
            "Fecha": {"date": {"start": fecha}},
            "Hora": {"date": {"start": h_utc}},
            "Jugadores": {"number": num(p.get("Jugadores"))},
            "Versión": {"select": {"name": prop(p.get("Versión")) or "NORMAL"}},
            "Origen": {"select": {"name": prop(p.get("Origen")) or "WEB"}},
            "Teléfono": {"phone_number": phone(p.get("Teléfono"))} if phone(p.get("Teléfono")) else {"phone_number": None}
        }
    })
    await notion_update_page(body.requestId, ENV_CONFIG, {"Estado": {"select": {"name": "CONFIRMADA"}}})
    return {"ok": True, "nombre": prop(p.get("Nombre")), "fecha": fecha, "hora": h_val}

@app.post("/api/rechazar-solicitud")
async def api_rechazar_solicitud(body: ActionRequest):
    r = await notion("pages/" + body.requestId, ENV_CONFIG)
    p = r.get("properties", {})
    
    props = {
        "Nombre": {"title": [{"text": {"content": prop(p.get("Nombre")) or "Solicitud rechazada"}}]},
        "Fecha": {"date": {"start": date_prop(p.get("Fecha"))}},
        "Hora": {"date": {"start": hora(p.get("Hora"))}},
        "Jugadores": {"number": num(p.get("Jugadores"))},
        "Versión": {"select": {"name": prop(p.get("Versión")) or "NORMAL"}},
        "Teléfono": {"phone_number": phone(p.get("Teléfono"))} if phone(p.get("Teléfono")) else {"phone_number": None}
    }
    if body.motivo:
        props["Motivo"] = {"select": {"name": body.motivo}}
    try:
        props["Tipo"] = {"select": {"name": "RECHAZADA"}}
    except Exception:
        pass

    await notion("pages", ENV_CONFIG, {
        "parent": {"database_id": DB_NO_REALIZADAS},
        "properties": props
    })
    try:
        await notion_archive_page(body.requestId, ENV_CONFIG)
    except Exception:
        await notion_update_page(body.requestId, ENV_CONFIG, {"Estado": {"select": {"name": "CANCELADA"}}})

    return {"ok": True}

@app.post("/api/revisar-correo")
async def api_revisar_correo():
    actions = await revisar_correo_logic(ENV_CONFIG)
    return {"ok": True, "actions": actions}


# =========================================================
# HTML FRONTEND (INTERFAZ COMPLETA + MODALES DESPLEGABLES)
# =========================================================

@app.get("/", response_class=HTMLResponse)
async def panel():
    return """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fugarte - Revisar</title>
<style>
*{box-sizing:border-box}
body{margin:0;font-family:Arial,sans-serif;background:#f4f5f7;color:#222}
header{background:#111827;color:#fff;padding:18px;text-align:center}
h1{margin:0;font-size:24px}
main{max-width:700px;margin:auto;padding:15px}
.card{background:#fff;border-radius:14px;padding:16px;margin-bottom:14px;box-shadow:0 2px 8px #0001}
.next{border:3px solid #f59e0b;background:#fffbeb}
.title{font-size:18px;font-weight:bold;margin-bottom:10px}
.info{line-height:1.6}
button,.wa{display:block;width:100%;padding:13px;border:0;border-radius:10px;background:#16a34a;color:#fff;font-size:16px;text-align:center;text-decoration:none;margin-top:12px;cursor:pointer}
.refresh{background:#2563eb}
.mail{background:#7c3aed}
.action{background:#dc2626}
.suspend{background:#d97706}
.small{font-size:13px;color:#666}
.empty{text-align:center;color:#666;padding:20px}
.badge{display:inline-block;padding:4px 8px;border-radius:8px;background:#eee;margin-top:5px;font-size:13px}
.error{color:#b91c1c;font-weight:bold}
.dropdown-btn{background:#4b5563;margin-top:8px}
.dropdown-content{display:none;margin-top:10px;padding-left:10px;border-left:3px solid #e5e7eb}

/* Estilos para los modales desplegables */
.modal{display:none;position:fixed;z-index:100;left:0;top:0;width:100%;height:100%;background:rgba(0,0,0,0.5);justify-content:center;align-items:center}
.modal-content{background:#fff;padding:20px;border-radius:14px;width:90%;max-width:400px;box-shadow:0 4px 15px #0003}
.modal-content label{display:block;font-weight:bold;margin-top:12px;margin-bottom:5px}
.modal-content select{width:100%;padding:10px;border-radius:8px;border:1px solid #ccc;font-size:15px}
.modal-btns{display:flex;gap:10px;margin-top:20px}
.modal-btns button{margin-top:0}
</style>
</head>
<body>
<header>
<h1>Fugarte Escape Room</h1>
<div>Panel de revisión</div>
</header>
<main>
<div class="card">
<button class="mail" onclick="revisarCorreo()">📧 REVISAR CORREO</button>
<div id="mailStatus" class="small" style="margin-top:10px">
Revisa INBOX, comprueba Notion y disponibilidad, crea la solicitud y se detiene en la primera acción que necesitas hacer tú.
</div>
</div>
<div id="review"></div>
<div id="next"></div>
<div class="card">
<div class="title">🔔 Solicitudes pendientes</div>
<div id="sol">Cargando...</div>
</div>
<div class="card">
<div class="title">📅 Reservas confirmadas pendientes de pago final</div>
<div id="res">Cargando...</div>
</div>
<div class="card">
<div class="title">📚 Sesiones realizadas</div>
<div id="ses">Cargando...</div>
</div>
<div class="card">
<div class="title">🚫 Histórico No Realizadas (Suspendidas / Rechazadas)</div>
<div id="no-realizadas">Cargando...</div>
</div>
<button class="refresh" onclick="cargar()">🔄 Actualizar</button>
</main>

<!-- Modal para Suspender Reserva -->
<div id="modalSuspender" class="modal">
<div class="modal-content">
<div class="title">⚠️ Suspender / Cancelar Reserva</div>
<label for="selectMotivoSusp">Motivo de la suspensión:</label>
<select id="selectMotivoSusp">
<option value="Suspende cliente">Suspende cliente</option>
<option value="Suspendemos nosotros">Suspendemos nosotros</option>
</select>
<label for="selectSenal">Señal de 10 €:</label>
<select id="selectSenal">
<option value="Devuelta">Devuelta</option>
<option value="No devuelta">No devuelta</option>
<option value="N/A">N/A</option>
</select>
<div class="modal-btns">
<button class="action" onclick="ejecutarSuspension()">Confirmar</button>
<button class="refresh" onclick="cerrarModales()">Cancelar</button>
</div>
</div>
</div>

<!-- Modal para Rechazar Solicitud -->
<div id="modalRechazar" class="modal">
<div class="modal-content">
<div class="title">❌ Rechazar Solicitud</div>
<label for="selectMotivoRech">Motivo del rechazo:</label>
<select id="selectMotivoRech">
<option value="Rechazada por el cliente">Rechazada por el cliente</option>
<option value="Rechazada por nosotros">Rechazada por nosotros</option>
</select>
<div class="modal-btns">
<button class="action" onclick="ejecutarRechazo()">Confirmar</button>
<button class="refresh" onclick="cerrarModales()">Cancelar</button>
</div>
</div>
</div>

<script>
function safe(value, fallback){
if(value===null||value===undefined||value===""){return fallback||"";}
return String(value);
}

let targetIdToProcess = null;

async function cargar(){
const sol=document.getElementById("sol");
const res=document.getElementById("res");
const ses=document.getElementById("ses");
const noRealizadas=document.getElementById("no-realizadas");
const next=document.getElementById("next");
sol.innerHTML="Cargando...";
res.innerHTML="Cargando...";
ses.innerHTML="Cargando...";
noRealizadas.innerHTML="Cargando...";
next.innerHTML="";

async function leerArray(url, nombre){
const r=await fetch(url,{method:"GET",cache:"no-store"});
let data=null;
try{data=await r.json();}catch(e){throw new Error(nombre+" no devolvió JSON válido ("+r.status+")");}
if(!r.ok){throw new Error(nombre+" "+r.status+(data?.error?": "+data.error:""));}
if(Array.isArray(data))return data;
if(Array.isArray(data.data))return data.data;
if(Array.isArray(data.results))return data.results;
throw new Error(nombre+" devolvió un formato inesperado");
}

const resultados=await Promise.allSettled([
leerArray("/api/solicitudes", "Solicitudes"),
leerArray("/api/reservas", "Reservas"),
leerArray("/api/sesiones", "Sesiones"),
leerArray("/api/no-realizadas", "No Realizadas")
]);

if(resultados[0].status==="fulfilled"){mostrarSolicitudes(resultados[0].value);}
else{sol.innerHTML='<div class="error">❌ Error al cargar solicitudes: '+safe(resultados[0].reason?.message,"error")+'</div>';}

if(resultados[1].status==="fulfilled"){mostrarReservas(resultados[1].value);}
else{res.innerHTML='<div class="error">❌ Error al cargar reservas: '+safe(resultados[1].reason?.message,"error")+'</div>';}

if(resultados[2].status==="fulfilled"){mostrarSesionesDesplegable(resultados[2].value, "ses");}
else{ses.innerHTML='<div class="error">❌ Error al cargar sesiones: '+safe(resultados[2].reason?.message,"error")+'</div>';}

if(resultados[3].status==="fulfilled"){mostrarSesionesDesplegable(resultados[3].value, "no-realizadas");}
else{noRealizadas.innerHTML='<div class="error">❌ Error al cargar histórico: '+safe(resultados[3].reason?.message,"error")+'</div>';}
}

async function revisarCorreo(){
const status=document.getElementById("mailStatus");
const review=document.getElementById("review");
status.textContent="Revisando correo, Notion y disponibilidad...";
review.innerHTML="";
try{
const r=await fetch("/api/revisar-correo",{method:"POST"});
const data=await r.json();
if(!r.ok||!data.ok){throw new Error(data.error||"Error revisando correo");}
if(!Array.isArray(data.actions)||!data.actions.length){
status.textContent="✅ Correo revisado. No hay nuevas solicitudes que procesar.";
return;
}
status.textContent="🛑 Revisión detenida en la primera acción que necesitas realizar.";
let output="";
data.actions.forEach(function(x){
output+='<div class="card">';
if(x.type==="CREATED"){output+='<div class="title">🛑 PRIMERA ACCIÓN NECESARIA</div>';}
else{output+='<div class="title">⚠️ REVISAR SOLICITUD</div>';}
output+='<div class="info"><strong>'+safe(x.nombre||x.subject,"Solicitud")+"</strong><br>";
if(x.fecha){output+="📅 "+safe(x.fecha)+"<br>";}
if(x.hora){output+="🕐 "+safe(x.hora)+"<br>";}
if(x.jugadores!==undefined){output+="👥 "+safe(x.jugadores)+" personas<br>";}
output+="ℹ️ "+safe(x.reason,"Requiere revisión")+"</div>";
if(x.whatsapp){output+='<a class="wa" href="'+x.whatsapp+'" target="_blank" rel="noopener">💬 ABRIR WHATSAPP</a>';}
output+="</div>";
});
review.innerHTML=output;
await cargar();
}catch(e){
status.textContent="❌ "+e.message;
review.innerHTML='<div class="card error">'+safe(e.message)+'</div>';
}
}

async function confirmarSolicitud(requestId){
if(!requestId){alert("No se ha encontrado el ID de la solicitud.");return;}
if(!confirm("¿Confirmar esta reserva?\\n\\nSe creará en Reservas Fugarte como CONFIRMADA."))return;
try{
const r=await fetch("/api/confirmar-solicitud",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({requestId})});
const data=await r.json();
if(!r.ok||!data.ok)throw new Error(data.error||"No se pudo confirmar");
alert("✅ Reserva confirmada.");
await cargar();
}catch(e){alert("❌ "+e.message);}
}

function abrirModalRechazar(requestId){
targetIdToProcess = requestId;
document.getElementById("modalRechazar").style.display = "flex";
}

async function ejecutarRechazo(){
if(!targetIdToProcess) return;
const motivo = document.getElementById("selectMotivoRech").value;
cerrarModales();

try{
const r=await fetch("/api/rechazar-solicitud",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({requestId: targetIdToProcess, motivo: motivo})});
const data=await r.json();
if(!r.ok||!data.ok)throw new Error(data.error||"No se pudo rechazar");
alert("✅ Solicitud rechazada y movida al histórico.");
await cargar();
}catch(e){alert("❌ "+e.message);}
}

function abrirModalSuspender(reservationId){
targetIdToProcess = reservationId;
document.getElementById("modalSuspender").style.display = "flex";
}

async function ejecutarSuspension(){
if(!targetIdToProcess) return;
const motivo = document.getElementById("selectMotivoSusp").value;
const senal = document.getElementById("selectSenal").value;
cerrarModales();

try{
const r=await fetch("/api/suspender-reserva",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({reservationId: targetIdToProcess, motivo: motivo, senal: senal})});
const data=await r.json();
if(!r.ok||!data.ok)throw new Error(data.error||"No se pudo suspender la reserva");
alert("✅ Reserva suspendida y movida al histórico.");
await cargar();
}catch(e){alert("❌ "+e.message);}
}

function cerrarModales(){
document.getElementById("modalSuspender").style.display = "none";
document.getElementById("modalRechazar").style.display = "none";
targetIdToProcess = null;
}

async function marcarSesionRealizada(reservationId){
if(!confirm("¿La sesión ya se ha realizado y el pago está recibido?"))return;
try{
const r=await fetch("/api/sesion-realizada",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({reservationId})});
const data=await r.json();
if(!r.ok||!data.ok)throw new Error(data.error||"No se pudo registrar la sesión");
alert(data.alreadyExists ? "Esta sesión ya estaba registrada en el histórico." : "✅ Sesión registrada como realizada y pagada.");
await cargar();
}catch(e){alert("❌ "+e.message);}
}

function mostrarSolicitudes(a){
if(!Array.isArray(a)||!a.length){document.getElementById("sol").innerHTML='<div class="empty">No hay solicitudes pendientes.</div>';return;}
let output="";
a.forEach(function(x){
output+='<div class="card">';
output+="<strong>"+safe(x.nombre,"Sin nombre")+"</strong>";
output+='<div class="info">📅 '+safe(x.fechaTexto,"Sin fecha")+"<br>🕐 "+safe(x.horaTexto,"Sin hora")+"<br>👥 "+safe(x.jugadores,"Sin dato")+" personas<br>🎯 "+safe(x.version,"Sin versión")+"</div>";
if(x.whatsapp){output+='<a class="wa" href="'+x.whatsapp+'" target="_blank" rel="noopener">💬 Abrir WhatsApp</a>';}
output+='<button class="action" onclick="confirmarSolicitud(\\''+x.id+'\\')">✅ CONFIRMAR RESERVA</button>';
output+='<button class="action" onclick="abrirModalRechazar(\\''+x.id+'\\')">❌ RECHAZAR SOLICITUD</button>';
output+="</div>";
});
document.getElementById("sol").innerHTML=output;
}

function mostrarReservas(a){
if(!Array.isArray(a)||!a.length){document.getElementById("res").innerHTML='<div class="empty">No hay reservas futuras.</div>';document.getElementById("next").innerHTML="";return;}
const n=a[0];
let siguiente='<div class="card next"><div class="title">⭐ PRÓXIMA SESIÓN</div><div class="info"><strong>'+safe(n.nombre,"Sin nombre")+"</strong><br>📅 "+safe(n.fechaTexto)+"<br>🕐 "+safe(n.horaTexto)+"<br>👥 "+safe(n.jugadores)+" personas<br>🎯 "+safe(n.version)+"</div></div>";
document.getElementById("next").innerHTML=siguiente;

let output="";
a.forEach(function(x,i){
output+='<div class="card"><strong>'+safe(x.nombre,"Sin nombre")+"</strong>";
if(i===0){output+='<div class="badge">⭐ Próxima</div>';}
output+='<div class="info">📅 '+safe(x.fechaTexto)+"<br>🕐 "+safe(x.horaTexto)+"<br>👥 "+safe(x.jugadores)+" personas<br>🎯 "+safe(x.version)+"</div>";
output+='<button class="action" onclick="marcarSesionRealizada(\\''+x.id+'\\')">✅ SESIÓN REALIZADA + PAGO RECIBIDO</button>';
output+='<button class="suspend" onclick="abrirModalSuspender(\\''+x.id+'\\')">⚠️ SUSPENDER / CANCELAR RESERVA</button>';
output+="</div>";
});
document.getElementById("res").innerHTML=output;
}

function mostrarSesionesDesplegable(a, containerId){
if(!Array.isArray(a)||!a.length){document.getElementById(containerId).innerHTML='<div class="empty">No hay registros.</div>';return;}

const arbol = {};
const mesesNombres = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"];

a.forEach(function(x){
if(!x.fecha) return;
const partes = x.fecha.split("-");
if(partes.length < 2) return;
const anio = partes[0];
const mesNum = parseInt(partes[1], 10);
const mesNombre = mesesNombres[mesNum] || partes[1];

if(!arbol[anio]) arbol[anio] = {};
if(!arbol[anio][mesNombre]) arbol[anio][mesNombre] = [];
arbol[anio][mesNombre].push(x);
});

const aniosOrdenados = Object.keys(arbol).sort().reverse();
let output = "";

aniosOrdenados.forEach(function(anio){
output += `<button class="dropdown-btn" onclick="toggleAnio('${containerId}', '${anio}')">📁 Año ${anio} ▾</button>`;
output += `<div id="anio-${containerId}-${anio}" class="dropdown-content">`;

const mesesOrdenados = Object.keys(arbol[anio]).sort(function(m1, m2){
return mesesNombres.indexOf(m2) - mesesNombres.indexOf(m1);
});

mesesOrdenados.forEach(function(mes){
output += `<button class="dropdown-btn" onclick="toggleMes('${containerId}', '${anio}', '${mes}')" style="background:#6b7280; font-size:15px;">📂 ${mes} ▾</button>`;
output += `<div id="mes-${containerId}-${anio}-${mes}" class="dropdown-content">`;

arbol[anio][mes].forEach(function(x){
output += `<div class="card" style="margin-top:8px; margin-bottom:8px; background:#f9fafb;">`;
output += `<strong>${safe(x.nombre,"Sin nombre")}</strong>`;
output += `<div class="info">📅 ${safe(x.fechaTexto)}<br>🕐 ${safe(x.horaTexto)}<br>👥 ${safe(x.jugadores)} personas<br>🎯 ${safe(x.version)}`;
if(x.tipo) output += `<br>📌 Tipo: ${safe(x.tipo)}`;
if(x.motivo) output += `<br>ℹ️ Motivo: ${safe(x.motivo)}`;
if(x.senal) output += `<br>💶 Señal: ${safe(x.senal)}`;
if(x.pago) output += `<br>💶 Pago: ${safe(x.pago)}`;
output += `</div></div>`;
});

output += `</div>`;
});

output += `</div>`;
});

document.getElementById(containerId).innerHTML = output || '<div class="empty">No hay registros.</div>';
}

function toggleAnio(containerId, anio){
const el = document.getElementById("anio-" + containerId + "-" + anio);
if(el){ el.style.display = (el.style.display === "block") ? "none" : "block"; }
}

function toggleMes(containerId, anio, mes){
const el = document.getElementById("mes-" + containerId + "-" + anio + "-" + mes);
if(el){ el.style.display = (el.style.display === "block") ? "none" : "block"; }
}

cargar();
</script>
</body>
</html>
    """