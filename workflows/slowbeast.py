from tempfile import mktemp

from bbk.env import get_env
from bbk.task.aggregatetask import AggregateTask
from bbk.task.result import TaskResult
from bbk.timeout import TimeoutWatchdog
from bbk.tools.slowbeast import SlowBeast, get_slowbeast_args
from bbk.workflow import Workflow


def task_result_is_conclusive(result):
    return result.is_done() and (
        any((r.is_incorrect() for r in result.output))
        or all((r.is_correct() for r in result.output))
    )


from .default import CompileAndCheck, VerificationData, task_result_is_conclusive


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


def create_task(programs, args, cmdargs, properties):
    """
    The default workflow for Bubaak. The initial task is to compile the
    programs into LLVM and then run LEE and SlowBeast in parallel on the
    compiled program.
    """

    if "cooperative" in args:
        task = CompileAndCheck(programs, properties, include_dirs=cmdargs.I) >> (
            lambda result: CooperativeSeBself(
                result.output, VerificationData(programs, properties, cmdargs)
            )
        )
    else:
        task = CompileAndCheck(programs, properties, include_dirs=cmdargs.I) >> (
            lambda result: SlowBeast(
                result.output,
                properties,
                args=get_slowbeast_args(cmdargs, properties, args),
                name="SlowBeast",
            )
        )

    return TimeoutWatchdog(task, cmdargs.timeout)


def workflow(programs, args, cmdargs, properties):
    return Workflow([create_task(programs, args, cmdargs, properties)])
