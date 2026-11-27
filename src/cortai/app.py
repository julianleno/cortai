from flask import Flask, render_template, request, jsonify, send_file, flash, redirect, url_for
import logging
import os
import sys
import threading
import time
import math
import copy
from datetime import datetime, timezone
from pathlib import Path
from . import __version__, db
from . import templates as template_config
from . import clip_selection
from . import transcription as transcription_module
from . import vocabulary as vocabulary_module
from . import updater
from .config import config
from .core import download_video, render_clip_from_source
from .segmentation import flatten_words, slice_transcription
from .subtitles import CAPTION_FONTS, CAPTION_PRESETS, CAPTION_PRESETS_METADATA, validate_caption_font
import json
import uuid
from urllib.parse import urlparse

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.jinja_env.globals['now'] = lambda: datetime.now(timezone.utc)
app.jinja_env.globals['app_version'] = __version__
logger = logging.getLogger(__name__)

db.init_db(config.DB_PATH, recover_orphans=True)

@app.before_request
def _ensure_db():
    if app.config.get('TESTING'):
        db.init_db(config.DB_PATH)

@app.context_processor
def _inject_update():
    # Só lê o que a checagem em segundo plano já guardou; nunca consulta a rede no render.
    return {'update': updater.available(), 'update_self': updater.can_self_update()}

def _render_kwargs_for_template(template_id, caption_preset_override=None, casing_mode_override=None,
                                caption_font_override=None, layout_override=None):
    kwargs = {}
    if template_id:
        template = db.get_template(config.DB_PATH, template_id)
        if template is not None:
            validated = template_config.validate_template_config(template['config'])
            kwargs = template_config.render_kwargs_from_template(validated)
    if caption_preset_override and caption_preset_override in CAPTION_PRESETS:
        kwargs['caption_preset'] = caption_preset_override
    if casing_mode_override and casing_mode_override in template_config.CASING_MODES:
        kwargs['casing_mode'] = casing_mode_override
    if caption_font_override:
        kwargs['caption_font'] = validate_caption_font(caption_font_override)
    if layout_override:
        if layout_override not in template_config.LAYOUTS:
            raise ValueError(f"layout invalido: {layout_override!r}")
        kwargs['layout'] = layout_override
    return kwargs


def _template_for_render(template_id):
    if not template_id:
        return template_config.DEFAULT_TEMPLATE_CONFIG
    template = db.get_template(config.DB_PATH, template_id)
    if template is None:
        raise ValueError("Template selecionado nao foi encontrado")
    return template_config.validate_template_config(template['config'])


def _render_options_from_form(form, template_id=None):
    overrides = {}
    for field in ('layout', 'caption_preset', 'caption_font', 'casing_mode'):
        if field in form:
            value = form.get(field, '').strip()
            if value:
                overrides[field] = value
    return template_config.RenderOptions.from_template_config(
        _template_for_render(template_id),
        aspect_ratio=form.get('aspect_ratio', '9:16').strip(),
        overrides=overrides,
        source_template_id=template_id,
    )


def _stored_render_options(clip=None, job=None):
    payload = (clip or {}).get('render_options') or (job or {}).get('render_options')
    if payload:
        return template_config.RenderOptions.from_dict(payload)
    template_id = (clip or {}).get('template_id')
    return template_config.RenderOptions.from_template_config(
        _template_for_render(template_id), source_template_id=template_id)


def _derive_terminal_job_status(rendered_count, expected_count):
    if expected_count > 0 and rendered_count == expected_count:
        return 'completed'
    if rendered_count > 0:
        return 'partial'
    return 'failed'


def _finish_job_from_clips(job_id, expected_count=None, extra_error=None):
    clips = db.list_clips_for_job(config.DB_PATH, job_id)
    rendered_count = sum(clip['status'] == 'rendered' for clip in clips)
    failed = [clip for clip in clips if clip['status'] == 'failed']
    expected_count = len(clips) if expected_count is None else expected_count
    status_value = _derive_terminal_job_status(rendered_count, expected_count)
    errors = [clip.get('error') for clip in failed if clip.get('error')]
    if extra_error:
        errors.append(str(extra_error))
    error = None
    if status_value == 'partial':
        error = f"{rendered_count} de {expected_count} clipes foram renderizados."
    elif status_value == 'failed':
        error = f"Nenhum dos {expected_count} clipes foi renderizado."
    if error and errors:
        error = f"{error} {errors[0]}"
    db.update_job(config.DB_PATH, job_id, status=status_value, progress=100, error=error)
    return status_value

import werkzeug.utils

ALLOWED_UPLOAD_EXTENSIONS = {'.mp4', '.mkv', '.mov', '.avi', '.webm'}

def _check_job_control_state(job_id) -> bool:
    """Retorna True se o processamento pode continuar, ou False se o job foi cancelado.
    Se o job estiver pausado, aguarda até que o status mude para processing ou cancelled."""
    while True:
        job = db.get_job(config.DB_PATH, job_id)
        if not job or job['status'] == 'cancelled':
            logger.info("Job %s foi cancelado pelo usuario.", job_id)
            return False
        if job['status'] == 'paused':
            time.sleep(1.0)
            continue
        return True

def process_suggestion_job(job_id, url, min_duration, max_duration, num_clips, language, template_id, vocabulary=None, caption_preset=None, casing_mode=None):
    """Fase 7: download once, transcribe the full video once (cached), ask
    for automatic clip suggestions (Fase 3, with local fallback), then render
    each suggested clip using the chosen template (Fase 6). `vocabulary`
    (Fase 9) auto-corrects misheard acronyms/proper nouns in the captions."""
    try:
        if not _check_job_control_state(job_id): return
        db.update_job(config.DB_PATH, job_id, status='processing', progress=10)

        # Check if caller already supplied a local video file (e.g. upload)
        existing_job = db.get_job(config.DB_PATH, job_id)
        source_video_path = existing_job.get('source_video_path') if existing_job else None
        language = (existing_job or {}).get('language') or language
        render_options = (existing_job or {}).get('render_options')
        if render_options:
            normalized_options = template_config.RenderOptions.from_dict(render_options)
        else:
            legacy_overrides = {
                key: value for key, value in {
                    'caption_preset': caption_preset,
                    'casing_mode': casing_mode,
                }.items() if value
            }
            normalized_options = template_config.RenderOptions.from_template_config(
                _template_for_render(template_id), overrides=legacy_overrides,
                source_template_id=template_id,
            )
            db.update_job(
                config.DB_PATH, job_id, render_options=normalized_options.to_dict(),
                language=language,
            )

        if source_video_path and os.path.exists(source_video_path):
            video_path = source_video_path
        else:
            logger.info("Fase 7 suggestion job: baixando video %s", url)
            video_path = download_video(url)
            db.update_job(config.DB_PATH, job_id, source_video_path=video_path)

        if not _check_job_control_state(job_id): return
        db.update_job(config.DB_PATH, job_id, progress=30)
        logger.info("Fase 7 suggestion job: transcrevendo video %s (cache habilitado)", video_path)
        full_transcription = transcription_module.transcribe_video_file(
            video_path, config.DB_PATH, language=language)
        if full_transcription.get('has_audio') is False:
            db.update_job(
                config.DB_PATH, job_id, status='failed', progress=100,
                error='O video nao possui faixa de audio; a selecao automatica de cortes exige audio.',
            )
            return

        if not _check_job_control_state(job_id): return
        db.update_job(config.DB_PATH, job_id, progress=40)
        logger.info("Fase 7 suggestion job: gerando %d sugestoes de clipes (%ss - %ss)",
                    num_clips, min_duration, max_duration)
        suggestions = clip_selection.suggest_clips_for_video(
            full_transcription, min_duration=min_duration, max_duration=max_duration,
            max_clips=num_clips, language=language,
            gemini_api_key=config.GEMINI_API_KEY, nvidia_api_key=config.NVIDIA_NIM_API_KEY,
            gemini_model=config.GEMINI_MODEL, nvidia_model=config.NVIDIA_NIM_MODEL,
        )

        if not suggestions:
            db.update_job(config.DB_PATH, job_id, status='failed', progress=100,
                          error='Nenhum trecho valido foi encontrado neste video para a duracao pedida.')
            return

        if not _check_job_control_state(job_id): return
        db.update_job(config.DB_PATH, job_id, progress=50)
        render_kwargs = normalized_options.to_render_kwargs()

        total = len(suggestions)
        for index, suggestion in enumerate(suggestions):
            if not _check_job_control_state(job_id): return
            clip_id = str(uuid.uuid4())
            result_filename = f"clip_{job_id}_{index + 1}.mp4"
            output_dir = config.OUTPUT_DIR
            os.makedirs(output_dir, exist_ok=True)

            db.create_clip(
                config.DB_PATH, clip_id, job_id, result_filename,
                os.path.join(output_dir, result_filename),
                render_options=normalized_options.to_dict(),
                crop_x_pct=normalized_options.crop_x_pct,
            )
            db.update_clip(
                config.DB_PATH, clip_id,
                start_seconds=suggestion["start"],
                end_seconds=suggestion["end"],
                title=suggestion.get("title"),
                hook=suggestion.get("hook"),
                justificativa=suggestion.get("justificativa"),
                clip_score=suggestion.get("clip_score") or suggestion.get("score"),
                source=suggestion.get("source"),
                status="processing",
                template_id=template_id,
            )

            logger.info("Fase 7 suggestion job: renderizando clipe %d/%d (id=%s, start=%.1f, end=%.1f)",
                        index + 1, total, clip_id, suggestion["start"], suggestion["end"])
            try:
                final_path = render_clip_from_source(
                    video_path, full_transcription,
                    suggestion["start"], suggestion["end"],
                    output_dir, result_filename,
                    vocabulary=vocabulary,
                    **render_kwargs,
                )
                db.update_clip(config.DB_PATH, clip_id, status='rendered', result_path=final_path)
            except Exception as e:
                logger.error("Erro renderizando clipe %s: %s", clip_id, e, exc_info=True)
                db.update_clip(config.DB_PATH, clip_id, status='failed', error=str(e))

            progress = 50 + int(((index + 1) / total) * 50)
            db.update_job(config.DB_PATH, job_id, progress=progress)

        if _check_job_control_state(job_id):
            _finish_job_from_clips(job_id, expected_count=total)
    except Exception as e:
        logger.error(f"Error processing suggestion job {job_id}: {e}", exc_info=True)
        job = db.get_job(config.DB_PATH, job_id)
        if job and job['status'] not in ('cancelled', 'interrupted'):
            clips = db.list_clips_for_job(config.DB_PATH, job_id)
            if clips:
                _finish_job_from_clips(
                    job_id, expected_count=locals().get('total') or len(clips), extra_error=e)
            else:
                db.update_job(config.DB_PATH, job_id, status='failed', progress=100, error=str(e))

def rerender_clip_job(clip_id, video_path, template_id):
    """Re-renders a single clip with a different template. Reuses the
    already-downloaded video and the cached full-video transcription --
    never re-downloads or re-transcribes. Also reapplies the job's
    vocabulary (Fase 9) and any manual word overrides saved for this clip."""
    try:
        clip = db.get_clip(config.DB_PATH, clip_id)
        job = db.get_job(config.DB_PATH, clip['job_id'])
        full_transcription = transcription_module.transcribe_video_file(
            video_path, config.DB_PATH, language=(job or {}).get('language'))
        current_options = _stored_render_options(clip, job)
        if template_id:
            template = db.get_template(config.DB_PATH, template_id)
            if template is None:
                raise ValueError('Template selecionado nao foi encontrado')
            current_options = current_options.with_template_config(
                template['config'], source_template_id=template_id)
        render_kwargs = current_options.to_render_kwargs()

        output_filename = f"clip_{clip_id}_{int(time.time())}.mp4"
        result_path = render_clip_from_source(
            video_path, full_transcription, clip['start_seconds'], clip['end_seconds'],
            config.OUTPUT_DIR, output_filename,
            vocabulary=job['vocabulary'] if job else None,
            word_overrides=clip['word_overrides'],
            **render_kwargs,
        )
        db.update_clip(
            config.DB_PATH, clip_id, status='rendered',
            result_file=output_filename, result_path=result_path,
            template_id=current_options.source_template_id,
            render_options=current_options.to_dict(),
            crop_x_pct=current_options.crop_x_pct, error=None,
        )
    except Exception as e:
        logger.exception("Falha ao re-renderizar clipe")
        db.update_clip(config.DB_PATH, clip_id, status='failed', error=str(e))

@app.route('/')
def index():
    templates_available = db.list_templates(config.DB_PATH)
    # Mesma resolução de chaves que clip_selection.suggest_clips_for_video (banco, depois .env).
    ai_enabled = any(db.get_setting(config.DB_PATH, key, fallback) for key, fallback in (
        ("gemini_api_key", config.GEMINI_API_KEY),
        ("openai_api_key", os.getenv("OPENAI_API_KEY")),
        ("anthropic_api_key", os.getenv("ANTHROPIC_API_KEY")),
        ("nvidia_nim_api_key", config.NVIDIA_NIM_API_KEY),
    ))
    return render_template(
        'index.html', templates=templates_available,
        caption_presets=CAPTION_PRESETS_METADATA, caption_fonts=CAPTION_FONTS,
        ai_enabled=ai_enabled,
    )

@app.route('/projects')
def projects():
    jobs = db.list_jobs(config.DB_PATH, limit=50)
    for job in jobs:
        job['clips'] = db.list_clips_for_job(config.DB_PATH, job['id'])
    return render_template('projects.html', jobs=jobs)

@app.route('/submit', methods=['POST'])
def submit():
    urls_raw = request.form.get('urls', request.form.get('url', '')).strip()
    template_id = request.form.get('template_id', '').strip() or None
    language = request.form.get('language', '').strip() or None
    caption_preset = request.form.get('caption_preset', '').strip() or None
    casing_mode = request.form.get('casing_mode', 'uppercase').strip() or None

    try:
        render_options = _render_options_from_form(request.form, template_id=template_id)
    except (ValueError, TypeError) as exc:
        flash(f'Opcoes de renderizacao invalidas: {exc}', 'error')
        return redirect(url_for('index'))

    if language and (len(language) > 12 or not language.replace('-', '').isalpha()):
        flash('Idioma invalido', 'error')
        return redirect(url_for('index'))

    uploaded_files = request.files.getlist('video_files')
    if not uploaded_files or not uploaded_files[0].filename:
        single_file = request.files.get('video_file')
        if single_file and single_file.filename:
            uploaded_files = [single_file]
        else:
            uploaded_files = []

    aspect_ratio = render_options.aspect_ratio
    max_allowed_duration = 300.0 if aspect_ratio == '16:9' else 60.0

    try:
        min_duration = float(request.form.get('min_duration', '15'))
        max_duration = float(request.form.get('max_duration', str(int(max_allowed_duration))))
        num_clips = int(request.form.get('num_clips', '3'))
        if min_duration <= 0 or max_duration <= min_duration or max_duration > max_allowed_duration:
            limit_msg = "5 minutos (300s)" if aspect_ratio == '16:9' else "1 minuto (60s)"
            flash(f'Duração inválida. Para o formato {aspect_ratio}, a duração máxima é de {limit_msg}.', 'error')
            return redirect(url_for('index'))
        if not (1 <= num_clips <= 10):
            flash('Quantidade de clipes deve ser entre 1 e 10', 'error')
            return redirect(url_for('index'))
    except ValueError:
        flash('Valores inválidos para duração ou quantidade de clipes', 'error')
        return redirect(url_for('index'))

    tasks = []

    # 1. Uploaded files
    for vf in uploaded_files:
        if vf and vf.filename:
            filename = werkzeug.utils.secure_filename(vf.filename)
            ext = os.path.splitext(filename)[1].lower()
            if ext not in ALLOWED_UPLOAD_EXTENSIONS:
                flash(f"Formato de vídeo não suportado ({ext}). Use: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}", 'error')
                return redirect(url_for('index'))
            os.makedirs(config.YTDLP_OUTDIR, exist_ok=True)
            unique_name = f"local_{uuid.uuid4().hex[:8]}_{filename}"
            saved_path = os.path.join(config.YTDLP_OUTDIR, unique_name)
            vf.save(saved_path)
            tasks.append({"url": f"file://{filename}", "source_video_path": saved_path})

    # 2. Line-by-line URLs
    if urls_raw:
        lines = [line.strip() for line in urls_raw.splitlines() if line.strip()]
        if len(lines) > 10:
            flash('Envie no maximo 10 URLs por lote', 'error')
            return redirect(url_for('index'))
        for u in lines:
            parsed = urlparse(u)
            hostname = (parsed.hostname or '').lower()
            if parsed.scheme in ('http', 'https') and hostname in (
                'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'
            ):
                tasks.append({"url": u, "source_video_path": None})

    if len(tasks) > 10:
        flash('Envie no maximo 10 videos por lote', 'error')
        return redirect(url_for('index'))

    if not tasks:
        flash('Informe pelo menos uma URL válida do YouTube ou selecione arquivos de vídeo', 'error')
        return redirect(url_for('index'))

    form_vocabulary = vocabulary_module.parse_vocabulary_input(request.form.get('vocabulary', ''))
    template_vocabulary = []
    if template_id:
        template = db.get_template(config.DB_PATH, template_id)
        if template is not None:
            template_vocabulary = template['config'].get('vocabulary', [])
    vocabulary = vocabulary_module.parse_vocabulary_input(",".join(template_vocabulary + form_vocabulary))

    created_job_ids = []
    for task in tasks:
        job_id = str(uuid.uuid4())
        db.create_job(
            config.DB_PATH, job_id, task["url"], str(min_duration), str(max_duration),
            language=language, render_options=render_options.to_dict(),
        )
        db.update_job(config.DB_PATH, job_id, vocabulary_json=json.dumps(vocabulary))
        if task["source_video_path"]:
            db.update_job(config.DB_PATH, job_id, source_video_path=task["source_video_path"])

        thread = threading.Thread(
            target=process_suggestion_job,
            args=(job_id, task["url"], min_duration, max_duration, num_clips, language, template_id, vocabulary, caption_preset, casing_mode),
        )
        thread.daemon = True
        thread.start()
        created_job_ids.append(job_id)

    if len(created_job_ids) > 1:
        flash(f'Lote de {len(created_job_ids)} vídeos iniciado com sucesso! Acompanhe a lista de projetos.', 'success')
        return redirect(url_for('projects'))
    else:
        return redirect(url_for('status', job_id=created_job_ids[0]))

@app.route('/status/<job_id>')
def status(job_id):
    job = db.get_job(config.DB_PATH, job_id)
    if job is None:
        flash('Job not found', 'error')
        return redirect(url_for('index'))

    clips = db.list_clips_for_job(config.DB_PATH, job_id)
    templates_available = db.list_templates(config.DB_PATH)
    rendered_count = sum(clip['status'] == 'rendered' for clip in clips)
    return render_template(
        'status.html', job=job, clips=clips, templates=templates_available,
        rendered_count=rendered_count,
    )

@app.route('/jobs/<job_id>/pause', methods=['POST'])
def pause_job(job_id):
    job = db.get_job(config.DB_PATH, job_id)
    if job and job['status'] in ('queued', 'processing'):
        db.update_job(config.DB_PATH, job_id, status='paused')
        flash('Processamento pausado com sucesso.', 'info')
    return redirect(url_for('status', job_id=job_id))

@app.route('/jobs/<job_id>/resume', methods=['POST'])
def resume_job(job_id):
    job = db.get_job(config.DB_PATH, job_id)
    if job and job['status'] == 'paused':
        db.update_job(config.DB_PATH, job_id, status='processing')
        flash('Processamento retomado com sucesso!', 'success')
    return redirect(url_for('status', job_id=job_id))

@app.route('/jobs/<job_id>/cancel', methods=['POST'])
def cancel_job(job_id):
    job = db.get_job(config.DB_PATH, job_id)
    if job:
        db.update_job(config.DB_PATH, job_id, status='cancelled')
        flash('Projeto cancelado. O historico foi preservado.', 'info')
    return redirect(url_for('projects'))

@app.route('/jobs/<job_id>/delete', methods=['POST'])
def delete_job_route(job_id):
    job = db.get_job(config.DB_PATH, job_id)
    if job:
        db.delete_job(config.DB_PATH, job_id)
        flash('Projeto excluído com sucesso.', 'info')
    return redirect(url_for('projects'))

@app.route('/api/status/<job_id>')
def api_status(job_id):
    job = db.get_job(config.DB_PATH, job_id)
    if job is None:
        return jsonify({'error': 'Job not found'}), 404

    clips = db.list_clips_for_job(config.DB_PATH, job_id)
    response = {
        'id': job['id'],
        'status': job['status'],
        'progress': job['progress'],
        'clips': [
            {'id': c['id'], 'status': c['status'], 'title': c['title'], 'clip_score': c['clip_score']}
            for c in clips
        ],
        'rendered_count': sum(c['status'] == 'rendered' for c in clips),
        'failed_count': sum(c['status'] == 'failed' for c in clips),
    }
    if job['status'] in ('partial', 'failed', 'interrupted'):
        response['error'] = job.get('error') or 'Unknown error'

    return jsonify(response)

@app.route('/clips/<clip_id>/download')
def download_clip(clip_id):
    clip = db.get_clip(config.DB_PATH, clip_id)
    if clip is None:
        flash('Clipe não encontrado', 'error')
        return redirect(url_for('index'))

    if clip['status'] != 'rendered' or not clip['result_path'] or not os.path.exists(clip['result_path']):
        flash('Clipe ainda não está pronto', 'error')
        return redirect(url_for('status', job_id=clip['job_id']))

    return send_file(clip['result_path'], as_attachment=True)

@app.route('/clips/<clip_id>/rerender', methods=['POST'])
def rerender_clip(clip_id):
    clip = db.get_clip(config.DB_PATH, clip_id)
    if clip is None:
        flash('Clipe não encontrado', 'error')
        return redirect(url_for('index'))

    job = db.get_job(config.DB_PATH, clip['job_id'])
    if job is None or not job.get('source_video_path') or not os.path.exists(job['source_video_path']):
        flash('Vídeo original não está mais disponível para re-renderizar', 'error')
        return redirect(url_for('status', job_id=clip['job_id']))

    template_id = request.form.get('template_id', '').strip() or None
    update_fields = {'status': 'processing', 'template_id': template_id, 'error': None}
    if template_id is None and job.get('render_options'):
        original_options = template_config.RenderOptions.from_dict(job['render_options'])
        update_fields.update(
            render_options=original_options.to_dict(),
            crop_x_pct=original_options.crop_x_pct,
        )
    db.update_clip(config.DB_PATH, clip_id, **update_fields)

    thread = threading.Thread(target=rerender_clip_job, args=(clip_id, job['source_video_path'], template_id))
    thread.daemon = True
    thread.start()

    flash('Re-renderizando clipe com o template escolhido...', 'success')
    return redirect(url_for('status', job_id=clip['job_id']))

@app.route('/clips/<clip_id>/adjust-crop', methods=['POST'])
def adjust_crop(clip_id):
    clip = db.get_clip(config.DB_PATH, clip_id)
    if clip is None:
        flash('Clipe não encontrado', 'error')
        return redirect(url_for('index'))

    job = db.get_job(config.DB_PATH, clip['job_id'])
    if job is None or not job.get('source_video_path') or not os.path.exists(job['source_video_path']):
        flash('Vídeo original não está mais disponível', 'error')
        return redirect(url_for('status', job_id=clip['job_id']))

    try:
        crop_x_pct = float(request.form.get('crop_x_pct', 50))
        if not math.isfinite(crop_x_pct) or not 0 <= crop_x_pct <= 100:
            raise ValueError
    except (TypeError, ValueError):
        flash('Posicao do crop deve estar entre 0 e 100', 'error')
        return redirect(url_for('status', job_id=clip['job_id']))

    render_options = _stored_render_options(clip, job).with_crop(crop_x_pct)
    db.update_clip(
        config.DB_PATH, clip_id, status='processing', error=None,
        crop_x_pct=crop_x_pct, render_options=render_options.to_dict(),
    )

    def crop_adjust_job():
        try:
            full_transcription = transcription_module.transcribe_video_file(
                job['source_video_path'], config.DB_PATH, language=job.get('language'))
            render_kwargs = render_options.to_render_kwargs()
            
            output_dir = os.path.dirname(clip['result_path']) if clip.get('result_path') else config.OUTPUT_DIR
            output_filename = f"clip_{clip_id}_crop_{int(time.time())}.mp4"
            
            final_path = render_clip_from_source(
                job['source_video_path'], full_transcription,
                clip['start_seconds'], clip['end_seconds'],
                output_dir, output_filename,
                vocabulary=job.get('vocabulary', []),
                word_overrides=clip.get('word_overrides', {}),
                **render_kwargs,
            )
            db.update_clip(config.DB_PATH, clip_id, status='rendered', result_path=final_path, result_file=os.path.basename(final_path))
        except Exception as e:
            logger.error(f"Error adjusting crop for clip {clip_id}: {e}", exc_info=True)
            db.update_clip(config.DB_PATH, clip_id, status='failed', error=str(e))

    thread = threading.Thread(target=crop_adjust_job)
    thread.daemon = True
    thread.start()

    flash(f'Ajustando enquadramento do clipe em {int(crop_x_pct)}%...', 'success')
    return redirect(url_for('status', job_id=clip['job_id']))

def _clip_words_for_editing(clip, job):
    """Fase 9: the clip's own transcript words, with vocabulary
    auto-correction and any saved manual overrides already applied -- what
    the edit form should display/pre-fill, plus the vocabulary-only
    baseline used to compute a minimal override diff on save."""
    full_transcription = transcription_module.transcribe_video_file(
        job['source_video_path'], config.DB_PATH, language=job.get('language'))
    sliced = slice_transcription(full_transcription, clip['start_seconds'], clip['end_seconds'])
    vocabulary_corrected = vocabulary_module.apply_vocabulary_corrections(sliced, job['vocabulary'])
    displayed = vocabulary_module.apply_word_overrides(vocabulary_corrected, clip['word_overrides'])
    return flatten_words(vocabulary_corrected), flatten_words(displayed)

@app.route('/clips/<clip_id>/edit-transcript', methods=['GET', 'POST'])
def edit_transcript(clip_id):
    clip = db.get_clip(config.DB_PATH, clip_id)
    if clip is None:
        flash('Clipe não encontrado', 'error')
        return redirect(url_for('index'))

    job = db.get_job(config.DB_PATH, clip['job_id'])
    if job is None or not job.get('source_video_path') or not os.path.exists(job['source_video_path']):
        flash('Vídeo original não está mais disponível para editar a legenda', 'error')
        return redirect(url_for('status', job_id=clip['job_id']))

    baseline_words, displayed_words = _clip_words_for_editing(clip, job)

    if request.method == 'POST':
        overrides = {}
        for index, baseline_word in enumerate(baseline_words):
            submitted = request.form.get(f'word_{index}', '').strip()
            if submitted and submitted != vocabulary_module.word_core(baseline_word['word']):
                overrides[index] = submitted
        try:
            options_payload = _stored_render_options(clip, job).to_dict()
            options_payload['caption_font'] = validate_caption_font(
                request.form.get('caption_font', options_payload['caption_font']))
            edited_options = template_config.RenderOptions.from_dict(options_payload)
        except (ValueError, TypeError) as exc:
            flash(f'Fonte de legenda invalida: {exc}', 'error')
            return redirect(url_for('edit_transcript', clip_id=clip_id))
        db.update_clip(
            config.DB_PATH, clip_id, word_overrides_json=json.dumps(overrides),
            render_options=edited_options.to_dict(),
        )

        db.update_clip(config.DB_PATH, clip_id, status='processing', error=None)
        thread = threading.Thread(
            target=rerender_clip_job, args=(clip_id, job['source_video_path'], None))
        thread.daemon = True
        thread.start()

        flash('Legenda corrigida -- re-renderizando o clipe...', 'success')
        return redirect(url_for('status', job_id=clip['job_id']))

    words_for_display = [
        {"index": i, "text": vocabulary_module.word_core(w["word"]), "start": w["start"], "end": w["end"]}
        for i, w in enumerate(displayed_words)
    ]
    render_options = _stored_render_options(clip, job).to_dict()
    return render_template(
        'edit_transcript.html', clip=clip, words=words_for_display,
        caption_fonts=CAPTION_FONTS, render_options=render_options,
    )

def _template_config_from_form(form, existing_config=None) -> dict:
    cfg = copy.deepcopy(existing_config or template_config.DEFAULT_TEMPLATE_CONFIG)
    for field in ('layout', 'caption_preset', 'caption_font', 'casing_mode', 'title', 'cta'):
        if field in form:
            value = form.get(field, '').strip()
            cfg[field] = value or None if field in ('title', 'cta') else value
    if 'vocabulary' in form:
        cfg['vocabulary'] = vocabulary_module.parse_vocabulary_input(form.get('vocabulary', ''))

    watermark = cfg.setdefault('watermark', {})
    watermark['enabled'] = form.get('watermark_enabled') == 'on'
    for field, cast in (
        ('watermark_position', str), ('watermark_opacity', float),
        ('watermark_margin_x', int), ('watermark_margin_y', int),
    ):
        if field in form:
            watermark[field.removeprefix('watermark_')] = cast(form.get(field))
    if 'watermark_width' in form:
        value = form.get('watermark_width', '').strip()
        watermark['width'] = int(value) if value else None

    export = cfg.setdefault('export', {})
    for form_field, config_field, cast in (
        ('export_vcodec', 'vcodec', str), ('export_preset', 'preset', str),
        ('export_crf', 'crf', int),
    ):
        if form_field in form:
            export[config_field] = cast(form.get(form_field))
    return cfg

def _template_form_context(template=None):
    cfg = template_config.validate_template_config(template['config']) if template else template_config.DEFAULT_TEMPLATE_CONFIG
    return dict(
        template=template,
        cfg=cfg,
        caption_presets=list(CAPTION_PRESETS),
        caption_presets_metadata=CAPTION_PRESETS_METADATA,
        caption_fonts=CAPTION_FONTS,
        layouts=template_config.LAYOUTS,
        positions=template_config.WATERMARK_POSITIONS,
        export_presets=template_config.EXPORT_PRESETS,
        export_vcodecs=template_config.EXPORT_VCODECS,
        defaults=template_config.DEFAULT_TEMPLATE_CONFIG,
    )

@app.route('/templates')
def templates_list():
    return render_template('templates_list.html', templates=db.list_templates(config.DB_PATH))

@app.route('/templates/new', methods=['GET', 'POST'])
def templates_new():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Informe um nome para o template', 'error')
            return redirect(url_for('templates_new'))
        try:
            cfg = template_config.validate_template_config(_template_config_from_form(request.form))
        except (ValueError, TypeError) as e:
            flash(f'Configuração inválida: {e}', 'error')
            return redirect(url_for('templates_new'))
        db.create_template(config.DB_PATH, str(uuid.uuid4()), name, cfg)
        flash('Template criado com sucesso', 'success')
        return redirect(url_for('templates_list'))

    return render_template('template_form.html', **_template_form_context())

@app.route('/templates/<template_id>/edit', methods=['GET', 'POST'])
def templates_edit(template_id):
    tpl = db.get_template(config.DB_PATH, template_id)
    if tpl is None:
        flash('Template não encontrado', 'error')
        return redirect(url_for('templates_list'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Informe um nome para o template', 'error')
            return redirect(url_for('templates_edit', template_id=template_id))
        try:
            cfg = template_config.validate_template_config(
                _template_config_from_form(request.form, existing_config=tpl['config']))
        except (ValueError, TypeError) as e:
            flash(f'Configuração inválida: {e}', 'error')
            return redirect(url_for('templates_edit', template_id=template_id))
        db.update_template(config.DB_PATH, template_id, name=name, config=cfg)
        flash('Template atualizado com sucesso', 'success')
        return redirect(url_for('templates_list'))

    return render_template('template_form.html', **_template_form_context(tpl))

@app.route('/templates/<template_id>/delete', methods=['POST'])
def templates_delete(template_id):
    db.delete_template(config.DB_PATH, template_id)
    flash('Template removido', 'success')
    return redirect(url_for('templates_list'))

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/settings', methods=['GET', 'POST'])
def settings_route():
    if request.method == 'POST':
        for key in ('gemini_api_key', 'openai_api_key', 'anthropic_api_key', 'nvidia_nim_api_key', 'ytdlp_cookies_browser'):
            val = request.form.get(key, '').strip()
            db.set_setting(config.DB_PATH, key, val)
        flash('Configurações salvas com sucesso!', 'success')
        return redirect(url_for('settings_route'))

    settings_data = db.get_all_settings(config.DB_PATH)
    return render_template('settings.html', settings=settings_data)

@app.route('/update/apply', methods=['POST'])
def update_apply():
    if request.headers.get('X-Cortai') != '1':  # barra POST vindo de outro site
        return jsonify(ok=False, error='Pedido inválido.'), 400
    release = updater.available()
    if release is None or not updater.can_self_update():
        return jsonify(ok=False, error='Não há atualização para instalar por aqui.'), 400
    try:
        updater.apply_update(release)
    except updater.UpdateError as e:
        return jsonify(ok=False, error=str(e)), 500
    # A versão nova já foi iniciada; encerra esta depois de responder (fecha a janela junto).
    threading.Timer(1.0, os._exit, args=(0,)).start()
    return jsonify(ok=True)

def main():
    if not app.testing:
        updater.start_background_check(config.DB_PATH)
    # Ensure directories exist
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    os.makedirs(config.TEMP_DIR, exist_ok=True)
    os.makedirs(config.YTDLP_OUTDIR, exist_ok=True)

    _wait_port_free(config.FLASK_HOST, config.FLASK_PORT)
    url = f"http://{config.FLASK_HOST}:{config.FLASK_PORT}"
    webview = _load_webview()
    if webview:
        _run_in_window(webview, url)
        return

    if getattr(sys, 'frozen', False):
        import webbrowser
        def _open_browser():
            time.sleep(1.2)
            webbrowser.open(url)
        threading.Thread(target=_open_browser, daemon=True).start()

    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, debug=config.FLASK_DEBUG)


def _wait_port_free(host: str, port: int, timeout: float = 10.0) -> None:
    """Logo após uma atualização, a versão antiga ainda segura a porta por ~1 s; espera ela soltar."""
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                pass  # alguém ainda responde na porta
        except OSError:
            return
        time.sleep(0.25)


def _load_webview():
    """Janela própria (pywebview) em vez do navegador. CORTAI_JANELA=0 força o modo servidor."""
    if os.getenv('CORTAI_JANELA', '1') == '0':
        return None
    try:
        import webview
    except ImportError:
        return None
    return webview


def _run_in_window(webview, url: str) -> None:
    import socket

    server = threading.Thread(
        target=lambda: app.run(host=config.FLASK_HOST, port=config.FLASK_PORT,
                               debug=False, use_reloader=False),
        daemon=True,
    )
    server.start()
    # Espera o servidor aceitar conexões antes de abrir a janela, senão ela mostra erro.
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with socket.create_connection((config.FLASK_HOST, config.FLASK_PORT), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)

    webview.settings['ALLOW_DOWNLOADS'] = True  # botão "Baixar" dos clipes
    if sys.platform == 'win32':
        # Identidade própria na barra de tarefas; sem isso o Windows agrupa com o python.exe e usa o ícone dele.
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Cortai.App')
    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', 'cortai.ico')
    webview.create_window('Cortaí', url, width=1320, height=880, min_size=(900, 640))
    webview.start(icon=icon)  # bloqueia até a janela fechar; o servidor (daemon) encerra junto


if __name__ == '__main__':
    main()
