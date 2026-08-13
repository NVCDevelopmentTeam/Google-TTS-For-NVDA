from __future__ import annotations

import unittest

from tests.test_support import load_driver_module, pcm_bytes as _pcm, pcm_samples as _samples


class PcmSilenceShortenerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.processing = load_driver_module("speech_processing")

    def _process(self, mode: str, pcm: bytes, chunks: list[int] | None = None) -> bytes:
        shortener = self.processing.create_pcm_silence_shortener(mode, 1000)
        if shortener is None:
            return pcm
        output = bytearray()
        if chunks is None:
            chunks = [len(pcm)]
        offset = 0
        for length in chunks:
            output.extend(shortener.feed(pcm[offset : offset + length]))
            offset += length
        output.extend(shortener.feed(pcm[offset:]))
        output.extend(shortener.finish())
        return bytes(output)

    def test_three_pause_modes(self) -> None:
        pcm = _pcm(*([1000] * 4), *([0] * 100), *([1200] * 4), *([0] * 100))
        do_not_shorten = self._process(self.processing.PAUSE_MODE_DO_NOT_SHORTEN, pcm)
        end_only = _samples(self._process(self.processing.PAUSE_MODE_SHORTEN_END_ONLY, pcm))
        shorten_all = _samples(self._process(self.processing.PAUSE_MODE_SHORTEN_ALL, pcm))

        self.assertEqual(pcm, do_not_shorten)
        self.assertEqual((*([1000] * 4), *([0] * 100), *([1200] * 4), *([0] * 35)), end_only)
        self.assertEqual((*([1000] * 4), *([0] * 25), *([1200] * 4), *([0] * 25)), shorten_all)

    def test_noise_floor_is_inclusive(self) -> None:
        self.assertFalse(self.processing.pcm_has_audible_sample(_pcm(-48, 0, 48)))
        self.assertTrue(self.processing.pcm_has_audible_sample(_pcm(-49)))
        self.assertTrue(self.processing.pcm_has_audible_sample(_pcm(49)))

        shortener = self.processing.create_pcm_silence_shortener(
            self.processing.PAUSE_MODE_SHORTEN_ALL,
            1000,
        )
        output = shortener.feed(_pcm(*([48] * 60), 49, -49, *([0] * 60))) + shortener.finish()
        self.assertEqual((*([48] * 25), 49, -49, *([0] * 25)), _samples(output))

    def test_pcm_chunk_boundaries_do_not_change_output(self) -> None:
        pcm = _pcm(*([0] * 60), *([800] * 7), *([0] * 60), *([-900] * 7), *([0] * 60))
        whole = self._process(self.processing.PAUSE_MODE_SHORTEN_ALL, pcm)
        for split_offset in range(len(pcm) + 1):
            with self.subTest(single_split=split_offset):
                self.assertEqual(
                    whole,
                    self._process(self.processing.PAUSE_MODE_SHORTEN_ALL, pcm, chunks=[split_offset]),
                )
        for chunk_size in (1, 2, 3, 5, 17, 64):
            with self.subTest(chunk_size=chunk_size):
                chunks = [chunk_size] * (len(pcm) // chunk_size)
                self.assertEqual(
                    whole,
                    self._process(self.processing.PAUSE_MODE_SHORTEN_ALL, pcm, chunks=chunks),
                )

    def test_incomplete_detection_block_is_flushed_at_finish(self) -> None:
        shortener = self.processing.create_pcm_silence_shortener(
            self.processing.PAUSE_MODE_SHORTEN_ALL,
            1000,
        )
        pcm = _pcm(700, 700, 0, 0)
        self.assertEqual(b"", shortener.feed(pcm))
        self.assertEqual(pcm, shortener.finish())

    def test_end_only_preserves_hidden_boundary_and_shortens_final_end(self) -> None:
        shortener = self.processing.create_pcm_silence_shortener(
            self.processing.PAUSE_MODE_SHORTEN_END_ONLY,
            1000,
        )
        self.assertIsNotNone(shortener)
        first = shortener.feed(_pcm(*([700] * 3), *([0] * 90))) + shortener.flush_boundary(
            shortenPause=False,
        )
        second = shortener.feed(_pcm(*([900] * 2), *([0] * 80))) + shortener.finish()
        self.assertEqual((*([700] * 3), *([0] * 90)), _samples(first))
        self.assertEqual((*([900] * 2), *([0] * 35)), _samples(second))

    def test_shorten_all_shortens_hidden_boundary_and_final_end(self) -> None:
        shortener = self.processing.create_pcm_silence_shortener(
            self.processing.PAUSE_MODE_SHORTEN_ALL,
            1000,
        )
        self.assertIsNotNone(shortener)
        first = shortener.feed(_pcm(*([700] * 3), *([0] * 90))) + shortener.flush_boundary(
            shortenPause=True,
        )
        second = shortener.feed(_pcm(*([900] * 2), *([0] * 80))) + shortener.finish()
        self.assertEqual((*([700] * 3), *([0] * 25)), _samples(first))
        self.assertEqual((*([900] * 2), *([0] * 25)), _samples(second))

    def test_unknown_pause_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.processing.create_pcm_silence_shortener("unknown", 24000)


class PcmLeadBufferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.processing = load_driver_module("speech_processing")

    def test_lead_is_released_once_and_later_pcm_passes_through(self) -> None:
        lead = self.processing.PcmLeadBuffer(sampleRate=1000, leadMs=3)
        self.assertEqual(b"", lead.feed(b"\x01\x02"))
        self.assertEqual(b"\x01\x02\x03\x04\x05\x06", lead.feed(b"\x03\x04\x05\x06"))
        self.assertEqual(b"\x07\x08", lead.feed(b"\x07\x08"))
        self.assertEqual(b"", lead.finish())

    def test_short_audio_flushes_without_loss(self) -> None:
        lead = self.processing.PcmLeadBuffer(sampleRate=1000, leadMs=10)
        self.assertEqual(b"", lead.feed(b"\x01\x02\x03\x04"))
        self.assertEqual(b"\x01\x02\x03\x04", lead.finish())
        self.assertEqual(b"\x05\x06", lead.feed(b"\x05\x06"))


class TextSegmenterLatencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.processing = load_driver_module("speech_processing")
        cls.segmenter = cls.processing.DEFAULT_TEXT_SEGMENTER

    def test_medium_fast_first_segmentation_is_opt_in(self) -> None:
        text = (
            "This medium announcement deliberately has no punctuation and remains below the regular "
            "phrase limit while still being long enough to benefit from a fast first segment"
        )

        regular = list(self.segmenter.iter_text_segments_for_latency(text, False))
        fast_first = list(self.segmenter.iter_text_segments_for_latency(text, True))

        self.assertEqual([text], regular)
        self.assertGreaterEqual(len(fast_first), 2)
        self.assertLessEqual(len(fast_first[0]), self.processing.FAST_SOFT_PHRASE_SEGMENT_MAX_CHARS)
        self.assertEqual("".join(text.split()), "".join("".join(fast_first).split()))

    def test_fast_first_limit_applies_only_to_first_segment(self) -> None:
        text = " ".join(["latency"] * 100)

        segments = list(self.segmenter.iter_text_segments_for_latency(text, True))

        self.assertGreaterEqual(len(segments), 3)
        self.assertLessEqual(len(segments[0]), self.processing.FAST_SOFT_PHRASE_SEGMENT_MAX_CHARS)
        self.assertTrue(any(len(segment) > self.processing.FAST_SOFT_PHRASE_SEGMENT_MAX_CHARS for segment in segments[1:]))


class ShortAudioCacheKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.processing = load_driver_module("speech_processing")
        cls.expected_option_fields = (
            "voiceId",
            "rate",
            "pitch",
            "postPitch",
            "volume",
            "outputGain",
            "artificialRate",
        )
        cls.options = {
            "voiceId": "vi-vn-x-multi:gft",
            "rate": 1.0,
            "pitch": 0.0,
            "postPitch": 1.0,
            "volume": 1.0,
            "outputGain": 1.75,
            "artificialRate": 1.0,
        }

    def test_cache_key_covers_audio_and_segmentation_inputs(self) -> None:
        self.assertEqual(self.expected_option_fields, self.processing.SHORT_AUDIO_CACHE_OPTION_FIELDS)
        baseline = self.processing.short_audio_cache_key("hello", self.options)
        pause_mode_keys = {
            self.processing.short_audio_cache_key(
                "hello",
                self.options,
                pauseShorteningMode=pause_mode,
            )
            for pause_mode in (
                self.processing.PAUSE_MODE_DO_NOT_SHORTEN,
                self.processing.PAUSE_MODE_SHORTEN_END_ONLY,
                self.processing.PAUSE_MODE_SHORTEN_ALL,
            )
        }
        changed_hidden_segments = self.processing.short_audio_cache_key("hello", self.options, ["hel", "lo"])
        changed_pitch_options = dict(self.options, postPitch=1.1)
        changed_pitch = self.processing.short_audio_cache_key("hello", changed_pitch_options)

        self.assertIsNotNone(baseline)
        self.assertEqual(3, len(pause_mode_keys))
        self.assertNotEqual(baseline, changed_hidden_segments)
        self.assertNotEqual(baseline, changed_pitch)
        for option_name in self.options:
            with self.subTest(option=option_name):
                changed_options = dict(self.options)
                changed_options[option_name] = f"changed-{option_name}"
                self.assertNotEqual(
                    baseline,
                    self.processing.short_audio_cache_key("hello", changed_options),
                )

    def test_cache_key_rejects_oversized_text_or_hidden_segments(self) -> None:
        self.assertIsNone(self.processing.short_audio_cache_key("x" * 5001, self.options))
        self.assertIsNone(self.processing.short_audio_cache_key("x", self.options, ["x"] * 25))
        self.assertIsNotNone(self.processing.short_audio_cache_key("x" * 4999, self.options, ["x" * 4999]))
        self.assertIsNone(self.processing.short_audio_cache_key("x", self.options, ["x" * 5001]))

    def test_segment_cache_key_covers_boundary_context(self) -> None:
        baseline = self.processing.segment_audio_cache_key(
            "hello",
            self.options,
            self.processing.PAUSE_MODE_SHORTEN_ALL,
            hasPreviousSegment=False,
            hasNextSegment=True,
        )
        changed_previous = self.processing.segment_audio_cache_key(
            "hello",
            self.options,
            self.processing.PAUSE_MODE_SHORTEN_ALL,
            hasPreviousSegment=True,
            hasNextSegment=True,
        )
        changed_next = self.processing.segment_audio_cache_key(
            "hello",
            self.options,
            self.processing.PAUSE_MODE_SHORTEN_ALL,
            hasPreviousSegment=False,
            hasNextSegment=False,
        )

        self.assertIsNotNone(baseline)
        self.assertNotEqual(baseline, changed_previous)
        self.assertNotEqual(baseline, changed_next)
        self.assertIsNone(
            self.processing.segment_audio_cache_key(
                "hello",
                dict(self.options, artificialRate=1.2),
                self.processing.PAUSE_MODE_SHORTEN_ALL,
                hasPreviousSegment=False,
                hasNextSegment=True,
            )
        )
        self.assertIsNone(
            self.processing.segment_audio_cache_key(
                "hello",
                dict(self.options, postPitch=1.1),
                self.processing.PAUSE_MODE_SHORTEN_ALL,
                hasPreviousSegment=False,
                hasNextSegment=True,
            )
        )
        self.assertIsNone(
            self.processing.segment_audio_cache_key(
                "",
                self.options,
                self.processing.PAUSE_MODE_SHORTEN_ALL,
                hasPreviousSegment=False,
                hasNextSegment=False,
            )
        )

    def test_complete_speech_result_requires_all_boundaries(self) -> None:
        complete = {"success": True, "done": True, "cancelled": False, "segmentEnds": 2}

        self.assertTrue(self.processing.is_complete_speech_result(complete, expectedSegmentEnds=2))
        self.assertFalse(self.processing.is_complete_speech_result(complete, expectedSegmentEnds=1))
        self.assertFalse(
            self.processing.is_complete_speech_result(
                dict(complete, cancelled=True),
                expectedSegmentEnds=2,
            )
        )
        self.assertFalse(
            self.processing.is_complete_speech_result(
                dict(complete, done=False),
                expectedSegmentEnds=2,
            )
        )
        self.assertFalse(
            self.processing.is_complete_speech_result(
                dict(complete, segmentEnds="invalid"),
                expectedSegmentEnds=2,
            )
        )
        self.assertTrue(
            self.processing.is_complete_speech_result(
                {"success": True, "done": True},
                expectedSegmentEnds=0,
            )
        )


if __name__ == "__main__":
    unittest.main()
