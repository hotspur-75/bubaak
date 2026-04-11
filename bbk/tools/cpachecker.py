import re

from bbk.env import get_env
from bbk.verdict import Verdict
from bbk.tool import Tool
from bbk.tools.tooloutputparser import ToolOutputParser
from bbk.task.result import TaskResult
from bbk.dbg import dbg, dbgv
from bbk.witness import WitnessGraphML, WitnessHarness
from bbk.properties import PropertiesSet
from os.path import exists, splitext

from bbk.utils import find_program

from svcomp.helpers import SVCompProperty


def line_contains(line, *args):
    return any((a in line for a in args))


class CPAcheckerParser(ToolOutputParser):
    def __init__(self, tool, properties, resultsdir):
        super().__init__(f"{resultsdir}/stdout.txt", f"{resultsdir}/stderr.txt")
        self._tool = tool
        self._properties = properties
        self._ignore_lines = []
        self._errors = []
        self_found_error = False
        self._killed_paths = []
        self._warnings = []
        self._retval = None
        self._resultsdir = resultsdir
        self._result = None

        # self._assert_re = re.compile(".*assertion .*: FAILURE")

    def get_prp(self, key):
        for p in self._properties:
            if p.key() == key:
                return p
        return None

    def add_error_or_killed(self, prpkey, line, errortype=None):
        prp = self.get_prp(prpkey)
        if prp:
            # Wait until KLEE finishes and dumps all the files
            # Use a (generous) timeout, if KLEE does not finish until then,
            # just continue. Generating the witness will probably fail in
            # that case.
            if not self._tool.wait_for_finish(5000):
                line += " (XXX: waiting for cpachecker finishing failed)"
            self._errors.append(
                Verdict(
                    Verdict.INCORRECT,
                    prp,
                    info=line,
                    witness=None,
                )
            )
        else:
            dbg("cpachecker found a different error")
            self._killed_paths.append(Verdict(Verdict.UNKNOWN, None, line))

    def add_ignore_stderr_lines(self, string):
        self._ignore_lines.append(string)

    def parse_stdout_ev(self, ev, line):
        assert ev == "line-stdout"
        self._parse_stdout(line.decode("utf-8"))

    def _parse_stdout(self, line):
        if line_contains(line, *self._ignore_lines):
            return

        if "Verification result: FALSE" in line:
            self._result = "FALSE"
            if "Property violation (assertion" in line:
                self.add_error_or_killed("unreach", line)
            if "Property violation (error label" in line:
                self.add_error_or_killed("unreach", line)
            if "Property violation (unreach-call" in line:
                self.add_error_or_killed("unreach", line)

        if "Verification result: TRUE" in line:
            assert not self._result
            self._result = "TRUE"

    def killed_paths(self):
        return self._killed_paths

    def warnings(self):
        return self._warnings

    def finish(self, retcode):
        self._retval = retcode

    def result(self):
        if self._errors:
            if self._result == "FALSE":
                return self._errors
            return [Verdict(Verdict.UNKNOWN, None, "Inconsistent results")]
        if self._retval is None:
            return None
        if self._killed_paths:
            return self._killed_paths
        if self._retval == 0:
            if self._killed_paths:
                return [Verdict(Verdict.UNKNOWN, None, kp) for kp in self._killed_paths]
            assert self._result != "FALSE"
            if self._result == "TRUE":
                return [Verdict(Verdict.CORRECT, prp, "") for prp in self._properties]
            else:
                return [Verdict(Verdict.UNKNOWN, None, "Inconsistent results")]
        return [Verdict(Verdict.ERROR, None, f"retval: {self._retval}")]


class CPAchecker(Tool):
    instance_counter = 0

    def __init__(
        self, programs, properties, args=None, name="cpachecker", timeout=None
    ):
        self._resultsdir = (
            f"{get_env().workdir}/cpachecker-{CPAchecker.instance_counter}"
        )
        CPAchecker.instance_counter += 1

        properties = PropertiesSet(*properties)

        args = args or []
        for prp in properties:
            try:
                prpfile = prp.prpfile()
                if prpfile[0] != "/":
                    prpfile = f"{get_env().cwd}/{prpfile}"
                args += ["-spec", prpfile]
            except AttributeError:
                continue

        the_args = ["-preprocess", "-heap", "10000M", "-benchmark"] + args

        cpachecker_exe = find_program(
            "cpa.sh", [f"{get_env().srcdir}/cpachecker/scripts/"]
        )
        dbgv(f"Using cpachecker: {cpachecker_exe}")
        super().__init__(
            cpachecker_exe,
            inputs=[cu.path for cu in programs],
            args=the_args,
            name=name,
            timeout=timeout,
        )

        self._parser = CPAcheckerParser(self, properties, self.resultsdir())
        self.add_event_listener("line-stdout", self, self._parser.parse_stdout_ev)

    def parser(self):
        return self._parser

    def add_ignore_stderr_lines(self, string):
        """
        During parsing stderr, ignore lines containing the given substring.
        This can be used to ignore selected found error etc.
        """
        self._parser.add_ignore_stderr_lines(string)

    def resultsdir(self):
        return self._resultsdir

    def finish(self):
        super().finish()  # get the retval

        self._parser.finish(self.retval().retval)
        return TaskResult("DONE", self._parser.result(), task=self)
