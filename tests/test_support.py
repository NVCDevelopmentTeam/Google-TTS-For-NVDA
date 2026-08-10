"""Shared helpers for standalone tests that must not import NVDA."""
from __future__ import annotations

from functools import lru_cache
import importlib.util
from pathlib import Path
import struct
import sys
from types import ModuleType
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DRIVER_DIR = ROOT / "googleTtsForNvda" / "synthDrivers" / "googleTtsForNvda"
DRIVER_PATH = DRIVER_DIR / "__init__.py"
PROCESSING_PATH = DRIVER_DIR / "speech_processing.py"
UNICODE_DATA_PATH = DRIVER_DIR / "unicode_data.py"
_TEST_DRIVER_PACKAGE = "_google_tts_for_nvda_test_driver"


def _test_driver_package() -> ModuleType:
	package = sys.modules.get(_TEST_DRIVER_PACKAGE)
	if package is None:
		package = ModuleType(_TEST_DRIVER_PACKAGE)
		package.__path__ = [str(DRIVER_DIR)]  # type: ignore[attr-defined]
		package.__package__ = _TEST_DRIVER_PACKAGE
		sys.modules[_TEST_DRIVER_PACKAGE] = package
	return package


@lru_cache(maxsize=None)
def load_driver_module(moduleName: str) -> Any:
	"""Load one pure driver module without executing its NVDA package initializer."""
	if not moduleName.isidentifier():
		raise ValueError(f"Invalid driver module name: {moduleName!r}")
	path = DRIVER_DIR / f"{moduleName}.py"
	if not path.is_file():
		raise FileNotFoundError(path)
	_test_driver_package()
	qualifiedName = f"{_TEST_DRIVER_PACKAGE}.{moduleName}"
	spec = importlib.util.spec_from_file_location(qualifiedName, path)
	if spec is None or spec.loader is None:
		raise ImportError(f"Could not create an import specification for {path}")
	module = importlib.util.module_from_spec(spec)
	sys.modules[qualifiedName] = module
	try:
		spec.loader.exec_module(module)
	except Exception:
		sys.modules.pop(qualifiedName, None)
		raise
	return module


def pcm_bytes(*samples: int) -> bytes:
	return struct.pack(f"<{len(samples)}h", *samples)


def pcm_samples(pcm: bytes) -> tuple[int, ...]:
	if len(pcm) % 2:
		raise ValueError("Signed 16-bit PCM must contain an even number of bytes")
	return struct.unpack(f"<{len(pcm) // 2}h", pcm)
