from select import POLLIN, POLLHUP
from time import clock_gettime, CLOCK_REALTIME

from bbk.dbg import dbg, dbgv, print_stdout, wdbg
from bbk.env import get_env
from bbk.poller import Poller
from bbk.task.processtask import ProcessTask
from bbk.task.result import TaskResult

STOPPED_TASK_WAIT_BEFORE_KILLING = 10  # seconds
POLL_TIMEOUT_INIT = 2  # miliseconds


def is_final_result(task, result):
    return task.parent() is None and not result.is_continuation()


def finish_fd_task(fd, poller, workflow):
    # Tool finishes/crash... Read all what is left on
    # this fd and the other fd of the task
    task, _ = poller.get_data(fd)
    assert fd in task.fds()
    for n, task_fd in enumerate(task.fds()):
        tmp_stream = "stdout" if n == 0 else "stderr"
        while read_fd(task_fd, task, tmp_stream):
            pass

    # remove fds from poller
    workflow.remove_task(task)
    result = workflow.finish_task(task)
    if result:
        if is_final_result(task, result):
            # we've got a result, stop all workflow and
            # return the result
            workflow.stop()
            return result, task
        print_stdout("----------------")
        print_stdout(f"{task.name()} finished with the following results:")
        # result.describe()
    return None


def read_fd(fd, task, stream):
    assert isinstance(task, ProcessTask), (task, type(task))
    # TODO: set stream based on the FD (compare it to the data in task)
    if stream == "stdout":
        fl = task.proc().stdout
        assert fd == task.proc().stdout.fileno()
        is_stdout = True
    elif stream == "stderr":
        fl = task.proc().stderr
        assert fd == task.proc().stderr.fileno()
        is_stdout = False
    else:
        raise RuntimeError(f"Unknown stream: {stream}")

    assert fd == fl.fileno()

    partial_lines = task._partial_output
    line = fl.readline()
    if line:
        ln = line.decode("utf-8", "ignore")
        if ln[-1] not in ("\n", "\r"):
            partial_lines.setdefault(stream, []).append(line)
            return False
        elif stream in partial_lines:
            assert "\n" in ln, ln
            line = (b"".join(partial_lines[stream])) + line
            ln = line.decode("utf-8")
            # reset the partial output
            task._partial_output = {}

        if __debug__:
            header = f"{task.name()}[{task.proc().pid}]/{1 if is_stdout else 2}"
            dbgv(
                f"  ##[{header:20}] '{ln.rstrip()}'",
                color="gray",
            )
        if is_stdout:
            task.emit_event("line-stdout", line)
        else:
            task.emit_event("line-stderr", line)
        return True
    return False


class Workflow:
    instance = 0

    def __init__(self, tasks=None):
        Workflow.instance += 1

        self._id = Workflow.instance
        self._poller = Poller()
        self._running = False
        self._tasks = []
        self._fd_tasks = []
        self._no_fd_tasks = []
        self._new_tasks = []
        # debugging log
        self._log = None

        for task in tasks or ():
            self.add_task(task)

    def get_id(self):
        return self._id

    def add_task(self, task):
        wdbg().msg(f"{self}.add_task({task})")

        task.set_workflow(self)
        self._new_tasks.append(task)

        if task.is_program():
            self._fd_tasks.append(task)
        else:
            self._no_fd_tasks.append(task)

    def _start_task(self, task):
        wdbg().msg(f"{self}.start_task({task})")
        task.execute()
        if not task.is_running() and not task.is_done():
            wdbg().msg(f"Failed starting task {task}")
            raise RuntimeError(f"Failed starting task {task}")

        self._tasks.append(task)
        if task.is_program():
            self._poller.add_task(task)

    def has_work(self):
        return len(self._tasks) > 0 or len(self._new_tasks) > 0

    def run(self):
        self._running = True
        return self._main_loop()

    def _main_loop(self):
        wdbg().msg(f"{self}._main_loop()")
        poller = self._poller
        poll_timeout = 10

        assert self._running
        has_work = self.has_work
        assert has_work(), "Has no work when starting"

        last_current_time = clock_gettime(CLOCK_REALTIME)

        while has_work():
            current_time = clock_gettime(CLOCK_REALTIME)

            if current_time - last_current_time > 20:
                dbg(
                    f"## @{current_time - get_env().start_time:.3f} has {len(self._tasks)} tasks running..."
                )
                if __debug__:
                    for task in self._tasks:
                        dbg(f"##   -> {task}")
                last_current_time = current_time

            if self._new_tasks:
                new_tasks = self._new_tasks
                self._new_tasks = []
                for task in new_tasks:
                    self._start_task(task)

                # something happend, re-set poll_timeout
                poll_timeout = POLL_TIMEOUT_INIT

            ### -----------------------------------------------------
            ### check non-fd tasks
            ### -----------------------------------------------------
            for task in self._no_fd_tasks:
                if task.is_done():
                    result = self.finish_task(task)
                    self.remove_task(task)
                    if result and is_final_result(task, result):
                        self.stop()
                        return result, task

                    # something happend, re-set poll_timeout
                    poll_timeout = POLL_TIMEOUT_INIT

            ### -----------------------------------------------------
            ### check fd tasks
            ### -----------------------------------------------------
            poll_fds = poller.poll(timeout=poll_timeout)
            poll_timeout = 2 * poll_timeout if poll_timeout < 300 else 333

            for fd, ev in poll_fds:
                # something happend, re-set poll_timeout
                poll_timeout = POLL_TIMEOUT_INIT

                if ev & POLLIN:
                    # There is somthing on the output of the tool
                    read_fd(fd, *poller.get_data(fd))
                if ev == POLLHUP:
                    results = finish_fd_task(fd, poller, self)
                    if results is None:
                        break
                    return results

            ### -----------------------------------------------------
            ### check timeout-ed tasks
            ### -----------------------------------------------------
            for task, timeout in self.get_timeouted_tasks(current_time):
                if task.was_stopped():
                    # the task has been already stopped
                    continue

                print_stdout(f"Tool '{task.name()}' reached timeout {timeout}s")
                self.stop_task(task)
                # Fake the result in case it takes more time to stop the task.
                # This way its listeners can continue and do not need to wait
                # until the `stop` method (over which  we have no control anyway)
                # does its job
                self._finish_task(task, TaskResult("TIMEOUT"))
                if not has_work():
                    # return timeout as the last task reached timeout
                    # this is mainly for debugging, without this, we just return 'unknown'
                    return TaskResult("TIMEOUT"), None

            ### -----------------------------------------------------
            ### check stopped tasks
            ### -----------------------------------------------------
            for task in self._tasks:
                if task.was_stopped():
                    # Does it take too long to stop the task?
                    if (
                        current_time - task.stopped_time()
                        > STOPPED_TASK_WAIT_BEFORE_KILLING
                    ):
                        # Kill and remove the task for good
                        dbg(f"Stopping task {task} takes too long, killing it")
                        self.stop_task(task)
                        self.kill_task(task)
                        self._finish_task(task, TaskResult("KILLED"))
                        #self.remove_task(task)

        assert not self._new_tasks
        return None, None

    def remove_task(self, task):
        """
        Remove the task from the poller and other data structures
        """
        wdbg().msg(f"{self}.remove_task({task})")

        assert task in self._tasks, (task, self._tasks)
        if task.is_program():
            self._poller.remove_task(task)
            self._fd_tasks.remove(task)
        else:
            self._no_fd_tasks.remove(task)
        self._tasks.remove(task)

    def finish_task(self, task):
        result = task.finish()
        assert result is not None
        return self._finish_task(task, result)

    def _finish_task(self, task, result):
        wdbg().result(task, result)

        if is_final_result(task, result):
            return result

        # We do not want to spawn new tasks if a task was stopped
        if result.is_continuation() and task.was_stopped():
            return None

        if result.is_replace_task():
            new_task = result.output
            if task.parent():
                task.parent().replace_subtask(task, new_task)
            else:
                self.add_task(new_task)
            wdbg().replaces(new_task, task)
            # this task is not finished yet
            return None

        if result.is_new_tasks():
            for new_task in result.output:
                self.add_task(new_task)

        return None

    def stop_task(self, task):
        """
        Stop the task.

        Do not remove it, it will be done later by the main loop.
        """
        wdbg().msg(f"{self}.stop_task({task})")

        assert task in self._tasks
        task.stop()
        assert task.was_stopped(), task

    def kill_task(self, task):
        """
        Kill the task. It is assumed that stop_task() was called first,
        that is that fds have been already removed from the poller.
        """
        wdbg().msg(f"{self}.kill_task({task})")
        assert task.was_stopped(), task
        task.kill()

    def stop(self, exclude=None):
        """Stop all tasks"""

        for task in (t for t in self._tasks.copy() if t not in (exclude or ())):
            self.stop_task(task)

    def kill(self, exclude=None):
        """Kill all tasks"""

        for task in (t for t in self._tasks if t not in (exclude or ())):
            self.kill_task(task)

    def get_timeouted_tasks(self, current_time):
        ret = []
        for task in self._tasks:
            assert task.start_time() > 0, task.start_time()
            to = task.timeout()
            if to is not None and (current_time - task.start_time() > to):
                ret.append((task, task.timeout()))
        return ret

    def cleanup(self):
        wdbg().msg(f"Cleanup workflow {self}")
        # clean-up also still running tools if there are some
        # -- but assert that there should not be any
        for t in self._tasks:
            self.stop_task(t)
            self.kill_task(t)
            t.cleanup()

        if self._log:
            self._log.write("\n}")
            self._log.close()

    def __repr__(self):
        return f"Workflow-{self._id}"
