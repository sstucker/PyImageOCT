from PyQt5 import QtWidgets, QtCore
from PyQt5.QtCore import QThread, QObject, pyqtSignal, pyqtSlot, QTimer
from pyqtgraph import PlotWidget, ImageView
import pyqtgraph as pyqtgraph
import sys  # We need sys so that we can pass argv to QApplication
import os
import time
import nidaqmx
from PyIMAQ import PyIMAQ
from nidaqmx.stream_writers import AnalogMultiChannelWriter
from nidaqmx.constants import LineGrouping, Edge, AcquisitionType, Signal
from PyScanPattern.Patterns import Figure8ScanPattern, RasterScanPattern
import numpy as np
from queue import Empty, Full
import multiprocessing as mp
from dataclasses import dataclass
import threading

# HardwareControlProcess messages
VOID = 0
INSTR_OPEN = 1
INSTR_CLOSE = 2
INSTR_STOP_ACQ = 3
INSTR_START_ACQ = 4
INSTR_UPDATE_ACQ = 5

# HardwareControlProcess states
READY = 0
NOT_READY = -2
ACQUIRING = 1
EXITING = -1


class RepeatTimer(threading.Timer):
    """
    Thank you Hans Then & right2clicky on stackoverflow!
    """

    def run(self):
        while not self.finished.wait(self.interval):
            self.function(*self.args, **self.kwargs)


# -- Data structures ---------------------------------------------------------------------------------------------------

class SlotStruct:

    def get_slots(self):
        return self.__slots__


class Message(SlotStruct):
    """
    Information enqueued by parent process of a HardwareControlProcess via the process's msg_queue
    """
    pass


@dataclass
class Frame:
    """
    Atomic unit of data allocated by HardwareControlProcess and enqueued
    """
    pass


# -- Messages ----------------------------------------------------------------------------------------------------------


class NIConfig(SlotStruct):
    """
    Fast access structure for NI IMAQ and DAQmx initial setup parameters
    """
    __slots__ = ["cam_device", "dac_device", "line_trig_ch", "frame_trig_ch", "x_ch", "y_ch",
                 "number_of_imaq_buffers", "dac_rate"]

    def __init__(self):
        self.cam_device = None
        self.dac_device = None
        self.line_trig_ch = None
        self.frame_trig_ch = None
        self.x_ch = None
        self.y_ch = None
        self.number_of_imaq_buffers = None
        self.dac_rate = None


class ScanSignals(SlotStruct):
    """
    Fast access structure for line scan signals to galvos x, y and line and frame triggers
    """
    __slots__ = ["line_trig_sig", "frame_trig_sig", "x_sig", "y_sig"]

    def __init__(self):
        self.line_trig_sig = None
        self.frame_trig_sig = None
        self.x_sig = None
        self.y_sig = None


class MotionOCTFrame(Frame):
    __slots__ = ["b", "mot", "spec"]


class MotionOCTMessage(Message):
    __slots__ = ["instruction", "niconfig", "scansig", "aline_size", "alines_per_buffer", "number_of_blines",
                 "apod_window", "lambda_data", "phase_corr_apod_filter_2d", "zstart", "npeak", "nrepeat", "time_lags"]

    def __init__(self, instr):
        # Sub structures
        self.instruction = instr
        self.niconfig = NIConfig()
        self.scansig = ScanSignals()

        # OCT Processing parameters
        self.aline_size = None
        self.alines_per_buffer = None
        self.number_of_blines = None
        self.apod_window = None
        self.lambda_data = None

        # Motion processing parameters
        self.phase_corr_apod_filter_2d = None
        self.zstart = None
        self.npeak = None
        self.nrepeat = None
        self.time_lags = None


# -- Process managers --------------------------------------------------------------------------------------------------


class HardwareControlProcess(mp.Process):
    """
    A process spawned via fork which manages the hardware and buffers its outputs and inputs. Pops from msg_queue and
    behaves accordingly via virtual methods recv_msg and acquire_frame
    """

    def __init__(self, exit_event, frame_buffer_max=1024, poll_time_sec=float(1 / 60)):
        """
        :param stop_event: The manager of the process should set this event to stop the dispose of it safely
        :param frame_buffer_max: The capacity of data buffer. A "frame" is the atomic unit of information that can be
        grabbed from the controller, realistically including structured image data as well as analysis or metadata
        :param poll_time_sec: Interval in seconds between polls to message buffer
        """
        super(HardwareControlProcess, self).__init__()

        # Public
        self.msg_queue = mp.Queue()
        self.frame_queue = mp.Queue(maxsize=frame_buffer_max)
        self.status = mp.Value('i', READY)
        self.dropped_frames = mp.Value('i', 0)

        # Protected
        self._poll_timer = None
        self._exit_event = exit_event
        self._poll_time_sec = poll_time_sec
        self._total_grabbed = 0

    def _poll_msg_buffer(self):
        print(type(self).__name__ + ': polling msg buffer...')
        try:
            self.recv_msg(self.msg_queue.get(timeout=False))
        except Empty:
            print(type(self).__name__ + ': no messages...')

    def send_msg(self, msg):
        self.msg_queue.put(msg, timeout=False)

    def get_frame_buffer(self):
        return self.frame_queue

    def run(self):
        """
        Overrides multiprocessing.Process run method with calls to setup, and then repeatedly polls msg_queue--
        passing any msgs to recv_msg-- and calls acquire_frame. Stops running after call to close when stop_event is
        set.
        """
        print(type(self).__name__ + ': running... PID', os.getpid())
        self._poll_timer = RepeatTimer(self._poll_time_sec, self._poll_msg_buffer)
        self._poll_timer.start()
        self.setup()
        self.status.value = READY
        while not self._exit_event.is_set():
            # -- begin optimized ------------------------------------------------------------------------------------
            # print(type(self).__name__+': status', str(self.status.value))
            if self.status.value is ACQUIRING:
                f = self.acquire_frame()
                if f is not None:
                    try:
                        self.frame_queue.put(f, timeout=False)
                        self._total_grabbed += 1
                        print(type(self).__name__ + ': enqueued a frame...')
                    except Full:
                        print(type(self).__name__ + ': dropped a frame! Buffer was full...')
                        with self.dropped_frames.get_lock():
                            self.dropped_frames.value += 1
            # -- end optimized --------------------------------------------------------------------------------------
        self._poll_timer.cancel()
        self.status.value = EXITING
        self.close()

    def setup(self):
        """
        Called at beginning of run, used to initialize memory and parameters to assist in acquiring frames and msgs
        :return: None
        """

    def close(self):
        """
        Called at end of run once stop_event is set.
        :return: None
        """

    def recv_msg(self, msg):
        """
        :param msg: a Message object
        """
        raise NotImplementedError

    def acquire_frame(self):
        """
        :return: a Frame object
        """
        raise NotImplementedError


class MotionOCTControlProcess(HardwareControlProcess):
    def __init__(self, exit_event, frame_buffer_max=1024, poll_time_sec=float(1 / 2)):

        super(MotionOCTControlProcess, self).__init__(exit_event, frame_buffer_max, poll_time_sec)

        # Properties
        self._time_started = None

        # DAQmx interface handles
        self._scan_task = None
        self._scan_writer = None
        self._scan_timing = None

        # Params
        self.niconfig = None
        self.scansig = None
        self.aline_size = None
        self.alines_per_buffer = None
        self.number_of_blines = None
        self.apod_window = None
        self.lambda_data = None
        self.phase_corr_apod_filter_2d = None
        self.zstart = None
        self.npeak = None
        self.nrepeat = None
        self.time_lags = None

        # Derived params
        self.bwidth = None
        self.halfwidth = None
        self.time_lags_n = None

    def close(self):
        PyIMAQ.imgStopAcq()
        PyIMAQ.imgClose()
        self._scan_task.stop()
        self._scan_task.close()

    def recv_msg(self, message):
        if isinstance(message, MotionOCTMessage):
            if message.instruction is INSTR_OPEN:
                print("MotionOCTController: recv INSTR_OPEN")
                # Copy all params from message
                self.niconfig = message.niconfig
                self.scansig = message.scansig
                self.aline_size = message.aline_size
                self.alines_per_buffer = message.alines_per_buffer
                self.number_of_blines = message.number_of_blines
                self.apod_window = message.apod_window
                self.lambda_data = message.lambda_data
                self.phase_corr_apod_filter_2d = message.phase_corr_apod_filter_2d.astype(np.float32)
                self.zstart = message.zstart
                self.npeak = message.npeak
                self.nrepeat = message.nrepeat
                self.time_lags = np.array(message.time_lags).astype(np.int32)
                # Derived params
                self.bwidth = int(self.alines_per_buffer / self.number_of_blines)
                self.halfwidth = int(self.aline_size / 2 + 1)
                self.time_lags_n = len(self.time_lags)

                # Initialize DAQmx interface
                self._scan_task = nidaqmx.Task()
                self._scan_timing = nidaqmx.task.Timing(self._scan_task)
                dac_name = self.niconfig.dac_device
                dac_ao_channel_ids = [
                    dac_name + '/' + self.niconfig.line_trig_ch,
                    dac_name + '/' + self.niconfig.frame_trig_ch,
                    dac_name + '/' + self.niconfig.x_ch,
                    dac_name + '/' + self.niconfig.y_ch
                ]
                for ch_name in dac_ao_channel_ids:
                    print("MotionOCTController: adding DAQmx ao channel", ch_name)
                    self._scan_task.ao_channels.add_ao_voltage_chan(ch_name)

                self._scan_task.timing.cfg_samp_clk_timing(self.niconfig.dac_rate,  # TODO move?
                                                           source="",
                                                           active_edge=Edge.RISING,
                                                           sample_mode=AcquisitionType.CONTINUOUS)
                self._scan_writer = AnalogMultiChannelWriter(self._scan_task.out_stream)

                print("MotionOCTController: initialized DAQmx interface...")
                # Initialize IMAQ interface
                PyIMAQ.imgOpen(self.niconfig.cam_device)
                PyIMAQ.imgConfigTrigBufferWithTTL1(timeout=1000)
                PyIMAQ.imgSetAttributeROI(0, 0, self.alines_per_buffer, self.aline_size)
                PyIMAQ.imgInitBuffer(self.niconfig.number_of_imaq_buffers)
                print("MotionOCTController: initialized IMAQ interface...")

                # Initialize FAST OCT interface
                PyIMAQ.octPlan(apod=self.apod_window)
                # PyIMAQ.octPlan()

                PyIMAQ.octMotionPlan(self.zstart, self.npeak, self.number_of_blines, repeat=self.nrepeat,
                                     apod_filter_2d=self.phase_corr_apod_filter_2d)
                print("MotionOCTController: planned Fast SD-OCT processing...")

                # Change status to ready
                self.status.value = READY

            elif message.instruction is INSTR_CLOSE:
                print("MotionOCTController: recv INSTR_CLOSE")
                self._exit_event.set()
            elif message.instruction is INSTR_START_ACQ:
                print("MotionOCTController: recv INSTR_START_ACQ")
                if self.status.value is READY:
                    self._buffer_scan_samples()  # Send new scan signals to the hardware
                    self._time_started = time.time()
                    PyIMAQ.imgStartAcq()
                    self._scan_task.start()
                    self.status.value = ACQUIRING
                else:
                    return
            elif message.instruction is INSTR_STOP_ACQ:
                print("MotionOCTController: recv INSTR_STOP_ACQ")
                self.status.value = READY
                PyIMAQ.imgStopAcq()
                self._scan_task.stop()
            elif message.instruction is INSTR_UPDATE_ACQ:
                print("MotionOCTController: recv INSTR_UPDATE_ACQ")
                self._buffer_scan_samples()
                # TODO allow for other parameters to be updated during acquisition?
            else:
                return
        else:
            return

    def acquire_frame(self):
        # Allocate frame memory
        f = MotionOCTFrame()
        bflat = np.zeros(self.halfwidth * self.alines_per_buffer, dtype=np.complex64)
        f.b = np.zeros([self.halfwidth, self.alines_per_buffer], dtype=np.complex64)
        f.mot = np.zeros([3 * self.number_of_blines], dtype=np.float32)
        print("MotionOCTController: allocated frame memory...")

        # Poll the fast OCT interface
        print('calling octMotion', self.time_lags_n, self.time_lags, f.mot)
        rval = PyIMAQ.octMotion(self.time_lags_n, self.time_lags, f.mot)
        if rval is 0:
            print("MotionOCTController: acquired motion successfully...")
            PyIMAQ.octCopyReferenceFrame(bflat)
            f.spec = PyIMAQ.octCopySpectrum(1)
            # Reshape b, ROI
            for i in range(self.alines_per_buffer):
                f.b[:, i] = bflat[self.halfwidth * i:self.halfwidth * i + self.halfwidth]
            f.b = np.roll(f.b, self.alines_per_buffer - 2, axis=1)  # TODO this is a visual bandaid on a problem!
            f.b = np.abs(f.b)
            f.b = f.b[self.zstart:self.zstart + self.bwidth, :]
            return f
        else:
            print("MotionOCTController: failed to acquire a frame...")
            return None

    def _buffer_scan_samples(self):
        if isinstance(self.scansig, ScanSignals):
            self._scan_writer.write_many_sample(np.array([self.scansig.line_trig_sig,  # Note that gain is pre-applied
                                                          self.scansig.frame_trig_sig,
                                                          self.scansig.x_sig,
                                                          self.scansig.y_sig]))
        else:
            return


# -- Controller objects ------------------------------------------------------------------------------------------------


class OCTController:
    """
    Base class for interfacing GUI with OCT hardware. Interaction with hardware such as frame grabs and galvo signal
    buffering and generation are carried out by another a Process. This class is responsible for managing the
    parameters of the current acquisition and for communicating with the Process class.
    """

    def __init__(self, line_size=2048):
        self._control_process = None  # type: HardwareControlProcess
        self._control_process_msg_queue = None  # type: mp.Queue
        self._scan_pattern = None  # type: LineScanPattern
        self._line_size = line_size

    # Required public methods

    def initialize(self):
        """
        Allocate memory and initialize interfaces and buffers
        :return: 0 on success
        """
        raise NotImplementedError()

    def close(self):
        """
        Dispose of interfaces and buffers
        :return: 0 on success
        """
        raise NotImplementedError()

    def start_scan(self):
        """
        Begin filling the frame buffer with the current acquisition parameters
        :return: 0 on success
        """
        raise NotImplementedError()

    def stop_scan(self):
        """
        Pause the acquisition and empty the frame buffer
        :return: 0 on success
        """
        raise NotImplementedError()

    def update_scan(self):
        """
        Change some parameters of the acquisiton. Critically, this is called while the scan is ongoing, and updates
        to the scan take place immediately. Not all parameters can be changed without restarting the scan. If the scan
        pattern is to be updated, this method should take the mutable paremeters and calls its generate method.
        :return: 0 on success
        """

    def get_frame(self):
        """
        Pop (FIFO) a frame from the frame buffer. Only called during a scan
        :return: 0 on success, -1 if the controller is not currently scanning
        """

    def set_scanpattern(self, scan_pattern):
        raise NotImplementedError()
        """
        Takes a scan pattern object and configures scanning system with it.
        """
