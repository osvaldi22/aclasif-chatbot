import os
import requests
import base64
from io import BytesIO
from PIL import Image
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
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

if not GROQ_API_KEY:
    print("⚠️ WARNING: GROQ_API_KEY no está configurada")

# ---------- TELEGRAM ----------
TELEGRAM_BOT_TOKEN = "8753872074:AAFub-e8qrfNhVvcLX46Kb_jpLUBzlSAJLA"
TELEGRAM_ADMIN_BOT_TOKEN = "8753184281:AAEaPQSD93oiRRkankYiVGY863pyvduuveA"
TELEGRAM_ADMIN_CHAT_ID = "1857096780"

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------------------------
# PROMPT DEL ASISTENTE (CHATBOT)
# ---------------------------
SYSTEM_PROMPT = """
Sos el asistente oficial de Aclasif 🇵🇾.
REGLAS DE ORO ABSOLUTAS:
- Aclasif es el ÚNICO INTERMEDIARIO en las ventas. Garantizamos compras 100% seguras.
- NUNCA le digas al cliente que contacte o hable directamente con el vendedor original.
- NUNCA digas que no manejamos pagos. Nosotros gestionamos el cobro por seguridad.
- NO des nombres propios de asesores ni dueños (prohibido decir nombres).
- ⚠️ REGLA DE FORMATO: NO USES formato Markdown. NO uses asteriscos (**) ni negritas. Escribe TODO en texto plano limpio.

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
        if valor is None: continue
        texto = str(valor).strip()
        if texto: return texto
    return default

def normalizar_texto(valor):
    return str(valor or "").strip()

def formatear_precio(valor):
    if valor is None: return "No especificado"
    texto = str(valor).strip()
    if not texto or texto.lower() in ["none", "null", "nan"]: return "No especificado"
    if "Gs" in texto or "₲" in texto or "USD" in texto or "$" in texto: return texto
    texto_num = texto.replace(".", "", texto.count(".") - 1) if texto.count(".") > 1 else texto
    texto_num = texto_num.replace(",", ".")
    try:
        numero = float(texto_num)
        if numero.is_integer(): return f"Gs. {int(numero):,}".replace(",", ".")
        return f"Gs. {numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return texto

def crear_contexto_compra_texto(compra):
    if not compra: return ""
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

def consultar_groq(mensaje, session_id, extra_context=""):
    if not GROQ_API_KEY:
        return "Error de configuración: Falta GROQ_API_KEY"

    if session_id not in conversaciones:
        conversaciones[session_id] = {"mensajes": [], "ultimo_mensaje": datetime.now(timezone.utc).isoformat(), "user_id": None, "compra": None}
    sesion = conversaciones[session_id]
    sesion["ultimo_mensaje"] = datetime.now(timezone.utc).isoformat()

    messages = [{"role": "system", "content": SYSTEM_PROMPT + extra_context}]
    for msg in sesion["mensajes"][-10:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": mensaje})

    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 500
    }
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROQ_API_KEY}"
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        respuesta = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print("Error Groq Chat:", str(e))
        respuesta = "Lo siento, tuve un pequeño problema de conexión. ¿Me repetís kape?"

    sesion["mensajes"].append({"role": "user", "content": mensaje})
    sesion["mensajes"].append({"role": "assistant", "content": respuesta})
    return respuesta

def notificar_telegram(mensaje):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_ADMIN_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={"chat_id": TELEGRAM_ADMIN_CHAT_ID, "text": mensaje, "parse_mode": "HTML"}, timeout=15)
        return resp.status_code == 200
    except:
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
        conversaciones[session_id] = {"mensajes": [], "ultimo_mensaje": datetime.now(timezone.utc).isoformat(), "user_id": user_id, "compra": None}
    sesion = conversaciones[session_id]
    sesion["ultimo_mensaje"] = datetime.now(timezone.utc).isoformat()
    if user_id: sesion["user_id"] = user_id

    try:
        respuesta = consultar_groq(mensaje, session_id, crear_contexto_compra_texto(sesion.get("compra")))
    except:
        respuesta = "Lo siento, tuve un problema de conexión. ¿Me repetís kape?"

    palabras_reclamo = ["reclamo", "estafa", "no recibí", "abogado", "devuelvan", "reembolso"]
    if any(p in mensaje.lower() for p in palabras_reclamo):
        notificar_telegram(f"🚨 <b>RECLAMO URGENTE</b>\nSesión: {session_id}\nMensaje: {mensaje[:200]}")

    return jsonify({"respuesta": respuesta})

@app.route("/api/historial/<session_id>", methods=["GET"])
def obtener_historial(session_id):
    sesion = conversaciones.get(session_id)
    if not sesion: return jsonify({"messages": []})
    return jsonify({"messages": sesion["mensajes"]})

# ---------------------------
# MODERACIÓN DE TEXTO E IMÁGENES CON GROQ VISION - LLAMA 4 SCOUT 👁
# ---------------------------
def obtener_imagen_base64(image_url):
    try:
        resp = requests.get(image_url, timeout=15)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content))
        if img.mode != 'RGB': img = img.convert('RGB')
        img.thumbnail((1024, 1024))
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode('utf-8')
    except Exception as e:
        print("Error al descargar imagen:", str(e))
        return None

def analizar_imagen_con_groq(image_url):
    if not GROQ_API_KEY:
        return "PENDIENTE", "GROQ_API_KEY no configurada"

    if not image_url: return "APROBAR", ""

    imagen_b64 = obtener_imagen_base64(image_url)
    if not imagen_b64: return "PENDIENTE", "No se pudo descargar o procesar el archivo de imagen."

    prompt_imagen = """Eres el moderador de seguridad de Aclasif. Actúas con OJO DE ÁGUILA.
    Analiza esta imagen. Tu único objetivo es detectar DATOS DE CONTACTO PERSONALES para evitar evasión de comisiones.

    ✅ EXCEPCIONES IMPORTANTES (DEBES APROBAR ESTO):
    - Marcas de fábrica grabadas en el producto.
    - Códigos de artículo, códigos ART, números de serie de fábrica o números de repuestos.
    - Medidas, talles o especificaciones técnicas del artículo.

    🚫 REGLAS ESTRICTAS - RESPONDE 'SUSPENDER' SI VES:
    1. Números de teléfono o WhatsApp escritos (ej. 0981, 0994, +595, o dígitos camuflados separados por puntos o espacios).
    2. Arrobas (@) con nombres de usuario de redes sociales (Instagram, TikTok, Facebook).
    3. Correos electrónicos (emails).
    4. Textos agregados a la foto que inviten a salir de la app: "contactame", "escribime", "mi whatsapp", "link en bio".
    5. Logos de WhatsApp o Instagram colocados para indicar contacto externo.

    Responde EXACTAMENTE en este formato plano, sin asteriscos ni negritas:
    APROBAR
    o
    SUSPENDER: [motivo corto de lo que viste]
    """

    payload = {
        "model": "meta-llama/llama-4-scout-17b-16e-instruct",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_imagen},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{imagen_b64}"}}
                ]
            }
        ],
        "temperature": 0.0,
        "max_tokens": 100
    }

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROQ_API_KEY}"
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        
        if resp.status_code != 200:
            print(f"Groq Vision Error {resp.status_code}: {resp.text}")
            return "PENDIENTE", f"Error API Groq Visión (Status {resp.status_code})"
            
        data = resp.json()
        if "choices" not in data or not data["choices"]:
            return "PENDIENTE", "Groq no devolvió respuesta - posible imagen bloqueada"
            
        respuesta = data["choices"][0]["message"]["content"].strip()

        if "SUSPENDER" in respuesta.upper():
            motivo = respuesta.split(":", 1)[1].strip() if ":" in respuesta else "Contacto visual detectado en la imagen."
            return "SUSPENDER", motivo
        return "APROBAR", ""
    except Exception as e:
        print("Exception Groq Vision:", str(e))
        return "PENDIENTE", f"Error interno en radar de imagen: {str(e)}"

def analizar_listing_con_groq(title, description, image_url=None):
    decision_imagen, motivo_imagen = "APROBAR", ""
    if image_url:
        decision_imagen, motivo_imagen = analizar_imagen_con_groq(image_url)

    if decision_imagen == "PENDIENTE": return "pending", f"Fallo de radar visual: {motivo_imagen}"
    elif decision_imagen == "SUSPENDER": return "suspended", f"Imagen: {motivo_imagen}"

    prompt_texto = f"""Eres un moderador de clasificados. Tu tarea es buscar DATOS DE CONTACTO en el título o descripción.

    TÍTULO: {title}
    DESCRIPCIÓN: {description}

    REGLA PRINCIPAL: 
    Si NO hay números de teléfono, correos o arrobas (@), debes aprobar obligatoriamente. Textos cortos, letras al azar (ej. "aaaaa"), o descripciones normales de productos son SEGUROS.

    ✅ DEBES APROBAR (Escribe 'APROBAR'):
    - Textos sin contacto.
    - Palabras al azar o descripciones de prueba cortas.
    - Códigos de artículo (ART), números de serie o repuestos.

    🚫 DEBES SUSPENDER (Escribe 'SUSPENDER'):
    - Teléfonos o números de WhatsApp (ej. 0981..., +595...).
    - Enlaces web externos, emails o usuarios de redes (@).

    Responde ÚNICAMENTE con una de estas dos palabras:
    APROBAR o SUSPENDER
    """
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": prompt_texto}],
        "temperature": 0.0
    }
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROQ_API_KEY}"
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        resp.raise_for_status()
        respuesta = resp.json()["choices"][0]["message"]["content"].strip()

        if "SUSPENDER" in respuesta.upper():
            return "suspended", "Contacto detectado en el texto descriptivo."
        return "verified", "Aprobado automáticamente."
    except Exception as e:
        print("Error IA Texto:", str(e))
        return "pending", f"Error IA Texto: {str(e)}"

@app.route("/api/moderar-listing", methods=["POST"])
def moderar_listing():
    data = request.json or {}
    listing_id = data.get("listing_id")
    if not listing_id: return jsonify({"success": False, "error": "Falta listing_id"}), 400

    try:
        if not supabase: return jsonify({"success": False, "error": "Supabase no configurado"}), 500
        listing_resp = supabase.table("listings").select("*").eq("id", listing_id).single().execute()
        if not listing_resp.data: return jsonify({"success": False, "error": "No encontrado"}), 404
        listing = listing_resp.data

        decision, nota = analizar_listing_con_groq(listing.get("title", ""), listing.get("description", ""), listing.get("image_url", ""))

        update_data = {
            "moderation_status": decision,
            "moderation_note": nota,
            "is_active": decision == "verified",
            "last_reviewed_at": datetime.now(timezone.utc).isoformat()
        }
        if decision == "verified": update_data["verified_at"] = datetime.now(timezone.utc).isoformat()

        supabase.table("listings").update(update_data).eq("id", listing_id).execute()

        if decision == "pending":
            notificar_telegram(f"⚠️ Publicación en revisión manual\nTítulo: {listing.get('title', '')}\nMotivo: {nota}\nID: {listing_id}")
        return jsonify({"success": True, "listing_id": listing_id, "decision": decision, "nota": nota})
    except Exception as e:
        print("Error moderar_listing:", str(e))
        return jsonify({"success": False, "error": str(e)}), 500

# ---------------------------
# WEBHOOK TELEGRAM PÚBLICO
# ---------------------------
@app.route("/webhook/telegram", methods=["POST"])
def webhook_telegram():
    data = request.json or {}
    if "message" not in data: return "OK", 200
    chat_id = data["message"]["chat"]["id"]
    texto = data["message"].get("text", "")
    try:
        respuesta = consultar_groq(texto, chat_id, "")
        if TELEGRAM_BOT_TOKEN:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": respuesta, "parse_mode": "HTML"}, timeout=10)
    except:
        pass
    return "OK", 200

# ---------------------------
# COMPRA / TELEGRAM / SUPABASE
# ---------------------------
def buscar_order(order):
    if not supabase: return None
    order = normalizar_texto(order)
    if not order or order.lower() in ["ninguna", "no especificado"]: return None
    columnas_orden = ["order_number", "id", "idx", "order", "order_id", "numero_orden", "nro_orden"]
    for columna in columnas_orden:
        try:
            res = supabase.table("orders").select("*").eq(columna, order).limit(1).execute()
            if res.data: return res.data[0]
        except: pass
        try:
            if str(order).isdigit():
                res = supabase.table("orders").select("*").eq(columna, int(order)).limit(1).execute()
                if res.data: return res.data[0]
        except: pass
    return None

def buscar_listing_por_id(listing_id):
    if not supabase: return None
    listing_id = normalizar_texto(listing_id)
    if not listing_id: return None
    try:
        res = supabase.table("listings").select("*").eq("id", listing_id).limit(1).execute()
        if res.data: return res.data[0]
    except: pass
    return None

def buscar_listing(producto="", article_code="", order=""):
    if not supabase: return None
    producto = normalizar_texto(producto)
    article_code = normalizar_texto(article_code)
    order = normalizar_texto(order)
    orden_data = buscar_order(order)

    if orden_data:
        posibles_listing_id = [orden_data.get("listing_id"), orden_data.get("listingId"), orden_data.get("product_id")]
        for lid in posibles_listing_id:
            listing = buscar_listing_por_id(lid)
            if listing: return listing
        posibles_art_order = [orden_data.get("article_code"), orden_data.get("codigo_articulo"), orden_data.get("art")]
        for cod in posibles_art_order:
            if cod:
                try:
                    res = supabase.table("listings").select("*").ilike("article_code", f"%{cod}%").limit(1).execute()
                    if res.data: return res.data[0]
                except: pass

    posibles_codigos = [article_code] if article_code else []
    if producto.upper().startswith("ART-"): posibles_codigos.append(producto)
    for codigo in posibles_codigos:
        try:
            res = supabase.table("listings").select("*").ilike("article_code", f"%{codigo}%").limit(1).execute()
            if res.data: return res.data[0]
        except: pass

    if producto:
        try:
            res = supabase.table("listings").select("*").ilike("title", f"%{producto}%").limit(1).execute()
            if res.data: return res.data[0]
        except: pass
    return None

def buscar_perfil_vendedor(seller_id):
    if not supabase or not seller_id: return None
    tablas = ["perfiles", "profiles", "usuarios", "users"]
    for tabla in tablas:
        try:
            res = supabase.table(tabla).select("*").eq("id", seller_id).limit(1).execute()
            if res.data: return res.data[0]
        except: pass
    return None

def sacar_datos_vendedor(listing_data, order_data=None):
    listing_data = listing_data or {}
    order_data = order_data or {}
    seller_id = listing_data.get("user_id") or order_data.get("seller_id")
    vendedor_nombre = valor_limpio(listing_data.get("seller_name"), default="Sin nombre")
    vendedor_whatsapp = valor_limpio(listing_data.get("seller_whatsapp"), default="Sin teléfono")
    perfil = buscar_perfil_vendedor(seller_id)
    if perfil:
        vendedor_nombre = valor_limpio(perfil.get("nombre"), perfil.get("full_name"), default=vendedor_nombre)
        vendedor_whatsapp = valor_limpio(perfil.get("whatsapp"), perfil.get("telefono"), default=vendedor_whatsapp)
    return {"seller_id": seller_id or "No encontrado", "vendedor_nombre": vendedor_nombre, "vendedor_whatsapp": vendedor_whatsapp}

def sacar_precio_listing(listing_data, order_data=None, data=None):
    listing_data = listing_data or {}
    order_data = order_data or {}
    data = data or {}
    precio = valor_limpio(order_data.get("total_usd"), data.get("precio"), listing_data.get("precio"), default="No especificado")
    return formatear_precio(precio)

def construir_link_articulo(listing_data, data, codigo_articulo=""):
    listing_data = listing_data or {}
    base = (FRONTEND_URL or "").strip().rstrip("/")
    codigo = valor_limpio(codigo_articulo, listing_data.get("article_code"), default="")
    if base and codigo and codigo != "No especificado": return f"{base}/producto/{codigo}"
    link_enviado = valor_limpio(data.get("link_articulo"), default="")
    if link_enviado and "/chat" not in link_enviado: return link_enviado
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
    listing_data = buscar_listing(producto=producto_recibido, article_code=article_code_recibido, order=order)

    if listing_data:
        titulo_producto = valor_limpio(listing_data.get("title"), producto_recibido, default="Título no encontrado")
        codigo_articulo = valor_limpio(listing_data.get("article_code"), article_code_recibido, default="No especificado")
    else:
        titulo_producto = valor_limpio(producto_recibido, default="Título no encontrado")
        codigo_articulo = valor_limpio(article_code_recibido, default="No especificado")

    vendedor = sacar_datos_vendedor(listing_data, order_data)
    precio = sacar_precio_listing(listing_data, order_data, data)
    link_articulo = construir_link_articulo(listing_data, data, codigo_articulo)

    compra_contexto = {
        "producto": producto_recibido, "titulo_producto": titulo_producto, "codigo_articulo": codigo_articulo,
        "precio": precio, "order": order, "nombre": nombre, "whatsapp": whatsapp, "email": email,
        "link_articulo": link_articulo, "seller_id": vendedor["seller_id"], "vendedor_nombre": vendedor["vendedor_nombre"],
        "vendedor_whatsapp": vendedor["vendedor_whatsapp"]
    }

    if session_id not in conversaciones:
        conversaciones[session_id] = {"mensajes": [], "ultimo_mensaje": datetime.now(timezone.utc).isoformat(), "user_id": None, "compra": compra_contexto}
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
    return jsonify({"success": True, "telegram_enviado": enviado, "compra": compra_contexto})

# ---------------------------
# NUEVA RUTA: CONSULTA DIRECTA DESDE LA WEB SIN SALIR A TELEGRAM 🚀
# ---------------------------
@app.route("/api/consulta-web", methods=["POST"])
def consulta_web():
    data = request.json or {}
    nombre = valor_limpio(data.get("nombre"), default="Anónimo")
    telefono = valor_limpio(data.get("telefono"), default="No especificado")
    mensaje = valor_limpio(data.get("mensaje"), default="")
    articulo_codigo = valor_limpio(data.get("codigo_art"), default="General / Footer")

    if not mensaje:
        return jsonify({"success": False, "error": "El mensaje de la consulta está vacío"}), 400

    texto_telegram = f"""💬 <b>NUEVA CONSULTA EN LA WEB</b> 💬

📦 <b>Artículo de Interés:</b> {articulo_codigo}
👤 <b>Nombre del Cliente:</b> {nombre}
📱 <b>WhatsApp de Contacto:</b> {telefono}

✉️ <b>Mensaje enviado:</b>
<i>{mensaje}</i>
"""
    enviado = notificar_telegram(texto_telegram)
    return jsonify({"success": enviado})

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "frontend_url": FRONTEND_URL, "groq_configured": bool(GROQ_API_KEY)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)