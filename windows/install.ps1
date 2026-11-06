# Installs agent-tts on Windows: reads Claude Code answers aloud (and Codex/Cursor, if installed).
# Usage (in the repository folder):  powershell -ExecutionPolicy Bypass -File windows\install.ps1
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Base = Join-Path $env:USERPROFILE ".agent-tts"
# Folder and startup shortcut used before the English rename: migrated and removed below.
$OldBase = Join-Path $env:USERPROFILE ".tts-agentes"
$Startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"

function Find-Python {
    $options = @(
        @{ Exe = "py"; Extra = @("-3") },
        @{ Exe = "python"; Extra = @() },
        @{ Exe = "python3"; Extra = @() }
    )
    foreach ($op in $options) {
        if (-not (Get-Command $op.Exe -ErrorAction SilentlyContinue)) { continue }
        $extra = $op.Extra
        try {
            $version = & $op.Exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($version -and ([version]$version -ge [version]"3.10")) { return $op }
        } catch { }
    }
    return $null
}

Write-Host "-> Checking prerequisites"
$py = Find-Python
if (-not $py) { throw "Python 3.10 or newer not found. Install it with: winget install Python.Python.3.12" }
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "   ffmpeg not found. Installing it with winget..."
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    Write-Host "   If ffmpeg isn't recognized right after, close and reopen this terminal and run the installer again."
}
if (-not (Get-Command bash -ErrorAction SilentlyContinue)) {
    Write-Host "   WARNING: Git for Windows' bash wasn't found in PATH. Claude Code runs hooks in it: winget install Git.Git"
}

New-Item -ItemType Directory -Force -Path $Base, (Join-Path $Base "bin") | Out-Null
if (Test-Path $OldBase) {
    Write-Host "-> Migrating the previous install ($OldBase)"
    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*\.tts-agentes\*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Remove-Item (Join-Path $Startup "TTS Agentes.lnk") -ErrorAction SilentlyContinue
    $oldConfig = Join-Path $OldBase "config.json"
    if ((Test-Path $oldConfig) -and -not (Test-Path (Join-Path $Base "config.json"))) {
        Copy-Item $oldConfig (Join-Path $Base "config.json")
    }
    $oldBin = Join-Path $OldBase "bin"
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    [Environment]::SetEnvironmentVariable("Path", ((($userPath -split ";") | Where-Object { $_ -ne $oldBin }) -join ";"), "User")
}

Write-Host "-> Copying files to $Base"
foreach ($file in "tts.py", "platform_support.py", "win_integration.py", "tray_windows.py", "selftest.py", "configure_hooks.py") {
    Copy-Item (Join-Path $Repo "src\$file") (Join-Path $Base $file) -Force
}

Write-Host "-> Creating the Python environment (this may take a while)"
$venvPython = Join-Path $Base ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    $extra = $py.Extra
    & $py.Exe @extra -m venv (Join-Path $Base ".venv")
}
& $venvPython -m pip install -q --upgrade pip
& $venvPython -m pip install -q --upgrade edge-tts sounddevice keyring psutil pystray pillow

Write-Host "-> 'tts' command"
$cmd = Join-Path $Base "bin\tts.cmd"
Set-Content -Path $cmd -Encoding ASCII -Value "@echo off`r`n`"%USERPROFILE%\.agent-tts\.venv\Scripts\python.exe`" `"%USERPROFILE%\.agent-tts\tts.py`" %*"
$bin = Join-Path $Base "bin"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (($userPath -split ";") -notcontains $bin) {
    [Environment]::SetEnvironmentVariable("Path", ($userPath.TrimEnd(";") + ";" + $bin), "User")
    Write-Host "   Added to PATH. Open a new terminal to use the tts command."
}

Write-Host "-> Claude Code hooks (and Codex/Cursor, if present)"
& $venvPython (Join-Path $Base "configure_hooks.py") install
if (Test-Path $OldBase) { Remove-Item $OldBase -Recurse -Force -ErrorAction SilentlyContinue }

Write-Host "-> Tray icon (opens at login)"
$pythonw = Join-Path $Base ".venv\Scripts\pythonw.exe"
$tray = Join-Path $Base "tray_windows.py"
$shortcut = Join-Path $Startup "Agent TTS.lnk"
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($shortcut)
$lnk.TargetPath = $pythonw
$lnk.Arguments = "`"$tray`""
$lnk.WorkingDirectory = $Base
$lnk.Save()
Start-Process -FilePath $pythonw -ArgumentList "`"$tray`"" -WorkingDirectory $Base

Write-Host ""
Write-Host "Done. Next steps:"
Write-Host "  1. Gemini key: create one at https://aistudio.google.com/apikey (enable billing), copy it and run:  tts key gemini"
Write-Host "     (without a key, the free Microsoft voice is used)"
Write-Host "  2. Diagnostic:  tts doctor      Audio test:  tts selftest"
Write-Host "  3. The app starts OFF. Click the icon near the clock and tick 'Reading on'."
Write-Host "  4. The voice language follows the Windows display language; change it in the tray menu (first item) or with: tts language"
Write-Host "  5. Shortcuts: Ctrl+Alt+P pause/resume, Ctrl+Alt+S read the selection (change them in %USERPROFILE%\.agent-tts\config.json)."
