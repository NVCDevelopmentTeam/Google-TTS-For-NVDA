# -*- coding: utf-8 -*-
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import threading
from typing import Any, Callable

import config
import globalVars
from logHandler import log

from . import language_detector, voice_store
from .bridge import (
	CdpCancelled,
	ChromeTtsBridge,
	CONFIG_AUTO_LANGUAGE_CANDIDATES,
	CONFIG_AUTO_LANGUAGE_DETECTION,
	CONFIG_AUTO_LANGUAGE_PREFERRED,
	CONFIG_AUTO_LANGUAGE_PROFILES,
	CONFIG_SECTION,
	DEFAULT_AUTO_LANGUAGE_CANDIDATES,
	DEFAULT_AUTO_LANGUAGE_DETECTION,
	DEFAULT_AUTO_LANGUAGE_PREFERRED,
	DEFAULT_AUTO_LANGUAGE_PROFILES,
	DEFAULT_KEEP_BROWSER_RUNTIME_READY,
	configured_keep_browser_runtime_ready,
	configured_browser_runtime,
)
from .catalog import VoiceCatalog


SYNTH_NAME = "googleTtsForNvda"
ADDON_DIR = Path(__file__).resolve().parents[2]
_VOICE_WARMUP_TEXT = " "
_OUTPUT_GAIN_MAKEUP = 2.0
_PROTECTED_ENGINE_RATE = 1.18
_MIN_ARTIFICIAL_RATE = 0.5
_MAX_ARTIFICIAL_RATE = 2.2


def _config_bool(value: Any, default: bool = False) -> bool:
	if isinstance(value, str):
		return value.strip().lower() in ("1", "true", "yes", "on")
	if value is None:
		return default
	return bool(value)


def keep_browser_runtime_ready_enabled() -> bool:
	try:
		if globalVars.appArgs.secure:
			return False
	except Exception:
		log.debug("Could not read NVDA secure-mode state for Google TTS standby.", exc_info=True)
		return False
	try:
		return configured_keep_browser_runtime_ready()
	except Exception:
		log.debug("Could not read Google TTS standby browser runtime setting.", exc_info=True)
		return DEFAULT_KEEP_BROWSER_RUNTIME_READY


def _normalize_language(language: str | None) -> str:
	return str(language or "").replace("_", "-").lower()


def _language_root(language: str | None) -> str:
	return _normalize_language(language).split("-", 1)[0]


def _language_match_keys(language: str | None) -> set[str]:
	key = _normalize_language(language)
	if not key:
		return set()
	return language_detector.language_match_keys(key)


def _language_matches(left: str | None, right: str | None) -> bool:
	leftKeys = _language_match_keys(left)
	rightKeys = _language_match_keys(right)
	return bool(leftKeys and rightKeys and leftKeys.intersection(rightKeys))


def _profile_int(value: Any, default: int) -> int:
	try:
		return max(0, min(100, int(value)))
	except (TypeError, ValueError):
		return max(0, min(100, int(default)))


def _profile_bool(value: Any, default: bool = False) -> bool:
	return _config_bool(value, default)


def _installed_catalog() -> VoiceCatalog | None:
	fullCatalog = VoiceCatalog.load()
	installedPackages = voice_store.installed_packages(fullCatalog)
	catalog = VoiceCatalog(installedPackages)
	if not catalog.speakers:
		return None
	return catalog


def _catalog_signature(catalog: VoiceCatalog) -> tuple[Any, ...]:
	packageSignature = tuple(
		(package.id, package.compressedSize, package.sha256Checksum, package.dependentVoiceId)
		for package in catalog.packages
	)
	try:
		warmupSignature = tuple(_warmup_voice_ids(catalog, _current_speech_state(catalog)))
	except Exception:
		log.debug("Could not build Google TTS standby warmup signature.", exc_info=True)
		warmupSignature = ()
	return (configured_browser_runtime(), packageSignature, warmupSignature)


def _speakers_by_package(catalog: VoiceCatalog) -> dict[str, list[Any]]:
	grouped: dict[str, list[Any]] = {}
	for speaker in catalog.speakers:
		grouped.setdefault(speaker.packageId, []).append(speaker)
	return grouped


def _speakers_for_language(catalog: VoiceCatalog, language: str | None) -> list[Any]:
	if not language:
		return []
	speakersByLanguage = catalog.voices_by_language()
	speakers = speakersByLanguage.get(language)
	if speakers is not None:
		return list(speakers)
	matches: list[Any] = []
	for speakerLanguage, languageSpeakers in speakersByLanguage.items():
		if _language_matches(speakerLanguage, language):
			matches.extend(languageSpeakers)
	return matches


def _available_languages(catalog: VoiceCatalog) -> list[str]:
	return list(catalog.voices_by_language())


def _configured_synth_section() -> Any:
	try:
		return config.conf["speech"][SYNTH_NAME]
	except Exception:
		return {}


def _initial_voice(catalog: VoiceCatalog) -> str:
	available = _available_languages(catalog)
	try:
		configured = str(_configured_synth_section().get("voice") or "")
		if configured in available:
			return configured
	except Exception:
		pass
	if "en-US" in available:
		return "en-US"
	return next(iter(available))


def _variant_for_language(catalog: VoiceCatalog, language: str) -> str:
	variantIds = [speaker.id for speaker in _speakers_for_language(catalog, language)]
	if not variantIds and catalog.speakers:
		variantIds = [catalog.speakers[0].id]
	try:
		configured = str(_configured_synth_section().get("variant") or "")
		if configured in variantIds:
			return configured
	except Exception:
		pass
	return variantIds[0]


def _current_speech_state(catalog: VoiceCatalog) -> dict[str, Any]:
	section = _configured_synth_section()
	language = _initial_voice(catalog)
	voice = _variant_for_language(catalog, language)
	return {
		"voice": voice,
		"rate": _profile_int(section.get("rate"), 50),
		"rateBoost": _profile_bool(section.get("rateBoost"), False),
		"pitch": _profile_int(section.get("pitch"), 50),
		"volume": _profile_int(section.get("volume"), 100),
	}


def _auto_language_detection_enabled() -> bool:
	try:
		value = config.conf[CONFIG_SECTION][CONFIG_AUTO_LANGUAGE_DETECTION]
	except Exception:
		return DEFAULT_AUTO_LANGUAGE_DETECTION
	return _config_bool(value, DEFAULT_AUTO_LANGUAGE_DETECTION)


def _auto_language_profiles() -> dict[str, dict[str, Any]]:
	try:
		rawValue = config.conf[CONFIG_SECTION][CONFIG_AUTO_LANGUAGE_PROFILES]
	except Exception:
		rawValue = DEFAULT_AUTO_LANGUAGE_PROFILES
	try:
		parsed = json.loads(str(rawValue or "{}"))
	except (TypeError, ValueError):
		return {}
	if not isinstance(parsed, dict):
		return {}
	profiles: dict[str, dict[str, Any]] = {}
	for rawLanguage, rawProfile in parsed.items():
		languageKey = _normalize_language(str(rawLanguage))
		if languageKey and isinstance(rawProfile, dict):
			profiles[languageKey] = dict(rawProfile)
	return profiles


def _auto_language_profile_for_language(language: str | None) -> dict[str, Any]:
	languageKey = _normalize_language(language)
	if not languageKey:
		return {}
	profiles = _auto_language_profiles()
	profile = profiles.get(languageKey)
	if profile is not None:
		return profile
	languageKeys = _language_match_keys(language)
	for profileLanguage, profile in profiles.items():
		if _language_match_keys(profileLanguage).intersection(languageKeys):
			return profile
	return {}


def _auto_language_candidates(catalog: VoiceCatalog) -> list[str]:
	profiles = _auto_language_profiles()
	try:
		rawValue = str(config.conf[CONFIG_SECTION][CONFIG_AUTO_LANGUAGE_CANDIDATES])
	except Exception:
		rawValue = DEFAULT_AUTO_LANGUAGE_CANDIDATES
	availableByKey = {
		_normalize_language(language): language
		for language in _available_languages(catalog)
	}
	if profiles:
		return [
			availableByKey[languageKey]
			for languageKey, profile in profiles.items()
			if languageKey in availableByKey and _profile_bool(profile.get("enabled"), False)
		]
	candidates: list[str] = []
	seen: set[str] = set()
	for rawLanguage in rawValue.split(","):
		key = _normalize_language(rawLanguage)
		if not key or key in seen or key not in availableByKey:
			continue
		candidates.append(availableByKey[key])
		seen.add(key)
	return candidates


def _auto_language_preferred(catalog: VoiceCatalog, candidateLanguages: list[str], fallbackVoice: str) -> str:
	try:
		configured = str(config.conf[CONFIG_SECTION][CONFIG_AUTO_LANGUAGE_PREFERRED])
	except Exception:
		configured = DEFAULT_AUTO_LANGUAGE_PREFERRED
	configuredKey = _normalize_language(configured)
	for language in candidateLanguages:
		if _normalize_language(language) == configuredKey:
			return language
	try:
		fallbackLanguage = catalog.language_for_voice(fallbackVoice)
	except Exception:
		fallbackLanguage = fallbackVoice
	fallbackRoot = _language_root(fallbackLanguage)
	for language in candidateLanguages:
		if _language_root(language) == fallbackRoot:
			return language
	return candidateLanguages[0] if candidateLanguages else fallbackLanguage


def _auto_language_candidates_in_warmup_order(catalog: VoiceCatalog, currentVoice: str) -> list[str]:
	candidateLanguages = _auto_language_candidates(catalog)
	if len(candidateLanguages) <= 1:
		return candidateLanguages
	orderedLanguages = list(candidateLanguages)
	preferredLanguage = _auto_language_preferred(catalog, orderedLanguages, currentVoice)
	if preferredLanguage in orderedLanguages:
		orderedLanguages.remove(preferredLanguage)
		orderedLanguages.insert(0, preferredLanguage)
	return orderedLanguages


def _voice_matches_language(catalog: VoiceCatalog, voice: str, language: str | None) -> bool:
	if not language:
		return True
	try:
		voiceLanguage = catalog.language_for_voice(voice)
	except Exception:
		return False
	return _language_matches(voiceLanguage, language)


def _voice_for_language(catalog: VoiceCatalog, language: str | None, fallbackVoice: str) -> str:
	if not language:
		return _speaker_for_voice_or_language(catalog, fallbackVoice)
	normalizedLanguage = _normalize_language(language)
	if not normalizedLanguage:
		return _speaker_for_voice_or_language(catalog, fallbackVoice)
	fallbackVoice = _speaker_for_voice_or_language(catalog, fallbackVoice)
	fallbackSpeaker = catalog.speaker_for_voice(fallbackVoice)
	if _language_matches(fallbackSpeaker.language, normalizedLanguage):
		return fallbackVoice
	for speaker in _speakers_for_language(catalog, normalizedLanguage):
		return speaker.id
	rootLanguage = normalizedLanguage.split("-", 1)[0]
	if _normalize_language(fallbackSpeaker.language).split("-", 1)[0] == rootLanguage:
		return fallbackVoice
	for languageKey, speakers in catalog.voices_by_language().items():
		if _normalize_language(languageKey).split("-", 1)[0] == rootLanguage:
			return speakers[0].id
	return fallbackVoice


def _speaker_for_voice_or_language(catalog: VoiceCatalog, value: str | None) -> str:
	if value:
		try:
			return catalog.speaker_for_voice(value).id
		except Exception:
			pass
		for speaker in _speakers_for_language(catalog, value):
			return speaker.id
	return catalog.speakers[0].id


def _auto_language_profile(
	catalog: VoiceCatalog,
	language: str | None,
	fallbackVoice: str,
	fallbackRate: int,
	fallbackRateBoost: bool,
	fallbackPitch: int,
	fallbackVolume: int,
) -> dict[str, Any]:
	profile = _auto_language_profile_for_language(language)
	voice = str(profile.get("voice") or "")
	if not _voice_matches_language(catalog, voice, language):
		voice = _voice_for_language(catalog, language, fallbackVoice)
	return {
		"voice": voice,
		"rate": _profile_int(profile.get("rate"), fallbackRate),
		"rateBoost": _profile_bool(profile.get("rateBoost"), fallbackRateBoost),
		"pitch": _profile_int(profile.get("pitch"), fallbackPitch),
		"volume": _profile_int(profile.get("volume"), fallbackVolume),
	}


def _voice_id_for_package(
	catalog: VoiceCatalog,
	speakersByPackage: dict[str, list[Any]],
	packageId: str,
	preferredSpeaker: str | None = None,
) -> str:
	speakers = speakersByPackage.get(packageId, [])
	fallbackVoiceId = speakers[0].id if speakers else ""
	for speaker in speakers:
		if preferredSpeaker and speaker.speaker == preferredSpeaker:
			return speaker.id
	return fallbackVoiceId


def _warmup_voice_ids_for_voice(
	catalog: VoiceCatalog,
	voiceId: str,
	seenPackages: set[str] | None = None,
) -> list[str]:
	if seenPackages is None:
		seenPackages = set()
	try:
		speaker = catalog.speaker_for_voice(voiceId)
		package = catalog.package_for_voice(voiceId)
	except Exception:
		log.debug("Could not resolve Google TTS standby preload voice %s.", voiceId, exc_info=True)
		return []
	if package.id in seenPackages:
		return []
	seenPackages.add(package.id)
	voiceIds: list[str] = []
	if package.dependentVoiceId:
		dependencyVoiceId = _voice_id_for_package(
			catalog,
			_speakers_by_package(catalog),
			package.dependentVoiceId,
			speaker.speaker,
		)
		if dependencyVoiceId:
			voiceIds.extend(_warmup_voice_ids_for_voice(catalog, dependencyVoiceId, seenPackages))
	if voiceId not in voiceIds:
		voiceIds.append(voiceId)
	return voiceIds


def _warmup_voice_ids(catalog: VoiceCatalog, state: dict[str, Any]) -> list[str]:
	currentVoice = str(state["voice"])
	if not _auto_language_detection_enabled():
		return _warmup_voice_ids_for_voice(catalog, currentVoice)
	candidateLanguages = _auto_language_candidates_in_warmup_order(catalog, currentVoice)
	if not candidateLanguages:
		return _warmup_voice_ids_for_voice(catalog, currentVoice)

	voiceIds: list[str] = []
	seenPackages: set[str] = set()
	for language in candidateLanguages:
		profile = _auto_language_profile(
			catalog,
			language,
			currentVoice,
			int(state["rate"]),
			bool(state["rateBoost"]),
			int(state["pitch"]),
			int(state["volume"]),
		)
		voiceId = str(profile.get("voice") or "")
		if not voiceId:
			continue
		for warmupVoiceId in _warmup_voice_ids_for_voice(catalog, voiceId):
			try:
				packageId = catalog.package_for_voice(warmupVoiceId).id
			except Exception:
				log.debug("Could not resolve Google TTS standby preload package for %s.", warmupVoiceId, exc_info=True)
				continue
			if packageId in seenPackages:
				continue
			seenPackages.add(packageId)
			voiceIds.append(warmupVoiceId)
	return voiceIds or [currentVoice]


def _rate_to_chrome(value: int, rateBoost: bool) -> float:
	percent = max(0, min(100, value)) / 100.0
	rate = 0.35 + (2.0 - 0.35) * percent
	if rateBoost:
		rate *= 2
	return round(max(0.1, min(10.0, rate)), 3)


def _pitch_to_chrome(pitch: int) -> float:
	pitchSemitones = -12.0 + 24.0 * max(0, min(100, pitch)) / 100.0
	return round(max(0.1, min(3.0, 1.0 + pitchSemitones / 20.0)), 3)


def _uses_protected_engine_rate(packageId: str) -> bool:
	return packageId.lower().endswith("-seanet")


def _speech_options(
	catalog: VoiceCatalog,
	rate: int,
	pitch: int,
	volume: int,
	voice: str,
	rateBoost: bool,
) -> dict[str, Any]:
	speaker = catalog.speaker_for_voice(voice)
	package = catalog.package_for_voice(speaker.id)
	volumeLevel = max(0.0, min(1.0, volume / 100.0))
	outputGain = max(0.0, min(_OUTPUT_GAIN_MAKEUP, volumeLevel * _OUTPUT_GAIN_MAKEUP))
	desiredRate = _rate_to_chrome(rate, rateBoost)
	engineRate = desiredRate
	artificialRate = 1.0
	usesProtectedEngineRate = _uses_protected_engine_rate(package.id)
	pitchValue = _pitch_to_chrome(pitch)
	enginePitch = 1.0 if usesProtectedEngineRate else pitchValue
	postPitch = pitchValue if usesProtectedEngineRate else 1.0
	if usesProtectedEngineRate and desiredRate > _PROTECTED_ENGINE_RATE:
		engineRate = _PROTECTED_ENGINE_RATE
		artificialRate = max(_MIN_ARTIFICIAL_RATE, min(_MAX_ARTIFICIAL_RATE, desiredRate / engineRate))
	return {
		"voiceId": speaker.id,
		"voiceName": speaker.name,
		"lang": speaker.language,
		"rate": round(engineRate, 3),
		"artificialRate": round(artificialRate, 3),
		"pitch": round(enginePitch, 3),
		"postPitch": round(postPitch, 3),
		"volume": round(volumeLevel, 4),
		"outputGain": round(outputGain, 4),
	}


def _warmup_options(catalog: VoiceCatalog) -> list[dict[str, Any]]:
	state = _current_speech_state(catalog)
	optionsList: list[dict[str, Any]] = []
	for voiceId in _warmup_voice_ids(catalog, state):
		try:
			optionsList.append(
				_speech_options(
					catalog,
					int(state["rate"]),
					int(state["pitch"]),
					0,
					voiceId,
					bool(state["rateBoost"]),
				),
			)
		except Exception:
			log.debug("Could not prepare Google TTS standby preload options for %s.", voiceId, exc_info=True)
	return optionsList


def _refresh_reason_requires_runtime_restart(reason: str) -> bool:
	return reason.startswith("watched directory changed")


class _DirectoryChangeWatcher:
	_NOTIFY_FILTER = (
		0x00000001  # FILE_NOTIFY_CHANGE_FILE_NAME
		| 0x00000002  # FILE_NOTIFY_CHANGE_DIR_NAME
		| 0x00000004  # FILE_NOTIFY_CHANGE_ATTRIBUTES
		| 0x00000008  # FILE_NOTIFY_CHANGE_SIZE
		| 0x00000010  # FILE_NOTIFY_CHANGE_LAST_WRITE
		| 0x00000040  # FILE_NOTIFY_CHANGE_CREATION
	)
	_WAIT_OBJECT_0 = 0x00000000
	_WAIT_FAILED = 0xFFFFFFFF
	_INFINITE = 0xFFFFFFFF
	_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

	def __init__(self, paths: Callable[[], tuple[Path, ...]], callback: Callable[[str], None]) -> None:
		self._paths = paths
		self._callback = callback
		self._lock = threading.RLock()
		self._thread: threading.Thread | None = None
		self._stopRequested = threading.Event()
		self._stopHandle: int | None = None

	def start(self) -> None:
		with self._lock:
			if self._thread is not None and self._thread.is_alive():
				return
			self._stopRequested.clear()
			thread = threading.Thread(
				name="googleTtsForNvda.standbyWatcher",
				target=self._run,
				daemon=True,
			)
			self._thread = thread
			thread.start()

	def stop(self) -> None:
		with self._lock:
			self._stopRequested.set()
			self._signal_stop_locked()

	def _signal_stop_locked(self) -> None:
		stopHandle = self._stopHandle
		if not stopHandle:
			return
		try:
			kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
			kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
			kernel32.SetEvent.restype = wintypes.BOOL
			kernel32.SetEvent(stopHandle)
		except Exception:
			log.debug("Could not signal Google TTS standby watcher stop event.", exc_info=True)

	def _run(self) -> None:
		try:
			kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
			kernel32.CreateEventW.argtypes = (
				wintypes.LPVOID,
				wintypes.BOOL,
				wintypes.BOOL,
				wintypes.LPCWSTR,
			)
			kernel32.CreateEventW.restype = wintypes.HANDLE
			kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
			kernel32.CloseHandle.restype = wintypes.BOOL
			kernel32.FindFirstChangeNotificationW.argtypes = (
				wintypes.LPCWSTR,
				wintypes.BOOL,
				wintypes.DWORD,
			)
			kernel32.FindFirstChangeNotificationW.restype = wintypes.HANDLE
			kernel32.FindCloseChangeNotification.argtypes = (wintypes.HANDLE,)
			kernel32.FindCloseChangeNotification.restype = wintypes.BOOL
			kernel32.WaitForMultipleObjects.argtypes = (
				wintypes.DWORD,
				ctypes.POINTER(wintypes.HANDLE),
				wintypes.BOOL,
				wintypes.DWORD,
			)
			kernel32.WaitForMultipleObjects.restype = wintypes.DWORD
		except Exception:
			log.debug("Could not initialize Google TTS standby directory watcher.", exc_info=True)
			return

		stopHandle = kernel32.CreateEventW(None, True, False, None)
		if not stopHandle:
			log.debug("Could not create Google TTS standby watcher stop event.")
			return
		with self._lock:
			self._stopHandle = stopHandle
		try:
			while not self._stopRequested.is_set():
				notificationHandles: list[int] = []
				notificationPaths: list[Path] = []
				try:
					for path in self._paths():
						handle = self._watch_path(kernel32, path)
						if not handle:
							continue
						notificationHandles.append(handle)
						notificationPaths.append(path)
					waitHandles = [stopHandle] + notificationHandles
					handleArray = (wintypes.HANDLE * len(waitHandles))(*waitHandles)
					result = kernel32.WaitForMultipleObjects(
						len(waitHandles),
						handleArray,
						False,
						self._INFINITE,
					)
					if result == self._WAIT_FAILED:
						log.debug("Google TTS standby watcher wait failed: %s.", ctypes.get_last_error())
						return
					signaledIndex = int(result - self._WAIT_OBJECT_0)
					if signaledIndex < 0 or signaledIndex >= len(waitHandles):
						log.debug("Google TTS standby watcher returned an unexpected wait result: %s.", result)
						return
					if signaledIndex == 0 or self._stopRequested.is_set():
						return
					pathIndex = signaledIndex - 1
					reason = "watched directory changed"
					if 0 <= pathIndex < len(notificationPaths):
						reason = f"watched directory changed: {notificationPaths[pathIndex]}"
				finally:
					for handle in notificationHandles:
						kernel32.FindCloseChangeNotification(handle)
				if not self._stopRequested.is_set():
					self._callback(reason)
		finally:
			with self._lock:
				self._stopHandle = None
				self._thread = None
			kernel32.CloseHandle(stopHandle)

	def _watch_path(self, kernel32: Any, path: Path) -> int | None:
		try:
			if not path.is_dir():
				return None
		except OSError:
			return None
		handle = kernel32.FindFirstChangeNotificationW(str(path), True, self._NOTIFY_FILTER)
		if not handle or handle == self._INVALID_HANDLE_VALUE:
			log.debug(
				"Could not watch Google TTS standby directory changes for %s: %s.",
				path,
				ctypes.get_last_error(),
			)
			return None
		return int(handle)


class _StandbyRuntimeManager:
	def __init__(self) -> None:
		self._lock = threading.RLock()
		self._bridge: ChromeTtsBridge | None = None
		self._signature: tuple[Any, ...] | None = None
		self._ready = False
		self._generation = 0
		self._cancelEvent: threading.Event | None = None
		self._worker: threading.Thread | None = None
		self._watcher: _DirectoryChangeWatcher | None = None
		self._synthActive = False
		self._shutdown = True

	def initialize(self) -> None:
		with self._lock:
			self._shutdown = False

	def refresh_async(self, reason: str = "", *, force: bool = True) -> None:
		bridgeToTerminate: ChromeTtsBridge | None = None
		with self._lock:
			if self._shutdown:
				return
			if not keep_browser_runtime_ready_enabled() or self._synthActive:
				self._generation += 1
				bridgeToTerminate = self._clear_standby_locked(cancelWorker=True)
				self._stop_watchers_locked()
				workerAlive = self._worker is not None and self._worker.is_alive()
				if workerAlive:
					log.debug("Google TTS standby runtime refresh skipped while synth is active or disabled.")
				else:
					self._worker = None
			elif not force and self._worker is not None and self._worker.is_alive():
				return
			else:
				self._generation += 1
				generation = self._generation
				cancelEvent = threading.Event()
				workerAlive = self._worker is not None and self._worker.is_alive()
				self._cancel_current_worker_locked()
				self._stop_watchers_locked()
				if workerAlive:
					# A cancelled worker may still be unwinding a CDP request; do not let
					# the replacement worker reuse the same browser bridge concurrently.
					bridgeToTerminate = self._clear_standby_locked(cancelWorker=False)
				self._cancelEvent = cancelEvent
				worker = threading.Thread(
					name="googleTtsForNvda.standby",
					target=self._run_refresh,
					args=(generation, cancelEvent, reason),
					daemon=True,
				)
				self._worker = worker
				worker.start()
		self._terminate_bridge(bridgeToTerminate)

	def claim_bridge(self, catalog: VoiceCatalog) -> ChromeTtsBridge | None:
		bridgeToTerminate: ChromeTtsBridge | None = None
		with self._lock:
			if self._shutdown:
				return None
			self._synthActive = True
			self._generation += 1
			self._cancel_current_worker_locked()
			self._stop_watchers_locked()
			signature = _catalog_signature(catalog)
			if self._bridge is not None and self._ready and self._signature == signature:
				bridge = self._bridge
				self._bridge = None
				self._signature = None
				self._ready = False
				return bridge
			bridgeToTerminate = self._clear_standby_locked(cancelWorker=False)
		self._terminate_bridge(bridgeToTerminate)
		return None

	def note_synth_active(self) -> None:
		bridgeToTerminate: ChromeTtsBridge | None = None
		with self._lock:
			if self._shutdown:
				return
			self._synthActive = True
			self._generation += 1
			self._cancel_current_worker_locked()
			self._stop_watchers_locked()
			bridgeToTerminate = self._clear_standby_locked(cancelWorker=False)
		self._terminate_bridge(bridgeToTerminate)

	def release_synth_bridge(self, bridge: ChromeTtsBridge, catalog: VoiceCatalog) -> bool:
		previousBridge: ChromeTtsBridge | None = None
		with self._lock:
			if self._shutdown or not keep_browser_runtime_ready_enabled():
				self._synthActive = False
				return False
			self._synthActive = False
			self._generation += 1
			self._cancel_current_worker_locked()
			self._stop_watchers_locked()
			previousBridge = self._bridge if self._bridge is not bridge else None
			self._bridge = bridge
			self._signature = _catalog_signature(catalog)
			self._ready = True
		self._terminate_bridge(previousBridge)
		self.refresh_async("Google TTS synth released its browser runtime")
		return True

	def release_synth_without_bridge(self, reason: str = "") -> None:
		with self._lock:
			if self._shutdown:
				self._synthActive = False
				return
			self._synthActive = False
			self._generation += 1
			self._cancel_current_worker_locked()
			self._stop_watchers_locked()
		self.refresh_async(reason or "Google TTS synth released without a reusable browser runtime")

	def terminate(self) -> None:
		bridgeToTerminate: ChromeTtsBridge | None
		with self._lock:
			self._shutdown = True
			self._synthActive = False
			self._generation += 1
			bridgeToTerminate = self._clear_standby_locked(cancelWorker=True)
			self._stop_watchers_locked()
		self._terminate_bridge(bridgeToTerminate)

	def _run_refresh(self, generation: int, cancelEvent: threading.Event, reason: str) -> None:
		bridgeForWorker: ChromeTtsBridge | None = None
		bridgeToTerminate: ChromeTtsBridge | None = None
		try:
			catalog = _installed_catalog()
			if cancelEvent.is_set():
				raise CdpCancelled()
			if catalog is None:
				with self._lock:
					if generation == self._generation:
						bridgeToTerminate = self._clear_standby_locked(cancelWorker=False)
						self._start_watchers_locked()
				return
			signature = _catalog_signature(catalog)
			with self._lock:
				if generation != self._generation or self._shutdown or self._synthActive:
					raise CdpCancelled()
				if (
					self._bridge is not None
					and self._signature == signature
					and not _refresh_reason_requires_runtime_restart(reason)
				):
					bridgeForWorker = self._bridge
				else:
					bridgeToTerminate = self._bridge
					bridgeForWorker = ChromeTtsBridge(catalog)
					self._bridge = bridgeForWorker
					self._signature = signature
					self._ready = False
			self._terminate_bridge(bridgeToTerminate)
			bridgeToTerminate = None
			bridgeForWorker.ensure_connection(cancelEvent=cancelEvent)
			for options in _warmup_options(catalog):
				if cancelEvent.is_set():
					raise CdpCancelled()
				warmupOptions = dict(options)
				warmupOptions["warmupText"] = _VOICE_WARMUP_TEXT
				bridgeForWorker.preload_voice(warmupOptions, cancelEvent=cancelEvent)
			with self._lock:
				if generation != self._generation or self._bridge is not bridgeForWorker:
					raise CdpCancelled()
				if self._shutdown or self._synthActive or not keep_browser_runtime_ready_enabled():
					bridgeToTerminate = self._clear_standby_locked(cancelWorker=False)
					raise CdpCancelled()
				self._ready = True
				self._start_watchers_locked()
			if reason:
				log.debug("Google TTS standby browser runtime is ready: %s.", reason)
			else:
				log.debug("Google TTS standby browser runtime is ready.")
		except CdpCancelled:
			log.debug("Google TTS standby browser runtime refresh cancelled.")
		except Exception:
			log.debug("Google TTS standby browser runtime refresh failed.", exc_info=True)
			with self._lock:
				if generation == self._generation and self._bridge is bridgeForWorker:
					bridgeToTerminate = self._clear_standby_locked(cancelWorker=False)
					self._start_watchers_locked()
		finally:
			with self._lock:
				if generation == self._generation and self._worker is threading.current_thread():
					self._worker = None
			self._terminate_bridge(bridgeToTerminate)

	def _clear_standby_locked(self, *, cancelWorker: bool) -> ChromeTtsBridge | None:
		if cancelWorker:
			self._cancel_current_worker_locked()
		bridge = self._bridge
		self._bridge = None
		self._signature = None
		self._ready = False
		return bridge

	def _cancel_current_worker_locked(self) -> None:
		if self._cancelEvent is not None:
			self._cancelEvent.set()
		self._cancelEvent = None

	def _start_watchers_locked(self) -> None:
		self._stop_watchers_locked()
		if self._shutdown or self._synthActive or not keep_browser_runtime_ready_enabled():
			return
		generation = self._generation
		self._watcher = _DirectoryChangeWatcher(
			self._watch_paths,
			lambda reason: self._refresh_from_watcher(reason, generation),
		)
		self._watcher.start()

	def _stop_watchers_locked(self) -> None:
		if self._watcher is None:
			return
		watcher = self._watcher
		self._watcher = None
		watcher.stop()

	def _refresh_from_watcher(self, reason: str, generation: int) -> None:
		with self._lock:
			if generation != self._generation or self._shutdown:
				return
		self.refresh_async(reason, force=False)

	def _watch_paths(self) -> tuple[Path, ...]:
		paths: list[Path] = [ADDON_DIR]
		try:
			paths.append(voice_store.voice_dir())
		except Exception:
			log.debug("Could not resolve Google TTS voices directory for standby watcher.", exc_info=True)
		uniquePaths: list[Path] = []
		seen: set[str] = set()
		for path in paths:
			try:
				resolved = str(path.resolve())
			except OSError:
				resolved = str(path)
			key = resolved.lower()
			if key in seen:
				continue
			seen.add(key)
			uniquePaths.append(path)
		return tuple(uniquePaths)

	def _terminate_bridge(self, bridge: ChromeTtsBridge | None) -> None:
		if bridge is None:
			return
		try:
			bridge.terminate()
		except Exception:
			log.debug("Could not terminate Google TTS standby browser runtime.", exc_info=True)


_manager = _StandbyRuntimeManager()


def initialize() -> None:
	_manager.initialize()


def refresh_async(reason: str = "", *, force: bool = True) -> None:
	_manager.refresh_async(reason, force=force)


def claim_bridge(catalog: VoiceCatalog) -> ChromeTtsBridge | None:
	return _manager.claim_bridge(catalog)


def note_synth_active() -> None:
	_manager.note_synth_active()


def release_synth_bridge(bridge: ChromeTtsBridge, catalog: VoiceCatalog) -> bool:
	return _manager.release_synth_bridge(bridge, catalog)


def release_synth_without_bridge(reason: str = "") -> None:
	_manager.release_synth_without_bridge(reason)


def terminate() -> None:
	_manager.terminate()
