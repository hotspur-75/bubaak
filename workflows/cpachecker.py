from bbk.compiler import CompilerTask
from bbk.timeout import TimeoutWatchdog
from bbk.dbg import dbg

from bbk.tools.cpachecker import CPAchecker
from bbk.workflow import Workflow


def create_init_task(programs, args, cmdargs, properties):
    """
    The workflow for Bubaak that runs only CPAchecker
    """

    the_args = args + cmdargs.X.copy()
    if not the_args:
        dbg("No configuration specified for CPAchecker, using predicate analysis")
        the_args.append("-predicateAnalysis")
    return TimeoutWatchdog(CPAchecker(programs, properties, the_args), cmdargs.timeout)


def workflow(programs, args, cmdargs, properties):
    return Workflow([create_init_task(programs, args, cmdargs, properties)])
