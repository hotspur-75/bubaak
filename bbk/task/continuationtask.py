from . import AggregateTask
from .result import TaskResult
from .task import Task

def wrap_continuation(other):
    assert callable(other) or isinstance(other, (Task, TaskResult)), other
    if callable(other):
         
        def new_continuation(result):
            res = other(result)
            if isinstance(res, Task):
                return TaskResult("REPLACE_TASK", res)
            if not isinstance(res, TaskResult):
                return TaskResult("DONE", result)
            return res

    elif isinstance(other, TaskResult):

        def new_continuation(result):
            return other if result.is_done() else result
    
    else:

        def new_continuation(result):
            return TaskResult("REPLACE_TASK", other) if result.is_done() else result

    return new_continuation
        

class ContinuationTask(Task):
    """
    Run a single task that is taken as an argument and
    process its result. This is handy when we do not want
    to inherit from that task (e.g., when the task is determined
    during runtime).

    """

    def __init__(self, task, continuation=None, timeout=None, name=None, descr=None):
        super().__init__(timeout=timeout, name=name, descr=descr)
        assert task
        self._task = task 
        self._task_result = None

        # overwrite the continuation method if given on commandline
        if continuation is not None:
            self.continuation = wrap_continuation(continuation)

        task.add_event_listener("finish", self, self._task_finished)

    def task(self):
        return self._task

    def continuation(self, result):
        """
        Override this method to take action after
        the task finishes. This is an alternative
        to passing a function as `continuation` parameter
        in the __init__ method.
        """
        raise RuntimeError("Must be overriden or given as a parameter")

    def execute(self):
        self._task.set_parent(self)
        self._workflow.add_task(self._task)

    def replace_subtask(self, task, new_task):
        # I am not so a big fan of this since I believe 
        #    that the workflow should handle this.
        # However, then the workflow has to handle listeners which is also not right.
        # This seems to work for now.

        assert task is self._task, (task, self._task)
        assert task.parent() is self

        self._task        = new_task
        new_task.add_event_listener("finish", self, self._task_finished)
        self.execute()

    def _task_finished(self, event, task, result):
        assert event == "finish", event
        assert task is self._task, (task, self._task)
        assert self._task_result is None, f"Already got a result: {self._task_result}"
        if result and result.is_replace_task(): return # The task is not really finished
        self._task_result = result
    
    def finish(self):
        """
        Run `continuation` and overwrite the result with the result
        from the continuation.
        """
        # do not proceed if the task was stopped
        if self.was_stopped():
            return TaskResult("ERROR", descr="Continuation task has been stopped")

        assert isinstance(self._task_result, TaskResult)
        result = self.continuation(self._task_result)

        self._task = None
        return result

    def is_running(self):
        return self._start_time is not None and self._task_result is None

    def stop(self):
        if self._task:
            self._task.stop()

    def kill(self):
        # this is partially because of debugging
        assert self.was_stopped(), "Task must be stopped before trying to kill it"
        if self._task:
            self._task.kill()

    def is_done(self):
        return self._task_result is not None
