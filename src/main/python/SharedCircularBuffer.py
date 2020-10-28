import numpy as np
import matplotlib.pyplot as plt
import multiprocessing as mp
import ctypes
import time
import os


class SharedCircularBuffer:

    def __init__(self, N, shape, dtype):
        print("SharedCircularBuffer created!")
        self._n = N  # The number of buffers in the ring
        self._dim = np.array(shape)  # The shape of each buffer
        self._dtype = dtype  # The type of the buffer or int bytes
        self._index = mp.Value('i', 0)  # The index of the next buffer to be put
        self._acquired = mp.Value('i', -1)  # The index of the currently acquired buffer
        self._locks = []  # The locks for each buffer in the ring
        self._ring = []  # Handles for the shared memory for each buffer
        for i in range(N):
            arr = mp.sharedctypes.RawArray(dtype, 2 * int(np.prod(self._dim)))
            rlock = mp.RLock()
            self._ring.append(arr)
            self._locks.append(rlock)

    def put(self, array):
        if self._locks[self._index.value % self._n].acquire(timeout=False):
            print('Process', os.getpid(), 'wrote to buffer', self._index.value % self._n, flush=True)
            np.frombuffer(self._ring[self._index.value % self._n])[:] = array
            self._locks[self._index.value % self._n].release()
        else:
            print('Dropped frame! Process', os.getpid(), 'tried to write to buffer', self._index.value % self._n, 'but could not acquire the lock', flush=True)
            self._acquired.value = -1
            return -1
        with self._index.get_lock():
            self._index.value += 1
        return self._index.value

    def examine(self, index=None):
        if self._acquired.value == -1:  # Only one buffer can be acquired at a time
            if index is None:
                index = (self._index.value - 1) % self._n
            if self._locks[int(index % self._n)].acquire(timeout=False):
                print('Process', os.getpid(), 'is examining', index % self._n, flush=True)
                self._acquired.value = int(index % self._n)
                return np.frombuffer(self._ring[index % self._n]).reshape(self._dim)
            else:
                print('Process', os.getpid(), 'tried to examine buffer', index % self._n, 'but could not acquire the lock', flush=True)
                self._acquired.value = -1
                return None
        else:
            return None

    def release(self):
        print('Process', os.getpid(), 'released buffer', self._acquired.value, flush=True)
        if self._acquired.value is not -1:
            self._locks[self._acquired.value].release()
            self._acquired.value = -1


class Producer(mp.Process):

    def __init__(self, exit_event, buffer, timeout):
        super(Producer, self).__init__()
        self._exit_event = exit_event
        self._buffer = buffer
        self._timeout = timeout

    def run(self):
        print("Producer process started PID", os.getpid())
        while not self._exit_event.is_set():
            a = np.arange(512 * 1 * 1).astype(np.float32)
            self._buffer.put(a)
            time.sleep(self._timeout)


if __name__ == "__main__":

    buffer_type = ctypes.c_float
    circular_buffer = SharedCircularBuffer(1, [512, 1, 1], buffer_type)

    producer_exit = mp.Event()
    producer = Producer(producer_exit, circular_buffer, 0.8)
    producer.start()

    time.sleep(3)  # Wait for the Producer to get going

    for i in range(3):
        a = circular_buffer.examine(i)
        print(a.astype(np.float32))
        circular_buffer.release()
        time.sleep(1)

    producer_exit.set()
