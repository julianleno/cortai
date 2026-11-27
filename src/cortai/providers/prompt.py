"""Builds the minimal prompt sent to external providers.

CLAUDE.md requires sending only transcription, timestamps, and text — never
the video itself, and no more metadata than needed.
"""

CLIP_SUGGESTION_JSON_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "clips": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "start": {"type": "NUMBER"},
                    "end": {"type": "NUMBER"},
                    "title": {"type": "STRING"},
                    "hook": {"type": "STRING"},
                    "justificativa": {"type": "STRING"},
                    "clip_score": {"type": "NUMBER"},
                },
                "required": ["start", "end", "title", "hook", "justificativa", "clip_score"],
            },
        },
    },
    "required": ["clips"],
}


def build_transcript_excerpt(transcription: dict, max_chars: int = 12000) -> str:
    """Segment-level transcript with timestamps, truncated to a sane size.

    Word-level timestamps stay local; the provider only needs segment
    boundaries and text to reason about content.
    """
    lines = []
    for segment in transcription.get("segments", []):
        lines.append(f"[{segment['start']:.2f}-{segment['end']:.2f}] {segment['text'].strip()}")
    excerpt = "\n".join(lines)
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars] + "\n[...transcricao truncada...]"
    return excerpt


def build_suggestion_prompt(transcription: dict, min_duration: float, max_duration: float,
                             max_clips: int, language: str = None) -> str:
    excerpt = build_transcript_excerpt(transcription)
    lang = language or transcription.get("language") or "pt"
    return (
        f"Voce e um editor de video especializado em identificar os melhores momentos de um "
        f"video falado (podcast, entrevista, aula) para virar clipes verticais de redes sociais.\n\n"
        f"Idioma do conteudo: {lang}.\n"
        f"Sugira ate {max_clips} trechos, cada um com duracao entre {min_duration:.0f}s e {max_duration:.0f}s.\n"
        f"Cada trecho deve: ter inicio compreensivel (nao comecar no meio de uma frase), ter um gancho "
        f"inicial forte, funcionar sem contexto excessivo, ter conteudo com informacao, conflito, "
        f"surpresa, humor ou valor, e ter uma conclusao (nao terminar cortado). Nao repita trechos.\n\n"
        f"Transcricao com timestamps em segundos:\n{excerpt}\n\n"
        f"Responda APENAS com JSON no formato solicitado. Cada clipe deve ter: start, end (em segundos, "
        f"aproximados - serao ajustados automaticamente para o limite de frase mais proximo), title "
        f"(titulo curto), hook (gancho inicial), justificativa (por que esse trecho funciona), e "
        f"clip_score (nota editorial de 0 a 10 do potencial do clipe - nunca uma garantia de "
        f"viralizacao, apenas uma estimativa)."
    )
