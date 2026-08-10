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

    def test_hidden_segment_boundary_shortens_each_segment_end(self) -> None:
        shortener = self.processing.create_pcm_silence_shortener(
            self.processing.PAUSE_MODE_SHORTEN_END_ONLY,
            1000,
        )
        self.assertIsNotNone(shortener)
        first = shortener.feed(_pcm(*([700] * 3), *([0] * 90))) + shortener.finish()
        second = shortener.feed(_pcm(*([900] * 2), *([0] * 80))) + shortener.finish()
        self.assertEqual((*([700] * 3), *([0] * 35)), _samples(first))
        self.assertEqual((*([900] * 2), *([0] * 35)), _samples(second))

    def test_unknown_pause_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.processing.create_pcm_silence_shortener("unknown", 24000)


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
        changed_mode = self.processing.short_audio_cache_key(
            "hello",
            self.options,
            pauseShorteningMode=self.processing.PAUSE_MODE_SHORTEN_ALL,
        )
        changed_hidden_segments = self.processing.short_audio_cache_key("hello", self.options, ["hel", "lo"])
        changed_pitch_options = dict(self.options, postPitch=1.1)
        changed_pitch = self.processing.short_audio_cache_key("hello", changed_pitch_options)

        self.assertIsNotNone(baseline)
        self.assertNotEqual(baseline, changed_mode)
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
        self.assertIsNone(self.processing.short_audio_cache_key("x" * 4999, self.options, ["xx"]))


if __name__ == "__main__":
    unittest.main()
