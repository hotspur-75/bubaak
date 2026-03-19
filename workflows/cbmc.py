from bbk.compiler import CompilerTask
from bbk.timeout import TimeoutWatchdog

from bbk.tools.cbmc import Cbmc
from bbk.workflow import Workflow


def create_init_task(programs, args, cmdargs, properties):
    """
    The workflow for Bubaak that runs only CBMC
    """

    return TimeoutWatchdog(
        Cbmc(programs, properties, args + cmdargs.X), cmdargs.timeout
    )


def workflow(programs, args, cmdargs, properties):
    return Workflow([create_init_task(programs, args, cmdargs, properties)])
