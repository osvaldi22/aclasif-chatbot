import os
import uuid
import re
import requests
import base64
from io import BytesIO
from PIL import Image, ImageEnhance, ImageFilter
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

app = Flask(__name__)
CORS(app)

# ---------------------------
# CONFIGURACIONES
# ---------------------------
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# ---------- TELEGRAM ----------
# NO TOCAR: estos datos son los que ya te funcionaban.
TELEGRAM_BOT_TOKEN = "8753872074:AAFub-e8qrfNhVvcLX46Kb_jpLUBzlSAJLA"
TELEGRAM_ADMIN_BOT_TOKEN = "8753184281:AAEaPQSD93oiRRkankYiVGY863pyvduuveA"
TELEGRAM_ADMIN_CHAT_ID = "1857096780"

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------------------------
# PROMPT DEL ASISTENTE
# ---------------------------
SYSTEM_PROMPT = """
Sos el asistente oficial de Aclasif 🇵🇾.
REGLAS DE ORO ABSOLUTAS: 
- Aclasif es el ÚNICO INTERMEDIARIO en las ventas. Garantizamos compras 100% seguras.
- NUNCA le digas al cliente que contacte o hable directamente con el vendedor original.
- NUNCA digas que no manejamos pagos. Nosotros gestionamos el cobro por seguridad.
- NO des nombres propios de asesores ni dueños (prohibido decir nombres).
- ⚠️ REGLA DE FORMATO: NO uses formato Markdown. NO uses asteriscos (**) ni negritas. Escribe TODO en texto plano limpio.

PROCESO DE COMPRA OFICIAL:
1. Confirma la recepción del artículo o código de manera breve y profesional.
2. Explicale que en Aclasif actuamos como intermediarios para garantizar una compra totalmente segura.
3. Decile que el siguiente paso es gestionar el pago con nosotros.
4. Dale ÚNICAMENTE este link para que se ponga en contacto con Ventas, gestione el pago, envíe el comprobante y finalice la compra: https://wa.me/595981784334

PRECIO Y DATOS DE COMPRA:
- Si en el contexto interno aparece un precio de compra, artículo, código ART, orden o link, usá esos datos exactos.
- Si el cliente pregunta "cuál era el precio" o "cuánto cuesta", y el contexto trae precio, respondé con el precio exacto.

⚠️ MANEJO DE RECLAMOS:
Cuando un cliente quiera hacer un reclamo, seguí este proceso:
1. Preguntar el nombre completo.
2. Preguntar correo o teléfono.
3. Preguntar el número de pedido o nombre del producto.
4. Solicitar descripción del problema.
5. Finalizá ÚNICAMENTE con esta frase exacta: "✅ Reclamo registrado. Un agente se contactará en Horario laboral con Usted."

ESTILO:
Sé humano, amable, estilo paraguayo, directo y breve.
"""

conversaciones = {}

# ---------------------------
# FUNCIONES DE UTILIDAD
# ---------------------------

def valor_limpio(*valores, default="No especificado"):
    for valor in valores:
        if valor is None:
            continue
        texto = str(valor).strip()
        if texto:
            return texto
    return default


def normalizar_texto(valor):
    return str(valor or "").strip()


def formatear_precio(valor):
    if valor is None:
        return "No especificado"

    texto = str(valor).strip()

    if not texto or texto.lower() in ["none", "null", "nan"]:
        return "No especificado"

    if "Gs" in texto or "₲" in texto or "USD" in texto or "$" in texto:
        return texto

    texto_num = texto.replace(".", "", texto.count(".") - 1) if texto.count(".") > 1 else texto
    texto_num = texto_num.replace(",", ".")

    try:
        numero = float(texto_num)

        if numero.is_integer():
            return f"Gs. {int(numero):,}".replace(",", ".")

        return f"Gs. {numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    except:
        return texto


def crear_contexto_compra_texto(compra):
    if not compra:
        return ""

    return f"""
CONTEXTO INTERNO DE LA COMPRA ACTUAL:
- N° de Orden: {compra.get("order", "No especificado")}
- Producto: {compra.get("titulo_producto", compra.get("producto", "No especificado"))}
- Código ART: {compra.get("codigo_articulo", compra.get("article_code", "No especificado"))}
- Precio: {compra.get("precio", "No especificado")}
- Link del artículo: {compra.get("link_articulo", "No especificado")}
- Nombre comprador: {compra.get("nombre", "No especificado")}
- WhatsApp comprador: {compra.get("whatsapp", "No especificado")}
- Email comprador: {compra.get("email", "No especificado")}
- Vendedor: {compra.get("vendedor_nombre", "No especificado")}
- WhatsApp vendedor: {compra.get("vendedor_whatsapp", "No especificado")}
"""


def consultar_deepseek(mensaje, session_id, extra_context=""):
    if session_id not in conversaciones:
        conversaciones[session_id] = {
            "mensajes": [],
            "ultimo_mensaje": datetime.now(timezone.utc).isoformat(),
            "user_id": None,
            "compra": None
        }

    sesion = conversaciones[session_id]
    sesion["ultimo_mensaje"] = datetime.now(timezone.utc).isoformat()

    messages = [{"role": "system", "content": SYSTEM_PROMPT + extra_context}]

    for msg in sesion["mensajes"][-10:]:
        messages.append(msg)

    messages.append({"role": "user", "content": mensaje})

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 500
    }

    resp = requests.post(
        "https://api.deepseek.com/v1/chat/completions",
        json=payload,
        headers=headers,
        timeout=30
    )

    resp.raise_for_status()
    respuesta = resp.json()["choices"][0]["message"]["content"]

    sesion["mensajes"].append({"role": "user", "content": mensaje})
    sesion["mensajes"].append({"role": "assistant", "content": respuesta})

    return respuesta


def notificar_telegram(mensaje):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_ADMIN_BOT_TOKEN}/sendMessage"

        resp = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_ADMIN_CHAT_ID,
                "text": mensaje,
                "parse_mode": "HTML"
            },
            timeout=10
        )

        print("📨 Telegram:", resp.status_code, resp.text)
        return resp.status_code == 200

    except Exception as e:
        print("❌ Error Telegram:", e)
        return False


# ---------------------------
# ENDPOINTS DEL CHAT
# ---------------------------

@app.route("/api/chat-web", methods=["POST"])
def chat_web():
    data = request.json or {}

    mensaje = data.get("mensaje", "")
    session_id = data.get("session_id", "anon")
    user_id = data.get("user_id", None)

    if session_id not in conversaciones:
        conversaciones[session_id] = {
            "mensajes": [],
            "ultimo_mensaje": datetime.now(timezone.utc).isoformat(),
            "user_id": user_id,
            "compra": None
        }

    sesion = conversaciones[session_id]
    sesion["ultimo_mensaje"] = datetime.now(timezone.utc).isoformat()

    if user_id:
        sesion["user_id"] = user_id

    try:
        respuesta = consultar_deepseek(
            mensaje,
            session_id,
            crear_contexto_compra_texto(sesion.get("compra"))
        )

    except Exception as e:
        print("Error DeepSeek chat:", e)
        respuesta = "Lo siento, tuve un problema de conexión. ¿Me repetís kape?"

    palabras_reclamo = ["reclamo", "estafa", "no recibí", "abogado", "devuelvan", "reembolso"]

    if any(p in mensaje.lower() for p in palabras_reclamo):
        notificar_telegram(
            f"🚨 <b>RECLAMO URGENTE</b>\nSesión: {session_id}\nMensaje: {mensaje[:200]}"
        )

    return jsonify({"respuesta": respuesta})


@app.route("/api/historial/<session_id>", methods=["GET"])
def obtener_historial(session_id):
    sesion = conversaciones.get(session_id)

    if not sesion:
        return jsonify({"messages": []})

    return jsonify({"messages": sesion["mensajes"]})


# ---------------------------
# MODERACIÓN BLINDADA V2
# ---------------------------

def detectar_contacto_regex(texto):
    """
    Filtro duro.
    Si encuentra contacto evidente, contacto camuflado, flyer de captación,
    o invitación a contactar fuera de Aclasif, suspende directo.
    """
    if not texto:
        return False, ""

    original = texto
    t = texto.lower()

    palabras_bloqueadas = [
        "whatsapp", "wpp", "wasap", "whats", "wa.me", "teléfono", "telefono",
        "tel ", "celular", "cel ", "nro", "numero", "número", "llamame",
        "llámame", "contactame", "contáctame", "contactanos", "contáctanos",
        "consultanos", "consúltanos", "consulta al", "consultas al",
        "escribime", "escríbeme", "mensajeame", "mi numero", "mi número",
        "mi whatsapp", "gmail", "hotmail", "outlook", "yahoo", "@gmail",
        "@hotmail", "instagram", "insta", "facebook", "telegram", "t.me",
        "fb", "ig", "seguime", "inbox", "dm", "directo", "direct",
        "socios", "socio", "dropshipping", "drop shipping", "buscando",
        "buscamos", "emprendedores", "revendedores", "distribuidores",
        "distribuidor", "plataforma", "ofrecer productos", "negocio",
        "ganancias", "asesor", "asesores"
    ]

    for palabra in palabras_bloqueadas:
        if palabra in t:
            return True, f"Contiene palabra prohibida o contacto externo: {palabra}"

    if re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", original):
        return True, "Contiene email"

    if re.search(r"(https?://|www\.|wa\.me/|t\.me/|\.com|\.net|\.org|\.py)", t):
        return True, "Contiene link externo"

    compactado = re.sub(r"[\s\-\.\(\)\+_/,:;|]+", "", original)

    if re.search(r"\d{7,}", compactado):
        return True, "Contiene número largo tipo teléfono"

    if re.search(r"(09\d{7,9}|595\d{8,10}|0\s*9\s*\d)", compactado):
        return True, "Contiene número paraguayo o WhatsApp"

    palabras_numeros = [
        "cero", "uno", "dos", "tres", "cuatro", "cinco",
        "seis", "siete", "ocho", "nueve"
    ]

    contador_palabras_numero = sum(1 for p in palabras_numeros if p in t)

    if contador_palabras_numero >= 4:
        return True, "Contiene número escrito en palabras"

    return False, ""


def preparar_imagen_para_ocr(img):
    """
    Mejora la imagen para OCR: aumenta tamaño, contraste y nitidez.
    """
    if img.mode != "RGB":
        img = img.convert("RGB")

    max_size = 1800

    if max(img.size) < max_size:
        scale = max_size / max(img.size)
        new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
        img = img.resize(new_size)

    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.8)

    enhancer = ImageEnhance.Sharpness(img)
    img = enhancer.enhance(1.6)

    img = img.filter(ImageFilter.SHARPEN)

    return img


def ocr_space_desde_pil(img, etiqueta="full"):
    try:
        img = preparar_imagen_para_ocr(img)

        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=92)

        imagen_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
        base64_str = f"data:image/jpeg;base64,{imagen_b64}"

        api_url = "https://api.ocr.space/parse/image"

        payload = {
            "apikey": os.environ.get("OCR_SPACE_API_KEY", "helloworld"),
            "base64Image": base64_str,
            "language": "spa",
            "isOverlayRequired": False,
            "scale": True,
            "OCREngine": 2
        }

        resp = requests.post(api_url, data=payload, timeout=35)
        resp.raise_for_status()

        resultado = resp.json()

        if resultado.get("IsErroredOnProcessing"):
            msg_error = resultado.get("ErrorMessage", "Error OCR desconocido")
            print(f"❌ OCR error en {etiqueta}: {msg_error}")
            return ""

        textos = []

        for item in resultado.get("ParsedResults", []):
            parsed = item.get("ParsedText", "")
            if parsed:
                textos.append(parsed)

        texto_final = " ".join(textos).strip()
        print(f"👀 OCR {etiqueta}: {texto_final}")

        return texto_final

    except Exception as e:
        print(f"❌ Error OCR {etiqueta}: {e}")
        return ""


def extraer_texto_de_imagen(image_url: str) -> str:
    """
    OCR múltiple:
    - imagen completa
    - parte superior
    - centro
    - parte inferior
    Esto ayuda a leer flyers donde el teléfono suele estar abajo.
    """
    try:
        print("📸 Descargando imagen para OCR V2...")
        img_resp = requests.get(image_url, timeout=20)
        img_resp.raise_for_status()

        img_original = Image.open(BytesIO(img_resp.content))

        if img_original.mode != "RGB":
            img_original = img_original.convert("RGB")

        w, h = img_original.size

        zonas = []

        zonas.append(("completa", img_original))

        if h > 300 and w > 200:
            zonas.append(("superior", img_original.crop((0, 0, w, int(h * 0.38)))))
            zonas.append(("centro", img_original.crop((0, int(h * 0.25), w, int(h * 0.75)))))
            zonas.append(("inferior", img_original.crop((0, int(h * 0.55), w, h))))

        textos = []

        for etiqueta, zona in zonas:
            texto = ocr_space_desde_pil(zona, etiqueta)

            if texto:
                textos.append(texto)

        texto_final = " ".join(textos).strip()
        texto_final = re.sub(r"\s+", " ", texto_final)

        print(f"👀 TEXTO OCR FINAL: {texto_final}")

        return texto_final

    except Exception as e:
        print(f"❌ Error crítico en OCR V2: {e}")
        return ""


def analizar_imagen_con_deepseek(image_url):
    """
    Reglas estrictas:
    - Sin imagen: pending.
    - OCR sin texto: pending.
    - Regex detecta contacto/flyer/redes: suspended.
    - IA detecta contacto: suspended.
    - Si OCR extrae demasiado texto de flyer pero IA no es clara: pending.
    """
    if not image_url:
        return "PENDIENTE", "Sin imagen para verificar"

    texto_extraido = extraer_texto_de_imagen(image_url)

    if not texto_extraido:
        return "PENDIENTE", "OCR no pudo leer texto de la imagen. Revisión manual necesaria."

    contacto, motivo_regex = detectar_contacto_regex(texto_extraido)

    if contacto:
        return "SUSPENDER", f"Imagen: {motivo_regex}"

    if len(texto_extraido) > 80:
        palabras_sospechosas = [
            "buscando", "socios", "dropshipping", "contact", "consulta",
            "whatsapp", "facebook", "instagram", "telegram", "emprendedor",
            "plataforma", "ganancia", "asesor"
        ]

        t = texto_extraido.lower()

        if any(p in t for p in palabras_sospechosas):
            return "SUSPENDER", "Imagen tipo flyer/publicidad con posible contacto externo"

    prompt = f"""Analiza el siguiente texto extraído de la imagen de un producto.
Tu objetivo es ser EXTREMADAMENTE ESTRICTO y detectar DATOS DE CONTACTO PERSONALES o publicidad de captación externa.

TEXTO EXTRAÍDO:
{texto_extraido}

DEBES SUSPENDER SI HAY:
1. Números de teléfono o WhatsApp.
2. Números camuflados con símbolos, espacios o palabras.
3. Emails.
4. Links externos.
5. Instagram, Facebook, Telegram, @usuario, redes sociales.
6. Frases como contactame, escribime, consultanos, mi número, mi WhatsApp, inbox, DM.
7. Flyer de socios, dropshipping, revendedores, asesores, plataforma externa o captación de personas.

Responde EXACTAMENTE:
APROBAR
SUSPENDER: motivo
"""

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "system",
                "content": "Eres un moderador estricto. Solo respondes APROBAR o SUSPENDER."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0,
        "max_tokens": 120
    }

    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=25
        )

        resp.raise_for_status()
        respuesta = resp.json()["choices"][0]["message"]["content"].strip()

        print(f"🤖 Decisión IA Imagen: {respuesta}")

        if respuesta.upper().startswith("SUSPENDER"):
            motivo = respuesta.split(":", 1)[1].strip() if ":" in respuesta else "Contacto oculto en imagen"
            return "SUSPENDER", motivo

        if respuesta.upper().startswith("APROBAR"):
            return "APROBAR", "Imagen aprobada"

        return "PENDIENTE", f"Respuesta IA imagen no clara: {respuesta}"

    except Exception as e:
        print("❌ Error IA imagen:", e)
        return "PENDIENTE", f"Error IA imagen: {str(e)}"


def analizar_listing_con_deepseek(title, description, image_url=None):
    texto_total = f"{title}\n{description}"

    contacto_texto, motivo_texto_regex = detectar_contacto_regex(texto_total)

    if contacto_texto:
        return "suspended", f"Texto: {motivo_texto_regex}"

    decision_imagen, motivo_imagen = analizar_imagen_con_deepseek(image_url)

    if decision_imagen == "SUSPENDER":
        return "suspended", f"Imagen: {motivo_imagen}"

    if decision_imagen == "PENDIENTE":
        return "pending", motivo_imagen

    prompt = f"""Eres un moderador automático IMPLACABLE de Aclasif.
Detecta DATOS DE CONTACTO PERSONALES DIRECTOS O CAMUFLADOS en el título/descripción.

TÍTULO:
{title}

DESCRIPCIÓN:
{description}

SUSPENDER SI HAY:
1. Teléfonos o WhatsApp.
2. Números camuflados.
3. Redes sociales o links.
4. Emails.
5. Frases de contacto directo.
6. Direcciones exactas para evitar la plataforma.
7. Captación de socios, dropshipping, revendedores o asesor externo.

Responde EXACTAMENTE:
APROBAR
SUSPENDER: motivo
"""

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "system",
                "content": "Solo respondes APROBAR o SUSPENDER."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0,
        "max_tokens": 120
    }

    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=25
        )

        resp.raise_for_status()
        respuesta = resp.json()["choices"][0]["message"]["content"].strip()

        print(f"🤖 Decisión IA Texto: {respuesta}")

        if respuesta.upper().startswith("SUSPENDER"):
            motivo = respuesta.split(":", 1)[1].strip() if ":" in respuesta else "Contacto en texto"
            return "suspended", f"Texto: {motivo}"

        if respuesta.upper().startswith("APROBAR"):
            return "verified", "Aprobado automáticamente por texto e imagen."

        return "pending", f"Respuesta IA texto no clara: {respuesta}"

    except Exception as e:
        print("❌ Error IA texto:", e)
        return "pending", f"Error IA texto: {str(e)}"


@app.route("/api/moderar-listing", methods=["POST"])
def moderar_listing():
    data = request.json or {}
    listing_id = data.get("listing_id")

    if not listing_id:
        return jsonify({"success": False, "error": "Falta listing_id"}), 400

    try:
        listing_resp = supabase.table("listings").select("*").eq("id", listing_id).single().execute()

        if not listing_resp.data:
            return jsonify({"success": False, "error": "No encontrado"}), 404

        listing = listing_resp.data

        decision, nota = analizar_listing_con_deepseek(
            listing.get("title", ""),
            listing.get("description", ""),
            listing.get("image_url", "")
        )

        update_data = {
            "moderation_status": decision,
            "moderation_note": nota,
            "is_active": decision == "verified",
            "last_reviewed_at": datetime.now(timezone.utc).isoformat()
        }

        if decision == "verified":
            update_data["verified_at"] = datetime.now(timezone.utc).isoformat()

        supabase.table("listings").update(update_data).eq("id", listing_id).execute()

        if decision == "suspended":
            notificar_telegram(
                f"🚫 <b>PUBLICACIÓN SUSPENDIDA POR IA</b>\n"
                f"Producto: {listing.get('title', '')}\n"
                f"ID: {listing_id}\n"
                f"Motivo: {nota}"
            )

        if decision == "pending":
            notificar_telegram(
                f"⚠️ <b>PUBLICACIÓN PENDIENTE DE REVISIÓN</b>\n"
                f"Producto: {listing.get('title', '')}\n"
                f"ID: {listing_id}\n"
                f"Motivo: {nota}"
            )

        return jsonify({
            "success": True,
            "listing_id": listing_id,
            "decision": decision,
            "nota": nota
        })

    except Exception as e:
        print("❌ Error moderar_listing:", e)

        try:
            supabase.table("listings").update({
                "moderation_status": "pending",
                "moderation_note": f"Error moderación backend: {str(e)}",
                "is_active": False,
                "last_reviewed_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", listing_id).execute()
        except:
            pass

        return jsonify({
            "success": False,
            "error": str(e),
            "decision": "pending"
        }), 500


# ---------------------------
# WEBHOOK TELEGRAM PÚBLICO
# ---------------------------

@app.route("/webhook/telegram", methods=["POST"])
def webhook_telegram():
    data = request.json or {}

    if "message" not in data:
        return "OK", 200

    chat_id = data["message"]["chat"]["id"]
    texto = data["message"].get("text", "")

    try:
        respuesta = consultar_deepseek(texto, chat_id, "")

        if TELEGRAM_BOT_TOKEN:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": respuesta,
                    "parse_mode": "HTML"
                },
                timeout=10
            )

    except Exception as e:
        print("Error Telegram webhook:", e)

    return "OK", 200


# ---------------------------
# COMPRA / TELEGRAM / SUPABASE
# ---------------------------

def buscar_order(order):
    order = normalizar_texto(order)

    if not order or order.lower() in ["ninguna", "no especificado"]:
        return None

    columnas_orden = ["order_number", "id", "idx", "order", "order_id", "numero_orden", "nro_orden"]

    for columna in columnas_orden:
        try:
            res = supabase.table("orders").select("*").eq(columna, order).limit(1).execute()

            if res.data:
                return res.data[0]

        except:
            pass

        try:
            if str(order).isdigit():
                res = supabase.table("orders").select("*").eq(columna, int(order)).limit(1).execute()

                if res.data:
                    return res.data[0]

        except:
            pass

    return None


def buscar_listing_por_id(listing_id):
    listing_id = normalizar_texto(listing_id)

    if not listing_id:
        return None

    try:
        res = supabase.table("listings").select("*").eq("id", listing_id).limit(1).execute()

        if res.data:
            return res.data[0]

    except:
        pass

    return None


def buscar_listing(producto="", article_code="", order=""):
    producto = normalizar_texto(producto)
    article_code = normalizar_texto(article_code)
    order = normalizar_texto(order)

    orden_data = buscar_order(order)

    if orden_data:
        posibles_listing_id = [
            orden_data.get("listing_id"),
            orden_data.get("listingId"),
            orden_data.get("product_id")
        ]

        for lid in posibles_listing_id:
            listing = buscar_listing_por_id(lid)

            if listing:
                return listing

        posibles_art_order = [
            orden_data.get("article_code"),
            orden_data.get("codigo_articulo"),
            orden_data.get("art")
        ]

        for cod in posibles_art_order:
            if cod:
                try:
                    res = supabase.table("listings").select("*").ilike("article_code", f"%{cod}%").limit(1).execute()

                    if res.data:
                        return res.data[0]

                except:
                    pass

    posibles_codigos = [article_code] if article_code else []

    if producto.upper().startswith("ART-"):
        posibles_codigos.append(producto)

    for codigo in posibles_codigos:
        try:
            res = supabase.table("listings").select("*").ilike("article_code", f"%{codigo}%").limit(1).execute()

            if res.data:
                return res.data[0]

        except:
            pass

    if producto:
        try:
            res = supabase.table("listings").select("*").ilike("title", f"%{producto}%").limit(1).execute()

            if res.data:
                return res.data[0]

        except:
            pass

    return None


def buscar_perfil_vendedor(seller_id):
    if not seller_id:
        return None

    tablas = ["perfiles", "profiles", "usuarios", "users"]

    for tabla in tablas:
        try:
            res = supabase.table(tabla).select("*").eq("id", seller_id).limit(1).execute()

            if res.data:
                return res.data[0]

        except:
            pass

    return None


def sacar_datos_vendedor(listing_data, order_data=None):
    listing_data = listing_data or {}
    order_data = order_data or {}

    seller_id = listing_data.get("user_id") or order_data.get("seller_id")

    vendedor_nombre = valor_limpio(
        listing_data.get("seller_name"),
        default="Sin nombre"
    )

    vendedor_whatsapp = valor_limpio(
        listing_data.get("seller_whatsapp"),
        default="Sin teléfono"
    )

    perfil = buscar_perfil_vendedor(seller_id)

    if perfil:
        vendedor_nombre = valor_limpio(
            perfil.get("nombre"),
            perfil.get("full_name"),
            default=vendedor_nombre
        )

        vendedor_whatsapp = valor_limpio(
            perfil.get("whatsapp"),
            perfil.get("telefono"),
            default=vendedor_whatsapp
        )

    return {
        "seller_id": seller_id or "No encontrado",
        "vendedor_nombre": vendedor_nombre,
        "vendedor_whatsapp": vendedor_whatsapp
    }


def sacar_precio_listing(listing_data, order_data=None, data=None):
    listing_data = listing_data or {}
    order_data = order_data or {}
    data = data or {}

    precio = valor_limpio(
        order_data.get("total_usd"),
        data.get("precio"),
        listing_data.get("precio"),
        listing_data.get("price_usd"),
        default="No especificado"
    )

    return formatear_precio(precio)


def construir_link_articulo(listing_data, data, codigo_articulo=""):
    listing_data = listing_data or {}
    base = (FRONTEND_URL or "").strip().rstrip("/")

    codigo = valor_limpio(
        codigo_articulo,
        listing_data.get("article_code"),
        default=""
    )

    if base and codigo and codigo != "No especificado":
        return f"{base}/producto/{codigo}"

    link_enviado = valor_limpio(data.get("link_articulo"), default="")

    if link_enviado and "/chat" not in link_enviado:
        return link_enviado

    return "Link no encontrado"


@app.route("/api/notificar-compra", methods=["POST"])
def notificar_compra():
    data = request.json or {}

    session_id = data.get("session_id") or "anon"
    producto_recibido = valor_limpio(data.get("producto"), default="")
    article_code_recibido = valor_limpio(data.get("article_code"), default="")
    nombre = valor_limpio(data.get("nombre"))
    whatsapp = valor_limpio(data.get("whatsapp"))
    email = valor_limpio(data.get("email"))
    order = valor_limpio(data.get("order"), default="Ninguna")

    order_data = buscar_order(order)
    listing_data = buscar_listing(
        producto=producto_recibido,
        article_code=article_code_recibido,
        order=order
    )

    if listing_data:
        titulo_producto = valor_limpio(
            listing_data.get("title"),
            producto_recibido,
            default="Título no encontrado"
        )

        codigo_articulo = valor_limpio(
            listing_data.get("article_code"),
            article_code_recibido,
            default="No especificado"
        )

    else:
        titulo_producto = valor_limpio(
            producto_recibido,
            default="Título no encontrado"
        )

        codigo_articulo = valor_limpio(
            article_code_recibido,
            default="No especificado"
        )

    vendedor = sacar_datos_vendedor(listing_data, order_data)
    precio = sacar_precio_listing(listing_data, order_data, data)
    link_articulo = construir_link_articulo(listing_data, data, codigo_articulo)

    compra_contexto = {
        "producto": producto_recibido,
        "titulo_producto": titulo_producto,
        "codigo_articulo": codigo_articulo,
        "precio": precio,
        "order": order,
        "nombre": nombre,
        "whatsapp": whatsapp,
        "email": email,
        "link_articulo": link_articulo,
        "seller_id": vendedor["seller_id"],
        "vendedor_nombre": vendedor["vendedor_nombre"],
        "vendedor_whatsapp": vendedor["vendedor_whatsapp"]
    }

    if session_id not in conversaciones:
        conversaciones[session_id] = {
            "mensajes": [],
            "ultimo_mensaje": datetime.now(timezone.utc).isoformat(),
            "user_id": None,
            "compra": compra_contexto
        }

    else:
        conversaciones[session_id]["compra"] = compra_contexto

    mensaje = f"""🚨 <b>NUEVA INTENCIÓN DE COMPRA</b> 🚨
📦 <b>Producto:</b> {titulo_producto}
🏷️ <b>Código (ART):</b> {codigo_articulo}
💰 <b>Precio final:</b> {precio}
🔗 <b>Link del artículo:</b> {link_articulo}
📝 <b>N° de Orden:</b> {order}

🛒 <b>DATOS DEL COMPRADOR:</b>
👤 Nombre: {nombre}
📱 WhatsApp: {whatsapp}
✉️ Email: {email}

🏪 <b>DATOS DEL VENDEDOR:</b>
👤 Nombre: {vendedor["vendedor_nombre"]}
📱 WhatsApp: {vendedor["vendedor_whatsapp"]}
🆔 ID Vendedor: {vendedor["seller_id"]}
"""

    enviado = notificar_telegram(mensaje)

    return jsonify({
        "success": True,
        "telegram_enviado": enviado,
        "compra": compra_contexto
    })


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "ok",
        "frontend_url": FRONTEND_URL,
        "moderacion": "blindada-v2-flyer",
        "regla": "si OCR falla queda pending; si detecta flyer/contacto/socios/dropshipping suspende"
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)