<#
.SYNOPSIS
  Instala o desinstala CallHermes como inicio automático en Windows.

.DESCRIPTION
  Crea una tarea en el Programador de Tareas de Windows que inicia
  CallHermes automáticamente al iniciar sesión.

.PARAMETER Uninstall
  Elimina la tarea de inicio automático.

.EXAMPLE
  .\install-startup.ps1          # Instalar auto-start
  .\install-startup.ps1 -Uninstall  # Desinstalar
#>

param(
  [switch]$Uninstall
)

$taskName = "CallHermes"
$taskPath = "\CallHermes\"

try {
  if ($Uninstall) {
    # ── Desinstalar ──────────────────────────────────────────
    Write-Host "Eliminando tarea '$taskName'..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskPath $taskPath -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "✓ Tarea eliminada. CallHermes ya no inicia automaticamente." -ForegroundColor Green
    return
  }

  # ── Instalar ──────────────────────────────────────────────
  $wslProject = "/mnt/d/PROCESO/callhermes"
  $startCmd = "bash -c 'cd $wslProject && source ~/.venvs/callhermes/bin/activate && python server.py'"

  $action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d Ubuntu $startCmd"

  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

  $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

  Write-Host "Creando tarea '$taskName'..." -ForegroundColor Yellow
  Register-ScheduledTask -TaskPath $taskPath -TaskName $taskName `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force

  Write-Host "✓ CallHermes iniciara automaticamente al iniciar sesion." -ForegroundColor Green
  Write-Host "  Para desinstalar: .\install-startup.ps1 -Uninstall" -ForegroundColor Gray

} catch {
  Write-Host "Error: $_" -ForegroundColor Red
  exit 1
}
