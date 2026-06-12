# CallHermes

**Agente de voz en tiempo real — 100% local, sin APIs pagadas, sin licencias.**

Habla por microfono, CallHermes te escucha, procesa tu solicitud con herramientas reales en tu computadora, y te responde por voz. Como una llamada telefonica con un asistente AI que puede ejecutar comandos, buscar archivos, controlar el navegador y mas.

---

## Caracteristicas

- **Voz a voz** — Habla, el agente escucha, procesa y responde. Sin texto intermedio.
- **Wake word "Yes"** — Di "Yes" para activarlo. Manos libres, 100% local con TensorFlow.js.
- **Silero VAD** — Deteccion inteligente de voz basada en modelo ML (no RMS simple).
- **Barge-in** — Interrumpe a Hermes mientras habla para hacer una nueva pregunta.
- **Historial de conversacion por sesion** — Contexto entre turnos, soporta multiples usuarios simultaneos.
- **Streaming de audio** — Respuesta de voz progresiva via MediaSource, sin esperar a que termine el TTS.
- **Auto-sleep** — Vuelve a modo reposo tras 30s de inactividad (configurable).
- **Auto-reconnect** — Reconexion automatica con exponential backoff si el servidor cae.
- **100% local** — Whisper (STT), Edge TTS (voz), TF.js (wake word), todo corre en tu maquina.
- **Sin APIs pagadas** — Sin OpenAI, sin ElevenLabs, sin Picovoice, sin Groq.
- **GPU auto-detect** — Usa CUDA si esta disponible (Whisper medium + float16).
- **Hermes Agent** — Procesamiento con herramientas reales: archivos, terminal, navegador, etc.
- **Interfaz web** — Diseno oscuro minimalista, VAD automatico, historial visible, panel de ajustes.
- **Atajos de teclado** — Espacio = PTT, Escape = cancelar/dormir.
- **WSL + Windows** — Funciona en Windows via WSL con auto-start opcional.

---

## Arquitectura

```
+-----------------------------------------------------------+
|                    WINDOWS HOST (WSL)                      |
|                                                           |
|  +--------------+    HTTP POST     +------------------+   |
|  |   NAVEGADOR   |<-- (streaming) -->|   CallHermes       |   |
|  |  (Web App)    |   audio chunks   |   Server (Python)  |   |
|  |               |                 |                     |   |
|  | * Microfono   |                 | * Whisper (STT)     |   |
|  | * Altavoz     |                 | * Edge TTS stream   |   |
|  | * Silero VAD  |                 | * GPU auto-detect   |   |
|  | * Wake word   |                 +---------+-----------+   |
|  |   (TF.js)     |                           | HTTP          |
|  | * Barge-in    |                 +----------+-----------+ |
|  | * Historial   |                 |  Hermes Gateway       | |
|  | * Settings    |                 |  API Server (:8642)   | |
|  +--------------+                  |  * Tools reales       | |
|                                    |  * Historial ctx      | |
|  +--------------+                  +-----------------------+ |
|  | PowerShell   |                                           |
|  | * Auto-start |  (Task Scheduler)                         |
|  | * Toast notif|                                           |
|  +--------------+                                           |
+-----------------------------------------------------------+
```

### Componentes

| Componente | Tecnologia | Rol |
|---|---|---|
| **Frontend** | HTML + JS + MediaSource | Captura microfono, VAD, wake word, reproduce audio streaming |
| **VAD** | Silero VAD (ML via ONNX) | Detecta cuando el usuario habla y cuando calla |
| **Wake word** | TF.js Speech Commands | Detecta "Yes" en el navegador, 100% local |
| **Backend** | aiohttp (Python) | Orquesta STT -> LLM -> TTS en streaming |
| **STT** | faster-whisper (local) | Voz -> texto, GPU auto-detect |
| **LLM + Tools** | Hermes Agent (API Server) | Procesa orden + ejecuta herramientas |
| **TTS** | edge-tts (gratuito) | Texto -> voz en streaming |
| **Auto-start** | PowerShell + Task Scheduler | Inicia automaticamente al iniciar sesion |

---

## Requisitos

- **Sistema**: Windows con WSL2 (Ubuntu 22.04+)
- **Python**: 3.11+
- **Hermes Agent**: [Instalacion](https://hermes-agent.nousresearch.com/docs)
- **Navegador**: Chrome, Edge o Brave (necesita Web Audio API + MediaSource)

---

## Instalacion

```bash
# 1. Clonar el repositorio
git clone https://github.com/REGT-URRED/callhermes.git
cd callhermes

# 2. Crear entorno virtual
python3.11 -m venv venv
source venv/bin/activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# Editar .env con tu HERMES_API_KEY

# 5. Iniciar
./start.sh              # Servidor + tunel publico
# o solo local:
./start.sh --no-tunnel
```

Abrir [http://localhost:3000](http://localhost:3000) en el navegador.

---

## Configuracion

Variables de entorno (`.env`). Dejar vacio = auto-detect:

| Variable | Por defecto | Descripcion |
|---|---|---|
| `HERMES_API_URL` | `http://localhost:8642/v1` | URL del Hermes API Server |
| `HERMES_API_KEY` | -- | API key para Hermes |
| `HERMES_MODEL` | `hermes-agent` | Modelo a usar |
| `HERMES_TIMEOUT` | `60` | Timeout para Hermes API (segundos) |
| `HOST` | `0.0.0.0` | Interfaz del servidor web |
| `PORT` | `3000` | Puerto del servidor web |
| `WHISPER_MODEL` | auto | `base`, `medium`, `small` o vacio para auto |
| `WHISPER_DEVICE` | auto | `cpu`, `cuda` o vacio para auto |
| `WHISPER_COMPUTE` | auto | `int8`, `float16` o vacio para auto |
| `TTS_VOICE` | `es-AR-ElenaNeural` | Voz de Edge TTS |
| `TTS_TIMEOUT` | `120` | Timeout para TTS (segundos) |
| `MAX_AUDIO_BYTES` | `52428800` | Maximo tamano de audio (50 MB) |
| `MIN_AUDIO_BYTES` | `100` | Minimo tamano de audio (bytes) |

---

## Uso

1. Asegurate de que **Hermes Gateway** este corriendo:
   ```bash
   systemctl --user status hermes-gateway.service
   ```

2. Inicia CallHermes:
   ```bash
   cd callhermes
   source venv/bin/activate
   python server.py
   ```

3. Abre [http://localhost:3000](http://localhost:3000)

4. **Habla naturalmente** — Silero VAD detecta tu voz automaticamente.
   Si hablas mientras Hermes responde, interrumpe (barge-in).
   El historial se mantiene entre turnos.

### Wake word

En modo reposo (orb tenue), di **"Yes"** para activar. Tras 30s de silencio vuelve a reposo automaticamente. Click en el orb o presiona Espacio para alternar manualmente.

### Atajos de teclado

| Tecla | Accion |
|---|---|
| `Espacio` | Hablar / dejar de hablar |
| `Escape` | Cancelar respuesta / dormir |

### Endpoints

| Metodo | Ruta | Descripcion |
|---|---|---|
| GET | `/` | Frontend web |
| GET | `/api/health` | Health check + config info |
| POST | `/api/audio` | Audio -> Whisper -> Hermes -> TTS (streaming) |
| POST | `/api/reset` | Reiniciar historial de conversacion |

---

## Auto-start en Windows

```powershell
# Instalar inicio automatico al iniciar sesion
powershell -ExecutionPolicy Bypass -File install-startup.ps1

# Desinstalar
powershell -ExecutionPolicy Bypass -File install-startup.ps1 -Uninstall
```

---

## Acceso remoto

```bash
# Opcion A: Cloudflare Tunnel (recomendado)
cloudflared tunnel --url http://localhost:3000

# Opcion B: Serveo (solo SSH, sin instalacion)
ssh -R 80:localhost:3000 serveo.net

# Opcion C: Ngrok
npx ngrok http 3000
```

---

## Roadmap

- [x] Backend REST API funcional
- [x] STT local con Whisper (GPU auto-detect)
- [x] TTS con Edge (streaming)
- [x] Integracion con Hermes Agent
- [x] Frontend web tipo llamada
- [x] Silero VAD (deteccion de voz por ML)
- [x] Barge-in (interrupcion mientras responde)
- [x] Historial de conversacion (multi-sesion)
- [x] Streaming de audio (MediaSource + chunked HTTP)
- [x] Tunel Cloudflare para acceso remoto
- [x] Wake word "Yes" (TF.js, 100% local, sin API key)
- [x] Auto-sleep + wake manual
- [x] Atajos de teclado
- [x] Panel de ajustes (VAD, timeout)
- [x] Historial visible en pantalla
- [x] Auto-reconnect con backoff
- [x] Auto-start en Windows (Task Scheduler)
- [x] Notificaciones Windows Toast
- [x] Graceful shutdown
- [x] Validacion de audio (formato, tamano)
- [x] Type hints + dataclasses
- [ ] WebRTC real con Pipecat
- [ ] Speaker diarization
- [ ] Tema claro/oscuro

---

## Licencia

MIT -- Usa, modifica, comparte.
