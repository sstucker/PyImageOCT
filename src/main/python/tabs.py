import multiprocessing as mp
import time

import matplotlib
import numpy as np
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import QWidget, QGridLayout, QProgressDialog
from PyQt5.QtWidgets import QApplication

from Control import INSTR_OPEN, INSTR_START_ACQ, INSTR_UPDATE_ACQ, INSTR_STOP_ACQ, INSTR_CLOSE, INSTR_BEGIN_SAVE,\
    INSTR_END_SAVE
from Control import OCTAControlProcess, OCTAMessage
from Control import STATUS_READY, STATUS_ACQUIRING, STATUS_NOT_READY
from PyScanPattern.Patterns import RasterScanPattern
from Widgets.graph import BScanView3D, SpectrumView
from Widgets.panel import FilePanel, RepeatsPanel, ProcessingConfigPanel, ControlPanel, \
    ScanPanelRaster

matplotlib.use('Qt5Agg')

# Size of line camera
ALINE_SIZE = 2048
HALFWIDTH = int(ALINE_SIZE / 2 + 1)

_frame_server_timer = QTimer()


class TabOCTA(QWidget):

    def __init__(self, parent):
        super(QWidget, self).__init__()

        # -- Load GUI -------------------------------------------------
        self._parent = parent

        self._layout = QGridLayout()

        self._filePanel = FilePanel()
        self._layout.addWidget(self._filePanel)

        self._configPanel = ProcessingConfigPanel()
        self._layout.addWidget(self._configPanel)
        self._configPanel.paramsChanged.connect(self._set_restart_required)

        self._scanPanel = ScanPanelRaster()
        self._scanPanel.changedScale.connect(lambda: self._program_controller(INSTR_UPDATE_ACQ))
        self._scanPanel.changedSize.connect(self._set_restart_required)

        self._layout.addWidget(self._scanPanel)

        self._repeatsPanel = RepeatsPanel()
        self._layout.addWidget(self._repeatsPanel)

        self._controlPanel = ControlPanel()
        self._layout.addWidget(self._controlPanel)
        self._controlPanel.connect_to_scan(self._start_scan)
        self._controlPanel.connect_to_acq(self._start_save)
        self._controlPanel.connect_to_stop(self._stop_scan)
        self._controlPanel.connect_to_stop(self._stop_save)

        self._bscanView = BScanView3D()
        self._layout.addWidget(self._bscanView, 0, 1, 3, 1)

        self._spectrumView = SpectrumView()
        self._layout.addWidget(self._spectrumView, 3, 1, 2, 1)
        chirp = np.linspace(self._configPanel.get_lambda_range()[0], self._configPanel.get_lambda_range()[1], ALINE_SIZE)
        self._spectrumView.viewer.set_chirp(chirp)

        self.setLayout(self._layout)

        self._progress_bar = None  # Tab progress bar

        _frame_server_timer.timeout.connect(self._frame_server)

        # -- Controller -------------------------------------------------

        self._controller_exit = None
        self._controller = None

        self._restart_required = False

        self._pattern = RasterScanPattern()
        self._update_scan_pattern()

    def _update_scan_pattern(self):
        print("Scan pattern dim", self._scanPanel.get_dim())
        self._pattern.generate(alines=self._scanPanel.get_dim()[0],
                               blines=self._scanPanel.get_dim()[1],
                               fov=self._scanPanel.get_fov())

    def _start_scan(self):
        if self._restart_required:
            self._parent.show_status("Please wait...", timeout=300)
            # self._parent.show_status("Applying changes to hardware interface before scanning begins...")
            # self._progress_bar = QProgressDialog("Applying changes to interface...", None, 0, 100, self)
            # self._progress_bar.setWindowModality(Qt.ApplicationModal)
            # self._progress_bar.show()
            self._controlPanel.disable()
            self._close_controller()
            # self._progress_bar.setValue(50)
            self._start_controller()
            self._restart_required = False
            self._controller.send_msg(OCTAMessage(INSTR_START_ACQ))
            # self._progress_bar.setValue(100)
        if self._controller.status.value is STATUS_READY:
            self._controller.send_msg(OCTAMessage(INSTR_START_ACQ))
        elif self._controller.saving.value is 1:
            self._controller.send_msg(OCTAMessage(INSTR_END_SAVE))

    def _stop_scan(self):
        self._controller.send_msg(OCTAMessage(INSTR_STOP_ACQ))

    def _start_save(self):
        msg = OCTAMessage(INSTR_BEGIN_SAVE)
        msg.writer.fpath = self._filePanel.get_experiment_directory() + '/' + self._filePanel.get_trial_name()
        msg.writer.max_bytes = self._filePanel.get_file_size_bytes()

        self._controller.send_msg(msg)

    def _stop_save(self):
        self._controller.send_msg(OCTAMessage(INSTR_END_SAVE))

    def _program_controller(self, instr):
        """
        Update the controller with parameters from the GUI
        """
        msg = OCTAMessage(instr)

        self._update_scan_pattern()

        # TODO implement config for CONSTS here
        NUMBER_OF_IMAQ_BUFFERS = 4
        TRIGGER_GAIN = 4
        msg.niconfig.dac_device = 'Dev1'
        msg.niconfig.cam_device = 'img1'
        msg.niconfig.line_trig_ch = 'ao0'
        msg.niconfig.frame_trig_ch = 'ao3'
        msg.niconfig.x_ch = 'ao1'
        msg.niconfig.y_ch = 'ao2'
        msg.niconfig.number_of_imaq_buffers = NUMBER_OF_IMAQ_BUFFERS
        msg.niconfig.dac_rate = self._pattern.get_sample_rate()

        msg.scansig.line_trig_sig = self._pattern.get_line_trig() * TRIGGER_GAIN
        msg.scansig.frame_trig_sig = self._pattern.get_frame_trig() * TRIGGER_GAIN
        x = self._pattern.get_x()
        y = self._pattern.get_y()
        x *= 1 / 0.092 * 1 / 1.056 * 44 / 32  # ~92 um/V, sq aspect  (calibrated 10/22/2020)
        y *= 1 / 0.092 * 1 / 1.288   # ~92 um/V, sq aspect
        msg.scansig.x_sig = x
        msg.scansig.y_sig = y

        msg.oct.aline_size = ALINE_SIZE
        msg.oct.alines_per_buffer = self._scanPanel.get_dim()[0] * self._scanPanel.get_dim()[1]
        msg.oct.alines_per_bline = self._scanPanel.get_dim()[0]
        msg.oct.number_of_blines = self._scanPanel.get_dim()[1]
        msg.oct.ztop = self._scanPanel.get_z_roi()[0]
        msg.oct.zbottom = self._scanPanel.get_z_roi()[1]
        msg.oct.apod_window = np.hanning(ALINE_SIZE).astype(np.float32)  # TODO get from config
        # msg.oct.apod_window = None

        msg.writer.fpath = self._filePanel.get_experiment_directory() + self._filePanel.get_trial_name()
        msg.writer.max_bytes = self._filePanel.get_file_size_bytes()

        chirp = np.linspace(self._configPanel.get_lambda_range()[0], self._configPanel.get_lambda_range()[1], ALINE_SIZE)
        self._spectrumView.viewer.set_chirp(chirp)
        self.rescale_plots()

        # Save the dimensions last sent to controller
        self._acq_dim = self._scanPanel.get_dim()

        self._controller.send_msg(msg)

    def _start_controller(self):

        self._parent.show_status("Initializing hardware interface...")

        self._controller_exit = mp.Event()
        self._controller = OCTAControlProcess(self._controller_exit)
        self._controller.start()

        # Program with initial conditions
        self._program_controller(INSTR_OPEN)

        # Start the server thread
        _frame_server_timer.start(int(1 / 60))

    def _close_controller(self):

        self._controller.send_msg(OCTAMessage(INSTR_CLOSE))
        self._controller_exit.set()
        self._controller.join(timeout=2)  # Wait 2 seconds for the process to clean itself up
        if self._controller.is_alive():  # If it's stuck, terminate it
            self._controller.terminate()
            print(type(self).__name__ + ': controller terminated!')
        else:
            print(type(self).__name__ + ': controller closed safely.')

        # Stop the server thread
        _frame_server_timer.stop()

    def _set_restart_required(self):
        print("Restart required")
        self._restart_required = True

    def _set_gui_scanning(self):
        self._controlPanel.set_scanning()
        self._configPanel.setEnabled(False)
        self._scanPanel.lock_size(True)

    def _set_gui_ready(self):
        self._controlPanel.set_idle()
        self._configPanel.setEnabled(True)
        self._scanPanel.lock_size(False)
        self._scanPanel.setEnabled(True)
        self._repeatsPanel.setEnabled(True)
        self._filePanel.setEnabled(True)

    def _set_gui_not_ready(self):
        self._controlPanel.disable()

    def _frame_server(self):
        """
        Displays buffered frames and keeps controller up to date with parameters
        from GUI
        """
        if self._controller.status.value is STATUS_ACQUIRING:

            f = self._controller.grab_frame()

            if f is not None:
                self._bscanView.draw(np.roll(f.rr, -2, axis=1))
                if self._spectrumView.get_dc():
                    self._spectrumView.draw(f.spec)
                else:
                    self._spectrumView.draw(f.dc)
            else:
                self._stop_scan()
                print("Grab failed. Stopping scan")

            self._set_gui_scanning()
            if self._controller.saving.value is 1:  # If saving
                self._controlPanel.set_acquiring()
                self._filePanel.setEnabled(False)
                self._parent.show_status("Acquiring data to "+str(self._filePanel.get_experiment_directory()), timeout=300)
            else:  # If not saving
                self._controlPanel.set_scanning()
                self._filePanel.setEnabled(True)
                self._parent.show_status("Scanning...", timeout=300)

        elif self._controller.status.value is STATUS_READY:
            self._parent.show_status("Ready to scan", timeout=300)
            self._set_gui_ready()

        elif self._controller.status.value is STATUS_NOT_READY:
            self._parent.show_status("Please wait...", timeout=300)
            self._set_gui_not_ready()

        QApplication.processEvents()

    def rescale_plots(self):
        dxdy = np.array(self._scanPanel.get_fov()) / np.array(self._scanPanel.get_dim()) * 10**-3  # in meters
        dz = ((ALINE_SIZE * 1310**2) / (4 * (self._configPanel.get_lambda_range()[1] - self._configPanel.get_lambda_range()[0]))) / HALFWIDTH   * 10**-9
        self._bscanView.set_pixel_sizes(dxdy[0], dxdy[1], dz)

    def select(self):
        print(type(self).__name__ + ' selected')
        self._start_controller()

    def deselect(self):
        print(type(self).__name__ + ' deselected')
        self._close_controller()

    def close(self):
        self._parent.show_status("Closing hardware interface...")
        self._close_controller()
