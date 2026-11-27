import importlib
import json
import os
from contextlib import contextmanager
from unittest import mock

import httpx
import pytest

from cortai import db, updater

ASSET_URL = "https://github.com/julianleno/cortai/releases/download/v9.9.9/Cortai.exe"
EXE_BYTES = b"MZ" + b"\0" * 98


def _release_json(tag="v9.9.9", assets=None):
    if assets is None:
        assets = [{"name": "Cortai.exe", "browser_download_url": ASSET_URL, "size": len(EXE_BYTES)}]
    return {"tag_name": tag, "html_url": f"https://github.com/julianleno/cortai/releases/tag/{tag}", "assets": assets}


def _response(payload, status=200):
    return httpx.Response(status, json=payload, request=httpx.Request("GET", updater.API_URL))


@pytest.fixture(autouse=True)
def _isolado(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "_latest", None)
    monkeypatch.setattr(updater, "__version__", "1.6.0")
    monkeypatch.delenv("CORTAI_CHECAR_ATUALIZACAO", raising=False)
    # Rede proibida: qualquer chamada real falha o teste.
    monkeypatch.setattr(httpx, "get", mock.Mock(side_effect=AssertionError("rede não pode ser usada")))
    monkeypatch.setattr(httpx, "stream", mock.Mock(side_effect=AssertionError("rede não pode ser usada")))


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "cortai.db")
    db.init_db(path)
    return path


# --- comparação de versões ---

@pytest.mark.parametrize("tag,esperado", [
    ("v1.7.0", True), ("1.6.1", True), ("v2.0", True),
    ("v1.6.0", False), ("1.5.9", False), ("v1.10.0", True),
    ("v1.7.0-beta", False), ("lançamento", False), ("", False), (None, False),
])
def test_is_newer(tag, esperado):
    assert updater.is_newer(tag) is esperado


def test_parse_version_tolera_tag_malformada():
    assert updater.parse_version("v1.2.3") == (1, 2, 3)
    assert updater.parse_version("v1.x") is None
    assert updater.parse_version(None) is None


# --- consulta ao GitHub ---

def test_fetch_latest_le_release_e_asset(monkeypatch):
    get = mock.Mock(return_value=_response(_release_json()))
    monkeypatch.setattr(httpx, "get", get)
    rel = updater.fetch_latest()
    assert rel == {"version": "9.9.9", "url": "https://github.com/julianleno/cortai/releases/tag/v9.9.9",
                   "asset_url": ASSET_URL, "asset_size": len(EXE_BYTES), "notes": ""}
    headers = get.call_args.kwargs["headers"]
    assert headers["User-Agent"].startswith("Cortai/") and len(headers) == 2  # só identifica o app
    assert get.call_args.kwargs["timeout"] <= 5


def test_fetch_latest_sem_asset_exe(monkeypatch):
    monkeypatch.setattr(httpx, "get", mock.Mock(return_value=_response(_release_json(assets=[]))))
    rel = updater.fetch_latest()
    assert rel["version"] == "9.9.9" and rel["asset_url"] is None


def test_fetch_latest_tag_malformada(monkeypatch):
    monkeypatch.setattr(httpx, "get", mock.Mock(return_value=_response(_release_json(tag="nightly"))))
    assert updater.fetch_latest() is None


@pytest.mark.parametrize("erro", [httpx.ConnectError("sem rede"), httpx.ReadTimeout("lento")])
def test_erro_de_rede_e_silencioso(monkeypatch, db_path, erro):
    monkeypatch.setattr(httpx, "get", mock.Mock(side_effect=erro))
    assert updater.check(db_path) is None
    assert updater.available() is None


def test_http_500_e_silencioso(monkeypatch, db_path):
    monkeypatch.setattr(httpx, "get", mock.Mock(return_value=_response({}, status=500)))
    assert updater.check(db_path) is None


# --- cache e desligamento ---

def test_cache_de_24h(monkeypatch, db_path):
    get = mock.Mock(return_value=_response(_release_json()))
    monkeypatch.setattr(httpx, "get", get)
    assert updater.check(db_path, now=1000.0)["version"] == "9.9.9"
    monkeypatch.setattr(updater, "_latest", None)  # simula app reaberto
    assert updater.check(db_path, now=1000.0 + 23 * 3600)["version"] == "9.9.9"
    assert get.call_count == 1
    updater.check(db_path, now=1000.0 + 25 * 3600)
    assert get.call_count == 2


def test_cache_com_versao_igual_nao_mostra_aviso(db_path):
    db.set_setting(db_path, "update_checked_at", "1000")
    db.set_setting(db_path, "update_latest", json.dumps({"version": "1.6.0", "url": "x"}))
    assert updater.check(db_path, now=1001.0) is None


def test_variavel_desliga_checagem(monkeypatch, db_path):
    monkeypatch.setenv("CORTAI_CHECAR_ATUALIZACAO", "0")
    get = mock.Mock(return_value=_response(_release_json()))
    monkeypatch.setattr(httpx, "get", get)
    _thread_sincrona(monkeypatch)
    assert updater.check(db_path) is None
    updater.start_background_check(db_path)
    get.assert_not_called()


def _thread_sincrona(monkeypatch):
    class Sincrona:
        def __init__(self, target, **_):
            self.target = target
        def start(self):
            self.target()
    monkeypatch.setattr(updater.threading, "Thread", Sincrona)


def test_limpeza_do_exe_roda_mesmo_com_checagem_desligada(monkeypatch, db_path, tmp_path):
    monkeypatch.setenv("CORTAI_CHECAR_ATUALIZACAO", "0")
    monkeypatch.setattr(updater, "can_self_update", lambda: True)
    monkeypatch.setattr(updater.time, "sleep", lambda _: None)
    monkeypatch.setattr(updater.sys, "executable", str(tmp_path / "Cortai.exe"))
    (tmp_path / "Cortai.old.exe").write_bytes(b"MZ")
    _thread_sincrona(monkeypatch)
    updater.start_background_check(db_path)
    assert not (tmp_path / "Cortai.old.exe").exists()


def test_novidades_viram_html_seguro():
    md = "\n".join(["## Novidades", "- **Janela** própria", "- legenda `Neon`", "", "Texto <script>x</script>", "1. um"])
    html = str(updater.notes_html(md))
    assert "<h4>Novidades</h4>" in html
    assert "<ul><li><strong>Janela</strong> própria</li><li>legenda <code>Neon</code></li></ul>" in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<ol><li>um</li></ol>" in html
    assert str(updater.notes_html(None)) == ""


def test_fetch_latest_guarda_as_novidades(monkeypatch):
    payload = _release_json()
    payload["body"] = "- Novidade"
    monkeypatch.setattr(httpx, "get", mock.Mock(return_value=_response(payload)))
    assert updater.fetch_latest()["notes"] == "- Novidade"


# --- banner no HTML ---

@pytest.fixture
def app_module(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cortai.db"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("TEMP_DIR", str(tmp_path / "temp"))
    import cortai.config as config_module
    import cortai.app as module
    importlib.reload(config_module)
    importlib.reload(module)
    module.app.testing = True
    return module


def test_banner_aparece_com_versao_nova(monkeypatch, app_module):
    monkeypatch.setattr(updater, "_latest", {"version": "9.9.9", "url": "https://github.com/julianleno/cortai/releases/tag/v9.9.9"})
    html = app_module.app.test_client().get("/").get_data(as_text=True)
    assert "Versão 9.9.9 disponível" in html
    assert 'role="status"' in html and 'target="_blank" rel="noopener"' in html
    assert "git pull" in html  # rodando pelo código-fonte
    assert "Atualizar agora" not in html


def test_banner_no_exe_tem_atualizar_agora(monkeypatch, app_module):
    monkeypatch.setattr(updater, "_latest", {"version": "9.9.9", "url": "u"})
    monkeypatch.setattr(updater, "can_self_update", lambda: True)
    html = app_module.app.test_client().get("/").get_data(as_text=True)
    assert "Atualizar agora" in html and "git pull" not in html


def test_banner_some_sem_versao_nova(monkeypatch, app_module):
    monkeypatch.setattr(updater, "_latest", {"version": "1.6.0", "url": "u"})
    html = app_module.app.test_client().get("/").get_data(as_text=True)
    assert "disponível" not in html and "ctUpdate" not in html


# --- troca do executável ---

@contextmanager
def _download(conteudo):
    resp = mock.Mock()
    resp.raise_for_status = mock.Mock()
    resp.iter_bytes = mock.Mock(return_value=iter([conteudo[:10], conteudo[10:]]))
    yield resp


def _release():
    return {"version": "9.9.9", "url": "u", "asset_url": ASSET_URL, "asset_size": len(EXE_BYTES)}


@pytest.fixture
def exe(tmp_path, monkeypatch):
    path = tmp_path / "Cortai.exe"
    path.write_bytes(b"MZ versao velha")
    return path


def test_apply_troca_arquivos_e_inicia_novo(monkeypatch, exe):
    monkeypatch.setattr(httpx, "stream", mock.Mock(side_effect=lambda *a, **k: _download(EXE_BYTES)))
    popen = mock.Mock()
    updater.apply_update(_release(), exe_path=str(exe), popen=popen)
    assert exe.read_bytes() == EXE_BYTES
    assert exe.with_name("Cortai.old.exe").read_bytes() == b"MZ versao velha"
    assert not exe.with_name("Cortai.new.exe").exists()
    args, kwargs = popen.call_args
    assert args[0] == [str(exe)] and kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


@pytest.mark.parametrize("conteudo", [EXE_BYTES[:50], b"PK" + EXE_BYTES[2:]])
def test_apply_descarta_download_invalido(monkeypatch, exe, conteudo):
    monkeypatch.setattr(httpx, "stream", mock.Mock(side_effect=lambda *a, **k: _download(conteudo)))
    popen = mock.Mock()
    with pytest.raises(updater.UpdateError):
        updater.apply_update(_release(), exe_path=str(exe), popen=popen)
    assert exe.read_bytes() == b"MZ versao velha"
    assert not exe.with_name("Cortai.new.exe").exists()
    assert not exe.with_name("Cortai.old.exe").exists()
    popen.assert_not_called()


def test_apply_erro_de_rede_no_download(monkeypatch, exe):
    monkeypatch.setattr(httpx, "stream", mock.Mock(side_effect=httpx.ConnectError("sem rede")))
    with pytest.raises(updater.UpdateError, match="internet"):
        updater.apply_update(_release(), exe_path=str(exe), popen=mock.Mock())
    assert exe.read_bytes() == b"MZ versao velha"
    assert not exe.with_name("Cortai.new.exe").exists()


def test_apply_desfaz_troca_se_novo_nao_inicia(monkeypatch, exe):
    monkeypatch.setattr(httpx, "stream", mock.Mock(side_effect=lambda *a, **k: _download(EXE_BYTES)))
    with pytest.raises(updater.UpdateError):
        updater.apply_update(_release(), exe_path=str(exe), popen=mock.Mock(side_effect=OSError("bloqueado")))
    assert exe.read_bytes() == b"MZ versao velha"
    assert not exe.with_name("Cortai.new.exe").exists()
    assert not exe.with_name("Cortai.old.exe").exists()


def test_apply_recusa_release_sem_exe(exe):
    with pytest.raises(updater.UpdateError):
        updater.apply_update({"version": "9.9.9", "asset_url": None, "asset_size": None}, exe_path=str(exe))
    with pytest.raises(updater.UpdateError):  # URL fora das releases do projeto
        updater.apply_update({**_release(), "asset_url": "https://exemplo.com/Cortai.exe"}, exe_path=str(exe))


def test_cleanup_orphans(tmp_path):
    for nome in ("Cortai.old.exe", "Cortai.new.exe", "Cortai.exe"):
        (tmp_path / nome).write_bytes(b"MZ")
    updater.cleanup_orphans(tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["Cortai.exe"]


def test_rota_apply_troca_e_encerra(monkeypatch, app_module, exe):
    monkeypatch.setattr(updater, "_latest", _release())
    monkeypatch.setattr(updater, "can_self_update", lambda: True)
    monkeypatch.setattr(updater.sys, "executable", str(exe))
    monkeypatch.setattr(httpx, "stream", mock.Mock(side_effect=lambda *a, **k: _download(EXE_BYTES)))
    popen = mock.Mock()
    monkeypatch.setattr(updater.subprocess, "Popen", popen)
    timer = mock.Mock()
    monkeypatch.setattr(app_module.threading, "Timer", timer)
    client = app_module.app.test_client()

    assert client.post("/update/apply").status_code == 400  # sem o cabeçalho
    resp = client.post("/update/apply", headers={"X-Cortai": "1"})
    assert resp.get_json() == {"ok": True}
    assert exe.read_bytes() == EXE_BYTES
    popen.assert_called_once()
    timer.assert_called_once_with(1.0, os._exit, args=(0,))
    timer.return_value.start.assert_called_once()


def test_rota_apply_mostra_erro_sem_encerrar(monkeypatch, app_module, exe):
    monkeypatch.setattr(updater, "_latest", _release())
    monkeypatch.setattr(updater, "can_self_update", lambda: True)
    monkeypatch.setattr(updater.sys, "executable", str(exe))
    monkeypatch.setattr(httpx, "stream", mock.Mock(side_effect=httpx.ConnectError("sem rede")))
    timer = mock.Mock()
    monkeypatch.setattr(app_module.threading, "Timer", timer)
    resp = app_module.app.test_client().post("/update/apply", headers={"X-Cortai": "1"})
    assert resp.status_code == 500 and "internet" in resp.get_json()["error"]
    timer.assert_not_called()
    assert exe.read_bytes() == b"MZ versao velha"


def test_modal_mostra_as_novidades(monkeypatch, app_module):
    monkeypatch.setattr(updater, "_latest", {"version": "9.9.9", "url": "u", "notes": "- **Legendas** novas"})
    html = app_module.app.test_client().get("/").get_data(as_text=True)
    assert 'id="ctUpdateModal"' in html
    assert "<li><strong>Legendas</strong> novas</li>" in html
