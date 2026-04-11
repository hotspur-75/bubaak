from bbk.compiler import CompilerTask
from bbk.task import AggregateTask
from bbk.timeout import TimeoutWatchdog

from bbk.tools.klee import Klee, get_klee_args
from bbk.tools.slowbeast import SlowBeast, get_slowbeast_args
from bbk.workflow import Workflow

from .default import VerificationData


class ExtendedKLEE(AggregateTask):
    def __init__(self, bitcode, data):
        klee = Klee(
            bitcode,
            data.properties,
            get_klee_args(data.args, data.properties) + ["-write-paths"],
        )

        super().__init__([klee])

        self._results = []

        def handle_unsupported_fp(event, line):
            if b"Dumped unfinished path to file:" in line:
                path = line.decode("utf-8").split()[7]
                self.add_subtask(
                    SlowBeast(
                        bitcode,
                        data.properties,
                        get_slowbeast_args(data.args, data.properties)
                        + ["-replay-path", path],
                    )
                )

        klee.add_event_listener("line-stderr", self, handle_unsupported_fp)
        klee.add_ignore_stderr_lines(
            "silently concretizing (reason: floating point) expression"
        )

    def aggregate(self, task, result):
        self._results.append(result)

        if not result.is_done():
            return result

        if not result.output[0].is_correct():
            return result

        return None

    def finish(self):
        if self._aggregated_result:
            return self._aggregated_result

        # all aggregated results are 'correct'
        return self._results[0]


def create_init_task(programs, args, cmdargs, properties):
    """
    The workflow for Bubaak where SlowBeast handles killed states from KLEE
    """

    if any((not p.is_unreach() for p in properties)):
        raise NotImplementedError("Only reachability supported ATM")

    init = CompilerTask(programs, include_dirs=cmdargs.I) >> (
        lambda result: ExtendedKLEE(
            result.output, VerificationData(programs, properties, cmdargs)
        )
    )

    return TimeoutWatchdog(init, cmdargs.timeout)


def workflow(programs, args, properties):
    return Workflow([create_init_task(programs, args, cmdargs, properties)])
