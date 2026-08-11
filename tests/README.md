# Segmentation corpus

`segmentation_corpus.json` records locale punctuation, abbreviation, URL, emoji,
CJK/Thai no-space text, and long-sentence cases. The source notes come from the
decompiled Google APK `MarkupGenerator` path, which selects a locale-specific
Java `BreakIterator`, trims emitted sentences, and diagnoses sentences longer
than 200 UTF-16 code units.

The corpus expected results describe this add-on's segmenter rather than claiming
byte-for-byte parity with Java `BreakIterator`.

The corpus imports the production `speech_processing.py` module directly. That
module has no NVDA imports, so the test exercises the real segmenter rather than
an AST copy of its implementation.

`test_support.py` owns repository paths, isolated loading of pure driver
modules, and PCM packing helpers. Reuse it when adding standalone tests instead
of modifying `sys.path` or copying loaders into individual test files. Corpus
schema version 1, required fields, unique IDs, categories, and operations are
validated before behavioral cases run.

`test_speech_processing.py` covers all three pause modes, the inclusive PCM
noise floor, arbitrary/odd PCM packet boundaries, hidden-segment finalization,
bounded lead buffering, whole/segment cache identity, and safe boundary-context
reuse. The corpus also protects the medium-text fast-first fallback.

`test_dependency_isolation.py` verifies that the browser bridge loads Google TTS
For NVDA's private vendored WebSocket client even when another add-on has already
registered a top-level `websocket` module. It also prevents optional third-party
imports from leaking into the vendored client and checks that CLD2, browser files,
and the pinned WASM engine stay anchored to this add-on's own directories.

`test_runtime_recovery.py` covers browser-reported speech failures: a request may
be retried once with a fresh runtime only before any PCM is emitted, partial audio
must never be repeated, and unhealthy or disconnected runtimes cannot be retained
by standby mode.

Run all standalone tests without importing NVDA:

```powershell
python -m unittest discover -s tests -v
```

`test_unicode_data.py` also verifies that every language root in the latest bundled
`voices.json` has generated script data and that the pinned Unicode sentence-terminal
table is complete. It imports the production `language_profiles.py` fallback
directly rather than extracting methods from the NVDA synth driver. The expected
script map covers every bundled language root, every mapped script is exercised
against a disjoint candidate, and shared-script language pairs must remain
ambiguous instead of being assigned arbitrarily.
`unicode_data.py` is generated from UCD 17.0 and CLDR 48.2 with:

```powershell
python generate_unicode_data.py --ucd-dir <ucd-directory> `
  --likely-subtags <cldr-likelySubtags.xml> --cldr-version 48.2
```

## NVDA compatibility and runtime testing

Check the add-on's static API contracts against every local NVDA checkout:

```powershell
python tests\check_nvda_api_contracts.py
```

The default looks for a sibling `NVDA source code` directory. A checkout or a
different parent directory can be supplied explicitly:

```powershell
python tests\check_nvda_api_contracts.py <NVDA-source-directory>
```

The script covers the synth driver and audio output, global plugin, speech hooks
and language profiles, Settings category, Voice Manager, updater, browser
runtime/standby, and shared NVDA state. It reports the actual high-risk
`setSynth`, `WavePlayer`, `nvwave.isInError`, output-device configuration, and
`AutoSettingsMixin.refreshGui` contracts found in each checkout.

Static inspection cannot validate WASM/browser startup, audible PCM quality, or
screen-reader focus announcements. Use
`NVDA_CHROMIUM_MANUAL_CHECKLIST.md` for those release tests and retain the test
record as evidence.
