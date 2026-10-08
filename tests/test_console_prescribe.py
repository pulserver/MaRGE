"""A plugin's protocol entries as the console's prescription and saturation bands, and back."""

import numpy as np
import pytest

from marge.console import geometry, prescribe
from marge.seq.pulserver_console import parse_listing

LISTING = """PROTOCOL
[Protocol]
imaging_mode: stringlist|0|2d|3d
fov: float|typein|220.0|50.0|500.0|1.0|mm
phase_fov: float|typein|200.0|50.0|500.0|1.0|mm
nslices: int|typein|5|1|64|1|
slice_thickness: float|typein|4.0|1.0|20.0|0.5|mm
slice_spacing: float|typein|1.0|0.0|20.0|0.5|mm
exsat_mask: config|3
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


@pytest.fixture
def entries():
    return parse_listing(LISTING)


@pytest.fixture
def values(entries):
    stated = {name: entry["value"] for name, entry in entries.items()}
    return {**stated, "orientation": "axial", "centre": [0.0, 0.0, 0.0], "inplane": 0.0, "tilt_read": 0.0, "tilt_phase": 0.0}


def test_a_two_dimensional_plugin_prescribes_a_stack_of_its_slices_spaced_by_their_gap(entries, values):
    planned = prescribe.prescription(values, entries)

    assert not planned.slab
    assert planned.fov == (220.0, 200.0)
    assert (planned.slices, planned.thickness, planned.gap, planned.pitch) == (5, 4.0, 1.0, 5.0)
    assert planned.limits == (1, 64)


def test_a_three_dimensional_plugin_prescribes_one_slab_of_its_locations(entries, values):
    values["imaging_mode"] = "3d"
    entries["imaging_mode"]["value"] = "3d"

    planned = prescribe.prescription(values, entries)

    assert planned.slab and planned.pitch == 4.0 and planned.extent == 10.0


def test_a_plugin_that_states_no_imaging_mode_or_thickness_is_told_apart(entries):
    del entries["imaging_mode"], entries["slice_thickness"]

    assert prescribe.unstated(entries) == ["imaging_mode", "slice_thickness"]
    assert not prescribe.slab(entries)


def test_an_orientation_is_read_whatever_its_case(entries, values):
    values["orientation"] = " Coronal"

    assert np.allclose(prescribe.prescription(values, entries).rotation, geometry.BASES["coronal"])


def test_written_values_are_held_within_each_entrys_range_and_on_its_step(entries, values):
    planned = prescribe.prescription(values, entries).resized(600.0, 123.4)

    changed = prescribe.written(planned, values, entries)

    assert changed == {"fov": 500.0, "phase_fov": 123.0}


def test_a_stack_dragged_longer_writes_its_slices_and_its_new_centre(entries, values):
    planned = prescribe.prescription(values, entries)

    changed = prescribe.written(planned.stretched(1, 2 * planned.pitch), values, entries)

    assert changed == {"nslices": 7, "centre": [0.0, 0.0, 5.0]}


def test_a_turned_prescription_is_written_as_its_angles_from_the_starting_plane(entries, values):
    planned = prescribe.prescription(values, entries).turned((1, 0, 0), 12.0)

    changed = prescribe.written(planned, values, entries)

    assert changed == {"tilt_read": 12.0}


def test_every_band_the_sequence_declares_is_read_with_its_normal(entries, values):
    found = prescribe.bands(values, entries)

    assert [n for n, _ in found] == [1, 2]
    assert np.allclose(found[0][1].normal, (1, 0, 0)) and found[0][1].position == -60.0


def test_a_band_is_written_back_held_to_its_entries(entries, values):
    entries["exsat_mask"]["value"] = 2

    ((n, band),) = prescribe.bands(values, entries)
    turned = geometry.Band(np.array([0.0, 0.6, 0.8]), 72.6, 35.2)

    assert n == 2 and np.allclose(band.normal, (0, 1, 0)) and (band.position, band.thickness) == (60.0, 40.0)
    assert prescribe.band_written(n, turned, values, entries) == {
        "exsat2_normal_y": 0.6,
        "exsat2_normal_z": 0.8,
        "exsat2_loc": 73.0,
        "exsat2_thickness": 35.0,
    }


def test_a_band_turned_about_a_point_on_it_keeps_that_point_on_its_centre_plane():
    band = geometry.Band(np.array([0.0, 1.0, 0.0]), 20.0, 10.0)
    point = np.array([5.0, 20.0, -3.0])

    turned = band.turned((0, 0, 1), 30.0, point)

    assert turned.position == pytest.approx(point @ turned.normal)
    assert np.allclose(turned.normal, (-0.5, np.cos(np.radians(30.0)), 0.0))


def test_values_are_read_and_written_under_the_keys_a_sequence_holds_them_by(entries, values):
    renamed = {f"{k}_p" if k in entries else k: v for k, v in values.items()}

    planned = prescribe.prescription(renamed, entries, key=lambda name: f"{name}_p")
    changed = prescribe.written(planned.resized(210.0, 200.0), renamed, entries, key=lambda name: f"{name}_p")

    assert planned.fov == (220.0, 200.0)
    assert changed == {"fov_p": 210.0}
