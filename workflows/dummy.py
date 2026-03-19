import sys
from os import makedirs
from os.path import join as pathjoin, basename, splitext

from bbk.compiler import CompilerTask, CompilationUnit
from bbk.dbg import dbg
from bbk.dbg import print_stderr
from bbk.env import get_env
from bbk.task.aggregatetask import AggregateTask
from bbk.task.continuationtask import ContinuationTask
from bbk.task.result import TaskResult
from bbk.tools.klee import Klee, get_klee_args
from bbk.tools.slowbeast import SlowBeast, get_slowbeast_args
from bbk.workflow import Workflow
from bbk.verdict import Verdict

from svcomp.witness_to_harness import convert_executable_witness_to_harness
from .default import CompileAndCheck
from .default import found_bug, task_result_is_conclusive

from .flows.verifiers import KLEEVerifier, SlowBeastVerifier
from .flows.split import DynamicSplittingVerifier, naive_merge

from .flows.workstealing import WorkStealingComposition

# Split verifier ------------------------------------------------------

class CompileAndRunKLEE(ContinuationTask):
    
    def __init__(self, inputfile, args, properties, check = False, timeout = None, name = None):
        if check:
            compiler_task = CompileAndCheck([inputfile], properties)
        else:
            compiler_task = CompilerTask([inputfile])

        super().__init__(compiler_task, name=name or "CompileAndRunKLEE")
        self._args = args
        self._properties = properties
        self._inputfile = inputfile
        self._timeout   = timeout
    
    def continuation(self, result):
        if not result.is_done():
            return TaskResult("ERROR", output=result, descr="Compiling {self._inputfile} failed")

        return TaskResult("REPLACE_TASK",
                          Klee(result.output, 
                               self._properties, 
                               get_klee_args(self._args, self._properties), 
                               timeout=self._timeout))


def create_split_verifier(splitv, split_config):
    if splitv == "klee":
        kwargs = {"args": split_config["args"], 
                  "properties": split_config["properties"],
                  "timeout": split_config["split_verifier_timeout"]}
        return (lambda inputfile: CompileAndRunKLEE(inputfile, **kwargs))
    
    raise ValueError("Unknown split verifier: %s" % splitv)

# Strong verifier ------------------------------------------------------------

class CompileAndRunSlowBeast(ContinuationTask):
    def __init__(self, inputfile, args, properties, handle_floats=False, timeout=None):
        super().__init__(
            CompileAndCheck([inputfile], properties, include_dirs=args.I),
            name="CompileAndRunSlowBeast",
        )
        self._args = args
        self._properties = properties
        self._inputfile = inputfile
        self._timeout = timeout
        self._handle_floats = handle_floats
    
    def continuation(self, result):

        if not result.is_done():
            return TaskResult(
                "ERROR", output=result, descr="Compiling {self._inputfile} failed"
            )

        if found_bug(result):
            return result

        properties = self._properties

        if self._handle_floats:
            args = get_slowbeast_args(self._args, properties)
            name = "sb-se"
        else:
            args = get_slowbeast_args(self._args, properties, ["-bself"])
            name = "sb-bself"
        
        if any((p.is_no_signed_overflow() for p in properties)) or any(
            (p.is_valid_deref() for p in properties)
        ):
            # we need to recompile the input file, because KLEE used sanitizers
            task = ContinuationTask(
                CompileAndCheck(
                    [self._inputfile], properties, include_dirs=self._args.I
                ),
                continuation=lambda result: TaskResult(
                    "REPLACE_TASK",
                    SlowBeast(
                        result.output,
                        properties,
                        args=args,
                        name=name,
                        timeout=self._timeout,
                    ),
                )
                # Compilation succeeded and bug was not found, so start verifiers
                if result.is_done() else result,
                descr="Compile and Start Slowbeast",
            )
        else:
            task = SlowBeast(
                result.output, properties, args=args, name=name, timeout=self._timeout
            )

        return TaskResult("REPLACE_TASK", task)
    

class ComposeResults(AggregateTask):
    def __init__(self, subtasks, name="ComposeResults"):
        super().__init__(subtasks, name=name)
        self._splits_num = len(subtasks)
        self._results = []

    def aggregate(self, task, result):
        if not result.is_done():
            # some split failed verification, return immediately
            return result

        if any((r.is_incorrect() for r in result.output)):
            result.task = task
            return result

        self._results.extend(result.output)

        self._splits_num -= 1
        if self._splits_num == 0:
            return TaskResult("DONE", output=self._results)

        # no result yet
        return None


def create_task(programs, args, cmdargs, properties):
    """
    The default workflow for Bubaak. The initial task is to compile the
    programs into LLVM and then run LEE and SlowBeast in parallel on the
    compiled program.
    """

    if args:
        dbg("default takes no arguments, ignoring them")

    klee = KLEEVerifier(
        cmdargs, properties[0], timeout = 4
    )

    split_verifier = DynamicSplittingVerifier(klee, max_height=3)

    sb = SlowBeastVerifier(cmdargs, properties[0], timeout = 10)
    ws = WorkStealingComposition([sb])

    def read_output(result):
        print(result)
        return result.verdicts()
    
    return split_verifier(programs[0]) >> ws >> read_output


def workflow(programs, args, cmdargs, properties):
    return Workflow([create_task(programs, args, cmdargs, properties)])
