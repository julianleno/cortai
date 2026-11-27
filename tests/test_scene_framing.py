"""Enquadramento por cena no layout "Segue o rosto" (issue #2)."""
import os
import tempfile

import ffmpeg

from cortai import face_tracking


def _janela(start, end, x, fallback=False):
    return {"start": start, "end": end, "x": x, "y": 0, "width": 608, "height": 1080,
            "source_width": 1920, "source_height": 1080, "faces_detected": 0 if fallback else 4,
            "used_fallback": fallback, "median_face_height_ratio": 0.1}


def test_corte_so_acima_do_limiar():
    distancias = [(0.5, 0.04), (1.0, 0.05), (2.0, 0.58), (3.0, 0.03), (4.5, 0.55)]
    assert face_tracking.find_scene_cuts(distancias) == [2.0, 4.5]


def test_ignora_flash_curto():
    # dois picos em menos de MIN_SCENE_SECONDS: só o primeiro vira corte
    distancias = [(2.0, 0.6), (2.2, 0.6), (5.0, 0.6)]
    assert face_tracking.find_scene_cuts(distancias) == [2.0, 5.0]


def test_junta_cenas_com_o_mesmo_enquadramento():
    janelas = [_janela(0, 4, 600), _janela(4, 5, 620), _janela(5, 9, 1200)]
    juntas = face_tracking.merge_similar_windows(janelas)
    assert [(j["start"], j["end"], j["x"]) for j in juntas] == [(0, 5, 600), (5, 9, 1200)]


def test_nao_junta_cena_sem_rosto_com_cena_com_rosto():
    janelas = [_janela(0, 4, 656), _janela(4, 8, 656, fallback=True)]
    assert len(face_tracking.merge_similar_windows(janelas)) == 2


def _video_duas_cenas(pasta):
    caminho = os.path.join(pasta, "duas_cenas.mp4")
    vermelho = ffmpeg.input("color=c=red:s=320x180:r=30:d=2", f="lavfi")
    azul = ffmpeg.input("color=c=blue:s=320x180:r=30:d=2", f="lavfi")
    (ffmpeg.concat(vermelho, azul, v=1, a=0).output(caminho, vcodec="libx264", pix_fmt="yuv420p")
     .overwrite_output().run(capture_stdout=True, capture_stderr=True))
    return caminho


def test_detecta_a_troca_de_cena_num_video_real():
    with tempfile.TemporaryDirectory() as pasta:
        cortes = face_tracking.detect_scene_cuts(_video_duas_cenas(pasta))
    assert len(cortes) == 1 and abs(cortes[0] - 2.0) < 0.1


def test_video_sem_rostos_vira_um_enquadramento_central():
    with tempfile.TemporaryDirectory() as pasta:
        janelas = face_tracking.compute_scene_crop_windows(_video_duas_cenas(pasta))
    # duas cenas, as duas sem rosto e com o mesmo crop central: viram uma janela só
    assert len(janelas) == 1
    assert janelas[0]["used_fallback"] is True
    assert janelas[0]["start"] == 0.0 and abs(janelas[0]["end"] - 4.0) < 0.1


def test_juntar_trechos_mantem_todos_os_quadros_e_o_audio():
    from cortai import core
    with tempfile.TemporaryDirectory() as pasta:
        entrada = os.path.join(pasta, "entrada.mp4")
        video = ffmpeg.input("testsrc=s=640x360:r=30:d=4", f="lavfi")
        audio = ffmpeg.input("sine=f=440:d=4", f="lavfi")
        (ffmpeg.output(video, audio, entrada, vcodec="libx264", acodec="aac", pix_fmt="yuv420p")
         .overwrite_output().run(capture_stdout=True, capture_stderr=True))
        janelas = [
            {"start": 0.0, "end": 1.37, "x": 0, "y": 0, "width": 202, "height": 360},
            {"start": 1.37, "end": 2.9, "x": 438, "y": 0, "width": 202, "height": 360},
            {"start": 2.9, "end": 4.0, "x": 219, "y": 0, "width": 202, "height": 360},
        ]
        saida = os.path.join(pasta, "saida.mp4")
        core.crop_to_vertical_windows(entrada, saida, janelas, 270, 480)

        def quadros(caminho):
            info = ffmpeg.probe(caminho, count_frames=None)
            return int(next(s for s in info["streams"] if s["codec_type"] == "video")["nb_read_frames"])

        info = ffmpeg.probe(saida)
        assert quadros(saida) == quadros(entrada) == 120
        assert any(s["codec_type"] == "audio" for s in info["streams"])
        video_saida = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert (video_saida["width"], video_saida["height"]) == (270, 480)
