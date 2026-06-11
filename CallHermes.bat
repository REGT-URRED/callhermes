@echo off
title CallHermes
color 0B

echo ============================================
echo          CallHermes - Inicio Rapido
echo ============================================
echo.

:: ─── Ruta del proyecto en WSL ───────────────────────────
set WSL_PROJECT=/mnt/d/PROCESO/callhermes
set WSL_VENV=/home/toor/.venvs/callhermes

:: ─── Flags ──────────────────────────────────────────────
set WITH_TUNNEL=false
set SHOW_HELP=false

:: ─── Parsear argumentos ─────────────────────────────────
:parse_args
if not "%1"=="" (
    if /I "%1"=="--tunnel" set WITH_TUNNEL=true
    if /I "%1"=="-t" set WITH_TUNNEL=true
    if /I "%1"=="--help" set SHOW_HELP=true
    if /I "%1"== "-h" set SHOW_HELP=true
    shift
    goto parse_args
)

if "%SHOW_HELP%"=="true" (
    echo Uso: %~nx0 [opciones]
    echo.
    echo Opciones:
    echo   --tunnel, -t   Iniciar tunel Cloudflare para acceso remoto
    echo   --help, -h     Mostrar esta ayuda
    echo.
    pause
    exit /b 0
)

:: ─── Verificar WSL ──────────────────────────────────────
echo [1/5] Verificando WSL...
wsl echo "OK" >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: WSL no esta disponible. Asegurate de tener WSL2 instalado.
    pause
    exit /b 1
)
echo   OK

:: ─── Verificar Hermes Gateway ───────────────────────────
echo [2/5] Verificando Hermes Gateway...
wsl -d Ubuntu bash -c "systemctl --user is-active hermes-gateway.service" >nul 2>&1
if %errorlevel% neq 0 (
    echo   Iniciando gateway...
    wsl -d Ubuntu bash -c "systemctl --user start hermes-gateway.service"
    timeout /t 3 /nobreak >nul
)
wsl -d Ubuntu bash -c "curl -s -o /dev/null -w '%%{http_code}' http://localhost:8642/v1/models -H 'Authorization: Bearer hermes-call-voice-key'" >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Hermes API no responde en :8642
    pause
    exit /b 1
)
echo   OK

:: ─── Limpiar procesos anteriores ────────────────────────
echo [3/5] Limpiando procesos anteriores...

:: Matar servidores CallHermes previos (puerto 3000)
wsl -d Ubuntu bash -c "kill \$(lsof -ti :3000 2>/dev/null) 2>/dev/null; echo 'done'"

:: Matar túneles cloudflared previos
wsl -d Ubuntu bash -c "kill \$(lsof -ti :20241 2>/dev/null) 2>/dev/null; echo 'done'"

:: Matar procesos python server.py en WSL (CallHermes)
wsl -d Ubuntu bash -c "pkill -f 'python.*server.py' 2>/dev/null; echo 'done'"

:: Esperar a que se liberen los puertos
timeout /t 2 /nobreak >nul
echo   OK

:: ─── Iniciar CallHermes Server ──────────────────────────
echo [4/5] Iniciando CallHermes Server...

:: Iniciar en una nueva ventana de WSL (minimizada)
start /min "" wsl -d Ubuntu bash -c "cd %WSL_PROJECT% && source %WSL_VENV%/bin/activate && python -u server.py"

:: Esperar a que levante
timeout /t 5 /nobreak >nul

:: Verificar que responda
wsl -d Ubuntu bash -c "curl -s -o /dev/null -w '%%{http_code}' http://localhost:3000/api/health" >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: CallHermes no responde en :3000
    pause
    exit /b 1
)
echo   OK - http://localhost:3000

:: ─── Iniciar túnel (opcional) ───────────────────────────
if "%WITH_TUNNEL%"=="true" (
    echo [5/5] Iniciando tunel Cloudflare...
    start /min "" wsl -d Ubuntu bash -c "/tmp/cloudflared tunnel --url http://localhost:3000"
    echo   Tunel solicitado - revisa la terminal WSL para la URL
) else (
    echo [5/5] Tunel omitido (usa --tunnel para activarlo)
)

:: ─── Resumen ────────────────────────────────────────────
echo.
echo ============================================
echo   CallHermes iniciado correctamente
echo   Local:  http://localhost:3000
echo   Cerrar: cierra la ventana o presiona
echo           cualquier tecla abajo
echo ============================================

:: ─── Abrir navegador ────────────────────────────────────
start http://localhost:3000

echo.
echo Presiona cualquier tecla para cerrar CallHermes...
echo (el servidor seguira corriendo en segundo plano)
pause >nul

:: ─── Al cerrar ──────────────────────────────────────────
echo.
echo Cerrando CallHermes...
wsl -d Ubuntu bash -c "kill \$(lsof -ti :3000 2>/dev/null) 2>/dev/null; kill \$(lsof -ti :20241 2>/dev/null) 2>/dev/null; pkill -f 'python.*server.py' 2>/dev/null; echo 'done'"
echo OK
timeout /t 2 /nobreak >nul
