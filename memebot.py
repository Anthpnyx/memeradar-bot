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
    "liquidez_minima_usd": 3_000,       # ignora tokens con menos liquidez que esto
    "volumen_24h_minimo_usd": 5_000,    # actividad mínima reciente
    "edad_maxima_horas": 48,            # solo tokens lanzados hace menos de X horas
    "market_cap_maximo_usd": 10_000_000,# evita tokens que ya "explotaron" y subir es más difícil
    "cambio_precio_5m_minimo_pct": 2,   # solo sube (no baja): mínimo % de subida en 5 min
    "holders_top10_maximo_pct": 35,     # rechaza tokens muy concentrados (riesgo de manipulación/rug)
}

# RPC público de Solana (oficial, gratis, sin API key) — usado para calcular
# qué % del suministro tienen las 10 wallets más grandes de un token
SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"

# ──────────────────────────────────────────────────────────────────────────
# SIMULACIÓN DE COMPRA (paper trading — no usa dinero real)
# ──────────────────────────────────────────────────────────────────────────

# Cuánto "invierte" el bot de forma simulada en cada token que te avisa
SIMULACION_MONTO_USD = 50

# Archivo donde guarda las simulaciones abiertas
SIMULACIONES_FILE = "simulaciones.json"

# A qué horas de vida de la simulación se manda un reporte de progreso
SIMULACION_CHECKPOINTS_HORAS = [1, 6, 24]

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


def obtener_direcciones_tokens_nuevos() -> list:
    """
    Consulta el endpoint de 'perfiles de token más recientes' de DexScreener.
    Este endpoint sí refleja tokens que acaban de aparecer, a diferencia de
    /search que devuelve siempre resultados similares para la misma consulta.
    Devuelve una lista de direcciones de contrato en Solana.
    """
    url = "https://api.dexscreener.com/token-profiles/latest/v1"
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
    """
    Dado un token address, trae sus pares de trading (liquidez, volumen, etc.)
    """
    url = f"https://api.dexscreener.com/latest/dex/tokens/{direccion}"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("pairs", []) or []
    except requests.RequestException as e:
        log.warning(f"Error consultando pares de {direccion}: {e}")
        return []


def obtener_tokens_nuevos_solana() -> list:
    """
    Combina los pasos anteriores: obtiene tokens recién listados en Solana
    y trae los datos completos de sus pares de trading.
    """
    direcciones = obtener_direcciones_tokens_nuevos()
    todos_los_pares = []
    for direccion in direcciones:
        pares = obtener_pares_de_token(direccion)
        pares_solana = [p for p in pares if p.get("chainId") == "solana"]
        todos_los_pares.extend(pares_solana)
        time.sleep(0.3)  # pausa breve para no saturar la API
    return todos_los_pares


def obtener_concentracion_top10(direccion_token: str):
    """
    Calcula qué % del suministro total tienen las 10 wallets más grandes,
    usando el RPC oficial y gratuito de Solana (sin API key).
    Devuelve un float (porcentaje) o None si no se pudo calcular.
    """
    try:
        # 1) Suministro total del token
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

        # 2) Las cuentas con más tokens (hasta 20, tomamos las primeras 10)
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
    except (requests.RequestException, TypeError, ValueError, KeyError) as e:
        log.warning(f"No se pudo calcular concentración de holders para {direccion_token}: {e}")
        return None


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
        if cambio_5m < FILTROS["cambio_precio_5m_minimo_pct"]:
            return False

        return True
    except (TypeError, ValueError):
        return False


def formatear_alerta_completa(par: dict, concentracion_top10=None) -> str:
    """Arma el texto del mensaje de Telegram con toda la info clave del token."""
    nombre = par.get("baseToken", {}).get("name", "?")
    simbolo = par.get("baseToken", {}).get("symbol", "?")
    direccion = par.get("baseToken", {}).get("address", "?")
    precio = par.get("priceUsd", "?")
    liquidez = par.get("liquidity", {}).get("usd", 0)
    volumen = par.get("volume", {}).get("h24", 0)
    market_cap = par.get("fdv") or par.get("marketCap") or 0
    cambio_5m = par.get("priceChange", {}).get("m5", 0)
    url = par.get("url", "")

    if concentracion_top10 is not None:
        linea_holders = f"👥 Top 10 holders: {concentracion_top10:.1f}% del suministro\n"
    else:
        linea_holders = ""

    return (
        "🚨 *Token nuevo detectado*\n\n"
        f"*{nombre}* (${simbolo})\n"
        f"💰 Precio: ${precio}\n"
        f"📊 Market cap: ${float(market_cap):,.0f}\n"
        f"💧 Liquidez: ${float(liquidez):,.0f}\n"
        f"📈 Volumen 24h: ${float(volumen):,.0f}\n"
        f"⚡ Cambio 5min: {cambio_5m}%\n"
        f"{linea_holders}"
        f"📄 Contrato:\n"
        f"`{direccion}`\n\n"
        f"⚠️ Verifica liquidez y holders antes de comprar. Esto es una alerta, no una recomendación.\n\n"
        f"🔗 {url}"
    )


def construir_botones(direccion: str) -> dict:
    """
    Arma el teclado inline con un solo botón:
    - 'Copiar Contrato': usa el botón nativo de copiar texto de Telegram
      (disponible desde Bot API 7.0), copia la dirección con un solo toque.
    """
    return {
        "inline_keyboard": [
            [
                {"text": "📋 Copiar Contrato", "copy_text": {"text": direccion}},
            ]
        ]
    }


def enviar_alerta_telegram(mensaje: str, botones: dict = None) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": mensaje,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    if botones:
        payload["reply_markup"] = botones
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning(f"Error enviando mensaje a Telegram: {e}")


# ──────────────────────────────────────────────────────────────────────────
# SIMULACIÓN DE COMPRA — funciones
# ──────────────────────────────────────────────────────────────────────────

def cargar_simulaciones() -> list:
    """Carga las simulaciones de compra que siguen abiertas."""
    if os.path.exists(SIMULACIONES_FILE):
        with open(SIMULACIONES_FILE, "r") as f:
            return json.load(f)
    return []


def guardar_simulaciones(simulaciones: list) -> None:
    with open(SIMULACIONES_FILE, "w") as f:
        json.dump(simulaciones, f)


def crear_simulacion(par: dict) -> dict:
    """
    Crea el registro de una 'compra simulada' con el monto configurado,
    al precio del momento en que el bot mandó la alerta.
    """
    direccion = par.get("baseToken", {}).get("address", "?")
    simbolo = par.get("baseToken", {}).get("symbol", "?")
    precio_entrada = float(par.get("priceUsd") or 0)

    return {
        "direccion": direccion,
        "simbolo": simbolo,
        "precio_entrada": precio_entrada,
        "monto_usd": SIMULACION_MONTO_USD,
        "timestamp_entrada": datetime.now(timezone.utc).timestamp(),
        "checkpoints_enviados": [],
    }


def obtener_precio_actual(direccion: str):
    """Trae el precio actual de un token, usando el par con más liquidez."""
    pares = obtener_pares_de_token(direccion)
    if not pares:
        return None
    mejor_par = max(
        pares, key=lambda p: float(p.get("liquidity", {}).get("usd") or 0)
    )
    precio = mejor_par.get("priceUsd")
    return float(precio) if precio else None


def formatear_reporte_simulacion(sim: dict, precio_actual: float, horas: float, es_final: bool) -> str:
    """Arma el mensaje de progreso/resultado de una simulación."""
    precio_entrada = sim["precio_entrada"]
    monto = sim["monto_usd"]
    valor_actual = (precio_actual / precio_entrada) * monto if precio_entrada else 0
    pct = ((valor_actual - monto) / monto) * 100 if monto else 0
    emoji = "🟢" if pct >= 0 else "🔴"
    titulo = "🏁 *Resultado final (24h)*" if es_final else "📈 *Actualización de simulación*"

    return (
        f"{titulo}\n\n"
        f"*{sim['simbolo']}* — simulación de ${monto} invertidos\n"
        f"⏱ Tiempo transcurrido: {horas:.1f}h\n"
        f"💵 Precio entrada: ${precio_entrada}\n"
        f"💵 Precio actual: ${precio_actual}\n"
        f"{emoji} Valor actual: ${valor_actual:.2f} ({pct:+.1f}%)\n\n"
        f"_Esto es solo una simulación, no dinero real._"
    )


def revisar_simulaciones(simulaciones: list) -> bool:
    """
    Revisa cada simulación abierta, manda reportes en los checkpoints
    definidos, y cierra (elimina) las que ya llegaron a las 24h.
    Devuelve True si hubo cambios que guardar.
    """
    cambios = False
    simulaciones_activas = []

    for sim in simulaciones:
        ahora = datetime.now(timezone.utc).timestamp()
        horas_transcurridas = (ahora - sim["timestamp_entrada"]) / 3600

        precio_actual = obtener_precio_actual(sim["direccion"])
        if precio_actual is None:
            # no se pudo obtener precio (quizás el pool ya no existe); la dejamos igual
            simulaciones_activas.append(sim)
            continue

        checkpoint_a_enviar = None
        for cp in SIMULACION_CHECKPOINTS_HORAS:
            if horas_transcurridas >= cp and cp not in sim["checkpoints_enviados"]:
                checkpoint_a_enviar = cp
                break

        if checkpoint_a_enviar is not None:
            es_final = checkpoint_a_enviar == max(SIMULACION_CHECKPOINTS_HORAS)
            mensaje = formatear_reporte_simulacion(
                sim, precio_actual, horas_transcurridas, es_final
            )
            enviar_alerta_telegram(mensaje)
            sim["checkpoints_enviados"].append(checkpoint_a_enviar)
            cambios = True
            log.info(f"Reporte de simulación enviado: {sim['simbolo']} ({checkpoint_a_enviar}h)")

        # si ya se envió el reporte final (24h), la simulación se cierra
        if max(SIMULACION_CHECKPOINTS_HORAS) in sim["checkpoints_enviados"]:
            cambios = True
            continue  # no se vuelve a agregar a la lista activa

        simulaciones_activas.append(sim)

    simulaciones[:] = simulaciones_activas
    return cambios


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

    simulaciones = cargar_simulaciones()
    log.info(f"Cargadas {len(simulaciones)} simulaciones abiertas.")

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
                    concentracion = obtener_concentracion_top10(direccion)
                    if (
                        concentracion is not None
                        and concentracion > FILTROS["holders_top10_maximo_pct"]
                    ):
                        log.info(
                            f"Descartado por concentración alta: "
                            f"{par.get('baseToken', {}).get('symbol')} "
                            f"({concentracion:.1f}% en top 10)"
                        )
                        vistos.add(direccion)
                        continue

                    mensaje = formatear_alerta_completa(par, concentracion)
                    botones = construir_botones(direccion)
                    enviar_alerta_telegram(mensaje, botones)
                    vistos.add(direccion)
                    nuevos_encontrados += 1
                    log.info(f"Alerta enviada: {par.get('baseToken', {}).get('symbol')}")

                    simulaciones.append(crear_simulacion(par))
                else:
                    # lo marcamos como visto igual para no re-evaluarlo cada vez
                    vistos.add(direccion)

            if nuevos_encontrados > 0:
                guardar_tokens_vistos(vistos)
                guardar_simulaciones(simulaciones)

            if revisar_simulaciones(simulaciones):
                guardar_simulaciones(simulaciones)

            log.info(
                f"Ciclo completo. {nuevos_encontrados} alertas nuevas enviadas. "
                f"{len(simulaciones)} simulaciones abiertas."
            )

        except Exception as e:
            log.error(f"Error inesperado en el loop principal: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
