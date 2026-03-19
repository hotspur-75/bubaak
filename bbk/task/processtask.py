from signal import SIGINT, SIGKILL
from subprocess import TimeoutExpired

from .task import Task
from .result import TaskResult

from bbk.dbg import dbg, print_stdout
from bbk.env import get_env
from bbk.utils import _popen


class ProcessResult:
    def __init__(self, retval, sigval=None):
        self.retval = retval
        self.signal = sigval


class ProcessTask(Task):
    def __init__(self, cmd=None, timeout=None, name=None, envir=None, cwd=None):
        super().__init__(timeout=timeout, name=name)
        self._env = get_env()
        self._proc = None
        # Here we store the `ProcessResult` in the default
        # implementation of `finish` method.
        self._retval = None
        # environment variables
        self._environ = envir
        self._cwd = cwd
        self._cmd = cmd

        # while reading the output of the tool, we might not read whole lines
        # every time. Keep the partial output here. The key is either "stdout"
        # or "stderr"
        self._partial_output = {}

    def env(self):
        return self._env

    def name(self):
        return self._name or str(self)

    def cmd(self):
        """
        The user can adjust what process is executed by setting self._cmd or by overwriting this method.

        Returns: A list that can be passed to Popen.
        """
        return self._cmd

    def set_cmd(self, cmd):
        self._cmd = cmd

    def is_done(self):
        return self._retval is not None

    def proc(self):
        return self._proc

    def retval(self):
        return self._retval

    def fds(self):
        """Get stdout and stderr (in this order) filedescriptors"""
        assert self.proc(), "Process is not running"
        return self.proc().stdout.fileno(), self.proc().stderr.fileno()

    def cleanup(self):
        pass

    def execute(self):
        cmd = self.cmd()
        assert isinstance(cmd, list), cmd
        assert all(
            (isinstance(x, str) for x in cmd)
        ), f"cmd is not a list of strings: {cmd}"

        print_stdout(
            f"## Run {self.name()} [time: {self.start_time() - self.env().start_time}] with timeout {self.timeout()}",
            color="cyan",
        )
        print_stdout("#", " ".join(cmd))
        self._popen(cmd, self._environ, self._cwd)

    def finish(self):
        """
        The default implementation of `finish` method.
        """
        dbg(f"## End {self.name()}", color="cyan")

        if self._proc is None:
            return TaskResult("ERROR", descr="Process failed starting")

        retval = self._proc.wait()
        self._retval = ProcessResult(retval)
        self._proc = None
        if retval == 0:
            return TaskResult("DONE")
        return TaskResult("ERROR")

    def _popen(self, cmd, env, cwd=None):
        self._proc = _popen(cmd, env=env, cwd=cwd)

    def stop(self):
        if self._proc:
            self._proc.send_signal(SIGINT)

    def kill(self):
        if self._proc:
            self._proc.terminate()
            self._proc.kill()
            # sometimes proc.kill() does not work :-/
            self._proc.send_signal(SIGKILL)

    def wait_for_finish(self, timeout=None):
        """
        Wait until the task finishes -- this blocks!

        This method can be used to synchronously wait until the task finishes.
        It should be used only in exceptional situations, with care, and ideally with a timeout.
        In the best case, you do not need to use this method. Where it make sense is, for example,
        when a task is stopped and it make take a few more miliseconds to actually finish
        and dump some files, then wait for the files.

        Params:
          timeout: None or float, timeout in miliseconds
        """
        if self._proc is None:
            return True

        if self._proc.poll() is not None:
            return True

        try:
            self._proc.wait(timeout)
            return True
        except TimeoutExpired:
            return False

    def is_running(self):
        return self._proc is not None

    def is_program(self):
        """
        Returns:

        True if this task is running a subprocess.
        (We need to know so that we can monitor its stdout and stderr).
        """
        return True
