from __future__ import annotations

import json
import re
import runpy
import unicodedata
import unittest

from tests.test_support import UNICODE_DATA_PATH, load_driver_module


EXPECTED_LANGUAGE_SCRIPT_GROUPS = {
    ("Arabic",): ("ar", "ur"),
    ("Arabic", "Devanagari"): ("ks", "sd"),
    ("Bengali",): ("as", "bn"),
    ("Bengali", "Meetei_Mayek"): ("mni",),
    ("Cyrillic",): ("bg", "ru", "uk"),
    ("Cyrillic", "Latin"): ("sr",),
    ("Devanagari",): ("brx", "doi", "hi", "kok", "mai", "mr", "ne", "sa"),
    ("Greek",): ("el",),
    ("Gujarati",): ("gu",),
    ("Gurmukhi",): ("pa",),
    ("Han",): ("cmn", "yue"),
    ("Han", "Hangul"): ("ko",),
    ("Han", "Hiragana", "Katakana"): ("ja",),
    ("Hebrew",): ("he",),
    ("Kannada",): ("kn",),
    ("Khmer",): ("km",),
    ("Latin",): (
        "bs", "ca", "cs", "cy", "da", "de", "en", "es", "et", "fi", "fil", "fr",
        "hr", "hu", "id", "is", "it", "jv", "lt", "lv", "ms", "nb", "nl", "pl",
        "pt", "ro", "sk", "sl", "sq", "su", "sv", "sw", "tr", "vi",
    ),
    ("Malayalam",): ("ml",),
    ("Ol_Chiki",): ("sat",),
    ("Oriya",): ("or",),
    ("Sinhala",): ("si",),
    ("Tamil",): ("ta",),
    ("Telugu",): ("te",),
    ("Thai",): ("th",),
}
EXPECTED_LANGUAGE_SCRIPTS = {
    root: scripts
    for scripts, roots in EXPECTED_LANGUAGE_SCRIPT_GROUPS.items()
    for root in roots
}


def _contains(ranges: tuple[tuple[int, int], ...], codepoint: int) -> bool:
    return any(start <= codepoint <= end for start, end in ranges)


def _first_letter_codepoint(ranges: tuple[tuple[int, int], ...]) -> int:
    for start, end in ranges:
        for codepoint in range(start, end + 1):
            if unicodedata.category(chr(codepoint)).startswith("L"):
                return codepoint
    raise AssertionError("Script has no letter recognized by the test Python runtime")


class UnicodeDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = runpy.run_path(str(UNICODE_DATA_PATH))
        catalog = load_driver_module("catalog")
        if not catalog.CATALOG_PATH.is_file():
            raise AssertionError(f"No bundled voices.json was found at {catalog.CATALOG_PATH}")
        packages = json.loads(catalog.CATALOG_PATH.read_text(encoding="utf-8"))
        cls.processing = load_driver_module("speech_processing")
        cls.language_profiles = load_driver_module("language_profiles")
        cls.supported_roots = set()
        for package in packages:
            parts = str(package.get("id", "")).split("-")
            if (
                len(parts) >= 2
                and re.fullmatch(r"[a-z]{2,3}", parts[0])
                and re.fullmatch(r"[a-z]{2}", parts[1])
            ):
                cls.supported_roots.add(parts[0])

    def test_generated_versions_are_pinned(self) -> None:
        self.assertEqual("17.0.0", self.data["UNICODE_VERSION"])
        self.assertEqual("48.2", self.data["CLDR_VERSION"])

    def test_every_bundled_language_root_has_official_script_data(self) -> None:
        language_scripts = self.data["SUPPORTED_LANGUAGE_SCRIPTS"]
        language_ranges = self.data["LANGUAGE_SCRIPT_RANGES"]
        self.assertEqual(self.supported_roots, set(language_scripts))
        self.assertEqual(self.supported_roots, set(language_ranges))
        self.assertEqual(EXPECTED_LANGUAGE_SCRIPTS, language_scripts)
        for root in sorted(self.supported_roots):
            with self.subTest(root=root):
                self.assertTrue(language_scripts[root])
                self.assertTrue(language_ranges[root])

    def test_language_ranges_are_exactly_composed_from_mapped_scripts(self) -> None:
        language_scripts = self.data["SUPPORTED_LANGUAGE_SCRIPTS"]
        script_ranges = self.data["SCRIPT_RANGES"]
        language_ranges = self.data["LANGUAGE_SCRIPT_RANGES"]
        self.assertEqual(
            {script for scripts in language_scripts.values() for script in scripts},
            set(script_ranges),
        )
        for root, scripts in language_scripts.items():
            with self.subTest(root=root):
                expected_ranges = tuple(
                    span
                    for script in scripts
                    for span in script_ranges[script]
                )
                self.assertEqual(expected_ranges, language_ranges[root])
                self.assertEqual(
                    expected_ranges,
                    self.language_profiles.script_ranges_for_language_root(root),
                )

    def test_script_ranges_are_sorted_and_non_overlapping(self) -> None:
        for script, ranges in self.data["SCRIPT_RANGES"].items():
            with self.subTest(script=script):
                previous_end = -1
                for start, end in ranges:
                    self.assertLessEqual(start, end)
                    self.assertGreater(start, previous_end)
                    previous_end = end

    def test_unicode_17_script_ranges_outside_old_blocks_are_present(self) -> None:
        language_ranges = self.data["LANGUAGE_SCRIPT_RANGES"]
        cases = {
            "ar": (0x0870, 0x10EC2),
            "cmn": (0x20000, 0x31350, 0x323B0),
            "en": (0xA7D0, 0x10780),
            "ja": (0x1AFF0, 0x1B120, 0x20000),
            "ko": (0xA960, 0xD7B0),
            "mni": (0xABC0,),
            "ru": (0x1C80, 0x2DE0),
            "sat": (0x1C5A,),
        }
        for root, codepoints in cases.items():
            for codepoint in codepoints:
                with self.subTest(root=root, codepoint=f"U+{codepoint:04X}"):
                    self.assertTrue(_contains(language_ranges[root], codepoint))

    def test_automatic_language_profile_fallback_uses_generated_ranges(self) -> None:
        language_scripts = self.data["SUPPORTED_LANGUAGE_SCRIPTS"]
        script_ranges = self.data["SCRIPT_RANGES"]
        for root, scripts in language_scripts.items():
            competitor = next(
                other_root
                for other_root, other_scripts in language_scripts.items()
                if root != other_root and set(scripts).isdisjoint(other_scripts)
            )
            for script in scripts:
                codepoint = _first_letter_codepoint(script_ranges[script])
                with self.subTest(root=root, script=script, codepoint=f"U+{codepoint:04X}"):
                    self.assertEqual(
                        root,
                        self.language_profiles.language_script_signal(
                            chr(codepoint),
                            {root, competitor, "unsupported"},
                        ),
                    )

    def test_shared_scripts_remain_ambiguous_between_language_profiles(self) -> None:
        language_scripts = self.data["SUPPORTED_LANGUAGE_SCRIPTS"]
        script_ranges = self.data["SCRIPT_RANGES"]
        roots = sorted(language_scripts)
        for index, left_root in enumerate(roots):
            for right_root in roots[index + 1 :]:
                shared_scripts = set(language_scripts[left_root]).intersection(language_scripts[right_root])
                for script in shared_scripts:
                    codepoint = _first_letter_codepoint(script_ranges[script])
                    with self.subTest(left=left_root, right=right_root, script=script):
                        self.assertIsNone(
                            self.language_profiles.language_script_signal(
                                chr(codepoint),
                                {left_root, right_root},
                            )
                        )

    def test_language_profile_fallback_rejects_missing_or_nonmatching_scripts(self) -> None:
        self.assertEqual((), self.language_profiles.script_ranges_for_language_root("unsupported"))
        self.assertIsNone(self.language_profiles.language_script_signal("", self.supported_roots))
        self.assertIsNone(self.language_profiles.language_script_signal("🙂", self.supported_roots))
        self.assertFalse(self.language_profiles.token_has_character_in_ranges("🙂", ((0x0041, 0x005A),)))

    def test_language_profile_module_has_no_nvda_dependencies(self) -> None:
        for module_name in ("addonHandler", "config", "globalVars", "languageHandler", "synthDriverHandler"):
            with self.subTest(module=module_name):
                self.assertNotIn(module_name, self.language_profiles.__dict__)

    def test_official_sentence_terminal_property_is_complete(self) -> None:
        terminals = self.data["SENTENCE_TERMINAL_CODEPOINTS"]
        self.assertEqual(170, len(terminals))
        for codepoint in (0x002E, 0x003F, 0x061E, 0x061F, 0x0964, 0x1B4E, 0x113D4, 0x16D6E):
            with self.subTest(codepoint=f"U+{codepoint:04X}"):
                self.assertIn(codepoint, terminals)

    def test_sentence_terminal_tailoring_is_minimal_and_disjoint(self) -> None:
        tailored = self.processing.TAILORED_SENTENCE_TERMINATORS
        self.assertEqual({0x037E, 0x0DF4, 0x0E5A, 0x0E5B, 0x2026, 0x22EF}, {ord(value) for value in tailored})
        self.assertTrue({ord(value) for value in tailored}.isdisjoint(self.data["SENTENCE_TERMINAL_CODEPOINTS"]))


if __name__ == "__main__":
    unittest.main()
