"""
CallHermes — Backend para agente de voz en tiempo real.

Flujo:
  1. Browser detecta voz (VAD) → envía audio por POST
  2. Whisper transcribe → texto
  3. Hermes API Server procesa (con historial de conversación)
  4. Edge TTS sintetiza respuesta en streaming
  5. Browser reproduce audio progresivamente

Mejoras implementadas:
  - Historial de conversación entre turnos
  - Streaming de audio (edge-tts Python API, chunked HTTP)
  - Preparado para barge-in (cabecera permite cancelación)
"""

import os
import json
import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from aiohttp import web

# ─── Configuración ───────────────────────────────────────────────────────────

load_dotenv()

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "3000"))

HERMES_API_URL = os.getenv("HERMES_API_URL", "http://localhost:8642/v1")
HERMES_API_KEY = os.getenv("HERMES_API_KEY", "")
HERMES_MODEL = os.getenv("HERMES_MODEL", "hermes-agent")

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "int8")

TTS_VOICE = os.getenv("TTS_VOICE", "es-AR-ElenaNeural")

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("callhermes")

# ─── Servicios (carga diferida) ──────────────────────────────────────────────

_whisper = None
_whisper_lock = asyncio.Lock()


async def get_whisper():
    """Inicializa Whisper bajo demanda (primera llamada)."""
    global _whisper
    if _whisper is not None:
        return _whisper
    async with _whisper_lock:
        if _whisper is not None:
            return _whisper
        log.info("Cargando Whisper modelo '%s' (dispositivo: %s)...", WHISPER_MODEL, WHISPER_DEVICE)
        from faster_whisper import WhisperModel
        _whisper = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE,
        )
        log.info("Whisper listo")
        return _whisper


# ─── Historial de conversación ────────────────────────────────────────────────

MAX_HISTORY_TURNS = 10  # 10 intercambios usuario-asistente = 20 mensajes

conversation_history: list[dict] = []
history_lock = asyncio.Lock()


async def get_system_prompt() -> str:
    return (
        "Eres Hermes, un asistente de voz en español. "
        "Responde de forma clara, concisa y natural como en una conversación hablada. "
        "Si ejecutas herramientas en la computadora, informa brevemente qué hiciste.\n\n"
        "REGLAS DE CONVERSACIÓN:\n"
        "1. Escucha activa: cuando el usuario termine de hablar, "
        "pregunta '¿Algo más?' o '¿Es todo?' antes de finalizar.\n"
        "2. No ejecutes ninguna acción hasta que el usuario diga 'procede'.\n"
        "3. Cuando el usuario diga 'procede', entonces sí ejecuta todo lo solicitado.\n"
        "4. Tus respuestas deben ser naturales y conversacionales.\n"
        "5. Si no entiendes algo, pide aclaración.\n"
        "6. Mantén el contexto de la conversación.\n"
        "7. Sé proactivo pero no ejecutes sin autorización ('procede')."
    )


async def build_messages(user_text: str) -> list[dict]:
    """Construye la lista de mensajes con historial."""
    global conversation_history

    # Agregar mensaje del usuario actual
    conversation_history.append({"role": "user", "content": user_text})

    # Limitar historial
    if len(conversation_history) > MAX_HISTORY_TURNS * 2:
        conversation_history = conversation_history[-(MAX_HISTORY_TURNS * 2):]

    messages = [
        {"role": "system", "content": await get_system_prompt()},
        *conversation_history,
    ]
    return messages


async def add_assistant_response(text: str):
    """Guarda la respuesta del asistente en el historial."""
    global conversation_history
    if conversation_history and conversation_history[-1]["role"] == "assistant":
        return
    conversation_history.append({"role": "assistant", "content": text})
    if len(conversation_history) > MAX_HISTORY_TURNS * 2:
        conversation_history = conversation_history[-(MAX_HISTORY_TURNS * 2):]


# ─── Handlers ─────────────────────────────────────────────────────────────────


async def handle_health(request: web.Request) -> web.Response:
    """Health check del servidor."""
    return web.json_response({
        "status": "ok",
        "service": "callhermes",
        "version": "2.0.0",
        "hermes_api": HERMES_API_URL,
    })


async def handle_audio(request: web.Request) -> web.StreamResponse:
    """
    Recibe audio del navegador, lo transcribe, consulta a Hermes y devuelve
    la respuesta sintetizada en streaming (audio/mpeg chunked).

    Permite barge-in: si el frontend cierra la conexión, el servidor
    detecta y aborta el procesamiento.
    """
    content_type = request.content_type or ""

    # ── 1. Extraer audio del request ───────────────────────────────────
    if "multipart" in content_type:
        try:
            reader = await request.multipart()
            part = await reader.next()
            if part is None:
                return web.json_response({"error": "no se recibió audio"}, status=400)
            audio_data = await part.read()
        except (ValueError, AssertionError, Exception) as e:
            log.warning("Error leyendo multipart: %s", e)
            return web.json_response({"error": f"formato multipart inválido: {e}"}, status=400)
    else:
        audio_data = await request.read()

    if not audio_data or len(audio_data) < 100:
        return web.json_response({"error": "audio demasiado pequeño o vacío"}, status=400)

    # Detectar extensión según content-type
    if "multipart" in content_type and part is not None:
        detected_type = part.headers.get("Content-Type", "audio/webm")
    else:
        detected_type = content_type or "audio/webm"

    ext = ".webm"
    if "wav" in detected_type:
        ext = ".wav"
    elif "ogg" in detected_type:
        ext = ".ogg"

    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    tmp.write(audio_data)
    tmp_path = tmp.name
    tmp.close()

    log.info("Audio recibido: %d bytes → %s", len(audio_data), tmp_path)

    try:
        # ── 2. Transcripción con Whisper ──────────────────────────────
        whisper = await get_whisper()
        segments, info = whisper.transcribe(tmp_path, language="es")
        text = " ".join(seg.text for seg in segments).strip()

        if not text:
            log.warning("No se detectó voz en el audio")
            return web.json_response({"error": "no se detectó voz"}, status=400)

        log.info("STT: %r", text)

        # ── 3. Consultar Hermes API Server (con historial) ────────────
        async with history_lock:
            messages = await build_messages(text)

        # Hacer la llamada a Hermes
        response_text = await ask_hermes(messages)

        # Guardar respuesta en historial
        async with history_lock:
            await add_assistant_response(response_text)

        log.info("Hermes: %r", response_text[:100])

        # ── 4. Streaming de TTS ───────────────────────────────────────
        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "audio/mpeg",
                "X-Response-Text": response_text[:200].replace("\n", " "),
                "X-Transcript": text[:200].replace("\n", " "),
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )
        await resp.prepare(request)

        # Enviar chunks de audio en streaming
        async for chunk in synthesize_stream(response_text):
            try:
                await resp.write(chunk)
            except (ConnectionResetError, ConnectionAbortedError, Exception):
                # Cliente desconectado (barge-in) — dejar de enviar
                log.info("Cliente desconectado durante streaming (posible barge-in)")
                break

        try:
            await resp.write_eof()
        except Exception:
            pass

        return resp

    finally:
        # Limpiar archivos temporales
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def handle_options(request: web.Request) -> web.Response:
    """CORS preflight."""
    return web.Response(headers=cors_headers())


# ─── Llamada a Hermes API Server ──────────────────────────────────────────────


async def ask_hermes(messages: list[dict]) -> str:
    """Envía los mensajes (con historial) a Hermes API Server."""
    import aiohttp

    headers = {
        "Authorization": f"Bearer {HERMES_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": HERMES_MODEL,
        "messages": messages,
        "max_tokens": 512,
        "temperature": 0.7,
        "stream": False,
    }

    timeout = aiohttp.ClientTimeout(total=60)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{HERMES_API_URL}/chat/completions",
            json=payload,
            headers=headers,
        ) as resp:
            if resp.status != 200:
                error_body = await resp.text()
                log.error("Hermes API error %d: %s", resp.status, error_body)
                return f"Lo siento, hubo un error al procesar tu solicitud (código {resp.status})."

            data = await resp.json()
            return data["choices"][0]["message"]["content"]


# ─── Síntesis de voz (streaming) ──────────────────────────────────────────────


async def synthesize_stream(text: str):
    """
    Genera audio MP3 desde edge-tts en streaming.
    Retorna chunks de bytes de audio.
    """
    import edge_tts

    communicate = edge_tts.Communicate(text, TTS_VOICE)
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            yield chunk["data"]


# ─── Utilidades ───────────────────────────────────────────────────────────────


def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Response-Text, X-Transcript",
    }


# ─── Inicialización de la app ────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
PUBLIC_DIR = BASE_DIR / "public"


async def handle_static(request: web.Request) -> web.Response:
    """Sirve archivos estáticos desde public/."""
    filename = request.match_info.get("filename", "index.html")
    filepath = PUBLIC_DIR / filename

    if not filepath.exists() or not filepath.is_file():
        filepath = PUBLIC_DIR / "index.html"

    if not filepath.exists():
        return web.json_response({"error": "not found"}, status=404)

    content_type_map = {
        ".html": "text/html",
        ".css": "text/css",
        ".js": "application/javascript",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
        ".wasm": "application/wasm",
    }
    ext = filepath.suffix.lower()
    ctype = content_type_map.get(ext, "application/octet-stream")

    return web.Response(
        body=filepath.read_bytes(),
        content_type=ctype,
        headers=cors_headers(),
    )


async def handle_index(request: web.Request) -> web.Response:
    return await handle_static(request)


# ─── Middleware CORS ──────────────────────────────────────────────────────────


@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        return web.Response(headers=cors_headers())
    response = await handler(request)
    for key, value in cors_headers().items():
        response.headers[key] = value
    return response


# ─── Endpoint para resetear historial ─────────────────────────────────────────


async def handle_reset(request: web.Request) -> web.Response:
    """Resetea el historial de conversación."""
    global conversation_history
    async with history_lock:
        conversation_history = []
    log.info("Historial de conversación reiniciado")
    return web.json_response({"status": "ok", "message": "historial reiniciado"})


# ─── Entry point ─────────────────────────────────────────────────────────────


def create_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware])

    app.router.add_get("/", handle_index)
    app.router.add_get("/{filename:.*}", handle_static)
    app.router.add_get("/api/health", handle_health)
    app.router.add_post("/api/audio", handle_audio)
    app.router.add_post("/api/reset", handle_reset)
    app.router.add_route("OPTIONS", "/api/audio", handle_options)

    # Loop startup
    app.on_startup.append(on_startup)

    return app


async def on_startup(app):
    log.info("─" * 50)
    log.info("  CallHermes v2.0.0")
    log.info("  Servidor: http://%s:%s", HOST, PORT)
    log.info("  Hermes API: %s", HERMES_API_URL)
    log.info("  Whisper: %s (%s)", WHISPER_MODEL, WHISPER_DEVICE)
    log.info("  TTS: %s (streaming)", TTS_VOICE)
    log.info("  Historial: %d turnos máx", MAX_HISTORY_TURNS)
    log.info("─" * 50)


if __name__ == "__main__":
    app = create_app()
    web.run_app(app, host=HOST, port=PORT)
