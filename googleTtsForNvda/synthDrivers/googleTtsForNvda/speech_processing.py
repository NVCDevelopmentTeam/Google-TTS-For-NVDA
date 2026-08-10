# -*- coding: utf-8 -*-
"""Pure-Python text segmentation and PCM processing helpers.

This module deliberately has no NVDA imports so its behavior can be exercised by
the regular Python test suite. Runtime-specific orchestration remains in the
synth driver.
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from functools import lru_cache
import unicodedata
from typing import Any

try:
	from .unicode_data import SENTENCE_TERMINAL_CODEPOINTS
except ImportError:  # Direct import by the standalone test suite.
	from unicode_data import SENTENCE_TERMINAL_CODEPOINTS


PAUSE_MODE_DO_NOT_SHORTEN = "0"
PAUSE_MODE_SHORTEN_END_ONLY = "1"
PAUSE_MODE_SHORTEN_ALL = "2"
SHORTENED_SILENCE_KEEP_MS = 35
SHORTENED_ALL_PAUSES_KEEP_MS = 25
SILENCE_SAMPLE_THRESHOLD = 48
PCM_BYTES_PER_SAMPLE = 2
SHORT_CACHE_MAX_CHARS = 5000
SHORT_CACHE_MAX_HIDDEN_SEGMENTS = 24
SHORT_AUDIO_CACHE_OPTION_FIELDS = (
	"voiceId",
	"rate",
	"pitch",
	"postPitch",
	"volume",
	"outputGain",
	"artificialRate",
)

FAST_FIRST_SEGMENT_MIN_CHARS = 30
REGULAR_SEGMENT_MIN_CHARS = 110
FAST_FIRST_SEGMENT_MAX_CHARS = 64
REGULAR_SEGMENT_MAX_CHARS = 240
SEAMLESS_UTTERANCE_MAX_CHARS = 900
FAST_SOFT_PHRASE_SEGMENT_MIN_CHARS = 30
FAST_SOFT_PHRASE_SEGMENT_MAX_CHARS = 90
FAST_SOFT_PHRASE_SEGMENT_LOOKAHEAD = 40
SOFT_PHRASE_SEGMENT_MIN_CHARS = 100
SOFT_PHRASE_SEGMENT_MAX_CHARS = 240
SOFT_PHRASE_SEGMENT_LOOKAHEAD = 55
URL_TOKEN_SEGMENT_MAX_CHARS = 220
FORCED_SEGMENT_MIN_CHARS = 32
FORCED_SEGMENT_FORWARD_LOOKAHEAD = 24
FORCED_SEGMENT_HARD_MAX_CHARS = 320
NO_SPACE_SCRIPT_SIGNAL_MIN_CHARS = 12
NO_SPACE_SCRIPT_SIGNAL_MIN_RATIO = 0.55
NO_SPACE_SCRIPT_COMBINING_LOOKAHEAD = 8

SOFT_BREAK_CHARS = (
	",;:\uFF0C\u3001\uFF1B\uFF1A\u2014\u2013"
	"\u0387"
	"\u060C\u061B"
	"\u055D"
	"\u0F0B\u0F0C"
	"\u1363\u1364\u1365\u1366"
	"\u17D6"
	"\u104A"
	"\uA9C8"
)
ASCII_SENTENCE_TERMINATORS = ".!?"
SENTENCE_TRAILING_CLOSERS = "'\")]}”’」』）》〉»\u2018-\u201F\u3009\u300B\u300D\u300F\u3011\uFF09\uFF3D\uFF5D"
# UCD Sentence_Terminal deliberately excludes some locale-specific or ambiguous
# sentence endings. Keep only supported-language/common tailoring here.
TAILORED_SENTENCE_TERMINATORS = set("\u037E\u0DF4\u0E5A\u0E5B\u2026\u22EF")
UNICODE_SOFT_BREAK_NAME_PARTS = (
	"COMMA",
	"SEMICOLON",
	"COLON",
	"PHRASE",
	"CLAUSE",
	"PADA LINGSA",
	"PUNCTUATION CHEIKHAN",
	"PUNCTUATION BINDU",
)
UNICODE_INITIAL_PUNCTUATION_NAME_PARTS = (
	"INVERTED QUESTION MARK",
	"INVERTED EXCLAMATION MARK",
	"INITIAL QUESTION MARK",
	"INITIAL EXCLAMATION MARK",
)
NON_BREAKING_SOFT_PUNCTUATION = set(
	"'\"`´’ʼʻʹʺ_-#@&/\\"
	"\u00B7\u05F3\u05F4\u2010\u2011\u2027\u30FB\uFF65"
)
NON_BREAKING_SOFT_PUNCTUATION_NAME_PARTS = (
	"APOSTROPHE",
	"QUOTATION MARK",
	"QUOTE",
	"HYPHEN",
	"SOLIDUS",
	"SLASH",
	"MIDDLE DOT",
)
NO_SPACE_SCRIPT_PROFILES = (
	(
		(
			(0x3100, 0x312F),
			(0x31A0, 0x31BF),
			(0x3400, 0x4DBF),
			(0x4E00, 0x9FFF),
			(0xF900, 0xFAFF),
			(0x20000, 0x2A6DF),
			(0x2A700, 0x2B73F),
			(0x2B740, 0x2B81F),
			(0x2B820, 0x2CEAF),
			(0x2CEB0, 0x2EBEF),
			(0x30000, 0x3134F),
		),
		80,
	),
	(
		(
			(0x3040, 0x30FF),
			(0x31F0, 0x31FF),
			(0x1AFF0, 0x1AFFF),
			(0x1B000, 0x1B16F),
			(0xFF66, 0xFF9F),
		),
		80,
	),
	(((0x0E00, 0x0E7F),), 70),
	(((0x0E80, 0x0EFF),), 70),
	(((0x1900, 0x194F),), 70),
	(((0x1950, 0x197F),), 70),
	(((0x1980, 0x19DF),), 70),
	(((0x1A00, 0x1A1F),), 70),
	(((0x1A20, 0x1AAF),), 70),
	(((0x1780, 0x17FF),), 70),
	(((0x1000, 0x109F), (0xA9E0, 0xA9FF), (0xAA60, 0xAA7F)), 70),
	(((0x0F00, 0x0FFF),), 70),
	(((0x1700, 0x171F),), 70),
	(((0x1720, 0x173F),), 70),
	(((0x1740, 0x175F),), 70),
	(((0x1760, 0x177F),), 70),
	(((0x1B00, 0x1B7F),), 70),
	(((0x1B80, 0x1BBF),), 70),
	(((0x1BC0, 0x1BFF),), 70),
	(((0x1C00, 0x1C4F),), 70),
	(((0xA000, 0xA48F),), 70),
	(((0xA930, 0xA95F),), 70),
	(((0xA980, 0xA9DF),), 70),
	(((0xAA00, 0xAA5F),), 70),
	(((0xAA80, 0xAADF),), 70),
)

COMMON_ABBREVIATIONS = {
	# English
	"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "rev", "gen", "col", "maj", "capt", "lt", "sgt",
	"hon", "gov", "sen", "rep", "esq", "vs", "etc", "inc", "ltd", "co", "corp", "no", "fig", "eq",
	"vol", "ch", "p", "pp", "sec", "min", "max", "approx", "est", "dept", "dist", "ave", "blvd", "rd",
	"jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
	"ph", "phd", "md", "ba", "ma", "bsc", "msc", "jd", "llb", "llm",
	# German
	"usw", "bzw", "ca", "evtl", "ggf", "inkl", "nr", "ing", "mag",
	# French
	"mme", "mlle", "mgr", "ex",
	# Spanish / Portuguese
	"sra", "srta", "dra", "profa", "num", "pag", "cap", "ej", "av",
	# Vietnamese
	"tp", "ths", "ts", "gs", "pgs", "bs", "ks", "cn", "tx", "tt", "qd", "nd",
	# Russian / Cyrillic
	"ул", "им", "обл", "рис", "см", "стр", "тд", "тп", "пр", "руб", "коп", "тыс", "млн", "млрд", "др", "г", "гор", "пер", "пл", "просп",
}


def pcm_bytes_for_milliseconds(milliseconds: int, sampleRate: int, bytesPerSample: int = PCM_BYTES_PER_SAMPLE) -> int:
	frames = max(0, int(sampleRate * milliseconds / 1000))
	return frames * bytesPerSample


def align_pcm_bytes(byteCount: int, bytesPerSample: int = PCM_BYTES_PER_SAMPLE) -> int:
	return max(0, int(byteCount) - (int(byteCount) % bytesPerSample))


def pcm_has_audible_sample(
	pcm: bytes,
	*,
	noiseFloor: int = SILENCE_SAMPLE_THRESHOLD,
	bytesPerSample: int = PCM_BYTES_PER_SAMPLE,
) -> bool:
	if bytesPerSample != 2:
		raise ValueError("Only signed 16-bit PCM is supported")
	pcmLength = align_pcm_bytes(len(pcm), bytesPerSample)
	if pcmLength <= 0:
		return False
	samples = memoryview(pcm)[:pcmLength].cast("h")
	return any(sample < -noiseFloor or sample > noiseFloor for sample in samples)


class PcmSilenceShortener:
	"""Shorten final or all silent PCM runs without copying individual samples."""

	def __init__(
		self,
		*,
		sampleRate: int,
		shortenAllPauses: bool,
		keepSilenceMs: int = SHORTENED_SILENCE_KEEP_MS,
		noiseFloor: int = SILENCE_SAMPLE_THRESHOLD,
		bytesPerSample: int = PCM_BYTES_PER_SAMPLE,
	) -> None:
		if bytesPerSample != 2:
			raise ValueError("Only signed 16-bit PCM is supported")
		self._shortenAllPauses = bool(shortenAllPauses)
		self._noiseFloor = max(0, int(noiseFloor))
		self._bytesPerSample = bytesPerSample
		self._heldSilence = bytearray()
		self._partialSample = bytearray()
		self._keepSilenceBytes = pcm_bytes_for_milliseconds(keepSilenceMs, sampleRate, bytesPerSample)

	def _release_held_silence(self, *, final: bool) -> bytes:
		if not self._heldSilence:
			return b""
		if final or self._shortenAllPauses:
			output = bytes(self._heldSilence[: self._keepSilenceBytes])
		else:
			output = bytes(self._heldSilence)
		self._heldSilence.clear()
		return output

	def _hold_silence(self, pcm: bytes) -> None:
		if not pcm:
			return
		if not self._shortenAllPauses:
			self._heldSilence.extend(pcm)
			return
		bytesNeeded = self._keepSilenceBytes - len(self._heldSilence)
		if bytesNeeded > 0:
			self._heldSilence.extend(pcm[:bytesNeeded])

	def feed(self, pcm: bytes) -> bytes:
		if not pcm:
			return b""
		if self._partialSample:
			pcm = bytes(self._partialSample) + pcm
			self._partialSample.clear()
		pcmLength = align_pcm_bytes(len(pcm), self._bytesPerSample)
		if pcmLength < len(pcm):
			self._partialSample.extend(pcm[pcmLength:])
		if pcmLength <= 0:
			return b""
		pcm = pcm[:pcmLength]
		samples = memoryview(pcm).cast("h")
		output = bytearray()
		runStart = 0
		runIsSilence = -self._noiseFloor <= samples[0] <= self._noiseFloor
		for sampleIndex in range(1, len(samples)):
			isSilence = -self._noiseFloor <= samples[sampleIndex] <= self._noiseFloor
			if isSilence == runIsSilence:
				continue
			run = pcm[runStart * self._bytesPerSample : sampleIndex * self._bytesPerSample]
			if runIsSilence:
				self._hold_silence(run)
			else:
				output.extend(self._release_held_silence(final=False))
				output.extend(run)
			runStart = sampleIndex
			runIsSilence = isSilence
		run = pcm[runStart * self._bytesPerSample : pcmLength]
		if runIsSilence:
			self._hold_silence(run)
		else:
			output.extend(self._release_held_silence(final=False))
			output.extend(run)
		return bytes(output)

	def finish(self) -> bytes:
		# A partial 16-bit sample is malformed input and cannot be sent to WavePlayer.
		self._partialSample.clear()
		return self._release_held_silence(final=True)


def create_pcm_silence_shortener(pauseMode: str, sampleRate: int) -> PcmSilenceShortener | None:
	if pauseMode == PAUSE_MODE_DO_NOT_SHORTEN:
		return None
	if pauseMode == PAUSE_MODE_SHORTEN_END_ONLY:
		return PcmSilenceShortener(
			sampleRate=sampleRate,
			shortenAllPauses=False,
			keepSilenceMs=SHORTENED_SILENCE_KEEP_MS,
		)
	if pauseMode == PAUSE_MODE_SHORTEN_ALL:
		return PcmSilenceShortener(
			sampleRate=sampleRate,
			shortenAllPauses=True,
			keepSilenceMs=SHORTENED_ALL_PAUSES_KEEP_MS,
		)
	raise ValueError(f"Unknown pause mode: {pauseMode!r}")


def short_audio_cache_key(
	text: str,
	options: dict[str, Any],
	hiddenSegments: Sequence[str] | None = None,
	pauseShorteningMode: str = PAUSE_MODE_DO_NOT_SHORTEN,
	*,
	maxChars: int = SHORT_CACHE_MAX_CHARS,
	maxHiddenSegments: int = SHORT_CACHE_MAX_HIDDEN_SEGMENTS,
) -> tuple[Any, ...] | None:
	if len(text) > maxChars:
		return None
	if hiddenSegments:
		if len(hiddenSegments) > maxHiddenSegments:
			return None
		if len(text) + sum(len(segment) for segment in hiddenSegments) > maxChars:
			return None
	return (
		text,
		tuple(hiddenSegments or ()),
		*(options.get(field) for field in SHORT_AUDIO_CACHE_OPTION_FIELDS),
		pauseShorteningMode,
	)


@lru_cache(maxsize=4096)
def _unicode_name(character: str) -> str:
	return unicodedata.name(character, "")


@lru_cache(maxsize=4096)
def _is_sentence_terminator_character(character: str) -> bool:
	return ord(character) in SENTENCE_TERMINAL_CODEPOINTS or character in TAILORED_SENTENCE_TERMINATORS


@lru_cache(maxsize=4096)
def _is_soft_break_character(character: str) -> bool:
	if character in SOFT_BREAK_CHARS:
		return True
	if character in ASCII_SENTENCE_TERMINATORS:
		return False
	if _is_sentence_terminator_character(character):
		return True
	category = unicodedata.category(character)
	if character in NON_BREAKING_SOFT_PUNCTUATION:
		return False
	name = _unicode_name(character)
	if any(part in name for part in UNICODE_INITIAL_PUNCTUATION_NAME_PARTS):
		return False
	if any(part in name for part in NON_BREAKING_SOFT_PUNCTUATION_NAME_PARTS):
		return False
	if category in {"Pd", "Po"}:
		return True
	return any(part in name for part in UNICODE_SOFT_BREAK_NAME_PARTS)


@lru_cache(maxsize=4096)
def _is_colon_like_character(character: str) -> bool:
	return character in ":：" or "COLON" in _unicode_name(character)


@lru_cache(maxsize=4096)
def _is_dash_like_character(character: str) -> bool:
	return character in "\u2013\u2014" or "DASH" in _unicode_name(character)


@lru_cache(maxsize=1024)
def _is_sentence_trailing_closer(character: str) -> bool:
	return (
		character in SENTENCE_TRAILING_CLOSERS
		or "\u2018" <= character <= "\u201F"
		or unicodedata.category(character) in {"Pe", "Pf"}
	)


def _is_no_space_script_character(character: str) -> bool:
	codepoint = ord(character)
	category = unicodedata.category(character)
	if not (category.startswith("L") or category.startswith("M")):
		return False
	return any(
		start <= codepoint <= end
		for ranges, _limit in NO_SPACE_SCRIPT_PROFILES
		for start, end in ranges
	)


class TextSegmenter:
	"""Unicode-aware latency segmenter independent of NVDA speech commands."""

	def iter_indexed_text_segments(
		self,
		text: str,
		indexes: list[tuple[Any, int]],
		fastFirstSegment: bool,
	) -> Iterator[tuple[str, list[tuple[Any, int]]]]:
		if not indexes:
			for segment in self.iter_text_segments_for_latency(text, fastFirstSegment):
				yield segment, []
			return
		segments: list[tuple[str, int, int]] = []
		searchStart = 0
		for segment in self.iter_text_segments_for_latency(text, fastFirstSegment):
			segmentStart = text.find(segment, searchStart)
			if segmentStart < 0:
				segmentStart = searchStart
			segmentEnd = segmentStart + len(segment)
			segments.append((segment, segmentStart, segmentEnd))
			searchStart = segmentEnd
		if not segments:
			return
		indexPosition = 0
		for segmentPosition, (segment, segmentStart, segmentEnd) in enumerate(segments):
			segmentIndexes: list[tuple[Any, int]] = []
			while indexPosition < len(indexes) and indexes[indexPosition][1] <= segmentEnd:
				index, charOffset = indexes[indexPosition]
				segmentIndexes.append((index, max(0, min(len(segment), charOffset - segmentStart))))
				indexPosition += 1
			if segmentPosition == len(segments) - 1:
				while indexPosition < len(indexes):
					index, _charOffset = indexes[indexPosition]
					segmentIndexes.append((index, len(segment)))
					indexPosition += 1
			yield segment, segmentIndexes

	def split_text_for_latency(self, text: str) -> list[str]:
		return list(self.iter_text_segments_for_latency(text, False))

	def sanitize_speech_text(self, text: str) -> str:
		if not text:
			return text
		return "".join(" " if unicodedata.category(character) == "Co" else character for character in text)

	def spoken_bridge_segments(self, segments: list[str]) -> list[str]:
		spokenSegments: list[str] = []
		for segment in segments:
			if (
				spokenSegments
				and spokenSegments[-1]
				and segment
				and self._needs_spoken_segment_space(spokenSegments[-1][-1], segment[0])
			):
				spokenSegments[-1] += " "
			spokenSegments.append(segment)
		return spokenSegments

	def _needs_spoken_segment_space(self, previousCharacter: str, nextCharacter: str) -> bool:
		if not previousCharacter.isalnum() or not nextCharacter.isalnum():
			return False
		return not (
			_is_no_space_script_character(previousCharacter)
			or _is_no_space_script_character(nextCharacter)
		)

	def find_sentence_splits(self, text: str) -> list[int]:
		splits: list[int] = []
		index = 0
		while index < len(text):
			terminatorStart = index
			terminator = text[index]
			if not _is_sentence_terminator_character(terminator):
				index += 1
				continue
			index += 1
			while index < len(text) and _is_sentence_terminator_character(text[index]):
				index += 1
			terminatorEnd = index
			while index < len(text) and _is_sentence_trailing_closer(text[index]):
				index += 1
			whitespaceStart = index
			while index < len(text) and text[index].isspace():
				index += 1
			trailingWhitespace = text[whitespaceStart:index]
			if index == len(text):
				continue
			if self._sentence_terminator_stays_with_token(text, terminatorStart, terminatorEnd, terminator):
				continue
			if terminator in ASCII_SENTENCE_TERMINATORS + ";":
				if not trailingWhitespace:
					continue
			else:
				splits.append(index)
				continue
			splits.append(index)
		return splits

	def _sentence_terminator_stays_with_token(
		self,
		text: str,
		terminatorStart: int,
		terminatorEnd: int,
		terminator: str,
	) -> bool:
		before = text[terminatorStart - 1] if terminatorStart > 0 else ""
		after = text[terminatorEnd] if terminatorEnd < len(text) else ""
		if before.isdigit() and after.isdigit():
			return True
		if terminator == ".":
			return self._period_stays_with_previous_token(text, terminatorStart)
		return False

	def _period_stays_with_previous_token(self, text: str, periodIndex: int) -> bool:
		if self._period_is_numeric_separator(text, periodIndex):
			return True
		wordStart = periodIndex - 1
		while wordStart >= 0 and text[wordStart].isalnum():
			wordStart -= 1
		wordBefore = text[wordStart + 1 : periodIndex].lower()
		if len(wordBefore) == 1 and wordBefore.isalpha():
			return True
		if wordBefore in COMMON_ABBREVIATIONS:
			return True
		return wordBefore.isalpha() and wordStart >= 0 and text[wordStart] == "."

	def _period_is_numeric_separator(self, text: str, periodIndex: int) -> bool:
		before = text[periodIndex - 1] if periodIndex > 0 else ""
		after = text[periodIndex + 1] if periodIndex + 1 < len(text) else ""
		return before.isdigit() and after.isdigit()

	def iter_text_segments_for_latency(self, text: str, fastFirstSegment: bool) -> Iterator[str]:
		if not text.strip():
			return
		splits = self.find_sentence_splits(text)
		chunkStart = 0
		firstYield = True
		for endIndex in splits + [len(text)]:
			candidate = text[chunkStart:endIndex].strip()
			if not candidate:
				continue
			targetLength = FAST_FIRST_SEGMENT_MIN_CHARS if (firstYield and fastFirstSegment) else REGULAR_SEGMENT_MIN_CHARS
			if len(candidate) >= targetLength or endIndex == len(text):
				for segment in self._iter_forced_latency_segments(candidate, firstYield):
					yield segment
					firstYield = False
				chunkStart = endIndex

	def _iter_forced_latency_segments(self, text: str, fastFirstSegment: bool) -> Iterator[str]:
		remaining = text.strip()
		if len(remaining) <= SEAMLESS_UTTERANCE_MAX_CHARS:
			yield from self._iter_soft_phrase_segments(remaining, fastFirstSegment)
			return
		firstYield = fastFirstSegment
		while remaining:
			if self.looks_like_url_token(remaining):
				maxLength = min(URL_TOKEN_SEGMENT_MAX_CHARS, FORCED_SEGMENT_HARD_MAX_CHARS)
			else:
				maxLength = FAST_FIRST_SEGMENT_MAX_CHARS if firstYield else REGULAR_SEGMENT_MAX_CHARS
			noSpaceLimit = self._no_space_script_segment_limit(remaining, maxLength)
			if noSpaceLimit is not None:
				maxLength = min(maxLength, noSpaceLimit)
			if len(remaining) <= maxLength:
				yield remaining
				return
			cut = self._find_forced_latency_cut(remaining, maxLength)
			segment = remaining[:cut].strip()
			if segment:
				yield segment
			remaining = remaining[cut:].strip()
			firstYield = False

	def _iter_soft_phrase_segments(self, text: str, fastFirstSegment: bool) -> Iterator[str]:
		remaining = text.strip()
		firstSegment = fastFirstSegment
		noSpaceSegmentation = (
			len(remaining) > SOFT_PHRASE_SEGMENT_MAX_CHARS
			and self._no_space_script_segment_limit(remaining, SOFT_PHRASE_SEGMENT_MAX_CHARS) is not None
		)
		while len(remaining) > SOFT_PHRASE_SEGMENT_MAX_CHARS or noSpaceSegmentation:
			noSpaceLimit = (
				self._no_space_script_segment_limit(remaining, SOFT_PHRASE_SEGMENT_MAX_CHARS)
				if noSpaceSegmentation
				else None
			)
			if noSpaceLimit is None:
				noSpaceSegmentation = False
				if len(remaining) <= SOFT_PHRASE_SEGMENT_MAX_CHARS:
					break
			if noSpaceLimit is not None and len(remaining) <= noSpaceLimit:
				break
			cut = (
				self._find_no_space_script_cut(remaining, noSpaceLimit)
				if noSpaceLimit is not None
				else self._find_soft_phrase_cut(remaining, firstSegment)
			)
			if cut is None:
				cut = self._find_whitespace_cut(
					remaining,
					SOFT_PHRASE_SEGMENT_MIN_CHARS,
					SOFT_PHRASE_SEGMENT_MAX_CHARS,
					SOFT_PHRASE_SEGMENT_LOOKAHEAD,
				)
			if cut is None:
				cut = min(len(remaining), FORCED_SEGMENT_HARD_MAX_CHARS)
			segment = remaining[:cut].strip()
			if segment:
				yield segment
			nextRemaining = remaining[cut:].strip()
			if nextRemaining == remaining:
				yield nextRemaining
				return
			remaining = nextRemaining
			firstSegment = False
		if remaining:
			yield remaining

	def _find_soft_phrase_cut(self, text: str, fastFirstSegment: bool = False) -> int | None:
		if fastFirstSegment:
			minLength = min(len(text), FAST_SOFT_PHRASE_SEGMENT_MIN_CHARS)
			maxLength = min(len(text), FAST_SOFT_PHRASE_SEGMENT_MAX_CHARS)
			lookahead = FAST_SOFT_PHRASE_SEGMENT_LOOKAHEAD
		else:
			minLength = min(len(text), SOFT_PHRASE_SEGMENT_MIN_CHARS)
			maxLength = min(len(text), SOFT_PHRASE_SEGMENT_MAX_CHARS)
			lookahead = SOFT_PHRASE_SEGMENT_LOOKAHEAD
		for index in range(maxLength, minLength - 1, -1):
			if self._is_contextual_soft_phrase_cut(text, index):
				return index
		lookaheadEnd = min(len(text), maxLength + lookahead)
		for index in range(maxLength, lookaheadEnd):
			if self._is_contextual_soft_phrase_cut(text, index + 1):
				return index + 1
		return None

	def _find_whitespace_cut(self, text: str, minLength: int, maxLength: int, lookahead: int) -> int | None:
		minLength = min(len(text), minLength)
		maxLength = min(len(text), maxLength)
		for index in range(maxLength, minLength - 1, -1):
			if text[index - 1].isspace():
				return index
		lookaheadEnd = min(len(text), maxLength + lookahead)
		for index in range(maxLength, lookaheadEnd):
			if text[index].isspace():
				return index
		return self._find_no_space_script_cut(text, maxLength)

	def _find_forced_latency_cut(self, text: str, maxLength: int) -> int:
		if len(text) <= maxLength:
			return len(text)
		minLength = min(maxLength, max(FORCED_SEGMENT_MIN_CHARS, int(maxLength * 0.55)))
		for index in range(maxLength, minLength - 1, -1):
			if self._is_contextual_soft_phrase_cut(text, index):
				return index
		for index in range(maxLength, minLength - 1, -1):
			if text[index - 1].isspace():
				return index
		lookaheadEnd = min(len(text), maxLength + FORCED_SEGMENT_FORWARD_LOOKAHEAD)
		for index in range(maxLength, lookaheadEnd):
			if text[index].isspace():
				return index
		noSpaceCut = self._find_no_space_script_cut(text, maxLength)
		if noSpaceCut is not None:
			return noSpaceCut
		urlBreakCharacters = "/\\?&=#%._-~:"
		for index in range(maxLength, minLength - 1, -1):
			if text[index - 1] in urlBreakCharacters:
				return index
		for index in range(maxLength, lookaheadEnd):
			if text[index] in urlBreakCharacters:
				return index + 1
		if text[maxLength - 1].isalnum() and text[maxLength].isalnum():
			wordEnd = min(len(text), FORCED_SEGMENT_HARD_MAX_CHARS)
			for index in range(maxLength, wordEnd):
				if not text[index].isalnum():
					return index
		return maxLength

	def _find_no_space_script_cut(self, text: str, maxLength: int) -> int | None:
		segmentLimit = self._no_space_script_segment_limit(text, maxLength)
		if segmentLimit is None:
			return None
		target = min(len(text), maxLength, max(FORCED_SEGMENT_MIN_CHARS, segmentLimit))
		for index in range(target, FORCED_SEGMENT_MIN_CHARS - 1, -1):
			if self._is_contextual_soft_phrase_cut(text, index):
				return index
		return self._extend_cut_over_combining_marks(
			text,
			target,
			min(len(text), maxLength + NO_SPACE_SCRIPT_COMBINING_LOOKAHEAD),
		)

	def _no_space_script_segment_limit(self, text: str, maxLength: int) -> int | None:
		sample = text[: min(len(text), maxLength)]
		if not sample:
			return None
		signalCharacters = 0
		noSpaceCharacters = 0
		segmentLimit: int | None = None
		for character in sample:
			category = unicodedata.category(character)
			if category.startswith("L") or category.startswith("M"):
				signalCharacters += 1
				codepoint = ord(character)
				for ranges, limit in NO_SPACE_SCRIPT_PROFILES:
					if any(start <= codepoint <= end for start, end in ranges):
						noSpaceCharacters += 1
						segmentLimit = limit if segmentLimit is None else min(segmentLimit, limit)
						break
		if not signalCharacters:
			return None
		if noSpaceCharacters < NO_SPACE_SCRIPT_SIGNAL_MIN_CHARS:
			return None
		if noSpaceCharacters / signalCharacters < NO_SPACE_SCRIPT_SIGNAL_MIN_RATIO:
			return None
		return segmentLimit

	def _extend_cut_over_combining_marks(self, text: str, cut: int, maxCut: int) -> int:
		while cut < maxCut and unicodedata.category(text[cut]).startswith("M"):
			cut += 1
		return cut

	def looks_like_url_token(self, text: str) -> bool:
		if any(character.isspace() for character in text):
			return False
		return "://" in text or "/" in text or "\\" in text

	def _is_forced_soft_break(self, text: str, index: int) -> bool:
		character = text[index - 1]
		before = text[index - 2] if index >= 2 else ""
		after = text[index] if index < len(text) else ""
		if before.isdigit() and after.isdigit():
			return False
		if _is_colon_like_character(character):
			if character == ":" and after in "/\\":
				if index == 2 and text[0].isalpha():
					return False
				schemeStart = index - 2
				while schemeStart >= 0 and (text[schemeStart].isalnum() or text[schemeStart] in "+-."):
					schemeStart -= 1
				scheme = text[schemeStart + 1 : index - 1]
				if scheme and scheme[0].isalpha() and text[index : index + 2] == "//":
					return False
		if _is_dash_like_character(character) and before.isalnum() and after.isalnum():
			return False
		return True

	def _is_contextual_soft_phrase_cut(self, text: str, index: int) -> bool:
		if index <= 0 or index > len(text):
			return False
		return _is_soft_break_character(text[index - 1]) and self._is_forced_soft_break(text, index)

	def should_pause_after_segment(self, segment: str) -> bool:
		stripped = segment.rstrip()
		while stripped and _is_sentence_trailing_closer(stripped[-1]):
			stripped = stripped[:-1].rstrip()
		return bool(stripped) and _is_sentence_terminator_character(stripped[-1])


DEFAULT_TEXT_SEGMENTER = TextSegmenter()
