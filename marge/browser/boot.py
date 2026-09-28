"""Start MaRGE in a browser tab as the console of pulserver's virtual scanner."""

from __future__ import annotations

import os
from typing import Any

#: MaRGE's working directory in the tab's file system, which lasts as long as the tab.
HOME = "/home/pyodide/marge"

#: A project, a study, a Red Pitaya address and an RF coil, which MaRGE's
#: session asks for and the virtual scanner does not use.
PROJECT, STUDY, RED_PITAYA, COIL = "pulserver", "Phantom", "127.0.0.1", "virtual"

_kept: list[Any] = []


async def start(address: str) -> Any:
    """Connect to ``pulserver console`` at ``address`` and open MaRGE's session window.

    The console's plugin listings are fetched before MaRGE imports its
    sequences, since no Qt handler may wait on the network in a tab, and
    scans play their sound through the tab's speaker. A tab
    starts with no configuration, so the session is configured with
    :data:`PROJECT`, :data:`STUDY`, :data:`RED_PITAYA` and :data:`COIL`.
    """
    from . import audio, qt5, runtime

    runtime.install()
    qt5.install()
    os.environ["MARGE_PULSERVER"] = address
    from marge.seq import pulserver_console as console

    gateway = await console.AsyncGateway.open(address)
    console.install((gateway, await console.listings_async(gateway)))
    console.SPEAKER = audio.Speaker()

    os.makedirs(HOME, exist_ok=True)
    os.chdir(HOME)
    for folder in ("experiments/parameterization", "calibration", "protocols", "reports", "configs"):
        os.makedirs(folder, exist_ok=True)

    from PyQt5.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    from marge.controller.controller_session import SessionController

    session = SessionController()
    if not os.path.exists("configs/sys_projects.csv"):
        _configure(session)
    session.show()
    _kept[:] = [application, session]
    return session


def _configure(session: Any) -> None:
    tab = session.tab_session
    for combo, name in ((tab.project_combo_box, PROJECT), (tab.study_combo_box, STUDY)):
        combo.addItem(name)
        combo.setCurrentText(name)
        combo.save_items()
    session.tab_console.text_box.setText(RED_PITAYA)
    session.tab_console.add_rp()
    session.tab_console.save_rp_entries()
    session.tab_console.update_hw_config_rp()
    session.tab_rf.text_box_1.setText(COIL)
    session.tab_rf.text_box_2.setText("1.0")
    session.tab_rf.add_rf()
    session.tab_rf.save_rf_entries()
    session.tab_gradients.save_gradient_entries()
    session.tab_others.save_others_entries()
    with open("configs/b1Efficiency.csv", "w") as file:
        file.write(f"{COIL}\n")
    session.update_hardware()
