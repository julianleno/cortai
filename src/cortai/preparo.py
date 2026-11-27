"""Preparação da primeira abertura: FFmpeg e modelo de transcrição.

- FFmpeg: procura no PATH e numa cópia portátil em `<pasta do app>/ffmpeg/bin`. No Windows,
  instala essa cópia portátil com um clique (não mexe no sistema).
- Modelo do Whisper: baixa antes do primeiro corte, com progresso, para o primeiro processamento
  não parecer travado em "Transcrição".

O estado fica em memória e a tela `/preparar` consulta `status()`.
"""
import logging
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import httpx

from .config import APP_DIR, config

logger = logging.getLogger(__name__)

FFMPEG_ZIP_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
FFMPEG_EXES = ("ffmpeg.exe", "ffprobe.exe")
# Tamanho aproximado de cada modelo, usado só se o Hugging Face não informar o total.
MODEL_SIZE_ESTIMATE = {"tiny": 75e6, "base": 148e6, "small": 484e6, "medium": 1.53e9, "large-v3": 3.09e9}

_lock = threading.Lock()
_state = {
    "ffmpeg": {"estado": "desconhecido", "baixado": 0, "total": 0, "erro": None},
    "modelo": {"estado": "desconhecido", "baixado": 0, "total": 0, "erro": None},
}


def _set(item: str, **values) -> None:
    with _lock:
        _state[item].update(values)


def local_ffmpeg_dir() -> Path:
    return Path(APP_DIR) / "ffmpeg" / "bin"


def activate_local_ffmpeg() -> None:
    """Põe a cópia portátil do FFmpeg no PATH deste processo, se ela existir."""
    folder = local_ffmpeg_dir()
    if all((folder / name).exists() for name in FFMPEG_EXES):
        path = os.environ.get("PATH", "")
        if str(folder) not in path.split(os.pathsep):
            os.environ["PATH"] = str(folder) + os.pathsep + path


def ffmpeg_ok() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def can_install_ffmpeg() -> bool:
    return sys.platform == "win32"


def _model_repo() -> str:
    from faster_whisper.utils import _MODELS
    size = str(config.WHISPER_MODEL_SIZE)
    return _MODELS.get(size, size)


def model_ok() -> bool:
    from faster_whisper.utils import download_model
    try:
        download_model(str(config.WHISPER_MODEL_SIZE), local_files_only=True)
        return True
    except Exception:
        return False


def needs_setup() -> bool:
    if os.getenv("CORTAI_PREPARO", "1") == "0":
        return False
    return not (ffmpeg_ok() and model_ok())


def status() -> dict:
    """Estado atual das duas etapas, para a tela e para a API."""
    with _lock:
        snapshot = {k: dict(v) for k, v in _state.items()}
    if snapshot["ffmpeg"]["estado"] not in ("baixando", "instalando", "erro"):
        snapshot["ffmpeg"]["estado"] = "pronto" if ffmpeg_ok() else "faltando"
    if snapshot["modelo"]["estado"] not in ("baixando", "erro"):
        snapshot["modelo"]["estado"] = "pronto" if model_ok() else "faltando"
    snapshot["ffmpeg"]["pode_instalar"] = can_install_ffmpeg()
    snapshot["modelo"]["nome"] = str(config.WHISPER_MODEL_SIZE)
    snapshot["pronto"] = snapshot["ffmpeg"]["estado"] == "pronto" and snapshot["modelo"]["estado"] == "pronto"
    return snapshot


# --- modelo do Whisper ---

def _hf_cache_dir() -> Path:
    from huggingface_hub import constants
    return Path(constants.HF_HUB_CACHE) / ("models--" + _model_repo().replace("/", "--"))


def _folder_size(folder: Path) -> int:
    if not folder.exists():
        return 0
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file() and not f.is_symlink())


def _model_total() -> int:
    try:
        from huggingface_hub import HfApi
        info = HfApi().model_info(_model_repo(), files_metadata=True)
        total = sum((s.size or 0) for s in info.siblings)
        if total:
            return total
    except Exception:
        logger.info("Não consegui ler o tamanho do modelo no Hugging Face; usando estimativa")
    return int(MODEL_SIZE_ESTIMATE.get(str(config.WHISPER_MODEL_SIZE), 0))


def start_model_download() -> None:
    """Baixa o modelo em segundo plano. Chamar de novo durante o download não faz nada."""
    with _lock:
        if _state["modelo"]["estado"] == "baixando":
            return
        _state["modelo"].update(estado="baixando", baixado=0, total=0, erro=None)

    def run():
        from faster_whisper.utils import download_model
        done = threading.Event()
        cache = _hf_cache_dir()
        start_size = _folder_size(cache)

        def monitor():
            _set("modelo", total=_model_total())
            while not done.wait(0.5):
                _set("modelo", baixado=max(0, _folder_size(cache) - start_size))

        threading.Thread(target=monitor, daemon=True).start()
        try:
            download_model(str(config.WHISPER_MODEL_SIZE))
            _set("modelo", estado="pronto")
        except Exception as e:
            logger.exception("Falha ao baixar o modelo do Whisper")
            _set("modelo", estado="erro", erro=f"Não foi possível baixar o modelo de transcrição: {e}")
        finally:
            done.set()

    threading.Thread(target=run, name="cortai-modelo", daemon=True).start()


# --- FFmpeg portátil (Windows) ---

def install_ffmpeg(url: str = FFMPEG_ZIP_URL) -> None:
    """Baixa o FFmpeg portátil para a pasta do app em segundo plano."""
    if not can_install_ffmpeg():
        _set("ffmpeg", estado="erro", erro="A instalação automática só existe no Windows.")
        return
    with _lock:
        if _state["ffmpeg"]["estado"] in ("baixando", "instalando"):
            return
        _state["ffmpeg"].update(estado="baixando", baixado=0, total=0, erro=None)
    threading.Thread(target=_install_ffmpeg, args=(url,), name="cortai-ffmpeg", daemon=True).start()


def _install_ffmpeg(url: str) -> None:
    folder = local_ffmpeg_dir()
    folder.mkdir(parents=True, exist_ok=True)
    zip_path = folder.parent / "ffmpeg-download.zip"
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(30, connect=10),
                          headers={"User-Agent": "Cortai"}) as resp:
            resp.raise_for_status()
            _set("ffmpeg", total=int(resp.headers.get("content-length") or 0))
            done = 0
            with open(zip_path, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
                    done += len(chunk)
                    _set("ffmpeg", baixado=done)

        _set("ffmpeg", estado="instalando")
        with zipfile.ZipFile(zip_path) as z:
            for name in FFMPEG_EXES:
                member = next((m for m in z.namelist() if m.endswith("/bin/" + name)), None)
                if member is None:
                    raise RuntimeError(f"O pacote baixado não tem o {name}.")
                with z.open(member) as src, open(folder / name, "wb") as dst:
                    shutil.copyfileobj(src, dst)

        result = subprocess.run([str(folder / "ffmpeg.exe"), "-version"], capture_output=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError("O FFmpeg baixado não abriu.")
        activate_local_ffmpeg()
        _set("ffmpeg", estado="pronto")
    except Exception as e:
        logger.exception("Falha ao instalar o FFmpeg portátil")
        for name in FFMPEG_EXES:
            (folder / name).unlink(missing_ok=True)
        _set("ffmpeg", estado="erro", erro=f"Não foi possível instalar o FFmpeg: {e}")
    finally:
        zip_path.unlink(missing_ok=True)
