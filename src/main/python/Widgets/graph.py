import os
from collections import deque

import numpy as np
import pyqtgraph
from PyQt5 import uic
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QWidget, QRadioButton, QCheckBox, QSlider, QLabel


_display_update_timer = QTimer()


def start_refresh(hz=60):
    t = int(1 / hz)
    _display_update_timer.start(t)


def stop_refresh():
    _display_update_timer.stop()


class RunningPlotWidget(pyqtgraph.GraphicsWindow):
    def __init__(self, window_length=512, x=None, miny=-2000, maxy=2000):
        super().__init__()

        self._length = window_length
        self.colors = [[69, 176, 207],
                        # [211, 64, 83],
                        # [92, 182, 76],
                        # [186, 92, 194],
                        # [178, 181, 66],
                        # [110, 102, 211],
                        # [198, 142, 69],
                        # [128, 128, 196],
                        # [197, 91, 50],
                        # [86, 173, 127],
                        # [204, 84, 148],
                        # [104, 123, 52],
                        [191, 106, 120]]
        self._lines = []
        self._data = []
        if x is None or len(x) is not self._length:
            self._x = np.arange(window_length)
        else:
            self._x = x

        self._plot_item = self.addPlot()
        self._plot_item.setYRange(miny, maxy)
        self._plot_item.addLegend()

        for i, color in enumerate(self.colors):
            pen = pyqtgraph.mkPen(color=color)
            circ = deque(maxlen=window_length)
            self._lines.append(self._plot_item.plot(pen=pen, name='B-scan '+str(i)))
            self._data.append(circ)

    def append_to_plot(self, values):
        """
        Appends iterable of values to the end of the plot
        """
        for i, val in enumerate(values):
            self._data[i].append(val)
            self._lines[i].setData(np.array(self._data[i]))


class SpectrumPlotWidget(pyqtgraph.GraphicsWindow):
    def __init__(self, bins=2048, chirp=[]):
        super().__init__()
        if ~(len(chirp) > 0):
            self._chirp = np.arange(bins)
        else:
            self._chirp = chirp

        self._bins = bins

        self._plot_item = self.addPlot()
        self._plot_item.setYRange(-2000, 2000)

        self._spectrum = self._plot_item.plot(color='#FFFFFF')
        self._spectrum.setData(self._chirp, np.zeros(len(self._chirp)))
        self._spectrum2 = self._plot_item.plot(color='#FF0000')
        self._spectrum2.setData(self._chirp, np.zeros(len(self._chirp)))

    def set_spectrum_data(self, y):
        if len(y) == self._bins:
            self._spectrum.setData(self._chirp, y)
            pyqtgraph.QtGui.QApplication.processEvents()

    def set_spectrum2_data(self, y):
        if len(y) == self._bins:
            self._spectrum2.setData(self._chirp, y)
            pyqtgraph.QtGui.QApplication.processEvents()

    def set_chirp(self, chirp):
        self._chirp = chirp


class OCTViewer(pyqtgraph.GraphicsLayoutWidget):

    def __init__(self):
        super().__init__()

        # TODO implement spatial units/x axis

        self._plot = self.addPlot()
        self._plot.setAspectLocked()
        self.image = pyqtgraph.ImageItem()
        self._plot.addItem(self.image)
        self.setLevels([-2, 100])

    def setImage(self, img):
        self.image.setImage(np.rot90(img, k=3))
        pyqtgraph.QtGui.QApplication.processEvents()

    def setLevels(self, levels):
        self.image.setLevels(levels)
        pyqtgraph.QtGui.QApplication.processEvents()


class SpectrumView(QWidget):

    def __init__(self):
        super(QWidget, self).__init__()
        ui = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + "\\ui\\spectrumplotter.ui"
        uic.loadUi(ui, self)

        plotwidget_placeholder = self.findChild(QWidget, "widgetSpec")
        plotwidget_placeholder_layout = plotwidget_placeholder.parent().layout()
        self.viewer = SpectrumPlotWidget()
        plotwidget_placeholder_layout.replaceWidget(plotwidget_placeholder, self.viewer)

        self._dc_check = self.findChild(QCheckBox, "checkRemoveDC")
        self._db_check = self.findChild(QCheckBox, "checkDb")

        self._dc_check.toggled.connect(self._draw_frame)
        self._db_check.toggled.connect(self._draw_frame)

        self._frame_shape = []
        self._current_frame = []

        self._buffer = deque(maxlen=12)  # TODO implement parameter for maxlen
        _display_update_timer.timeout.connect(self._update_display)

    def get_buffer(self):
        """
        :return: Handle to deque object
        """
        return self._buffer

    def enqueue_frame(self, frame):
        """
        Enques a 1D signal. Will overwrite previously queued undisplayed frames if the circular queue
        is full. The rate at which the display updates and displays a new frame from the buffer can be set with
        set_update_rate(rate_in_hz). Default is 60.
        :param frame: 1D spectral data
        :return: 0 on success
        """
        self._buffer.append(np.array(frame).astype(int))
        # # print('SpectrumView: Frame appended. Frames in buffer:', len(self._buffer))

    def _update_display(self):
        try:
            f = self._buffer.pop()
            # # print('Frame popped. Frames in buffer:', len(self._buffer))
        except IndexError:  # If deque is empty
            f = self._current_frame

        self._current_frame = f

        if self._draw_frame() is -1:  # Draws the frame
            print('Couldn\'t draw a frame')

    def _draw_frame(self):
        frame = self._current_frame
        # TODO implement dc
        try:
            if self._db_check.isChecked():
                frame = 20 * np.log10(frame)
            self.viewer.set_spectrum_data(frame)
        except IndexError:  # Usually occurs when a new frame has been added with different dimensions
            return -1
        except ValueError:
            return -1


class BScanView(QWidget):

    def __init__(self):

        super(QWidget, self).__init__()
        ui = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + "\\ui\\bscanplotter.ui"  # Double escape dir
        uic.loadUi(ui, self)

        # Add pyqtgraph subwidget
        viewer_placeholder = self.findChild(QWidget, "widget3D")
        viewer_placeholder_layout = viewer_placeholder.parent().layout()
        self.viewer = OCTViewer()
        viewer_placeholder_layout.replaceWidget(viewer_placeholder, self.viewer)

        self._slice_slider = self.findChild(QSlider, "sliderSlice")
        self._enface_radio = self.findChild(QRadioButton, "radioEnface")
        self._scan_check = self.findChild(QCheckBox, "checkScanThrough")
        self._db_check = self.findChild(QCheckBox, "checkDb")
        self._mip_check = self.findChild(QCheckBox, "checkMIP")
        self.slice_label = self.findChild(QLabel, "sliceLabel")

        self._lf_crop = 0
        self._ROI_z = 100

        # Initial conditions
        self._frame_shape = []
        self._current_frame = []
        self._current_slice = -1
        self._slice_max = 0
        self.enface_enabled = None  # Intentionally public

        self._buffer = deque(maxlen=12)  # TODO implement parameter for maxlen

        self._slice_slider.valueChanged.connect(self._slider_change)
        self._enface_radio.toggled.connect(self._set_orientation)
        self._scan_check.toggled.connect(self._set_scan_toggle)
        self._mip_check.toggled.connect(self._mip_changed)
        self._db_check.toggled.connect(self._db_changed)

        # self._slice_timer.timeout.connect(self._slice_thru_advance)
        _display_update_timer.timeout.connect(self._update_display)

    def get_buffer(self):
        """
        :return: Handle to deque object
        """
        return self._buffer

    def enqueue_frame(self, frame):
        """
        Enques a 3D OCT frame for display. Will overwrite previously queued undisplayed frames if the circular queue
        is full. The rate at which the display updates and displays a new frame from the buffer can be set with
        set_update_rate(rate_in_hz). Default is 60.
        :param frame: 3D OCT data frame
            First dimension: A-line, z
            Second dimension: B-line, fast axis
            Third dimension (optional): Slow axis
        :return: 0 on success
        """
        if np.max(frame) is 0:  # Don't enqueue null arrays
            return -1
        f = np.array(frame).astype(np.complex64)[self._lf_crop:self._lf_crop + self._ROI_z, :, :]  # Crop to ROI
        self._buffer.append(f)
        if self._current_slice is -1:  # On initial startup only
            self._set_slice(1)  # Slice 1 is index 0!
            self._set_orientation()
            self._frame_shape = np.shape(f)
        # print('BScanView: Frame appended. Frames in buffer:', len(self._buffer))

    def get_frame_shape(self):
        """
        Gets shape of currently displayed 3D data
        :return: 3D shape array
        """
        return self._frame_shape

    def _slice_thru_advance(self):
        if self._current_slice is self._slice_max:
            self._set_slice(1)
        else:
            self._set_slice(self._current_slice + 1)

    def _update_display(self):
        # print('BScanView: updating display')
        if self._scan_check.isChecked():
            self._slice_thru_advance()

        try:
            f = self._buffer.pop()
            # print('BScanView: Frame popped. Frames in buffer:', len(self._buffer))
        except IndexError:  # If deque is empty
            f = self._current_frame
            # # # print('Buffer empty. Stopping refresh timer until a new frame is added')

        self._current_frame = f

        if self._draw_frame() is -1:  # Draws the frame
            # print('BScanView: draw failed')
            pass
            # _display_update_timer.stop()

    def _draw_frame(self):
        frame = self._current_frame
        frame = np.abs(np.real(frame))  # TODO make abs vs real vs imag etc parameters
        try:
            if self._db_check.isChecked():
                frame = 20 * np.log10(frame)
            if self.enface_enabled:  # Enface mode
                if self._mip_check.isChecked():  # MIP
                    self.viewer.setImage(np.max(frame, axis=0))  # Axial MIP
                else:
                    self.viewer.setImage(frame[self._current_slice - 1, :, :])  # Slice is indexed from 1
            else:  # B-scan mode
                if self._mip_check.isChecked():  # MIP
                    self.viewer.setImage(np.max(frame, axis=2))  # MIP across slow axis
                else:
                    self.viewer.setImage(frame[:, :, self._current_slice - 1])
            return 0
        except IndexError:  # Occurs when a new frame has been added with different dimensions
            # print('BScanViewer: IndexError raised on draw')
            return -1
        except ValueError:
            # print('BScanViewer: ValueError raised on draw')
            return -1

    def _set_scan_toggle(self):
        if self._scan_check.isChecked():
            self._slice_slider.setEnabled(False)
            # self._slice_timer.start(64)  # TODO implement parameter
            # # # print("Automatic scan")
            self._slice_slider.blockSignals(True)
        else:
            self._slice_slider.setEnabled(True)
            # self._slice_timer.stop()
            # # # print("Manual control")
            self._slice_slider.blockSignals(False)

    def _mip_changed(self):
        if self._mip_check.isChecked():
            self._slice_slider.setEnabled(0)
            self._scan_check.setEnabled(0)
            self.slice_label.setEnabled(0)
            # self._slice_timer.stop()
            # # # print('MIP mode entered')
            self._scan_check.setChecked(False)
        else:
            self._slice_slider.setEnabled(1)
            self._scan_check.setEnabled(1)
            self.slice_label.setEnabled(1)
            self._set_scan_toggle()  # Resume normal scan control
            # # # print('MIP mode exit')
        self._draw_frame()

    def _db_changed(self):
        self._draw_frame()

    def _set_orientation(self):
        if self._enface_radio.isChecked():
            self.enface_enabled = True
            try:
                self._slice_max = self._frame_shape[0]  # Slice through z
                print('BScanViewer: Enface mode', self._slice_max)
            except IndexError:
                return
        else:
            self.enface_enabled = False
            try:
                self._slice_max = self._frame_shape[2]  # Slice through slow axis
            except IndexError:
                self._slice_max = 1
        if self._current_slice > self._slice_max:  # If current slice too big for new orientation
            try:
                self._set_slice(self._frame_shape[2])
            except IndexError:
                self._slice_max = 1
        else:
            self._set_slice(self._current_slice)  # Update slice label
        self._slice_slider.setMaximum(self._slice_max)

    def _set_slice(self, index):
        # # # print('Current slice', index)
        self._current_slice = index  # Only place where this assignment is allowed!
        self.slice_label.setText(str(self._current_slice) + "/" + str(self._slice_max))
        self._slice_slider.setValue(self._current_slice)  # Slider not connected to this method on auto slice
        self._draw_frame()

    def _slider_change(self):
        self._set_slice(self._slice_slider.value())
