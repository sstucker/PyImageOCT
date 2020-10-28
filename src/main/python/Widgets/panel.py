import glob
import os
from datetime import datetime

from PyQt5 import uic
from PyQt5.QtCore import pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QGroupBox, QLineEdit, QComboBox, QToolButton, QFileDialog, QSpinBox, QDoubleSpinBox, \
    QCheckBox, QPushButton, QLabel

RATES = {
    "76 kHz": 76000,
    "146 kHz": 146000
}

# ControlPanel modes
BUSY = -1
IDLE = 0
SCANNING = 1
ACQUIRING = 2


class ControlPanel(QGroupBox):

    def __init__(self):
        super(QGroupBox, self).__init__()
        ui = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + "\\ui\\control.ui"
        uic.loadUi(ui, self)

        self._scan_button = self.findChild(QPushButton, "pushScan")
        self._acq_button = self.findChild(QPushButton, "pushAcquire")
        self._stop_button = self.findChild(QPushButton, "pushStop")

        self._buttons = [self._scan_button, self._acq_button, self._stop_button]

        self._scan_button.clicked.connect(self.set_scanning)
        self._acq_button.clicked.connect(self.set_acquiring)
        self._stop_button.clicked.connect(self._stop)

        # Public
        self.mode = None
        self.disable()

    def connect_to_scan(self, slot):
        self._scan_button.clicked.connect(slot)

    def connect_to_acq(self, slot):
        self._acq_button.clicked.connect(slot)

    def connect_to_stop(self, slot):
        self._stop_button.clicked.connect(slot)

    def disable(self):
        """
        Disables GUI entirely
        :return: 0 on success
        """
        self.mode = BUSY
        for button in self._buttons:
            button.setEnabled(False)
        return 0

    def set_idle(self):
        """
        GUI is ready to begin another scan/acq session
        :return: 0 on success
        """
        self.mode = IDLE
        self._scan_button.setEnabled(True)
        self._acq_button.setEnabled(True)
        self._stop_button.setEnabled(False)
        return 0

    def _stop(self):
        # Parent must re-enable GUI when processing/saving/displaying is complete
        self.disable()

    def set_scanning(self):
        self.mode = SCANNING
        self._scan_button.setEnabled(False)
        self._acq_button.setEnabled(True)
        self._stop_button.setEnabled(True)

    def set_acquiring(self):
        self.mode = ACQUIRING
        self._scan_button.setEnabled(True)
        self._acq_button.setEnabled(False)
        self._stop_button.setEnabled(True)


class ScanPanelRose(QGroupBox):
    """
    For figure-8 or rose scan patterns
    """

    changedScale = pyqtSignal()
    changedSize = pyqtSignal()

    def __init__(self):

        super(QGroupBox, self).__init__()
        ui = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + "\\main\\ui\\scan_rose.ui"  # Double escape dir
        uic.loadUi(ui, self)

        self._scan_number_spin = self.findChild(QSpinBox, "spinScanNumber")

        self._z_top_spin = self.findChild(QSpinBox, "spinZTop")
        self._z_bottom_spin = self.findChild(QSpinBox, "spinZBottom")

        self._b_count_spin = self.findChild(QSpinBox, "spinBCount")
        self._a_count_spin = self.findChild(QSpinBox, "spinACount")

        self._spacing_spin = self.findChild(QDoubleSpinBox, "spinSpacing")
        self._rotation_spin = self.findChild(QDoubleSpinBox, "spinRotation")
        self._width_spin = self.findChild(QDoubleSpinBox, "spinWidth")

        self._width_spin.valueChanged.connect(self._width_changed)
        self._a_count_spin.valueChanged.connect(self._count_changed)
        self._spacing_spin.valueChanged.connect(self._spacing_changed)

    def get_dim(self):
        """
        Returns list [A count, B count]
        """
        return [self._a_count_spin.value(), self._b_count_spin()]

    def get_fov(self):
        """
        Returns B-width
        """
        return self._width_spin.value()

    def get_z_roi(self):
        """
        Returns list [z top, z bottom]
        """
        return [self._z_top_spin.value(), self._z_bottom_spin.value()]

    def get_rot(self):
        return self._rotation_spin.value()

    def lock_size(self, val):
        """
        If val is True, disables fields that affect buffer size/require acquisition to be restarted
        :param val: If true, scanning mode is enabled: some controls are disabled
        """
        self._a_count_spin.setEnabled(not val)
        self._b_count_spin.setEnabled(not val)
        self._scan_number_spin.setEnabled(not val)

    def _count_changed(self):
        # Need to block signals so that update functions aren't recursive
        self._width_spin.blockSignals(True)

        try:
            self._spacing_spin.setValue((self._spacing_spin.value() / 1000) * (self._a_count_spin.value() - 0))
        except ZeroDivisionError:
            pass

        # Unblock them
        self._width_spin.blockSignals(False)
        self.changedSize.emit()

    def _spacing_changed(self):
        self._width_spin.blockSignals(True)

        try:
            self._width_spin.setValue((self._spacing_spin.value() / 1000) * (self._a_count_spin.value() - 0))
        except ZeroDivisionError:
            pass

        self._width_spin.blockSignals(False)

    def _width_changed(self):
        self._spacing_spin.blockSignals(True)

        try:
            self._spacing_spin.setValue((self._width_spin.value() * 1000) / self._a_count_spin.value())
        except ZeroDivisionError:
            pass

        self._spacing_spin.blockSignals(False)
        self.changedScale.emit()

    def _z_roi_changed(self):
        self.changedScale.emit()


class ScanPanelRaster(QGroupBox):

    changedScale = pyqtSignal()
    changedSize = pyqtSignal()

    def __init__(self):

        super(QGroupBox, self).__init__()
        ui = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + "\\ui\\scan_octa.ui"  # Double escape dir
        uic.loadUi(ui, self)

        self._indefinite_check = self.findChild(QCheckBox, "checkIndefinite")
        self._indefinite_check.stateChanged.connect(self._indefinite_check_changed)

        self._equal_aspect_check = self.findChild(QCheckBox, "checkEqualAspect")
        self._equal_aspect_check.stateChanged.connect(self._equal_aspect_check_changed)

        self._scan_number_spin = self.findChild(QSpinBox, "spinScanNumber")

        self._z_top_spin = self.findChild(QSpinBox, "spinZTop")
        self._z_bottom_spin = self.findChild(QSpinBox, "spinZBottom")
        self._z_top_spin.editingFinished.connect(self.changedScale.emit)
        self._z_bottom_spin.editingFinished.connect(self.changedScale.emit)

        self._x_roi_spin = self.findChild(QDoubleSpinBox, "spinROIWidth")
        self._x_count_spin = self.findChild(QSpinBox, "spinACount")
        self._x_spacing_spin = self.findChild(QDoubleSpinBox, "spinFastAxisSpacing")

        self._x_roi_spin.valueChanged.connect(self._roi_changed)
        self._x_count_spin.valueChanged.connect(self._count_changed)
        self._x_spacing_spin.valueChanged.connect(self._x_spacing_changed)
        self._x_spacing_spin.valueChanged.connect(self._spacing_changed)

        self._x_roi_spin.editingFinished.connect(self.changedScale.emit)
        self._x_count_spin.editingFinished.connect(self.changedSize.emit)
        self._x_spacing_spin.editingFinished.connect(self.changedScale.emit)

        self._y_roi_spin = self.findChild(QDoubleSpinBox, "spinROIHeight")
        self._y_count_spin = self.findChild(QSpinBox, "spinBCount")
        self._y_spacing_spin = self.findChild(QDoubleSpinBox, "spinSlowAxisSpacing")

        self._y_roi_spin.valueChanged.connect(self._roi_changed)
        self._y_spacing_spin.valueChanged.connect(self._spacing_changed)
        self._y_count_spin.valueChanged.connect(self._count_changed)

        self._y_roi_spin.editingFinished.connect(self.changedScale.emit)
        self._y_count_spin.editingFinished.connect(self.changedSize.emit)
        self._y_spacing_spin.editingFinished.connect(self.changedScale.emit)

        # self._z_count_spin = self.findChild(QSpinBox, 'spinZCount')

        self._preview_button = self.findChild(QPushButton, "pushPreview")

        # Init
        self._count_changed()
        self._roi_changed()
        self._spacing_changed()

    def get_dim(self):
        """
        Returns list [x count, y count]
        """
        return [self._x_count_spin.value(), self._y_count_spin.value()]

    def get_fov(self):
        """
        Returns list [x ROI width, y ROI width]
        """
        return [self._x_roi_spin.value(), self._y_roi_spin.value()]

    def get_z_roi(self):
        """
        Returns list [Z ROI crop top, Z ROI crop bottom]
        """
        return [self._z_top_spin.value(), self._z_bottom_spin.value()]

    def lock_size(self, val):
        """
        If val is True, disables fields that affect buffer size/require acquisition to be restarted
        :param val: If true, scanning mode is enabled: some controls are disabled
        """
        self._x_count_spin.setEnabled(not val)
        self._y_count_spin.setEnabled(not val)
        self._indefinite_check.setEnabled(not val)
        self._scan_number_spin.setEnabled(not val)

    def _count_changed(self):
        # Need to block signals so that update functions arent recursive
        self._x_roi_spin.blockSignals(True)
        self._y_roi_spin.blockSignals(True)

        try:
            self._x_roi_spin.setValue((self._x_spacing_spin.value() / 1000) * (self._x_count_spin.value() - 0))
            self._y_roi_spin.setValue((self._y_spacing_spin.value() / 1000) * (self._y_count_spin.value() - 0))
        except ZeroDivisionError:
            pass

        # Unblock them
        self._x_roi_spin.blockSignals(False)
        self._y_roi_spin.blockSignals(False)

    def _spacing_changed(self):
        self._x_roi_spin.blockSignals(True)
        self._y_roi_spin.blockSignals(True)

        try:
            self._x_roi_spin.setValue((self._x_spacing_spin.value() / 1000) * (self._x_count_spin.value() - 0))
            self._y_roi_spin.setValue((self._y_spacing_spin.value() / 1000) * (self._y_count_spin.value() - 0))
        except ZeroDivisionError:
            pass

        self._x_roi_spin.blockSignals(False)
        self._y_roi_spin.blockSignals(False)
        # self.changedScale.emit()

    def _roi_changed(self):
        self._x_spacing_spin.blockSignals(True)
        self._y_spacing_spin.blockSignals(True)

        try:
            self._x_spacing_spin.setValue((self._x_roi_spin.value() * 1000) / self._x_count_spin.value())
            self._y_spacing_spin.setValue((self._y_roi_spin.value() * 1000) / self._y_count_spin.value())
        except ZeroDivisionError:
            pass

        self._x_spacing_spin.blockSignals(False)
        self._y_spacing_spin.blockSignals(False)

    def _indefinite_check_changed(self):
        if self._indefinite_check.isChecked():
            self._scan_number_spin.setEnabled(False)
        else:
            self._scan_number_spin.setEnabled(True)

    def _equal_aspect_check_changed(self):
        if self._equal_aspect_check.isChecked():
            self._y_spacing_spin.setValue(self._x_spacing_spin.value())
            self._y_spacing_spin.setEnabled(False)
            self._y_count_spin.setEnabled(False)
        else:
            self._y_spacing_spin.setEnabled(True)
            self._y_count_spin.setEnabled(True)

    def _x_spacing_changed(self):
        if self._equal_aspect_check.isChecked():
            self._y_spacing_spin.setValue(self._x_spacing_spin.value())

    def _z_roi_changed(self):
        pass


class ProcessingConfigPanel(QGroupBox):

    paramsChanged = pyqtSignal()

    def __init__(self):

        super(QGroupBox, self).__init__()
        ui = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + "\\ui\\processing_config.ui"  # Double escape dir
        uic.loadUi(ui, self)

        self.toggled.connect(self.paramsChanged.emit)

        self._interp_check = self.findChild(QCheckBox, "checkInterpolation")
        self._apod_check = self.findChild(QCheckBox, "checkApodization")

        self._interp_check.stateChanged.connect(self._toggle_interp)
        self._interp_check.stateChanged.connect(self.paramsChanged.emit)
        self._apod_check.stateChanged.connect(self._toggle_apod)
        self._apod_check.stateChanged.connect(self.paramsChanged.emit)

        self._interp_label = self.findChild(QLabel, "labelLambda")
        self._interp_label2 = self.findChild(QLabel, "labelTo")
        self._lambda_min_spin = self.findChild(QSpinBox, "spinLambdaMin")
        self._lambda_max_spin = self.findChild(QSpinBox, "spinLambdaMax")

        self._lambda_min_spin.valueChanged.connect(self.paramsChanged.emit)
        self._lambda_max_spin.valueChanged.connect(self.paramsChanged.emit)

        self._apod_label = self.findChild(QLabel, "labelWindowType")
        self._window_type = self.findChild(QComboBox, "comboApodization")

        self._window_type.currentIndexChanged.connect(self.paramsChanged.emit)

    def _toggle_apod(self):
        val = self._apod_check.isChecked()
        self._apod_label.setEnabled(val)
        self._window_type.setEnabled(val)

    def _toggle_interp(self):
        val = self._interp_check.isChecked()
        self._interp_label.setEnabled(val)
        self._interp_label2.setEnabled(val)
        self._lambda_max_spin.setEnabled(val)
        self._lambda_min_spin.setEnabled(val)

    def get_enabled(self):
        return self.isChecked()

    def get_lambda_range(self):
        return [self._lambda_min_spin.value(), self._lambda_max_spin.value()]

    def get_fft_enabled(self):
        return self._fft_check.isChecked()

    def get_apod_enabled(self):
        return self._apod_check.isChecked()

    def get_apod_window_type(self):
        return str(self._window_type.text())


class SpectralRadarConfigPanel(QGroupBox):

    def __init__(self):
        super(QGroupBox, self).__init__()
        ui = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + "\\ui\\config.ui"  # Double escape dir
        uic.loadUi(ui, self)

        self._configdir = "C:\\ThorLabs\\SpectralRadar\\config"  # TODO set this intelligently on startup
        self._configpaths = glob.glob(self._configdir + "/*.ini")

        self._config_combo = self.findChild(QComboBox, "comboConfig")
        for path in self._configpaths:
            self._config_combo.addItem(os.path.basename(path).split(".")[0])  # Just get name of file w/o extension

        self._rate_combo = self.findChild(QComboBox, "comboRate")
        self._apod_combo = self.findChild(QComboBox, "comboApodization")

        self._bitness_check = self.findChild(QCheckBox, "check32")
        self._fft_check = self.findChild(QCheckBox, "checkFFT")
        self._interp_check = self.findChild(QCheckBox, "checkInterpolation")

    def get_params(self):
        """
        Returns a list of 3 booleans: [fft, interp, 32bit or 64bit]
        """
        return [self._fft_check.checked(), self._interp_check.checked(), self._bitness_check.checked()]

    def get_config(self):
        return str(self._config_combo.text())

    def get_rate(self):
        return RATES[self._rate_combo.text()]

    def get_apod(self):
        return str(self._apod_combo.text())


class RepeatsPanel(QGroupBox):

    def __init__(self):
        super(QGroupBox, self).__init__()
        ui = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + "\\ui\\repeats.ui"  # Double escape dir
        uic.loadUi(ui, self)

        self._a_repeat_spin = self.findChild(QSpinBox, "spinARepeat")
        self._b_repeat_spin = self.findChild(QSpinBox, "spinBRepeat")
        self._avg_check = self.findChild(QCheckBox, "checkAveraging")

    def get_a_repeat(self):
        if self.isEnabled():
            return self._a_repeat_spin.value()
        else:
            return 1

    def get_b_repeat(self):
        if self.isEnabled():
            return self._b_repeat_spin.value()
        else:
            return 1

    def get_averaging(self):
        if self.isEnabled():
            return self._avg_check.checked()
        else:
            return False


FILETYPES = [
    ".npy",
    ".mat"
]

FILESIZES = [
    "250 MB",
    "500 MB",
    "1 GB",
    "2 GB",
    "4 GB"
]

BYTES = [
    2.5 * 10**8,
    5 * 10**8,
    1 * 10**9,
    2 * 10**9,
    3 * 10**9
]


class FilePanel(QGroupBox):

    def __init__(self):
        super(QGroupBox, self).__init__()

        ui = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + "\\ui\\filePanel.ui"  # Double escape dir
        uic.loadUi(ui, self)

        self._exp_dir_line = self.findChild(QLineEdit, "lineExp")
        self._trial_name_line = self.findChild(QLineEdit, "lineFileName")
        self._file_type_combo = self.findChild(QComboBox, "comboFileType")
        self._file_size_combo = self.findChild(QComboBox, "comboFileSize")

        self._file_browse_button = self.findChild(QToolButton, "buttonBrowse")
        self._file_browse_button.clicked.connect(self.browse_for_file)

        self._file_dialog = QFileDialog()

        self._default_exp_dir = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))) + "\\exp-" + datetime.today().strftime('%Y-%m-%d')
        self._exp_dir_line.setText(self._default_exp_dir)

        self.default_trial_name = "rec01"
        self._trial_name_line.setText(self.default_trial_name)

        self._file_type_combo.setCurrentIndex(0)

        self._file_type_combo.setCurrentIndex(1)

    def browse_for_file(self):
        #  Uses QFileDialog to select a directory to save in
        self._exp_dir_line.setText(self._file_dialog.getExistingDirectory(self, "Select Directory"))

    # Getters

    def get_experiment_directory(self):
        if not os.path.isdir(self._exp_dir_line.text()):
            os.mkdir(self._exp_dir_line.text())
        return self._exp_dir_line.text()

    def get_trial_name(self):
        return self._trial_name_line.text()

    def get_file_type(self):
        return str(self._file_type_combo.currentText())

    def get_file_size(self):
        return str(self._file_size_combo.currentText())

    def get_file_size_bytes(self):
        return int(BYTES[self._file_size_combo.currentIndex()])
