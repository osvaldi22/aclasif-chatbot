import os
import uuid
import requests
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from supabase import create_client, Client

# OCR
import pytesseract
from PIL import Image
from io import BytesIO

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

# En local queda http://localhost:3000/producto/ART-XXXXXX
# Cuando subas online podés poner FRONTEND_URL=https://www.aclasif.com en .env
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Ruta de Tesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

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

PROCESO DE COMPRA OFICIAL (Cuando el cliente pregunte por un artículo o quiera comprar):
1. Confirma la recepción del artículo o código de manera breve y profesional.
2. Explicale que en Aclasif actuamos como intermediarios para garantizar una compra totalmente segura.
3. Decile que el siguiente paso es gestionar el pago con nosotros.
4. Dale ÚNICAMENTE este link para que se ponga en contacto con Ventas, gestione el pago, envíe el comprobante (ticket) y finalice la compra: https://wa.me/595981784334

PRECIO Y DATOS DE COMPRA:
- Si en el contexto interno aparece un precio de compra, artículo, código ART, orden o link, usá esos datos exactos.
- Si el cliente pregunta "cuál era el precio", "cuánto cuesta", "precio del artículo", "me olvidé el precio", y el contexto trae precio, respondé con el precio exacto.
- No digas que no manejás precios si el contexto interno ya trae el precio.
- No inventes precio si el contexto dice "No especificado".

⚠️ MANEJO DE RECLAMOS:
Cuando un cliente quiera hacer un reclamo, seguí este proceso paso a paso, de forma conversacional y completa:
1. Preguntar el nombre completo del cliente.
2. Preguntar el correo electrónico o un teléfono de contacto.
3. Preguntar el número de pedido (si lo tiene) o el nombre del producto/vendedor involucrado.
4. Solicitar una descripción detallada del problema.
5. Una vez que tengas TODOS los datos anteriores, confirmalos con el cliente y luego finalizá ÚNICAMENTE con esta frase exacta en un mensaje separado: "✅ Reclamo registrado. Un agente se contactará en Horario laboral con Usted."

ESTILO:
Sé humano, amable, estilo paraguayo, directo y breve.
"""

# Memoria de conversaciones
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
    """
    Tu tabla orders guarda el total en total_usd, pero por ahora tu sistema maneja ese total como monto final.
    Ejemplo: 238050.00 -> Gs. 238.050
    """
    if valor is None:
        return "No especificado"

    texto = str(valor).strip()

    if not texto:
        return "No especificado"

    if texto.lower() in ["none", "null", "nan"]:
        return "No especificado"

    if "Gs" in texto or "₲" in texto or "USD" in texto or "$" in texto:
        return texto

    texto_num = texto.replace(".", "", texto.count(".") - 1) if texto.count(".") > 1 else texto
    texto_num = texto_num.replace(",", ".")

    try:
        numero = float(texto_num)

        if numero.is_integer():
            numero_int = int(numero)
            return f"Gs. {numero_int:,}".replace(",", ".")

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

Usá estos datos cuando el cliente pregunte por su compra actual.
Si el cliente pregunta por el precio y arriba hay un precio diferente de "No especificado", respondé ese precio exacto.
No digas que no manejás precio si el contexto trae precio.
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
        if not TELEGRAM_ADMIN_BOT_TOKEN:
            print("❌ Falta TELEGRAM_ADMIN_BOT_TOKEN")
            return False

        if not TELEGRAM_ADMIN_CHAT_ID:
            print("❌ Falta TELEGRAM_ADMIN_CHAT_ID")
            return False

        url = f"https://api.telegram.org/bot{TELEGRAM_ADMIN_BOT_TOKEN}/sendMessage"

        resp = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_ADMIN_CHAT_ID,
                "text": mensaje,
                "parse_mode": "HTML",
                "disable_web_page_preview": False
            },
            timeout=10
        )

        print("📨 Respuesta Telegram admin:", resp.status_code, resp.text)

        if resp.status_code == 200:
            print("✅ Aviso enviado a Telegram admin")
            return True

        print("❌ Telegram no aceptó el mensaje")
        return False

    except Exception as e:
        print(f"❌ Error Telegram admin: {e}")
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

    compra = sesion.get("compra")
    extra_context = crear_contexto_compra_texto(compra)

    try:
        respuesta = consultar_deepseek(mensaje, session_id, extra_context)

    except Exception as e:
        print("Error DeepSeek:", e)
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

    if not sesion.get("user_id"):
        try:
            ultimo = datetime.fromisoformat(sesion["ultimo_mensaje"])

            if (datetime.now(timezone.utc) - ultimo).seconds > 1800:
                del conversaciones[session_id]
                return jsonify({"messages": []})

        except:
            pass

    return jsonify({"messages": sesion["mensajes"]})


# ---------------------------
# OCR + MODERACIÓN
# ---------------------------

def extraer_texto_de_imagen(image_url: str) -> str:
    try:
        resp = requests.get(image_url, timeout=15)
        resp.raise_for_status()

        img = Image.open(BytesIO(resp.content))

        try:
            texto = pytesseract.image_to_string(img, lang="spa")
        except:
            texto = pytesseract.image_to_string(img, lang="eng")

        return texto.strip()

    except Exception as e:
        print(f"Error OCR: {e}")
        return ""


def analizar_imagen_con_deepseek(image_url):
    if not image_url:
        return "APROBAR", ""

    texto_extraido = extraer_texto_de_imagen(image_url)

    if not texto_extraido:
        return "APROBAR", ""

    prompt = f"""Analiza el siguiente texto extraído de la imagen de un producto. 
Detecta DATOS DE CONTACTO PERSONALES: teléfono, WhatsApp, email, @usuario, direcciones, etc.

TEXTO: {texto_extraido}

Responde EXACTAMENTE:
- "APROBAR" si NO hay datos de contacto
- "SUSPENDER: motivo breve" si SÍ los hay
- "PENDIENTE: motivo breve" si tienes dudas
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
                "content": "Eres un moderador automático. Solo respondes APROBAR, SUSPENDER o PENDIENTE."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.1,
        "max_tokens": 100
    }

    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=20
        )

        resp.raise_for_status()
        respuesta = resp.json()["choices"][0]["message"]["content"].strip()

        print(f"Vision OCR dice: {respuesta}")

        if respuesta.upper().startswith("SUSPENDER"):
            motivo = respuesta.split(":", 1)[1].strip() if ":" in respuesta else "Datos de contacto en imagen."
            return "SUSPENDER", motivo

        elif respuesta.upper().startswith("PENDIENTE"):
            motivo = respuesta.split(":", 1)[1].strip() if ":" in respuesta else "IA con dudas sobre imagen."
            return "PENDIENTE", motivo

        else:
            return "APROBAR", ""

    except Exception as e:
        print(f"Error OCR+DeepSeek: {e}")
        return "APROBAR", ""


def analizar_listing_con_deepseek(title, description, image_url=None):
    decision_imagen, motivo_imagen = "APROBAR", ""

    if image_url:
        decision_imagen, motivo_imagen = analizar_imagen_con_deepseek(image_url)

    prompt = f"""Eres un moderador automático de Aclasif/AmazonPY.
Detecta DATOS DE CONTACTO PERSONALES en título/descripción.

TÍTULO: {title}
DESCRIPCIÓN: {description}

Responde EXACTAMENTE:
- "APROBAR"
- "SUSPENDER: motivo breve"
- "PENDIENTE: motivo breve"
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
                "content": "Solo respondes APROBAR, SUSPENDER o PENDIENTE."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.1,
        "max_tokens": 100
    }

    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=20
        )

        resp.raise_for_status()
        respuesta = resp.json()["choices"][0]["message"]["content"].strip()

        print(f"DeepSeek texto dice: {respuesta}")

        if decision_imagen == "SUSPENDER":
            return "suspended", f"Imagen: {motivo_imagen}"

        elif respuesta.upper().startswith("SUSPENDER"):
            motivo = respuesta.split(":", 1)[1].strip() if ":" in respuesta else "Contacto en texto."
            return "suspended", f"Texto: {motivo}"

        elif decision_imagen == "PENDIENTE" or respuesta.upper().startswith("PENDIENTE"):
            motivo = motivo_imagen or (
                respuesta.split(":", 1)[1].strip() if ":" in respuesta else "Duda."
            )
            return "pending", motivo

        elif respuesta.upper().startswith("APROBAR"):
            return "verified", "Aprobado automáticamente texto + imagen."

        else:
            motivo = respuesta.split(":", 1)[1].strip() if ":" in respuesta else "No decidió."
            return "pending", motivo

    except Exception as e:
        print(f"Error moderación: {e}")
        return "pending", f"Error IA: {str(e)}"


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

        if decision == "pending":
            notificar_telegram(
                f"⚠️ Publicación dudosa pendiente\nTítulo: {listing.get('title', '')}\nID: {listing_id}"
            )

        return jsonify({
            "success": True,
            "listing_id": listing_id,
            "decision": decision,
            "nota": nota
        })

    except Exception as e:
        print(f"Error moderación endpoint: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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

    except Exception as e:
        print("Error webhook Telegram:", e)
        respuesta = "Lo siento, tuve un problema de conexión. ¿Me repetís kape?"

    try:
        if TELEGRAM_BOT_TOKEN:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

            requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": respuesta,
                    "parse_mode": "HTML"
                },
                timeout=10
            )

    except Exception as e:
        print("Error enviando respuesta Telegram público:", e)

    if "✅ Reclamo registrado. Un agente se contactará en Horario laboral con Usted." in respuesta:
        sesion = conversaciones.get(chat_id, {})
        historial = sesion.get("mensajes", [])
        contexto = ""

        if historial:
            relevantes = historial[-6:]
            contexto = "\n".join([f"{m['role']}: {m['content']}" for m in relevantes])

        prompt_resumen = f"""Extrae del siguiente historial de conversación los datos del cliente que ha realizado un reclamo.
Si no encuentras algún dato, indica "No proporcionado".

Datos a extraer:
- Nombre del cliente
- Correo electrónico
- Teléfono o WhatsApp
- Número de pedido
- Motivo del reclamo

Historial:
{contexto}
Último mensaje del cliente: {texto}

Responde en este formato exacto:
NOMBRE: ...
CORREO: ...
TELEFONO: ...
PEDIDO: ...
MOTIVO: ...
"""

        try:
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {
                        "role": "system",
                        "content": "Eres un asistente que extrae datos de reclamos. Responde solo con el formato solicitado."
                    },
                    {
                        "role": "user",
                        "content": prompt_resumen
                    }
                ],
                "temperature": 0.1,
                "max_tokens": 150
            }

            resp_ia = requests.post(
                "https://api.deepseek.com/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=20
            )

            resumen = resp_ia.json()["choices"][0]["message"]["content"]

        except:
            resumen = "No se pudo extraer el resumen automáticamente."

        notificar_telegram(
            f"🚨 <b>RECLAMO REGISTRADO DESDE TELEGRAM</b>\nChat ID: {chat_id}\n\n{resumen}"
        )

    return "OK", 200


# ---------------------------
# COMPRA / TELEGRAM / SUPABASE
# ---------------------------

def buscar_order(order):
    """
    Busca la orden en Supabase.
    Tu tabla orders usa order_number.
    """
    order = normalizar_texto(order)

    if not order or order.lower() in ["ninguna", "no especificado"]:
        return None

    columnas_orden = [
        "order_number",
        "id",
        "idx",
        "order",
        "order_id",
        "numero_orden",
        "nro_orden"
    ]

    for columna in columnas_orden:
        try:
            res = supabase.table("orders").select("*").eq(columna, order).limit(1).execute()

            if res.data:
                fila = res.data[0]
                fila["_columna_orden"] = columna
                print(f"✅ Orden encontrada en orders por {columna}={order}")
                return fila

        except Exception as e:
            print(f"No pude buscar orden por {columna} texto: {e}")

        try:
            if str(order).isdigit():
                res = supabase.table("orders").select("*").eq(columna, int(order)).limit(1).execute()

                if res.data:
                    fila = res.data[0]
                    fila["_columna_orden"] = columna
                    print(f"✅ Orden encontrada en orders por {columna}={int(order)}")
                    return fila

        except Exception as e:
            print(f"No pude buscar orden por {columna} número: {e}")

    return None


def buscar_listing_por_id(listing_id):
    listing_id = normalizar_texto(listing_id)

    if not listing_id:
        return None

    try:
        res = supabase.table("listings").select("*").eq("id", listing_id).limit(1).execute()

        if res.data:
            print(f"✅ Listing encontrado por id={listing_id}")
            return res.data[0]

    except Exception as e:
        print(f"No pude buscar listing por id {listing_id}: {e}")

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
            orden_data.get("product_id"),
            orden_data.get("producto_id"),
            orden_data.get("article_id"),
            orden_data.get("articulo_id"),
        ]

        for lid in posibles_listing_id:
            listing = buscar_listing_por_id(lid)

            if listing:
                return listing

        posibles_art_order = [
            orden_data.get("article_code"),
            orden_data.get("codigo_articulo"),
            orden_data.get("art"),
            orden_data.get("codigo"),
        ]

        for cod in posibles_art_order:
            cod = normalizar_texto(cod)

            if cod:
                try:
                    res = supabase.table("listings").select("*").ilike("article_code", f"%{cod}%").limit(1).execute()

                    if res.data:
                        print(f"✅ Listing encontrado por ART guardado en orders: {cod}")
                        return res.data[0]

                except Exception as e:
                    print(f"No pude buscar listing por ART de orders {cod}: {e}")

    posibles_codigos = []

    if article_code:
        posibles_codigos.append(article_code)

    if producto.upper().startswith("ART-"):
        posibles_codigos.append(producto)

    for codigo in posibles_codigos:
        try:
            res = supabase.table("listings").select("*").ilike("article_code", f"%{codigo}%").limit(1).execute()

            if res.data:
                print(f"✅ Listing encontrado por article_code={codigo}")
                return res.data[0]

        except Exception as e:
            print(f"Error buscando por article_code {codigo}: {e}")

    if producto:
        try:
            res = supabase.table("listings").select("*").ilike("title", f"%{producto}%").limit(1).execute()

            if res.data:
                print(f"✅ Listing encontrado por título parecido a: {producto}")
                return res.data[0]

        except Exception as e:
            print(f"Error buscando por title {producto}: {e}")

    return None


def buscar_perfil_vendedor(seller_id):
    if not seller_id:
        return None

    tablas = ["perfiles", "profiles", "usuarios", "users"]

    mejor_perfil = None

    for tabla in tablas:
        try:
            res = supabase.table(tabla).select("*").eq("id", seller_id).limit(1).execute()

            if res.data:
                perfil = res.data[0]
                perfil["_tabla_origen"] = tabla

                nombre = valor_limpio(
                    perfil.get("nombre"),
                    perfil.get("full_name"),
                    perfil.get("name"),
                    perfil.get("display_name"),
                    default=""
                )

                tel = valor_limpio(
                    perfil.get("whatsapp"),
                    perfil.get("telefono"),
                    perfil.get("phone"),
                    perfil.get("celular"),
                    perfil.get("mobile"),
                    perfil.get("phone_number"),
                    perfil.get("numero_whatsapp"),
                    perfil.get("numero"),
                    perfil.get("contact_phone"),
                    perfil.get("contacto"),
                    default=""
                )

                if tel:
                    print(f"✅ Vendedor encontrado en {tabla} con WhatsApp: {tel}")
                    return perfil

                if mejor_perfil is None and nombre:
                    mejor_perfil = perfil

        except Exception as e:
            print(f"No pude buscar vendedor en {tabla}: {e}")

    return mejor_perfil


def sacar_datos_vendedor(listing_data, order_data=None):
    listing_data = listing_data or {}
    order_data = order_data or {}

    seller_id = (
        listing_data.get("user_id")
        or listing_data.get("seller_id")
        or listing_data.get("owner_id")
        or listing_data.get("profile_id")
        or listing_data.get("created_by")
        or listing_data.get("userId")
        or listing_data.get("sellerId")
        or order_data.get("seller_id")
        or order_data.get("sellerId")
        or order_data.get("user_id")
        or order_data.get("owner_id")
    )

    vendedor_nombre = valor_limpio(
        listing_data.get("seller_name"),
        listing_data.get("vendedor_nombre"),
        listing_data.get("nombre_vendedor"),
        order_data.get("seller_name"),
        order_data.get("vendedor_nombre"),
        default="Sin nombre"
    )

    vendedor_whatsapp = valor_limpio(
        listing_data.get("seller_whatsapp"),
        listing_data.get("vendedor_whatsapp"),
        listing_data.get("whatsapp_vendedor"),
        listing_data.get("seller_phone"),
        listing_data.get("vendedor_phone"),
        listing_data.get("phone"),
        listing_data.get("telefono"),
        listing_data.get("whatsapp"),
        listing_data.get("mobile"),
        listing_data.get("celular"),
        order_data.get("seller_whatsapp"),
        order_data.get("vendedor_whatsapp"),
        order_data.get("whatsapp_vendedor"),
        default="Sin teléfono"
    )

    perfil = buscar_perfil_vendedor(seller_id)

    if perfil:
        vendedor_nombre = valor_limpio(
            perfil.get("nombre"),
            perfil.get("full_name"),
            perfil.get("name"),
            perfil.get("display_name"),
            perfil.get("username"),
            perfil.get("email"),
            vendedor_nombre,
            default="Sin nombre"
        )

        vendedor_whatsapp = valor_limpio(
            perfil.get("whatsapp"),
            perfil.get("telefono"),
            perfil.get("phone"),
            perfil.get("celular"),
            perfil.get("mobile"),
            perfil.get("phone_number"),
            perfil.get("numero_whatsapp"),
            perfil.get("numero"),
            perfil.get("contact_phone"),
            perfil.get("contacto"),
            vendedor_whatsapp,
            default="Sin teléfono"
        )

    return {
        "seller_id": seller_id or "No encontrado",
        "vendedor_nombre": vendedor_nombre,
        "vendedor_whatsapp": vendedor_whatsapp
    }


def sacar_precio_listing(listing_data, order_data=None, data=None):
    """
    IMPORTANTE:
    Según tu Supabase, el precio final de la compra está en:
    orders.total_usd

    Ejemplo:
    price_usd: 207000.00
    service_fee_usd: 31050.00
    total_usd: 238050.00

    Por eso usamos total_usd primero.
    """
    listing_data = listing_data or {}
    order_data = order_data or {}
    data = data or {}

    precio = valor_limpio(
        order_data.get("total_usd"),
        order_data.get("total"),
        order_data.get("total_amount"),
        order_data.get("final_price"),
        order_data.get("precio_total"),
        order_data.get("total_price"),
        order_data.get("subtotal"),

        data.get("precio"),
        data.get("price"),
        data.get("monto"),
        data.get("amount"),

        order_data.get("price_usd"),
        order_data.get("precio"),
        order_data.get("price"),
        order_data.get("monto"),
        order_data.get("amount"),

        listing_data.get("precio"),
        listing_data.get("price"),
        listing_data.get("monto"),
        listing_data.get("amount"),
        listing_data.get("precio_final"),
        listing_data.get("final_price"),
        listing_data.get("sale_price"),
        listing_data.get("regular_price"),
        listing_data.get("valor"),
        listing_data.get("precio_gs"),
        listing_data.get("price_gs"),
        listing_data.get("precio_venta"),
        listing_data.get("selling_price"),
        listing_data.get("listing_price"),
        listing_data.get("product_price"),
        listing_data.get("current_price"),
        listing_data.get("base_price"),
        listing_data.get("precio_publicado"),
        listing_data.get("published_price"),
        listing_data.get("price_amount"),

        default="No especificado"
    )

    print("💰 Precio detectado:", precio)
    print("💰 total_usd en order:", order_data.get("total_usd") if order_data else None)
    print("💰 price_usd en order:", order_data.get("price_usd") if order_data else None)
    print("🧾 Columnas disponibles en listing:", list(listing_data.keys()) if listing_data else [])
    print("🧾 Columnas disponibles en order:", list(order_data.keys()) if order_data else [])

    return formatear_precio(precio)


def construir_link_articulo(listing_data, data, codigo_articulo=""):
    listing_data = listing_data or {}
    codigo_articulo = normalizar_texto(codigo_articulo)

    base = (
        FRONTEND_URL
        or os.environ.get("SITE_URL")
        or os.environ.get("NEXT_PUBLIC_SITE_URL")
        or ""
    ).strip().rstrip("/")

    if not base:
        url_actual = valor_limpio(data.get("url_actual"), default="")

        if url_actual.startswith("http"):
            try:
                partes = url_actual.split("/")
                base = partes[0] + "//" + partes[2]
            except:
                base = ""

    codigo = valor_limpio(
        codigo_articulo,
        listing_data.get("article_code"),
        data.get("article_code"),
        data.get("codigo_articulo"),
        default=""
    )

    if base and codigo:
        return f"{base}/producto/{codigo}"

    link_enviado = valor_limpio(
        data.get("link_articulo"),
        data.get("article_url"),
        data.get("producto_url"),
        data.get("pagina_origen"),
        default=""
    )

    if link_enviado and "/chat" not in link_enviado:
        return link_enviado

    link_de_tabla = valor_limpio(
        listing_data.get("url"),
        listing_data.get("link"),
        listing_data.get("permalink"),
        listing_data.get("product_url"),
        listing_data.get("listing_url"),
        listing_data.get("article_url"),
        default=""
    )

    if link_de_tabla:
        return link_de_tabla

    listing_id = listing_data.get("id")
    slug = listing_data.get("slug")

    if base and slug:
        return f"{base}/producto/{slug}"

    if base and listing_id:
        return f"{base}/listing/{listing_id}"

    return "Link no encontrado"


@app.route("/api/notificar-compra", methods=["POST"])
def notificar_compra():
    data = request.json or {}

    session_id = data.get("session_id") or "anon"

    producto_recibido = valor_limpio(data.get("producto"), default="")
    article_code_recibido = valor_limpio(
        data.get("article_code"),
        data.get("codigo_articulo"),
        default=""
    )

    nombre = valor_limpio(data.get("nombre"))
    whatsapp = valor_limpio(data.get("whatsapp"))
    email = valor_limpio(data.get("email"))
    order = valor_limpio(data.get("order"), default="Ninguna")

    print("====================================")
    print("🛒 NUEVA NOTIFICACIÓN DE COMPRA")
    print(f"Session ID: {session_id}")
    print(f"Producto recibido: {producto_recibido}")
    print(f"ART recibido: {article_code_recibido}")
    print(f"Orden recibida: {order}")
    print(f"Nombre comprador: {nombre}")
    print(f"WhatsApp comprador: {whatsapp}")
    print(f"Email comprador: {email}")
    print("====================================")

    order_data = buscar_order(order)

    listing_data = buscar_listing(
        producto=producto_recibido,
        article_code=article_code_recibido,
        order=order
    )

    if listing_data:
        print("✅ Artículo encontrado en Supabase")

        titulo_producto = valor_limpio(
            listing_data.get("title"),
            producto_recibido,
            order_data.get("listing_title") if order_data else None,
            default="Título no encontrado"
        )

        codigo_articulo = valor_limpio(
            listing_data.get("article_code"),
            article_code_recibido,
            order_data.get("article_code") if order_data else None,
            producto_recibido,
            default="ART no encontrado"
        )

    else:
        print("⚠️ No se encontró el artículo en Supabase")

        listing_data = {}

        titulo_producto = valor_limpio(
            order_data.get("listing_title") if order_data else None,
            producto_recibido,
            default="Título no encontrado"
        )

        codigo_articulo = valor_limpio(
            order_data.get("article_code") if order_data else None,
            article_code_recibido,
            producto_recibido,
            default="ART no encontrado"
        )

    vendedor = sacar_datos_vendedor(listing_data, order_data)
    precio = sacar_precio_listing(listing_data, order_data, data)
    link_articulo = construir_link_articulo(listing_data, data, codigo_articulo)

    compra_contexto = {
        "producto": producto_recibido,
        "titulo_producto": titulo_producto,
        "codigo_articulo": codigo_articulo,
        "article_code": codigo_articulo,
        "precio": precio,
        "price": precio,
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
        conversaciones[session_id]["ultimo_mensaje"] = datetime.now(timezone.utc).isoformat()

    vendedor_texto = f"""👤 Nombre: {vendedor["vendedor_nombre"]}
📱 WhatsApp: {vendedor["vendedor_whatsapp"]}
🆔 ID Vendedor: {vendedor["seller_id"]}"""

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
{vendedor_texto}
"""

    enviado = notificar_telegram(mensaje)

    return jsonify({
        "success": True,
        "telegram_enviado": enviado,
        "compra": compra_contexto
    })


# ---------------------------
# ENDPOINT DE SALUD
# ---------------------------

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "ok",
        "deepseek": bool(DEEPSEEK_API_KEY),
        "supabase": bool(SUPABASE_URL),
        "telegram_publico": bool(TELEGRAM_BOT_TOKEN),
        "telegram_admin": bool(TELEGRAM_ADMIN_BOT_TOKEN and TELEGRAM_ADMIN_CHAT_ID),
        "admin_chat_id": TELEGRAM_ADMIN_CHAT_ID,
        "frontend_url": FRONTEND_URL
    })


if __name__ == "__main__":
    print("🤖 Iniciando Bot Aclasif...")
    print(f"🧠 DeepSeek: {'✅' if DEEPSEEK_API_KEY else '❌'}")
    print(f"🗄️  Supabase: {'✅' if SUPABASE_URL else '❌'}")
    print(f"📱 Telegram público: {'✅' if TELEGRAM_BOT_TOKEN else '❌'}")
    print(f"🔔 Telegram admin: {'✅' if TELEGRAM_ADMIN_BOT_TOKEN and TELEGRAM_ADMIN_CHAT_ID else '❌'}")
    print(f"🆔 Admin chat ID: {TELEGRAM_ADMIN_CHAT_ID}")
    print(f"🌐 FRONTEND_URL: {FRONTEND_URL}")
    app.run(host="0.0.0.0", port=5000, debug=True)