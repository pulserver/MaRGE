"""The geometry of the console's graphical prescription, in the patient's LPS frame."""

import types

import numpy as np
import pytest

from marge.console import geometry as g


def _plane(across, down, centre=(0.0, 0.0, 0.0), size=128, spacing=2.0):
    across, down = np.asarray(across, float), np.asarray(down, float)
    origin = np.asarray(centre, float) - 0.5 * (size - 1) * spacing * (across + down)
    return g.ImagePlane(origin, across, down, (spacing, spacing), (size, size))


AXIAL = _plane((1, 0, 0), (0, 1, 0))
CORONAL = _plane((1, 0, 0), (0, 0, -1))
SAGITTAL = _plane((0, 1, 0), (0, 0, -1))


@pytest.mark.parametrize("base", list(g.BASES))
def test_each_starting_plane_is_a_rotation_whose_image_reads_as_the_localizers_plane(base):
    planes = {"axial": AXIAL, "coronal": CORONAL, "sagittal": SAGITTAL}
    matrix = g.BASES[base]

    assert np.allclose(matrix.T @ matrix, np.eye(3)) and np.linalg.det(matrix) == pytest.approx(1.0)
    assert np.allclose(matrix[:, 0], planes[base].across)
    assert np.allclose(matrix[:, 1], planes[base].down)


@pytest.mark.parametrize("base", list(g.BASES))
@pytest.mark.parametrize("turns", [(0, 0, 0), (30, 0, 0), (0, 20, 0), (0, 0, -15), (25, -10, 40)])
def test_the_angles_of_a_rotation_rebuild_it(base, turns):
    matrix = g.rotation(base, *turns)

    assert np.allclose(g.angles(matrix, base), turns, atol=1e-9)


def test_a_tilt_about_the_readout_turns_the_slices_about_the_readout_direction():
    matrix = g.rotation("axial", 0.0, 10.0, 0.0)

    assert np.allclose(matrix[:, 0], (1, 0, 0))
    assert matrix[:, 2] @ (0, 0, 1) == pytest.approx(np.cos(np.radians(10.0)))


def test_an_image_planes_pixels_map_to_points_and_back():
    plane = _plane((0.6, 0.8, 0.0), (0.0, 0.0, -1.0), centre=(5, -3, 12))
    pixels = np.array([[0.0, 0.0], [10.5, 3.25], [127.0, 127.0]])
    points = np.array([plane.point(*p) for p in pixels])

    assert np.allclose(plane.pixel(points), pixels)
    assert np.allclose(plane.centre, (5, -3, 12))


def test_a_planes_dicom_geometry_reads_with_its_row_and_column_spacings_apart():
    dataset = types.SimpleNamespace(
        ImageOrientationPatient=[1, 0, 0, 0, 1, 0],
        ImagePositionPatient=[-127, -100, 4],
        PixelSpacing=[3.0, 2.0],  # between rows, between columns
        Rows=64,
        Columns=128,
    )
    plane = g.ImagePlane.of(dataset)

    assert plane.spacing == (2.0, 3.0)
    assert np.allclose(plane.point(1, 1), (-125, -97, 4))


def _stack(**changes):
    values = dict(
        centre=np.zeros(3), rotation=g.BASES["axial"], fov=(200.0, 160.0), thickness=4.0, gap=1.0, slices=5
    )
    return g.Prescription(**{**values, **changes})


def test_an_axial_stack_shows_as_a_box_on_the_axial_view_and_as_lines_across_the_others():
    stack = _stack()

    on_axial = g.section(stack, AXIAL)
    on_coronal = g.section(stack, CORONAL)

    assert on_axial.facing and on_axial.lines == []
    assert np.ptp(on_axial.box[:, 0]) == pytest.approx(200.0 / 2.0)
    assert np.ptp(on_axial.box[:, 1]) == pytest.approx(160.0 / 2.0)
    assert not on_coronal.facing and len(on_coronal.lines) == 5
    rows = sorted(line[0, 1] for line in on_coronal.lines)
    assert np.allclose(np.diff(rows), 5.0 / 2.0)  # a pitch of 5 mm, 2 mm pixels
    assert all(np.ptp(line[:, 0]) == pytest.approx(100.0) for line in on_coronal.lines)


def test_the_end_handles_sit_on_the_first_and_last_slice_across_the_stack():
    stack = _stack(centre=np.array([0.0, 0.0, 10.0]))

    first, last = g.section(stack, CORONAL).ends

    assert np.allclose(CORONAL.point(*first), (0, 0, 0))
    assert np.allclose(CORONAL.point(*last), (0, 0, 20))


def test_a_stack_tilted_out_of_a_view_shows_as_lines_that_tilt_on_it():
    stack = _stack(rotation=g.rotation("axial", 0.0, 0.0, 20.0))

    lines = g.section(stack, CORONAL).lines
    slope = [np.diff(line[:, 1]) / np.diff(line[:, 0]) for line in lines]

    assert np.allclose(np.abs(slope), np.tan(np.radians(20.0)))


@pytest.mark.parametrize("side", [-1, 1])
def test_dragging_an_end_outward_adds_slices_past_it_and_keeps_the_other_end(side):
    stack = _stack()

    grown = stack.stretched(side, 2.2 * stack.pitch)

    assert grown.slices == 7
    assert np.allclose(grown.end(-side), stack.end(-side))
    assert np.allclose(grown.end(side), stack.end(side) + side * 2 * stack.pitch * stack.normal)


def test_dragging_an_end_inward_removes_slices_down_to_the_fewest():
    stack = _stack(limits=(2, 64))

    assert stack.stretched(1, -1.0 * stack.pitch).slices == 4
    assert stack.stretched(1, -10.0 * stack.pitch).slices == 2


def test_a_slab_spans_its_locations_without_gaps_and_shows_as_one_box():
    slab = _stack(slab=True, slices=32, thickness=1.0, gap=3.0)

    on_coronal = g.section(slab, CORONAL)

    assert slab.pitch == 1.0 and slab.extent == 16.0
    assert on_coronal.lines == []
    assert np.ptp(on_coronal.outline[:, 1]) == pytest.approx(32.0 / 2.0)
    assert np.allclose([CORONAL.point(*e)[2] for e in on_coronal.ends], (-16.0, 16.0))


def test_turning_a_prescription_turns_it_about_its_centre():
    stack = _stack(centre=np.array([5.0, 0.0, 0.0]))

    turned = stack.turned((0, 0, 1), 90.0)

    assert np.allclose(turned.centre, stack.centre)
    assert np.allclose(turned.rotation[:, 0], (0, 1, 0))


@pytest.mark.parametrize(("position", "covered"), [(0.0, 40.0), (120.0, 28.0), (400.0, 0.0)])
def test_a_band_covers_the_part_of_a_view_within_its_slab(position, covered):
    band = g.Band(axis=0, position=position, thickness=40.0)

    area = g.band_area(band, AXIAL)
    width = np.ptp(area[:, 0]) * 2.0 if len(area) else 0.0

    assert width == pytest.approx(covered)


def test_the_box_cut_of_a_stack_lies_on_the_view():
    stack = _stack(rotation=g.rotation("axial", 30.0, 15.0, -20.0), centre=np.array([3.0, -4.0, 2.0]))

    outline = g.section(stack, SAGITTAL).outline
    points = np.array([SAGITTAL.point(*p) for p in outline])

    assert len(outline) >= 4
    assert np.allclose((points - SAGITTAL.origin) @ SAGITTAL.normal, 0.0)
    assert all(abs((p - stack.centre) @ stack.normal) <= stack.extent + 1e-9 for p in points)


def test_a_stack_tilted_less_than_half_a_right_angle_still_faces_its_starting_view_as_a_projected_box():
    stack = _stack(rotation=g.rotation("axial", 0.0, 0.0, 30.0))

    on_axial = g.section(stack, AXIAL)

    assert on_axial.facing and on_axial.lines == []
    assert np.ptp(on_axial.box[:, 0]) == pytest.approx(200.0 * np.cos(np.radians(30.0)) / 2.0)
    assert not g.section(_stack(rotation=g.rotation("axial", 0.0, 0.0, 50.0)), AXIAL).facing
