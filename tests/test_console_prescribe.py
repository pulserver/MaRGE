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
sat_x: int|typein|0|0|3|1|
sat_x_loc1: float|typein|-60.0|-500.0|500.0|1.0|mm
sat_x_loc2: float|typein|60.0|-500.0|500.0|1.0|mm
sat_x_thickness: float|typein|40.0|5.0|200.0|1.0|mm
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


def test_a_band_turned_on_is_read_at_its_location_and_written_back_held(entries, values):
    values["sat_x"] = 2

    ((axis, location, band),) = prescribe.bands(values, entries)
    moved = geometry.Band(band.axis, 72.6, 35.2)

    assert (axis, location, band) == ("x", "loc2", geometry.Band(0, 60.0, 40.0))
    assert prescribe.band_written(axis, location, moved, values, entries) == {
        "sat_x_loc2": 73.0,
        "sat_x_thickness": 35.0,
    }


def test_values_are_read_and_written_under_the_keys_a_sequence_holds_them_by(entries, values):
    renamed = {f"{k}_p" if k in entries else k: v for k, v in values.items()}

    planned = prescribe.prescription(renamed, entries, key=lambda name: f"{name}_p")
    changed = prescribe.written(planned.resized(210.0, 200.0), renamed, entries, key=lambda name: f"{name}_p")

    assert planned.fov == (220.0, 200.0)
    assert changed == {"fov_p": 210.0}
