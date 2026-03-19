from bbk.task import AggregateTask


class TimeoutWatchdog(AggregateTask):
    """
    Run a given task with a (global) timeout.
    If the task rewrites itself, the timeout will not reset.
    """

    def __init__(self, task, timeout):
        super().__init__([task], aggregate=lambda t, r: r, timeout=timeout)
