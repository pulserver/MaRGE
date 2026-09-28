"""What a browser tab lacks that MaRGE reaches for: threads, processes, a terminal, serial ports and BM4D.

A tab runs Python on the browser's one thread. A thread MaRGE starts runs on
the event loop instead, after the code that started it returns; a process
fails as a command that is not installed would; and the modules without a
WebAssembly build import as stand-ins that raise when used.
"""

from __future__ import annotations

import asyncio
import importlib.abc
import importlib.machinery
import os
import subprocess
import sys
import threading
import types
from typing import Any

#: Modules with no WebAssembly build, and why each is missing.
MISSING = {
    "bm4d": "BM4D denoising has no WebAssembly build",
    "serial": "a browser tab reaches no serial port",
    "sigpy": "SigPy needs Numba, which has no WebAssembly build",
    "termios": "a browser tab has no terminal",
    "tty": "a browser tab has no terminal",
}


def install() -> None:
    """Replace threads, processes and the modules in :data:`MISSING` for a browser tab."""
    threading.Thread.start = _start_on_event_loop
    threading.Thread.join = lambda self, timeout=None: None
    threading.Thread.is_alive = lambda self: False
    subprocess.run = subprocess.call = subprocess.check_call = subprocess.check_output = _no_process
    subprocess.Popen = _no_popen
    os.system = lambda command: 127
    if not any(isinstance(finder, _StandIns) for finder in sys.meta_path):
        sys.meta_path.insert(0, _StandIns())


def _start_on_event_loop(self: threading.Thread) -> None:
    asyncio.get_event_loop().call_soon(self.run)


def _no_process(args: Any = None, *rest: Any, **kwargs: Any) -> Any:
    raise subprocess.CalledProcessError(127, args, output=b"a browser tab runs no process")


def _no_popen(args: Any = None, *rest: Any, **kwargs: Any) -> Any:
    raise FileNotFoundError(f"a browser tab runs no process: {args!r}")


class _Missing:
    """An attribute of a stand-in module: reachable, and raising when called."""

    def __init__(self, name: str, reason: str) -> None:
        self._name, self._reason = name, reason

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(f"{self._name}: {self._reason}")

    def __getattr__(self, attribute: str) -> _Missing:
        if attribute.startswith("__"):
            raise AttributeError(attribute)
        return _Missing(f"{self._name}.{attribute}", self._reason)


def _unavailable(name: str, reason: str) -> type:
    """A class MaRGE can subclass, raising when instantiated."""

    def __init__(self: Any, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError(f"{name}: {reason}")

    return type(name.rsplit(".", 1)[1], (), {"__init__": __init__})


class _StandIn(types.ModuleType):
    def __getattr__(self, attribute: str) -> _Missing:
        if attribute.startswith("__"):
            raise AttributeError(attribute)
        return _Missing(f"{self.__name__}.{attribute}", MISSING[self.__name__.split(".")[0]])


class _StandIns(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Import the modules in :data:`MISSING`, and any of their submodules, as stand-ins."""

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if fullname.split(".")[0] in MISSING:
            return importlib.machinery.ModuleSpec(fullname, self, is_package=True)
        return None

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType:
        module = _StandIn(spec.name)
        module.__path__ = []
        return module

    def exec_module(self, module: types.ModuleType) -> None:
        if module.__name__ == "serial":
            module.Serial = _unavailable("serial.Serial", MISSING["serial"])
            module.SerialException = OSError
        elif module.__name__ == "serial.tools.list_ports":
            module.comports = lambda *args, **kwargs: []
        elif module.__name__ == "serial.tools.list_ports_common":
            module.ListPortInfo = type("ListPortInfo", (), {})
