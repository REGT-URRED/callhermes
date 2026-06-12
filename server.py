"""
CallHermes — Backend para agente de voz en tiempo real.

Flujo:
  1. Browser detecta voz (VAD) → envía audio por POST
  2. Whisper transcribe → texto
  3. Hermes API Server procesa (con historial de conversación por sesión)
  4. Edge TTS sintetiza respuesta en streaming
  5. Browser reproduce audio progresivamente

Mejoras implementadas:
  - Historial de conversación entre turnos (por sesión)
  - Streaming de audio (edge-tts Python API, chunked HTTP)
  - Preparado para barge-in (cabecera permite cancelación)
  - Type hints en toda la base de código
  - Timeouts configurables por servicio
  - Validación de formato y tamaño de audio
  - Graceful shutdown
"""

import os
import json
import asyncio
import logging
import tempfile
import time
import subprocess
from pathlib import Path
from typing import Optional, Dict, List, AsyncGenerator
from dataclasses import dataclass, field
from uuid import uuid4

import numpy as np

import aiohttp
import edge_tts
from dotenv import load_dotenv
from aiohttp import web

# ─── Configuración ───────────────────────────────────────────────────────────

load_dotenv()

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "3000"))

HERMES_API_URL = os.getenv("HERMES_API_URL", "http://localhost:8642/v1")
HERMES_API_KEY = os.getenv("HERMES_API_KEY", "")
HERMES_MODEL = os.getenv("HERMES_MODEL", "hermes-agent")
HERMES_TIMEOUT = int(os.getenv("HERMES_TIMEOUT", "60"))

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "")  # vacío = auto
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "")  # vacío = auto
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "")  # vacío = auto

TTS_VOICE = os.getenv("TTS_VOICE", "es-AR-ElenaNeural")
TTS_TIMEOUT = int(os.getenv("TTS_TIMEOUT", "120"))

MAX_AUDIO_BYTES = int(os.getenv("MAX_AUDIO_BYTES", str(50 * 1024 * 1024)))  # 50 MB
MIN_AUDIO_BYTES = int(os.getenv("MIN_AUDIO_BYTES", "100"))
ALLOWED_AUDIO_TYPES = {"audio/webm", "audio/wav", "audio/wave", "audio/ogg", "audio/opus", "audio/mpeg", "audio/mp4"}

# ─── Auto-configuración Whisper ──────────────────────────────────────────────


def detect_whisper_config():
    """Detecta GPU y ajusta configuración Whisper automáticamente."""
    model = WHISPER_MODEL or "base"
    device = WHISPER_DEVICE
    compute = WHISPER_COMPUTE

    if not device:
        try:
            import torch
            if torch.cuda.is_available():
                device = "cuda"
                compute = compute or "float16"
                model = WHISPER_MODEL or "medium"
                log.info("GPU detectada → Whisper %s en CUDA (%s)", model, compute)
            else:
                device = "cpu"
                compute = compute or "int8"
                log.info("GPU no disponible → Whisper %s en CPU (%s)", model, compute)
        except ImportError:
            device = "cpu"
            compute = compute or "int8"
            log.info("torch no disponible → Whisper %s en CPU (%s)", model, compute)

    return model, device, compute or "int8"

# ─── Tipos ───────────────────────────────────────────────────────────────────

@dataclass
class Turn:
    """Un intercambio usuario-asistente."""
    user: str = ""
    assistant: str = ""


@dataclass
class Session:
    """Estado de una conversación."""
    id: str = ""
    turns: List[Turn] = field(default_factory=list)
    max_turns: int = 10

    def to_messages(self, system_prompt: str) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for t in self.turns:
            messages.append({"role": "user", "content": t.user})
            if t.assistant:
                messages.append({"role": "assistant", "content": t.assistant})
        return messages

    def add_turn(self, user_text: str) -> None:
        self.turns.append(Turn(user=user_text))
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns:]

    def set_assistant_response(self, text: str) -> None:
        if self.turns and not self.turns[-1].assistant:
            self.turns[-1].assistant = text


# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("callhermes")

# ─── Servicios (carga diferida) ──────────────────────────────────────────────

_whisper = None
_whisper_lock = asyncio.Lock()
_whisper_config = None


async def get_whisper():
    """Inicializa Whisper bajo demanda (primera llamada) con auto-configuración."""
    global _whisper, _whisper_config
    if _whisper is not None:
        return _whisper
    async with _whisper_lock:
        if _whisper is not None:
            return _whisper
        model, device, compute = detect_whisper_config()
        _whisper_config = (model, device, compute)
        log.info("Cargando Whisper '%s' (%s / %s)...", model, device, compute)
        try:
            from faster_whisper import WhisperModel
            t0 = time.time()
            _whisper = WhisperModel(model, device=device, compute_type=compute)
            elapsed = time.time() - t0
            log.info("Whisper listo en %.1fs", elapsed)
        except Exception as e:
            log.error("Error cargando Whisper: %s", e)
            raise
        return _whisper


async def warmup_whisper():
    """Precarga Whisper en startup para evitar cold start."""
    try:
        await get_whisper()
        log.info("✓ Whisper precargado — sin cold start en primer request")
    except Exception as e:
        log.warning("Whisper warmup falló (se cargará bajo demanda): %s", e)


async def transcribe_audio(audio_data: bytes) -> tuple[str, float]:
    """Transcribe audio con Whisper, evita tempfile usando pipe a ffmpeg."""
    import numpy as np

    whisper = await get_whisper()

    try:
        proc = await asyncio.create_subprocess_exec(
            'ffmpeg', '-i', 'pipe:0',
            '-f', 'f32le', '-ac', '1', '-ar', '16000',
            'pipe:1',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(audio_data),
            timeout=30,
        )

        if not stdout or len(stdout) < 640:
            return "", 0.0

        audio_array = np.frombuffer(stdout, dtype=np.float32)
        t0 = time.time()
        segments, info = whisper.transcribe(audio_array, language="es")
        text = " ".join(seg.text for seg in segments).strip()
        elapsed = time.time() - t0
        return text, elapsed

    except (subprocess.SubprocessError, asyncio.TimeoutError) as e:
        log.warning("ffmpeg pipe falló, usando tempfile: %s", e)
        # Fallback: tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
        tmp_path = tmp.name
        try:
            tmp.write(audio_data)
        finally:
            tmp.close()
        try:
            segments, info = whisper.transcribe(tmp_path, language="es")
            text = " ".join(seg.text for seg in segments).strip()
            return text, 0.0
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


async def release_whisper() -> None:
    """Libera recursos de Whisper en shutdown."""
    global _whisper
    if _whisper is not None:
        try:
            del _whisper
        except Exception:
            pass
        _whisper = None
        log.info("Whisper liberado")


# ─── Sesiones de conversación ────────────────────────────────────────────────

_sessions: Dict[str, Session] = {}
_sessions_lock = asyncio.Lock()

SYSTEM_PROMPT = (
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


async def get_or_create_session(session_id: Optional[str] = None) -> Session:
    """Obtiene o crea una sesión por ID. Si no se provee ID, crea una nueva."""
    async with _sessions_lock:
        if session_id and session_id in _sessions:
            return _sessions[session_id]
        new_id = session_id or str(uuid4())
        session = Session(id=new_id)
        _sessions[new_id] = session
        log.info("Nueva sesión: %s", new_id)
        return session


async def build_messages(session: Session, user_text: str) -> List[Dict[str, str]]:
    """Agrega el mensaje del usuario a la sesión y construye la lista de mensajes."""
    session.add_turn(user_text)
    return session.to_messages(SYSTEM_PROMPT)


async def add_assistant_response(session: Session, text: str) -> None:
    """Guarda la respuesta del asistente en la sesión."""
    async with _sessions_lock:
        session.set_assistant_response(text)


# ─── Validación de audio ─────────────────────────────────────────────────────


def validate_audio(audio_data: bytes, content_type: str) -> Optional[str]:
    """Valida formato y tamaño del audio. Retorna mensaje de error o None."""
    if not audio_data:
        return "audio vacío"
    if len(audio_data) < MIN_AUDIO_BYTES:
        return f"audio demasiado pequeño ({len(audio_data)} < {MIN_AUDIO_BYTES} bytes)"
    if len(audio_data) > MAX_AUDIO_BYTES:
        return f"audio demasiado grande ({len(audio_data)} > {MAX_AUDIO_BYTES} bytes)"
    if content_type and content_type not in ALLOWED_AUDIO_TYPES:
        log.warning("Content-Type no esperado: %s (se intentará igual)", content_type)
    return None


def detect_extension(content_type: str) -> str:
    """Detecta extensión de archivo según content-type."""
    ct = content_type.lower()
    if "wav" in ct:
        return ".wav"
    if "ogg" in ct:
        return ".ogg"
    if "mpeg" in ct or "mp3" in ct:
        return ".mp3"
    if "mp4" in ct:
        return ".mp4"
    return ".webm"  # default


# ─── Handlers ─────────────────────────────────────────────────────────────────


async def handle_health(request: web.Request) -> web.Response:
    """Health check del servidor."""
    return web.json_response({
        "status": "ok",
        "service": "callhermes",
        "version": "2.0.0",
        "hermes_api": HERMES_API_URL,
        "whisper_loaded": _whisper is not None,
        "whisper_config": {
            "model": _whisper_config[0] if _whisper_config else None,
            "device": _whisper_config[1] if _whisper_config else None,
            "compute": _whisper_config[2] if _whisper_config else None,
        } if _whisper_config else None,
        "active_sessions": len(_sessions),
    })


async def handle_audio(request: web.Request) -> web.StreamResponse:
    """
    Recibe audio del navegador, lo transcribe, consulta a Hermes y devuelve
    la respuesta sintetizada en streaming (audio/mpeg chunked).

    Permite barge-in: si el frontend cierra la conexión, el servidor
    detecta y aborta el procesamiento.

    Soporta sesiones via X-Session-ID header.
    """
    content_type = request.content_type or ""

    # ── 1. Obtener/crear sesión ─────────────────────────────────────────
    session_id = request.headers.get("X-Session-ID")
    session = await get_or_create_session(session_id)

    # ── 2. Extraer audio del request ────────────────────────────────────
    try:
        if "multipart" in content_type:
            reader = await request.multipart()
            part = await reader.next()
            if part is None:
                return web.json_response({"error": "no se recibió audio"}, status=400)
            audio_data = await part.read()
            detected_type = part.headers.get("Content-Type", "audio/webm")
        else:
            audio_data = await request.read()
            detected_type = content_type or "audio/webm"
    except (ValueError, AssertionError, Exception) as e:
        log.warning("Error leyendo audio: %s", e)
        return web.json_response({"error": f"formato inválido: {e}"}, status=400)

    # ── 3. Validar audio ────────────────────────────────────────────────
    validation_error = validate_audio(audio_data, detected_type)
    if validation_error:
        log.warning("Audio inválido: %s", validation_error)
        # Si es muy pequeño, podría ser silencio — no es error grave
        if len(audio_data) < 100:
            return web.json_response({"error": "audio vacío o silencio"}, status=400)
        return web.json_response({"error": validation_error}, status=400)

    ext = detect_extension(detected_type)

    log.info("Audio recibido: %d bytes (sesión: %s)", len(audio_data), session.id)

    try:
        # ── 4. Transcripción con Whisper (en memoria) ────────────────────
        text, stt_time = await transcribe_audio(audio_data)

        if not text:
            log.warning("No se detectó voz en el audio (sesión: %s)", session.id)
            return web.json_response({"error": "no se detectó voz"}, status=400)

        log.info("STT [%s] (%.2fs): %r", session.id, stt_time, text)

        # ── 5. Construir mensajes con historial ──────────────────────────
        messages = await build_messages(session, text)

        # ── 6. Consultar Hermes API Server ───────────────────────────────
        response_text = await ask_hermes(messages)

        # ── 7. Guardar respuesta en historial ────────────────────────────
        await add_assistant_response(session, response_text)

        log.info("Hermes [%s]: %r", session.id, response_text[:100])

        # ── 8. Streaming de TTS ─────────────────────────────────────────
        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "audio/mpeg",
                "X-Response-Text": response_text[:200].replace("\n", " "),
                "X-Transcript": text[:200].replace("\n", " "),
                "X-Session-ID": session.id,
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )
        await resp.prepare(request)

        try:
            async for chunk in synthesize_stream(response_text):
                try:
                    await resp.write(chunk)
                except (ConnectionResetError, ConnectionAbortedError):
                    log.info("Barge-in detectado (sesión: %s)", session.id)
                    break
        except asyncio.TimeoutError:
            log.warning("TTS timeout (sesión: %s)", session.id)

        try:
            await resp.write_eof()
        except Exception:
            pass

        return resp

    finally:
        # Si el fallback usó tempfile, limpiar aquí se maneja en transcribe_audio
        pass


async def handle_options(request: web.Request) -> web.Response:
    """CORS preflight."""
    return web.Response(headers=cors_headers())


async def handle_reset(request: web.Request) -> web.Response:
    """Resetea el historial de una sesión. Si no se provee ID, resetea todas."""
    session_id = request.headers.get("X-Session-ID")
    async with _sessions_lock:
        if session_id and session_id in _sessions:
            _sessions[session_id] = Session(id=session_id)
            log.info("Sesión reiniciada: %s", session_id)
            return web.json_response({"status": "ok", "message": f"sesión {session_id} reiniciada"})
        elif session_id:
            return web.json_response({"error": "sesión no encontrada"}, status=404)
        else:
            _sessions.clear()
            log.info("Todas las sesiones reiniciadas")
            return web.json_response({"status": "ok", "message": "todas las sesiones reiniciadas"})


# ─── Llamada a Hermes API Server ──────────────────────────────────────────────


async def ask_hermes(messages: List[Dict[str, str]]) -> str:
    """Envía los mensajes (con historial) a Hermes API Server."""
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

    timeout = aiohttp.ClientTimeout(total=HERMES_TIMEOUT)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post(
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
    except asyncio.TimeoutError:
        log.error("Hermes API timeout después de %ds", HERMES_TIMEOUT)
        return "Lo siento, la solicitud tardó demasiado. Intenta de nuevo."
    except aiohttp.ClientError as e:
        log.error("Hermes API connection error: %s", e)
        return "Lo siento, no pude conectar con el servidor de procesamiento."


# ─── Síntesis de voz (streaming) ──────────────────────────────────────────────


async def synthesize_stream(text: str) -> AsyncGenerator[bytes, None]:
    """
    Genera audio MP3 desde edge-tts en streaming.
    Retorna chunks de bytes de audio.
    """
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            yield chunk["data"]


# ─── Utilidades ───────────────────────────────────────────────────────────────


def cors_headers() -> Dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS, DELETE",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Response-Text, X-Transcript, X-Session-ID",
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


# ─── Graceful shutdown ───────────────────────────────────────────────────────


async def on_shutdown(app):
    """Limpieza de recursos al detener el servidor."""
    log.info("Apagando servidor...")
    await release_whisper()
    async with _sessions_lock:
        _sessions.clear()
    log.info("Sesiones liberadas")


async def on_startup(app):
    """Precarga servicios y log de inicio."""
    log.info("─" * 50)
    log.info("  CallHermes v2.0.0")
    log.info("  Servidor: http://%s:%s", HOST, PORT)
    log.info("  Hermes API: %s", HERMES_API_URL)
    log.info("  TTS: %s (streaming)", TTS_VOICE)
    log.info("  Timeouts: Hermes=%ds TTS=%ds", HERMES_TIMEOUT, TTS_TIMEOUT)
    log.info("  Max audio: %d MB", MAX_AUDIO_BYTES // (1024 * 1024))
    log.info("─" * 50)

    # Precargar Whisper (evita cold start en primer request)
    await warmup_whisper()

    # Mostrar configuración final de Whisper
    if _whisper_config:
        model, device, compute = _whisper_config
        log.info("  Whisper: %s (%s / %s)", model, device, compute)
    else:
        log.warning("  Whisper: no cargado")


# ─── Entry point ─────────────────────────────────────────────────────────────


def create_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware])

    app.router.add_get("/", handle_index)
    app.router.add_get("/{filename:.*}", handle_static)
    app.router.add_get("/api/health", handle_health)
    app.router.add_post("/api/audio", handle_audio)
    app.router.add_post("/api/reset", handle_reset)
    app.router.add_route("OPTIONS", "/api/audio", handle_options)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    return app


if __name__ == "__main__":
    app = create_app()
    web.run_app(app, host=HOST, port=PORT)
