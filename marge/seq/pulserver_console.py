"""MaRGE as the console of pulserver's virtual scanner.

With ``MARGE_PULSERVER`` set to the WebSocket address that ``pulserver console``
serves, MaRGE's sequences are pulserver's scanner-sequence plugins. Each
plugin's protocol listing gives its parameters. A run sends the protocol and
the prescription of MaRGE's FOV planning to pulserver, which designs the
sequence, builds its IR, plays it on the virtual scanner and reconstructs it;
the images come back as DICOM and are the run's output. The subject's name
chooses the phantom, and the ``Localizer`` is drawn from the phantom's ground
truth without a scan. MaRGE's own sequences are hidden.

The protocol travels in the interpreter's text blocks, so MaRGE asks pulserver
exactly what a scanner's interpreter asks.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import io
import json
import math
import os
import sys
import traceback
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np

#: WebSocket address of ``pulserver console``; the console mode is off without it.
GATEWAY = os.environ.get("MARGE_PULSERVER", "")

PROTOCOL_BEGIN = "[NimPulseqGUI Protocol]"
PROTOCOL_END = "[NimPulseqGUI Protocol End]"
FOV_OFFSET = ("fov_offset_x", "fov_offset_y", "fov_offset_z")
FOV_ROTATION = tuple(f"fov_rotation_{i}{j}" for i in (1, 2, 3) for j in (1, 2, 3))
PRESCRIPTION = FOV_OFFSET + FOV_ROTATION

#: Rotations from the logical readout, phase and slice axes to the physical ones.
ORIENTATIONS = {
    "axial": np.eye(3),
    "coronal": np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]]),
    "sagittal": np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
}

#: The protocol's field-of-view entries along the logical readout, phase and
#: slice axes, which MaRGE plans as its ``fov``, in cm.
FOV_SIZE = ("fov", "phase_fov", "slice_thickness")

#: MaRGE's names of the localizer's axial, coronal and sagittal planes, on
#: which it plans the field of view.
PLANE_TITLES = ("Transversal", "Coronal", "Sagittal")
#: The physical directions of MaRGE's planning axes 0, 1 and 2, as columns.
#: MaRGE takes the across and down directions of its Transversal, Coronal and
#: Sagittal images as its axes 2 and 1, 2 and 0, and 1 and 0; on the
#: localizer's planes these are +x and +y, +x and -z, and +y and -z.
MARGE_AXES = np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]])
#: Centimetres per unit of a field-of-view entry.
CM_PER_UNIT = {"mm": 0.1, "cm": 1.0, "m": 100.0}

_EDITABLE = ("float", "int", "bool", "stringlist")


def console_mode() -> bool:
    """Whether MaRGE runs as the console of pulserver's virtual scanner."""
    return bool(GATEWAY)


def parse_listing(reply: str) -> dict[str, dict[str, Any]]:
    """Return the entries of a ``list`` reply, by name, as the interpreter reads them.

    Each entry holds its ``kind`` and ``value``; a number also its ``mode``,
    ``min``, ``max``, ``step``, ``unit`` and dropdown ``options``, and a
    stringlist its ``options``.
    """
    entries: dict[str, dict[str, Any]] = {}
    inside = False
    for line in reply.splitlines():
        text = line.strip()
        if text == PROTOCOL_BEGIN:
            inside = True
            continue
        if text == PROTOCOL_END:
            break
        if not inside or text.startswith("#") or ": " not in text:
            continue
        name, _, spec = text.partition(": ")
        kind, *fields = spec.split("|")
        if kind in ("float", "int"):
            cast = float if kind == "float" else int
            mode, value, low, high, step, unit, *options = fields
            entries[name] = {
                "kind": kind,
                "mode": mode,
                "value": cast(value),
                "min": cast(low),
                "max": cast(high),
                "step": cast(step),
                "unit": unit,
                "options": [cast(o) for o in options if o],
            }
        elif kind == "bool":
            entries[name] = {"kind": kind, "value": fields[0] == "true"}
        elif kind == "stringlist":
            index, *options = fields
            entries[name] = {
                "kind": kind,
                "value": options[int(index)],
                "options": options,
            }
        elif kind == "config":
            entries[name] = {"kind": kind, "value": int(fields[0])}
        else:
            entries[name] = {
                "kind": kind,
                "value": "|".join(fields).replace("\\n", "\n"),
            }
    return entries


def shown(entries: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Return the entries a console shows for editing: the editable ones outside the prescription."""
    return [
        name
        for name, entry in entries.items()
        if entry["kind"] in _EDITABLE
        and entry.get("mode") != "off"
        and name not in PRESCRIPTION
    ]


def format_values(
    values: Mapping[str, Any],
    entries: Mapping[str, Mapping[str, Any]],
    rotation: np.ndarray,
    offset_mm: Sequence[float],
) -> str:
    """Return the value block of a request: the given values, then the prescription.

    A stringlist travels as its option index and a boolean as ``true`` or
    ``false``, as the interpreter sends them. ``rotation`` takes the logical
    readout, phase and slice axes to the physical ones, and ``offset_mm`` is the
    field-of-view offset along the logical axes, in mm.
    """
    lines = []
    for name, value in values.items():
        entry = entries[name]
        if entry["kind"] not in _EDITABLE or name in PRESCRIPTION:
            continue
        if entry["kind"] == "bool":
            text = "true" if value else "false"
        elif entry["kind"] == "stringlist":
            text = str(entry["options"].index(value))
        elif entry["kind"] == "int":
            text = str(int(value))
        else:
            text = repr(float(value))
        lines.append(f"{name}: {text}")
    prescribed = [*np.asarray(offset_mm, dtype=float), *np.asarray(rotation).ravel()]
    lines += [f"{n}: {float(v)!r}" for n, v in zip(PRESCRIPTION, prescribed, strict=True)]
    return "\n".join([PROTOCOL_BEGIN, *lines, PROTOCOL_END]) + "\n"


def prescription(
    orientation: str,
    angle_deg: float,
    axis: Sequence[float],
    dfov_mm: Sequence[float],
) -> tuple[np.ndarray, np.ndarray]:
    """Return the rotation from logical to physical axes and the offset along the logical axes, in mm.

    The orientation's rotation is turned by ``angle_deg`` about the physical
    ``axis``, right-handed, as MaRGE's FOV planning turns a box; ``dfov_mm``
    is the field-of-view centre along the physical axes.
    """
    axis = np.asarray(axis, dtype=float)
    norm = np.linalg.norm(axis)
    turn = np.eye(3)
    if norm > 0.0 and angle_deg != 0.0:
        k = axis / norm
        cross = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
        theta = math.radians(angle_deg)
        turn = np.eye(3) + math.sin(theta) * cross + (1 - math.cos(theta)) * cross @ cross
    rotation = turn @ ORIENTATIONS[orientation]
    return rotation, rotation.T @ np.asarray(dfov_mm, dtype=float)


def subject_phantom(session: Mapping[str, Any]) -> str:
    """Return the phantom a session's subject names: its name, else its id."""
    for key in ("subject_name", "subject_id"):
        value = str(session.get(key, "") or "").strip()
        if value:
            return value
    return ""


def dicom_images(files: Sequence[bytes]) -> list[tuple[np.ndarray, Any]]:
    """Return the pixels, rescaled, and the dataset of each DICOM file."""
    import pydicom

    images = []
    for data in files:
        dataset = pydicom.dcmread(io.BytesIO(data))
        pixels = dataset.pixel_array.astype(float)
        pixels = pixels * float(getattr(dataset, "RescaleSlope", 1.0)) + float(
            getattr(dataset, "RescaleIntercept", 0.0)
        )
        images.append((pixels, dataset))
    return images


class Gateway:
    """A connection to ``pulserver console``, one request answered at a time.

    Parameters
    ----------
    address
        The console's WebSocket address, ``ws://host:port``.
    connect
        Opens a connection with ``send`` and ``recv`` methods; the ``websockets``
        synchronous client by default.
    """

    def __init__(self, address: str, connect: Callable[[str], Any] | None = None):
        if connect is None:
            from websockets.sync.client import connect as open_connection

            def connect(url: str) -> Any:
                return open_connection(url, max_size=None).__enter__()

        self._socket = connect(address)
        self._next = 0

    def request(
        self, call: str, on_message: Callable[[dict], None] | None = None, **fields: Any
    ) -> dict:
        """Send one request and return its last reply; ``on_message`` sees every earlier one.

        A scan answers with its clock and images before its ``done`` reply;
        every other call answers once. A reply carrying ``error`` is raised.
        """
        self._next += 1
        ident = self._next
        self._socket.send(json.dumps({"id": ident, "call": call, **fields}))
        while True:
            reply = json.loads(self._socket.recv())
            if reply.get("id") != ident:
                continue
            if "error" in reply:
                raise RuntimeError(reply["error"])
            if call != "scan" or "done" in reply:
                return reply
            if on_message is not None:
                on_message(reply)

    def localizer(self, subject: str) -> list[bytes]:
        """Start an exam on ``subject`` and return its three-plane localizer, as DICOM files."""
        reply = self.request("exam", subject=subject)
        return [base64.b64decode(f) for f in reply["localizer"]]


class AsyncGateway:
    """A connection to ``pulserver console`` on an asyncio event loop, as MaRGE has in the browser.

    Under Pyodide a Qt handler cannot wait on the network, so every request is
    a coroutine; the connection is the browser's WebSocket there and the
    ``websockets`` asyncio client elsewhere.
    """

    asynchronous = True

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._next = 0

    @classmethod
    async def open(cls, address: str) -> "AsyncGateway":
        """Connect to the console at ``address``."""
        if sys.platform == "emscripten":
            return cls(await _BrowserSocket.open(address))
        from websockets.asyncio.client import connect

        return cls(await connect(address, max_size=None))

    async def request(
        self, call: str, on_message: Callable[[dict], None] | None = None, **fields: Any
    ) -> dict:
        """Send one request and return its last reply, as :meth:`Gateway.request` does."""
        self._next += 1
        ident = self._next
        await self._connection.send(json.dumps({"id": ident, "call": call, **fields}))
        while True:
            reply = json.loads(await self._connection.recv())
            if reply.get("id") != ident:
                continue
            if "error" in reply:
                raise RuntimeError(reply["error"])
            if call != "scan" or "done" in reply:
                return reply
            if on_message is not None:
                on_message(reply)

    async def localizer(self, subject: str) -> list[bytes]:
        """Start an exam on ``subject`` and return its three-plane localizer, as DICOM files."""
        reply = await self.request("exam", subject=subject)
        return [base64.b64decode(f) for f in reply["localizer"]]


class _BrowserSocket:
    """The browser's WebSocket, its messages held in an asyncio queue."""

    @classmethod
    async def open(cls, url: str) -> "_BrowserSocket":
        from js import WebSocket
        from pyodide.ffi import create_proxy

        self = cls()
        self._queue = asyncio.Queue()
        opened = asyncio.get_event_loop().create_future()

        def on_open(event: Any) -> None:
            if not opened.done():
                opened.set_result(None)

        def on_error(event: Any) -> None:
            if not opened.done():
                opened.set_exception(OSError(f"cannot reach {url}"))

        self._socket = WebSocket.new(url)
        self._proxies = [create_proxy(on_open), create_proxy(on_error)]
        self._proxies.append(create_proxy(lambda event: self._queue.put_nowait(event.data)))
        self._socket.onopen, self._socket.onerror, self._socket.onmessage = self._proxies
        await opened
        return self

    async def send(self, text: str) -> None:
        self._socket.send(text)

    async def recv(self) -> str:
        return await self._queue.get()


async def _answer(reply: Any) -> Any:
    return await reply if inspect.isawaitable(reply) else reply


def listings(gateway: Gateway) -> dict[str, dict[str, dict[str, Any]]]:
    """Return the entries of each plugin the console lists, by plugin."""
    return asyncio.run(listings_async(gateway))


async def listings_async(gateway: Any) -> dict[str, dict[str, dict[str, Any]]]:
    """Return the entries of each plugin the console lists, by plugin, on either kind of gateway."""
    found = {}
    for plugin in (await _answer(gateway.request("plugins")))["plugins"]:
        reply = await _answer(gateway.request("list", plugin=plugin))
        if reply["status"] == 0:
            found[plugin] = parse_listing(reply["reply"])
    return found


#: The gateway and the plugin listings, fetched before MaRGE imports its
#: sequences where fetching may not block, as in the browser.
PRELOADED: tuple[Any, dict[str, dict[str, dict[str, Any]]]] | None = None

#: MaRGE's sequence toolbar: a scan that completes in the background runs its
#: sequence through it again, which then shows the scan's images.
TOOLBAR: Any = None

#: Plays a scan's sound as it streams, given ``(n, 2)`` samples in [-1, 1] and
#: their rate in Hz; scans are not asked for their sound without it.
SPEAKER: Callable[[np.ndarray, float], None] | None = None


def register_console(toolbar: Any) -> None:
    """Record MaRGE's sequence toolbar, through which background scans show their images."""
    global TOOLBAR
    TOOLBAR = toolbar


def _show_clock(name: str, clock: float, duration: float) -> None:
    """Show a scan's clock, in s, in the status bar of MaRGE's main window."""
    if TOOLBAR is not None:
        TOOLBAR.main.statusBar().showMessage(f"{name}: {clock:.1f} s of {duration:.1f} s")


def sequence_classes(
    gateway: Any, entries: Mapping[str, Mapping[str, Mapping[str, Any]]]
) -> dict[str, type]:
    """Return a MaRGE sequence class for each plugin's entries, and the localizer's."""
    from marge.seq.mriBlankSeq import MRIBLANKSEQ

    classes = {"Localizer": _localizer_class(MRIBLANKSEQ, gateway)}
    for plugin, plugin_entries in entries.items():
        classes[plugin] = _plugin_class(MRIBLANKSEQ, gateway, plugin, plugin_entries)
    return classes


def _run(sequence: Any, gateway: Any, work: Callable[[], Any]) -> bool:
    """Run a sequence's work, in the background on an asynchronous gateway.

    With a synchronous gateway the work runs to its end. With an asynchronous
    one it starts as a task and the run reports nothing yet; once the task
    completes, the sequence is run again through MaRGE's toolbar, and that run
    reports the task's outcome at once, so that MaRGE shows its output.
    """
    finished = getattr(sequence, "_finished", None)
    if finished is not None:
        sequence._finished = None
        return finished
    if not getattr(gateway, "asynchronous", False):
        return asyncio.run(work())

    async def background() -> None:
        try:
            sequence._finished = await work()
        except Exception as error:  # noqa: BLE001 -- MaRGE's console shows it
            print(f"pulserver: {error}")
            sequence._finished = False
        sequence.deleteOutput()
        if TOOLBAR is not None:
            try:
                TOOLBAR.startAcquisition(seq_name=sequence.mapVals["seqName"])
            except Exception:  # noqa: BLE001 -- MaRGE's console shows it
                print(traceback.format_exc())

    asyncio.ensure_future(background())
    return False


def _image_output(
    images: Sequence[tuple[np.ndarray, Any]], titles: Sequence[str] | None = None
) -> list[dict]:
    """Return MaRGE's output for DICOM images, drawn with their columns across and rows down.

    MaRGE's image plot draws an array's first axis across, so each image is
    handed over transposed. Without ``titles``, each is titled by its series.
    """
    return [
        {
            "widget": "image",
            "data": pixels.T[np.newaxis],
            "xLabel": "",
            "yLabel": "",
            "title": titles[column] if titles else str(getattr(dataset, "SeriesDescription", "")),
            "row": 0,
            "col": column,
        }
        for column, (pixels, dataset) in enumerate(images)
    ]


def _console_class(base: type) -> type:
    class ConsoleSequence(base):
        """A MaRGE sequence whose images are the DICOM files the console returns, in ``files``."""

        titles: Sequence[str] | None = None

        def sequenceAnalysis(self, mode=None) -> list:  # noqa: N802 -- MaRGE's API
            self.mode = mode
            self.output = _image_output(dicom_images(self.files), self.titles)
            self.saveRawData()
            return self.output

        def saveRawData(self) -> None:  # noqa: N802 -- MaRGE's API
            """Save the run as MaRGE does, with the console's DICOM files in place of MaRGE's own."""
            output, self.output = self.output, []
            try:
                super().saveRawData()
            finally:
                self.output = output
            folder = os.path.join(os.path.dirname(self.directory_mat), "dcm")
            for index, data in enumerate(self.files, 1):
                with open(os.path.join(folder, f"{self.file_name}.{index:04d}.dcm"), "wb") as file:
                    file.write(data)

    return ConsoleSequence


def _localizer_class(base: type, gateway: Any) -> type:
    class Localizer(_console_class(base)):
        """The three planes of the subject's phantom, drawn from its ground truth, 25.6 cm square."""

        titles = PLANE_TITLES

        def __init__(self) -> None:
            super().__init__()
            self.addParameter(key="seqName", string="Localizer", val="Localizer")
            self.addParameter(key="toMaRGE", val=True)
            self.addParameter(key="pulserverConsole", val=True)
            self.addParameter(key="fov", val=[25.6, 25.6, 25.6], units=1e-2)
            self.addParameter(key="dfov", val=[0.0, 0.0, 0.0], units=1e-3)
            self.files: list[bytes] = []

        def sequenceRun(self, plotSeq=0, demo=False) -> bool:  # noqa: N802 -- MaRGE's API
            async def work() -> bool:
                subject = subject_phantom(getattr(self, "session", {}) or {})
                self.files = await _answer(gateway.localizer(subject))
                return True

            return _run(self, gateway, work)

    return Localizer


def _marge_axes(rotation: np.ndarray) -> list[int]:
    """Return the MaRGE axis closest to each of the logical readout, phase and slice axes."""
    return [int(np.argmax(np.abs(MARGE_AXES.T @ column))) for column in np.asarray(rotation).T]


def _plugin_class(
    base: type, gateway: Any, plugin: str, entries: Mapping[str, Mapping[str, Any]]
) -> type:
    sizes = [name for name in FOV_SIZE if name in shown(entries)]
    names = [name for name in shown(entries) if name not in sizes]

    def cm_per_unit(name: str) -> float:
        return CM_PER_UNIT.get(entries[name].get("unit", ""), CM_PER_UNIT["mm"])

    class PluginSequence(_console_class(base)):
        def __init__(self) -> None:
            super().__init__()
            self.addParameter(key="seqName", string=plugin, val=plugin)
            self.addParameter(key="toMaRGE", val=True)
            self.addParameter(key="pulserverConsole", val=True)
            for name in names:
                entry = entries[name]
                unit = entry.get("unit", "")
                label = f"{name} ({unit})" if unit else name
                self.addParameter(key=name, string=label, val=entry["value"], units=1, field="SEQ")
            fov = [0.0, 0.0, 0.0]
            for name, axis in zip(FOV_SIZE, _marge_axes(ORIENTATIONS["axial"]), strict=True):
                if name in sizes:
                    fov[axis] = entries[name]["value"] * cm_per_unit(name)
            self.addParameter(key="fov", string="FOV (cm)", val=fov, units=1e-2, field="IM")
            self.addParameter(
                key="orientation", string="Orientation", val="axial", units=1, field="IM"
            )
            self.addParameter(
                key="dfov", string="FOV centre (mm)", val=[0.0, 0.0, 0.0], units=1e-3, field="IM"
            )
            self.addParameter(key="angle", string="Angle (deg)", val=0.0, units=1, field="IM")
            self.addParameter(
                key="rotationAxis", string="Rotation axis", val=[1.0, 0.0, 0.0], units=1, field="IM"
            )
            self.files: list[bytes] = []
            self.clock = (0.0, 0.0)

        def sequenceRun(self, plotSeq=0, demo=False) -> bool:  # noqa: N802 -- MaRGE's API
            return _run(self, gateway, self._scan)

        async def _scan(self) -> bool:
            centre = MARGE_AXES @ np.asarray(self.mapVals["dfov"], dtype=float)
            rotation, offset = prescription(
                str(self.mapVals["orientation"]),
                float(self.mapVals["angle"]),
                MARGE_AXES @ np.asarray(self.mapVals["rotationAxis"], dtype=float),
                centre,
            )
            values = {name: self.mapVals[name] for name in names}
            for name, axis in zip(FOV_SIZE, _marge_axes(rotation), strict=True):
                if name in sizes:
                    values[name] = float(self.mapVals["fov"][axis]) / cm_per_unit(name)
            block = format_values(values, entries, rotation, offset)
            generated = await _answer(gateway.request("generate", plugin=plugin, block=block))
            if generated["status"] != 0:
                raise RuntimeError(generated["reply"].strip())
            files = []

            def received(message: dict) -> None:
                if "clock" in message:
                    self.clock = (message["clock"], message["duration"])
                    _show_clock(plugin, *self.clock)
                    if "sound" in message and SPEAKER is not None:
                        pcm = np.frombuffer(base64.b64decode(message["sound"]), dtype="<i2")
                        SPEAKER(pcm.reshape(-1, 2) / 32767.0, float(message["rate"]))
                elif "dicom" in message:
                    files.append(base64.b64decode(message["dicom"]))

            done = await _answer(
                gateway.request(
                    "scan",
                    on_message=received,
                    design=generated["design"],
                    rotation=rotation.ravel().tolist(),
                    centre_mm=centre.tolist(),
                    sound=SPEAKER is not None,
                )
            )
            self.files = files
            return done["done"] == 0

    PluginSequence.__name__ = PluginSequence.__qualname__ = f"Pulserver_{plugin}"
    return PluginSequence


def install(preloaded: tuple[Any, dict] | None = None) -> None:
    """Add the console's sequences to this module, where MaRGE's sequence list finds them.

    ``preloaded`` is a gateway and the listings fetched through it; without
    it, the listings are fetched here, through a blocking gateway. In the
    browser, where nothing may block, the page fetches them with an
    :class:`AsyncGateway` and passes them in before MaRGE is imported.
    """
    global PRELOADED
    if preloaded is not None:
        PRELOADED = preloaded
    if PRELOADED is None:
        gateway = Gateway(GATEWAY)
        PRELOADED = (gateway, listings(gateway))
    globals().update({cls.__name__: cls for cls in sequence_classes(*PRELOADED).values()})


if console_mode() and sys.platform != "emscripten":
    try:
        install()
    except OSError as error:
        print(f"pulserver console at {GATEWAY} is not reachable: {error}")
