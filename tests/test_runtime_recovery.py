from __future__ import annotations

import threading
import unittest

from tests.test_support import load_driver_module


bridgeModule = load_driver_module("bridge")


class _FakeCdpClient:
	def __init__(self, connected: bool = True) -> None:
		self.connected = connected

	def is_connected(self) -> bool:
		return self.connected


class _FailingEngine:
	def __init__(self, error: BaseException) -> None:
		self.error = error
		self.calls = 0
		self.runtime_busy = False

	def speak(self, *args: object, **kwargs: object) -> dict[str, object]:
		self.calls += 1
		raise self.error


class _SuccessfulEngine:
	def __init__(self) -> None:
		self.calls = 0
		self.runtime_busy = False

	def speak(self, *args: object, **kwargs: object) -> dict[str, object]:
		self.calls += 1
		return {"success": True}


def _browser_speech_error(*, audioStarted: bool) -> BaseException:
	return bridgeModule._BrowserSpeechError(
		"Could not speak",
		"Browser harness reported a speech error",
		audioStarted=audioStarted,
	)


def _runtime_bridge(engine: object) -> object:
	bridge = bridgeModule.ChromeTtsBridge.__new__(bridgeModule.ChromeTtsBridge)
	bridge._lock = threading.RLock()
	bridge._engine = engine
	bridge._cdp_client = _FakeCdpClient()
	bridge._needsRecycle = False
	bridge._recycleUrgent = False
	bridge._recycleReason = ""
	bridge.ensure_connection = lambda cancelEvent=None: None
	return bridge


class RuntimeRecoveryTests(unittest.TestCase):
	def test_browser_speech_errors_require_runtime_recycle(self) -> None:
		error = _browser_speech_error(audioStarted=False)
		self.assertTrue(bridgeModule._runtime_error_requires_recycle(error))

	def test_no_audio_browser_error_retries_once_after_recycle(self) -> None:
		failedEngine = _FailingEngine(_browser_speech_error(audioStarted=False))
		successfulEngine = _SuccessfulEngine()
		bridge = _runtime_bridge(failedEngine)
		recycleCalls = 0

		def maybe_recycle_runtime(*, allowIdleRecycle: bool = True, checkMemory: bool = True) -> bool:
			nonlocal recycleCalls
			if not allowIdleRecycle:
				return False
			recycleCalls += 1
			bridge._engine = successfulEngine
			bridge._needsRecycle = False
			return True

		bridge.maybe_recycle_runtime = maybe_recycle_runtime
		result = bridge.speak("text", {}, lambda _audio: None)

		self.assertEqual({"success": True}, result)
		self.assertEqual(1, failedEngine.calls)
		self.assertEqual(1, successfulEngine.calls)
		self.assertEqual(1, recycleCalls)

	def test_partial_audio_browser_error_recycles_without_retry(self) -> None:
		error = _browser_speech_error(audioStarted=True)
		failedEngine = _FailingEngine(error)
		successfulEngine = _SuccessfulEngine()
		bridge = _runtime_bridge(failedEngine)
		recycleCalls = 0

		def maybe_recycle_runtime(*, allowIdleRecycle: bool = True, checkMemory: bool = True) -> bool:
			nonlocal recycleCalls
			if not allowIdleRecycle:
				return False
			recycleCalls += 1
			bridge._engine = successfulEngine
			bridge._needsRecycle = False
			return True

		bridge.maybe_recycle_runtime = maybe_recycle_runtime
		with self.assertRaises(bridgeModule._BrowserSpeechError):
			bridge.speak("text", {}, lambda _audio: None)

		self.assertEqual(1, failedEngine.calls)
		self.assertEqual(0, successfulEngine.calls)
		self.assertEqual(1, recycleCalls)

	def test_browser_error_is_never_retried_more_than_once(self) -> None:
		failedEngine = _FailingEngine(_browser_speech_error(audioStarted=False))
		bridge = _runtime_bridge(failedEngine)
		recycleCalls = 0

		def maybe_recycle_runtime(*, allowIdleRecycle: bool = True, checkMemory: bool = True) -> bool:
			nonlocal recycleCalls
			if not allowIdleRecycle:
				return False
			recycleCalls += 1
			bridge._needsRecycle = False
			return True

		bridge.maybe_recycle_runtime = maybe_recycle_runtime
		with self.assertRaises(bridgeModule._BrowserSpeechError):
			bridge.speak("text", {}, lambda _audio: None)

		self.assertEqual(2, failedEngine.calls)
		self.assertEqual(2, recycleCalls)

	def test_only_healthy_connected_runtime_is_safe_for_standby(self) -> None:
		bridge = _runtime_bridge(_SuccessfulEngine())
		self.assertTrue(bridge.safe_for_standby_release())

		bridge._needsRecycle = True
		self.assertFalse(bridge.safe_for_standby_release())
		bridge._needsRecycle = False
		bridge._engine.runtime_busy = True
		self.assertFalse(bridge.safe_for_standby_release())
		bridge._engine.runtime_busy = False
		bridge._cdp_client.connected = False
		self.assertFalse(bridge.safe_for_standby_release())


if __name__ == "__main__":
	unittest.main()
