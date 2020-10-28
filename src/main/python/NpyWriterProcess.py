# -*- coding: utf-8 -*-
"""
Created on Mon Oct 19 14:18:42 2020

@author: OCT
"""
import time
import os
import numpy as np
import multiprocessing as mp
from queue import Empty, Full
from ctypes import c_wchar_p, c_char_p
from pathlib import Path
from npy_append_array import NpyAppendArray

ZPAD = 3  # Number of zeros to use for file names. 3 means max of 999 files

class NpyWriterProcess(mp.Process):
    
    def __init__(self, exit_event):
        super(NpyWriterProcess, self).__init__()

        self.name = "NumPy Writer Process"

        self._fpath = None
        self._max_bytes = 0
        self._cfg_queue = mp.Queue(maxsize=8)  # Buffer of new params

        self._buffer = mp.Queue()  # Buffer of stuff to write

        self._exit_event = exit_event

    def get_buffer(self):
        return self._buffer

    def enqueue(self, f):
        self._buffer.put(f)

    def config(self, fpath, maxsize):
        self._cfg_queue.put([str(fpath), int(maxsize)], timeout=False)

    def run(self):
        """
        Runs in new process on start()
        """
        print('NpyWriterProcess: running PID', os.getpid())

        open_file = None  # Current open file
        open_path = None  # Current open file path
        open_index = 0
        written = 0  # Bytes written to the current file

        while not self._exit_event.is_set() and os.getppid() is not 1:
            if written > self._max_bytes:
                # Start new file if max size is reached
                written = 0
                open_index += 1
                open_path = open_path[0:-ZPAD] + str(open_index).zfill(ZPAD)
                print("NpyWriterProcess: saving", open_path)
                if os.path.exists(open_path):
                    os.remove(open_path)  # Delete the file if it exists
                open_file = NpyAppendArray(open_path + ".npy")
            try:
                # Poll config buffer
                cfg = self._cfg_queue.get(timeout=False)
                open_index = 0
                self._fpath = cfg[0] + '_' + str(open_index).zfill(ZPAD)
                self._max_bytes = cfg[1]
                written = 0

                # Open new file
                open_path = self._fpath
                print("NpyWriterProcess: saving", open_path)
                if os.path.exists(open_path):
                    os.remove(open_path)  # Delete the file if it exists
                open_file = NpyAppendArray(open_path + ".npy")

            except Empty:
                pass
            if open_file is not None:
                try:
                    f = self._buffer.get(timeout=1)
                    written += f.nbytes
                    open_file.append(f)
                except Empty:
                    pass
        del open_file  # Close the last file
        print("NpyWriteProcess: exiting...")
