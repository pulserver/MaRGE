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
#: The explicit saturation bands' mask: bit n - 1 turns band n on.
BAND_MASK = "exsat_mask"

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
) -> list[tuple[int, geometry.Band]]:
    """Return each explicit saturation band a sequence's values turn on, by its number, 1 to 6."""
    if BAND_MASK not in entries:
        return []
    mask = int(values[key(BAND_MASK)])
    found = []
    for n in range(1, 7):
        if not mask & (1 << (n - 1)) or f"exsat{n}_loc" not in entries:
            continue
        normal = np.array([float(values[key(f"exsat{n}_normal_{a}")]) for a in "xyz"])
        length = float(np.linalg.norm(normal))
        if length == 0.0:
            continue
        position = _mm(entries, f"exsat{n}_loc", values[key(f"exsat{n}_loc")])
        thickness = _mm(entries, f"exsat{n}_thickness", values[key(f"exsat{n}_thickness")])
        found.append((n, geometry.Band(normal / length, position, thickness)))
    return found


def band_written(
    n: int,
    band: geometry.Band,
    values: Mapping[str, Any],
    entries: Mapping[str, Mapping[str, Any]],
    key: Key = str,
) -> dict[str, Any]:
    """Return the values that place band ``n``, those that change only."""
    found = {}
    for axis, component in zip("xyz", band.normal, strict=True):
        name = f"exsat{n}_normal_{axis}"
        found[key(name)] = _held(entries[name], float(component))
    for name, mm in ((f"exsat{n}_loc", band.position), (f"exsat{n}_thickness", band.thickness)):
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
