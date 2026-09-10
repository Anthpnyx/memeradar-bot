# MemeBot — Detector de memecoins nuevas en Solana

Bot de Telegram que revisa tokens nuevos en Solana cada minuto y te avisa
cuando uno cumple ciertos criterios de "potencial" (liquidez, volumen,
movimiento de precio, edad del token).

## Paso 1: Crear tu bot de Telegram

1. Abre Telegram y busca **@BotFather**
2. Envíale `/newbot`
3. Ponle un nombre (ej. "Mi Memebot") y un usuario único (debe terminar en `bot`, ej. `mi_memebot_bot`)
4. BotFather te dará un **token** parecido a esto:
   `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`
   Guárdalo, es tu `TELEGRAM_TOKEN`.

## Paso 2: Obtener tu Chat ID

1. Busca **@userinfobot** en Telegram y envíale cualquier mensaje
2. Te va a responder con tu ID numérico (ej. `987654321`)
   Ese es tu `CHAT_ID`.
3. **Importante:** después de crear tu bot, mándale un mensaje cualquiera
   a TU bot (búscalo por su username) antes de correr el script — si no,
   Telegram no te deja enviarle mensajes todavía.

## Paso 3: Instalar dependencias

En tu computadora, con Python 3.9 o más nuevo instalado:

```bash
pip install requests --break-system-packages
```

(No necesitas `python-telegram-bot` para esta versión — el script usa la
API de Telegram directamente con `requests`, es más simple.)

## Paso 4: Configurar el bot

Abre `memebot.py` y reemplaza estas dos líneas con tus datos:

```python
TELEGRAM_TOKEN = os.getenv("MEMEBOT_TELEGRAM_TOKEN", "PON_AQUI_TU_TOKEN")
CHAT_ID = os.getenv("MEMEBOT_CHAT_ID", "PON_AQUI_TU_CHAT_ID")
```

O, más seguro (para no dejar tus credenciales escritas en el archivo),
define variables de entorno antes de correrlo:

```bash
export MEMEBOT_TELEGRAM_TOKEN="tu_token_aqui"
export MEMEBOT_CHAT_ID="tu_chat_id_aqui"
```

## Paso 5: Ajustar los filtros (opcional pero recomendado)

Dentro del archivo, busca el diccionario `FILTROS`:

```python
FILTROS = {
    "liquidez_minima_usd": 10_000,
    "volumen_24h_minimo_usd": 20_000,
    "edad_maxima_horas": 24,
    "market_cap_maximo_usd": 5_000_000,
    "cambio_precio_5m_minimo_pct": 5,
}
```

- **liquidez_minima_usd**: sube este número para evitar tokens con poca
  liquidez (más riesgo de rug pull o slippage alto al vender)
- **edad_maxima_horas**: bájalo si solo quieres tokens recién lanzados
  (más riesgo, pero más "temprano" si funciona)
- **market_cap_maximo_usd**: si lo bajas, el bot solo te avisa de tokens
  muy chicos (más potencial de subida %, pero también más riesgo)

No hay una combinación "correcta" — depende de cuánto riesgo quieras asumir.

## Paso 6: Correrlo

```bash
python memebot.py
```

El bot va a quedarse corriendo y revisando cada 60 segundos. Para dejarlo
corriendo 24/7 sin tener tu computadora prendida todo el tiempo, más
adelante puedes moverlo a un servidor barato (ej. una VPS de $5/mes) o
un servicio como Railway o Render.

## Qué NO hace este bot (por ahora)

- No compra ni vende nada automáticamente — solo avisa
- No verifica si el contrato del token tiene funciones maliciosas
  (honeypots, impuestos ocultos de venta, etc.) — eso requeriría análisis
  adicional del bytecode del contrato
- No te dice "compra esto" — te da datos para que TÚ decidas

## Próximos pasos posibles (si quieres mejorarlo después)

- Agregar un check de "holders top 10" para evitar tokens muy concentrados
- Conectar con una API que detecte honeypots automáticamente
- Guardar el historial en una base de datos en vez de un archivo JSON
- Agregar botones interactivos en Telegram (ej. "marcar como interesante")
