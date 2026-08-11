# -*- coding: utf-8 -*-
"""Pure helpers for Unicode-script language-profile fallback.

This module intentionally has no NVDA imports so the same production logic can
be exercised by the standalone test suite.
"""
from __future__ import annotations

from collections.abc import Collection

from .unicode_data import LANGUAGE_SCRIPT_RANGES


ScriptRanges = tuple[tuple[int, int], ...]


def script_ranges_for_language_root(root: str) -> ScriptRanges:
	return LANGUAGE_SCRIPT_RANGES.get(root, ())


def token_has_character_in_ranges(token: str, ranges: ScriptRanges) -> bool:
	return any(
		start <= ord(character) <= end
		for character in token
		for start, end in ranges
	)


def language_script_signal(token: str, candidateRoots: Collection[str]) -> str | None:
	"""Return the only candidate root whose generated script ranges match."""
	matchingRoots = {
		root
		for root in candidateRoots
		if (ranges := script_ranges_for_language_root(root))
		and token_has_character_in_ranges(token, ranges)
	}
	if len(matchingRoots) == 1:
		return next(iter(matchingRoots))
	return None
