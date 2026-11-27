"""Templates de canal (Fase 6).

Um template e um dicionario JSON validado e persistido na tabela
`templates` (ja criada na Fase 1). Controla layout, legenda, watermark e
parametros de export -- exatamente os campos que o CLAUDE.md pede:
"layout, fundo, cores, estilo/posicao de legenda, marca-d'agua (tamanho,
opacidade, posicao, margens seguras), titulo/CTA opcionais, parametros de
exportacao".

Formato validado, portavel (JSON puro) e extensivel: campos desconhecidos
sao rejeitados cedo (erro claro), campos ausentes recebem o default.
"""
from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, replace

from .subtitles import CAPTION_FONTS, CAPTION_PRESETS, validate_caption_font

LAYOUTS = ("auto", "center", "active_speaker", "blurred_background", "all_faces", "card_frame", "header_footer_frame")
WATERMARK_POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right", "center")
EXPORT_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")
EXPORT_VCODECS = ("libx264", "h264_amf", "hevc_amf", "h264_nvenc", "hevc_nvenc")
CASING_MODES = ("uppercase", "titlecase", "first_letter")
ASPECT_RATIO_DIMENSIONS = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
}
RENDER_OPTIONS_SCHEMA_VERSION = 1

DEFAULT_TEMPLATE_CONFIG = {
    "layout": "auto",
    "caption_preset": "white-bold",
    "caption_font": "Arial",
    "casing_mode": "uppercase",
    "watermark": {
        "enabled": True,
        "path": None,  # None -> usa config.WATERMARK_IMAGE / fallback gerado
        "position": "bottom-right",
        "opacity": 0.5,
        "margin_x": 20,
        "margin_y": 20,
        "width": None,  # None -> tamanho nativo da imagem
    },
    "title": None,
    "cta": None,
    "vocabulary": [],  # termos corretos (siglas, nomes proprios) para corrigir a transcricao -- Fase 9
    "export": {
        "target_width": 1080,
        "target_height": 1920,
        "vcodec": "libx264",
        "acodec": "aac",
        "preset": "medium",
        "crf": 23,
    },
}


def _merge_defaults(config: dict, defaults: dict) -> dict:
    merged = copy.deepcopy(defaults)
    for key, value in config.items():
        if key not in defaults:
            raise ValueError(f"Campo desconhecido no template: {key!r}. Campos validos: {list(defaults)}")
        if isinstance(value, dict) and isinstance(defaults[key], dict):
            merged[key] = _merge_defaults(value, defaults[key])
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def validate_template_config(config: dict) -> dict:
    """Normaliza um config de template contra `DEFAULT_TEMPLATE_CONFIG`:
    preenche campos ausentes, rejeita campos desconhecidos, e valida os
    valores que tem um conjunto fechado de opcoes ou faixa numerica.

    Returns a new, fully-populated dict -- never mutates the input.
    """
    if not isinstance(config, dict):
        raise ValueError("Config de template deve ser um dicionario")

    merged = _merge_defaults(config, DEFAULT_TEMPLATE_CONFIG)

    if merged["layout"] not in LAYOUTS:
        raise ValueError(f"layout invalido: {merged['layout']!r}. Opcoes: {LAYOUTS}")

    if merged["casing_mode"] not in CASING_MODES:
        raise ValueError(f"casing_mode invalido: {merged['casing_mode']!r}. Opcoes: {CASING_MODES}")

    if merged["caption_preset"] not in CAPTION_PRESETS:
        raise ValueError(
            f"caption_preset invalido: {merged['caption_preset']!r}. Opcoes: {list(CAPTION_PRESETS)}"
        )

    validate_caption_font(merged["caption_font"])

    if not isinstance(merged["vocabulary"], list) or not all(isinstance(t, str) for t in merged["vocabulary"]):
        raise ValueError(f"vocabulary deve ser uma lista de strings, recebido: {merged['vocabulary']!r}")

    watermark = merged["watermark"]
    if watermark["position"] not in WATERMARK_POSITIONS:
        raise ValueError(f"watermark.position invalido: {watermark['position']!r}. Opcoes: {WATERMARK_POSITIONS}")
    if not (0.0 <= float(watermark["opacity"]) <= 1.0):
        raise ValueError(f"watermark.opacity deve estar entre 0.0 e 1.0, recebido: {watermark['opacity']!r}")
    if watermark["width"] is not None and int(watermark["width"]) <= 0:
        raise ValueError(f"watermark.width deve ser positivo, recebido: {watermark['width']!r}")

    export = merged["export"]
    if int(export["target_width"]) <= 0 or int(export["target_height"]) <= 0:
        raise ValueError("export.target_width/target_height devem ser positivos")
    if not (0 <= int(export["crf"]) <= 51):
        raise ValueError(f"export.crf deve estar entre 0 e 51, recebido: {export['crf']!r}")
    if export["vcodec"] not in EXPORT_VCODECS:
        raise ValueError(f"export.vcodec invalido: {export['vcodec']!r}. Opcoes: {EXPORT_VCODECS}")
    if export["preset"] not in EXPORT_PRESETS:
        raise ValueError(f"export.preset invalido: {export['preset']!r}. Opcoes: {EXPORT_PRESETS}")

    return merged


def render_kwargs_from_template(config: dict) -> dict:
    """Maps a validated template config to the keyword arguments
    `core.process_video_to_clip` expects."""
    watermark = config["watermark"]
    export = config["export"]
    watermark_path = watermark["path"] if watermark["enabled"] else ""
    return {
        "caption_preset": config["caption_preset"],
        "caption_font": config.get("caption_font", "Arial"),
        "casing_mode": config.get("casing_mode", "uppercase"),
        "layout": config["layout"],
        "title": config["title"],
        "cta": config["cta"],
        "watermark_path": watermark_path,
        "watermark_position": watermark["position"],
        "watermark_opacity": watermark["opacity"],
        "watermark_margin_x": watermark["margin_x"],
        "watermark_margin_y": watermark["margin_y"],
        "watermark_width": watermark["width"],
        "target_width": export["target_width"],
        "target_height": export["target_height"],
        "vcodec": export["vcodec"],
        "acodec": export["acodec"],
        "preset": export["preset"],
        "crf": export["crf"],
    }


@dataclass(frozen=True, slots=True)
class RenderOptions:
    """Immutable, versioned snapshot of every option that changes a render.

    Jobs persist one normalized snapshot and clips may copy/replace it when a
    user intentionally re-renders with different visual settings. A template
    is only an input to this snapshot; later template edits cannot change an
    already-created job or clip.
    """

    schema_version: int = RENDER_OPTIONS_SCHEMA_VERSION
    aspect_ratio: str = "9:16"
    target_width: int = 1080
    target_height: int = 1920
    layout: str = "auto"
    caption_preset: str = "white-bold"
    caption_font: str = "Arial"
    casing_mode: str = "uppercase"
    crop_x_pct: float | None = None
    watermark_path: str | None = None
    watermark_position: str = "bottom-right"
    watermark_opacity: float = 0.5
    watermark_margin_x: int = 20
    watermark_margin_y: int = 20
    watermark_width: int | None = None
    title: str | None = None
    cta: str | None = None
    vcodec: str = "libx264"
    acodec: str = "aac"
    preset: str = "medium"
    crf: int = 23
    source_template_id: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping | "RenderOptions" | None = None) -> "RenderOptions":
        if isinstance(value, cls):
            value.validate()
            return value
        if value is None:
            value = {}
        if not isinstance(value, Mapping):
            raise ValueError("RenderOptions deve ser um objeto/dicionario")

        raw = dict(value)
        known = {item.name for item in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"Campos desconhecidos em RenderOptions: {sorted(unknown)}")

        data = asdict(cls())
        data.update(raw)
        data["schema_version"] = int(data["schema_version"])
        data["aspect_ratio"] = str(data["aspect_ratio"])

        if data["aspect_ratio"] not in ASPECT_RATIO_DIMENSIONS:
            raise ValueError(
                f"aspect_ratio invalido: {data['aspect_ratio']!r}. "
                f"Opcoes: {tuple(ASPECT_RATIO_DIMENSIONS)}"
            )
        expected_width, expected_height = ASPECT_RATIO_DIMENSIONS[data["aspect_ratio"]]
        if "target_width" not in raw:
            data["target_width"] = expected_width
        if "target_height" not in raw:
            data["target_height"] = expected_height

        for key in ("target_width", "target_height", "watermark_margin_x", "watermark_margin_y", "crf"):
            data[key] = int(data[key])
        data["watermark_opacity"] = float(data["watermark_opacity"])
        if data["watermark_width"] not in (None, ""):
            data["watermark_width"] = int(data["watermark_width"])
        else:
            data["watermark_width"] = None
        if data["crop_x_pct"] not in (None, ""):
            data["crop_x_pct"] = float(data["crop_x_pct"])
        else:
            data["crop_x_pct"] = None

        options = cls(**data)
        options.validate()
        return options

    @classmethod
    def from_template_config(
        cls,
        config: Mapping | None = None,
        *,
        aspect_ratio: str = "9:16",
        overrides: Mapping | None = None,
        crop_x_pct: float | None = None,
        source_template_id: str | None = None,
    ) -> "RenderOptions":
        template = validate_template_config(dict(config or {}))
        allowed_overrides = {"layout", "caption_preset", "caption_font", "casing_mode"}
        overrides = dict(overrides or {})
        unknown = set(overrides) - allowed_overrides
        if unknown:
            raise ValueError(f"Overrides de render desconhecidos: {sorted(unknown)}")
        for key, value in overrides.items():
            if value not in (None, ""):
                template[key] = value
        template = validate_template_config(template)

        render_kwargs = render_kwargs_from_template(template)
        target_width, target_height = ASPECT_RATIO_DIMENSIONS.get(aspect_ratio, (None, None))
        payload = {
            **render_kwargs,
            "schema_version": RENDER_OPTIONS_SCHEMA_VERSION,
            "aspect_ratio": aspect_ratio,
            "target_width": target_width,
            "target_height": target_height,
            "crop_x_pct": crop_x_pct,
            "source_template_id": source_template_id,
        }
        return cls.from_dict(payload)

    def validate(self) -> None:
        if self.schema_version != RENDER_OPTIONS_SCHEMA_VERSION:
            raise ValueError(
                f"Versao de RenderOptions nao suportada: {self.schema_version}; "
                f"esperada: {RENDER_OPTIONS_SCHEMA_VERSION}"
            )
        if self.aspect_ratio not in ASPECT_RATIO_DIMENSIONS:
            raise ValueError(f"aspect_ratio invalido: {self.aspect_ratio!r}")
        expected_dimensions = ASPECT_RATIO_DIMENSIONS[self.aspect_ratio]
        if (self.target_width, self.target_height) != expected_dimensions:
            raise ValueError(
                f"Dimensoes {self.target_width}x{self.target_height} nao correspondem a "
                f"{self.aspect_ratio} ({expected_dimensions[0]}x{expected_dimensions[1]})"
            )
        if self.layout not in LAYOUTS:
            raise ValueError(f"layout invalido: {self.layout!r}. Opcoes: {LAYOUTS}")
        if self.caption_preset not in CAPTION_PRESETS:
            raise ValueError(f"caption_preset invalido: {self.caption_preset!r}")
        validate_caption_font(self.caption_font)
        if self.casing_mode not in CASING_MODES:
            raise ValueError(f"casing_mode invalido: {self.casing_mode!r}")
        if self.crop_x_pct is not None and (
            not math.isfinite(self.crop_x_pct) or not 0.0 <= self.crop_x_pct <= 100.0
        ):
            raise ValueError(f"crop_x_pct deve estar entre 0 e 100: {self.crop_x_pct!r}")
        if self.watermark_position not in WATERMARK_POSITIONS:
            raise ValueError(f"watermark_position invalido: {self.watermark_position!r}")
        if not math.isfinite(self.watermark_opacity) or not 0.0 <= self.watermark_opacity <= 1.0:
            raise ValueError(f"watermark_opacity deve estar entre 0 e 1: {self.watermark_opacity!r}")
        if self.watermark_margin_x < 0 or self.watermark_margin_y < 0:
            raise ValueError("Margens da marca-d'agua nao podem ser negativas")
        if self.watermark_width is not None and self.watermark_width <= 0:
            raise ValueError("watermark_width deve ser positivo")
        if self.vcodec not in EXPORT_VCODECS:
            raise ValueError(f"vcodec invalido: {self.vcodec!r}")
        if self.preset not in EXPORT_PRESETS:
            raise ValueError(f"preset invalido: {self.preset!r}")
        if not self.acodec:
            raise ValueError("acodec nao pode ser vazio")
        if not 0 <= self.crf <= 51:
            raise ValueError(f"crf deve estar entre 0 e 51: {self.crf!r}")
        for key, value in (
            ("watermark_path", self.watermark_path),
            ("title", self.title),
            ("cta", self.cta),
            ("source_template_id", self.source_template_id),
        ):
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{key} deve ser texto ou null")

    def to_dict(self) -> dict:
        return asdict(self)

    def to_render_kwargs(self) -> dict:
        return {
            "caption_preset": self.caption_preset,
            "caption_font": self.caption_font,
            "casing_mode": self.casing_mode,
            "layout": self.layout,
            "crop_x_pct": self.crop_x_pct,
            "title": self.title,
            "cta": self.cta,
            "watermark_path": self.watermark_path,
            "watermark_position": self.watermark_position,
            "watermark_opacity": self.watermark_opacity,
            "watermark_margin_x": self.watermark_margin_x,
            "watermark_margin_y": self.watermark_margin_y,
            "watermark_width": self.watermark_width,
            "target_width": self.target_width,
            "target_height": self.target_height,
            "vcodec": self.vcodec,
            "acodec": self.acodec,
            "preset": self.preset,
            "crf": self.crf,
        }

    def with_crop(self, crop_x_pct: float | None) -> "RenderOptions":
        return RenderOptions.from_dict(replace(self, crop_x_pct=crop_x_pct).to_dict())

    def with_template_config(
        self, config: Mapping, *, source_template_id: str | None = None
    ) -> "RenderOptions":
        return RenderOptions.from_template_config(
            config,
            aspect_ratio=self.aspect_ratio,
            crop_x_pct=self.crop_x_pct,
            source_template_id=source_template_id,
        )


def validate_render_options(value: Mapping | RenderOptions | None = None) -> dict:
    """Returns a canonical JSON-serializable RenderOptions snapshot."""
    return RenderOptions.from_dict(value).to_dict()


def render_kwargs_from_options(value: Mapping | RenderOptions) -> dict:
    return RenderOptions.from_dict(value).to_render_kwargs()
