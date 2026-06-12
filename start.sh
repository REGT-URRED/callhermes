#!/bin/bash
# CallHermes — Inicio rápido con túnel público
# Uso: ./start.sh [--no-tunnel]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "╔══════════════════════════════════════════╗"
echo "║        CallHermes v2.0.0                 ║"
echo "╚══════════════════════════════════════════╝"

# Verificar gateway
if systemctl --user is-active hermes-gateway.service &>/dev/null; then
    echo "✓ Gateway activo"
else
    echo "! Iniciando gateway..."
    systemctl --user start hermes-gateway.service
    sleep 2
fi

# Verificar API
if curl -s -o /dev/null -w "" http://localhost:8642/v1/models -H "Authorization: Bearer hermes-call-voice-key"; then
    echo "✓ Hermes API responde"
else
    echo "✗ Hermes API no responde"
    exit 1
fi

# Activar venv
source venv/bin/activate 2>/dev/null || source ~/.venvs/callhermes/bin/activate

# Iniciar servidor
echo ""
echo "Iniciando servidor en :3000..."
python server.py &
SERVER_PID=$!

# Esperar a que levante
for i in $(seq 1 10); do
    if curl -s -o /dev/null http://localhost:3000/api/health 2>/dev/null; then
        echo "✓ Servidor listo en http://localhost:3000"
        # Notificación Windows
        powershell.exe -Command "New-BurntToastNotification -AppLogo '\\wsl.localhost\Ubuntu\mnt\d\PROCESO\callhermes\public\favicon.ico' -Text 'CallHermes', 'Servidor listo en localhost:3000'" 2>/dev/null || \
        powershell.exe -Command "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]::CreateToastNotifier('CallHermes').Show((New-Object Windows.Data.Xml.Dom.XmlDocument).LoadXml('<toast><visual><binding template=\"ToastText02\"><text id=\"1\">CallHermes</text><text id=\"2\">Servidor listo en localhost:3000</text></binding></visual></toast>'))" 2>/dev/null || true
        break
    fi
    sleep 1
done

# Túnel público (opcional)
if [ "$1" != "--no-tunnel" ]; then
    echo ""
    echo "Iniciando túnel Cloudflare..."
    CLOUDFLARED=$(which cloudflared 2>/dev/null || echo "/tmp/cloudflared")
    
    if [ -f "$CLOUDFLARED" ]; then
        "$CLOUDFLARED" tunnel --url http://localhost:3000 &
        TUNNEL_PID=$!
        
        # Esperar URL
        sleep 5
        echo ""
        echo "⚠  Túnel activo (cierra con Ctrl+C)"
        echo "   La URL aparece arriba (trycloudflare.com)"
    else
        echo "✗ cloudflared no encontrado"
    fi
fi

echo ""
echo "Presiona Ctrl+C para detener todo"

# Trap para limpiar
trap "kill $SERVER_PID $TUNNEL_PID 2>/dev/null; exit" INT TERM

# Mantener vivo
wait
