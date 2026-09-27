"""PyQt5 as MaRGE imports it, over the PyQt6 of Pyodide's Qt build.

Qt for WebAssembly runs on the browser's one thread: this build has no thread
classes, no Designer loader and no OpenGL, and a QImage built around an outside
buffer is null in it. The thread classes are stand-ins that run their work on
the event loop, and pyqtgraph copies an array into an image it allocates.
"""

from __future__ import annotations

import enum
import os
import sys
import types
from typing import Any

#: Classes Qt 6 moved from QtWidgets to QtGui.
MOVED_TO_QTGUI = ("QAction", "QActionGroup", "QShortcut", "QUndoCommand", "QUndoStack", "QFileSystemModel")


def install() -> None:
    """Make ``import PyQt5`` give PyQt6, spelled as PyQt5 spells it."""
    if "PyQt5" in sys.modules:
        return
    # qtpy, and QDarkStyle through it, and pyqtgraph take the binding named here.
    os.environ["QT_API"] = "pyqt6"
    os.environ["PYQTGRAPH_QT_LIB"] = "PyQt6"
    import PyQt6
    from PyQt6 import QtCore, QtGui, QtWidgets, sip

    _thread_classes(QtCore)
    _absent(PyQt6, QtWidgets)
    for module in (QtCore, QtGui, QtWidgets):
        _unscoped_enums(module)
        for klass in _classes(module):
            if hasattr(klass, "exec") and not hasattr(klass, "exec_"):
                klass.exec_ = klass.exec
    for name in MOVED_TO_QTGUI:
        if hasattr(QtGui, name) and not hasattr(QtWidgets, name):
            setattr(QtWidgets, name, getattr(QtGui, name))
    QtWidgets.QFileDialog.Options = lambda *flags: QtWidgets.QFileDialog.Option(0)
    QtCore.Qt.MidButton = QtCore.Qt.MouseButton.MiddleButton

    # qtpy keeps the first binding it finds imported, so it settles before the alias exists.
    import qtpy.QtCore  # noqa: F401
    import qtpy.QtGui  # noqa: F401
    import qtpy.QtWidgets  # noqa: F401

    package = types.ModuleType("PyQt5")
    package.__path__ = []
    package.QtCore, package.QtGui, package.QtWidgets, package.sip = QtCore, QtGui, QtWidgets, sip
    sys.modules.update(
        {
            "PyQt5": package,
            "PyQt5.QtCore": QtCore,
            "PyQt5.QtGui": QtGui,
            "PyQt5.QtWidgets": QtWidgets,
            "PyQt5.sip": sip,
        }
    )

    import pyqtgraph
    import qdarkstyle

    qdarkstyle.load_stylesheet_pyqt5 = lambda: qdarkstyle.load_stylesheet(qt_api="pyqt6")
    pyqtgraph.functions.ndarray_to_qimage = _ndarray_to_qimage


def _classes(module: types.ModuleType) -> list[type]:
    return [value for value in vars(module).values() if isinstance(value, type)]


def _unscoped_enums(module: types.ModuleType) -> None:
    """Make each scoped enum member reachable from its class, as ``Qt.AlignCenter``."""
    for klass in _classes(module):
        for name in dir(klass):
            if not name[:1].isupper():
                continue
            try:
                scoped = getattr(klass, name)
            except Exception:  # noqa: BLE001 -- some descriptors raise on the class
                continue
            if isinstance(scoped, type) and issubclass(scoped, enum.Enum):
                for member in scoped:
                    if member.name and not hasattr(klass, member.name):
                        try:
                            setattr(klass, member.name, member)
                        except (AttributeError, TypeError):
                            pass


def _thread_classes(QtCore: types.ModuleType) -> None:  # noqa: N803 -- the module's name
    """Give QtCore the thread classes this build lacks, running their work on the event loop."""

    class QMutex:
        def lock(self) -> None:
            pass

        def unlock(self) -> None:
            pass

        def tryLock(self, *args: Any) -> bool:  # noqa: N802 -- Qt's API
            return True

    class QMutexLocker:
        def __init__(self, mutex: Any) -> None:
            self.mutex = mutex

        def __enter__(self) -> "QMutexLocker":
            return self

        def __exit__(self, *exc: Any) -> bool:
            return False

        def unlock(self) -> None:
            pass

        def relock(self) -> None:
            pass

    class QRunnable:
        def run(self) -> None:
            pass

        def setAutoDelete(self, value: bool) -> None:  # noqa: N802 -- Qt's API
            pass

        def autoDelete(self) -> bool:  # noqa: N802 -- Qt's API
            return True

    class QThreadPool(QtCore.QObject):
        _global = None

        @classmethod
        def globalInstance(cls) -> "QThreadPool":  # noqa: N802 -- Qt's API
            if cls._global is None:
                cls._global = cls()
            return cls._global

        def start(self, runnable: Any, *args: Any) -> None:
            QtCore.QTimer.singleShot(0, runnable.run)

        def maxThreadCount(self) -> int:  # noqa: N802 -- Qt's API
            return 1

        def setMaxThreadCount(self, count: int) -> None:  # noqa: N802 -- Qt's API
            pass

        def activeThreadCount(self) -> int:  # noqa: N802 -- Qt's API
            return 0

        def waitForDone(self, *args: Any) -> bool:  # noqa: N802 -- Qt's API
            return True

    class QThread(QtCore.QObject):
        started = QtCore.pyqtSignal()
        finished = QtCore.pyqtSignal()

        def start(self, *args: Any) -> None:
            def work() -> None:
                self.started.emit()
                self.run()
                self.finished.emit()

            QtCore.QTimer.singleShot(0, work)

        def run(self) -> None:
            pass

        def isRunning(self) -> bool:  # noqa: N802 -- Qt's API
            return False

        def isFinished(self) -> bool:  # noqa: N802 -- Qt's API
            return True

        def wait(self, *args: Any) -> bool:
            return True

        def quit(self) -> None:
            pass

        def terminate(self) -> None:
            pass

        def requestInterruption(self) -> None:  # noqa: N802 -- Qt's API
            pass

        def isInterruptionRequested(self) -> bool:  # noqa: N802 -- Qt's API
            return False

        @staticmethod
        def msleep(milliseconds: int) -> None:
            pass

        @staticmethod
        def sleep(seconds: int) -> None:
            pass

        @staticmethod
        def currentThread() -> None:  # noqa: N802 -- Qt's API
            return None

        @staticmethod
        def idealThreadCount() -> int:  # noqa: N802 -- Qt's API
            return 1

    stand_ins = {
        "QMutex": QMutex,
        "QRecursiveMutex": QMutex,
        "QMutexLocker": QMutexLocker,
        "QRunnable": QRunnable,
        "QThreadPool": QThreadPool,
        "QThread": QThread,
    }
    for name, stand_in in stand_ins.items():
        if not hasattr(QtCore, name):
            setattr(QtCore, name, stand_in)
    if not hasattr(QtCore.QObject, "moveToThread"):
        QtCore.QObject.moveToThread = lambda self, thread: None
    if not hasattr(QtCore.QObject, "thread"):
        QtCore.QObject.thread = lambda self: None


class _Absent(types.ModuleType):
    """A module the build leaves out, whose attributes raise when called."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)

        def absent(*args: Any, **kwargs: Any) -> Any:
            raise NotImplementedError(f"{self.__name__}.{name} is not in the browser's Qt build")

        return absent


def _absent(PyQt6: types.ModuleType, QtWidgets: types.ModuleType) -> None:  # noqa: N803 -- module names
    """Stand in for the modules the build leaves out: Designer's loader and OpenGL."""
    widgets = _Absent("PyQt6.QtOpenGLWidgets")
    # pyqtgraph subclasses it at import; nothing draws through it.
    widgets.QOpenGLWidget = QtWidgets.QWidget
    for module in (_Absent("PyQt6.uic"), _Absent("PyQt6.QtOpenGL"), widgets):
        if module.__name__ not in sys.modules:
            sys.modules[module.__name__] = module
            setattr(PyQt6, module.__name__.rsplit(".", 1)[1], module)


def _ndarray_to_qimage(array: Any, fmt: Any) -> Any:
    """Copy ``array`` into a QImage of format ``fmt``; pyqtgraph's own wraps the array's buffer."""
    import numpy as np
    from PyQt6 import QtGui

    height, width = array.shape[:2]
    image = QtGui.QImage(width, height, fmt)
    bits = image.bits()
    bits.setsize(image.sizeInBytes())
    rows = np.frombuffer(bits, dtype=np.uint8).reshape(height, image.bytesPerLine())
    source = np.ascontiguousarray(array).view(np.uint8).reshape(height, -1)
    rows[:, : source.shape[1]] = source
    return image
