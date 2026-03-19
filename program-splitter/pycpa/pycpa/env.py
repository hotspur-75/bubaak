from contextlib import contextmanager
from time import clock_gettime, CLOCK_REALTIME


def _current_time():
    return clock_gettime(CLOCK_REALTIME)

class Timer:

    def __init__(self):
        self._handlers = []

    def add_handler(self, handler):
        self._handlers.append(handler)
        
    def tick(self):
        current_time = _current_time()
        
        remove_handlers = []
        for handler in self._handlers: 
            if handler(current_time) == False:
                remove_handlers.append(handler)
        
        for handler in remove_handlers:
            self._handlers.remove(handler)

    def reset(self):
        self._handlers = []


class TimeoutHandler:

    def __init__(self, timeout, start_time = None):
        self._start_time = start_time or _current_time()
        self._timeout = timeout
        self._stop = False
    
    def __call__(self, current_time):
        if self._stop: return False
        elapsed_time = current_time - self._start_time
        if elapsed_time > self._timeout:
            raise TimeoutError(f"Timeout exceeded after {elapsed_time:.3f} seconds")
        
    def stop(self):
        self._stop = True


GLOBAL_TIMER = Timer()

@contextmanager
def global_timeout(timeout):

    try:
        handler = TimeoutHandler(timeout)
        GLOBAL_TIMER.add_handler(handler)
        yield handler
    finally:
        handler.stop()