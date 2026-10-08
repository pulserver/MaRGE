"""A plugin's protocol entries as a prescription and saturation bands, and back.

The prescription's orientation is a starting plane, an in-plane turn and two
tilts, and its centre a point in the patient's LPS frame, in mm; its field of
view, slices and thickness are the plugin's own entries, in the units its
listing states. Values written back are held within each entry's range and on
its step, as the interpreter holds them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import numpy as np

from . import geometry

#: Millimetres per unit of a length entry.
MM_PER_UNIT = {"mm": 1.0, "cm": 10.0, "m": 1000.0}

#: The entries that place and size what a scan acquires, and those of its
#: saturation bands, by their meaning.
READ_FOV, PHASE_FOV, THICKNESS, SLICES, GAP, MODE = (
    "fov", "phase_fov", "slice_thickness", "nslices", "slice_spacing", "imaging_mode"
)
GEOMETRY = (READ_FOV, PHASE_FOV, SLICES, THICKNESS, GAP)
AXES = "xyz"
LOCATIONS = ("loc1", "loc2")

#: The console's own values of the prescription, beside the plugin's entries.
ORIENTATION, CENTRE, INPLANE, TILT_READ, TILT_PHASE = (
    "orientation", "centre", "inplane", "tilt_read", "tilt_phase"
)
PRESCRIPTION = (ORIENTATION, CENTRE, INPLANE, TILT_READ, TILT_PHASE)

Key = Callable[[str], str]


def slab(entries: Mapping[str, Mapping[str, Any]]) -> bool:
    """Whether a plugin's locations form one slab, as its ``imaging_mode`` states; a plugin that states none acquires slices."""
    mode = entries.get(MODE)
    return mode is not None and str(mode["value"]).strip().lower() == "3d"


def unstated(entries: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Return the geometry entries a plugin does not state, without which its prescription cannot be drawn whole."""
    return [name for name in (MODE, READ_FOV, SLICES, THICKNESS) if name not in entries]


def prescription(
    values: Mapping[str, Any],
    entries: Mapping[str, Mapping[str, Any]],
    key: Key = str,
) -> geometry.Prescription:
    """Return the prescription a sequence's values state; ``key`` names the value of each entry."""
    matrix = geometry.rotation(
        str(values[ORIENTATION]).strip().lower(),
        float(values[INPLANE]),
        float(values[TILT_READ]),
        float(values[TILT_PHASE]),
    )

    def length(name: str) -> float | None:
        return _mm(entries, name, values[key(name)]) if name in entries else None

    read = length(READ_FOV) or 0.0
    slices = entries.get(SLICES)
    return geometry.Prescription(
        centre=np.asarray(values[CENTRE], dtype=float),
        rotation=matrix,
        fov=(read, length(PHASE_FOV) or read),
        thickness=length(THICKNESS),
        gap=length(GAP) or 0.0,
        slices=int(values[key(SLICES)]) if slices else 1,
        slab=slab(entries),
        limits=(int(slices["min"]), int(slices["max"])) if slices else (1, 1),
    )


def written(
    planned: geometry.Prescription,
    values: Mapping[str, Any],
    entries: Mapping[str, Mapping[str, Any]],
    key: Key = str,
) -> dict[str, Any]:
    """Return the values that state ``planned``, by their keys, those that change only."""
    base = str(values[ORIENTATION]).strip().lower()
    inplane, tilt_read, tilt_phase = geometry.angles(planned.rotation, base)
    found = {
        INPLANE: round(inplane, 2),
        TILT_READ: round(tilt_read, 2),
        TILT_PHASE: round(tilt_phase, 2),
        CENTRE: [round(float(c), 1) for c in planned.centre],
    }
    lengths = {READ_FOV: planned.fov[0], PHASE_FOV: planned.fov[1], THICKNESS: planned.thickness}
    for name, mm in lengths.items():
        if name in entries and mm is not None:
            found[key(name)] = _held(entries[name], mm / MM_PER_UNIT.get(entries[name].get("unit", ""), 1.0))
    if SLICES in entries:
        found[key(SLICES)] = int(_held(entries[SLICES], planned.slices))
    return {name: value for name, value in found.items() if values.get(name) != value}


def bands(
    values: Mapping[str, Any], entries: Mapping[str, Mapping[str, Any]], key: Key = str
) -> list[tuple[str, str, geometry.Band]]:
    """Return each saturation band a sequence's values turn on: its axis letter, its location's name and the band."""
    found = []
    for index, axis in enumerate(AXES):
        if f"sat_{axis}" not in entries:
            continue
        mask = int(values[key(f"sat_{axis}")])
        thickness = _mm(entries, f"sat_{axis}_thickness", values[key(f"sat_{axis}_thickness")])
        for bit, location in enumerate(LOCATIONS):
            if mask & (1 << bit):
                name = f"sat_{axis}_{location}"
                position = _mm(entries, name, values[key(name)])
                found.append((axis, location, geometry.Band(index, position, thickness)))
    return found


def band_written(
    axis: str,
    location: str,
    band: geometry.Band,
    values: Mapping[str, Any],
    entries: Mapping[str, Mapping[str, Any]],
    key: Key = str,
) -> dict[str, Any]:
    """Return the values that place a band, those that change only."""
    found = {}
    for name, mm in ((f"sat_{axis}_{location}", band.position), (f"sat_{axis}_thickness", band.thickness)):
        entry = entries[name]
        found[key(name)] = _held(entry, mm / MM_PER_UNIT.get(entry.get("unit", ""), 1.0))
    return {name: value for name, value in found.items() if values.get(name) != value}


def _mm(entries: Mapping[str, Mapping[str, Any]], name: str, value: Any) -> float:
    return float(value) * MM_PER_UNIT.get(entries[name].get("unit", ""), 1.0)


def _held(entry: Mapping[str, Any], value: float) -> float | int:
    """Return ``value`` within the entry's range, on its step from its minimum."""
    low, high, step = float(entry["min"]), float(entry["max"]), float(entry.get("step") or 0.0)
    value = min(high, max(low, float(value)))
    if step > 0.0:
        value = min(high, low + round((value - low) / step) * step)
    if entry["kind"] == "int":
        return int(round(value))
    return round(value, 6)
