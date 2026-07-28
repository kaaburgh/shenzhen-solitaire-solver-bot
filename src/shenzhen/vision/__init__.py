"""Reading a Shenzhen Solitaire board off a screenshot."""

from .classify import Guess, TemplateBank
from .crop import card_crop
from .layout import BoardLayout, Box, LayoutConfig, LayoutError, detect_layout
from .recognize import Recognition, RecognitionError, load_bank, load_image, recognize

__all__ = [
    "BoardLayout",
    "Box",
    "Guess",
    "card_crop",
    "LayoutConfig",
    "LayoutError",
    "Recognition",
    "RecognitionError",
    "TemplateBank",
    "detect_layout",
    "load_bank",
    "load_image",
    "recognize",
]
