from time import clock_gettime, CLOCK_REALTIME

from bbk.dbg import wdbg
from bbk.task.result import TaskResult


def finish_wrapper(task, finish_method):
    def new_finish():
        wdbg().msg(f"Finishing task {task} ({task.descr()})")

        task._finish_time = clock_gettime(CLOCK_REALTIME)
        result = finish_method()
        task._result = result

        task.emit_event("finish", task, result)

        return result

    return new_finish


def wrap_methods(task):
    """
    Wrap methods of a Task instance.

    We want to track things like start and stop time (and those are
    important, we need to track them due to timeouts and so on.).
    But to keep it simple, we want the user just to define `execute`,
    and `finish`, etc. without explicitly doing this logging.
    So we wrap the methods with another code during Task's
    initialization.
    """
    task_stop = task.stop
    task_exec = task.execute

    def new_stop():
        task.set_stopped_time(clock_gettime(CLOCK_REALTIME))
        task.emit_event("stop")
        return task_stop()

    def new_execute():
        # do not proceed if the task was stopped in between queeing and starting
        if task.was_stopped():
            wdbg().msg(f"NOT Starting STOPPED task {task} ({task.descr()})")
            return

        wdbg().msg(f"Starting task {task} ({task.descr()})")
        task._start_time = clock_gettime(CLOCK_REALTIME)
        task_exec()

        task.emit_event("execute", task)

    task.stop = new_stop
    task.execute = new_execute
    task.finish = finish_wrapper(task, task.finish)


class Task:
    """
    Base class for tasks of a workflow.

    Every task is assigned to exactly one aggregator task
    that waits for its results and processes them.
    """

    def __init__(self, timeout=None, name=None, descr=None):
        self._name = name
        self._description = descr
        self._workflow = None
        self._start_time = None
        self._finish_time = None
        self._timeout = timeout
        # Stopping a time can take some time (if that is a process).
        # We remember when we tried to stop it and if it is not stopped
        # after some time, we kill it.
        self._stopped_time = None
        # here we store the result of the task so that it is accessible
        # even later after the call to `finish()`
        self._result = None
        # This flag is set to true when the task is being tearing down
        self._tearing_down = False

        # listening tasks that wait for the notification
        # about events that occur while executing the tasks.
        # We always support 'execute' and 'finish' events.
        self._events_callbacks = {}
        # the revers relation to _events_callbacks
        self._listens_to = {}
        # some tasks may be in parent-child relationship.
        # If a task is a child of some other task, its result
        # is not considered to be a result of the Workflow it runs
        # in. Final results are yield only by tasks without parents.
        self._parent = None
        self._unwrapped_finish = self.finish
        # self._original_finish = self.finish

        wrap_methods(self)

    def execute(self):
        """
        Execute this task. The result of executing this task must be stored in the object
        and returned by the `finish` method. It can be also later accessible by the `result` method.

        To store the result, there's the attribute _result that is returned by the default
        implementation of the method `result`. It is up to the implementation if it stores
        the result into `_result` or if it overrides the `result` method.
        """
        raise NotImplementedError(f"Must be overriden by the class: {type(self)}")

    def finish(self):
        """
        This method is called when the task finishes.
        """
        raise NotImplementedError(f"Must be overriden by the class: {type(self)}")

    def events_callbacks(self) -> dict:
        return self._events_callbacks

    def listens_to(self) -> dict:
        return self._listens_to

    def add_event_listener(self, event: str, task, callback) -> None:
        """
        Add listener to this task for event `event`. Whenever the event
        is triggered, `callback` is called (with parameters specific
        for that event).

        \param event:    string describing the event
        \param task:     the task that listens to the event
        \param callback: the callback to call when event is triggered
        """
        assert isinstance(event, str), (event, type(event))
        assert isinstance(task, Task), (task, type(task))

        wdbg().listens_to(task, self)
        self._events_callbacks.setdefault(event, []).append(callback)
        task._listens_to.setdefault(event, []).append(self)
        self._reports_to_workflow = False

    def emit_event(self, event: str, *args, **kwargs):
        """
        Emit callbacks for a given event
        """
        for cb in self.events_callbacks().get(event) or ():
            cb(event, *args, **kwargs)

    def set_parent(self, task):
        assert self._parent is None, "Already have a parent"
        self._parent = task

    def parent(self) -> "Task":
        return self._parent

    def is_program(self):
        """
        Return True if this task is running a program.
        (We need to know so that we can monitor its stdout and stderr).
        """
        return False

    def is_done(self):
        """
        Determine if the task is done.

        This method should be overriden by the child class.

        Returns:
          True if the task is done else False.
        """
        return False

    def is_running(self):
        """
        Determine if the task is running (it has been started and is not done yet).

        This method can be overriden by the child class if the default implementation
        is not enough.

        Returns:
          True if the task is running else False.
        """
        return self.start_time() is not None and not self.is_done()

    def result(self):
        """
        Cached result of the task.

        Once the task is done, the method `finish_task()` is called by the workflow.
        This method calls `task.finish()` which returns a result that we
        remember in `self._result` and it can be later obtained via a call to this method.
        """
        return self._result

    def stop(self):
        """
        Stop the task.

        Must be overriden by child classes to implement stopping
        a running task. Stopping the running task must result
        in `is_done` method returning True in a short amount
        of time.
        """
        raise NotImplementedError("Must be overridden")

    def kill(self):
        """
        Kill the task.

        Must be overriden by child classes to implement killing
        a running task. Killing a task must cause an action
        that makes `is_done` _immediately_ return True if called.
        """
        raise NotImplementedError(f"Must be overridden for class {type(self)}")

    def cleanup(self):
        """
        Cleanup after the task.

        This method is called by the workflow after stopping or killing the task.
        """
        pass

    def name(self):
        return self._name

    def is_aggregate(self):
        return False

    def start_time(self):
        return self._start_time

    def finish_time(self):
        return self._finish_time

    def stopped_time(self):
        return self._stopped_time

    def was_stopped(self):
        return self._stopped_time is not None

    def set_stopped_time(self, tm):
        self._stopped_time = tm

    def set_workflow(self, wf):
        self._workflow = wf

    def workflow(self):
        return self._workflow

    def set_timeout(self, timeout):
        self._timeout = timeout

    def timeout(self):
        return self._timeout

    def set_descr(self, msg):
        self._description = msg

    def descr(self):
        return self._description

    def __repr__(self):
        d = self._description
        if d:
            return f"<{d} 0x{hex(id(self))}>"
        return super().__repr__()
