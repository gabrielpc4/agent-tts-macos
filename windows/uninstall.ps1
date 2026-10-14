# Remove o agent-tts do Windows. A chave do Gemini no Gerenciador de Credenciais fica; para apagar:
#   cmdkey /delete:tts-gemini   (ou use o proprio Gerenciador de Credenciais)
$ErrorActionPreference = "Continue"
$Base = Join-Path $env:USERPROFILE ".tts-agentes"
$py = Join-Path $Base ".venv\Scripts\python.exe"

Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*tray_windows.py*" -or $_.CommandLine -like "*tts.py _worker*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if (Test-Path (Join-Path $Base "configurar_hooks.py")) { & $py (Join-Path $Base "configurar_hooks.py") remover }
Remove-Item (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\TTS Agentes.lnk") -ErrorAction SilentlyContinue
$bin = Join-Path $Base "bin"
$pathUsuario = [Environment]::GetEnvironmentVariable("Path", "User")
[Environment]::SetEnvironmentVariable("Path", ((($pathUsuario -split ";") | Where-Object { $_ -ne $bin }) -join ";"), "User")
Remove-Item $Base -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Removido."
