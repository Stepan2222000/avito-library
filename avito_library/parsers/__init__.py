"""Parsers for Avito library plan prototypes."""

from .card_parser import CardData, CardParsingError, parse_card

__all__ = [
    "CardData",
    "CardParsingError",
    "parse_card",
]
