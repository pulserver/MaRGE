"""The console's workspace: three views to prescribe on, above a viewer of the exam's series.

Each view shows an image of a series it chooses, a slice at a time. The
prescribing views draw the selected sequence's prescription where it lies on
their image, with handles that move, turn, resize and stretch it and move its
saturation bands; every drag writes the sequence's values, and every value
typed redraws the views.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtCore, QtWidgets

from . import geometry
from .series import Series

#: Colours of what a view draws.
STACK, SLAB, HANDLE, TURN, END, BAND = "#ffd23f", "#ffd23f", "#3ddc97", "#ff6ad5", "#4cc9f0", "#ff595e"

#: How far past the field of view the turning handle sits, as a fraction of it.
LEVER = 0.15


class Handle(pg.TargetItem):
    """A handle a view's prescription is dragged by, which tells its view when a drag starts and ends."""

    def __init__(self, view: PlanView, role: Any, symbol: str, colour: str, size: int = 12) -> None:
        super().__init__(
            size=size,
            symbol=symbol,
            pen=pg.mkPen(colour, width=2),
            brush=pg.mkBrush(colour),
            hoverPen=pg.mkPen("w", width=2),
            hoverBrush=pg.mkBrush("w"),
            movable=True,
        )
        self.view, self.role = view, role
        self.sigPositionChanged.connect(lambda handle: handle.moving and view.dragged(handle))

    def mouseDragEvent(self, ev) -> None:  # noqa: N802 -- Qt's API
        if ev.isStart() and ev.button() == QtCore.Qt.MouseButton.LeftButton:
            self.view.began(self)
        super().mouseDragEvent(ev)
        if ev.isFinish():
            self.view.ended(self)

    def place(self, xy: np.ndarray | None) -> None:
        """Put the handle at a view's ``(x, y)``, or hide it; a handle being dragged stays where it is dragged."""
        if xy is None:
            self.hide()
            return
        self.show()
        if not self.moving:
            self.setPos(float(xy[0]), float(xy[1]))


class SeriesView(QtWidgets.QWidget):
    """An image of one of the exam's series, a slice at a time."""

    def __init__(self, workspace: Workspace, title: str) -> None:
        super().__init__()
        self.workspace = workspace
        self.series: Series | None = None
        self.index = 0
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        header = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel(title)
        self.choice = QtWidgets.QComboBox()
        self.choice.currentIndexChanged.connect(self._chosen)
        header.addWidget(self.title)
        header.addWidget(self.choice, 1)
        layout.addLayout(header)
        self.graphics = pg.GraphicsLayoutWidget()
        self.plot = self.graphics.addPlot()
        self.plot.setAspectLocked(True)
        self.plot.invertY(True)
        self.plot.hideAxis("left")
        self.plot.hideAxis("bottom")
        self.plot.setMenuEnabled(False)
        self.image = pg.ImageItem(axisOrder="row-major")
        self.plot.addItem(self.image)
        layout.addWidget(self.graphics, 1)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider.valueChanged.connect(self._slid)
        self.where = QtWidgets.QLabel("")
        footer = QtWidgets.QHBoxLayout()
        footer.addWidget(self.slider, 1)
        footer.addWidget(self.where)
        layout.addLayout(footer)

    @property
    def plane(self) -> geometry.ImagePlane | None:
        return None if self.series is None else self.series.planes[self.index]

    def offer(self, series: list[Series]) -> None:
        """List the exam's series to choose from, keeping the one shown."""
        self.choice.blockSignals(True)
        self.choice.clear()
        self.choice.addItems([s.label for s in series])
        if self.series is not None:
            self.choice.setCurrentIndex([s.name for s in series].index(self.series.name))
        self.choice.blockSignals(False)

    def show_series(self, series: Series, index: int | None = None) -> None:
        """Show an image of ``series``, the middle one unless ``index`` names another."""
        fresh = self.series is None or series.name != self.series.name
        self.series = series
        self.index = len(series.images) // 2 if index is None else index
        names = [s.name for s in self.workspace.series]
        if series.name in names:
            self.choice.blockSignals(True)
            self.choice.setCurrentIndex(names.index(series.name))
            self.choice.blockSignals(False)
        self.slider.blockSignals(True)
        self.slider.setRange(0, len(series.images) - 1)
        self.slider.setValue(self.index)
        self.slider.blockSignals(False)
        self._draw(fresh)

    def _chosen(self, index: int) -> None:
        if 0 <= index < len(self.workspace.series):
            self.show_series(self.workspace.series[index])

    def _slid(self, index: int) -> None:
        if self.series is not None:
            self.index = index
            self._draw(False)

    def _draw(self, fresh: bool) -> None:
        image = self.series.images[self.index]
        self.image.setImage(image, autoLevels=True)
        self.where.setText(f"{self.index + 1}/{len(self.series.images)}")
        if fresh:
            self.plot.autoRange()
        self.drawn()

    def drawn(self) -> None:
        """Called once a new image is drawn."""


class PlanView(SeriesView):
    """A view the selected sequence is prescribed on.

    A view that faces the slices shows the field of view as a box, with a
    handle at each corner that resizes it; one across them shows each slice
    as a line, or a slab as a box, with a diamond on the first and on the
    last slice that drags slices onto that end or off it. Every view has a
    handle at the centre that moves the prescription in the view's plane, and
    one that turns it about the view's normal. Saturation bands show as
    shaded areas, each with a handle that moves it along its axis and one
    that sets its thickness.
    """

    def __init__(self, workspace: Workspace, title: str) -> None:
        super().__init__(workspace, title)
        self.traces = pg.PlotDataItem(pen=pg.mkPen(STACK, width=1.5), connect="pairs")
        self.box = pg.PlotDataItem(pen=pg.mkPen(STACK, width=2))
        self.outline = pg.PlotDataItem(pen=pg.mkPen(SLAB, width=1.5, style=QtCore.Qt.PenStyle.DashLine))
        self.areas: list[QtWidgets.QGraphicsPolygonItem] = []
        for item in (self.traces, self.box, self.outline):
            self.plot.addItem(item)
        self.handles: dict[Any, Handle] = {}
        for role, symbol, colour in (("centre", "o", HANDLE), ("turn", "o", TURN), (-1, "d", END), (1, "d", END)):
            self._handle(role, symbol, colour)
        for corner in range(4):
            self._handle(("corner", corner), "s", STACK, 9)
        self._start: tuple[Any, ...] | None = None

    def _handle(self, role: Any, symbol: str, colour: str, size: int = 12) -> Handle:
        handle = Handle(self, role, symbol, colour, size)
        handle.setZValue(10)
        self.plot.addItem(handle)
        handle.hide()
        self.handles[role] = handle
        return handle

    def drawn(self) -> None:
        self.redraw()

    # Drawing ---------------------------------------------------------------

    def redraw(self) -> None:
        """Draw the selected sequence's prescription on the image shown, or nothing without one."""
        plane, planned = self.plane, self.workspace.planned()
        for area in self.areas:
            self.plot.removeItem(area)
        self.areas = []
        if plane is None or planned is None:
            for item in (self.traces, self.box, self.outline):
                item.setData([], [])
            for handle in self.handles.values():
                handle.hide()
            return
        cut = geometry.section(planned, plane)
        self.box.setData(*_closed(cut.box) if cut.box is not None else ([], []))
        lines = np.concatenate(cut.lines) if cut.lines else np.empty((0, 2))
        self.traces.setData(lines[:, 0] + 0.5, lines[:, 1] + 0.5)
        self.outline.setData(*_closed(cut.outline) if len(cut.outline) else ([], []))
        self.handles["centre"].place(cut.centre + 0.5)
        self.handles["turn"].place(plane.pixel(self._lever(planned, plane)) + 0.5)
        for side, end in zip((-1, 1), cut.ends, strict=True):
            self.handles[side].place(None if end is None else end + 0.5)
        for corner in range(4):
            self.handles[("corner", corner)].place(None if cut.box is None else cut.box[corner] + 0.5)
        self._draw_bands(plane)

    def _draw_bands(self, plane: geometry.ImagePlane) -> None:
        shown = set()
        for axis, location, band in self.workspace.bands():
            area = geometry.band_area(band, plane)
            if not len(area):
                continue
            polygon = QtWidgets.QGraphicsPolygonItem(
                _polygon(area + 0.5)
            )
            colour = pg.mkColor(BAND)
            colour.setAlpha(70)
            polygon.setBrush(pg.mkBrush(colour))
            polygon.setPen(pg.mkPen(BAND, width=1))
            self.plot.addItem(polygon)
            self.areas.append(polygon)
            middle = plane.pixel(_on_view(plane, band, 0.0))
            edge = plane.pixel(_on_view(plane, band, 0.5 * band.thickness))
            for role, at, symbol in (("band", middle, "t"), ("width", edge, "s")):
                key = (role, axis, location)
                handle = self.handles.get(key) or self._handle(key, symbol, BAND, 10)
                handle.place(at + 0.5)
                shown.add(key)
        for key, handle in self.handles.items():
            if isinstance(key, tuple) and key[0] in ("band", "width") and key not in shown:
                handle.hide()

    @staticmethod
    def _lever(planned: geometry.Prescription, plane: geometry.ImagePlane) -> np.ndarray:
        """Return where the turning handle goes: past the field of view, along the view, from the centre."""
        read, phase, normal = planned.rotation.T
        along = np.cross(normal, plane.normal)
        if np.linalg.norm(along) < 1e-6:
            along = -phase
        along = along / np.linalg.norm(along)
        reach = 0.5 * (abs(along @ read) * planned.fov[0] + abs(along @ phase) * planned.fov[1])
        centre = plane.point(*plane.pixel(planned.centre))
        return centre - (1.0 + LEVER) * reach * (along if along @ plane.down < 0 else -along)

    # Dragging --------------------------------------------------------------

    def _point(self, handle: Handle) -> np.ndarray:
        position = handle.pos()
        return self.plane.point(position.x() - 0.5, position.y() - 0.5)

    def began(self, handle: Handle) -> None:
        self._start = (self.workspace.planned(), dict(self._bands()), self._point(handle))

    def ended(self, handle: Handle) -> None:
        self._start = None
        self.workspace.redraw()

    def dragged(self, handle: Handle) -> None:
        if self._start is None or self.plane is None:
            return
        planned, bands, start = self._start
        point, plane = self._point(handle), self.plane
        role = handle.role
        if isinstance(role, tuple) and role[0] in ("band", "width"):
            _, axis, location = role
            band = bands[(axis, location)]
            if role[0] == "band":
                band = geometry.Band(band.axis, band.position + (point - start) @ band.normal, band.thickness)
            else:
                band = geometry.Band(band.axis, band.position, 2.0 * abs(point @ band.normal - band.position))
            self.workspace.place_band(axis, location, band)
            return
        if planned is None:
            return
        if role == "centre":
            planned = planned.moved(point - start)
        elif role == "turn":
            pivot = plane.point(*plane.pixel(planned.centre))
            a, b = start - pivot, point - pivot
            degrees = math.degrees(math.atan2(plane.normal @ np.cross(a, b), a @ b))
            planned = planned.turned(plane.normal, degrees)
        elif role in (-1, 1):
            planned = planned.stretched(role, role * (point - start) @ planned.normal)
        elif isinstance(role, tuple) and role[0] == "corner":
            offset = point - planned.centre
            read, phase, _ = planned.rotation.T
            planned = planned.resized(2.0 * abs(offset @ read), 2.0 * abs(offset @ phase))
        self.workspace.prescribe(planned)

    def _bands(self) -> list[tuple[tuple[str, str], geometry.Band]]:
        return [((axis, location), band) for axis, location, band in self.workspace.bands()]


class Workspace(QtWidgets.QWidget):
    """Three views to prescribe on and a viewer, over the exam's series.

    Parameters
    ----------
    sequence
        Returns the sequence selected in MaRGE, or None where it is not one
        that is prescribed: one with ``planned``, ``plan``, ``planned_bands``
        and ``plan_band``.
    shown
        Shows a sequence's values that the views have changed, by their keys.
    """

    def __init__(self, sequence: Callable[[], Any], shown: Callable[[Any, list[str]], None]) -> None:
        super().__init__()
        self._sequence, self._shown = sequence, shown
        self.series: list[Series] = []
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QtWidgets.QHBoxLayout()
        self.views = [PlanView(self, f"Rx {k + 1}") for k in range(3)]
        for view in self.views:
            row.addWidget(view, 1)
        layout.addLayout(row, 1)
        self.viewer = SeriesView(self, "Viewer")
        layout.addWidget(self.viewer, 1)

    def add(self, series: Series) -> None:
        """Add a run's series to the exam and show it in the viewer; a localizer's planes also go to the views that show nothing yet."""
        self.series.append(series)
        for view in (*self.views, self.viewer):
            view.offer(self.series)
        if series.description == "Localizer":
            for index, view in enumerate(self.views[: len(series.images)]):
                view.show_series(series, index)
        self.viewer.show_series(series)

    def show_run(self, name: str) -> None:
        """Show the series of the run MaRGE names ``name`` in the viewer."""
        for series in self.series:
            if series.name == name:
                self.viewer.show_series(series)
                return

    def planned(self) -> geometry.Prescription | None:
        sequence = self._sequence()
        return None if sequence is None else sequence.planned()

    def bands(self) -> list[tuple[str, str, geometry.Band]]:
        sequence = self._sequence()
        return [] if sequence is None else sequence.planned_bands()

    def prescribe(self, planned: geometry.Prescription) -> None:
        sequence = self._sequence()
        if sequence is not None:
            self._shown(sequence, list(sequence.plan(planned)))
            self.redraw()

    def place_band(self, axis: str, location: str, band: geometry.Band) -> None:
        sequence = self._sequence()
        if sequence is not None:
            self._shown(sequence, list(sequence.plan_band(axis, location, band)))
            self.redraw()

    def redraw(self) -> None:
        for view in self.views:
            view.redraw()


def _closed(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ring = np.vstack([points, points[:1]]) + 0.5
    return ring[:, 0], ring[:, 1]


def _polygon(points: np.ndarray):
    from PyQt5 import QtGui

    return QtGui.QPolygonF([QtCore.QPointF(float(x), float(y)) for x, y in points])


def _on_view(plane: geometry.ImagePlane, band: geometry.Band, offset: float) -> np.ndarray:
    """Return the point of a view on a band's centre plane shifted by ``offset`` along its normal, nearest the view's centre."""
    centre = plane.centre
    target = band.position + offset
    # Move from the view's centre within the view's plane until on the target plane.
    along = band.normal - (band.normal @ plane.normal) * plane.normal
    if np.linalg.norm(along) < 1e-9:
        return centre
    return centre + (target - centre @ band.normal) / (along @ band.normal) * along
