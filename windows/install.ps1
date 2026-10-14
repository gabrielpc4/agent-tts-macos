# Instala o agent-tts no Windows: leitura em voz alta das respostas do Claude Code (e Codex/Cursor, se instalados).
# Uso (na pasta do repositorio):  powershell -ExecutionPolicy Bypass -File windows\install.ps1
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Base = Join-Path $env:USERPROFILE ".tts-agentes"

function Achar-Python {
    $opcoes = @(
        @{ Exe = "py"; Extra = @("-3") },
        @{ Exe = "python"; Extra = @() },
        @{ Exe = "python3"; Extra = @() }
    )
    foreach ($op in $opcoes) {
        if (-not (Get-Command $op.Exe -ErrorAction SilentlyContinue)) { continue }
        $extra = $op.Extra
        try {
            $versao = & $op.Exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($versao -and ([version]$versao -ge [version]"3.10")) { return $op }
        } catch { }
    }
    return $null
}

Write-Host "-> Conferindo pre-requisitos"
$py = Achar-Python
if (-not $py) { throw "Python 3.10 ou mais novo nao encontrado. Instale com: winget install Python.Python.3.12" }
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "   ffmpeg nao encontrado. Instalando com winget..."
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    Write-Host "   Se o ffmpeg nao for reconhecido logo em seguida, feche e abra este terminal e rode o instalador de novo."
}
if (-not (Get-Command bash -ErrorAction SilentlyContinue)) {
    Write-Host "   AVISO: o bash do Git for Windows nao foi encontrado no PATH. Os hooks do Claude Code rodam nele: winget install Git.Git"
}

Write-Host "-> Copiando arquivos para $Base"
New-Item -ItemType Directory -Force -Path $Base, (Join-Path $Base "bin") | Out-Null
foreach ($arq in "tts.py", "plataforma.py", "win_integracao.py", "tray_windows.py", "autoteste.py", "configurar_hooks.py") {
    Copy-Item (Join-Path $Repo "src\$arq") (Join-Path $Base $arq) -Force
}

Write-Host "-> Criando o ambiente Python (pode demorar um pouco)"
$venvPython = Join-Path $Base ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    $extra = $py.Extra
    & $py.Exe @extra -m venv (Join-Path $Base ".venv")
}
& $venvPython -m pip install -q --upgrade pip
& $venvPython -m pip install -q --upgrade edge-tts sounddevice keyring psutil pystray pillow

Write-Host "-> Comando 'tts'"
$cmd = Join-Path $Base "bin\tts.cmd"
Set-Content -Path $cmd -Encoding ASCII -Value "@echo off`r`n`"%USERPROFILE%\.tts-agentes\.venv\Scripts\python.exe`" `"%USERPROFILE%\.tts-agentes\tts.py`" %*"
$bin = Join-Path $Base "bin"
$pathUsuario = [Environment]::GetEnvironmentVariable("Path", "User")
if (($pathUsuario -split ";") -notcontains $bin) {
    [Environment]::SetEnvironmentVariable("Path", ($pathUsuario.TrimEnd(";") + ";" + $bin), "User")
    Write-Host "   Adicionado ao PATH. Abra um novo terminal para usar o comando tts."
}

Write-Host "-> Hooks do Claude Code (e Codex/Cursor, se existirem)"
& $venvPython (Join-Path $Base "configurar_hooks.py") instalar

Write-Host "-> Icone na bandeja (abre no login)"
$pythonw = Join-Path $Base ".venv\Scripts\pythonw.exe"
$tray = Join-Path $Base "tray_windows.py"
$atalho = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\TTS Agentes.lnk"
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($atalho)
$lnk.TargetPath = $pythonw
$lnk.Arguments = "`"$tray`""
$lnk.WorkingDirectory = $Base
$lnk.Save()
Start-Process -FilePath $pythonw -ArgumentList "`"$tray`"" -WorkingDirectory $Base

Write-Host ""
Write-Host "Pronto. Proximos passos:"
Write-Host "  1. Chave do Gemini: crie em https://aistudio.google.com/apikey (ative o faturamento), copie e rode:  tts chave gemini"
Write-Host "     (sem chave, a voz usada e a da Microsoft, gratis)"
Write-Host "  2. Diagnostico:  tts diagnostico      Teste de audio:  tts autoteste"
Write-Host "  3. O programa abre DESLIGADO. Clique no icone perto do relogio e marque 'Leitura ligada'."
Write-Host "  4. Atalhos: Ctrl+Alt+P pausa/continua, Ctrl+Alt+S le a selecao (mude em %USERPROFILE%\.tts-agentes\config.json)."
