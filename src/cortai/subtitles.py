"""Legendas estilizadas (Fase 5).

Gera arquivos .ASS (Advanced SubStation Alpha) diretamente -- em vez de SRT
com um unico `force_style` fixo (Fase 0) -- porque so o ASS da acesso as
features realmente necessarias aqui: cores/contorno/sombra por preset via
Style, tags de override inline por palavra (`{\\c...}`) para destacar uma
palavra-chave, e karaoke nativo (`\\kf`) para o destaque progressivo
palavra por palavra. Tudo isso e renderizado pelo libass, ja usado pelo
filtro `subtitles` do FFmpeg.

Presets (inspirados em padroes visuais conhecidos de legendas para redes
sociais, sem copiar a identidade visual de nenhum produto especifico):

1. white-bold             -- branco caixa-alta, contorno preto
2. yellow-bold             -- amarelo forte, contorno preto
3. white-yellow-keyword     -- branco com uma palavra-chave em amarelo
4. white-red-highlight      -- branco com uma expressao destacada (aproximada
                               com contorno/sombra vermelhos grossos, ja que
                               ASS nao permite uma caixa solida por-palavra
                               dentro de uma unica linha/estilo -- ver
                               ADR correspondente em DECISIONS.md)
5. cyan-shadow              -- ciano com sombra preta
6. progressive-word-highlight -- destaque progressivo palavra por palavra
                                 (karaoke ASS nativo)

A "palavra-chave"/"expressao destacada" dos presets 3 e 4 e escolhida por
uma heuristica determinista (a palavra mais longa do trecho) -- nao ha
extracao de palavra-chave por NLP/IA nesta fase.
"""
import logging
import os
import re
from dataclasses import dataclass, replace

from .segmentation import flatten_words

logger = logging.getLogger(__name__)

# ASS Style colors: &HAABBGGRR (alpha, blue, green, red)
_WHITE = "&H00FFFFFF"
_BLACK = "&H00000000"
_YELLOW = "&H0000FFFF"
_CYAN = "&H00FFFF00"
_RED = "&H000000FF"

# ASS inline override colors: &HBBGGRR& (no alpha)
_WHITE_INLINE = "&HFFFFFF&"
_BLACK_INLINE = "&H000000&"
_YELLOW_INLINE = "&H00FFFF&"
_RED_INLINE = "&H0000FF&"

# Exact allowlist for ASS Fontname values exposed by the UI. Keeping this
# closed prevents commas/newlines from escaping the ASS Style field. Whether
# a family is available to libass is a separate packaging/preflight concern.
CAPTION_FONTS = (
    "Arial",
    "Montserrat",
    "Impact",
    "Anton",
    "Bebas Neue",
    "Poppins",
    "Inter",
    "Outfit",
    "Roboto",
)


def validate_caption_font(font_name: str) -> str:
    if font_name not in CAPTION_FONTS:
        raise ValueError(f"Fonte de legenda desconhecida: {font_name!r}. Opcoes: {CAPTION_FONTS}")
    return font_name


@dataclass
class CaptionPreset:
    name: str
    font_name: str = "Arial"
    font_size: int = 64
    bold: bool = True
    uppercase: bool = False
    primary_color: str = _WHITE
    secondary_color: str = _WHITE
    outline_color: str = _BLACK
    back_color: str = _BLACK
    outline_width: float = 3.0
    shadow_depth: float = 1.0
    border_style: int = 1  # 1 = outline+shadow (opaque box is style-wide, not per-word)
    highlight_mode: str | None = None  # None | "keyword_color" | "expression_highlight" | "karaoke"
    highlight_open: str = ""
    highlight_close: str = ""


CAPTION_PRESETS: dict[str, CaptionPreset] = {
    "white-bold": CaptionPreset(
        name="white-bold", uppercase=True, primary_color=_WHITE,
        outline_color=_BLACK, outline_width=3.0, shadow_depth=1.0,
    ),
    "yellow-bold": CaptionPreset(
        name="yellow-bold", primary_color=_YELLOW,
        outline_color=_BLACK, outline_width=3.0, shadow_depth=1.0,
    ),
    "white-yellow-keyword": CaptionPreset(
        name="white-yellow-keyword", primary_color=_WHITE, outline_color=_BLACK,
        outline_width=2.5, shadow_depth=1.0,
        highlight_mode="keyword_color",
        highlight_open="{\\c" + _YELLOW_INLINE + "}",
        highlight_close="{\\c" + _WHITE_INLINE + "}",
    ),
    "white-red-highlight": CaptionPreset(
        name="white-red-highlight", primary_color=_WHITE, outline_color=_BLACK,
        outline_width=2.5, shadow_depth=1.0,
        highlight_mode="expression_highlight",
        highlight_open="{\\3c" + _RED_INLINE + "\\bord6\\shad0}",
        highlight_close="{\\3c" + _BLACK_INLINE + "\\bord2.5\\shad1}",
    ),
    "cyan-shadow": CaptionPreset(
        name="cyan-shadow", primary_color=_CYAN,
        outline_color=_BLACK, outline_width=1.0, shadow_depth=3.0,
    ),
    "progressive-word-highlight": CaptionPreset(
        name="progressive-word-highlight", primary_color=_YELLOW, secondary_color=_WHITE,
        outline_color=_BLACK, outline_width=2.0, shadow_depth=1.0,
        highlight_mode="karaoke",
    ),
}

CAPTION_PRESETS_METADATA = {
    "progressive-word-highlight": {
        "label": "Karaoke Destaque",
        "sample": 'DESTAQUE <span style="color: #ffffff;">PALAVRA</span> <span style="color: #ffffff;">POR</span> <span style="color: #ffffff;">PALAVRA</span>',
        "css": "font-family: Arial, sans-serif; font-weight: 900; color: #ffff00; -webkit-text-stroke: 1.2px #000000; font-size: 0.85rem; letter-spacing: 0.5px;",
        "description": "Efeito Karaoke estilo Reels/TikTok",
    },
    "white-bold": {
        "label": "Branco Bold",
        "sample": "LEGENDA EM CAIXA ALTA",
        "css": "font-family: Arial, sans-serif; font-weight: 900; color: #ffffff; -webkit-text-stroke: 1.2px #000000; font-size: 0.85rem;",
        "description": "Branco caixa alta com contorno preto",
    },
    "yellow-bold": {
        "label": "Amarelo Bold",
        "sample": "AMARELO COM CONTORNO",
        "css": "font-family: Arial, sans-serif; font-weight: 900; color: #ffff00; -webkit-text-stroke: 1.2px #000000; font-size: 0.85rem;",
        "description": "Amarelo forte em alta visibilidade",
    },
    "white-yellow-keyword": {
        "label": "Branco + Amarelo",
        "sample": 'TEXTO COM <span style="color: #ffff00;">PALAVRA</span>',
        "css": "font-family: Arial, sans-serif; font-weight: 900; color: #ffffff; -webkit-text-stroke: 1.2px #000000; font-size: 0.85rem;",
        "description": "Destaca a palavra principal em amarelo",
    },
    "white-red-highlight": {
        "label": "Destaque Vermelho",
        "sample": 'FRASE EM <span style="color: #ff3333; -webkit-text-stroke: 1.5px #cc0000;">VERMELHO</span>',
        "css": "font-family: Arial, sans-serif; font-weight: 900; color: #ffffff; -webkit-text-stroke: 1.2px #000000; font-size: 0.85rem;",
        "description": "Contorno em vermelho intenso",
    },
    "cyan-shadow": {
        "label": "Ciano com Sombra",
        "sample": "CIANO COM SOMBRA PRETA",
        "css": "font-family: Arial, sans-serif; font-weight: 900; color: #00ffff; text-shadow: 2px 2px 0px #000000; font-size: 0.85rem;",
        "description": "Estilo gamer / moderno em ciano",
    },
}


def escape_ass_text(text: str) -> str:
    """Defensive escaping: `{`/`}` are ASS override-tag delimiters. Transcript
    text should never contain them, but never trust external/model text as
    control syntax."""
    return text.replace("{", "(").replace("}", ")")


def chunk_words_for_captions(words: list[dict], max_words: int = 6,
                              max_duration: float = 2.5, pause_threshold: float = 0.4) -> list[list[dict]]:
    """Groups words into caption chunks: breaks on word-count/duration caps,
    or earlier at a natural pause, so each chunk reads as a short, fast unit
    (a chunk becomes at most 2 lines via `wrap_chunk_into_word_groups`)."""
    chunks = []
    current: list[dict] = []
    for word in words:
        if current:
            gap = word["start"] - current[-1]["end"]
            duration_if_added = word["end"] - current[0]["start"]
            would_exceed = len(current) >= max_words or duration_if_added > max_duration
            natural_break = gap >= pause_threshold and len(current) >= 2
            if would_exceed or natural_break:
                chunks.append(current)
                current = []
        current.append(word)
    if current:
        chunks.append(current)
    return chunks


def wrap_chunk_into_word_groups(words: list[dict]) -> list[list[dict]]:
    """Splits a chunk's words into at most 2 lines."""
    if len(words) <= 4:
        return [words]
    mid = (len(words) + 1) // 2
    return [words[:mid], words[mid:]]


def _pick_highlight_word(words: list[dict]) -> dict:
    """Heuristic: the longest word (by stripped length) is treated as the
    'important' word for keyword/expression highlight presets. Not NLP
    keyword extraction -- documented deterministic proxy."""
    def cleaned_len(word):
        return len(word["word"].strip().strip(".,!?;:"))
    return max(words, key=cleaned_len)


_EXCLUDED_LOWERCASE_WORDS = {
    "o", "a", "os", "as", "um", "uma", "uns", "umas",
    "de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas",
    "e", "ou", "se", "que", "por", "com", "para", "pra", "pela", "pelo", "pelas", "pelos", "sem", "sob", "sobre",
    "of", "the", "and", "to", "in", "on", "at", "for", "a", "an", "or", "but", "by", "with", "without",
}


def format_word_casing(word: str, casing_mode: str = "uppercase", is_first_word: bool = False) -> str:
    """Formats a word based on casing_mode:
    - 'uppercase': ALL CAPS (e.g. 'EXEMPLO')
    - 'first_letter' / 'titlecase': First letter capitalized, EXCEPT connecting words (o/a/de/e...)
      unless it's the very first word in the line.
    """
    if casing_mode == "uppercase":
        return word.upper()
    elif casing_mode in ("first_letter", "titlecase"):
        clean_word = word.strip().strip(".,!?;:\"'()").lower()
        if not is_first_word and clean_word in _EXCLUDED_LOWERCASE_WORDS:
            return word.lower()
        else:
            return word.capitalize()
    return word


def _render_plain_line(words: list[dict], preset: CaptionPreset, highlight_word: dict | None, casing_mode: str = "uppercase") -> str:
    parts = []
    for idx, word in enumerate(words):
        raw_text = word["word"].strip()
        formatted = format_word_casing(raw_text, casing_mode, is_first_word=(idx == 0))
        text = escape_ass_text(formatted)
        if highlight_word is not None and word is highlight_word:
            text = preset.highlight_open + text + preset.highlight_close
        parts.append(text)
    return " ".join(parts)


def _render_karaoke_line(words: list[dict], preset: CaptionPreset, casing_mode: str = "uppercase") -> str:
    parts = []
    for idx, word in enumerate(words):
        duration_cs = max(1, round((word["end"] - word["start"]) * 100))
        raw_text = word["word"].strip()
        formatted = format_word_casing(raw_text, casing_mode, is_first_word=(idx == 0))
        text = escape_ass_text(formatted)
        parts.append(f"{{\\kf{duration_cs}}}{text}")
    return " ".join(parts)


def format_ass_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis == 100:
        centis = 0
        secs += 1
        if secs == 60:
            secs = 0
            minutes += 1
            if minutes == 60:
                minutes = 0
                hours += 1
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def validate_ass_content(content: str) -> None:
    """Guards against visible VTT/HTML-style tags and captions longer than 2 lines."""
    if re.search(r"<[^>]+>", content):
        raise ValueError("Tag estilo VTT/HTML encontrada no ASS gerado")
    for line in content.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        body = line[len("Dialogue:"):].strip()
        fields = body.split(",", 9)
        if len(fields) < 10:
            continue
        text_field = fields[9]
        if text_field.count("\\N") > 1:
            raise ValueError(f"Legenda com mais de 2 linhas (\\N duplicado): {line[:120]}")


def _build_events(chunks: list[list[dict]], preset: CaptionPreset, casing_mode: str = "uppercase") -> list[dict]:
    events = []
    for chunk in chunks:
        line_groups = wrap_chunk_into_word_groups(chunk)
        highlight_word = None
        if preset.highlight_mode in ("keyword_color", "expression_highlight"):
            highlight_word = _pick_highlight_word(chunk)

        rendered_lines = []
        for group in line_groups:
            if preset.highlight_mode == "karaoke":
                rendered_lines.append(_render_karaoke_line(group, preset, casing_mode=casing_mode))
            else:
                rendered_lines.append(_render_plain_line(group, preset, highlight_word, casing_mode=casing_mode))

        events.append({
            "start": chunk[0]["start"],
            "end": chunk[-1]["end"],
            "text": "\\N".join(rendered_lines),
        })
    return events


def _render_ass_document(preset: CaptionPreset, video_width: int, video_height: int,
                          alignment: int, margin_lr: int, margin_v: int, events: list[dict]) -> str:
    bold_flag = -1 if preset.bold else 0
    header = (
        f"[Script Info]\n"
        f"Title: Cortaí Caption ({preset.name})\n"
        f"ScriptType: v4.00+\n"
        f"PlayResX: {video_width}\n"
        f"PlayResY: {video_height}\n"
        f"WrapStyle: 0\n"
        f"ScaledBorderAndShadow: yes\n\n"
        f"[V4+ Styles]\n"
        f"Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        f"Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        f"Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{preset.font_name},{preset.font_size},{preset.primary_color},"
        f"{preset.secondary_color},{preset.outline_color},{preset.back_color},{bold_flag},0,0,0,"
        f"100,100,0,0,{preset.border_style},{preset.outline_width},{preset.shadow_depth},"
        f"{alignment},{margin_lr},{margin_lr},{margin_v},1\n\n"
        f"[Events]\n"
        f"Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = [header]
    for event in events:
        start = format_ass_timestamp(event["start"])
        end = format_ass_timestamp(event["end"])
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{event['text']}\n")
    return "".join(lines)


def build_ass_subtitle(transcription: dict, preset_name: str, video_width: int, video_height: int,
                        output_path: str, position: str = "bottom",
                        max_words_per_chunk: int = 6, max_chunk_duration: float = 2.5,
                        casing_mode: str = "uppercase", font_name: str | None = None) -> str:
    """Entry point for Fase 5: builds and validates an .ass subtitle file
    from a transcription (word-level timestamps from Fase 2) and a preset,
    then writes it to `output_path`.

    `position`: "bottom" (default) or "top" -- callers pass "top" when the
    detected face (Fase 4) sits low in the vertical frame, to avoid
    covering it when avoidable.
    `casing_mode`: "uppercase" (default) or "titlecase" / "first_letter"
    `font_name`: optional validated ASS family override. The preset object is
    copied so concurrent renders never mutate the shared CAPTION_PRESETS map.
    """
    if preset_name not in CAPTION_PRESETS:
        raise ValueError(f"Preset de legenda desconhecido: {preset_name!r}. Opcoes: {list(CAPTION_PRESETS)}")
    preset = CAPTION_PRESETS[preset_name]
    if font_name is not None:
        preset = replace(preset, font_name=validate_caption_font(font_name))

    if not transcription or (not transcription.get("segments") and not transcription.get("text") and not transcription.get("title") and "duration" not in transcription):
        raise ValueError("Transcrição vazia sem palavras.")

    words = flatten_words(transcription)
    if not words:
        # Fallback for silent video or video without audio: render a static caption spanning full duration
        fallback_text = (transcription.get("title") or transcription.get("text") or "Vídeo sem áudio de fala").strip()
        fallback_words = fallback_text.split()
        formatted_words = [
            format_word_casing(w, casing_mode, is_first_word=(i == 0))
            for i, w in enumerate(fallback_words)
        ]
        text_str = escape_ass_text(" ".join(formatted_words))
        events = [{
            "start": 0.0,
            "end": max(5.0, float(transcription.get("duration", 10.0))),
            "text": text_str,
        }]
    else:
        chunks = chunk_words_for_captions(words, max_words_per_chunk, max_chunk_duration)
        events = _build_events(chunks, preset, casing_mode=casing_mode)

    alignment = 8 if position == "top" else 2
    margin_v = max(120, int(round(0.12 * video_height))) if position == "bottom" else max(80, int(round(0.08 * video_height)))
    margin_lr = max(20, int(round(0.08 * video_width)))

    content = _render_ass_document(preset, video_width, video_height, alignment, margin_lr, margin_v, events)
    validate_ass_content(content)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    return output_path
