"""
MemeBot - Versión Original Limpia Restaurada para Railway
=========================================================
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
# FUNCIONES NATIVAS ORIGINALES
# ──────────────────────────────────────────────────────────────────────────

def cargar_tokens_vistos() -> set:
    if os.path.exists(SEEN_TOKENS_FILE):
        with open(SEEN_TOKENS_FILE, "r") as f:
            return set(json.load(f))
    return set()


def guardar_tokens_vistos(vistos: set) -> None:
    with open(SEEN_TOKENS_FILE, "w") as f:
        json.dump(list(vistos), f)


def obtener_direcciones_tokens_nuevos() -> list:
    url = "https://dexscreener.com"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        perfiles = resp.json() or []
        direcciones = [
            p.get("tokenAddress")
            for p in perfiles
            if p.get("chainId") == "solana" and p.get("tokenAddress")
        ]
        return direcciones
    except requests.RequestException as e:
        log.warning(f"Error consultando perfiles nuevos de DexScreener: {e}")
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
        resp_supply = requests.post(
            SOLANA_RPC_URL,
            json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getTokenSupply",
                "params": [direccion_token],
            },
            timeout=10,
        )
        resp_supply.raise_for_status()
        supply_data = resp_supply.json()
        total_supply = float(
            supply_data.get("result", {}).get("value", {}).get("uiAmount") or 0
        )
        if total_supply <= 0:
            return None

        resp_top = requests.post(
            SOLANA_RPC_URL,
            json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getTokenLargestAccounts",
                "params": [direccion_token],
            },
            timeout=10,
        )
        resp_top.raise_for_status()
        top_data = resp_top.json()
        cuentas = top_data.get("result", {}).get("value", []) or []
        top10 = cuentas[:10]
        suma_top10 = sum(float(c.get("uiAmount") or 0) for c in top10)

        return (suma_top10 / total_supply) * 100
    except Exception as e:
        log.warning(f"No se pudo calcular concentración de holders para {direccion_token}: {e}")
        return None


def cumple_filtros(par: dict) -> bool:
    try:
        liquidez = float(par.get("liquidity", {}).get("usd") or 0)
        volumen_24h = float(par.get("volume", {}).get("h24") or 0)
        market_cap = float(par.get("fdv") or par.get("marketCap") or 0)
        cambio_5m = float(par.get("priceChange", {}).get("m5") or 0)
        creado_ts = par.get("pairCreatedAt")

        if not creado_ts:
            return False

        edad_horas = (
            datetime.now(timezone.utc).timestamp() - (creado_ts / 1000)
        ) / 3600

        if liquidez < FILTROS["liquidez_minima_usd"]:
            return False
        if volumen_24h < FILTROS["volumen_24h_minimo_usd"]:
            return False
        if market_cap > FILTROS["market_cap_maximo_usd"]:
            return False
        if edad_horas > FILTROS["edad_maxima_horas"]:
            return False
        if cambio_5m < FILTROS["cambio_precio_5m_minimo_pct"]:
            return False

        return True
    except Exception as e:
        log.error(f"Error evaluando filtros: {e}")
        return False


# ──────────────────────────────────────────────────────────────────────────
# FORMATO VISUAL CON VISTA PREVIA Y BOTÓN ÚNICO
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

    mensaje = (
        f"🚨 *Token nuevo detectado*\n\n"
        f"*{nombre}* ({simbolo})\n"
        f"💰 *Precio:* \${precio}\n"
        f"📊 *Market cap:* \${market_cap:,.0f}\n"
        f"💧 *Liquidez:* \${liquidez:,.0f}\n"
        f"📈 *Volumen 24h:* \${volumen_24h:,.0f}\n"
        f"⚡ *Cambio 5min:* {cambio_5m}%\n\n"
        f"📄 *Contrato:*\n`{token_address}`\n\n"
        f"⚠️ *Verifica liquidez y holders antes de comprar. Esto es una alerta, no una recomendación.*"
    )

    # UN SOLO BOTÓN INTERACTIVO: Copiar contrato
    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "📋 Copiar Contrato",
                    "callback_data": f"copy_{token_address}"
                }
            ]
        ]
    }

    url_telegram = f"https://telegram.org{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": mensaje,
        "parse_mode": "Markdown",
        "reply_markup": json.dumps(reply_markup),
        "disable_web_page_preview": False  # PERMITE LA VISTA PREVIA DEL GRÁFICO
    }

    if dex_url:
        payload["text"] += f"\n\n🔗 [Ver en DexScreener]({dex_url})"

    try:
        requests.post(url_telegram, json=payload, timeout=10)
        log.info(f"Alerta enviada para el token {simbolo}")
    except Exception as e:
        log.error(f"Error enviando mensaje a Telegram: {e}")


# ──────────────────────────────────────────────────────────────────────────
# BUCLE ORIGINAL SEGURO
# ──────────────────────────────────────────────────────────────────────────

def ejecutar_bot():
    log.info("Memebot iniciado correctamente. Escaneando Solana...")
    tokens_vistos = cargar_tokens_vistos()

    while True:
        try:
            pares_nuevos = obtener_tokens_nuevos_solana()
            for par in pares_nuevos:
                token_address = par.get("baseToken", {}).get("address")
                if not token_address or token_address in tokens_vistos:
                    continue

                if cumple_filtros(par):
                    top10_pct = obtener_concentracion_top10(token_address)
                    
                    if top10_pct and top10_pct > FILTROS["holders_top10_maximo_pct"]:
                        log.info(f"Token ignorado por alta concentración de holders ({top10_pct:.2f}%)")
                        tokens_vistos.add(token_address)
                        continue

                    enviar_alerta_telegram(par)
                    tokens_vistos.add(token_address)
                    guardar_tokens_vistos(tokens_vistos)

            time.sleep(POLL_INTERVAL_SECONDS)
        except Exception as e:
            log.error(f"Error en el bucle principal: {e}")
            time.sleep(10)


if __name__ == "__main__":
    ejecutar_bot()
