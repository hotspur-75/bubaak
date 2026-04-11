from .task import Task
from .aggregatetask import AggregateTask
from .result import TaskResult
from .continuationtask import ContinuationTask

# There is currently no nicer way (except for merging all tasks into one file)
Task.__rshift__ = lambda self, other: ContinuationTask(self, continuation=other)