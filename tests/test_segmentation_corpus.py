from __future__ import annotations

import json
import unicodedata
import unittest
from pathlib import Path

from tests.test_support import load_driver_module

CORPUS_PATH = Path(__file__).with_name("segmentation_corpus.json")
SUPPORTED_SCHEMA_VERSION = 1
SUPPORTED_OPERATIONS = {"sentenceUnits", "latencySegments"}
REQUIRED_CATEGORIES = {
    "locale-punctuation",
    "abbreviation",
    "url",
    "emoji",
    "cjk",
    "thai",
    "long-sentence",
}


def _materialize_text(case: dict[str, object]) -> str:
    text = case.get("text")
    if isinstance(text, str):
        return text
    builder = case.get("textBuilder")
    if not isinstance(builder, dict):
        raise AssertionError(f"{case['id']}: text or textBuilder is required")
    pattern = str(builder.get("pattern", ""))
    repeat = int(builder.get("repeat", 1))
    separator = str(builder.get("separator", ""))
    return str(builder.get("prefix", "")) + separator.join([pattern] * repeat) + str(builder.get("suffix", ""))


def _sentence_units(harness: object, text: str) -> list[str]:
    starts = [0, *harness.find_sentence_splits(text)]
    ends = [*starts[1:], len(text)]
    return [text[start:end].strip() for start, end in zip(starts, ends) if text[start:end].strip()]


class SegmentationCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.processing = load_driver_module("speech_processing")
        cls.harness = cls.processing.DEFAULT_TEXT_SEGMENTER
        cls.corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))

    def test_corpus_schema(self) -> None:
        self.assertEqual(SUPPORTED_SCHEMA_VERSION, self.corpus.get("schemaVersion"))
        self.assertIsInstance(self.corpus.get("source"), dict)
        cases = self.corpus.get("cases")
        self.assertIsInstance(cases, list)
        self.assertTrue(cases)
        seen_ids: set[str] = set()
        for case in cases:
            self.assertIsInstance(case, dict)
            case_id = case.get("id")
            self.assertIsInstance(case_id, str)
            self.assertTrue(case_id)
            self.assertNotIn(case_id, seen_ids, f"Duplicate corpus case ID: {case_id}")
            seen_ids.add(case_id)
            self.assertIn(case.get("category"), REQUIRED_CATEGORIES, case_id)
            operation = case.get("operation")
            self.assertIn(operation, SUPPORTED_OPERATIONS, case_id)
            self.assertNotEqual("text" in case, "textBuilder" in case, case_id)
            _materialize_text(case)
            if operation == "sentenceUnits":
                expected = case.get("expected")
                self.assertIsInstance(expected, list, case_id)
                self.assertTrue(all(isinstance(segment, str) and segment for segment in expected), case_id)
            else:
                self.assertIsInstance(case.get("assert"), dict, case_id)

    def test_corpus_cases(self) -> None:
        for case in self.corpus["cases"]:
            with self.subTest(case=case["id"]):
                text = _materialize_text(case)
                operation = case["operation"]
                if operation == "sentenceUnits":
                    self.assertEqual(case["expected"], _sentence_units(self.harness, text))
                elif operation == "latencySegments":
                    segments = list(
                        self.harness.iter_text_segments_for_latency(text, bool(case.get("fastFirstSegment", False)))
                    )
                    self._assert_latency_segments(case, text, segments)
                else:
                    self.fail(f"Unknown corpus operation: {operation}")

    def _assert_latency_segments(self, case: dict[str, object], text: str, segments: list[str]) -> None:
        assertions = case["assert"]
        self.assertGreaterEqual(len(segments), assertions.get("minSegmentCount", 1))
        if "maxSegmentLength" in assertions:
            self.assertLessEqual(max(map(len, segments)), assertions["maxSegmentLength"])
        if "firstMaxLength" in assertions:
            self.assertLessEqual(len(segments[0]), assertions["firstMaxLength"])
        if assertions.get("preservesNonWhitespace"):
            compact = lambda value: "".join(value.split())
            self.assertEqual(compact(text), compact("".join(segments)))
        forbidden_starts = tuple(assertions.get("forbidSegmentStartCharacters", []))
        forbidden_ends = tuple(assertions.get("forbidSegmentEndCharacters", []))
        for segment in segments:
            self.assertTrue(segment)
            if forbidden_starts:
                self.assertNotIn(segment[0], forbidden_starts)
            if forbidden_ends:
                self.assertNotIn(segment[-1], forbidden_ends)
            if assertions.get("noLeadingCombiningMark"):
                self.assertFalse(unicodedata.category(segment[0]).startswith("M"))

    def test_corpus_covers_requested_categories(self) -> None:
        categories = {case["category"] for case in self.corpus["cases"]}
        self.assertTrue(REQUIRED_CATEGORIES <= categories)

    def test_helper_module_import_does_not_load_nvda(self) -> None:
        for module_name in (
            "addonHandler",
            "config",
            "globalVars",
            "nvwave",
            "synthDriverHandler",
            "wx",
        ):
            self.assertNotIn(module_name, self.processing.__dict__)


if __name__ == "__main__":
    unittest.main()
