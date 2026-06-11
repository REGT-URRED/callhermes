# CallHermes 🎙️🤖

**Agente de voz en tiempo real — 100% local, sin APIs pagadas.**

Habla por micrófono, CallHermes te escucha, procesa tu solicitud con herramientas reales en tu computadora, y te responde por voz. Como una llamada telefónica con un asistente AI que puede ejecutar comandos, buscar archivos, controlar el navegador y más.

---

## ✨ Características

- 🎤 **Voz a voz** — Habla, el agente escucha, procesa y responde. Sin texto intermedio.
- 🧠 **Silero VAD** — Detección inteligente de voz basada en modelo ML (no RMS simple).
- ⚡ **Barge-in** — Interrumpe a Hermes mientras habla para hacer una nueva pregunta.
- 📜 **Historial de conversación** — Contexto entre turnos (10 intercambios máx).
- 🚀 **Streaming de audio** — Respuesta de voz progresiva, sin esperar a que termine el TTS.
- 🏠 **100% local** — Whisper (STT), Edge TTS (voz), todo corre en tu máquina.
- 🆓 **Sin APIs pagadas** — Sin OpenAI, sin ElevenLabs, sin Groq.
- 🛠️ **Hermes Agent** — Procesamiento con herramientas reales: archivos, terminal, navegador, etc.
- 🌐 **Interfaz web** — Diseño oscuro minimalista, VAD automático.
- 🐧 **WSL + Windows** — Funciona en Windows via WSL.

---

## 🧱 Arquitectura

```
┌─────────────────────────────────────────────────────────┐
│                    WINDOWS HOST (WSL)                      │
│                                                           │
│  ┌──────────────┐    HTTP POST     ┌──────────────────┐  │
│  │   NAVEGADOR   │◄── (streaming) ─►│   CallHermes       │  │
│  │  (Web App)    │   audio chunks   │   Server (Python)  │  │
│  │               │                 │                     │  │
│  │ • Micrófono   │                 │ • Silero VAD (JS)   │  │
│  │ • Altavoz     │                 │ • Whisper (STT)     │  │
│  │ • VAD ML      │                 │ • Edge TTS stream   │  │
│  │ • Barge-in    │                 └─────────┬───────────┘  │
│  └──────────────┘                           │ HTTP          │
│                                    ┌────────▼───────────┐  │
│                                    │  Hermes Gateway     │  │
│                                    │  API Server (:8642) │  │
│                                    │  • Tools reales     │  │
│                                    │  • Historial ctx    │  │
│                                    └─────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### Componentes

| Componente | Tecnología | Rol |
|---|---|---|
| **Frontend** | HTML + JS + MediaSource | Captura micrófono, VAD, reproduce audio streaming |
| **VAD** | Silero VAD (ML via ONNX) | Detecta cuándo el usuario habla y cuándo calla |
| **Backend** | aiohttp (Python) | Orquesta STT → LLM → TTS en streaming |
| **STT** | faster-whisper (local) | Voz → texto |
| **LLM + Tools** | Hermes Agent (API Server) | Procesa orden + ejecuta herramientas |
| **TTS** | edge-tts (gratuito) | Texto → voz en streaming |

---

## 📋 Requisitos

- **Sistema**: Windows con WSL2 (Ubuntu 22.04+)
- **Python**: 3.11+
- **Hermes Agent**: [Instalación](https://hermes-agent.nousresearch.com/docs)

---

## 🚀 Instalación

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

# 5. ¡Listo!
./start.sh          # Servidor + túnel público
# o solo local:
./start.sh --no-tunnel
```

Abrir [http://localhost:3000](http://localhost:3000) en el navegador.

---

## ⚙️ Configuración

Variables de entorno (`.env`):

| Variable | Por defecto | Descripción |
|---|---|---|
| `HERMES_API_URL` | `http://localhost:8642/v1` | URL del Hermes API Server |
| `HERMES_API_KEY` | — | API key para Hermes |
| `HERMES_MODEL` | `hermes-agent` | Modelo a usar |
| `HOST` | `0.0.0.0` | Interfaz del servidor web |
| `PORT` | `3000` | Puerto del servidor web |
| `WHISPER_MODEL` | `base` | Modelo Whisper (`base`, `small`, `medium`) |
| `TTS_VOICE` | `es-AR-ElenaNeural` | Voz de Edge TTS |

---

## 🎯 Uso

1. Asegúrate de que **Hermes Gateway** esté corriendo:
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

4. **Habla naturalmente** — Silero VAD detecta tu voz automáticamente.
   Si hablas mientras Hermes responde, interrumpe (barge-in).
   El historial se mantiene entre turnos — puedes hacer preguntas de seguimiento.

### Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/` | Frontend web |
| GET | `/api/health` | Health check |
| POST | `/api/audio` | Audio → Whisper → Hermes → TTS (streaming) |
| POST | `/api/reset` | Reiniciar historial de conversación |

---

## 🌐 Acceso remoto

```bash
# Opción A: Cloudflare Tunnel (recomendado)
cloudflared tunnel --url http://localhost:3000

# Opción B: Serveo (solo SSH, sin instalación)
ssh -R 80:localhost:3000 serveo.net

# Opción C: Ngrok
npx ngrok http 3000
```

---

## 🗺️ Roadmap

- [x] Backend REST API funcional
- [x] STT local con Whisper
- [x] TTS con Edge
- [x] Integración con Hermes Agent
- [x] Frontend web tipo llamada
- [x] Silero VAD (detección de voz por ML)
- [x] Barge-in (interrupción mientras responde)
- [x] Historial de conversación
- [x] Streaming de audio (MediaSource + chunked HTTP)
- [x] Túnel Cloudflare para acceso remoto
- [ ] WebRTC real con Pipecat
- [ ] Wake word ("Oye Hermes")
- [ ] Speaker diarization

---

## 📄 Licencia

MIT — Usa, modifica, comparte.
