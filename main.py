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
        if method == "GET":
            r = await client.get("https://api.notion.com/v1/" + url, headers=headers)
        elif method == "PATCH":
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
    return p["date"]["start"][:10]


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
            if re.match(r"^\d{1,2}:\d{2}$", val.strip()):
                partes = val.strip().split(":")
                return f"{partes[0].zfill(2)}:{partes[1]}"
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


def normalizar_hora_humana(h: int, m: int, contexto: str) -> str:
    ctx = contexto.lower()
    if 1 <= h <= 9:
        h += 12
    elif h == 10 and any(w in ctx for w in ["noche", "tarde", "pm"]):
        h = 22
    elif h == 12 and "noche" in ctx:
        h = 22
    # Ajustar minutos a :00 o :30 (sin cuartos)
    if m < 15:
        m_norm = 0
    elif m < 45:
        m_norm = 30
    else:
        m_norm = 0
        h += 1
    if h < 10:
        h, m_norm = 10, 0
    if h > 22 or (h == 22 and m_norm > 0):
        h, m_norm = 22, 0
    return f"{str(h).zfill(2)}:{str(m_norm).zfill(2)}"


def extract_preference_segment(text: str) -> str:
    """Extrae preferentemente las líneas de comentarios/horario del cliente para no confundirse con cabeceras del email."""
    limpio = re.sub(r"<[^>]*>", " ", text or "")
    # Quitar fechas YYYY-MM-DD o DD/MM/YYYY y teléfonos para que sus números no parezcan horas
    limpio = re.sub(r"\b20\d{2}[-\/]\d{1,2}[-\/]\d{1,2}\b", " ", limpio)
    limpio = re.sub(r"\b\d{1,2}[\/-]\d{1,2}[\/-]20\d{2}\b", " ", limpio)
    limpio = re.sub(r"(?:\+34|0034)?[\s().-]*[6789](?:[\s().-]*\d){8}(?!\d)", " ", limpio)
    # Quitar cabeceras técnicas tipo "Enviado el ... a las 14:23"
    limpio = re.sub(r"(?:enviado|sent|date|fecha de env[íi]o).*?\d{1,2}:\d{2}", " ", limpio, flags=re.IGNORECASE)
    return limpio


def extract_after_from_mail(text: str) -> str:
    limpio = extract_preference_segment(text)
    mapa_num = {"una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12}
    
    m = re.search(r"(?:a partir de|desde|despu[ée]s de)\s*(?:las?\s*)?([01]?\d|2[0-3])(?:\s*[:hH\.]\s*([0-5]\d))?(?:\s*y\s*media)?", limpio, re.IGNORECASE)
    if m:
        h = int(m.group(1))
        mins = int(m.group(2)) if m.group(2) else (30 if "media" in m.group(0).lower() else 0)
        return normalizar_hora_humana(h, mins, limpio)
        
    for palabra, num_h in mapa_num.items():
        m_pal = re.search(rf"(?:a partir de|desde|despu[ée]s de)\s*(?:las?\s*)?\b{palabra}\b(\s*y\s*media)?", limpio, re.IGNORECASE)
        if m_pal:
            mins = 30 if m_pal.group(1) else 0
            return normalizar_hora_humana(num_h, mins, limpio)
    return ""


def extract_before_from_mail(text: str) -> str:
    limpio = extract_preference_segment(text)
    mapa_num = {"una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12}

    m = re.search(r"(?:antes de|hasta|no m[áa]s tarde de|como tarde a)\s*(?:las?\s*)?([01]?\d|2[0-3])(?:\s*[:hH\.]\s*([0-5]\d))?(?:\s*y\s*media)?", limpio, re.IGNORECASE)
    if m:
        h = int(m.group(1))
        mins = int(m.group(2)) if m.group(2) else (30 if "media" in m.group(0).lower() else 0)
        return normalizar_hora_humana(h, mins, limpio)

    for palabra, num_h in mapa_num.items():
        m_pal = re.search(rf"(?:antes de|hasta|no m[áa]s tarde de|como tarde a)\s*(?:las?\s*)?\b{palabra}\b(\s*y\s*media)?", limpio, re.IGNORECASE)
        if m_pal:
            mins = 30 if m_pal.group(1) else 0
            return normalizar_hora_humana(num_h, mins, limpio)
    return ""


def extract_time_from_mail(text: str) -> str:
    """Extrae una hora específica pedida por el cliente (ej. '18:00', 'a las 6', 'sobre las seis y media').
    Si el cliente solo expresa un tramo ('a partir de las 10', 'por la tarde'), devuelve '' para usar la lógica de tramo."""
    limpio = extract_preference_segment(text)
    # Eliminar expresiones de rango ('a partir de...', 'antes de...') para no confundir el límite de un tramo con una hora fija
    sin_rangos = re.sub(
        r"(?:a partir de|desde|despu[ée]s de|antes de|hasta|no m[áa]s tarde de|como tarde a)\s*(?:las?\s*)?(?:[01]?\d|2[0-3]|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce)(?:\s*[:hH\.]\s*[0-5]\d)?(?:\s*y\s*media)?",
        " ",
        limpio,
        flags=re.IGNORECASE
    )

    # 1. Formato HH:MM o HH.MM o HHhMM (ej. 18:30, 19.00, 18h30)
    m = re.search(r"\b([01]?\d|2[0-3])\s*[:hH\.]\s*([0-5]\d)\b", sin_rangos)
    if m:
        return normalizar_hora_humana(int(m.group(1)), int(m.group(2)), sin_rangos)

    # 2. Formato "18h" o "18 horas" o "a las 6" o "sobre las 19 y media"
    m = re.search(r"(?:(?:a|sobre|hacia)\s+las?\s+|hora\s*[:\-]?\s*)([01]?\d|2[0-3])(?:\s*y\s*(media|cuarto))?\b", sin_rangos, re.IGNORECASE)
    if m:
        h = int(m.group(1))
        mins = 30 if (m.group(2) and "media" in m.group(2).lower()) else 0
        return normalizar_hora_humana(h, mins, sin_rangos)

    m = re.search(r"\b([01]?\d|2[0-3])\s*h(?:oras?)?\b", sin_rangos, re.IGNORECASE)
    if m:
        return normalizar_hora_humana(int(m.group(1)), 0, sin_rangos)

    # 3. Horas escritas con palabras ("sobre las seis", "a las siete y media", "las ocho")
    mapa_num = {
        "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
        "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
        "once": 11, "doce": 12
    }
    for palabra, num_h in mapa_num.items():
        patron_pal = rf"(?:(?:a|sobre|hacia)\s+las?\s+|\blas\s+){palabra}(?:\s+y\s+(media))?\b"
        m_pal = re.search(patron_pal, sin_rangos, re.IGNORECASE)
        if m_pal:
            mins = 30 if m_pal.group(1) else 0
            return normalizar_hora_humana(num_h, mins, sin_rangos)

    return ""


def minutes_of(time_str: str) -> Optional[int]:
    m = re.match(r"^(\d{1,2}):(\d{2})$", (time_str or "").strip())
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def str_from_minutes(total_m: int) -> str:
    return f"{str(total_m // 60).zfill(2)}:{str(total_m % 60).zfill(2)}"


def calcular_mejor_horario(
    fecha: str,
    version_nueva: str,
    hora_exacta_pedida: str,
    after: str,
    before: str,
    text: str,
    sesiones_ocupadas_dia: list,
    ahora: datetime
) -> tuple[Optional[str], Optional[str], bool]:
    """
    Calcula el mejor horario disponible siguiendo las 7 reglas de negocio:
    - Huecos posibles de 10:00 a 22:00 en intervalos de 30 min (:00 y :30).
    - Margen de 120 min (2h) para sesiones NORMAL y 150 min (2h30m) para sesiones PRO.
    - Si es para hoy, al menos 60 min después de la hora actual (`ahora`).
    - Si pide hora exacta -> busca la libre más cercana a esa hora.
    - Si pide tramo -> busca la libre más cercana a 12:00 (mañana) o 18:30 (tarde).
    - Si el turno pedido (mañana/tarde) está completo -> primera libre de tarde (si pidió mañana) o última libre de mañana (si pidió tarde).
    Devuelve: (mejor_hora_str, hora_referencia_cliente, fue_desplazada)
    """
    # 1. Generar todos los slots de 10:00 (600) a 22:00 (1320) cada 30 min
    todos_los_slots = list(range(10 * 60, 22 * 60 + 1, 30))

    # Regla 3: Al menos 1 hora más tarde de la hora de descarga si la reserva es para hoy
    hoy_str = ahora.strftime("%Y-%m-%d")
    minimo_por_reloj = 0
    if fecha == hoy_str:
        minimo_por_reloj = (ahora.hour * 60 + ahora.minute) + 60
    elif fecha < hoy_str:
        return None, hora_exacta_pedida or None, False

    duracion_nueva = 150 if (version_nueva or "").upper() == "PRO" else 120

    # 2. Filtrar los slots que respetan a TODAS las reservas y solicitudes del día (Reglas 1 y 2)
    slots_libres = []
    for cand in todos_los_slots:
        if cand < minimo_por_reloj:
            continue
        choca = False
        for oc in sesiones_ocupadas_dia:
            t_ex = minutes_of(oc.get("hora", ""))
            if t_ex is None:
                continue
            ver_ex = (oc.get("version") or "NORMAL").upper()
            dur_ex = 150 if ver_ex == "PRO" else 120

            if cand == t_ex:
                choca = True
                break
            elif cand < t_ex:
                # La nueva sesión empieza antes que la existente: debe terminar antes de t_ex
                if cand + duracion_nueva > t_ex:
                    choca = True
                    break
            else:
                # La nueva sesión empieza después que la existente: debe empezar cuando haya acabado la existente (2h o 2.5h)
                if t_ex + dur_ex > cand:
                    choca = True
                    break
        if not choca:
            slots_libres.append(cand)

    if not slots_libres:
        return None, hora_exacta_pedida or None, False

    # División Mañana (10:00 a 15:30 -> 600 a 930) y Tarde (16:00 a 22:00 -> 960 a 1320)
    LIMITE_MANANA = 15 * 60 + 30  # 15:30

    # 3. Determinar si el cliente pidió una hora concreta o un tramo
    min_rango = minutes_of(after) if after else None
    max_rango = minutes_of(before) if before else None

    if hora_exacta_pedida:
        objetivo = minutes_of(hora_exacta_pedida)
        es_preferencia_tarde = (objetivo >= 16 * 60)
        hora_ref_cliente = hora_exacta_pedida
    else:
        limpio_lower = extract_preference_segment(text).lower()
        mencion_manana = any(w in limpio_lower for w in ["mañana", "matí", "mediodia", "mediodía", "am"])
        mencion_tarde = any(w in limpio_lower for w in ["tarde", "noche", "noches", "vesprada", "pm"])

        if min_rango is not None and not mencion_manana and not mencion_tarde:
            es_preferencia_tarde = (min_rango >= 15 * 60)
        elif max_rango is not None and not mencion_manana and not mencion_tarde:
            es_preferencia_tarde = (max_rango > 16 * 60)
        elif mencion_manana and not mencion_tarde:
            es_preferencia_tarde = False
        else:
            # Si dice "tarde" o no especifica nada concreto, priorizamos según mención (mañana -> 12:00, tarde -> 18:30)
            es_preferencia_tarde = True if mencion_tarde else False

        objetivo = (18 * 60 + 30) if es_preferencia_tarde else (12 * 60)
        hora_ref_cliente = None

    # 4. Seleccionar candidatos dentro del turno preferido (respetando after/before si los hay)
    slots_manana = [s for s in slots_libres if s <= LIMITE_MANANA]
    slots_tarde = [s for s in slots_libres if s > LIMITE_MANANA]

    turno_preferido = slots_tarde if es_preferencia_tarde else slots_manana

    # Aplicar filtros "a partir de" / "antes de" si existen dentro del turno
    candidatos_turno = turno_preferido
    if min_rango is not None:
        filtrados = [s for s in candidatos_turno if s >= min_rango]
        if filtrados:
            candidatos_turno = filtrados
    if max_rango is not None:
        filtrados = [s for s in candidatos_turno if s <= max_rango]
        if filtrados:
            candidatos_turno = filtrados

    if candidatos_turno:
        # Elegir el hueco libre más cercano al objetivo (si hay empate, prioriza la hora solicitada o la más cercana)
        mejor_m = min(candidatos_turno, key=lambda s: abs(s - objetivo))
    else:
        # Regla 6: Si el turno pedido está completamente ocupado:
        # - Si pedía mañana -> primera libre de la tarde
        # - Si pedía tarde -> última libre de la mañana
        if not es_preferencia_tarde and slots_tarde:
            mejor_m = min(slots_tarde)  # Primera de la tarde
        elif es_preferencia_tarde and slots_manana:
            mejor_m = max(slots_manana)  # Última de la mañana
        else:
            mejor_m = min(slots_libres, key=lambda s: abs(s - objetivo))

    mejor_hora_str = str_from_minutes(mejor_m)
    fue_desplazada = bool(hora_ref_cliente and hora_ref_cliente != mejor_hora_str)
    return mejor_hora_str, hora_ref_cliente, fue_desplazada


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

    # Descargamos todas las reservas confirmadas y solicitudes pendientes para cruzar disponibilidad y duplicados
    reservas_activas = await reservas(env)
    solicitudes_activas = await solicitudes(env)
    todas_ocupadas = reservas_activas + solicitudes_activas
    ahora = datetime.now(TZ)

    actions = []
    for msg in messages:
        uid = str(msg.get("uid") or msg.get("id") or "")
        if not uid:
            continue
        
        detail = await hostinger_get("/api/v1/mailboxes/" + HOSTINGER_MAILBOX + "/folders/INBOX/messages/" + uid + "/text", env)
        subject = msg.get("subject") or detail.get("subject") or ""
        text = subject + "\n" + mail_text(detail)

        fecha = extract_date_from_mail(text)
        jugadores = extract_players_from_mail(text)
        version = extract_version_from_mail(text)
        telefono = extract_phone_from_mail(text)
        nombre = extract_name_from_mail(text, subject)

        if not fecha or jugadores is None:
            continue

        # --- MEJORA 3: Evitar duplicados del mismo cliente en la misma fecha ---
        tel_limpio = re.sub(r"\D", "", telefono or "")[-9:] if telefono else ""
        nombre_limpio = (nombre or "").strip().lower()
        ocupadas_mismo_dia = [x for x in todas_ocupadas if x.get("fecha") == fecha]

        es_duplicado = False
        for ex in ocupadas_mismo_dia:
            ex_tel = re.sub(r"\D", "", ex.get("telefono") or "")[-9:] if ex.get("telefono") else ""
            ex_nom = (ex.get("nombre") or "").strip().lower()
            if (tel_limpio and ex_tel and tel_limpio == ex_tel) or (nombre_limpio and ex_nom and nombre_limpio == ex_nom):
                es_duplicado = True
                break

        if es_duplicado:
            try:
                await hostinger_move(env, uid)
            except Exception:
                pass
            continue

        # Extraer preferencia de hora o tramo del correo
        hora_exacta_pedida = extract_time_from_mail(text)
        after = extract_after_from_mail(text)
        before = extract_before_from_mail(text)

        hora_asignada, hora_pedida_ref, fue_desplazada = calcular_mejor_horario(
            fecha=fecha,
            version_nueva=version,
            hora_exacta_pedida=hora_exacta_pedida,
            after=after,
            before=before,
            text=text,
            sesiones_ocupadas_dia=ocupadas_mismo_dia,
            ahora=ahora
        )

        # --- MEJORA 2: Si el día está 100% completo -> Pasar directamente a Solicitudes Rechazadas (por nosotros) ---
        if not hora_asignada:
            hora_ref = hora_exacta_pedida or after or "12:00"
            try:
                dt_base_rech = datetime.fromisoformat(fecha + "T" + hora_ref + ":00").replace(tzinfo=TZ)
                hora_utc_rech = dt_base_rech.astimezone(zoneinfo.ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")
            except Exception:
                hora_utc_rech = fecha + "T10:00:00Z"

            props_rech = {
                "Nombre": {"title": [{"text": {"content": nombre or "Solicitud web"}}]},
                "Fecha": {"date": {"start": fecha}},
                "Hora": {"date": {"start": hora_utc_rech}},
                "Jugadores": {"number": jugadores},
                "Versión": {"select": {"name": version}},
                "Tipo": {"select": {"name": "RECHAZADA"}},
                "Motivo": {"select": {"name": "Rechazada por nosotros"}}
            }
            if telefono:
                props_rech["Teléfono"] = {"phone_number": telefono}

            await notion("pages", env, {
                "parent": {"database_id": DB_NO_REALIZADAS},
                "properties": props_rech
            }, method="POST")

            try:
                await hostinger_move(env, uid)
            except Exception:
                pass

            mensaje_completo = (
                f"Hola{((' ' + nombre) if nombre else '')}, escribo de Fugarte Escape Room.\n\n"
                f"Muchas gracias por vuestro interés, pero lamentablemente tenemos el {fmt_date(fecha)} completo y no nos queda ningún hueco disponible.\n\n"
                "Si os viene bien cualquier otro día, estaremos encantados de recibiros.\n\n"
                "¡Muchas gracias y disculpad las molestias! 😊"
            )

            actions.append({
                "type": "REJECTED_FULL",
                "uid": uid,
                "subject": subject,
                "nombre": nombre,
                "fecha": fecha,
                "hora": hora_pedida_ref or "Día completo",
                "jugadores": jugadores,
                "version": version,
                "telefono": telefono,
                "whatsapp": wa(telefono, mensaje_completo),
                "reason": "🚫 DÍA COMPLETO: Movida automáticamente a Solicitudes Rechazadas (Rechazada por nosotros). Pulsa debajo para avisar al cliente por WhatsApp."
            })
            break

        # --- Creación normal o con hora alternativa cercana (MEJORA 1) ---
        dt_base = datetime.fromisoformat(fecha + "T" + hora_asignada + ":00").replace(tzinfo=TZ)
        hora_utc = dt_base.astimezone(zoneinfo.ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")

        nota_extra = f" (Pidió {hora_pedida_ref}, asignada {hora_asignada} por disponibilidad)" if fue_desplazada else ""

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
                "Notas": {"rich_text": [{"text": {"content": f"Solicitud recibida por Hostinger INBOX. UID {uid}.{nota_extra}"}}]}
            }
        })

        try:
            await hostinger_move(env, uid)
        except Exception:
            pass

        if fue_desplazada:
            mensaje = (
                f"Hola{((' ' + nombre) if nombre else '')}, escribo de Fugarte Escape Room.\n\n"
                f"Para el {fmt_date(fecha)} la hora que nos indicáis ({hora_pedida_ref}) ya está ocupada, pero tenemos disponibilidad a las {hora_asignada} para {jugadores} personas.\n\n"
                "Si os encaja ese horario y queréis confirmar la reserva, sería necesario hacer un Bizum de 10 € a este número de teléfono.\n\n"
                "Una vez recibido, dejamos la reserva confirmada.\n\n"
                "Si finalmente no estáis interesados, por favor contestad a este WhatsApp para que podamos ofrecer el horario a otros usuarios.\n\n"
                "¡Muchas gracias! 😊"
            )
            motivo_panel = f"⚠️ Pidió a las {hora_pedida_ref} (ocupado). Se ha asignado la hora libre más cercana: {hora_asignada}."
        else:
            mensaje = (
                f"Hola{((' ' + nombre) if nombre else '')}, escribo de Fugarte Escape Room.\n\n"
                f"Tenemos disponibilidad el {fmt_date(fecha)} a las {hora_asignada} para {jugadores} personas.\n\n"
                "Si queréis confirmar la reserva, sería necesario hacer un Bizum de 10 € a este número de teléfono.\n\n"
                "Una vez recibido, dejamos la reserva confirmada.\n\n"
                "Si finalmente no estáis interesados, por favor contestad a este WhatsApp para que podamos ofrecer el horario a otros usuarios.\n\n"
                "¡Muchas gracias! 😊"
            )
            motivo_panel = "Solicitud creada en Notion y correo movido a INBOX.Aceptadas."

        actions.append({
            "type": "CREATED",
            "uid": uid,
            "subject": subject,
            "requestId": created["id"],
            "nombre": nombre,
            "fecha": fecha,
            "hora": hora_asignada,
            "jugadores": jugadores,
            "version": version,
            "telefono": telefono,
            "whatsapp": wa(telefono, mensaje),
            "reason": motivo_panel
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
async def api_sesion_realizada(request: Request):
    try:
        body = await request.json()
        reservation_id = body.get("reservationId")
        if not reservation_id:
            raise HTTPException(status_code=400, detail="Falta el ID de la reserva.")

        reservation = await notion("pages/" + reservation_id, ENV_CONFIG, method="GET")
        p = reservation.get("properties", {})
        
        fecha_val = date_prop(p.get("Fecha"))
        h_val = hora(p.get("Hora"))
        
        hora_iso = None
        if fecha_val:
            t_str = h_val if (h_val and len(h_val) == 5) else "10:00"
            try:
                dt_base = datetime.fromisoformat(fecha_val + "T" + t_str + ":00").replace(tzinfo=TZ)
                hora_iso = dt_base.astimezone(zoneinfo.ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")
            except Exception:
                hora_iso = fecha_val + "T10:00:00Z"

        await notion("pages", ENV_CONFIG, {
            "parent": {"database_id": DB_SES},
            "properties": {
                "Nombre": {"title": [{"text": {"content": prop(p.get("Nombre")) or "Sesión"}}]},
                "Fecha": {"date": {"start": fecha_val}} if fecha_val else {"date": None},
                "Hora": {"date": {"start": hora_iso}} if hora_iso else {"date": None},
                "Jugadores": {"number": num(p.get("Jugadores"))},
                "Versión": {"select": {"name": prop(p.get("Versión")) or "NORMAL"}},
                "Origen": {"select": {"name": prop(p.get("Origen")) or "WEB"}},
                "Pago": {"select": {"name": "RECIBIDO"}},
                "Fecha registro": {"date": {"start": datetime.now(TZ).isoformat()}},
                "Reserva original": {"relation": [{"id": reservation_id}]}
            }
        }, method="POST")

        await notion_update_page(reservation_id, ENV_CONFIG, {"Pago": {"select": {"name": "RECIBIDO"}}})
        return {"ok": True, "alreadyExists": False}
    except Exception as e:
        print("ERROR EN SESIÓN REALIZADA:", str(e))
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@app.post("/api/suspender-reserva")
async def api_suspender_reserva(request: Request):
    try:
        body = await request.json()
        reservation_id = body.get("reservationId")
        motivo = body.get("motivo")
        senal = body.get("senal")

        if not reservation_id:
            raise HTTPException(status_code=400, detail="Falta el ID de la reserva a suspender.")
            
        reservation = await notion("pages/" + reservation_id, ENV_CONFIG, method="GET")
        p = reservation.get("properties", {})
        
        fecha_val = date_prop(p.get("Fecha"))
        h_val = hora(p.get("Hora"))
        
        hora_iso = None
        if fecha_val:
            t_str = h_val if (h_val and len(h_val) == 5) else "10:00"
            try:
                dt_base = datetime.fromisoformat(fecha_val + "T" + t_str + ":00").replace(tzinfo=TZ)
                hora_iso = dt_base.astimezone(zoneinfo.ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")
            except Exception:
                hora_iso = fecha_val + "T10:00:00Z"

        props = {
            "Nombre": {"title": [{"text": {"content": prop(p.get("Nombre")) or "Reserva suspendida"}}]},
            "Fecha": {"date": {"start": fecha_val}} if fecha_val else {"date": None},
            "Hora": {"date": {"start": hora_iso}} if hora_iso else {"date": None},
            "Jugadores": {"number": num(p.get("Jugadores"))},
            "Versión": {"select": {"name": prop(p.get("Versión")) or "NORMAL"}}
        }
        
        tel = phone(p.get("Teléfono"))
        if tel:
            props["Teléfono"] = {"phone_number": tel}

        if motivo:
            props["Motivo"] = {"select": {"name": motivo}}
        if senal:
            props["Señal"] = {"select": {"name": senal}}
        
        props["Tipo"] = {"select": {"name": "SUSPENDIDA"}}

        await notion("pages", ENV_CONFIG, {
            "parent": {"database_id": DB_NO_REALIZADAS},
            "properties": props
        }, method="POST")
        
        try:
            await notion_archive_page(reservation_id, ENV_CONFIG)
        except Exception:
            await notion_update_page(reservation_id, ENV_CONFIG, {"Estado": {"select": {"name": "CANCELADA"}}})

        return {"ok": True}
        
    except Exception as e:
        print("ERROR EN SUSPENSIÓN:", str(e))
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@app.post("/api/rechazar-solicitud")
async def api_rechazar_solicitud(request: Request):
    try:
        body = await request.json()
        request_id = body.get("requestId")
        motivo = body.get("motivo")

        if not request_id:
            raise HTTPException(status_code=400, detail="Falta el ID de la solicitud.")

        r = await notion("pages/" + request_id, ENV_CONFIG, method="GET")
        p = r.get("properties", {})
        
        fecha_val = date_prop(p.get("Fecha"))
        h_val = hora(p.get("Hora"))
        
        hora_iso = None
        if fecha_val:
            t_str = h_val if (h_val and len(h_val) == 5) else "10:00"
            try:
                dt_base = datetime.fromisoformat(fecha_val + "T" + t_str + ":00").replace(tzinfo=TZ)
                hora_iso = dt_base.astimezone(zoneinfo.ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")
            except Exception:
                hora_iso = fecha_val + "T10:00:00Z"
        
        props = {
            "Nombre": {"title": [{"text": {"content": prop(p.get("Nombre")) or "Solicitud rechazada"}}]},
            "Fecha": {"date": {"start": fecha_val}} if fecha_val else {"date": None},
            "Hora": {"date": {"start": hora_iso}} if hora_iso else {"date": None},
            "Jugadores": {"number": num(p.get("Jugadores"))},
            "Versión": {"select": {"name": prop(p.get("Versión")) or "NORMAL"}}
        }
        
        tel = phone(p.get("Teléfono"))
        if tel:
            props["Teléfono"] = {"phone_number": tel}

        if motivo:
            props["Motivo"] = {"select": {"name": motivo}}
        
        props["Tipo"] = {"select": {"name": "RECHAZADA"}}

        await notion("pages", ENV_CONFIG, {
            "parent": {"database_id": DB_NO_REALIZADAS},
            "properties": props
        }, method="POST")

        try:
            await notion_archive_page(request_id, ENV_CONFIG)
        except Exception:
            await notion_update_page(request_id, ENV_CONFIG, {"Estado": {"select": {"name": "CANCELADA"}}})

        return {"ok": True}
        
    except Exception as e:
        print("ERROR EN RECHAZO:", str(e))
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@app.post("/api/confirmar-solicitud")
async def api_confirmar_solicitud(request: Request):
    try:
        body = await request.json()
        request_id = body.get("requestId")
        if not request_id:
            raise HTTPException(status_code=400, detail="Falta el ID de la solicitud.")

        r = await notion("pages/" + request_id, ENV_CONFIG, method="GET")
        p = r.get("properties", {})
        fecha = date_prop(p.get("Fecha"))
        h_val = hora(p.get("Hora"))
        
        dt_base = datetime.fromisoformat(fecha + "T" + (h_val if len(h_val) == 5 else "10:00") + ":00").replace(tzinfo=TZ)
        h_utc = dt_base.astimezone(zoneinfo.ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")

        await notion("pages", ENV_CONFIG, {
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
        }, method="POST")
        await notion_update_page(request_id, ENV_CONFIG, {"Estado": {"select": {"name": "CONFIRMADA"}}})
        return {"ok": True, "nombre": prop(p.get("Nombre")), "fecha": fecha, "hora": h_val}
    except Exception as e:
        print("ERROR EN CONFIRMAR:", str(e))
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


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
const idToSend = targetIdToProcess;
cerrarModales();

try{
const r=await fetch("/api/rechazar-solicitud",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({requestId: idToSend, motivo: motivo})});
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
const idToSend = targetIdToProcess;
cerrarModales();

try{
const r=await fetch("/api/suspender-reserva",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({reservationId: idToSend, motivo: motivo, senal: senal})});
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
output+='<div class="info">📅 '+safe(x.fechaTexto)+"<br>🕐 "+safe(n.horaTexto ? x.horaTexto : "")+"<br>👥 "+safe(x.jugadores)+" personas<br>🎯 "+safe(x.version)+"</div>";
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