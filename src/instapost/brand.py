"""Brand template (brand.yaml): fonts, colours, texts, scene presets."""

from pathlib import Path

import yaml
from pydantic import BaseModel

from instapost.config import get_settings


class BrandFonts(BaseModel):
    hook: str
    body: str
    reference: str
    header: str
    footer: str


class BrandColors(BaseModel):
    paper: str
    paper_edge: str
    ink: str
    ink_soft: str
    highlight: str
    highlight_alpha: float
    glow: str


PLACEHOLDER_HANDLE = "@your_handle"


class Brand(BaseModel):
    page_name: str
    handle: str
    header_text: str
    divider_symbol: str
    fonts: BrandFonts
    colors: BrandColors
    scene_presets: list[str]
    illustration_style: str
    glow_position: tuple[float, float]

    @property
    def printed_handle(self) -> str:
        """The handle to print on posts; nothing while brand.yaml still has the placeholder."""
        h = self.handle.strip()
        return "" if h in ("", "@", PLACEHOLDER_HANDLE) else h

    def font_path(self, role: str) -> Path:
        return get_settings().assets_dir / "fonts" / getattr(self.fonts, role)


def load_brand(path: Path | None = None) -> Brand:
    path = path or get_settings().brand_file
    return Brand.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
