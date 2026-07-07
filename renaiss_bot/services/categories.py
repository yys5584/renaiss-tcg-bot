"""Supported card categories."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RenaissCategory:
    key: str
    label: str
    enabled: bool
    description: str
    required_match_fields: tuple[str, ...]


_CATEGORIES: dict[str, RenaissCategory] = {
    "pokemon_tcg": RenaissCategory(
        key="pokemon_tcg",
        label="Pokemon TCG",
        enabled=True,
        description="Default category using Pokemon TCG cards and Renaiss Market Value lookup.",
        required_match_fields=("card_name", "set_code", "collector_number", "language", "grade"),
    ),
    "one_piece_tcg": RenaissCategory(
        key="one_piece_tcg",
        label="One Piece TCG",
        enabled=True,
        description="Expansion category for Renaiss-indexed One Piece cards.",
        required_match_fields=("card_name", "set_code", "collector_number", "language", "grade"),
    ),
    "other_renaiss_cards": RenaissCategory(
        key="other_renaiss_cards",
        label="Other Renaiss Cards",
        enabled=False,
        description="Planned category for additional collectible card markets supported by Renaiss.",
        required_match_fields=("category", "card_name", "collection_id", "item_number", "grade"),
    ),
}

_CATEGORY_ALIASES = {
    "pokemon": "pokemon_tcg",
    "poke": "pokemon_tcg",
    "pokemon_tcg": "pokemon_tcg",
    "onepiece": "one_piece_tcg",
    "one_piece": "one_piece_tcg",
    "one_piece_tcg": "one_piece_tcg",
    "op": "one_piece_tcg",
    "other": "other_renaiss_cards",
    "etc": "other_renaiss_cards",
    "other_renaiss_cards": "other_renaiss_cards",
}


def list_categories() -> list[RenaissCategory]:
    return list(_CATEGORIES.values())


def get_category(key: str | None) -> RenaissCategory:
    normalized = (key or "pokemon_tcg").strip().lower()
    normalized = _CATEGORY_ALIASES.get(normalized, normalized)
    return _CATEGORIES.get(normalized, _CATEGORIES["pokemon_tcg"])


def resolve_category_key(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    if not normalized:
        return None
    if normalized in _CATEGORIES:
        return normalized
    return _CATEGORY_ALIASES.get(normalized)


def split_category_args(args: list[str], default: str = "pokemon_tcg") -> tuple[str, list[str]]:
    if not args:
        return default, []
    first = resolve_category_key(args[0])
    if first:
        return first, args[1:]
    return default, args
