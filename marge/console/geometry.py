"""Geometry of a prescription and of the images it is planned on, in the patient's LPS frame, in mm.

The physical axes of pulserver's virtual scanner are the patient's LPS axes for
a subject lying head first and supine, and a prescription's rotation takes the
logical readout, phase and slice axes to them: its columns are the readout,
phase and slice directions. A DICOM image's ``ImageOrientationPatient`` holds
the directions of increasing column and row, so an image of a prescription
carries its readout and phase directions.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np

#: The starting planes of a prescription, as the localizer shows them: the
#: readout, phase and slice directions as columns.
BASES = {
    "axial": np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]),
    "coronal": np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]]),
    "sagittal": np.array([[0.0, 0.0, -1.0], [1.0, 0.0, 0.0], [0.0, -1.0, 0.0]]),
}

#: How far from facing the slices, in degrees, a view may be and still show
#: the field of view as a box projected onto it, rather than the slices as
#: the lines they cut it along.
FACING_DEG = 45.0


def turn(axis: Sequence[float], degrees: float) -> np.ndarray:
    """Return the right-handed rotation by ``degrees`` about ``axis``."""
    k = np.asarray(axis, dtype=float)
    k = k / np.linalg.norm(k)
    cross = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    theta = math.radians(degrees)
    return np.eye(3) + math.sin(theta) * cross + (1.0 - math.cos(theta)) * cross @ cross


def rotation(base: str, inplane: float, tilt_read: float, tilt_phase: float) -> np.ndarray:
    """Return the rotation of a prescription turned from a starting plane, in degrees.

    The plane is tilted about its readout direction, then about its phase
    direction, then turned in-plane about its slice direction: ``B Rx Ry Rz``,
    each factor about the logical axis it names.
    """
    logical = turn((1, 0, 0), tilt_read) @ turn((0, 1, 0), tilt_phase) @ turn((0, 0, 1), inplane)
    return BASES[base] @ logical


def angles(matrix: np.ndarray, base: str) -> tuple[float, float, float]:
    """Return the in-plane turn and the tilts about the readout and phase directions, in degrees, of a rotation from a starting plane.

    The inverse of :func:`rotation`; a tilt about the phase direction of a
    right angle leaves the other two apart only by their sum, which is taken
    as the in-plane turn.
    """
    m = BASES[base].T @ np.asarray(matrix, dtype=float)
    tilt_phase = math.degrees(math.asin(max(-1.0, min(1.0, m[0, 2]))))
    if abs(m[0, 2]) > 1.0 - 1e-12:
        return math.degrees(math.atan2(m[1, 0], m[1, 1])), 0.0, tilt_phase
    tilt_read = math.degrees(math.atan2(-m[1, 2], m[2, 2]))
    inplane = math.degrees(math.atan2(-m[0, 1], m[0, 0]))
    return inplane, tilt_read, tilt_phase


@dataclass(frozen=True)
class ImagePlane:
    """Where an image's pixels are: the plane of a DICOM image.

    Attributes
    ----------
    origin
        Centre of the first pixel, ``ImagePositionPatient``.
    across, down
        Directions of increasing column and row, ``ImageOrientationPatient``.
    spacing
        Distance between columns and between rows.
    shape
        Rows and columns.
    """

    origin: np.ndarray
    across: np.ndarray
    down: np.ndarray
    spacing: tuple[float, float]
    shape: tuple[int, int]

    @classmethod
    def of(cls, dataset) -> ImagePlane:
        """Return the plane of a DICOM image."""
        orientation = np.asarray(dataset.ImageOrientationPatient, dtype=float)
        rows, columns = (float(s) for s in dataset.PixelSpacing)
        return cls(
            np.asarray(dataset.ImagePositionPatient, dtype=float),
            orientation[:3],
            orientation[3:],
            (columns, rows),
            (int(dataset.Rows), int(dataset.Columns)),
        )

    @property
    def normal(self) -> np.ndarray:
        return np.cross(self.across, self.down)

    @property
    def centre(self) -> np.ndarray:
        rows, columns = self.shape
        return self.point(0.5 * (columns - 1), 0.5 * (rows - 1))

    def point(self, column: float, row: float) -> np.ndarray:
        """Return the point at a fractional column and row."""
        return self.origin + column * self.spacing[0] * self.across + row * self.spacing[1] * self.down

    def pixel(self, points: np.ndarray) -> np.ndarray:
        """Return the fractional ``(column, row)`` of points, projected onto the plane along its normal."""
        offset = np.asarray(points, dtype=float) - self.origin
        return np.stack(
            [offset @ self.across / self.spacing[0], offset @ self.down / self.spacing[1]], axis=-1
        )

    def corners(self) -> np.ndarray:
        """Return the corners of the area the pixels cover, in order around it."""
        rows, columns = self.shape
        return np.array(
            [self.point(c, r) for c, r in ((-0.5, -0.5), (columns - 0.5, -0.5), (columns - 0.5, rows - 0.5), (-0.5, rows - 0.5))]
        )


@dataclass(frozen=True)
class Prescription:
    """Where a scan acquires: a stack of slices, or a slab of locations, and its field of view.

    Attributes
    ----------
    centre
        Centre of the stack.
    rotation
        Readout, phase and slice directions as columns.
    fov
        Field of view along the readout and phase directions.
    thickness
        Thickness of a slice, or of a location of a slab; None where the
        sequence does not state it.
    gap
        Gap between adjacent slices; a slab's locations have none.
    slices
        Slices of the stack, or locations of the slab.
    slab
        Whether the locations form one slab, as a 3D acquisition's do.
    limits
        Fewest and most slices.
    """

    centre: np.ndarray
    rotation: np.ndarray
    fov: tuple[float, float]
    thickness: float | None
    gap: float = 0.0
    slices: int = 1
    slab: bool = False
    limits: tuple[int, int] = (1, 1024)

    @property
    def normal(self) -> np.ndarray:
        return self.rotation[:, 2]

    @property
    def pitch(self) -> float:
        """Distance between the centres of adjacent slices or locations; zero without a thickness."""
        if self.thickness is None:
            return 0.0
        return self.thickness + (0.0 if self.slab else self.gap)

    @property
    def extent(self) -> float:
        """Half the stack's thickness along its normal, from the first slice's far face to the last's."""
        if self.thickness is None:
            return 0.0
        return 0.5 * ((self.slices - 1) * self.pitch + self.thickness)

    def slice_centres(self) -> np.ndarray:
        offsets = (np.arange(self.slices) - 0.5 * (self.slices - 1)) * self.pitch
        return self.centre + offsets[:, None] * self.normal

    def end(self, side: int) -> np.ndarray:
        """Return the centre of the first slice, ``side`` -1, or of the last, ``side`` +1."""
        return self.centre + side * 0.5 * (self.slices - 1) * self.pitch * self.normal

    def moved(self, delta: Sequence[float]) -> Prescription:
        return replace(self, centre=self.centre + np.asarray(delta, dtype=float))

    def turned(self, axis: Sequence[float], degrees: float) -> Prescription:
        """Return the prescription turned about ``axis`` through its centre."""
        return replace(self, rotation=turn(axis, degrees) @ self.rotation)

    def resized(self, read: float, phase: float) -> Prescription:
        return replace(self, fov=(float(read), float(phase)))

    def stretched(self, side: int, distance: float) -> Prescription:
        """Return the stack with slices added past its ``side`` end, or removed from it, the other end in place.

        ``distance`` is how far the end is dragged outward, in mm; a slice is
        added for every pitch, to the nearest, within the stack's limits.
        """
        if self.pitch <= 0.0:
            return self
        lo, hi = self.limits
        slices = int(min(hi, max(lo, self.slices + round(distance / self.pitch))))
        added = slices - self.slices
        return replace(
            self, slices=slices, centre=self.centre + side * 0.5 * added * self.pitch * self.normal
        )


@dataclass(frozen=True)
class Band:
    """A saturation band: a slab of any orientation.

    Attributes
    ----------
    normal
        Its unit normal along the physical axes.
    position
        Its centre's distance from the isocentre along the normal.
    thickness
        Its thickness along the normal.
    """

    normal: np.ndarray
    position: float
    thickness: float

    def moved(self, distance: float) -> Band:
        return replace(self, position=self.position + distance)

    def turned(self, axis: Sequence[float], degrees: float, about: Sequence[float]) -> Band:
        """Return the band turned about ``axis`` through the point ``about``, which stays on its centre plane."""
        normal = turn(axis, degrees) @ self.normal
        return replace(self, normal=normal, position=float(np.asarray(about, dtype=float) @ normal))


@dataclass(frozen=True)
class Section:
    """A prescription as a view shows it, in the view's fractional pixels.

    Attributes
    ----------
    facing
        Whether the view faces the slices, within :data:`FACING_DEG`.
    box
        The field of view of the slice nearest the view, projected onto it,
        its corners in order, on a view that faces the slices; else None.
    lines
        Each slice's trace on the view, its two ends, on a view across the
        slices; empty for a slab.
    outline
        The trace of the stack or slab as a box across the view, its corners
        in order; empty where the view misses it.
    ends
        Where the first and the last slice's handles go, or None where the
        view misses that end.
    centre
        The centre projected onto the view.
    """

    facing: bool
    box: np.ndarray | None
    lines: list[np.ndarray]
    outline: np.ndarray
    ends: tuple[np.ndarray | None, np.ndarray | None]
    centre: np.ndarray


def section(prescription: Prescription, plane: ImagePlane) -> Section:
    """Return how a view shows a prescription."""
    p = prescription
    read, phase, normal = p.rotation.T
    half = (0.5 * p.fov[0] * read, 0.5 * p.fov[1] * phase)
    facing = abs(float(normal @ plane.normal)) > math.cos(math.radians(FACING_DEG))
    centre = plane.pixel(p.centre)
    if facing:
        nearest = p.slice_centres()[np.argmin(np.abs((p.slice_centres() - plane.centre) @ plane.normal))]
        box = plane.pixel(_rectangle(nearest, *half))
        return Section(True, box, [], np.empty((0, 2)), (None, None), centre)
    lines = []
    if not p.slab:
        for middle in p.slice_centres():
            cut = _cut_polygon(_rectangle(middle, *half), plane)
            if len(cut) == 2:
                lines.append(plane.pixel(cut))
    depth = p.extent * normal
    outline = _cut_box(p.centre, (*half, depth), plane)
    ends = []
    for side in (-1, 1):
        face = p.end(side) + (side * 0.5 * p.thickness * normal if p.slab and p.thickness else 0.0)
        cut = _cut_polygon(_rectangle(face, *half), plane)
        ends.append(plane.pixel(cut.mean(axis=0)) if len(cut) == 2 else None)
    return Section(False, None, lines, plane.pixel(outline) if len(outline) else np.empty((0, 2)), tuple(ends), centre)


def band_area(band: Band, plane: ImagePlane) -> np.ndarray:
    """Return the part of a view a band covers, its corners in fractional pixels; empty where it covers none."""
    polygon = plane.corners()
    for sign in (-1.0, 1.0):
        # Keep the side of each face that holds the band.
        bound = band.position + sign * 0.5 * band.thickness
        polygon = _clip(polygon, -sign * band.normal, -sign * bound)
    return plane.pixel(polygon) if len(polygon) else np.empty((0, 2))


def _rectangle(centre: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    return np.array([centre - u - v, centre + u - v, centre + u + v, centre - u + v])


def _cut_polygon(corners: np.ndarray, plane: ImagePlane) -> np.ndarray:
    """Return where a planar polygon's edges cross a view's plane."""
    side = (corners - plane.origin) @ plane.normal
    points = []
    for k in range(len(corners)):
        a, b = side[k], side[(k + 1) % len(corners)]
        if (a < 0.0) != (b < 0.0):
            t = a / (a - b)
            points.append(corners[k] + t * (corners[(k + 1) % len(corners)] - corners[k]))
    return np.array(points) if points else np.empty((0, 3))


def _cut_box(centre: np.ndarray, halves: tuple[np.ndarray, ...], plane: ImagePlane) -> np.ndarray:
    """Return where a box's edges cross a view's plane, in order around the section."""
    u, v, w = halves
    signs = [(a, b, c) for a in (-1, 1) for b in (-1, 1) for c in (-1, 1)]
    vertices = {s: centre + s[0] * u + s[1] * v + s[2] * w for s in signs}
    side = {s: float((x - plane.origin) @ plane.normal) for s, x in vertices.items()}
    points = []
    for s in signs:
        for k in range(3):
            if s[k] < 0:
                t_ = list(s)
                t_[k] = 1
                t = tuple(t_)
                a, b = side[s], side[t]
                if (a < 0.0) != (b < 0.0):
                    f = a / (a - b)
                    points.append(vertices[s] + f * (vertices[t] - vertices[s]))
    if len(points) < 3:
        return np.empty((0, 3))
    points = np.array(points)
    middle = points.mean(axis=0)
    angle = np.arctan2((points - middle) @ plane.down, (points - middle) @ plane.across)
    return points[np.argsort(angle)]


def _clip(polygon: np.ndarray, normal: np.ndarray, offset: float) -> np.ndarray:
    """Return the part of a polygon where ``x . normal >= offset``."""
    if len(polygon) == 0:
        return polygon
    kept = []
    side = polygon @ normal - offset
    for k in range(len(polygon)):
        a, b = side[k], side[(k + 1) % len(polygon)]
        if a >= 0.0:
            kept.append(polygon[k])
        if (a >= 0.0) != (b >= 0.0):
            t = a / (a - b)
            kept.append(polygon[k] + t * (polygon[(k + 1) % len(polygon)] - polygon[k]))
    return np.array(kept) if kept else np.empty((0, 3))
