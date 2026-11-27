import contextlib
import io
import os
import threading
import zipfile
from unittest import mock

import pytest

from cortai import preparo

# Thread real: o download do modelo tem uma thread de monitoramento que só termina no fim.
_RealThread = threading.Thread


@pytest.fixture(autouse=True)
def _estado_limpo(monkeypatch, tmp_path):
    monkeypatch.setattr(preparo, "APP_DIR", tmp_path)
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))  # a instalação mexe no PATH; volta ao normal depois
    for item in ("ffmpeg", "modelo"):
        monkeypatch.setitem(preparo._state, item, {"estado": "desconhecido", "baixado": 0, "total": 0, "erro": None})

    class Sincrona:  # roda as threads na hora, para o teste ver o resultado
        def __init__(self, target, args=(), **_):
            self.target, self.args = target, args
        def start(self):
            self.target(*self.args)
    monkeypatch.setattr(preparo.threading, "Thread", Sincrona)


def _zip_ffmpeg(com_ffprobe=True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ffmpeg-9.1-essentials_build/bin/ffmpeg.exe", b"MZ ffmpeg")
        if com_ffprobe:
            z.writestr("ffmpeg-9.1-essentials_build/bin/ffprobe.exe", b"MZ ffprobe")
        z.writestr("ffmpeg-9.1-essentials_build/README.txt", b"x")
    return buf.getvalue()


def _stream(conteudo: bytes):
    class Resp:
        headers = {"content-length": str(len(conteudo))}
        def raise_for_status(self): pass
        def iter_bytes(self): yield conteudo
    @contextlib.contextmanager
    def fake(*a, **k): yield Resp()
    return fake


def test_needs_setup_respeita_a_variavel(monkeypatch):
    monkeypatch.setattr(preparo, "ffmpeg_ok", lambda: False)
    monkeypatch.setenv("CORTAI_PREPARO", "0")
    assert preparo.needs_setup() is False
    monkeypatch.setenv("CORTAI_PREPARO", "1")
    assert preparo.needs_setup() is True


def test_ffmpeg_local_entra_no_path(monkeypatch, tmp_path):
    pasta = tmp_path / "ffmpeg" / "bin"
    pasta.mkdir(parents=True)
    for nome in preparo.FFMPEG_EXES:
        (pasta / nome).write_bytes(b"MZ")
    monkeypatch.setenv("PATH", "C:\\outro")
    preparo.activate_local_ffmpeg()
    preparo.activate_local_ffmpeg()  # não duplica
    assert os.environ["PATH"].split(os.pathsep) == [str(pasta), "C:\\outro"]


def test_instala_ffmpeg_portatil(monkeypatch, tmp_path):
    monkeypatch.setattr(preparo, "can_install_ffmpeg", lambda: True)
    monkeypatch.setattr(preparo.httpx, "stream", _stream(_zip_ffmpeg()))
    run = mock.Mock(return_value=mock.Mock(returncode=0))
    monkeypatch.setattr(preparo.subprocess, "run", run)
    monkeypatch.setattr(preparo, "ffmpeg_ok", lambda: True)
    preparo.install_ffmpeg("https://exemplo/ffmpeg.zip")
    pasta = tmp_path / "ffmpeg" / "bin"
    assert (pasta / "ffmpeg.exe").read_bytes() == b"MZ ffmpeg"
    assert (pasta / "ffprobe.exe").read_bytes() == b"MZ ffprobe"
    assert not (tmp_path / "ffmpeg" / "ffmpeg-download.zip").exists()  # o zip some
    assert preparo._state["ffmpeg"]["estado"] == "pronto"
    assert run.call_args.args[0][0].endswith("ffmpeg.exe")


def test_pacote_sem_ffprobe_vira_erro_e_limpa(monkeypatch, tmp_path):
    monkeypatch.setattr(preparo, "can_install_ffmpeg", lambda: True)
    monkeypatch.setattr(preparo.httpx, "stream", _stream(_zip_ffmpeg(com_ffprobe=False)))
    preparo.install_ffmpeg("https://exemplo/ffmpeg.zip")
    assert preparo._state["ffmpeg"]["estado"] == "erro"
    assert "ffprobe" in preparo._state["ffmpeg"]["erro"]
    assert not (tmp_path / "ffmpeg" / "bin" / "ffmpeg.exe").exists()


def test_ffmpeg_que_nao_abre_vira_erro(monkeypatch, tmp_path):
    monkeypatch.setattr(preparo, "can_install_ffmpeg", lambda: True)
    monkeypatch.setattr(preparo.httpx, "stream", _stream(_zip_ffmpeg()))
    monkeypatch.setattr(preparo.subprocess, "run", mock.Mock(return_value=mock.Mock(returncode=1)))
    preparo.install_ffmpeg("https://exemplo/ffmpeg.zip")
    assert preparo._state["ffmpeg"]["estado"] == "erro"
    assert not (tmp_path / "ffmpeg" / "bin" / "ffmpeg.exe").exists()


def test_fora_do_windows_nao_instala(monkeypatch):
    monkeypatch.setattr(preparo, "can_install_ffmpeg", lambda: False)
    stream = mock.Mock()
    monkeypatch.setattr(preparo.httpx, "stream", stream)
    preparo.install_ffmpeg()
    stream.assert_not_called()
    assert preparo._state["ffmpeg"]["estado"] == "erro"


def test_baixa_o_modelo(monkeypatch, tmp_path):
    baixar = mock.Mock()
    monkeypatch.setattr("faster_whisper.utils.download_model", baixar)
    monkeypatch.setattr(preparo, "_hf_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(preparo, "_model_total", lambda: 100)
    monkeypatch.setattr(preparo.threading, "Thread", _RealThread)
    preparo.start_model_download()
    _esperar(lambda: preparo._state["modelo"]["estado"] != "baixando")
    assert preparo._state["modelo"]["estado"] == "pronto"
    baixar.assert_called_once()


def test_erro_no_download_do_modelo(monkeypatch, tmp_path):
    monkeypatch.setattr("faster_whisper.utils.download_model", mock.Mock(side_effect=OSError("sem internet")))
    monkeypatch.setattr(preparo, "_hf_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(preparo, "_model_total", lambda: 100)
    monkeypatch.setattr(preparo.threading, "Thread", _RealThread)
    preparo.start_model_download()
    _esperar(lambda: preparo._state["modelo"]["estado"] != "baixando")
    assert preparo._state["modelo"]["estado"] == "erro"
    assert "sem internet" in preparo._state["modelo"]["erro"]


def _esperar(condicao, limite=5.0):
    import time
    fim = time.time() + limite
    while time.time() < fim and not condicao():
        time.sleep(0.02)


# --- rotas ---

@pytest.fixture
def client(monkeypatch):
    import cortai.app as app_module
    monkeypatch.setattr(app_module, "_setup_shown", False)
    app_module.app.testing = True
    return app_module, app_module.app.test_client()


def test_home_leva_para_a_preparacao_uma_vez(monkeypatch, client):
    app_module, c = client
    monkeypatch.setenv("CORTAI_PREPARO", "1")
    monkeypatch.setattr(preparo, "ffmpeg_ok", lambda: False)
    r = c.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/preparar")
    html = c.get("/preparar").get_data(as_text=True)
    assert "Preparando o Cortaí" in html and "Continuar mesmo assim" in html
    assert c.get("/").status_code == 200  # depois de ver a tela, não intercepta de novo


def test_home_sem_preparacao_pendente(monkeypatch, client):
    _, c = client
    monkeypatch.setenv("CORTAI_PREPARO", "1")
    monkeypatch.setattr(preparo, "needs_setup", lambda: False)
    assert c.get("/").status_code == 200


def test_api_exige_cabecalho(monkeypatch, client):
    _, c = client
    instalar = mock.Mock()
    monkeypatch.setattr(preparo, "install_ffmpeg", instalar)
    assert c.post("/api/preparo/ffmpeg").status_code == 400
    instalar.assert_not_called()
    assert c.post("/api/preparo/ffmpeg", headers={"X-Cortai": "1"}).get_json() == {"ok": True}
    instalar.assert_called_once()
    assert c.post("/api/preparo/outro", headers={"X-Cortai": "1"}).status_code == 404


def test_api_status(monkeypatch, client):
    _, c = client
    monkeypatch.setattr(preparo, "ffmpeg_ok", lambda: True)
    monkeypatch.setattr(preparo, "model_ok", lambda: True)
    dados = c.get("/api/preparo").get_json()
    assert dados["pronto"] is True and dados["ffmpeg"]["estado"] == "pronto"
