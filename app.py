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
# Se recomienda tener estos datos en Render / variables de entorno.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ADMIN_BOT_TOKEN = os.environ.get("TELEGRAM_ADMIN_BOT_TOKEN", "")
TELEGRAM_ADMIN_CHAT_ID = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")

FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://aclasif-web.vercel.app")

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
- NO des nombres propios de asesores ni dueños.
- NO uses formato Markdown. NO uses asteriscos ni negritas. Escribe TODO en texto plano limpio.

PROCESO DE COMPRA OFICIAL:
1. Confirma la recepción del artículo o código de manera breve y profesional.
2. Explicale que en Aclasif actuamos como intermediarios para garantizar una compra totalmente segura.
3. Decile que el siguiente paso es gestionar el pago con nosotros.
4. Dale ÚNICAMENTE este link para que se ponga en contacto con Ventas, gestione el pago, envíe el comprobante y finalice la compra: https://wa.me/595981784334

PRECIO Y DATOS DE COMPRA:
- Si en el contexto interno aparece un precio de compra, artículo, código ART, orden o link, usá esos datos exactos.
- Si el cliente pregunta "cuál era el precio" o "cuánto cuesta", y el contexto trae precio, respondé con el precio exacto.

MANEJO DE RECLAMOS:
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
        if not TELEGRAM_ADMIN_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
            print("⚠️ Telegram admin no configurado en variables de entorno.")
            return False

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
# MODERACIÓN INTELIGENTE V4
# ---------------------------

DOMINIOS_PERMITIDOS = [
    "aclasif.com",
    "www.aclasif.com",
    "aclasif-web.vercel.app"
]


def quitar_dominios_permitidos(texto):
    t = str(texto or "").lower()

    for dominio in DOMINIOS_PERMITIDOS:
        t = t.replace(dominio, "")

    return t


def detectar_numero_camuflado(texto):
    """
    Detecta teléfonos escondidos:
    +595 994 808030
    595994808030
    0994808030
    0.9.9.4.8.0.8.0.3.0
    0-9-9-4-8-0-8-0-3-0
    0f9l9f4 8f0d8sa0r3d0
    cero nueve nueve cuatro ocho cero ocho cero tres cero
    """
    original = str(texto or "").lower()

    if not original.strip():
        return False, ""

    # Detectar números escritos con palabras.
    palabras_numero = {
        "cero": "0",
        "uno": "1",
        "una": "1",
        "dos": "2",
        "tres": "3",
        "cuatro": "4",
        "cinco": "5",
        "seis": "6",
        "siete": "7",
        "ocho": "8",
        "nueve": "9"
    }

    palabras_detectadas = []

    for palabra, digito in palabras_numero.items():
        for match in re.finditer(rf"\b{palabra}\b", original):
            palabras_detectadas.append((match.start(), digito))

    palabras_detectadas.sort(key=lambda x: x[0])

    if len(palabras_detectadas) >= 7:
        numero_palabras = "".join([x[1] for x in palabras_detectadas])

        if numero_palabras.startswith("09") or numero_palabras.startswith("9") or numero_palabras.startswith("595"):
            return True, "Contiene número telefónico escrito en palabras"

        return True, "Contiene muchos números escritos en palabras, posible teléfono"

    # Sacar solo dígitos aunque haya letras, puntos, guiones o espacios en medio.
    solo_digitos = re.sub(r"\D", "", original)

    if not solo_digitos:
        return False, ""

    # Paraguay claro.
    if solo_digitos.startswith("595") and len(solo_digitos) >= 11:
        return True, "Contiene número telefónico paraguayo +595"

    if solo_digitos.startswith("09") and len(solo_digitos) >= 9:
        return True, "Contiene número telefónico paraguayo 09"

    # WhatsApp/celular sin cero inicial, ejemplo 994808030.
    if solo_digitos.startswith("9") and len(solo_digitos) >= 9:
        return True, "Contiene número de celular probable"

    # Número muy largo metido entre letras o símbolos.
    # Evita bloquear medidas como 18,5 o 220v porque son cortas.
    if len(solo_digitos) >= 10:
        return True, "Contiene número largo sospechoso tipo teléfono"

    # Patrón de muchos dígitos separados por letras o símbolos.
    # Ejemplo: 0f9l8f5 7f8d6sa4r4d33
    cantidad_digitos = len(re.findall(r"\d", original))
    cantidad_letras = len(re.findall(r"[a-zA-Z]", original))

    if cantidad_digitos >= 8 and cantidad_letras >= 2:
        if solo_digitos.startswith("0") or solo_digitos.startswith("9") or solo_digitos.startswith("595"):
            return True, "Contiene número camuflado entre letras"

    # Patrón separado por puntos, guiones, espacios, barras, etc.
    patron_separado = r"(?:\d[\s\-\.\_/|:;,+()]+){7,}\d"

    if re.search(patron_separado, original):
        return True, "Contiene número camuflado con separadores"

    return False, ""


def detectar_contacto_regex(texto):
    """
    Bloquea contactos reales.
    Permite marcas, modelos, medidas, promociones y texto normal.
    """
    if not texto:
        return False, ""

    original = str(texto)
    t = original.lower()
    t_sin_permitidos = quitar_dominios_permitidos(t)

    # Contacto por palabra.
    palabras_contacto = [
        "whatsapp",
        "wpp",
        "wasap",
        "whats",
        "wa.me",
        "teléfono",
        "telefono",
        "tel:",
        "tel ",
        "celular",
        "cel:",
        "nro celular",
        "nro whatsapp",
        "numero whatsapp",
        "número whatsapp",
        "mi numero",
        "mi número",
        "mi whatsapp",
        "llamame",
        "llámame",
        "llamar",
        "llame",
        "contactame",
        "contáctame",
        "contactanos",
        "contáctanos",
        "consultanos",
        "consúltanos",
        "escribime",
        "escríbeme",
        "mensajeame",
        "inbox",
        "dm",
        "directo",
        "direct"
    ]

    for palabra in palabras_contacto:
        if palabra in t:
            return True, f"Contiene invitación de contacto externo: {palabra}"

    # Email directo.
    if re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", original):
        return True, "Contiene email"

    # Usuario @roberto, @tienda, etc.
    if re.search(r"(?<!\w)@[a-zA-Z0-9._]{3,}", original):
        return True, "Contiene usuario externo con @"

    # Redes sociales / correos conocidos.
    redes = [
        "instagram",
        "insta",
        "facebook",
        "fb.com",
        "telegram",
        "t.me",
        "messenger",
        "snapchat",
        "tiktok",
        "gmail",
        "hotmail",
        "outlook",
        "yahoo",
        "@gmail",
        "@hotmail",
        "@outlook",
        "@yahoo"
    ]

    for red in redes:
        if red in t:
            return True, f"Contiene red social o correo externo: {red}"

    # Links externos.
    posibles_links = re.findall(
        r"(https?://[^\s]+|www\.[^\s]+|[a-zA-Z0-9.-]+\.(com|net|org|py|app|shop|store|online|info)[^\s]*)",
        t_sin_permitidos
    )

    if posibles_links:
        return True, "Contiene link externo no permitido"

    # Teléfonos y números escondidos.
    hay_numero, motivo_numero = detectar_numero_camuflado(original)

    if hay_numero:
        return True, motivo_numero

    # Dirección exacta con número.
    palabras_direccion = [
        "direccion",
        "dirección",
        "ubicacion",
        "ubicación",
        "calle",
        "avenida",
        "avda",
        "barrio",
        "local",
        "casa numero",
        "casa número"
    ]

    if any(p in t for p in palabras_direccion) and re.search(r"\d{2,}", t):
        return True, "Contiene posible dirección con número"

    return False, ""


def preparar_imagen_para_ocr(img):
    if img.mode != "RGB":
        img = img.convert("RGB")

    max_size = 2000

    if max(img.size) < max_size:
        scale = max_size / max(img.size)
        new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
        img = img.resize(new_size)

    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.8)

    enhancer = ImageEnhance.Sharpness(img)
    img = enhancer.enhance(1.8)

    img = img.filter(ImageFilter.SHARPEN)

    return img


def ocr_space_desde_pil(img, etiqueta="full"):
    try:
        img = preparar_imagen_para_ocr(img)

        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=94)

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

        resp = requests.post(api_url, data=payload, timeout=40)
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
    try:
        print("📸 Descargando imagen para OCR V4...")
        img_resp = requests.get(image_url, timeout=25)
        img_resp.raise_for_status()

        img_original = Image.open(BytesIO(img_resp.content))

        if img_original.mode != "RGB":
            img_original = img_original.convert("RGB")

        w, h = img_original.size

        zonas = []
        zonas.append(("completa", img_original))

        if h > 250 and w > 150:
            zonas.append(("superior", img_original.crop((0, 0, w, int(h * 0.40)))))
            zonas.append(("centro", img_original.crop((0, int(h * 0.22), w, int(h * 0.78)))))
            zonas.append(("inferior", img_original.crop((0, int(h * 0.50), w, h))))
            zonas.append(("inferior_baja", img_original.crop((0, int(h * 0.65), w, h))))

        textos = []

        for etiqueta, zona in zonas:
            texto = ocr_space_desde_pil(zona, etiqueta)

            if texto:
                textos.append(texto)

        texto_final = " ".join(textos).strip()
        texto_final = re.sub(r"\s+", " ", texto_final)

        print(f"👀 TEXTO OCR FINAL V4: {texto_final}")

        return texto_final

    except Exception as e:
        print(f"❌ Error crítico en OCR V4: {e}")
        return ""


def analizar_imagen_con_deepseek(image_url):
    """
    V4:
    - Si OCR detecta contacto: suspende.
    - Si OCR no lee nada: pending, no aprueba automático.
    - Si OCR lee texto normal sin contacto: IA revisa y puede aprobar.
    """
    if not image_url:
        return "APROBAR", "Sin imagen, se revisa solo texto"

    texto_extraido = extraer_texto_de_imagen(image_url)

    if not texto_extraido:
        return "PENDIENTE", "OCR no pudo leer texto de la imagen. Revisión manual necesaria."

    contacto, motivo_regex = detectar_contacto_regex(texto_extraido)

    if contacto:
        return "SUSPENDER", f"Imagen: {motivo_regex}"

    prompt = f"""Analiza este texto extraído de la imagen de un producto.

OBJETIVO:
Solo suspender si hay DATOS DE CONTACTO o intento de sacar la comunicación fuera de Aclasif.

TEXTO EXTRAÍDO:
{texto_extraido}

SUSPENDER SI HAY:
1. Teléfono, WhatsApp o número para contacto.
2. Número camuflado con puntos, guiones, espacios, letras o símbolos.
3. Email.
4. Usuario con @, por ejemplo @roberto.
5. Redes sociales: Instagram, Facebook, Telegram, TikTok.
6. Link externo que no sea de Aclasif.
7. Dirección exacta con número.
8. Frases de contacto directo: escribime, contactame, llamame, inbox, DM, mi número, mi WhatsApp.

PERMITIR:
- Marcas: Curren, Casio, Samsung, Nike, AmazonPY, etc.
- Modelos.
- Medidas: 18,5 pulgadas, 8 mm, 220v, talle 42.
- Promociones: comprá 2 y llevá 3.
- Precios.
- Texto normal descriptivo del producto.
- Logo o link interno de Aclasif.

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
                "content": "Eres un moderador estricto contra contactos. Suspendes contactos reales u ocultos. No suspendas marcas, modelos, medidas, precios ni texto normal de producto."
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

        print(f"🤖 Decisión IA Imagen V4: {respuesta}")

        if respuesta.upper().startswith("SUSPENDER"):
            motivo = respuesta.split(":", 1)[1].strip() if ":" in respuesta else "Contacto en imagen"
            return "SUSPENDER", motivo

        return "APROBAR", "Imagen sin contacto detectado"

    except Exception as e:
        print("❌ Error IA imagen V4:", e)
        return "PENDIENTE", f"Error IA imagen. Revisión manual necesaria: {str(e)}"


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

    prompt = f"""Eres un moderador automático de Aclasif.

Revisá el título y descripción.

TÍTULO:
{title}

DESCRIPCIÓN:
{description}

SUSPENDER SI HAY:
1. Teléfono o WhatsApp.
2. Número camuflado.
3. Email.
4. Usuario con @.
5. Redes sociales o usuario externo.
6. Link externo.
7. Dirección exacta con número para vender fuera de Aclasif.
8. Frases para contactar fuera de la plataforma.

PERMITIR:
- Marcas.
- Modelos.
- Medidas.
- Promociones.
- Precios.
- Texto normal de producto.

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
                "content": "Solo suspendes datos de contacto reales u ocultos. No suspendas texto normal de producto, marcas, modelos, medidas ni precios."
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

        print(f"🤖 Decisión IA Texto V4: {respuesta}")

        if respuesta.upper().startswith("SUSPENDER"):
            motivo = respuesta.split(":", 1)[1].strip() if ":" in respuesta else "Contacto en texto"
            return "suspended", f"Texto: {motivo}"

        return "verified", "Aprobado automáticamente. No se detectaron datos de contacto."

    except Exception as e:
        print("❌ Error IA texto V4:", e)
        return "pending", f"Error IA texto. Revisión manual necesaria: {str(e)}"


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
        print("❌ Error moderar_listing V4:", e)

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
        "moderacion": "inteligente-v4-contactos-fuerte",
        "regla": "bloquea telefonos, emails, redes, arrobas, links externos y numeros camuflados; permite marcas, modelos, medidas y texto normal"
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)