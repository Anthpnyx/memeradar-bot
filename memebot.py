"""
MemeBot - Bot de Telegram para detectar memecoins nuevas con potencial en Solana
Incluye: Filtro de Social Hype, Simulador de Papel (S/. 100) y Comando /portafolio
=================================================================================
"""

import os
import time
import json
import logging
import requests
from datetime import datetime, timezone

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
            return set(json.load(f))
    return set()

def guardar_tokens_vistos(vistos: set) -> None:
    with open(SEEN_TOKENS_FILE, "w") as f:
        json.dump(list(vistos), f)

def cargar_simulaciones() -> list:
    if os.path.exists(SIMULATIONS_FILE):
        with open(SIMULATIONS_FILE, "r") as f:
            return json.load(f)
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
    except requests.RequestException as e:
        log.warning(f"Error consultando perfiles nuevos: {e}")
        return []

def obtener_pares_de_token(direccion: str) -> list:
    url = f"https://dexscreener.com{direccion}"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("pairs", []) or []
    except requests.RequestException as e:
        log.warning(f"Error consultando pares de {direccion}: {e}")
        return []

def obtener_tokens_nuevos_solana() -> list:
    direcciones = obtener_direcciones_tokens_nuevos()
    todos_los_pares = []
    for direccion in direcciones:
        pares = obtener_pares_de_token(direccion)
        pares_solana = [p for p in pares if p.get("chainId") == "solana"]
        todos_los_pares.extend(pares_solana)
        time.sleep(0.3)
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


# ──────────────────────────────────────────────────────────────────────────
# FILTROS Y LÓGICA DE SOCIAL HYPE
# ──────────────────────────────────────────────────────────────────────────

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
        log.error(f"Error en filtros: {e}")
        return False


# ──────────────────────────────────────────────────────────────────────────
# ALERTAS Y BOTONES INTERACTIVOS
# ──────────────────────────────────────────────────────────────────────────

def enviar_alerta_telegram(par: dict) -> None:
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

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "📋 Copiar Contrato", "callback_data": f"copy|{token_address}"},
                {"text": "🧪 Simular S/. 100", "callback_data": f"sim|{token_address}|{simbolo}|{precio}"}
            ]
        ]
    }

    url_telegram = f"https://telegram.org{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": mensaje,
        "parse_mode": "Markdown",
        "reply_markup": json.dumps(reply_markup),
        "disable_web_page_preview": False
    }

    if dex_url:
        payload["text"] += f"\n\n🔗 [Ver en DexScreener]({dex_url})"

    try:
        requests.post(url_telegram, json=payload, timeout=10)
    except Exception as e:
        log.error(f"Error sending alert: {e}")


# ──────────────────────────────────────────────────────────────────────────
# MANEJO DEL SIMULADOR CORREGIDO (LÓGICA CON SEPARADOR '|')
# ──────────────────────────────────────────────────────────────────────────

def procesar_actualizaciones_telegram():
    url_updates = f"https://telegram.org{TELEGRAM_TOKEN}/getUpdates"
    try:
        resp = requests.get(url_updates, params={"timeout": 1, "allowed_updates": ["message", "callback_query"]}, timeout=5)
        if resp.status_code != 200: return

        updates = resp.json().get("result", [])
        ultimo_id = 0
        
        for u in updates:
            ultimo_id = u.get("update_id")
            
            if "message" in u and "text" in u["message"]:
                msg_text = u["message"]["text"]
                chat_id_remitente = str(u["message"]["chat"]["id"])
                
                if msg_text == "/portafolio" and chat_id_remitente == CHAT_ID:
                    enviar_resumen_portafolio()

            elif "callback_query" in u:
                cb = u["callback_query"]
                cb_id = cb.get("id")
                data = cb.get("data", "")
                
                # Usamos una barra recta '|' para separar los datos sin conflictos
                if data.startswith("sim|"):
                    parts = data.split("|")
                    if len(parts) >= 4:
                        address = parts[1]
                        simbolo = parts[2]
                        precio_entrada = parts[3]
                        
                        nueva_sim = {
                            "address": address,
                            "simbolo": simbolo,
                            "precio_entrada": float(precio_entrada),
