from fcntl import fcntl, F_SETFL, F_GETFL
from os import O_NONBLOCK
from select import poll, POLLIN


def _set_non_blocking(fd):
    fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK)


class Poller:
    def __init__(self):
        self._poll = poll()
        self._monitored_fds = set()
        self._data = {}
        self._tasks = []

    def add_fd(self, fd, data):
        _set_non_blocking(fd)

        self._poll.register(fd, POLLIN)
        self._data[fd] = data
        self._monitored_fds.add(fd)

    def remove_fd(self, fd):
        assert fd in self._monitored_fds
        self._poll.unregister(fd)
        self._monitored_fds.remove(fd)
        self._data[fd] = None

    def remove_all_fds(self):
        for fd in self._monitored_fds.copy():
            self.remove_fd(fd)

    def get_data(self, fd):
        return self._data[fd]

    def add_task(self, task):
        if task.is_program():
            fdout, fderr = task.fds()
            self.add_fd(fdout, (task, "stdout"))
            self.add_fd(fderr, (task, "stderr"))
        else:
            raise NotImplementedError("Other types of tasks not implemented yet")
        self._tasks.append(task)

    def remove_task(self, task):
        if task.is_program():
            fdout, fderr = task.fds()
            self.remove_fd(fdout)
            self.remove_fd(fderr)
        else:
            raise NotImplementedError("Other types of tasks not implemented yet")
        self._tasks.remove(task)

    def poll(self, timeout=None):
        return self._poll.poll(timeout)

    def __bool__(self):
        return len(self._monitored_fds) > 0
