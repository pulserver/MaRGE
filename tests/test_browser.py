"""MaRGE in a browser tab as the console of pulserver's virtual scanner, from exam to images.

Runs where ``MARGE_WEB`` is the address the page of ``web/build.py`` is served
at and ``MARGE_PULSERVER`` the WebSocket address of a ``pulserver console``
that has the ``gre2d`` plugin and a reconstruction proxy behind it.
``CHROMIUM`` names a Chromium to run instead of Playwright's own.
"""

import json
import os
import time

import pytest

from marge.browser.boot import PROJECT

WEB = os.environ.get("MARGE_WEB", "")
CONSOLE = os.environ.get("MARGE_PULSERVER", "")

pytestmark = pytest.mark.skipif(
    not (WEB and CONSOLE), reason="needs MARGE_WEB and MARGE_PULSERVER"
)


@pytest.fixture(scope="module")
def page():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("CHROMIUM") or None,
            args=["--autoplay-policy=no-user-gesture-required"],
        )
        tab = browser.new_page(viewport={"width": 1400, "height": 900})
        tab.goto(f"{WEB}/index.html?console={CONSOLE}")
        tab.wait_for_function(
            "window.marge && (window.marge.ready || window.marge.error)", timeout=600_000
        )
        assert tab.evaluate("window.marge.error") is None
        yield tab
        browser.close()


def _python(page, code):
    """Run ``code`` in the tab and return its last expression, which is JSON."""
    return json.loads(page.evaluate("code => window.pyodide.runPythonAsync(code)", code))


def _until(page, code, timeout=600.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = _python(page, code)
        if value:
            return value
        page.wait_for_timeout(1000)
    raise AssertionError(f"not within {timeout} s: {code}")


PRELUDE = """
import json
from marge.browser import boot
from marge.seq.sequences import defaultsequences
session = boot._kept[1]
main = session.main_gui
history = [] if main is None else [main.history_list.item(i).text() for i in range(main.history_list.count())]
"""


def test_the_session_opens_configured_for_the_virtual_scanner_with_its_coils_to_choose_from(page):
    state = _python(
        page,
        PRELUDE
        + "combo = session.tab_session.rf_coil_combo_box\n"
        + "json.dumps([session.launch_gui_action.isEnabled(),"
        + " session.tab_session.project_combo_box.currentText(),"
        + " [combo.itemText(i) for i in range(combo.count())], combo.currentText()])",
    )

    assert state == [True, PROJECT, ["body", "head8", "head32", "head48"], "body"]


def test_an_exam_opens_on_the_localizer_of_the_subjects_phantom_among_pulservers_sequences_only(page):
    _python(
        page,
        PRELUDE
        + """
session.tab_session.name_line_edit.setText("vials")
session.launch_gui_action.trigger()
json.dumps(None)
""",
    )

    history = _until(page, PRELUDE + "json.dumps(history)")
    localizer, names = _python(
        page,
        PRELUDE + 'json.dumps([len(defaultsequences["Localizer"].files), sorted(defaultsequences)])',
    )

    assert history[0].split(" | ")[1].startswith("Localizer.")
    assert localizer == 3
    assert names == ["Localizer", "gre2d"]


def test_a_scan_of_marges_protocol_plays_its_sound_and_returns_its_reconstruction(page):
    _python(
        page,
        PRELUDE
        + """
sequence = defaultsequences["gre2d"]
sequence.mapVals["nx"] = sequence.mapVals["ny"] = 32
main.toolbar_sequences.startAcquisition(seq_name="gre2d")
json.dumps(None)
""",
    )

    history = _until(page, PRELUDE + "json.dumps(len(history) == 2 and history)")
    files, clock, played = _python(
        page,
        PRELUDE
        + """
from marge.seq import pulserver_console as console
sequence = defaultsequences["gre2d"]
json.dumps([len(sequence.files), sequence.clock, console.SPEAKER.played])
""",
    )

    assert history[1].split(" | ")[1].startswith("gre2d.")
    assert files >= 1
    assert clock[0] == pytest.approx(clock[1]) and clock[1] > 0.0
    assert played == pytest.approx(clock[1], rel=0.01)
