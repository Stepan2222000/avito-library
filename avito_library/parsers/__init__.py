"""Parsers for Avito library plan prototypes."""

from .card_parser import CardData, CardParsingError, parse_card, CardParseStatus, CardParseResult

__all__ = [
    "CardData",
    "CardParsingError",
    "parse_card",
    "CardParseStatus",
    "CardParseResult",
]
