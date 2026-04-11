import sys
from os import makedirs
from os.path import join as pathjoin, basename, splitext, exists

from bbk.dbg import dbg
from bbk.env import get_env

from bbk.task.task import Task
from bbk.task.result import TaskResult
from bbk.task.continuationtask import ContinuationTask

from bbk.verdict import Verdict
from bbk.compiler import CompilationUnit

from bbk.timeout import TimeoutWatchdog

from bbk.workflow import Workflow

from bbk.tools.cpachecker import CPAchecker

from bbk.utils import find_file_in_dirs

from .flows import CPAcheckerVerifier, KLEEVerifier
from .splitflows import create_verifier, task_result_is_conclusive


def _has_error(result):
    return isinstance(result.output, list) and any(
        (isinstance(r, Verdict) and r.is_error()) for r in result.output
    )


class ReduceBackup(ContinuationTask):

    def __init__(self, verifier, reduced_task, backup_task):
        super().__init__(
            verifier(reduced_task),
            name = "VerifyWithBackup"
        )

        self._verifier     = verifier
        self._reduced_task = reduced_task
        self._backup_task  = backup_task

    def continuation(self, result):
        if result.is_done() and task_result_is_conclusive(result):
            return result
        
        return TaskResult("REPLACE_TASK", output = self._verifier(self._backup_task))


class ReduceAndVerify(ContinuationTask):

    def __init__(self, verifier, input_file, condition, fold = False):
        options = [
            "-residualProgramGenerator" if not fold else "-residualProgramGenerator-CFA",
            "-setprop", 
            f"AssumptionAutomaton.cpa.automaton.inputFile={condition}"
        ]

        super().__init__(
            CPAchecker(
                [input_file],
                [verifier.property],
                args = options,
            ),
            name = "Reducer"
        )

        self._verifier = verifier
        self._input_file = input_file
        self._condition = condition


    def continuation(self, result):
        if not result.is_done(): 
            return result
        
        residual = None

        if not _has_error(result):
            residual = find_file_in_dirs(
                "residual_program.c",
                [f"{get_env().workdir}/output/"]
            )

        if residual is None or not exists(residual):
            dbg(f"Generator did not produce a residual program at path {self._condition}. Continue with verifier.")
            return TaskResult("REPLACE_TASK", output = self._verifier(self._input_file))

        residual = CompilationUnit(residual)
        return TaskResult("REPLACE_TASK", output = self._verifier(residual))


class VerifyAndReduce(ContinuationTask):

    def __init__(self, generator_task, input_file, verifier = None, fold = False):
        super().__init__(
            generator_task,
            name = "VerifyAndReduce"
        )

        self._input_file = input_file
        self._verifier   = verifier
        self._fold       = fold

    def continuation(self, result):
        if result.is_done() and task_result_is_conclusive(result):
            return result
        
        if self._verifier is None: return result
        
        condition = find_file_in_dirs(
            "AssumptionAutomaton.txt",
            [f"{get_env().workdir}/output/"]
        )

        if condition is None or not exists(condition):
            dbg(f"Generator did not produce a condition at path {condition}. Continue with verifier.")
            return TaskResult("REPLACE_TASK", output = self._verifier(self._input_file))
        
        return TaskResult("REPLACE_TASK", output = ReduceAndVerify(
            self._verifier, self._input_file, condition, fold = self._fold
        ))
        


class VerifyAndCMC(ContinuationTask):

    def __init__(self, generator_task, input_file, verifier = None):
        super().__init__(
            generator_task,
            name = "VerifyAndReduce"
        )

        self._input_file = input_file
        self._verifier   = verifier

    def continuation(self, result):
        if result.is_done() and task_result_is_conclusive(result):
            return result
        
        if self._verifier is None: return result
        
        condition = find_file_in_dirs(
            "AssumptionAutomaton.txt",
            [f"{get_env().workdir}/output/"]
        )

        if condition is None or not exists(condition):
            dbg(f"Generator did not produce a condition at path {condition}. Continue with verifier.")
            return TaskResult("REPLACE_TASK", output = self._verifier(self._input_file))
        
        self._verifier.args.X += [
            "-setprop", 
            f"AssumptionAutomaton.cpa.automaton.inputFile={condition}"
        ]
        
        self._verifier.config_name += "-USE-CMC"
        return TaskResult("REPLACE_TASK", output = self._verifier(self._input_file))
        


def create_generator(name, args, properties, timeout = None):
    
    if name == "se":
        return CPAcheckerVerifier(
            "symbolicExecution-CMC", args, properties[0], timeout=timeout
        )
    
    if name == "senoa":
        return CPAcheckerVerifier(
            "symbolicExecution", args, properties[0], timeout=timeout
        )
    
    if name == "klee":
        return KLEEVerifier(args, properties[0], timeout = timeout)

    if name == "bmc":
        return CPAcheckerVerifier(
            "bmc-CMC", args, properties[0], timeout=timeout
        )

    if name == "ki":
        return CPAcheckerVerifier(
            "kInduction-CMC", args, properties[0], timeout=timeout
        )

    if name == "pa":
        return CPAcheckerVerifier(
            "predicateAnalysis-CMC", args, properties[0], timeout=timeout
        )

    raise NotImplementedError("Unknown CMC verifier: %s" % name)



def create_init_task(programs, args, cmdargs, properties):
    """
    The workflow for Bubaak that runs reducer based CMC
    """

    assert len(programs) == 1, "Multiple programs not supported by the splitter"

    workflow = cmdargs.workflow
    _, generator, *verifier = workflow.split("-")

    assert len(verifier) >= 1, "Need at least one verifier"

    fold = False
    direct = False
    if len(verifier) == 2:
        assert verifier[0] in ["fold", "direct"]
        fold = verifier[0] == "fold"
        direct = verifier[0] == "direct"
        verifier = verifier[1]
    else:
        assert len(verifier) == 1
        verifier = verifier[0]

    if generator != "id":

        if cmdargs.timeout is None or cmdargs.timeout >= 800:
            gen_timeout = 100
        else:
            gen_timeout = max(4, int(0.1 * cmdargs.timeout))

        generator = create_generator(
            generator, cmdargs, properties, timeout = gen_timeout
        )(programs[0])
    
    else:
        generator = None

    if verifier == "id":
        verifier = None
    else:
        verifier = create_verifier(
            verifier, cmdargs, properties, timeout = None
        )

    assert generator is not None or verifier is not None, "Either generator or verifier has to be existent"

    if generator is not None:
        if direct:
            verify_reduce = VerifyAndCMC(generator, programs[0], verifier)
        else:
            verify_reduce = VerifyAndReduce(generator, programs[0], verifier, fold = fold)
    else:
        verify_reduce = verifier(programs[0])

    return TimeoutWatchdog(
        verify_reduce, cmdargs.timeout
    )


def workflow(programs, args, cmdargs, properties):
    return Workflow([create_init_task(programs, args, cmdargs, properties)])
