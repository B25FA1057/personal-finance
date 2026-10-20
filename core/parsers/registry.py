from __future__ import annotations

from typing import Any

from .base import BankParser
from .golomt import GolomtParser

PARSERS: list[type[BankParser]] = [GolomtParser]


def detect_parser(workbook: Any) -> BankParser:
    for parser_cls in PARSERS:
        if parser_cls.detect(workbook):
            return parser_cls()
    raise ValueError("Unsupported statement format")
