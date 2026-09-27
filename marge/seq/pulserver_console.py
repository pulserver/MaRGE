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

import base64
import io
import json
import math
import os
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


def sequence_classes(gateway: Gateway) -> dict[str, type]:
    """Return a MaRGE sequence class for each plugin the console lists, and the localizer's."""
    from marge.seq.mriBlankSeq import MRIBLANKSEQ

    classes = {"Localizer": _localizer_class(MRIBLANKSEQ, gateway)}
    for plugin in gateway.request("plugins")["plugins"]:
        reply = gateway.request("list", plugin=plugin)
        if reply["status"] == 0:
            entries = parse_listing(reply["reply"])
            classes[plugin] = _plugin_class(MRIBLANKSEQ, gateway, plugin, entries)
    return classes


def _image_output(images: Sequence[tuple[np.ndarray, Any]]) -> list[dict]:
    return [
        {
            "widget": "image",
            "data": pixels[np.newaxis],
            "xLabel": "",
            "yLabel": "",
            "title": str(getattr(dataset, "SeriesDescription", "")),
            "row": 0,
            "col": column,
        }
        for column, (pixels, dataset) in enumerate(images)
    ]


def _localizer_class(base: type, gateway: Gateway) -> type:
    class Localizer(base):
        """The three planes of the subject's phantom, drawn from its ground truth."""

        def __init__(self) -> None:
            super().__init__()
            self.addParameter(key="seqName", string="Localizer", val="Localizer")
            self.addParameter(key="toMaRGE", val=True)
            self.addParameter(key="pulserverConsole", val=True)
            self.images: list = []

        def sequenceRun(self, plotSeq=0, demo=False) -> bool:  # noqa: N802 -- MaRGE's API
            session = getattr(self, "session", {}) or {}
            self.images = dicom_images(gateway.localizer(subject_phantom(session)))
            return True

        def sequenceAnalysis(self, mode=None) -> list:  # noqa: N802 -- MaRGE's API
            self.output = _image_output(self.images)
            return self.output

    return Localizer


def _plugin_class(
    base: type, gateway: Gateway, plugin: str, entries: Mapping[str, Mapping[str, Any]]
) -> type:
    names = shown(entries)

    class PluginSequence(base):
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
            self.addParameter(
                key="orientation", string="Orientation", val="axial", units=1, field="IM"
            )
            self.addParameter(key="dfov", string="FOV centre (mm)", val=[0.0, 0.0, 0.0], units=1, field="IM")
            self.addParameter(key="angle", string="Angle (deg)", val=0.0, units=1, field="IM")
            self.addParameter(
                key="rotationAxis", string="Rotation axis", val=[0.0, 0.0, 1.0], units=1, field="IM"
            )
            self.images: list = []
            self.clock = (0.0, 0.0)

        def sequenceRun(self, plotSeq=0, demo=False) -> bool:  # noqa: N802 -- MaRGE's API
            rotation, offset = prescription(
                str(self.mapVals["orientation"]),
                float(self.mapVals["angle"]),
                self.mapVals["rotationAxis"],
                self.mapVals["dfov"],
            )
            values = {name: self.mapVals[name] for name in names}
            block = format_values(values, entries, rotation, offset)
            generated = gateway.request("generate", plugin=plugin, block=block)
            if generated["status"] != 0:
                raise RuntimeError(generated["reply"].strip())
            files = []

            def received(message: dict) -> None:
                if "clock" in message:
                    self.clock = (message["clock"], message["duration"])
                elif "dicom" in message:
                    files.append(base64.b64decode(message["dicom"]))

            done = gateway.request(
                "scan",
                on_message=received,
                design=generated["design"],
                rotation=rotation.ravel().tolist(),
                centre_mm=[float(c) for c in self.mapVals["dfov"]],
            )
            self.images = dicom_images(files)
            return done["done"] == 0

        def sequenceAnalysis(self, mode=None) -> list:  # noqa: N802 -- MaRGE's API
            self.output = _image_output(self.images)
            return self.output

    PluginSequence.__name__ = PluginSequence.__qualname__ = f"Pulserver_{plugin}"
    return PluginSequence


if console_mode():
    try:
        globals().update(
            {cls.__name__: cls for cls in sequence_classes(Gateway(GATEWAY)).values()}
        )
    except OSError as error:
        print(f"pulserver console at {GATEWAY} is not reachable: {error}")
