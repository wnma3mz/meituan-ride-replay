"""Route colours, overridable from filters.yaml.

The three route states have to read as one family, differing in brightness
rather than in kind.  The earlier palette used white for the finished legs,
which lands on RGB(173, 180, 188) over the dark basemap — a neutral grey.  So a
leg turned grey the moment the camera moved on, which looked like a rendering
fault rather than a state change.

Built-in schemes keep the travelled line yellow (it is the eye's anchor) and
vary the hue of everything else:

    cyan    complementary to yellow, 137° apart — the default
    purple  far from both yellow and the blue basemap
    green   adjacent to yellow but still separable
    orange  yellow's darker sibling; only 19° from it, so progress reads weakly

Override in filters.yaml:

    video:
      palette: purple

or give explicit colours:

    video:
      colors:
        ahead: [90, 200, 215, 190]
        done: [190, 240, 248, 220]
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

RGBA = tuple[int, int, int, int]

# The travelled line. Yellow in every scheme: it is the one thing that must
# never be mistaken for map furniture.
ACCENT = (255, 214, 10)


@dataclass(frozen=True)
class Palette:
    """Colours for the four route states, as drawn over the dark basemap."""

    # A leg the rider has finished. Brighter than `ahead`, same hue family.
    done: RGBA = (190, 240, 248, 220)
    # A leg not yet reached, and the road ahead inside the current leg.
    ahead: RGBA = (90, 200, 215, 190)
    ahead_current: RGBA = (90, 200, 215, 145)
    # Dark outline under every route, so pale roads cannot swallow a thin line.
    casing: RGBA = (0, 0, 0, 150)
    # Dashes across a transfer the rider did not cycle.
    transfer: RGBA = (90, 200, 215, 150)
    accent: tuple[int, int, int] = ACCENT

    @property
    def travelled(self) -> RGBA:
        return self.accent + (255,)


SCHEMES: dict[str, Palette] = {
    "cyan": Palette(),
    "purple": Palette(
        done=(232, 214, 246, 220),
        ahead=(198, 160, 220, 200),
        ahead_current=(198, 160, 220, 150),
        transfer=(198, 160, 220, 150),
    ),
    "green": Palette(
        done=(196, 240, 212, 220),
        ahead=(110, 190, 140, 200),
        ahead_current=(110, 190, 140, 150),
        transfer=(110, 190, 140, 150),
    ),
    "orange": Palette(
        done=(252, 226, 190, 220),
        ahead=(236, 176, 110, 200),
        ahead_current=(236, 176, 110, 150),
        transfer=(236, 176, 110, 150),
    ),
    # The pre-2026-09 look, kept so old output can be reproduced.
    "legacy": Palette(
        done=(255, 255, 255, 120),
        ahead=(152, 176, 208, 96),
        ahead_current=(255, 255, 255, 46),
        casing=(0, 0, 0, 90),
        transfer=(150, 170, 196, 130),
    ),
}

DEFAULT_SCHEME = "cyan"


def _as_rgba(value: object, fallback: RGBA) -> RGBA:
    if not isinstance(value, (list, tuple)) or not 3 <= len(value) <= 4:
        return fallback
    try:
        parts = [int(v) for v in value]
    except (TypeError, ValueError):
        return fallback
    if len(parts) == 3:
        parts.append(fallback[3])
    return (parts[0], parts[1], parts[2], parts[3])


def _filters_path() -> Path:
    override = os.environ.get("RIDE_FILTERS")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent.parent.parent / "filters.yaml"


def load(path: Path | None = None) -> Palette:
    """Resolve the palette: a named scheme, optionally with per-colour overrides."""
    path = path or _filters_path()
    if not path.exists():
        return SCHEMES[DEFAULT_SCHEME]
    try:
        import yaml

        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return SCHEMES[DEFAULT_SCHEME]
    block = document.get("video") if isinstance(document, dict) else None
    if not isinstance(block, dict):
        return SCHEMES[DEFAULT_SCHEME]

    name = str(block.get("palette", DEFAULT_SCHEME))
    base = SCHEMES.get(name, SCHEMES[DEFAULT_SCHEME])

    colors = block.get("colors")
    if not isinstance(colors, dict):
        return base
    return Palette(
        done=_as_rgba(colors.get("done"), base.done),
        ahead=_as_rgba(colors.get("ahead"), base.ahead),
        ahead_current=_as_rgba(colors.get("ahead_current"), base.ahead_current),
        casing=_as_rgba(colors.get("casing"), base.casing),
        transfer=_as_rgba(colors.get("transfer"), base.transfer),
        accent=_as_rgba(colors.get("accent"), base.accent + (255,))[:3],
    )
