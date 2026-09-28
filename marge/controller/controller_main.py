"""
:author:    J.M. Algarín
:email:     josalggui@i3m.upv.es
:affiliation: MRILab, i3M, CSIC, Valencia, Spain

"""
import sys
import threading

from PyQt5.QtCore import QEvent

from marge.seq.pulserver_console import console_mode, register_console
from marge.seq.sequences import defaultsequences
from marge.ui.window_main import MainWindow
import marge.autotuning.autotuning as autotuning
import marge.configs.hw_config as hw


class MainController(MainWindow):
    def __init__(self, *args, **kwargs):
        # As the console of pulserver's virtual scanner, MaRGE drives no MaRCoS
        # hardware, as in demo mode.
        if console_mode():
            kwargs["demo"] = True
        super(MainController, self).__init__(*args, **kwargs)

        self.set_session(self.session)

        self.initializeThread()

        self.history_list.sequence_ready_signal.connect(self.history_list.updateHistoryFigure2)
        self.history_list.figure_ready_signal.connect(self.toolbar_figures.doScreenshot)

        # Define the arduinos
        self.arduino_autotuning = autotuning.Arduino(baudrate=hw.ard_br_autotuning)
        self.arduino_autotuning.connect(serial_number=hw.ard_sn_autotuning)
        if hw.ard_sn_autotuning==hw.ard_sn_interlock:
            self.arduino_interlock = self.arduino_autotuning
        else:
            self.arduino_interlock = autotuning.Arduino(hw.ard_br_interlock)
            self.arduino_interlock.connect(serial_number=hw.ard_sn_interlock)

        if console_mode():
            register_console(self.toolbar_sequences)

    def set_demo(self, demo):
        self.demo = demo or console_mode()

    def set_session(self, session):
        # Set window title
        self.session = session
        self.setWindowTitle("MaRGE " + session["software_version"] + ": " + session['directory'])
        # Add the session to all sequences
        for sequence in defaultsequences.values():
            sequence.session = session
        # As the console of pulserver's virtual scanner, a session is an exam
        # on the phantom its subject names, which opens on its localizer when
        # the window shows; the localizer needs no scan.
        self.exam_opened = not console_mode()

    def showEvent(self, event):
        super().showEvent(event)
        if not self.exam_opened:
            self.exam_opened = True
            self.toolbar_sequences.startLocalizer()

    def initializeThread(self):
        # The sniffers run the waiting list on the MaRCoS server, which a
        # console of pulserver's virtual scanner does not have.
        if console_mode():
            return
        # Start the sniffer
        thread = threading.Thread(target=self.history_list.waitingForRun)
        thread.start()
        thread = threading.Thread(target=self.history_list.waitingForRecon)
        thread.start()
        print("Sniffer initialized.\n")

    def set_console(self):
        self.layout_left.addWidget(self.console)

    def changeEvent(self, event):
        """
        Handle window activation change events.

        Redirects the shared console widget into this window's layout whenever
        this window becomes the active window.

        Args:
            event (QEvent): The Qt event object.
        """
        if event.type() == QEvent.ActivationChange:  # Event type 99
            if self.isActiveWindow():
                self.set_console()
        super().changeEvent(event)

    def closeEvent(self, event):
        """
        Shuts down the application on close.

        This method is called when the application is being closed. It sets the `app_open` flag to False, restores
        `sys.stdout` to its default value, and performs additional cleanup tasks if the `demo` flag is not set.
        It also prints a closing message to the console.

        Args:
            event (QCloseEvent): The close event triggered by the user.

        Returns:
            None
        """
        # Return stdout to defaults.
        sys.stdout = sys.__stdout__
            
        print('\nMain GUI closed successfully!')

        self.parent.show()

        super().closeEvent(event)
