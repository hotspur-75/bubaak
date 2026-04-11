""" Tasks for work stealing """
from bbk.dbg import dbg

from os.path import basename

from bbk.task.task import Task
from bbk.task.aggregatetask import AggregateTask
from bbk.task.continuationtask import ContinuationTask

from bbk.compiler import CompilationUnit
from bbk.verdict import Verdict

from bbk.task.result import TaskResult
from .verifiers import SoftwareVerifier, ParallelComposition
from .verifiers import found_bug, task_result_is_conclusive


class WorkStealingComposition(ParallelComposition):

    def __or__(self, other):
        if isinstance(other, ParallelComposition):
            others = other._verifiers
        else:
            others = [other]
        
        return WorkStealingComposition(self._verifiers + others)

    def create_batch(self, inputfiles):
        return WorkStealingAlgorithm(self._verifiers, inputfiles)

    def create_task(self, inputfile: CompilationUnit) -> Task:
        return WorkStealingAlgorithm(self._verifiers, [inputfile])


class WorkStealingResult(TaskResult):

    def verdicts(self):
        return TaskResult("DONE", output = [r.output[0] for r in self.output])


class WorkStealingAlgorithm(AggregateTask):
    
    def __init__(self, verifiers, inputfiles):
        super().__init__([], name="WorkStealingAlgorithm")
        self._tasks = inputfiles
        self._base_verifiers = verifiers
        self._base_verifier_types = [v.name for v in verifiers]

        self._running_verifiers = [None] * len(self._base_verifiers)

        self._running_tasks = [None] * len(self._base_verifiers)
        self._running_alloc = [None] * len(self._tasks)

        self._results = [None] * len(self._tasks)

    def _next_task_for(self, verifier_id):
        # Find open tasks
        for i, _ in enumerate(self._tasks):
            if self._running_alloc[i] is None and self._results[i] is None:
                return i
        
        # Find stealing points
        for i, _ in enumerate(self._tasks):
            if self._results[i] is not None: continue
            running_alloc = self._running_alloc[i]
            running_verifier = self._base_verifier_types[verifier_id]
            if running_verifier in {self._base_verifier_types[vid] for vid in running_alloc}: continue
            return i

        return -1

    def _alloc_task(self, verifier_id, task_id, task):
        self._running_verifiers[verifier_id] = task_id
        self._running_tasks[verifier_id] = task

        if self._running_alloc[task_id] is None:
            self._running_alloc[task_id] = [verifier_id]
        else:
            self._running_alloc[task_id].append(verifier_id)

    def _stop_all_running_tasks(self, task_id):
        for v_id, t_id in enumerate(self._running_verifiers):
            if t_id == task_id:
                self._running_verifiers[v_id] = None
                self._running_tasks[v_id].stop()
                self._running_tasks[v_id] = None
        self._running_alloc[task_id] = None

    def _create_task(self, verifier_id, task_id):
        verifier, task = self._base_verifiers[verifier_id], self._tasks[task_id]
        return verifier(task)
    
    def execute(self):
        for verifier_id, _ in enumerate(self._base_verifiers):
            task_id = self._next_task_for(verifier_id)
            if task_id == -1: continue

            verifier_type = self._base_verifier_types[verifier_id]
            if self._running_alloc[task_id] is not None:
                dbg(f"Start verifier '{verifier_type}' to steal task '{self._tasks[task_id]}'")
            else:
                dbg(f"Start verifier '{verifier_type}' to process task '{self._tasks[task_id]}'")

            task = self._create_task(verifier_id, task_id)
            self._alloc_task(verifier_id, task_id, task)
            self.add_subtask(task)


    def aggregate(self, task, result):
        if not result.is_done() or found_bug(result): return result
        
        # Identify task id (hacky solution for now)
        solver = result.task
        task_id = -1
        target_task = basename(solver._inputs[0])
        for i, task_name in enumerate(self._tasks):
            tname = basename(task_name.path)
            if target_task.startswith(tname):
                task_id = i
        assert task_id != -1, target_task + " is not in " + str(self._tasks)

        self._results[task_id] = result
        self._stop_all_running_tasks(task_id)

        if all(r is not None for r in self._results):
            return WorkStealingResult("DONE", output=self._results, task = self)
        
        self.execute() # Reschedule the remaining tasks
        return None

    def finish(self):
        result = self.result()
        if result is None:
            return WorkStealingResult("DONE", output=[TaskResult("DONE", output=[
                                                        Verdict(Verdict.UNKNOWN, None, "No config got a result")
                                                ])], task = self)
        return result   