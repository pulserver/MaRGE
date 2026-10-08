"""The console's workspace: the exam's series in the views, and the prescription dragged on them."""

import io
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PyQt5.QtWidgets")

from marge.console import geometry, prescribe  # noqa: E402
from marge.console.series import Series  # noqa: E402
from marge.console.workspace import Workspace  # noqa: E402
from marge.seq.pulserver_console import parse_listing  # noqa: E402

LISTING = """PROTOCOL
[Protocol]
imaging_mode: stringlist|0|2d|3d
fov: float|typein|200.0|50.0|500.0|1.0|mm
phase_fov: float|typein|200.0|50.0|500.0|1.0|mm
nslices: int|typein|5|1|64|1|
slice_thickness: float|typein|4.0|1.0|20.0|0.5|mm
slice_spacing: float|typein|1.0|0.0|20.0|0.5|mm
exsat_mask: int|typein|2|0|3|1|
exsat1_normal_x: float|typein|1.0|-1.0|1.0|0.001|
exsat1_normal_y: float|typein|0.0|-1.0|1.0|0.001|
exsat1_normal_z: float|typein|0.0|-1.0|1.0|0.001|
exsat1_loc: float|typein|-60.0|-500.0|500.0|1.0|mm
exsat1_thickness: float|typein|40.0|5.0|200.0|1.0|mm
exsat2_normal_x: float|typein|0.0|-1.0|1.0|0.001|
exsat2_normal_y: float|typein|1.0|-1.0|1.0|0.001|
exsat2_normal_z: float|typein|0.0|-1.0|1.0|0.001|
exsat2_loc: float|typein|60.0|-500.0|500.0|1.0|mm
exsat2_thickness: float|typein|40.0|5.0|200.0|1.0|mm
[Protocol End]
"""

#: The localizer's planes: their directions across and down.
PLANES = {"axial": ((1, 0, 0), (0, 1, 0)), "coronal": ((1, 0, 0), (0, 0, -1)), "sagittal": ((0, 1, 0), (0, 0, -1))}


@pytest.fixture(scope="module")
def application():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _dicom(across, down, number, size=64, spacing=4.0):
    import pydicom

    across, down = np.asarray(across, float), np.asarray(down, float)
    dataset = pydicom.Dataset()
    dataset.file_meta = pydicom.dataset.FileMetaDataset()
    dataset.file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
    dataset.file_meta.MediaStorageSOPClassUID = pydicom.uid.MRImageStorage
    dataset.file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
    dataset.SOPClassUID = pydicom.uid.MRImageStorage
    dataset.SOPInstanceUID = dataset.file_meta.MediaStorageSOPInstanceUID
    dataset.Rows = dataset.Columns = size
    dataset.SamplesPerPixel, dataset.PhotometricInterpretation = 1, "MONOCHROME2"
    dataset.BitsAllocated, dataset.BitsStored, dataset.HighBit, dataset.PixelRepresentation = 16, 16, 15, 0
    dataset.PixelData = (np.arange(size * size) % 4096).astype("<u2").tobytes()
    dataset.InstanceNumber = number
    dataset.PixelSpacing = [spacing, spacing]
    dataset.ImageOrientationPatient = [*across, *down]
    dataset.ImagePositionPatient = list(-0.5 * (size - 1) * spacing * (across + down))
    buffer = io.BytesIO()
    dataset.save_as(buffer, enforce_file_format=True)
    return buffer.getvalue()


class _Sequence:
    """A prescribed sequence as the console's plugin sequences are, over the listing above."""

    def __init__(self):
        self.entries = parse_listing(LISTING)
        self.mapVals = {name: e["value"] for name, e in self.entries.items()}
        self.mapVals.update(orientation="axial", centre=[0.0, 0.0, 0.0], inplane=0.0, tilt_read=0.0, tilt_phase=0.0)
        self.shown = []

    def planned(self):
        return prescribe.prescription(self.mapVals, self.entries)

    def plan(self, planned):
        changed = prescribe.written(planned, self.mapVals, self.entries)
        self.mapVals.update(changed)
        return changed

    def planned_bands(self):
        return prescribe.bands(self.mapVals, self.entries)

    def plan_band(self, n, band):
        changed = prescribe.band_written(n, band, self.mapVals, self.entries)
        self.mapVals.update(changed)
        return changed


@pytest.fixture
def workspace(application):
    sequence = _Sequence()
    space = Workspace(lambda: sequence, lambda s, keys: sequence.shown.extend(keys))
    files = [_dicom(*PLANES[name], number) for number, name in enumerate(PLANES, 1)]
    space.add(Series.of(1, "Localizer", "Localizer.0001.mat", files))
    space.sequence = sequence
    return space


def _points(item):
    """Return how many points a view's curve draws."""
    x, _ = item.getData()
    return 0 if x is None else len(x)


def _drag(view, role, to_point):
    """Drag a view's handle to a point of its plane, as the mouse would."""
    handle = view.handles[role]
    view.began(handle)
    xy = view.plane.pixel(np.asarray(to_point, float)) + 0.5
    handle.moving = True
    handle.setPos(float(xy[0]), float(xy[1]))
    handle.moving = False
    view.ended(handle)


def test_a_localizer_fills_the_three_views_with_its_planes_and_the_viewer_shows_it(workspace):
    titles = [view.series.label for view in workspace.views]

    assert titles == ["S1 Localizer"] * 3
    assert [view.index for view in workspace.views] == [0, 1, 2]
    assert workspace.viewer.series.label == "S1 Localizer"


def test_an_axial_stack_is_a_box_on_the_axial_view_and_lines_on_the_others(workspace):
    axial, coronal, sagittal = workspace.views

    assert _points(axial.box) == 5 and _points(axial.traces) == 0
    assert _points(coronal.traces) == 2 * 5
    assert coronal.handles[-1].isVisible() and coronal.handles[1].isVisible()
    assert not axial.handles[1].isVisible()


def test_dragging_the_centre_on_the_coronal_view_moves_the_stack_in_its_plane(workspace):
    coronal = workspace.views[1]

    _drag(coronal, "centre", (10.0, 0.0, 20.0))

    assert workspace.sequence.mapVals["centre"] == [10.0, 0.0, 20.0]
    assert "centre" in workspace.sequence.shown


def test_dragging_the_last_slices_diamond_adds_slices_past_it(workspace):
    coronal = workspace.views[1]
    planned = workspace.sequence.planned()

    _drag(coronal, 1, planned.end(1) + 2 * planned.pitch * planned.normal)

    assert workspace.sequence.mapVals["nslices"] == 7
    assert np.allclose(workspace.sequence.planned().end(-1), planned.end(-1))


def test_turning_on_the_sagittal_view_tilts_the_stack_about_the_readout(workspace):
    sagittal = workspace.views[2]
    planned = workspace.sequence.planned()
    lever = sagittal._lever(planned, sagittal.plane)
    pivot = sagittal.plane.point(*sagittal.plane.pixel(planned.centre))

    _drag(sagittal, "turn", pivot + geometry.turn(sagittal.plane.normal, 10.0) @ (lever - pivot))

    assert abs(workspace.sequence.mapVals["tilt_read"]) == pytest.approx(10.0, abs=0.01)
    assert workspace.sequence.mapVals["tilt_phase"] == pytest.approx(0.0, abs=0.01)


def test_a_corner_of_the_box_on_the_axial_view_resizes_the_field_of_view(workspace):
    _drag(workspace.views[0], ("corner", 2), (120.0, 90.0, 0.0))

    assert (workspace.sequence.mapVals["fov"], workspace.sequence.mapVals["phase_fov"]) == (240.0, 180.0)


def test_a_band_is_shaded_on_the_views_across_it_and_dragged_along_its_normal(workspace):
    axial = workspace.views[0]

    assert axial.areas
    _drag(axial, ("band", 2), (0.0, 30.0, 0.0))

    assert workspace.sequence.mapVals["exsat2_loc"] == 30.0


def test_a_band_turned_on_a_view_turns_about_the_views_normal(workspace):
    axial = workspace.views[0]
    ((_, band),) = workspace.bands()
    lever = axial.plane.point(*(np.array(axial.handles[("tilt", 2)].pos()) - 0.5))
    centre = axial.plane.point(*(np.array(axial.handles[("band", 2)].pos()) - 0.5))

    _drag(axial, ("tilt", 2), centre + geometry.turn(axial.plane.normal, 30.0) @ (lever - centre))

    values = workspace.sequence.mapVals
    normal = np.array([values[f"exsat2_normal_{a}"] for a in "xyz"])
    assert normal @ (0.0, 0.0, 1.0) == pytest.approx(0.0, abs=1e-3)
    assert abs(normal @ band.normal) == pytest.approx(np.cos(np.radians(30.0)), abs=2e-3)


def test_a_value_typed_redraws_the_views(workspace):
    workspace.sequence.mapVals["nslices"] = 9

    workspace.redraw()

    assert _points(workspace.views[1].traces) == 2 * 9
