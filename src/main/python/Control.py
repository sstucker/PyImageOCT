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
from PyScanPattern.Patterns import Figure8ScanPattern, RasterScanPattern, LineScanPattern
import numpy as np
from queue import Empty, Full
import multiprocessing as mp
from dataclasses import dataclass
import threading
from NpyWriterProcess import *
from SharedCircularBuffer import *

# HardwareControlProcess messages
VOID = 0  # Do nothing
INSTR_OPEN = 1  # Open the hardware interface with the parameters in the msg
INSTR_CLOSE = 2  # Close the hardware interface
INSTR_STOP_ACQ = 3  # Stop the scan if scanning
INSTR_START_ACQ = 4  # Start the scan if ready
INSTR_UPDATE_ACQ = 5  # Update the interface with the parameters in the msg, but not those that require a reopen
INSTR_BEGIN_SAVE = 6  # Start saving to disk
INSTR_END_SAVE = 7  # Stop saving to disk

# HardwareControlProcess states
STATUS_READY = 0  # Interface opened successfully; ready to begin a scan
STATUS_NOT_READY = -2  # No interface exists or interface failed to open
STATUS_ACQUIRING = 1  # The interface is currently busy with a scan
STATUS_EXITING = -1  # The process's lifetime has ended


# -- Data structures ---------------------------------------------------------------------------------------------------

class SlotStruct:

    def get_slots(self):
        return self.__slots__


class Message:
    """
    Information enqueued by parent process of a HardwareControlProcess via the process's msg_queue
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


class WriterConfig(SlotStruct):
    """
    Fast access structure for line scan signals to galvos x, y and line and frame triggers
    """
    __slots__ = ["fpath", "max_bytes", "type"]

    def __init__(self):
        self.fpath = None
        self.max_bytes = 0
        self.type = None


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


class OCTConfig(SlotStruct):
    __slots__ = ["aline_size", "alines_per_buffer", "alines_per_bline", "number_of_blines", "apod_window",
                 "lambda_data", "ztop", "zbottom"]

    def __init__(self):
        self.aline_size = None
        self.alines_per_buffer = None
        self.alines_per_bline = None
        self.number_of_blines = None
        self.apod_window = None
        self.lambda_data = None
        self.ztop = None
        self.zbottom = None


class MotionOCTMessage(Message):

    def __init__(self, instr):
        self.instruction = instr
        self.niconfig = NIConfig()
        self.scansig = ScanSignals()
        self.writer = WriterConfig()
        self.oct = OCTConfig()

        # Motion processing parameters
        self.phase_corr_apod_filter_2d = None
        self.zstart = None
        self.npeak = None
        self.nrepeat = None
        self.time_lags = None


class OCTAMessage(Message):

    def __init__(self, instr):
        self.instruction = instr
        self.niconfig = NIConfig()
        self.scansig = ScanSignals()
        self.oct = OCTConfig()
        self.writer = WriterConfig()


# -- Frames ------------------------------------------------------------------------------------------------------------

class Frame:
    """
    Atomic unit of data allocated by HardwareControlProcess and enqueued
    """
    pass


class MotionOCTFrame(Frame):
    __slots__ = ["b", "mot", "spec"]


class OCTAFrame(Frame):
    __slots__ = ["rr", "raw", "spec"]


# -- Process managers --------------------------------------------------------------------------------------------------

class HardwareControlProcess(mp.Process):
    """
    A process spawned via fork which manages the hardware and buffers its outputs and inputs. Pops from msg_queue and
    behaves accordingly via virtual methods recv_msg and acquire_frame
    """

    def __init__(self, exit_event, frame_buffer_max=32, poll_time_sec=float(1 / 60)):
        """
        :param stop_event: The manager of the process should set this event to stop the dispose of it safely
        :param frame_buffer_max: The capacity of data buffer. A "frame" is the atomic unit of information that can be
        grabbed from the controller, realistically including structured image data as well as analysis or metadata
        :param poll_time_sec: Interval in seconds between polls to message buffer
        """
        super(HardwareControlProcess, self).__init__()

        # Public
        self.msg_queue = mp.Queue()  # Queue for incoming msgs
        self.frame_queue = mp.Queue(maxsize=frame_buffer_max)  # Receptacle for acquired frames, for proc or display
        self.status = mp.Value('i', STATUS_NOT_READY)  # Process status
        self.saving = mp.Value('i', 0)  # If true, frames are enqueued with writer process
        self.dropped_frames = mp.Value('i', 0)

        # Protected
        self._exit_event = exit_event
        self._poll_time_sec = poll_time_sec
        self._frame_buffer_max = frame_buffer_max

        self._total_grabbed = 0  # Total frames grabbed since opening

    def _poll_msg_buffer(self, timeout=None):
        try:
            self.recv_msg(self.msg_queue.get(timeout=timeout))
        except Empty:
            pass

    def send_msg(self, msg):
        self.msg_queue.put(msg, timeout=False)

    def get_frame_buffer(self):
        return self.frame_queue

    def grab_frame(self):
        try:
            return self.frame_queue.get(timeout=None)
        except Empty:
            return None

    def run(self):
        """
        Overrides multiprocessing.Process run method with calls to setup, and then repeatedly polls msg_queue--
        passing any msgs to recv_msg-- and calls acquire_frame. Stops running after call to close when stop_event is
        set.
        """
        print(type(self).__name__ + ': running... PID', os.getpid())
        self._total_grabbed = 0
        self.setup()
        while not self._exit_event.is_set() and os.getppid() is not 1:
            # -- begin optimized ------------------------------------------------------------------------------------
            if self.status.value is STATUS_ACQUIRING:
                self._poll_msg_buffer(timeout=False)
                f = self.acquire_frame()
                if f is not None:
                    try:
                        self.frame_queue.put(f, timeout=False)
                        self._total_grabbed += 1
                    except Full:
                        print(type(self).__name__ + ': dropped a frame! Buffer was full...')
                        with self.dropped_frames.get_lock():
                            self.dropped_frames.value += 1
            else:
                self._poll_msg_buffer(timeout=1)  # Block and wait for messages if not scanning
            # -- end optimized --------------------------------------------------------------------------------------
        self.status.value = STATUS_EXITING
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
        :return: a Frame object or None
        """
        raise NotImplementedError


class NIOCTControlProcess(HardwareControlProcess):

    def __init__(self, exit_event, frame_buffer_max, poll_time_sec):
        super(NIOCTControlProcess, self).__init__(exit_event, frame_buffer_max, poll_time_sec)

        self.name = "OCT Control Process"

        self.niconfig = NIConfig()
        self.scansig = ScanSignals()
        self.oct = OCTConfig()

        # DAQmx interface handles
        self._scan_task = None
        self._scan_writer = None
        self._scan_timing = None

    def _init_hardware_interface(self):
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
            self._scan_task.ao_channels.add_ao_voltage_chan(ch_name)

        self._scan_task.timing.cfg_samp_clk_timing(self.niconfig.dac_rate,  # TODO move?
                                                   source="",
                                                   active_edge=Edge.RISING,
                                                   sample_mode=AcquisitionType.CONTINUOUS)
        self._scan_task.regen_mode = nidaqmx.constants.RegenerationMode.DONT_ALLOW_REGENERATION  # TODO test
        self._scan_writer = AnalogMultiChannelWriter(self._scan_task.out_stream)

        print("NIOCTController: initialized DAQmx interface...")
        # Initialize IMAQ interface
        PyIMAQ.imgOpen(self.niconfig.cam_device)
        PyIMAQ.imgConfigTrigBufferWithTTL1(timeout=1000)
        PyIMAQ.imgSetAttributeROI(0, 0, self.oct.alines_per_buffer, self.oct.aline_size)
        PyIMAQ.imgInitBuffer(self.niconfig.number_of_imaq_buffers)
        print("NIOCTController: initialized IMAQ interface...")

    def _close_hardware_interface(self):
        PyIMAQ.imgStopAcq()
        PyIMAQ.imgClose()
        try:
            self._scan_task.stop()
            self._scan_task.close()
            print("Hardware interface closed.")
        except AttributeError:
            pass


class OCTAControlProcess(NIOCTControlProcess):
    def __init__(self, exit_event, frame_buffer_max=32, poll_time_sec=float(1 / 2)):

        super(NIOCTControlProcess, self).__init__(exit_event, frame_buffer_max, poll_time_sec)

        # Shared buffer
        self.frame_buffer = None  # SharedCircularBuffer

        # Properties
        self.writer = WriterConfig()
        self.halfwidth = None  # Width of the spatial A-line
        self._time_started = None
        self._grabbed = 0
        self._tmp_buffer = None  # Buffer for frame prior to cropping and reshaping
        self.frame_buffer_mode = False  # If True, multiple buffers are used to acquire frame

        self._writer_exit = None
        self._writer_process = None  # Process which saves each grab to disk if the saving flag is True.

    def setup(self):
        self._writer_exit = mp.Event()
        self._writer_process = NpyWriterProcess(self._writer_exit)
        self._writer_process.start()

    def close(self):
        self._close_hardware_interface()
        self._writer_exit.set()
        self._writer_process.join(timeout=0)
        if self._writer_process.is_alive():  # If it's stuck, terminate it
            self._writer_process.terminate()
            print(type(self).__name__ + ': writer terminated!')
        else:
            print(type(self).__name__ + ': writer closed safely.')

    def recv_msg(self, message):
        if isinstance(message, OCTAMessage):
            if message.instruction is INSTR_OPEN:
                print("OCTAControlProcess: recv INSTR_OPEN")
                self._copy_params_from_msg(message)

                if self.oct.alines_per_bline * self.oct.number_of_blines > 0:
                    self.frame_buffer_mode = True
                    frame_size = self.oct.aline_size * self.oct.alines_per_bline
                else:
                    self.frame_buffer_mode = False
                    frame_size = self.oct.aline_size * self.oct.alines_per_bline * self.oct.number_of_blines

                print("OCTAControlProcess: opening hardware interface with buflist trigger mode", self.frame_buffer_mode)
                self._init_hardware_interface()
                if PyIMAQ.imgGetFrameSize() == frame_size:
                    # Initialize FAST OCT interface
                    if self.oct.apod_window is not None:
                        if self.oct.lambda_data is not None:
                            print("OCTAControlProcess: planning Fast SD-OCT processing with interp and apod")
                            PyIMAQ.octPlan(lam=self.oct.lambda_data, apod=self.oct.apod_window)
                        else:
                            print("OCTAControlProcess: planning Fast SD-OCT processing with apod")
                            PyIMAQ.octPlan(apod=self.oct.apod_window)
                    else:
                        print("OCTAControlProcess: planning Fast SD-OCT processing, FFT only")
                        PyIMAQ.octPlan()
                    # Change status to ready
                    self.status.value = STATUS_READY
                else:
                    print(
                        "OCTAControlProcess: failed to open IMAQ interface... the requested buffer size is too large, or the hardware may be in use")

            elif message.instruction is INSTR_CLOSE:
                print("OCTAControlProcess: recv INSTR_CLOSE")
                self.status.value = STATUS_NOT_READY
                self._exit_event.set()
            elif message.instruction is INSTR_START_ACQ:
                print("OCTAControlProcess: recv INSTR_START_ACQ")
                if self.status.value is STATUS_READY:
                    self._buffer_scan_samples()  # Send new scan signals to the hardware
                    self._time_started = time.time()
                    self._grabbed = 0
                    PyIMAQ.imgStartAcq()
                    print("OCTAControlProcess: img start acq")
                    self._scan_task.start()
                    print("OCTAControlProcess: scan task started")
                    self.status.value = STATUS_ACQUIRING
                else:
                    print("OCTAControlProcess: Can't start... not ready")
                    return
            elif message.instruction is INSTR_STOP_ACQ:
                print("OCTAControlProcess: recv INSTR_STOP_ACQ")
                if self.status.value is STATUS_ACQUIRING:
                    PyIMAQ.imgStopAcq()
                    self._scan_task.stop()
                    self.status.value = STATUS_READY
            elif message.instruction is INSTR_UPDATE_ACQ:
                print("OCTAControlProcess: recv INSTR_UPDATE_ACQ")
                if self.status.value is STATUS_ACQUIRING:
                    print("Buffering new scan signals mid-acquisition...")
                    self.scansig = message.scansig
                    self.oct.ztop = message.oct.ztop
                    self.oct.zbottom = message.oct.zbottom
                    self._buffer_scan_samples()
                else:
                    self._copy_params_from_msg(message)
                # TODO allow for other parameters to be updated during acquisition?
            elif message.instruction is INSTR_BEGIN_SAVE:
                print("OCTAControlProcess: recv INSTR_BEGIN_SAVE")
                self.saving.value = 1
                self.writer = message.writer
                print("Writer config", self.writer.fpath, self.writer.max_bytes)
                self._writer_process.config(self.writer.fpath, self.writer.max_bytes)
            elif message.instruction is INSTR_END_SAVE:
                print("OCTAControlProcess: recv INSTR_END_SAVE")
                self.saving.value = 0
            else:
                return
        else:
            return

    def acquire_frame(self):
        # Allocate frame memory
        f = OCTAFrame()
        f.rr = np.empty([self.oct.zbottom - self.oct.ztop, self.oct.alines_per_bline, self.oct.number_of_blines],
                        dtype=np.complex64)
        f.dc = None  # Allocated by IMAQ wrapper
        f.spec = None
        if self.frame_buffer_mode:  # One buffer per B-scan
            for j in range(self.oct.number_of_blines):
                rval = PyIMAQ.octCopyBuffer(j, self._tmp_buffer)
                if rval is not -1:
                    self._grabbed += 1
                    # Reshape and crop frame
                    seek = 0
                    for i in range(self.oct.alines_per_bline):
                        np.copyto(f.rr[:, i, j], self._tmp_buffer[
                                        self.halfwidth * seek:self.halfwidth * seek + self.halfwidth][
                                        self.oct.ztop:self.oct.zbottom])
                        seek += 1
                else:
                    return None
            f.dc = PyIMAQ.octCopyDCSpectrum()
            f.spec = PyIMAQ.octCopySpectrum(0)
            if self.saving.value == 1:
                if len(np.shape(f.rr)) is 3:
                    self._writer_process.enqueue(np.reshape(f.rr, [1, self.oct.zbottom - self.oct.ztop,
                                                                   self.oct.alines_per_bline,
                                                                   self.oct.number_of_blines]))
            return f
        else:  # One buffer per volume
            # Poll the fast OCT interface
            rval = PyIMAQ.octCopyBuffer(self._grabbed, self._tmp_buffer)
            if rval is not -1:
                # Reshape and crop frame
                seek = 0
                for j in range(self.oct.number_of_blines):
                    for i in range(self.oct.alines_per_bline):
                        f.rr[:, i, j] = self._tmp_buffer[self.halfwidth * seek:self.halfwidth * seek + self.halfwidth][
                                        self.oct.ztop:self.oct.zbottom]
                        seek += 1
                f.dc = PyIMAQ.octCopyDCSpectrum()
                f.spec = PyIMAQ.octCopySpectrum(0)
                self._grabbed += 1
                if self.saving.value == 1:
                    if len(np.shape(f.rr)) is 3:
                        self._writer_process.enqueue(np.reshape(f.rr, [1, self.oct.zbottom - self.oct.ztop,
                                                                       self.oct.alines_per_bline,
                                                                       self.oct.number_of_blines]))
                return f
            else:
                return None

    def _copy_params_from_msg(self, message):
        self.niconfig = message.niconfig
        self.scansig = message.scansig
        self.oct = message.oct
        self.writer = message.writer
        self.halfwidth = int(self.oct.aline_size / 2 + 1)
        self._tmp_buffer = np.empty(self.halfwidth * self.oct.alines_per_bline * self.oct.number_of_blines,
                                    dtype=np.complex64)
        print("OCTAControlProcess: all params updated...")

    def _buffer_scan_samples(self):
        if isinstance(self.scansig, ScanSignals):
            self._scan_writer.write_many_sample(np.array([self.scansig.line_trig_sig,  # Note that gain is pre-applied
                                                          self.scansig.frame_trig_sig,
                                                          self.scansig.x_sig,
                                                          self.scansig.y_sig]))
            print("OCTAControlProcess: buffered scan signals")
        else:
            print("OCTAControlProcess: failed to buffer scan sigs: invalid format")
            return

    def _init_hardware_interface(self):
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
            self._scan_task.ao_channels.add_ao_voltage_chan(ch_name)

        self._scan_task.timing.cfg_samp_clk_timing(self.niconfig.dac_rate,  # TODO move?
                                                   source="",
                                                   active_edge=Edge.RISING,
                                                   sample_mode=AcquisitionType.CONTINUOUS)
        self._scan_task.regen_mode = nidaqmx.constants.RegenerationMode.DONT_ALLOW_REGENERATION  # TODO test
        self._scan_writer = AnalogMultiChannelWriter(self._scan_task.out_stream)

        # Initialize IMAQ interface
        PyIMAQ.imgOpen(self.niconfig.cam_device)
        if self.frame_buffer_mode:
            if self.oct.number_of_blines > 512:
                self.niconfig.number_of_imaq_buffers = 1
            elif self.oct.number_of_blines > 256:
                self.niconfig.number_of_imaq_buffers = 1
            elif self.oct.number_of_blines > 128:
                self.niconfig.number_of_imaq_buffers = 1
            PyIMAQ.imgSetAttributeROI(0, 0, self.oct.alines_per_bline, self.oct.aline_size)
            PyIMAQ.imgConfigTrigBufferListWithTTL1(timeout=1000)
            PyIMAQ.imgInitBuffer(self.oct.number_of_blines * self.niconfig.number_of_imaq_buffers)
        else:
            PyIMAQ.imgSetAttributeROI(0, 0, self.oct.alines_per_buffer, self.oct.aline_size)
            PyIMAQ.imgConfigTrigBufferWithTTL1(timeout=1000)
            PyIMAQ.imgInitBuffer(self.niconfig.number_of_imaq_buffers)
