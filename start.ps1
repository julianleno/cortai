# Cortai - script de inicializacao (Windows)
#
# Verifica dependencias, cria o ambiente virtual se necessario, instala
# pacotes, garante que o .env e os diretorios de armazenamento existem, e
# inicia a interface local. Pensado para rodar com duplo clique via
# start.bat, ou diretamente: powershell -ExecutionPolicy Bypass -File start.ps1

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "OK: $msg" -ForegroundColor Green }
function Write-Fail($msg) { Write-Host "ERRO: $msg" -ForegroundColor Red }

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

Write-Step "Verificando Python"
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Fail "Python nao encontrado no PATH."
    Write-Host "Instale Python 3.12 ou mais recente em https://www.python.org/downloads/"
    Write-Host "Durante a instalacao, marque a opcao 'Add python.exe to PATH'."
    exit 1
}
$pythonVersion = & python --version 2>&1
Write-Ok "Python encontrado: $pythonVersion"

Write-Step "Verificando FFmpeg"
$ffmpegCmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ffmpegCmd) {
    Write-Fail "FFmpeg nao encontrado no PATH."
    Write-Host "Baixe um build 'full' em https://www.gyan.dev/ffmpeg/builds/, extraia o .zip,"
    Write-Host "e adicione a pasta 'bin' dele as variaveis de ambiente PATH do Windows."
    exit 1
}
Write-Ok "FFmpeg encontrado: $($ffmpegCmd.Source)"

Write-Step "Verificando ffprobe"
$ffprobeCmd = Get-Command ffprobe -ErrorAction SilentlyContinue
if (-not $ffprobeCmd) {
    Write-Fail "ffprobe nao encontrado no PATH (normalmente vem junto com o FFmpeg)."
    Write-Host "Verifique se a pasta 'bin' do FFmpeg (que contem ffmpeg.exe e ffprobe.exe) esta no PATH."
    exit 1
}
Write-Ok "ffprobe encontrado: $($ffprobeCmd.Source)"

Write-Step "Verificando arquivo .env"
if (-not (Test-Path ".env")) {
    Write-Host "Arquivo .env nao encontrado. Copiando de .env.example..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    Write-Host "Configure GEMINI_API_KEY e/ou NVIDIA_NIM_API_KEY no arquivo .env para habilitar" -ForegroundColor Yellow
    Write-Host "a selecao automatica de clipes por IA (opcional -- sem chave, o sistema usa" -ForegroundColor Yellow
    Write-Host "apenas regras locais deterministicas)." -ForegroundColor Yellow
}
Write-Ok "Arquivo .env presente"

Write-Step "Verificando ambiente virtual Python (.venv)"
$venvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue

if (-not (Test-Path $venvPython)) {
    Write-Host "Ambiente virtual nao encontrado. Criando .venv..." -ForegroundColor Yellow
    if ($uvCmd) {
        & uv venv .venv
    } else {
        & python -m venv .venv
    }
}
Write-Ok "Ambiente virtual pronto"

Write-Step "Instalando/atualizando dependencias"
if ($uvCmd) {
    & uv pip install -e . --python .venv
} else {
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -e .
}
Write-Ok "Dependencias instaladas"

Write-Step "Verificando diretorios de armazenamento"
New-Item -ItemType Directory -Force -Path "storage\downloads" | Out-Null
New-Item -ItemType Directory -Force -Path "storage\output" | Out-Null
New-Item -ItemType Directory -Force -Path "storage\temp" | Out-Null
Write-Ok "Diretorios prontos"

Write-Step "Verificando porta"
$portValue = 5000
$envPortLine = Get-Content ".env" -ErrorAction SilentlyContinue | Where-Object { $_ -match "^FLASK_PORT=" }
if ($envPortLine) {
    $portValue = ($envPortLine -split "=", 2)[1].Trim()
}
$portInUse = $null
try {
    $portInUse = Get-NetTCPConnection -LocalPort $portValue -State Listen -ErrorAction SilentlyContinue
} catch {
    # Get-NetTCPConnection may be unavailable on some systems; not fatal, just skip the check.
}
if ($portInUse) {
    Write-Fail "A porta $portValue ja esta em uso por outro processo."
    Write-Host "Feche o outro processo, ou mude FLASK_PORT no arquivo .env e tente novamente."
    exit 1
}
Write-Ok "Porta $portValue disponivel"

Write-Step "Iniciando Cortai"
Write-Host ""
Write-Host "Abra http://127.0.0.1:$portValue no navegador." -ForegroundColor Cyan
Write-Host "Pressione CTRL+C nesta janela para encerrar." -ForegroundColor Cyan
Write-Host ""

& $venvPython -m cortai.app
