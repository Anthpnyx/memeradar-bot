"""
MemeBot - Versión Estable Oficial para Railway (Python 3.10)
Incluye: Filtro de Social Hype, Simulador de Papel (S/. 100) y Comando /portafolio
=================================================================================
"""

import os
import time
import json
import logging
import requests
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext

# ──────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.getenv("MEMEBOT_TELEGRAM_TOKEN", "PON_AQUI_TU_TOKEN")
CHAT_ID = os.getenv("MEMEBOT_CHAT_ID", "PON_AQUI_TU_CHAT_ID")

POLL_INTERVAL_SECONDS = 60
SEEN_TOKENS_FILE = "seen_tokens.json"
SIMULATIONS_FILE = "simulaciones.json"

FILTROS = {
    "liquidez_minima_usd": 3_000,       
    "volumen_24h_minimo_usd": 5_000,    
    "edad_maxima_horas": 48,            
    "market_cap_maximo_usd": 10_000_000,
    "cambio_precio_5m_minimo_pct": 2,   
    "holders_top10_maximo_pct": 35,     
}

SOLANA_RPC_URL = "https://solana.com"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("memebot")


# ──────────────────────────────────────────────────────────────────────────
# GESTIÓN DE ARCHIVOS LOCALES
# ──────────────────────────────────────────────────────────────────────────

def cargar_tokens_vistos() -> set:
    if os.path.exists(SEEN_TOKENS_FILE):
        with open(SEEN_TOKENS_FILE, "r") as f:
            try:
                return set(json.load(f))
            except Exception:
                return set()
    return set()

def guardar_tokens_vistos(vistos: set) -> None:
    with open(SEEN_TOKENS_FILE, "w") as f:
        json.dump(list(vistos), f)

def cargar_simulaciones() -> list:
    if os.path.exists(SIMULATIONS_FILE):
        with open(SIMULATIONS_FILE, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return []
    return []

def guardar_simulacion(nueva_sim: dict) -> None:
    simulaciones = cargar_simulaciones()
    simulaciones.append(nueva_sim)
    with open(SIMULATIONS_FILE, "w") as f:
        json.dump(simulaciones, f, indent=4)


# ──────────────────────────────────────────────────────────────────────────
# CONEXIÓN CON APIS
# ──────────────────────────────────────────────────────────────────────────

def obtener_direcciones_tokens_nuevos() -> list:
    url = "https://dexscreener.com"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        perfiles = resp.json() or []
        return [p.get("tokenAddress") for p in perfiles if p.get("chainId") == "solana" and p.get("tokenAddress")]
    except Exception as e:
        log.warning(f"Error consultando perfiles nuevos: {e}")
        return []

def obtener_pares_de_token(direccion: str) -> list:
    url = f"https://dexscreener.com{direccion}"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("pairs", []) or []
    except Exception as e:
        log.warning(f"Error consultando pares de {direccion}: {e}")
        return []

def obtener_tokens_nuevos_solana() -> list:
    direcciones = obtener_direcciones_tokens_nuevos()
    todos_los_pares = []
    for direccion in direcciones:
        pares = obtener_pares_de_token(direccion)
        pares_solana = [p for p in pares if p.get("chainId") == "solana"]
        todos_los_pares.extend(pares_solana)
        time.sleep(0.2)
    return todos_los_pares

def obtener_concentracion_top10(direccion_token: str):
    try:
        resp_supply = requests.post(SOLANA_RPC_URL, json={"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply", "params": [direccion_token]}, timeout=10)
        resp_supply.raise_for_status()
        total_supply = float(resp_supply.json().get("result", {}).get("value", {}).get("uiAmount") or 0)
        if total_supply <= 0: return None

        resp_top = requests.post(SOLANA_RPC_URL, json={"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": [direccion_token]}, timeout=10)
        resp_top.raise_for_status()
        cuentas = resp_top.json().get("result", {}).get("value", []) or []
        suma_top10 = sum(float(c.get("uiAmount") or 0) for c in cuentas[:10])
        return (suma_top10 / total_supply) * 100
    except Exception as e:
        log.warning(f"No se pudo calcular holders para {direccion_token}: {e}")
        return None

def cumple_filtros(par: dict) -> bool:
    try:
        info = par.get("info", {})
        socials = info.get("socials", []) or []
        websites = info.get("websites", []) or []
        
        tiene_twitter = any(s.get("type") == "twitter" for s in socials)
        tiene_web = len(websites) > 0
        
        if not tiene_twitter and not tiene_web:
            return False

        liquidez = float(par.get("liquidity", {}).get("usd") or 0)
        volumen_24h = float(par.get("volume", {}).get("h24") or 0)
        market_cap = float(par.get("fdv") or par.get("marketCap") or 0)
        cambio_5m = float(par.get("priceChange", {}).get("m5") or 0)
        creado_ts = par.get("pairCreatedAt")

        if not creado_ts or liquidez < FILTROS["liquidez_minima_usd"] or volumen_24h < FILTROS["volumen_24h_minimo_usd"]:
            return False
        if market_cap > FILTROS["market_cap_maximo_usd"] or cambio_5m < FILTROS["cambio_precio_5m_minimo_pct"]:
            return False

        edad_horas = (datetime.now(timezone.utc).timestamp() - (creado_ts / 1000)) / 3600
        if edad_horas > FILTROS["edad_maxima_horas"]:
            return False

        return True
    except Exception as e:
        return False


# ──────────────────────────────────────────────────────────────────────────
# ENVÍO DE ALERTAS
# ──────────────────────────────────────────────────────────────────────────

def enviar_alerta_telegram(bot, par: dict) -> None:
    token_address = par.get("baseToken", {}).get("address", "")
    nombre = par.get("baseToken", {}).get("name", "Unknown")
    simbolo = par.get("baseToken", {}).get("symbol", "TOKEN")
    precio = par.get("priceUsd", "0.00")
    liquidez = par.get("liquidity", {}).get("usd", 0)
    market_cap = par.get("fdv") or par.get("marketCap") or 0
    volumen_24h = par.get("volume", {}).get("h24", 0)
    cambio_5m = par.get("priceChange", {}).get("m5", 0)
    dex_url = par.get("url", "")

    info = par.get("info", {})
    socials = info.get("socials", []) or []
    twitter_url = next((s.get("url") for s in socials if s.get("type") == "twitter"), None)

    redes_texto = ""
    if twitter_url:
        redes_texto = f"🐦 *Twitter (X):* [Abrir Perfil]({twitter_url})\n"

    mensaje = (
        f"🚨 *Token nuevo detectado*\n\n"
        f"*{nombre}* ({simbolo})\n"
        f"💰 *Precio:* ${precio}\n"
        f"📊 *Market cap:* ${market_cap:,.0f}\n"
        f"💧 *Liquidez:* ${liquidez:,.0f}\n"
        f"📈 *Volumen 24h:* ${volumen_24h:,.0f}\n"
        f"⚡ *Cambio 5min:* {cambio_5m}%\n"
        f"{redes_texto}\n"
        f"📄 *Contrato:*\n`{token_address}`\n\n"
        f"⚠️ *Verifica liquidez antes de simular u operar.*"
    )

    if dex_url:
        mensaje += f"\n\n🔗 [Ver en DexScreener]({dex_url})"

    keyboard = [
        [
            InlineKeyboardButton("📋 Copiar Contrato", callback_data=f"copy|{token_address}"),
            InlineKeyboardButton("🧪 Simular S/. 100", callback_data=f"sim|{token_address}|{simbolo}|{precio}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        bot.send_message(chat_id=CHAT_ID, text=mensaje, parse_mode="Markdown", reply_markup=reply_markup, disable_web_page_preview=False)
    except Exception as e:
        log.error(f"Error enviando alerta: {e}")


# ──────────────────────────────────────────────────────────────────────────
# MANEJO DE COMANDOS Y CALLBACKS (OFICIAL)
# ──────────────────────────────────────────────────────────────────────────

def portafolio_command(update: Update, context: CallbackContext) -> None:
    chat_id = str(update.effective_chat.id)
    if chat_id != CHAT_ID: return

    simulaciones = cargar_simulaciones()
    if not simulaciones:
        update.message.reply_text("📁 Tu portafolio de simulación está vacío. ¡Presiona el botón de simular en las alertas!")
        return

    texto_reporte = "📊 *RESUMEN DE TU PORTAFOLIO FICTICIO (P&L)*\n\n"
    total_invertido_soles = len(simulaciones) * 100
    total_actual_soles = 0.0

    for sim in simulaciones:
        addr = sim["address"]
        sym = sim["simbolo"]
        p_entrada = sim["precio_entrada"]
        
        pares = obtener_pares_de_token(addr)
        p_actual = p_entrada
        if pares:
            try:
                p_actual = float(pares[0].get("priceUsd", p_entrada))
            except Exception:
                p_actual = p_entrada
            
        rendimiento_pct = ((p_actual - p_entrada) / p_entrada) * 100 if p_entrada > 0 else 0
        valor_actual_soles = 100 * (1 + (rendimiento_pct / 100))
        total_actual_soles += valor_actual_soles
        
        emoji = "📈" if rendimiento_pct >= 0 else "📉"
        texto_reporte += f"{emoji} *{sym}*:\n• Entrada: ${p_entrada}\n• Actual: ${p_actual}\n• Rendimiento: {rendimiento_pct:+.2f}%\n• Valor actual: S/. {valor_actual_soles:.2f}\n\n"

    ganancia_neta_soles = total_actual_soles - total_invertido_soles
    emoji_total = "🟢" if ganancia_neta_soles >= 0 else "🔴"
    
    texto_reporte += (
        f"───────────────────\n"
        f"💰 *Total Invertido:* S/. {total_invertido_soles:.2f}\n"
        f"💵 *Valor de Mercado:* S/. {total_actual_soles:.2f}\n"
