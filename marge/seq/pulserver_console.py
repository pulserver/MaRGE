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
import re
import sys
import traceback
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np

#: WebSocket address of ``pulserver console``; the console mode is off without it.
GATEWAY = os.environ.get("MARGE_PULSERVER", "")

PROTOCOL_BEGIN = "[Protocol]"
PROTOCOL_END = "[Protocol End]"
#: The delimiters of a console that predates pulserver's ``[Protocol]`` block, also read.
_FORMER = ("[NimPulseqGUI Protocol]", "[NimPulseqGUI Protocol End]")
FOV_OFFSET = ("fov_offset_x", "fov_offset_y", "fov_offset_z")
FOV_ROTATION = tuple(f"fov_rotation_{i}{j}" for i in (1, 2, 3) for j in (1, 2, 3))
PRESCRIPTION = FOV_OFFSET + FOV_ROTATION

#: MaRGE's names of the localizer's axial, coronal and sagittal planes.
PLANE_TITLES = ("Transversal", "Coronal", "Sagittal")

#: The keys MaRGE gives every sequence; a protocol entry of the same name is
#: held under its name with this suffix.
MARGE_KEYS = frozenset(("seqName", "toMaRGE", "pulserverConsole", "fov", "dfov", "angle", "rotationAxis"))
RENAMED = "_protocol"

#: A user entry, ``user<N>_value``, named by its ``user<N>_name`` description.
USER_ENTRY = re.compile(r"user(\d+)_value")

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
        if text in (PROTOCOL_BEGIN, _FORMER[0]):
            inside = True
            continue
        if text in (PROTOCOL_END, _FORMER[1]):
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


#: Entries a sequence states once, as the interpreter reads them at its start,
#: and a console does not edit.
STATED = ("imaging_mode",)


def shown(entries: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Return the entries a console shows for editing: the editable ones outside the prescription and those the sequence states."""
    return [
        name
        for name, entry in entries.items()
        if entry["kind"] in _EDITABLE
        and entry.get("mode") != "off"
        and name not in PRESCRIPTION
        and name not in STATED
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
            # MaRGE's parameter tabs hand an edited boolean back as its text.
            on = value.strip().lower() == "true" if isinstance(value, str) else bool(value)
            text = "true" if on else "false"
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

        A scan answers with its preparation, clock and images before its ``done`` reply;
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

    def localizer(self, subject: str, coil: str | None = None) -> list[bytes]:
        """Start an exam on ``subject`` in ``coil`` and return its three-plane localizer, as DICOM files.

        Without ``coil``, the exam keeps the coil the console has.
        """
        reply = self.request("exam", subject=subject, **_named(coil))
        return [base64.b64decode(f) for f in reply["localizer"]]


class AsyncGateway:
    """A connection to ``pulserver console`` on an asyncio event loop, as MaRGE has in the browser.

    Under Pyodide a Qt handler cannot wait on the network, so every request is
    a coroutine; the connection is the browser's WebSocket there and the
    ``websockets`` asyncio client elsewhere. Requests may overlap, as two scans
    started one after the other do: each reply reaches the request whose id it
    carries, whichever request read it off the connection.
    """

    asynchronous = True

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._next = 0
        self._replies: dict[int, asyncio.Queue] = {}
        self._reading = asyncio.Lock()

    @classmethod
    async def open(cls, address: str) -> AsyncGateway:
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
        self._replies[ident] = asyncio.Queue()
        try:
            await self._connection.send(json.dumps({"id": ident, "call": call, **fields}))
            while True:
                reply = await self._reply(ident)
                if "error" in reply:
                    raise RuntimeError(reply["error"])
                if call != "scan" or "done" in reply:
                    return reply
                if on_message is not None:
                    on_message(reply)
        finally:
            del self._replies[ident]

    async def _reply(self, ident: int) -> dict:
        """Return the next reply to request ``ident``, reading the connection, one reader at a time, until one has arrived."""
        replies = self._replies[ident]
        while replies.empty():
            async with self._reading:
                if not replies.empty():
                    break
                reply = json.loads(await self._connection.recv())
                if reply.get("id") in self._replies:
                    self._replies[reply["id"]].put_nowait(reply)
        return replies.get_nowait()

    async def localizer(self, subject: str, coil: str | None = None) -> list[bytes]:
        """Start an exam on ``subject`` in ``coil`` and return its three-plane localizer, as :meth:`Gateway.localizer` does."""
        reply = await self.request("exam", subject=subject, **_named(coil))
        return [base64.b64decode(f) for f in reply["localizer"]]


def _named(coil: str | None) -> dict[str, str]:
    return {} if coil is None else {"coil": coil}


class _BrowserSocket:
    """The browser's WebSocket, its messages held in an asyncio queue."""

    @classmethod
    async def open(cls, url: str) -> _BrowserSocket:
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


async def coil_names(gateway: Any) -> list[str]:
    """Return the names of the virtual scanner's coils, on either kind of gateway."""
    return [coil["name"] for coil in (await _answer(gateway.request("coils")))["coils"]]


async def exam_coil(gateway: Any, session: Mapping[str, Any]) -> str | None:
    """Return the session's RF coil where the virtual scanner has a coil of that name; else None, and say so."""
    name = str(session.get("rf_coil", "") or "")
    names = await coil_names(gateway)
    if name in names:
        return name
    print(f"The virtual scanner's coils are {names}, not {name!r}: the exam keeps its coil.")
    return None


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


#: The console's workspace, where each run's series is shown and the selected
#: sequence prescribed; MaRGE shows a run's images itself without it.
WORKSPACE: Any = None


def register_workspace(workspace: Any) -> None:
    """Record the console's workspace."""
    global WORKSPACE
    WORKSPACE = workspace


def register_console(toolbar: Any) -> None:
    """Record MaRGE's sequence toolbar, through which background scans show their images."""
    global TOOLBAR
    TOOLBAR = toolbar


def _show_clock(name: str, clock: float, duration: float) -> None:
    """Show a scan's clock, in s, in the status bar of MaRGE's main window."""
    if TOOLBAR is not None:
        TOOLBAR.main.statusBar().showMessage(f"{name}: {clock:.1f} s of {duration:.1f} s")


def _show_preparing(name: str, left: float | None) -> None:
    """Show that a scan is simulated ahead of its clock, and the time left, in s, where known."""
    if TOOLBAR is not None:
        text = "preparing" if left is None else f"preparing, {left:.0f} s left"
        TOOLBAR.main.statusBar().showMessage(f"{name}: {text}")


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
            """Save the run; show its images in the workspace as a series of the exam, else as MaRGE's output."""
            self.mode = mode
            self.output = [] if WORKSPACE is not None else _image_output(dicom_images(self.files), self.titles)
            self.saveRawData()
            if WORKSPACE is not None and self.files:
                from marge.console.series import Series

                WORKSPACE.add(
                    Series.of(
                        len(WORKSPACE.series) + 1,
                        str(self.mapVals["seqName"]),
                        str(self.mapVals.get("fileName", "")),
                        self.files,
                    )
                )
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
            # Carried so that the localizer's FOV box is redrawn turned as planned.
            self.addParameter(key="angle", val=0.0, units=1)
            self.addParameter(key="rotationAxis", val=[1.0, 0.0, 0.0], units=1)
            self.files: list[bytes] = []

        def sequenceRun(self, plotSeq=0, demo=False) -> bool:  # noqa: N802 -- MaRGE's API
            async def work() -> bool:
                session = getattr(self, "session", {}) or {}
                coil = await exam_coil(gateway, session)
                self.files = await _answer(gateway.localizer(subject_phantom(session), coil))
                return True

            return _run(self, gateway, work)

    return Localizer


def _key(name: str) -> str:
    """Return the key of a protocol entry among a sequence's values."""
    return f"{name}{RENAMED}" if name in MARGE_KEYS else name


def _field(name: str) -> str:
    """Return MaRGE's tab of a protocol entry: the image tab for what places and samples the image, the other tab for the user entries, the sequence tab for the rest."""
    from marge.console import prescribe

    if USER_ENTRY.fullmatch(name):
        return "OTH"
    if name in (*prescribe.GEOMETRY, prescribe.MODE, "nx", "ny"):
        return "IM"
    return "SEQ"


def _label(name: str, entries: Mapping[str, Mapping[str, Any]]) -> str:
    """Return the label of an entry: a user entry's name where the plugin gives one, with the entry's unit."""
    user = USER_ENTRY.fullmatch(name)
    named = entries.get(f"user{user.group(1)}_name") if user else None
    label = str(named["value"]) if named and str(named["value"]).strip() else name
    unit = entries[name].get("unit", "")
    return f"{label} ({unit})" if unit else label


def _plugin_class(
    base: type, gateway: Any, plugin: str, entries: Mapping[str, Mapping[str, Any]]
) -> type:
    from marge.console import prescribe

    names = shown(entries)

    class PluginSequence(_console_class(base)):
        def __init__(self) -> None:
            super().__init__()
            self.addParameter(key="seqName", string=plugin, val=plugin)
            self.addParameter(key="toMaRGE", val=True)
            self.addParameter(key="pulserverConsole", val=True)
            # MaRGE's own field of view and centre, in cm and mm, which it keeps
            # with each run; the prescription is the values below.
            self.addParameter(key="fov", val=[0.0, 0.0, 0.0], units=1e-2)
            self.addParameter(key="dfov", val=[0.0, 0.0, 0.0], units=1e-3)
            for key, label, value, tip in (
                (prescribe.ORIENTATION, "Orientation", "axial", "axial, coronal or sagittal: the plane the prescription is turned from"),
                (prescribe.CENTRE, "Centre (mm)", [0.0, 0.0, 0.0], "Centre of the stack along the patient's L, P and S axes"),
                (prescribe.INPLANE, "In-plane (deg)", 0.0, "Turn about the slice direction"),
                (prescribe.TILT_READ, "Tilt about read (deg)", 0.0, "Tilt of the starting plane about its readout direction"),
                (prescribe.TILT_PHASE, "Tilt about phase (deg)", 0.0, "Tilt of the starting plane about its phase direction"),
            ):
                self.addParameter(key=key, string=label, val=value, units=1, field="IM", tip=tip)
            for name in names:
                self.addParameter(
                    key=_key(name),
                    string=_label(name, entries),
                    val=entries[name]["value"],
                    units=1,
                    field=_field(name),
                )
            self.files: list[bytes] = []
            self.clock = (0.0, 0.0)
            self.prepared = 0

        def sequenceRun(self, plotSeq=0, demo=False) -> bool:  # noqa: N802 -- MaRGE's API
            return _run(self, gateway, self._scan)

        def planned(self):
            """Return the prescription the values state."""
            return prescribe.prescription(self.mapVals, entries, _key)

        def plan(self, planned) -> dict:
            """Set the values that state ``planned``; return those that changed, by their keys."""
            changed = prescribe.written(planned, self.mapVals, entries, _key)
            self.mapVals.update(changed)
            return changed

        def planned_bands(self) -> list:
            return prescribe.bands(self.mapVals, entries, _key)

        def plan_band(self, axis: str, location: str, band) -> dict:
            """Set the values that place a saturation band; return those that changed, by their keys."""
            changed = prescribe.band_written(axis, location, band, self.mapVals, entries, _key)
            self.mapVals.update(changed)
            return changed

        def parameterChanged(self, key: str) -> None:  # noqa: N802 -- MaRGE's naming
            """Redraw the prescription once a value is typed."""
            if WORKSPACE is not None:
                WORKSPACE.redraw()

        async def _scan(self) -> bool:
            planned = self.planned()
            rotation, centre = planned.rotation, planned.centre
            self.mapVals["fov"] = [0.1 * planned.fov[0], 0.1 * planned.fov[1], 0.2 * planned.extent]
            self.mapVals["dfov"] = [float(c) for c in centre]
            values = {name: self.mapVals[_key(name)] for name in names}
            block = format_values(values, entries, rotation, rotation.T @ centre)
            generated = await _answer(gateway.request("generate", plugin=plugin, block=block))
            if generated["status"] != 0:
                raise RuntimeError(generated["reply"].strip())
            files = []

            def received(message: dict) -> None:
                if "preparing" in message:
                    self.prepared += 1
                    _show_preparing(plugin, message["preparing"])
                elif "sound" in message:
                    if SPEAKER is not None:
                        pcm = np.frombuffer(base64.b64decode(message["sound"]), dtype="<i2")
                        SPEAKER(pcm.reshape(-1, 2) / 32767.0, float(message["rate"]))
                elif "clock" in message:
                    self.clock = (message["clock"], message["duration"])
                    _show_clock(plugin, *self.clock)
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
