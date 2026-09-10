"""
MemeBot - Bot de Telegram para detectar memecoins nuevas con potencial en Solana
=================================================================================

Qué hace:
- Revisa periódicamente los pares/tokens nuevos en Solana usando la API pública
  de DexScreener (gratis, sin necesidad de API key).
- Filtra los tokens según criterios de "potencial" que tú defines (liquidez
  mínima, volumen, ratio compradores/vendedores, etc.)
- Envía una alerta a tu Telegram cuando encuentra un token que cumple los filtros.
- Evita mandar el mismo token dos veces (guarda un registro local).

Requisitos antes de correrlo:
1. Python 3.9+
2. Instalar dependencias:
       pip install python-telegram-bot requests --break-system-packages
3. Crear un bot de Telegram:
       - Habla con @BotFather en Telegram
       - Envía /newbot y sigue las instrucciones
       - Copia el TOKEN que te da
4. Obtener tu chat_id:
       - Habla con @userinfobot en Telegram, te dirá tu ID numérico
       - O usa el ID de un grupo/canal si quieres que llegue ahí
5. Reemplaza las variables TELEGRAM_TOKEN y CHAT_ID abajo (o ponlas como
   variables de entorno, más seguro).

Cómo correrlo:
       python memebot.py

IMPORTANTE:
- Este bot es una herramienta de DETECCIÓN, no de trading automático. No compra
  ni vende nada por ti. Te avisa, tú decides.
- Los filtros por defecto son un punto de partida. Ajústalos según tu propio
  criterio de riesgo (ver sección FILTROS más abajo).
- Ningún filtro elimina el riesgo de rug pulls o pérdidas. Esto es una ayuda
  para filtrar ruido, no una garantía de nada.
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

# Cada cuánto revisa tokens nuevos (en segundos)
POLL_INTERVAL_SECONDS = 60

# Archivo donde guarda qué tokens ya avisó, para no repetir alertas
SEEN_TOKENS_FILE = "seen_tokens.json"

# ──────────────────────────────────────────────────────────────────────────
# FILTROS — Ajusta esto según qué tan arriesgado quieras ser
# ──────────────────────────────────────────────────────────────────────────

FILTROS = {
    "liquidez_minima_usd": 10_000,      # ignora tokens con menos liquidez que esto
    "volumen_24h_minimo_usd": 20_000,   # actividad mínima reciente
    "edad_maxima_horas": 24,            # solo tokens lanzados hace menos de X horas
    "market_cap_maximo_usd": 5_000_000, # evita tokens que ya "explotaron" y subir es más difícil
    "cambio_precio_5m_minimo_pct": 5,   # debe estar moviéndose, no plano
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("memebot")


# ──────────────────────────────────────────────────────────────────────────
# FUNCIONES
# ──────────────────────────────────────────────────────────────────────────

def cargar_tokens_vistos() -> set:
    """Carga la lista de tokens que ya avisamos antes."""
    if os.path.exists(SEEN_TOKENS_FILE):
        with open(SEEN_TOKENS_FILE, "r") as f:
            return set(json.load(f))
    return set()


def guardar_tokens_vistos(vistos: set) -> None:
    with open(SEEN_TOKENS_FILE, "w") as f:
        json.dump(list(vistos), f)


def obtener_tokens_nuevos_solana() -> list:
    """
    Consulta DexScreener por pares recientes en Solana.
    Devuelve una lista de diccionarios con la info de cada par.
    """
    url = "https://api.dexscreener.com/latest/dex/search"
    params = {"q": "solana"}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        pares = data.get("pairs", []) or []
        # nos quedamos solo con pares de la chain solana
        return [p for p in pares if p.get("chainId") == "solana"]
    except requests.RequestException as e:
        log.warning(f"Error consultando DexScreener: {e}")
        return []


def cumple_filtros(par: dict) -> bool:
    """Aplica los criterios definidos en FILTROS a un par de DexScreener."""
    try:
        liquidez = float(par.get("liquidity", {}).get("usd") or 0)
        volumen_24h = float(par.get("volume", {}).get("h24") or 0)
        market_cap = float(par.get("fdv") or par.get("marketCap") or 0)
        cambio_5m = float(par.get("priceChange", {}).get("m5") or 0)
        creado_ts = par.get("pairCreatedAt")  # milisegundos epoch

        if not creado_ts:
            return False

        edad_horas = (
            datetime.now(timezone.utc).timestamp() - (creado_ts / 1000)
        ) / 3600

        if liquidez < FILTROS["liquidez_minima_usd"]:
            return False
        if volumen_24h < FILTROS["volumen_24h_minimo_usd"]:
            return False
        if edad_horas > FILTROS["edad_maxima_horas"]:
            return False
        if market_cap > FILTROS["market_cap_maximo_usd"]:
            return False
        if abs(cambio_5m) < FILTROS["cambio_precio_5m_minimo_pct"]:
            return False

        return True
    except (TypeError, ValueError):
        return False


def formatear_alerta_completa(par: dict) -> str:
    """Arma el mensaje de Telegram con toda la info clave del token."""
    nombre = par.get("baseToken", {}).get("name", "?")
    simbolo = par.get("baseToken", {}).get("symbol", "?")
    direccion = par.get("baseToken", {}).get("address", "?")
    precio = par.get("priceUsd", "?")
    liquidez = par.get("liquidity", {}).get("usd", 0)
    volumen = par.get("volume", {}).get("h24", 0)
    market_cap = par.get("fdv") or par.get("marketCap") or 0
    cambio_5m = par.get("priceChange", {}).get("m5", 0)
    url = par.get("url", "")

    return (
        "🚨 *Token nuevo detectado*\n\n"
        f"*{nombre}* (${simbolo})\n"
        f"💰 Precio: ${precio}\n"
        f"📊 Market cap: ${float(market_cap):,.0f}\n"
        f"💧 Liquidez: ${float(liquidez):,.0f}\n"
        f"📈 Volumen 24h: ${float(volumen):,.0f}\n"
        f"⚡ Cambio 5min: {cambio_5m}%\n\n"
        f"📄 Contrato: `{direccion}`\n"
        f"🔗 [Ver en DexScreener]({url})\n\n"
        f"⚠️ Verifica liquidez y holders antes de comprar. Esto es una alerta, no una recomendación."
    )


def enviar_alerta_telegram(mensaje: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": mensaje,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning(f"Error enviando mensaje a Telegram: {e}")


# ──────────────────────────────────────────────────────────────────────────
# LOOP PRINCIPAL
# ──────────────────────────────────────────────────────────────────────────

def main():
    log.info("Iniciando MemeBot...")

    if TELEGRAM_TOKEN == "PON_AQUI_TU_TOKEN" or CHAT_ID == "PON_AQUI_TU_CHAT_ID":
        log.error(
            "Falta configurar TELEGRAM_TOKEN y CHAT_ID. "
            "Edita las variables al inicio del archivo o usa variables de entorno."
        )
        return

    vistos = cargar_tokens_vistos()
    log.info(f"Cargados {len(vistos)} tokens ya vistos previamente.")

    while True:
        try:
            pares = obtener_tokens_nuevos_solana()
            log.info(f"Consultados {len(pares)} pares de Solana.")

            nuevos_encontrados = 0
            for par in pares:
                direccion = par.get("baseToken", {}).get("address")
                if not direccion or direccion in vistos:
                    continue

                if cumple_filtros(par):
                    mensaje = formatear_alerta_completa(par)
                    enviar_alerta_telegram(mensaje)
                    vistos.add(direccion)
                    nuevos_encontrados += 1
                    log.info(f"Alerta enviada: {par.get('baseToken', {}).get('symbol')}")
                else:
                    # lo marcamos como visto igual para no re-evaluarlo cada vez
                    vistos.add(direccion)

            if nuevos_encontrados > 0:
                guardar_tokens_vistos(vistos)

            log.info(f"Ciclo completo. {nuevos_encontrados} alertas nuevas enviadas.")

        except Exception as e:
            log.error(f"Error inesperado en el loop principal: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
