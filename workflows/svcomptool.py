from bbk.timeout import TimeoutWatchdog

from bbk.tools.svcomptool import GetSVCompTool, SVCompTool
from bbk.workflow import Workflow


def create_init_task(programs, args, cmdargs, properties):
    """
    The workflow for Bubaak that runs a sv-comp tool.
    The tool is downloaded if used for the first time.
    """

    return TimeoutWatchdog(
        # the continuation with SVCompTool is wrapped into lambda function because
        # we need the constructor be called only after GetSVCompTool finished.
        # Wrapping the constructor in the lambda function does precisely that.
        # XXX: forbid continuations without lambda? Because this can lead to
        # a really hard-to-find bugs
        GetSVCompTool(args[0], properties, year=None)
        >> (
            lambda r: SVCompTool(
                args[0], programs, properties, args[1:], cmdargs.timeout
            )
            if r.is_done()
            else r
        ),
        timeout=cmdargs.timeout,
    )


def workflow(programs, args, cmdargs, properties):
    return Workflow([create_init_task(programs, args, cmdargs, properties)])
