from bbk.env import get_env
from bbk.verdict import Verdict
from bbk.tool import Tool
from bbk.tools.tooloutputparser import ToolOutputParser
from bbk.task.result import TaskResult
from bbk.dbg import dbg, dbgv
from bbk.witness import WitnessGraphML, WitnessHarness
from os import environ, listdir
from os.path import exists, splitext

from svcomp.helpers import SVCompProperty


def line_contains(line, *args):
    return any((a in line for a in args))


def err_file_matches_line(path, file, lineno):
    lineno_ok, file_ok = False, False

    with open(path, "r") as fobj:
        for line in fobj:
            if "File:" in line and line.rstrip().endswith(file):
                file_ok = True
            if "Line:" in line and line.rstrip().endswith(lineno):
                lineno_ok = True

            if file_ok and lineno_ok:
                return True
    return False


def err_file_matches(file, line_file, line_line, errortype):
    if line_file is None:
        if errortype is None or errortype in file:
            if errortype is None:
                dbgv("No dbg info nor error type, taking the first witness")
            else:
                dbgv(f"Taking the first witness of the given type: {errortype}")
            return True
    elif err_file_matches_line(file, line_file, line_line):
        return True

    return False


def get_witness(resultsdir, line, errortype):
    """
    Get witness object referencing the right GraphML file
    that corresponds to the found error (based on the error message)
    """
    # KLEE: ERROR: splits/t-l.c:11:
    if "location information missing" in line:
        line_file, line_line = None, None
    else:
        tmp = line.split()[2].split(":")
        line_file, line_line = tmp[0], tmp[1]
        assert int(line_line) > 0  # "check" also the format

    witnesses = []

    for file in listdir(resultsdir):
        if file.endswith(".err"):
            dotidx = file.find(".")
            assert dotidx > 0, file
            namebase = file[:dotidx]
            if err_file_matches(
                f"{resultsdir}/{file}", line_file, line_line, errortype
            ):
                path = f"{resultsdir}/{namebase}.graphml"
                if exists(path):
                    witnesses.append(WitnessGraphML(path=path))
                path = f"{resultsdir}/{namebase}.harness.c"
                if exists(path):
                    witnesses.append(WitnessHarness(path=path))

    return witnesses or None


class KleeParser(ToolOutputParser):
    def __init__(self, tool, properties, resultsdir):
        super().__init__(f"{resultsdir}/stdout.txt", f"{resultsdir}/stderr.txt")
        self._tool = tool
        self._properties = properties
        self._ignore_lines = []
        self._errors = []
        self._killed_paths = []
        self._warnings = []
        self._retval = None
        self._memsafety = any(
            (
                prp.is_valid_deref() or prp.is_valid_free() or prp.is_no_memleak()
                for prp in properties
            )
        )
        self._only_termination = (
            len(properties) == 1 and next(iter(properties)).is_termination()
        )
        self._only_memcleanup = (
            len(properties) == 1 and next(iter(properties)).is_memcleanup()
        )

        self._resultsdir = resultsdir

    def get_prp(self, key):
        for p in self._properties:
            if p.key() == key:
                return p
        return None

    def add_error_or_killed(self, prpkey, line, errortype=None):
        prp = self.get_prp(prpkey)
        if prp:
            dbg("KLEE found an error")
            # Wait until KLEE finishes and dumps all the files
            # Use a (generous) timeout, if KLEE does not finish until then,
            # just continue. Generating the witness will probably fail in
            # that case.
            if not self._tool.wait_for_finish(100):
                line += " (XXX: waiting for KLEE finishing failed)"
            self._errors.append(
                Verdict(
                    Verdict.INCORRECT,
                    prp,
                    info=line,
                    witness=get_witness(self._resultsdir, line, errortype),
                )
            )
        else:
            dbg("KLEE found a different error")
            self._killed_paths.append(Verdict(Verdict.UNKNOWN, None, line))

    def add_ignore_stderr_lines(self, string):
        self._ignore_lines.append(string)

    def parse_stderr_ev(self, ev, line):
        assert ev == "line-stderr"
        self._parse_stderr(line.decode("utf-8"))

    def _parse_stderr(self, line):
        if line_contains(line, *self._ignore_lines):
            return

        if "ASSERTION FAIL:" in line:
            if self._only_termination or self._only_memcleanup:
                # ignore ASSERTION errors as those just terminate a path
                return
            self.add_error_or_killed("unreach", line, "assert")
            # else we're not looking for unreach call
        elif line_contains(
            line,
            "silently concretizing (reason: floating point)",
            "Call to pthread_create",
            "unsupported pthread API.",
            "failed external call: ",
            ": divide by zero",
            "KLEE: WARNING: Maximum stack size reached.",
            "WARNING ONCE: skipping fork (memory cap exceeded)",
            "return void when caller expected a result",
            "Query timed out (fork)",
            "KLEE: ctrl-c detected, requesting interpreter to halt",
        ) or ("WARNING: killing" in line and "over memory cap: " in line):
            self._killed_paths.append(Verdict(Verdict.UNKNOWN, None, line))
        elif "memory error:" in line:
            if self._memsafety:
                if "memory error: memory leak detected" in line:
                    self.add_error_or_killed("no-memleak", line, "leak")
                elif line_contains(
                    line,
                    "memory error: out of bound pointer",
                    "memory error: object read only",
                    "memory error: calling nullptr",
                ):
                    self.add_error_or_killed("valid-deref", line, "ptr")
                elif line_contains(
                    line,
                    "memory error: invalid pointer: free",
                    "memory error: free of alloca",
                    "memory error: free of global",
                ):
                    self.add_error_or_killed("valid-free", line, "free")
                else:
                    self._killed_paths.append(Verdict(Verdict.UNKNOWN, None, line))

            elif "memory error: memory not cleaned up" in line:
                self.add_error_or_killed("memcleanup", line, "leak")
            else:
                self._killed_paths.append(Verdict(Verdict.UNKNOWN, None, line))
        elif "KLEE: ERROR:" in line and line_contains(
            line,
            "signed-integer-overflow",
            "integer division overflow",
            ": overshift error",
            "shift out of bounds",
        ):
            self.add_error_or_killed("no-signed-overflow", line)
        elif self._memsafety and line_contains(
            line, "KLEE: WARNING: Failed resolving segment in memcleanup check"
        ):
            self._killed_paths.append(Verdict(Verdict.UNKNOWN, None, line))
        # elif "KLEE: done: partially completed paths" in line:
        #    if int(line.split()[6]) > 0:
        #        self._killed_paths.append(Verdict(Verdict.UNKNOWN, None, line))
        elif "KLEE: WARNING" in line:
            self._warnings.append(line)

    def killed_paths(self):
        return self._killed_paths

    def warnings(self):
        return self._warnings

    def finish(self, retcode):
        self._retval = retcode

    def result(self):
        if self._errors:
            return self._errors
        if self._retval is None:
            return None
        if self._killed_paths:
            return self._killed_paths
        if self._retval == 0:
            # if self._killed_paths:
            #    return [Verdict(Verdict.UNKNOWN, kp) for kp in self._killed_paths]
            # assert self._no_error_found
            return [Verdict(Verdict.CORRECT, prp, "") for prp in self._properties]
        return [Verdict(Verdict.ERROR, None, f"retval: {self._retval}")]


class Klee(Tool):
    instance_counter = 0

    def __init__(self, bitcode, properties, args=None, name="klee", timeout=None):
        self._resultsdir = f"{get_env().workdir}/lee-{Klee.instance_counter}"
        Klee.instance_counter += 1

        the_args = [
            "-dump-states-on-halt=0",
            "--output-stats=0",
            "--use-call-paths=0",
            # "--optimize=false",
            "-silent-klee-assume=1",
            "-istats-write-interval=60s",
            "-timer-interval=10",
            "-only-output-states-covering-new=1",
            "-use-forked-solver=0",
            "-external-calls=pure",
            "-max-memory=8000",
            "-output-source=false",
            "-malloc-symbolic-contents",
            f"-output-dir={self._resultsdir}",
        ] + (args or [])

        error_fns = []
        for prp in properties:
            if prp.is_unreach():
                error_fns += prp.error_funs()
        if error_fns:
            the_args.extend(["-error-fn", ",".join(error_fns)])

        new_env = environ.copy()
        new_env[
            "LD_LIBRARY_PATH"
        ] = f"{get_env().srcdir}/klee/build/lib/:{environ['LD_LIBRARY_PATH']}"
        klee_runtime_dir = f"{get_env().srcdir}/klee/build/lib/klee/runtime"
        if exists(klee_runtime_dir):
            # If this dir exists, we're out of build and we must tell KLEE
            # that there it finds its libraries.
            # Otherwise KLEE will get them from the build automatically
            # and we do not need to set anything.
            new_env["KLEE_RUNTIME_LIBRARY_PATH"] = klee_runtime_dir

        super().__init__(
            f"{get_env().srcdir}/klee/build/bin/klee",
            inputs=[bitcode],
            args=the_args,
            name=name,
            envir=new_env,
            timeout=timeout,
        )

        self._parser = KleeParser(self, properties, self.resultsdir())
        self.add_event_listener("line-stderr", self, self._parser.parse_stderr_ev)

    def parser(self):
        return self._parser

    def add_ignore_stderr_lines(self, string):
        """
        During parsing stderr of KLEE, ignore lines containing the given substring.
        This can be used to ignore selected found error etc.
        """
        self._parser.add_ignore_stderr_lines(string)

    def stop(self):
        super().stop()

    def resultsdir(self):
        return self._resultsdir

    def finish(self):
        super().finish()  # get the retval

        self._parser.finish(self.retval().retval)
        return TaskResult("DONE", self._parser.result(), task=self)


def get_klee_args(args, properties):
    options = []
    options.extend(args.X)

    if args.sv_comp:
        options.append("-write-witness")

    # if args.timeout and args.timeout > 0:
    #    options.append(f"-max-time={args.timeout}")
    def add(p):
        if p not in options:
            options.append(p)

    no_memleak = True
    for prp in properties:
        if prp.is_unreach():
            add("-exit-on-error-type=Assert")
        elif prp.is_memcleanup():
            # NOTE: this elif must be before memleak, because that one matches too
            add("-check-memcleanup")
            add("-exit-on-error-type=Leak")
        elif prp.is_no_memleak():
            no_memleak = False
            add("-check-leaks")
            add("-exit-on-error-type=Leak")
        elif prp.is_valid_deref():
            add("-exit-on-error-type=Ptr")
            add("-exit-on-error-type=ReadOnly")
            add("-exit-on-error-type=BadVectorAccess")
        elif prp.is_valid_free():
            add("-exit-on-error-type=Free")
        elif prp.is_no_signed_overflow():
            add("-ubsan-runtime")
            add("-exit-on-error-type=Overflow")
            add("-exit-on-error-type=ReportError")
        elif prp.is_termination():
            pass
        elif isinstance(prp, SVCompProperty) and prp.is_memcleanup():
            add("-check-memcleanup")
        else:
            return None
    if no_memleak:
        # these leak memory, so do not use them if we look for leaks
        # FIXME: do not add these if libc or POSIX are not used by the program
        add("-libc=klee")
        # FIXME: we do not use POSIX runtime since syscall is not modelled by KLEE
        # and thus it is symbolic, which basically breaks the POSIX runtime of KLEE
        # add("-libc=uclibc")
        # add("-posix-runtime")
    else:
        add("-libc=klee")
    if args.exec_witness or args.harness:
        add("-write-harness")

    # This makes KLEE exit without searching the path-space for some reason
    # add("-write-ktests=0")

    return options
