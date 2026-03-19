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
from bbk.task.task import Task
from bbk.timeout import TimeoutWatchdog
from bbk.tools.klee import Klee, get_klee_args
from bbk.tools.slowbeast import SlowBeast, get_slowbeast_args
from bbk.workflow import Workflow
from svcomp.witness_to_harness import convert_executable_witness_to_harness
from .default import CompileAndCheck
from .default import found_bug, task_result_is_conclusive

# import the program splitter
sys.path.insert(0, pathjoin(get_env().srcdir, "program-splitter"))
from split import program_splitter

sys.path.pop(0)

# The depth limit of the splitting tree
SPLIT_DEPTH_LIMIT = 4

# The maximal runtime of the weak verifier (KLEE)
WEAK_VERIFIER_TIMEOUT = 2

# KLEE timeout used in strong backup solver
KLEE_TIMEOUT = 17

# The maximal runtime of the backup (SlowBeast) solver on each split
SPLIT_BACKUP_TIMEOUT = None


def klee_failed_on_floats(result):
    if not result.is_done():
        return False

    for r in result.output:
        if (
            r is not None
            and r.is_unknown()
            and "silently concretizing (reason: floating point)" in r.info()
        ):
            return True
    return False


# Weak verifier -----------------------------


class CompileAndRunWeakVerifier(ContinuationTask):
    def __init__(self, inputfile, args, properties, check=False):
        if check:
            compiler_task = CompileAndCheck([inputfile], properties)
        else:
            compiler_task = CompilerTask([inputfile])

        super().__init__(compiler_task, name="CompileAndRunWeakVerifier")
        self._args = args
        self._properties = properties
        self._inputfile = inputfile

    def continuation(self, result):
        if not result.is_done():
            return TaskResult(
                "ERROR", output=result, descr="Compiling {self._inputfile} failed"
            )

        return TaskResult(
            "REPLACE_TASK",
            Klee(
                result.output,
                self._properties,
                get_klee_args(self._args, self._properties),
                timeout=WEAK_VERIFIER_TIMEOUT,
            ),
        )


# StrongVerifier ---------------------------------


class RunStrongVerifier(ContinuationTask):
    def __init__(self, bitcode, inputfile, args, properties, timeout=None):
        super().__init__(
            Klee(
                bitcode,
                properties,
                get_klee_args(args, properties),
                timeout=KLEE_TIMEOUT,
            ),
            name="RunStrongVerifier",
        )
        self._bitcode = bitcode
        self._args = args
        self._properties = properties
        self._inputfile = inputfile
        self._timeout = timeout - KLEE_TIMEOUT if timeout else None

    def continuation(self, result):
        if task_result_is_conclusive(result):
            return result

        if self._timeout is not None and self._timeout <= 0:
            return result

        properties = self._properties

        if klee_failed_on_floats(result):
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
                        self._bitcode,
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
                self._bitcode, properties, args=args, name=name, timeout=self._timeout
            )

        return TaskResult("REPLACE_TASK", task)


class CompileAndRunStrongVerifier(ContinuationTask):
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
            return TaskResult(
                "REPLACE_TASK",
                SlowBeast(
                    result.output,
                    properties,
                    args=get_slowbeast_args(self._args, properties),
                    name="sb-se",
                    timeout=self._timeout,
                ),
            )

        return TaskResult(
            "REPLACE_TASK",
            RunStrongVerifier(
                result.output,
                self._inputfile,
                self._args,
                self._properties,
                timeout=self._timeout,
            ),
        )


# Splitting ---------------------------------


class SplitTask(Task):
    """
    Split the given input file.
    """

    def __init__(self, input_file, options=None):
        super().__init__(name="splitter", descr=f"Split '{input_file}'")

        self._input = input_file
        self._outputs = None
        self._options = options
        self._outdir = pathjoin(get_env().workdir, "splits/")

        makedirs(self._outdir, exist_ok=True)

    def execute(self):
        filename = basename(self._input.path)
        base, suffix = splitext(filename)

        outputs = [
            pathjoin(self._outdir, f"{base}-l{suffix}"),
            pathjoin(self._outdir, f"{base}-r{suffix}"),
        ]

        try:
            program_splitter(
                self._input.path, outputs[0], outputs[1], allowed_unrolls=2
            )
        except ValueError as e:
            print_stderr(str(e))
            self._result = TaskResult("ERROR", descr=str(e))
            return

        self._outputs = outputs

    def is_done(self):
        return self._outputs or self._result

    def stop(self):
        pass

    def kill(self):
        pass

    def finish(self):
        if self._result:
            return self._result

        if self._outputs:
            return TaskResult(
                "DONE",
                output=[
                    CompilationUnit(path, self._input.lang) for path in self._outputs
                ],
            )

        return TaskResult("ERROR", descr="Splitting produced nothing")


class ComposeSplits(AggregateTask):
    def __init__(self, splits, name="ComposeSplits"):
        super().__init__(splits)
        self._splits_num = len(splits)
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


class SplitAndCont(ContinuationTask):
    def __init__(self, inputfile, args, properties, depth=0):
        super().__init__(SplitTask(inputfile), name="SplitAndCont")
        self._inputfile = inputfile
        self._args = args
        self._properties = properties
        self._depth = depth

    def continuation(self, result):
        if result.is_done():
            return TaskResult(
                "REPLACE_TASK",
                ComposeSplits(
                    [
                        CheckAndSplit(
                            result.output[0],
                            self._args,
                            self._properties,
                            self._depth + 1,
                        ),
                        CheckAndSplit(
                            result.output[1],
                            self._args,
                            self._properties,
                            self._depth + 1,
                        ),
                    ]
                ),
            )

        return TaskResult(
            "REPLACE_TASK",
            CompileAndRunStrongVerifier(
                self._inputfile,
                self._args,
                self._properties,
                timeout=SPLIT_BACKUP_TIMEOUT,
            ),
        )


class CheckAndSplit(ContinuationTask):
    def __init__(self, inputfile, args, properties, depth=0):
        super().__init__(
            CompileAndRunWeakVerifier(inputfile, args, properties, check=depth == 0),
            name="CheckAndSplit",
        )
        self._inputfile = inputfile
        self._args = args
        self._properties = properties
        self._depth = depth

    def continuation(self, result):
        if result.is_error():
            # Failed. Propagate error:
            return result

        if task_result_is_conclusive(result):
            # Success.
            return result

        if self._depth < SPLIT_DEPTH_LIMIT:
            return TaskResult(
                "REPLACE_TASK",
                SplitAndCont(
                    self._inputfile, self._args, self._properties, self._depth
                ),
            )

        return TaskResult(
            "REPLACE_TASK",
            CompileAndRunStrongVerifier(
                self._inputfile,
                self._args,
                self._properties,
                handle_floats=klee_failed_on_floats(result),
                timeout=SPLIT_BACKUP_TIMEOUT,
            ),
        )


# Validation -----------------------------------------------


class ConvertHarnessTask(Task):
    """
    Converts an executable witness to a test harness
    """

    def __init__(self, input_file, witness, options=None):
        super().__init__(
            name="convert-harness",
            descr=f"Convert witness for '{input_file}' to test harness",
        )

        self._input = input_file
        self._witness = witness
        self._output = None
        self._result = None
        self._outdir = pathjoin(get_env().workdir, "harness/")

        makedirs(self._outdir, exist_ok=True)

    def execute(self):
        witness_path = self._witness.path
        harness_path = pathjoin(self._outdir, "harness.c")
        try:
            harness = convert_executable_witness_to_harness(witness_path)

            with open(harness_path, "w") as o:
                o.write(harness)

        except ValueError as e:
            print_stderr(str(e))
            self._result = TaskResult("ERROR", descr=str(e))
            return

        self._output = harness_path

    def is_done(self):
        return self._output or self._result

    def stop(self):
        pass

    def kill(self):
        pass

    def finish(self):
        if self._result:
            return self._result

        if self._output:
            return TaskResult(
                "DONE", output=CompilationUnit(self._output, self._input.lang)
            )

        return TaskResult("ERROR", descr="Conversion to harness produced nothing")


class ValidateViolationResultCont(ContinuationTask):
    def __init__(self, input_file, harness, args, properties):
        super().__init__(
            CompileAndCheck([input_file, harness], properties=properties),
            name="ValidateViolationResultCont",
        )
        self._inputfile = input_file
        self._harness = harness
        self._args = args
        self._properties = properties

    def continuation(self, result):
        if not result.is_done():
            return TaskResult(
                "ERROR",
                output=result,
                descr="Compiling a harness {self._harness} with {self._inputfile} failed",
            )

        return TaskResult(
            "REPLACE_TASK",
            Klee(
                result.output,
                self._properties,
                get_klee_args(self._args, self._properties),
            ),
        )


class ValidateViolationResult(ContinuationTask):
    def __init__(self, inputfile, witness, args, properties):
        super().__init__(
            ConvertHarnessTask(inputfile, witness), name="ValidateViolationResult"
        )
        self._inputfile = inputfile
        self._witness = witness
        self._args = args
        self._properties = properties

    def continuation(self, result):
        if not result.is_done():
            return TaskResult(
                "ERROR",
                output=result,
                descr="Computing a harness for {self._inputfile} failed",
            )

        return TaskResult(
            "REPLACE_TASK",
            ValidateViolationResultCont(
                self._inputfile, result.output, self._args, self._properties
            ),
        )


class CheckAndValidate(ContinuationTask):
    def __init__(self, check_task, inputfile, args, properties):
        super().__init__(check_task, name="CheckAndValidate")

        self._inputfile = inputfile
        self._args = args
        self._properties = properties

    def continuation(self, result):
        if not result.is_done():
            return result

        if any((r.is_incorrect() for r in result.output)):
            violation_result = next(r for r in result.output if r.is_incorrect())

            witness = violation_result.witness()
            if witness is None or witness.path is None:
                # Nothing to do here. Return result
                dbg("No witness provided. Return.")
                return result

            return TaskResult(
                "REPLACE_TASK",
                ValidateViolationResult(
                    self._inputfile, witness, self._args, self._properties
                ),
            )

        return result

    def stop(self):
        super().stop()
        print("STOPPING")


# Backup solver ------------------------------------------------------


# A backup solver for the case that splitting does not work
class CheckWithBackup(ContinuationTask):
    def __init__(self, check_task, backup_task):
        super(CheckWithBackup, self).__init__(check_task, name="CheckWithBackup")
        self._backup = backup_task

    def continuation(self, result):
        # Actually, we want to try again with the backup if we run into an error.
        if result.is_error():
            dbg("Splitter ran into an error. Continue with backup.")
            # return result

        if task_result_is_conclusive(result):
            return result

        return TaskResult("REPLACE_TASK", self._backup)


# Main ----------------------------------------------------------------


def create_task(programs, args, cmdargs, properties):
    """
    The workflow of Bubaak-Split. We start by running a weak
    verifier (KLEE) on the input. If the weak verifier cannot solve
    the problem, we split the task and check the splits.
    We continue the splitting until a fixed splitting depth is reached.
    A backup solver (SlowBeast) is executed on the remaining parts of the program.
    """

    assert len(programs) == 1, "Multiple programs not supported by the splitter"

    backup_task = CompileAndRunStrongVerifier(programs[0], cmdargs, properties)

    check_task = CheckWithBackup(
        CheckAndSplit(programs[0], cmdargs, properties), backup_task
    )

    check_validate_task = CheckAndValidate(check_task, programs[0], cmdargs, properties)

    return TimeoutWatchdog(check_validate_task, cmdargs.timeout)


def workflow(programs, args, cmdargs, properties):
    return workflow_svcomp24(programs, args, cmdargs, properties)


def workflow_svcomp24(programs, args, cmdargs, properties):
    return Workflow([create_task(programs, args, cmdargs, properties)])
