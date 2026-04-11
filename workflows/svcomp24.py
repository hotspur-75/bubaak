from tempfile import mktemp

from bbk.compiler import CompilerTask, CompilationOptions
from bbk.env import get_env
from bbk.task.aggregatetask import AggregateTask
from bbk.task.result import TaskResult
from bbk.timeout import TimeoutWatchdog
from bbk.tools.klee import Klee, get_klee_args
from bbk.tools.slowbeast import SlowBeast, get_slowbeast_args
from bbk.verdict import Verdict
from bbk.workflow import Workflow


def found_bug(result):
    return isinstance(result.output, list) and any(
        (isinstance(r, Verdict) and r.is_incorrect()) for r in result.output
    )


def task_result_is_conclusive(result):
    return result.is_done() and (
        any((r.is_incorrect() for r in result.output))
        or all((r.is_correct() for r in result.output))
    )


class VerificationData:
    def __init__(self, inputs, properties, args):
        self.inputs = inputs
        self.properties = properties
        self.args = args


class CooperativeSeBself(AggregateTask):
    """
    Start SE and BSELF in SlowBeast in parallel with cooperation.
    """

    def __init__(self, bitcode, data):
        super().__init__([], name="CoopSlowBeast")
        self._bitcode = bitcode
        self.data = data

    def execute(self):
        chan = mktemp(dir=get_env().workdir, prefix="chan-")

        self.add_subtask(
            SlowBeast(
                self._bitcode,
                self.data.properties,
                get_slowbeast_args(
                    self.data.args, self.data.properties, [f"-coop-channel=w:{chan}"]
                ),
            )
        )

        self.add_subtask(
            SlowBeast(
                self._bitcode,
                self.data.properties,
                get_slowbeast_args(
                    self.data.args,
                    self.data.properties,
                    ["-bself", f"-coop-channel=r:{chan}"],
                ),
            )
        )

    def aggregate(self, task, result):
        if task_result_is_conclusive(result):
            return result
        return None

    def finish(self):
        result = self.result()
        if result is None:
            return TaskResult(
                "DONE",
                output=[Verdict(Verdict.UNKNOWN, None, "No config got a result")],
            )
        return result


class CompileAndCheck(CompilerTask):
    def __init__(self, inputs, properties, include_dirs=None, options=None):
        options = options or CompilationOptions()
        if any((p.is_no_signed_overflow() for p in properties)):
            options._sanitize.append("ubsan")
        if any((p.is_valid_deref() for p in properties)):
            options._sanitize.append("asan")

        super().__init__(inputs, options=options, include_dirs=include_dirs)
        self._properties = properties

    def finish(self):
        result = super().finish()
        warnings = self.warnings()
        if warnings:
            overflow = self._properties.get("no-signed-overflow")
            if overflow:
                for line in warnings:
                    if "warning: overflow in expression;" in line or (
                        "implicit conversion" in line
                        and "changes value from" in line
                        and "to 'float'" not in line
                    ):
                        # This is a work-around for the problem that the translation to LLVM looses
                        # some information -- in this case, an overflow is detected by clang, reported,
                        # but LLVM is generated without this overflow
                        return TaskResult(
                            "DONE",
                            [
                                Verdict(
                                    Verdict.INCORRECT,
                                    overflow,
                                    "Signed overflow detected",
                                )
                            ],
                        )

        return result


class StartTerminationVerification(AggregateTask):
    """
    Start KLEE and SlowBeast in parallel for verifying termination
    """

    def __init__(self, bitcode, data):
        super().__init__([], name="StartTerminationVerification")
        self._bitcode = bitcode
        self.data = data

    def execute(self):
        self.add_subtask(
            Klee(
                self._bitcode,
                self.data.properties,
                get_klee_args(self.data.args, self.data.properties),
            )
        )

        self.add_subtask(
            SlowBeast(
                self._bitcode,
                self.data.properties,
                get_slowbeast_args(self.data.args, self.data.properties),
            )
        )

    def aggregate(self, task, result):
        if task_result_is_conclusive(result):
            return result
        return None


class StartVerificationKlee(Klee):
    def __init__(self, bitcode, data):
        to = data.args.timeout
        if to is not None and to > 0:
            klee_timeout = max(int(to / 3), 10)
        else:
            klee_timeout = 123

        super().__init__(
            bitcode,
            data.properties,
            get_klee_args(data.args, data.properties),
            timeout=klee_timeout,
        )
        self._bitcode = bitcode
        self.data = data

    def klee_failed_on_floats(self):
        for r in self.parser().killed_paths():
            if "silently concretizing (reason: floating point)" in r.info():
                return True
        return False

    def finish(self):
        result = super().finish()
        if task_result_is_conclusive(result):
            return result

        properties = self.data.properties

        if self.klee_failed_on_floats():
            args = get_slowbeast_args(self.data.args, properties)
            task = SlowBeast(
                self._bitcode, self.data.properties, args=args, name="sb-se"
            )
            return TaskResult("REPLACE_TASK", task)

        if any((p.is_no_signed_overflow() for p in properties)) or any(
            (p.is_valid_deref() for p in properties)
        ):
            # we need to recompile the input file, because KLEE used sanitizers
            task = CompileAndCheck(
                self.data.inputs,
                self.data.properties,
                include_dirs=self.data.args.I,
            ) >> (
                lambda result: CooperativeSeBself(
                    result.output,
                    self.data,
                )
            )
        else:
            task = CooperativeSeBself(
                self._bitcode,
                self.data,
            )

        return TaskResult("REPLACE_TASK", task)


def create_task(programs, args, cmdargs, properties):
    """
    The default workflow for Bubaak. The initial task is to compile the
    programs into LLVM and then run LEE and SlowBeast in parallel on the
    compiled program.
    """

    if len(properties) == 1 and properties[0].is_termination():
        init = CompilerTask(programs, include_dirs=cmdargs.I) >> (
            lambda result: StartTerminationVerification(
                result.output, VerificationData(programs, properties, cmdargs)
            )
        )
    else:
        init = CompileAndCheck(programs, properties, include_dirs=cmdargs.I) >> (
            lambda result: TaskResult(
                "REPLACE_TASK",
                StartVerificationKlee(
                    result.output, VerificationData(programs, properties, cmdargs)
                ),
            )
            if result.is_done() and not found_bug(result)
            else result
        )

    return TimeoutWatchdog(init, cmdargs.timeout)


def workflow(programs, args, cmdargs, properties):
    return Workflow([create_task(programs, args, cmdargs, properties)])
