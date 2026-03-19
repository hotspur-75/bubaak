class TaskResult:
    """
    The result of executing a Task.
    """

    # These are reserved results
    RESULTS = [
        # spawn new tasks in the current workflow,
        # they are independent of any other task
        # (unless some relations are explicitely set)
        "NEW_TASKS",
        # Replace the currently ending task with a new task.
        # Any listener of the finishing task is re-connected
        # to the new task, so it is assumed that the new
        # task "finishes" the job of the old task
        # and returns results in the same format
        "REPLACE_TASK",
        # task is done and there were no problems
        # in _executing_ the task (the result of the task still
        # may represent an error found in a program)
        "DONE",
        # an error was met during executing the task
        # (e.g., the process failed starting or executing)
        "ERROR",
        # the task timeout-ed
        "TIMEOUT",
        # the task has been stopped
        "STOPPED",
    ]

    def __init__(self, status, output=None, descr=None, task=None):
        self.status = status
        self.output = output
        self.description = descr
        # if the task is an aggregate task, we want to know what subtask
        # gave the result and it is stored here
        self.task = task

    def is_done(self):
        return self.status.startswith("DONE")

    def is_error(self):
        return self.status.startswith("ERROR")

    def is_timeout(self):
        return self.status.startswith("TIMEOUT")

    def is_stopped(self):
        return self.status == "STOPPED"

    def is_new_tasks(self):
        return self.status == "NEW_TASKS"

    def is_replace_task(self):
        return self.status == "REPLACE_TASK"

    def is_continuation(self):
        """Return true if this result asks for running another tasks"""
        return self.is_new_tasks() or self.is_replace_task()

    def __repr__(self):
        return f"TaskResult({self.status}, descr={self.description}, output={self.output}, task={self.task})"
