from bbk.compiler import CompilerTask
from bbk.timeout import TimeoutWatchdog

from bbk.tools.klee import Klee, get_klee_args
from bbk.workflow import Workflow


def create_init_task(programs, args, cmdargs, properties):
    """
    The workflow for Bubaak where SlowBeast handles killed states from KLEE
    """

    if any((not p.is_unreach() for p in properties)):
        raise NotImplementedError("Only reachability supported ATM")

    init = CompilerTask(programs, include_dirs=cmdargs.I) >> (
        lambda result: Klee(
            result.output, properties, get_klee_args(cmdargs, properties)
        )
    )

    return TimeoutWatchdog(init, cmdargs.timeout)


def workflow(programs, args, cmdargs, properties):
    return Workflow([create_init_task(programs, args, cmdargs, properties)])
