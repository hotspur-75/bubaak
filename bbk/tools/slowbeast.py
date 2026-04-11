# from tempfile import mkdtemp
from os import environ, makedirs

from bbk.env import get_env
from bbk.tools.tooloutputparser import ToolOutputParser
from bbk.task.result import TaskResult
from bbk.tool import Tool
from bbk.verdict import Verdict
from bbk.witness import WitnessGraphML, WitnessHarness


def line_contains(line, *args):
    return any((a in line for a in args))


def get_witnesses(args, resultsdir, line):
    """
    Get witness object referencing the right GraphML file
    that corresponds to the found error (based on the error message)
    """

    witnesses = []

    # [38] /opt/bubaak/tests/svcomp/termination-bwb/not-02-false.c:18:14: [non-termination]: an infinite execution found
    try:
        state_n = int(line.split()[0][1:-1])
    except ValueError:
        return None

    # the files may not exist right now, so do not check for their existence
    # (if needed, we can wait for their creation as we do with KLEE files
    #  that we need to parse)
    if "-sv-comp-witness" in args:
        witnesses.append(WitnessGraphML(path=f"{resultsdir}/witness-{state_n}.graphml"))
    if "-gen-harness" in args:
        witnesses.append(WitnessHarness(path=f"{resultsdir}/harness-{state_n}.c"))

    return witnesses or None


class SlowBeastParser(ToolOutputParser):
    def __init__(self, properties, args, resultsdir):
        super().__init__(f"{resultsdir}/stdout.txt", f"{resultsdir}/stderr.txt")
        self._properties = properties
        self._no_error_found = False
        self._killed_paths = []
        self._errors = []
        self._retval = None
        self._args = args
        self._outdir = resultsdir

    def is_bse(self):
        return "-bself" in self._args or "-bse" in self._args

    def try_get_prp(self, key):
        for p in self._properties:
            if p.key() == key:
                return p
        return None

    def get_prp(self, key):
        prp = self.try_get_prp(key)
        if prp is None:
            raise RuntimeError(f"Did not find property: {key}")
        return prp

    def add_error_or_killed(self, prpkey, line):
        prp = self.get_prp(prpkey)
        if prp:
            self._errors.append(
                Verdict(
                    Verdict.INCORRECT,
                    prp,
                    line,
                    witness=get_witnesses(self._args, self._outdir, line),
                )
            )
        else:
            self._killed_paths.append(Verdict(Verdict.UNKNOWN, None, line))

    def parse_stderr_ev(self, ev, line):
        assert ev == "line-stderr"
        self._parse_stderr(line.decode("utf-8"))

    def parse_stdout_ev(self, ev, line):
        assert ev == "line-stdout"
        self._parse_stdout(line.decode("utf-8"))

    def _parse_stdout(self, line):
        if line.startswith("Found errors:"):
            if line == "Found errors: 0\n":
                self._no_error_found = True
        if line.startswith("Killed paths:"):
            num = int(line.split()[2])
            if num == 0:
                # BSELF does not report the paths correctly right now
                pass  # assert len(self._killed_paths) == 0
            elif num > 0 and len(self._killed_paths) == 0:
                # slowbeast killed a path, but we didn't catch the warning
                # (maybe there was not one). Add at least this line
                self._killed_paths.append(line)
        if "KILLED STATE: " in line or "Interrupted..." in line:
            self._killed_paths.append(line)

    def _parse_stderr(self, line):
        if "[assertion error]:" in line and (
            "reachable" in line or "error function called" in line
        ):
            self.add_error_or_killed("unreach", line)
        elif "[non-termination]: an infinite execution found" in line:
            self.add_error_or_killed("termination", line)
        elif "[assertion error]:" in line and line_contains(
            line, "signed integer overflow", "signed integer underflow"
        ):
            self.add_error_or_killed("no-signed-overflow", line)
        elif "[memory error] - uninitialized read" in line:
            self._killed_paths.append(line)
        elif self.is_bse() and "Ignoring function pointer call: " in line:
            self._killed_paths.append(line)

    def finish(self, retcode):
        self._retval = retcode

    def result(self):
        if self._errors:
            assert not self._no_error_found
            return self._errors
        if self._retval is None:
            return None
        if self._retval == 0:
            if self._killed_paths:
                return [Verdict(Verdict.UNKNOWN, None, kp) for kp in self._killed_paths]
            if not self._no_error_found:
                return [
                    Verdict(
                        Verdict.ERROR,
                        None,
                        f"inconsistency: no_error_found = {self._no_error_found}, errors = {self._errors}, killed_paths = {self._killed_paths}",
                    )
                ]
            return [
                Verdict(
                    Verdict.CORRECT,
                    prp,
                    witness=WitnessGraphML(
                        path=f"{self._outdir}/correctness-witness.graphml"
                    ),
                )
                for prp in self._properties
            ]
        return [Verdict(Verdict.ERROR, None, f"retval: {self._retval}")]


class SlowBeast(Tool):
    instance_counter = 0

    def __init__(
        self,
        bitcode,
        properties,
        args=None,
        name="slowbeast",
        timeout=None,
    ):
        the_args = args.copy() if args else []

        self._out_dir = f"{get_env().workdir}/sb-{SlowBeast.instance_counter}"  # mkdtemp(prefix="sb-out.")
        makedirs(self._out_dir, exist_ok=True)
        SlowBeast.instance_counter += 1
        the_args.extend(("-out-dir", self._out_dir))

        for prp in properties:
            if prp.is_unreach():
                for fn in prp.error_funs():
                    the_args.extend(["-error-fn", fn])

        new_env = environ.copy()
        new_env["PYTHONOPTIMIZE"] = "1"

        super().__init__(
            f"{get_env().srcdir}/slowbeast/sb",
            inputs=[bitcode],
            args=the_args,
            name=name,
            envir=new_env,
            timeout=timeout,
        )

        self._parser = SlowBeastParser(properties, args, self.resultsdir())
        self.add_event_listener("line-stdout", self, self._parser.parse_stdout_ev)
        self.add_event_listener("line-stderr", self, self._parser.parse_stderr_ev)

    def resultsdir(self):
        return self._out_dir

    def finish(self):
        super().finish()  # get the retval

        self._parser.finish(self.retval().retval)
        return TaskResult("DONE", self._parser.result(), task=self)


def get_slowbeast_args(args, properties, options=None):
    options = options or []
    if args.X:
        options.extend(args.X)
    have_termination = False
    for prp in properties:
        if not prp.is_unreach():
            if prp.is_no_signed_overflow():
                options.extend(("-check", "no-overflow"))
            else:
                options.extend(("-check", prp.key()))
        if prp.is_termination():
            have_termination = True
            if "-bself" in options:
                raise NotImplementedError("BSELF does not support termination")

    if "-bself" in options:
        options.append("-forbid-floats")
        options.append("-forbid-threads")

    if args.sv_comp:
        # ["-pointer-bitwidth", str(args.pointer_bitwidth)]
        # options += ["-svcomp-witness", "-se-exit-on-error", "-se-replay-errors"]
        options += ["-svcomp-witness", "-exit-on-error"]
        # XXX: we replay errors in reach and overflows for now to find bugs
        # if not have_termination:
        #     options.append("-replay-error")

    if args.exec_witness or args.harness:
        options.append("-gen-harness")
    return options
