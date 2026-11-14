"""Location and provenance of the compiler shipped in PS2Apple."""
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent
ENGINE_ROOT = BACKEND_ROOT / "engine"
RELEASE_VERSION = (BACKEND_ROOT / "VERSION").read_text(encoding="ascii").strip()
ENGINE_VERSION = "1.6.4"
ENGINE_UPSTREAM = "exoteric33/ps2-native-recomp"
ENGINE_REVISION = "e9a48aae8e0537f3a91d8584b2d78c6056723435"
