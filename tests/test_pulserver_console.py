"""MaRGE as the console of pulserver's virtual scanner: the interpreter's text blocks, the prescription and the gateway."""

import base64
import json

import numpy as np
import pytest

from marge.seq import pulserver_console as console

#: pulserver's listing of its gre2d test plugin, with a boolean, a string list
#: and a description added.
LISTING = """PROTOCOL
[NimPulseqGUI Protocol]
TE: int|dropdown|8000|1000|80000|10|us|-2|5000|8000
bandwidth: float|typein|250000.0|1000.0|1000000.0|1.0|Hz
nx: int|typein|128|32|512|2|
fatsat: bool|false
readout: stringlist|1|cartesian|radial
note: description|two\\nlines
fov_offset_x: float|off|0.0|-1000.0|1000.0|0.1|mm
fov_rotation_11: float|off|1.0|-1.0|1.0|1e-06|
[NimPulseqGUI Protocol End]
"""


def test_a_listing_reads_as_the_interpreter_reads_it():
    entries = console.parse_listing(LISTING)

    assert entries["TE"] == {
        "kind": "int",
        "mode": "dropdown",
        "value": 8000,
        "min": 1000,
        "max": 80000,
        "step": 10,
        "unit": "us",
        "options": [-2, 5000, 8000],
    }
    assert entries["bandwidth"]["value"] == 250000.0
    assert entries["fatsat"] == {"kind": "bool", "value": False}
    assert entries["readout"] == {
        "kind": "stringlist",
        "value": "radial",
        "options": ["cartesian", "radial"],
    }
    assert entries["note"]["value"] == "two\nlines"


def test_the_console_shows_the_editable_entries_outside_the_prescription():
    entries = console.parse_listing(LISTING)

    assert console.shown(entries) == ["TE", "bandwidth", "nx", "fatsat", "readout"]


def test_a_value_block_carries_the_values_then_the_prescription_as_the_interpreter_sends_them():
    entries = console.parse_listing(LISTING)
    rotation = console.ORIENTATIONS["coronal"]

    block = console.format_values(
        {"TE": 5000.0, "bandwidth": 1e5, "fatsat": True, "readout": "cartesian"},
        entries,
        rotation,
        (1.5, -2.0, 3.0),
    )

    lines = block.splitlines()
    assert lines[0] == console.PROTOCOL_BEGIN
    assert lines[-1] == console.PROTOCOL_END
    assert lines[1:5] == ["TE: 5000", "bandwidth: 100000.0", "fatsat: true", "readout: 0"]
    prescribed = dict(line.split(": ") for line in lines[5:-1])
    assert list(prescribed) == list(console.PRESCRIPTION)
    np.testing.assert_allclose(
        [float(prescribed[n]) for n in console.FOV_OFFSET], [1.5, -2.0, 3.0]
    )
    np.testing.assert_allclose(
        [float(prescribed[n]) for n in console.FOV_ROTATION], rotation.ravel()
    )


@pytest.mark.parametrize("orientation", list(console.ORIENTATIONS))
def test_an_unturned_prescription_is_the_orientation_and_its_centre_along_the_logical_axes(
    orientation,
):
    rotation, offset = console.prescription(orientation, 0.0, (0, 0, 1), (10.0, -5.0, 2.0))

    np.testing.assert_allclose(rotation, console.ORIENTATIONS[orientation])
    np.testing.assert_allclose(rotation @ offset, [10.0, -5.0, 2.0])


def test_a_prescription_turns_the_orientation_right_handed_about_the_axis():
    rotation, _ = console.prescription("axial", 90.0, (0, 0, 2), (0.0, 0.0, 0.0))

    np.testing.assert_allclose(rotation @ [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], atol=1e-12)
    np.testing.assert_allclose(rotation.T @ rotation, np.eye(3), atol=1e-12)


def test_the_subjects_name_else_its_id_names_the_phantom():
    assert console.subject_phantom({"subject_name": " brainweb ", "subject_id": "7"}) == "brainweb"
    assert console.subject_phantom({"subject_name": "", "subject_id": "vials"}) == "vials"
    assert console.subject_phantom({}) == ""


class _Socket:
    """A console connection that answers from a script of replies."""

    def __init__(self, replies):
        self.sent, self._replies = [], list(replies)

    def send(self, text):
        self.sent.append(json.loads(text))

    def recv(self):
        return json.dumps(self._replies.pop(0))


def test_a_scan_request_passes_its_clock_and_images_on_and_returns_when_done():
    socket = _Socket(
        [
            {"id": 0, "clock": 9.0, "duration": 9.0},
            {"id": 1, "clock": 0.5, "duration": 1.0},
            {"id": 1, "dicom": "AA==", "name": "0001.dcm"},
            {"id": 1, "done": 0},
        ]
    )
    gateway = console.Gateway("ws://console", connect=lambda url: socket)
    seen = []

    done = gateway.request("scan", on_message=seen.append, design="d1")

    assert done == {"id": 1, "done": 0}
    assert seen == [
        {"id": 1, "clock": 0.5, "duration": 1.0},
        {"id": 1, "dicom": "AA==", "name": "0001.dcm"},
    ]
    assert socket.sent == [{"id": 1, "call": "scan", "design": "d1"}]


def test_an_error_reply_is_raised():
    socket = _Socket([{"id": 1, "error": "ValueError: no such design"}])
    gateway = console.Gateway("ws://console", connect=lambda url: socket)

    with pytest.raises(RuntimeError, match="no such design"):
        gateway.request("scan", design="missing")


def test_an_exam_returns_the_localizer_files_the_console_sends():
    files = [b"one", b"two", b"three"]
    socket = _Socket([{"id": 1, "localizer": [base64.b64encode(f).decode() for f in files]}])
    gateway = console.Gateway("ws://console", connect=lambda url: socket)

    assert gateway.localizer("vials") == files
    assert socket.sent == [{"id": 1, "call": "exam", "subject": "vials"}]


def test_without_a_gateway_address_marge_is_not_a_console(monkeypatch):
    monkeypatch.setattr(console, "GATEWAY", "")

    assert not console.console_mode()
