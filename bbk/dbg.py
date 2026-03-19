import sys

from bbk.env import get_env

COLORS = {
    "dark_blue": "\033[0;34m",
    "dark_green": "\033[0;32m",
    "cyan": "\033[0;36m",
    "blue": "\033[1;34m",
    "purple": "\033[0;35m",
    "red": "\033[1;31m",
    "wine": "\033[0;31m",
    "green": "\033[1;32m",
    "brown": "\033[0;33m",
    "yellow": "\033[1;33m",
    "white": "\033[1;37m",
    "gray": "\033[0;37m",
    "dark_gray": "\033[1;30m",
    "dark_gray_thin": "\033[38;5;238m",
    "orange": "\033[38;5;214m",
    "orangebg": "\033[1;43m",
    "greenbg": "\033[1;42m",
    "redbg": "\033[1;41m",
    "orangeul": "\033[1;4;33m",
    "greenul": "\033[1;4;32m",
    "redul": "\033[1;4;31m",
    "reset": "\033[0m",
}

_global_prefix = None


def inc_print_indent():
    global _global_prefix
    _global_prefix = "  " + (_global_prefix or "")


def dec_print_indent():
    global _global_prefix
    _global_prefix = _global_prefix[2:]
    if not _global_prefix:
        _global_prefix = None


def print_stream(msg, stream, prefix=None, print_ws="\n", color=None):
    """
    Print message to stderr/stdout

    @ msg      : str    message to print
    @ prefix   : str    prefix for the message
    @ print_nl : bool  print new line after the message
    @ color    : str    color to use when printing, default None
    """

    # don't print color when the output is redirected
    # to a file
    if not stream.isatty():
        color = None

    if color is not None:
        stream.write(COLORS[color.lower()])

    if msg == "":
        return
    if prefix is not None:
        stream.write(prefix)
    if _global_prefix is not None:
        stream.write(_global_prefix)

    stream.write(msg)

    if color is not None:
        stream.write(COLORS["reset"])

    if print_ws:
        stream.write(print_ws)

    stream.flush()


def print_stderr(msg, prefix=None, print_ws="\n", color=None):
    print_stream(msg, sys.stderr, prefix, print_ws, color)


def print_stdout(msg, prefix=None, print_ws="\n", color=None):
    print_stream(msg, sys.stdout, prefix, print_ws, color)


def print_highlight(s, words, prefix=None, stream=sys.stdout):
    """Words: dictionary words -> colors"""
    if prefix:
        print_stream(prefix, print_ws=None, stream=stream)
    for w in s.split():
        c = words.get(w)
        if c:
            print_stream(w, color=c, print_ws=" ", stream=stream)
        else:
            print_stream(w, print_ws=" ", stream=stream)
    stream.write("\n")


_is_debugging = 0
_debugging_prefix = ""


def set_debugging(verbose_lvl=1):
    global _is_debugging
    _is_debugging = verbose_lvl


def unset_debugging():
    global _is_debugging
    _is_debugging = 0


def set_debugging_prefix(prefix=""):
    global _debugging_prefix
    _debugging_prefix = prefix


def get_debugging_prefix():
    global _debugging_prefix
    return _debugging_prefix


def inc_debugging_lvl():
    global _debugging_prefix
    _debugging_prefix = "  " + _debugging_prefix


def dec_debugging_lvl():
    global _debugging_prefix
    if _debugging_prefix.startswith("  "):
        _debugging_prefix = _debugging_prefix[2:]


def dbg(msg, print_ws="\n", color="GRAY", fn=print_stderr):
    if _is_debugging < 1:
        return

    fn(msg, f"{_debugging_prefix}", print_ws, color)


def dbgv(msg, verbose_lvl=2, print_ws="\n", color="GRAY", fn=print_stderr):
    if _is_debugging < verbose_lvl:
        return

    fn(msg, f"{_debugging_prefix}", print_ws, color)


def warn(msg, print_ws="\n", color="BROWN"):
    print_stderr(msg, "WARNING: ", print_ws, color)


class WorkflowDbg:
    """Specialized debugging class for workflows"""

    def __init__(self, args):
        self._args = args
        self._log_path = f"{get_env().workdir}/bubaak-workflows.log"
        log = open(self._log_path, "w")
        log.write(f"digraph Workflows {{\n")
        self._log = log

    def __del__(self):
        if self._log:
            self._log.write("\n}\n")
            self._log.close()
        if self._args.save_files:
            dbgv(f"Workflows log: {self._log_path}")
        del self

    def dbg(self, msg, *args, **kwargs):
        log = self._log
        log.write("  // ")
        log.write(msg)
        log.write("\n")

    def msg(self, msg, *args, **kwargs):
        self.dbg(msg, *args, *kwargs)
        dbgv(msg, verbose_lvl=3, *args, **kwargs)

    def listens_to(self, task1, task2, *args, **kwargs):
        dbgv(f"  {task1} listens to {task2}", verbose_lvl=3, *args, **kwargs)
        self._log.write(f"{task1} -> {task2}\n")

    def replaces(self, task1, task2, *args, **kwargs):
        dbgv(f"  {task1} replaces {task2}", verbose_lvl=3, *args, **kwargs)
        self._log.write(f'{task1} -> {task2}[penwidth=3, color=red,label="replaces"]\n')

    def result(self, task, res, *args, **kwargs):
        dbgv(f"  {task} -> {res}", verbose_lvl=3, *args, **kwargs)
        self._log.write(f'RESULT{id(res)}[shape=underline,label="{res}"]\n')
        self._log.write(f'{task} -> RESULT{id(res)}[color=green,label="result"]\n')


_wdbg = None


def start_workflow_log(args):
    global _wdbg
    _wdbg = WorkflowDbg(args)


def wdbg():
    global _wdbg
    return _wdbg
