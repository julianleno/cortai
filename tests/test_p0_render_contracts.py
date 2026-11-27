"""P0 regression contracts for durable jobs and truthful render controls.

These tests deliberately avoid real background threads, network access,
Whisper model loading, face detection, and FFmpeg rendering.  Media behavior
that needs a real encoder belongs in the slower integration suite; this file
guards the fast orchestration and persistence contracts on every test run.
"""

from __future__ import annotations

import io
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from cortai import app as app_module
from cortai import db
from cortai.face_tracking import compute_positioned_crop_window
from cortai.subtitles import CAPTION_PRESETS, build_ass_subtitle
from cortai.templates import RenderOptions


@pytest.fixture
def app_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Isolate the Flask singleton and SQLite-backed worker in one temp tree."""
    database = tmp_path / "cortai.db"
    output = tmp_path / "output"
    downloads = tmp_path / "downloads"
    temporary = tmp_path / "temp"
    for directory in (output, downloads, temporary):
        directory.mkdir()

    monkeypatch.setattr(app_module.config, "DB_PATH", str(database))
    monkeypatch.setattr(app_module.config, "OUTPUT_DIR", str(output))
    monkeypatch.setattr(app_module.config, "YTDLP_OUTDIR", str(downloads))
    monkeypatch.setattr(app_module.config, "TEMP_DIR", str(temporary))
    monkeypatch.setitem(app_module.app.config, "TESTING", True)
    monkeypatch.setitem(app_module.app.config, "SECRET_KEY", "p0-test-secret")

    db.init_db(str(database))
    return SimpleNamespace(
        db_path=str(database),
        output=output,
        downloads=downloads,
        temporary=temporary,
        client=app_module.app.test_client(),
    )


@pytest.fixture
def spoken_transcript() -> dict:
    return {
        "language": "pt",
        "language_probability": 0.99,
        "duration": 8.0,
        "has_audio": True,
        "text": "um teste",
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 2.0,
                "text": "um teste",
                "words": [
                    {"word": "um", "start": 0.0, "end": 0.8, "probability": 0.99},
                    {"word": "teste", "start": 0.8, "end": 2.0, "probability": 0.99},
                ],
            }
        ],
    }


def _suggestions() -> list[dict]:
    return [
        {
            "start": 0.0,
            "end": 2.0,
            "title": "Primeiro",
            "hook": "Gancho um",
            "justificativa": "Teste um",
            "clip_score": 8.0,
            "source": "local",
        },
        {
            "start": 2.0,
            "end": 4.0,
            "title": "Segundo",
            "hook": "Gancho dois",
            "justificativa": "Teste dois",
            "clip_score": 7.0,
            "source": "local",
        },
    ]


def _create_worker_job(
    env: SimpleNamespace,
    *,
    job_id: str = "p0-worker-job",
    language: str = "pt",
) -> tuple[str, RenderOptions]:
    source = env.downloads / f"{job_id}.mp4"
    source.write_bytes(b"worker media is mocked")
    options = RenderOptions.from_template_config(
        {},
        aspect_ratio="16:9",
        overrides={
            "layout": "center",
            "caption_preset": "yellow-bold",
            "caption_font": "Impact",
            "casing_mode": "titlecase",
        },
    )
    db.create_job(
        env.db_path,
        job_id,
        f"file://{source.name}",
        "1",
        "5",
        language=language,
        render_options=options.to_dict(),
    )
    db.update_job(env.db_path, job_id, source_video_path=str(source))
    return str(source), options


def test_render_options_normalize_16_9_and_reject_invalid_values() -> None:
    options = RenderOptions.from_template_config(
        {},
        aspect_ratio="16:9",
        overrides={
            "layout": "center",
            "caption_preset": "cyan-shadow",
            "caption_font": "Impact",
            "casing_mode": "titlecase",
        },
    )

    assert (options.target_width, options.target_height) == (1920, 1080)
    assert options.aspect_ratio == "16:9"
    assert options.layout == "center"
    assert options.caption_font == "Impact"
    render_kwargs = options.to_render_kwargs()
    assert (render_kwargs["target_width"], render_kwargs["target_height"]) == (1920, 1080)
    assert render_kwargs["layout"] == "center"
    assert render_kwargs["caption_font"] == "Impact"

    invalid_payloads = [
        {"aspect_ratio": "4:3"},
        {"aspect_ratio": "16:9", "target_width": 1080, "target_height": 1920},
        {"layout": "zoom-arbitrario"},
        {"caption_font": "Impact,64\nStyle: Injected"},
        {"crop_x_pct": -0.01},
        {"crop_x_pct": 100.01},
        {"crop_x_pct": float("nan")},
    ]
    for payload in invalid_payloads:
        with pytest.raises(ValueError):
            RenderOptions.from_dict(payload)


def test_db_round_trip_and_restart_recovery_preserve_snapshots(app_env: SimpleNamespace) -> None:
    options = RenderOptions.from_template_config(
        {}, aspect_ratio="16:9", overrides={"caption_font": "Impact"}, crop_x_pct=25
    )
    db.create_job(
        app_env.db_path,
        "recover-me",
        "file://source.mp4",
        "10",
        "30",
        language="pt",
        render_options=options.to_dict(),
    )
    db.update_job(app_env.db_path, "recover-me", status="processing")
    db.create_clip(
        app_env.db_path,
        "preserved-clip",
        "recover-me",
        "clip.mp4",
        str(app_env.output / "clip.mp4"),
        render_options=options.to_dict(),
        crop_x_pct=25,
    )

    db.create_job(
        app_env.db_path,
        "already-done",
        "file://done.mp4",
        "10",
        "30",
        render_options=RenderOptions().to_dict(),
    )
    db.update_job(app_env.db_path, "already-done", status="completed", progress=100)

    db.init_db(app_env.db_path, recover_orphans=True)

    recovered = db.get_job(app_env.db_path, "recover-me")
    clip = db.get_clip(app_env.db_path, "preserved-clip")
    assert recovered["status"] == "interrupted"
    assert "reinicializa" in recovered["error"].lower()
    assert recovered["language"] == "pt"
    assert recovered["render_options"] == options.to_dict()
    assert clip["render_options"] == options.to_dict()
    assert clip["crop_x_pct"] == 25
    assert db.get_job(app_env.db_path, "already-done")["status"] == "completed"


def test_ass_font_override_does_not_mutate_shared_preset(
    tmp_path: Path, spoken_transcript: dict
) -> None:
    impact_ass = tmp_path / "impact.ass"
    default_ass = tmp_path / "default.ass"
    original_font = CAPTION_PRESETS["white-bold"].font_name

    build_ass_subtitle(
        spoken_transcript,
        "white-bold",
        1080,
        1920,
        str(impact_ass),
        font_name="Impact",
    )
    build_ass_subtitle(
        spoken_transcript,
        "white-bold",
        1080,
        1920,
        str(default_ass),
    )

    assert "Style: Default,Impact," in impact_ass.read_text(encoding="utf-8")
    assert f"Style: Default,{original_font}," in default_ass.read_text(encoding="utf-8")
    assert CAPTION_PRESETS["white-bold"].font_name == original_font


@pytest.mark.parametrize(
    ("crop_x_pct", "expected_x"),
    [(0, 0), (50, 656), (100, 1312)],
)
def test_positioned_crop_maps_full_horizontal_travel(
    crop_x_pct: float, expected_x: int
) -> None:
    x, y, width, height = compute_positioned_crop_window(
        1920, 1080, 1080, 1920, crop_x_pct
    )
    assert (x, y, width, height) == (expected_x, 0, 608, 1080)


def test_submit_persists_normalized_snapshot_without_starting_worker(
    app_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    created_threads = []

    class NoStartThread:
        def __init__(self, target=None, args=(), kwargs=None, **_ignored):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}
            self.daemon = False
            self.started = False
            created_threads.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr(app_module.threading, "Thread", NoStartThread)

    response = app_env.client.post(
        "/submit",
        data={
            "input_source": "file",
            "video_file": (io.BytesIO(b"fake mp4"), "source.mp4"),
            "aspect_ratio": "16:9",
            "layout": "center",
            "caption_preset": "yellow-bold",
            "caption_font": "Impact",
            "casing_mode": "titlecase",
            "language": "pt",
            "min_duration": "30",
            "max_duration": "120",
            "num_clips": "2",
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "/status/" in response.headers["Location"]
    jobs = db.list_jobs(app_env.db_path)
    assert len(jobs) == 1
    job = jobs[0]
    options = RenderOptions.from_dict(job["render_options"])
    assert job["language"] == "pt"
    assert options.aspect_ratio == "16:9"
    assert (options.target_width, options.target_height) == (1920, 1080)
    assert options.layout == "center"
    assert options.caption_preset == "yellow-bold"
    assert options.caption_font == "Impact"
    assert options.casing_mode == "titlecase"
    assert Path(job["source_video_path"]).exists()
    assert len(created_threads) == 1
    assert created_threads[0].started is True
    assert created_threads[0].target is app_module.process_suggestion_job


@pytest.mark.parametrize(
    ("outcomes", "expected_job_status", "expected_clip_statuses"),
    [
        ((True, True), "completed", ["rendered", "rendered"]),
        ((True, False), "partial", ["rendered", "failed"]),
        ((False, False), "failed", ["failed", "failed"]),
    ],
)
def test_worker_terminal_state_matches_clip_results_and_forwards_language(
    app_env: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    spoken_transcript: dict,
    outcomes: tuple[bool, bool],
    expected_job_status: str,
    expected_clip_statuses: list[str],
) -> None:
    source, options = _create_worker_job(app_env)
    transcribe = mock.Mock(return_value=spoken_transcript)
    suggest = mock.Mock(return_value=_suggestions())
    outcome_iter = iter(outcomes)

    def render(*_args, **_kwargs):
        if not next(outcome_iter):
            raise RuntimeError("render controlado falhou")
        return str(app_env.output / "rendered.mp4")

    render_mock = mock.Mock(side_effect=render)
    monkeypatch.setattr(app_module.transcription_module, "transcribe_video_file", transcribe)
    monkeypatch.setattr(app_module.clip_selection, "suggest_clips_for_video", suggest)
    monkeypatch.setattr(app_module, "render_clip_from_source", render_mock)

    app_module.process_suggestion_job(
        "p0-worker-job",
        "file://p0-worker-job.mp4",
        1.0,
        5.0,
        2,
        None,
        None,
    )

    job = db.get_job(app_env.db_path, "p0-worker-job")
    clips = db.list_clips_for_job(app_env.db_path, "p0-worker-job")
    assert job["status"] == expected_job_status
    assert job["progress"] == 100
    assert [clip["status"] for clip in clips] == expected_clip_statuses
    assert all(clip["render_options"] == options.to_dict() for clip in clips)
    assert (job["error"] is None) is (expected_job_status == "completed")
    transcribe.assert_called_once_with(source, app_env.db_path, language="pt")
    assert render_mock.call_count == 2
    for call in render_mock.call_args_list:
        assert call.kwargs["target_width"] == 1920
        assert call.kwargs["target_height"] == 1080
        assert call.kwargs["layout"] == "center"
        assert call.kwargs["caption_font"] == "Impact"


def test_silent_worker_fails_before_suggestion_or_render(
    app_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _ = _create_worker_job(app_env, job_id="silent-job", language="en")
    silent = {
        "language": "en",
        "duration": 4.0,
        "segments": [],
        "text": "",
        "has_audio": False,
    }
    transcribe = mock.Mock(return_value=silent)
    suggest = mock.Mock()
    render = mock.Mock()
    monkeypatch.setattr(app_module.transcription_module, "transcribe_video_file", transcribe)
    monkeypatch.setattr(app_module.clip_selection, "suggest_clips_for_video", suggest)
    monkeypatch.setattr(app_module, "render_clip_from_source", render)

    app_module.process_suggestion_job(
        "silent-job", "file://silent-job.mp4", 1.0, 5.0, 2, None, None
    )

    job = db.get_job(app_env.db_path, "silent-job")
    assert job["status"] == "failed"
    assert job["progress"] == 100
    assert "audio" in job["error"].lower()
    assert db.list_clips_for_job(app_env.db_path, "silent-job") == []
    transcribe.assert_called_once_with(source, app_env.db_path, language="en")
    suggest.assert_not_called()
    render.assert_not_called()


def test_partial_job_api_html_and_control_routes_are_consistent(app_env: SimpleNamespace) -> None:
    db.create_job(app_env.db_path, "partial-job", "file://source.mp4", "1", "5")
    db.update_job(
        app_env.db_path,
        "partial-job",
        status="partial",
        progress=100,
        error="1 de 2 clipes foram renderizados.",
    )
    db.create_clip_suggestion(
        app_env.db_path,
        "ok-clip",
        "partial-job",
        0.0,
        2.0,
        "OK",
        "H",
        "J",
        8.0,
        "local",
        status="rendered",
    )
    db.create_clip_suggestion(
        app_env.db_path,
        "bad-clip",
        "partial-job",
        2.0,
        4.0,
        "Falhou",
        "H",
        "J",
        7.0,
        "local",
        status="failed",
    )
    db.update_clip(app_env.db_path, "bad-clip", error="erro controlado")

    api_response = app_env.client.get("/api/status/partial-job")
    payload = api_response.get_json()
    assert api_response.status_code == 200
    assert payload["status"] == "partial"
    assert payload["rendered_count"] == 1
    assert payload["failed_count"] == 1
    assert payload["error"]

    html_response = app_env.client.get("/status/partial-job")
    assert html_response.status_code == 200
    assert "Processamento parcial".encode() in html_response.data
    assert b"1 de 2" in html_response.data

    counts = Counter(rule.rule for rule in app_module.app.url_map.iter_rules())
    duplicates = {rule: count for rule, count in counts.items() if count > 1}
    assert duplicates == {}

    for path in (
        "/jobs/<job_id>/pause",
        "/jobs/<job_id>/resume",
        "/jobs/<job_id>/cancel",
    ):
        rules = [rule for rule in app_module.app.url_map.iter_rules() if rule.rule == path]
        assert len(rules) == 1
        assert "POST" in rules[0].methods
        assert "GET" not in rules[0].methods


def test_adjust_crop_persists_and_forwards_real_percentage(
    app_env: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    spoken_transcript: dict,
) -> None:
    source = app_env.downloads / "crop-source.mp4"
    source.write_bytes(b"render is mocked")
    options = RenderOptions.from_template_config({}, aspect_ratio="9:16")
    db.create_job(
        app_env.db_path, "crop-job", f"file://{source.name}", "1", "5",
        language="pt", render_options=options.to_dict(),
    )
    db.update_job(app_env.db_path, "crop-job", source_video_path=str(source))
    db.create_clip(
        app_env.db_path, "crop-clip", "crop-job", "old.mp4",
        str(app_env.output / "old.mp4"), render_options=options.to_dict(),
    )
    db.update_clip(
        app_env.db_path, "crop-clip", start_seconds=0.0, end_seconds=2.0,
        status="rendered",
    )

    class InlineThread:
        def __init__(self, target=None, args=(), kwargs=None, **_ignored):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}
            self.daemon = False

        def start(self):
            self.target(*self.args, **self.kwargs)

    render = mock.Mock(return_value=str(app_env.output / "cropped.mp4"))
    monkeypatch.setattr(app_module.threading, "Thread", InlineThread)
    monkeypatch.setattr(
        app_module.transcription_module, "transcribe_video_file",
        mock.Mock(return_value=spoken_transcript),
    )
    monkeypatch.setattr(app_module, "render_clip_from_source", render)

    response = app_env.client.post(
        "/clips/crop-clip/adjust-crop", data={"crop_x_pct": "25"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    clip = db.get_clip(app_env.db_path, "crop-clip")
    assert clip["crop_x_pct"] == 25
    assert clip["render_options"]["crop_x_pct"] == 25
    assert render.call_args.kwargs["crop_x_pct"] == 25

    render.reset_mock()
    response = app_env.client.post(
        "/clips/crop-clip/adjust-crop", data={"crop_x_pct": "nan"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert db.get_clip(app_env.db_path, "crop-clip")["crop_x_pct"] == 25
    render.assert_not_called()
