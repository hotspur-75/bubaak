from bbk.dbg import wdbg
from .task import Task
from .result import TaskResult

class AggregateTask(Task):
    """
    This task spawns one or more subtasks and aggregates results from them.
    It takes their results as they come and decides whether to
    finish or continue the tasks and what to return.
    If the result is REPLACE_TASK, it is done so by the workflow automatically,
    so AggregateTask is something like an envelope for multiple tasks that can
    evolve inside.

    If this task has a timeout and it is passed, the subtasks
    are stopped too.
    TODO: we could do this configurable
    """

    def __init__(
        self, tasks: list, aggregate=None, timeout=None, name=None, descr=None
    ):
        super().__init__(timeout=timeout, name=name, descr=descr)
        if aggregate:
            self.aggregate = aggregate

        self._initial_tasks = tasks
        self._subtasks = []
        self._aggregated_result = None

    def execute(self):
        assert self._workflow is not None
        for task in self._initial_tasks:
            self.add_subtask(task)

    def add_subtask(self, task):
        assert (
            self._aggregated_result is None
        ), f"Adding a subtask {task} to already determined task {self} which already has result: {self._aggregated_result}"
        wdbg().msg(f"{self}.add_subtask({task})", color="blue")
        assert self._workflow is not None

        self._subtasks.append(task)
        task.set_parent(self)
        task.add_event_listener("finish", self, self._subtask_finished)
        self._workflow.add_task(task)

    def _subtask_finished(self, event: str, task, result):
        assert event == "finish", event
        assert isinstance(task, Task), (task, type(task))
        assert isinstance(result, TaskResult), (result, type(result))
        wdbg().msg(f"{self}.subtask_finished({task}, {result})", color="blue")

        self._subtasks.remove(task)

        result = self.subtask_finished(task, result)
        self.emit_event("subtask-finished", task, result)

        # We already have the result, therefore the current call of this method
        # is for the stopped tools, and we want to do nothing,
        # or the result is that the task should be rewritten and therefor
        # we are not done yet (and we will not call aggregate() on this result)
        if self._aggregated_result or result.is_continuation():
            return

        result = self.aggregate(task, result)
        # stop all subtasks if we have a final result
        if result is not None:
            assert isinstance(result, TaskResult), (result, type(result))
            # store into result that `task` is the one that finished
            result.subtask = task
            self._aggregated_result = result
            # stop other subtasks
            self.stop()

    def replace_subtask(self, subtask, new_task):
        # the task has been already removed,
        # so just add a new subtask
        assert subtask not in self._subtasks
        assert subtask.parent() is self
        self.add_subtask(new_task)

    def subtask_finished(self, task, result):
        """
        Child classes can override this method to get notified when a subtask finished
        and to override the result. Whatever this method does, the task is going to be
        removed from the subtasks as it has finished.

        Alternatively one can get notified the standard way by adding an event listener
        to 'subtask-finished(task, result)' event.
        """
        return result

    def finish(self):
        assert self.is_done()
        if self._aggregated_result is None:
            raise RuntimeError(
                f"Aggregation returned None, override this method for class {self} to handle this case"
            )
        return self._aggregated_result

    def is_aggregate(self):
        return True

    def aggregate(self, task, result):
        """
        This method is called whenever a sub-task `task` is finished, and it returns the `result`
        which is other than REPLACE_TASK (in that case the task is replaced and this function
        is not called).

        If this method returns None, nothing happens and the Aggregate task continues.
        If it returns result other than None, it is taken as the final result of the
        Aggregate task.
        The method can also return None all the time, in which case the Aggregation task
        finishes after all subtasks are finished and it is up to the `finish` method to return
        something sensible.
        """
        raise RuntimeError("This method must be overridden or given in __init__")

    def is_running(self):
        return self._aggregated_result is None and self._start_time is not None
    
    def result(self):
        return self._aggregated_result

    def stop(self):
        for task in self._subtasks:
            task.stop()

    def kill(self):
        for task in self._subtasks:
            task.kill()

    def is_done(self):
        # Note that this check could fail in concurrent setup (if we ever
        # do that) as adding a subtask to `self._subtasks` and setting the start time
        # in the wrapper of `execute` is not atomic.
        # But for now, we're fine.
        return not self._subtasks and self._start_time is not None
