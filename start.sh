#!/usr/bin/env bash
# Cortaí - script de inicialização (Linux e macOS)
#
# Verifica dependências, cria o ambiente virtual se necessário, instala
# pacotes, garante que o .env e os diretórios de armazenamento existem, e
# inicia a interface local. Rode com: ./start.sh

set -euo pipefail

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
ok() { printf '\033[32mOK: %s\033[0m\n' "$1"; }
fail() { printf '\033[31mERRO: %s\033[0m\n' "$1" >&2; }
warn() { printf '\033[33m%s\033[0m\n' "$1"; }

cd "$(dirname "$0")"

step "Verificando Python"
if ! command -v python3 >/dev/null 2>&1; then
    fail "python3 não encontrado no PATH."
    echo "Instale Python 3.12 ou mais recente:"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-venv"
    echo "  macOS:         brew install python@3.12"
    exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
    fail "Python 3.12 ou mais recente é necessário (encontrado: $(python3 --version 2>&1))."
    echo "  Ubuntu/Debian: sudo apt install python3.12 python3.12-venv"
    echo "  macOS:         brew install python@3.12"
    exit 1
fi
ok "Python encontrado: $(python3 --version 2>&1)"

step "Verificando FFmpeg"
if ! command -v ffmpeg >/dev/null 2>&1; then
    fail "FFmpeg não encontrado no PATH."
    echo "  Ubuntu/Debian: sudo apt install ffmpeg"
    echo "  macOS:         brew install ffmpeg"
    exit 1
fi
ok "FFmpeg encontrado: $(command -v ffmpeg)"

step "Verificando ffprobe"
if ! command -v ffprobe >/dev/null 2>&1; then
    fail "ffprobe não encontrado no PATH (normalmente vem junto com o FFmpeg)."
    echo "Reinstale o FFmpeg pelo apt ou pelo brew: o pacote inclui o ffprobe."
    exit 1
fi
ok "ffprobe encontrado: $(command -v ffprobe)"

step "Verificando arquivo .env"
if [ ! -f .env ]; then
    warn "Arquivo .env não encontrado. Copiando de .env.example..."
    cp .env.example .env
    warn "Configure uma chave de IA (GEMINI_API_KEY, NVIDIA_NIM_API_KEY, OPENAI_API_KEY ou"
    warn "ANTHROPIC_API_KEY) no .env ou na tela Configurações para habilitar a seleção de"
    warn "clipes por IA (opcional: sem chave, o sistema usa apenas regras locais)."
fi
ok "Arquivo .env presente"

step "Verificando ambiente virtual Python (.venv)"
venv_python=".venv/bin/python"
has_uv=false
command -v uv >/dev/null 2>&1 && has_uv=true

if [ ! -x "$venv_python" ]; then
    warn "Ambiente virtual não encontrado. Criando .venv..."
    if $has_uv; then
        uv venv .venv
    else
        python3 -m venv .venv
    fi
fi
ok "Ambiente virtual pronto"

step "Instalando/atualizando dependências"
if $has_uv; then
    uv pip install -e . --python .venv
else
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -e .
fi
ok "Dependências instaladas"

step "Verificando diretórios de armazenamento"
mkdir -p storage/downloads storage/output storage/temp
ok "Diretórios prontos"

step "Verificando porta"
port=$(sed -n 's/^FLASK_PORT=//p' .env | tail -n 1 | tr -d '[:space:]')
port=${port:-5000}
if command -v lsof >/dev/null 2>&1; then
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
        fail "A porta $port já está em uso por outro processo."
        echo "Feche o outro processo, ou mude FLASK_PORT no arquivo .env e tente novamente."
        exit 1
    fi
    ok "Porta $port disponível"
else
    warn "lsof não encontrado: pulando a checagem da porta $port."
fi

step "Iniciando Cortaí"
echo ""
printf '\033[36m%s\033[0m\n' "Abra http://127.0.0.1:$port no navegador."
printf '\033[36m%s\033[0m\n' "Pressione CTRL+C neste terminal para encerrar."
echo ""

exec "$venv_python" -m cortai.app
