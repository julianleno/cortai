"""Aviso de versão nova e autoatualização do Cortai.exe.

Só consulta a última release pública no GitHub: nada do usuário é enviado.
Desligue com CORTAI_CHECAR_ATUALIZACAO=0.
"""
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
from markupsafe import Markup, escape

from . import __version__, db

logger = logging.getLogger(__name__)

API_URL = "https://api.github.com/repos/julianleno/cortai/releases/latest"
DOWNLOAD_PREFIX = "https://github.com/julianleno/cortai/releases/download/"
ASSET_NAME = "Cortai.exe"
CACHE_SECONDS = 24 * 3600
HEADERS = {"User-Agent": f"Cortai/{__version__}", "Accept": "application/vnd.github+json"}

_latest: dict | None = None  # última release conhecida (da rede ou do cache)


class UpdateError(Exception):
    """Falha ao instalar a atualização; a mensagem vai para a interface."""


def parse_version(tag) -> tuple[int, ...] | None:
    try:
        return tuple(int(p) for p in str(tag).strip().lstrip("vV").split("."))
    except (ValueError, AttributeError):
        return None


def is_newer(version) -> bool:
    new, cur = parse_version(version), parse_version(__version__)
    return bool(new and cur and new > cur)


def enabled() -> bool:
    return os.getenv("CORTAI_CHECAR_ATUALIZACAO", "1").strip() != "0"


def can_self_update() -> bool:
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


def fetch_latest() -> dict | None:
    """Consulta o GitHub. Qualquer falha vira None (silêncio)."""
    try:
        resp = httpx.get(API_URL, headers=HEADERS, timeout=4, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        version = str(data["tag_name"]).lstrip("vV")
        if parse_version(version) is None:
            logger.info("Tag de release ignorada: %r", data["tag_name"])
            return None
        asset = next((a for a in data.get("assets") or [] if a.get("name") == ASSET_NAME), None)
        return {
            "version": version,
            "url": data.get("html_url") or "https://github.com/julianleno/cortai/releases/latest",
            "asset_url": asset["browser_download_url"] if asset else None,
            "asset_size": int(asset["size"]) if asset else None,
            "notes": str(data.get("body") or "")[:8000],  # novidades da versão (markdown da release)
        }
    except Exception as e:  # rede, JSON, campos faltando
        logger.info("Checagem de atualização falhou: %s", e)
        return None


def check(db_path: str, now: float | None = None) -> dict | None:
    """Atualiza a release conhecida, consultando a rede no máximo uma vez a cada 24 h."""
    global _latest
    if not enabled():
        return None
    now = time.time() if now is None else now
    try:
        checked_at = float(db.get_setting(db_path, "update_checked_at", "0"))
        cached = json.loads(db.get_setting(db_path, "update_latest", "null"))
    except (ValueError, TypeError):
        checked_at, cached = 0.0, None
    if checked_at and 0 <= now - checked_at < CACHE_SECONDS:  # relógio voltou = consulta de novo
        _latest = cached
        return available()
    release = fetch_latest()
    if release is not None:
        db.set_setting(db_path, "update_latest", json.dumps(release))
        db.set_setting(db_path, "update_checked_at", str(now))
    _latest = release or cached
    return available()


def available() -> dict | None:
    """Release mais nova que a versão em uso, sem tocar na rede (usado no render)."""
    return _latest if _latest and is_newer(_latest.get("version")) else None


def start_background_check(db_path: str) -> None:
    def run():
        # A limpeza não depende da checagem: quem desligou a checagem também atualiza o .exe.
        if can_self_update():
            time.sleep(5)  # dá tempo da versão anterior fechar e soltar o Cortai.old.exe
            cleanup_orphans(Path(sys.executable).parent)
        if not enabled():
            return
        try:
            check(db_path)
        except Exception:
            logger.debug("Checagem de atualização falhou", exc_info=True)

    threading.Thread(target=run, name="cortai-updater", daemon=True).start()


def cleanup_orphans(folder: Path) -> None:
    for name in ("Cortai.old.exe", "Cortai.new.exe"):
        try:
            (folder / name).unlink(missing_ok=True)
        except OSError:
            pass


def apply_update(release: dict, exe_path: str | None = None, popen=None) -> None:
    """Baixa o Cortai.exe novo, troca pelo atual e inicia a versão nova.

    Se algo falhar, o executável atual continua no lugar.
    """
    url, size = release.get("asset_url"), release.get("asset_size")
    if not url or not size or not str(url).startswith(DOWNLOAD_PREFIX):
        raise UpdateError("Esta versão não tem o Cortai.exe para baixar. Baixe pela página da versão.")
    exe = Path(exe_path or sys.executable)
    new, old = exe.with_name("Cortai.new.exe"), exe.with_name("Cortai.old.exe")

    try:
        with httpx.stream("GET", url, headers=HEADERS, follow_redirects=True,
                          timeout=httpx.Timeout(30, connect=10)) as resp:
            resp.raise_for_status()
            with open(new, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
        if new.stat().st_size != size:
            raise UpdateError("O download veio incompleto. Tente de novo.")
        with open(new, "rb") as f:
            if f.read(2) != b"MZ":
                raise UpdateError("O arquivo baixado não é um executável válido.")
    except Exception as e:
        new.unlink(missing_ok=True)
        if isinstance(e, UpdateError):
            raise
        raise UpdateError("Não foi possível baixar a atualização. Verifique a internet e tente de novo.") from e

    try:
        old.unlink(missing_ok=True)
        exe.replace(old)  # o Windows deixa renomear um .exe em uso
    except OSError as e:
        new.unlink(missing_ok=True)
        raise UpdateError("Não foi possível substituir o Cortai.exe. Verifique as permissões da pasta.") from e

    try:
        new.replace(exe)
        env = os.environ.copy()
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"  # o .exe novo extrai os próprios arquivos, sem herdar os deste
        env.pop("_MEIPASS2", None)
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        (popen or subprocess.Popen)([str(exe)], cwd=str(exe.parent), env=env, creationflags=flags, close_fds=True)
    except Exception as e:
        try:
            if exe.exists():
                exe.replace(new)
            old.replace(exe)
        except OSError:
            logger.exception("Falha ao desfazer a troca do executável")
        new.unlink(missing_ok=True)
        raise UpdateError("Não foi possível iniciar a versão nova. O Cortaí continua na versão atual.") from e


_INLINE = [
    (re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
]


def notes_html(markdown: str | None) -> Markup:
    """Converte o markdown das novidades (subconjunto: títulos, listas, negrito, código) em HTML.

    Todo o texto é escapado antes; só as tags geradas aqui entram no HTML.
    """
    out, list_tag = [], None

    def close_list():
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    def inline(text: str) -> str:
        text = str(escape(text.strip()))
        for pattern, repl in _INLINE:
            text = pattern.sub(repl, text)
        return text

    for line in (markdown or "").splitlines():
        stripped = line.strip()
        bullet = re.match(r"^[-*]\s+(.*)", stripped)
        numbered = re.match(r"^\d+[.)]\s+(.*)", stripped)
        if bullet or numbered:
            tag = "ul" if bullet else "ol"
            if list_tag != tag:
                close_list()
                out.append(f"<{tag}>")
                list_tag = tag
            out.append(f"<li>{inline((bullet or numbered).group(1))}</li>")
            continue
        close_list()
        heading = re.match(r"^(#{1,6})\s+(.*)", stripped)
        if heading:
            out.append(f"<h4>{inline(heading.group(2))}</h4>")
        elif stripped:
            out.append(f"<p>{inline(stripped)}</p>")
    close_list()
    return Markup("".join(out))
