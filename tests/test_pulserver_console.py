"""MaRGE as the console of pulserver's virtual scanner: the interpreter's text blocks, the prescription and the gateway."""

import asyncio
import base64
import io
import json
import os

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
fov: float|typein|250.0|50.0|500.0|0.1|mm
phase_fov: float|typein|200.0|50.0|500.0|0.1|mm
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

    assert console.shown(entries) == [
        "TE", "bandwidth", "nx", "fov", "phase_fov", "fatsat", "readout"
    ]


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


class _AsyncSocket:
    """An asynchronous console connection that answers from a script of replies."""

    def __init__(self, replies):
        self.sent, self._replies = [], list(replies)

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def recv(self):
        return json.dumps(self._replies.pop(0))


class _Scripted:
    """A synchronous gateway that answers each call from a table."""

    asynchronous = False

    def __init__(self, answers):
        self.calls, self._answers = [], answers

    def request(self, call, on_message=None, **fields):
        self.calls.append((call, fields))
        answer = self._answers[call]
        for message in answer.get("messages", []):
            on_message(message)
        return answer["reply"]


class _AsyncScripted(_Scripted):
    asynchronous = True

    async def request(self, call, on_message=None, **fields):
        return _Scripted.request(self, call, on_message, **fields)


class _Base:
    """The part of MaRGE's sequence base the console's sequences use."""

    def __init__(self):
        self.mapVals, self.deleted, self.saved_with = {}, 0, None

    def addParameter(self, key="", string="", val=0, units=True, field="", tip=None):  # noqa: N802
        self.mapVals[key] = val

    def deleteOutput(self):  # noqa: N802
        self.deleted += 1

    def saveRawData(self):  # noqa: N802
        """Name the run and make its folders, as MaRGE does, noting the output its DICOM writer sees."""
        directory = self.session["directory"]
        for folder in ("mat", "dcm"):
            os.makedirs(os.path.join(directory, folder), exist_ok=True)
        self.directory_mat = os.path.join(directory, "mat")
        self.file_name = f"{self.mapVals['seqName']}.2026.09.27"
        self.mapVals["fileName"] = f"{self.file_name}.mat"
        self.saved_with = list(self.output)


class _Toolbar:
    def __init__(self):
        self.started = []

    def startAcquisition(self, seq_name=None):  # noqa: N802
        self.started.append(seq_name)


def test_an_asynchronous_scan_request_passes_its_clock_on_and_returns_when_done():
    socket = _AsyncSocket(
        [{"id": 1, "clock": 0.5, "duration": 1.0}, {"id": 1, "done": 0}]
    )
    gateway = console.AsyncGateway(socket)
    seen = []

    done = asyncio.run(gateway.request("scan", on_message=seen.append, design="d1"))

    assert done == {"id": 1, "done": 0}
    assert seen == [{"id": 1, "clock": 0.5, "duration": 1.0}]


def test_the_listings_are_each_plugins_entries():
    gateway = _Scripted(
        {
            "plugins": {"reply": {"plugins": ["gre2d", "broken"]}},
            "list": {"reply": {"status": 0, "reply": LISTING}},
        }
    )

    found = console.listings(gateway)

    assert list(found) == ["gre2d", "broken"]
    assert found["gre2d"]["TE"]["value"] == 8000


def _scan_answers(status=0):
    return {
        "generate": {"reply": {"status": 0, "reply": "GENERATED d1\n", "design": "d1"}},
        "scan": {
            "messages": [{"clock": 1.0, "duration": 2.0}],
            "reply": {"done": status},
        },
    }


def test_a_plugin_sequence_generates_and_scans_through_a_blocking_gateway():
    gateway = _Scripted(_scan_answers())
    entries = console.parse_listing(LISTING)
    sequence = console._plugin_class(_Base, gateway, "gre2d", entries)()

    assert sequence.sequenceRun() is True

    (generate, fields), (scan, scanned) = gateway.calls
    assert (generate, scan) == ("generate", "scan")
    assert "TE: 8000" in fields["block"]
    assert scanned["design"] == "d1"
    assert sequence.clock == (1.0, 2.0)


def test_through_an_asynchronous_gateway_a_scan_runs_in_the_background_then_shows_through_the_toolbar(
    monkeypatch,
):
    toolbar = _Toolbar()
    monkeypatch.setattr(console, "TOOLBAR", toolbar)
    gateway = _AsyncScripted(_scan_answers())
    entries = console.parse_listing(LISTING)
    sequence = console._plugin_class(_Base, gateway, "gre2d", entries)()

    async def run():
        started = sequence.sequenceRun()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return started

    assert asyncio.run(run()) is False
    assert toolbar.started == ["gre2d"]
    assert sequence.deleted == 1
    assert sequence.sequenceRun() is True
    assert [call for call, _ in gateway.calls] == ["generate", "scan"]


def test_a_failed_background_scan_shows_nothing(monkeypatch, capsys):
    toolbar = _Toolbar()
    monkeypatch.setattr(console, "TOOLBAR", toolbar)
    answers = _scan_answers()
    answers["generate"]["reply"] = {"status": 1, "reply": "ERROR too strong\n"}
    sequence = console._plugin_class(
        _Base, _AsyncScripted(answers), "gre2d", console.parse_listing(LISTING)
    )()

    async def run():
        sequence.sequenceRun()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(run())

    assert toolbar.started == ["gre2d"]
    assert sequence.sequenceRun() is False
    assert "ERROR too strong" in capsys.readouterr().out


def _dicom(pixels, description):
    import pydicom

    dataset = pydicom.Dataset()
    dataset.file_meta = pydicom.dataset.FileMetaDataset()
    dataset.file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
    dataset.file_meta.MediaStorageSOPClassUID = pydicom.uid.MRImageStorage
    dataset.file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
    dataset.SOPClassUID = pydicom.uid.MRImageStorage
    dataset.SOPInstanceUID = dataset.file_meta.MediaStorageSOPInstanceUID
    dataset.Rows, dataset.Columns = pixels.shape
    dataset.SamplesPerPixel, dataset.PhotometricInterpretation = 1, "MONOCHROME2"
    dataset.BitsAllocated, dataset.BitsStored, dataset.HighBit = 16, 16, 15
    dataset.PixelRepresentation, dataset.RescaleSlope, dataset.RescaleIntercept = 0, 2, 0
    dataset.PixelData = pixels.astype("<u2").tobytes()
    dataset.SeriesDescription = description
    buffer = io.BytesIO()
    dataset.save_as(buffer, enforce_file_format=True)
    return buffer.getvalue()


def test_a_run_is_saved_as_marge_saves_it_with_the_consoles_dicom_files_as_its_images(tmp_path):
    pixels = [np.arange(12).reshape(3, 4), np.ones((3, 4))]
    files = [_dicom(p, "Localizer") for p in pixels]
    gateway = _Scripted({"exam": {"reply": {"localizer": [base64.b64encode(f).decode() for f in files]}}})
    gateway.localizer = lambda subject: console.Gateway.localizer(gateway, subject)
    sequence = console._localizer_class(_Base, gateway)()
    sequence.session = {"subject_name": "vials", "directory": str(tmp_path)}

    assert sequence.sequenceRun() is True
    output = sequence.sequenceAnalysis()

    assert [item["widget"] for item in output] == ["image", "image"]
    np.testing.assert_array_equal(output[1]["data"], 2.0 * pixels[1][np.newaxis])
    assert sequence.saved_with == []
    written = sorted(os.listdir(tmp_path / "dcm"))
    assert written == ["Localizer.2026.09.27.0001.dcm", "Localizer.2026.09.27.0002.dcm"]
    assert (tmp_path / "dcm" / written[0]).read_bytes() == files[0]


def test_marge_plans_the_protocols_field_of_view_entries_as_its_fov_in_cm():
    gateway = _Scripted(_scan_answers())
    sequence = console._plugin_class(_Base, gateway, "gre2d", console.parse_listing(LISTING))()

    assert sequence.mapVals["fov"] == [25.0, 20.0, 0.0]
    assert "phase_fov" not in sequence.mapVals
    sequence.mapVals["fov"] = [30.0, 15.0, 0.0]
    sequence.sequenceRun()

    block = gateway.calls[0][1]["block"].splitlines()
    assert "fov: 300.0" in block
    assert "phase_fov: 150.0" in block
    assert not any(line.startswith("slice_thickness") for line in block)
