# Removes agent-tts from Windows. The Gemini key in Credential Manager stays; to delete it:
#   cmdkey /delete:tts-gemini   (or use Credential Manager itself)
$ErrorActionPreference = "Continue"
$Base = Join-Path $env:USERPROFILE ".agent-tts"
$py = Join-Path $Base ".venv\Scripts\python.exe"

Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*tray_windows.py*" -or $_.CommandLine -like "*tts.py _worker*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if (Test-Path (Join-Path $Base "configure_hooks.py")) { & $py (Join-Path $Base "configure_hooks.py") remove }
Remove-Item (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\Agent TTS.lnk") -ErrorAction SilentlyContinue
$bin = Join-Path $Base "bin"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
[Environment]::SetEnvironmentVariable("Path", ((($userPath -split ";") | Where-Object { $_ -ne $bin }) -join ";"), "User")
Remove-Item $Base -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Removed."
