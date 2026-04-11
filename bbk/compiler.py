from os.path import join as pathjoin, basename

from bbk.dbg import dbg, print_stderr
from bbk.task import TaskResult, AggregateTask
from bbk.tools.tooloutputparser import ToolOutputParser
from bbk.tool import ProcessTask
from bbk.utils import err, _popen


class CompilationUnit:
    def __init__(self, path, lang="C"):
        self.path = path
        self.lang = lang

    def __repr__(self):
        return f"CompilationUnit({self.path}, lang={self.lang})"


def _run(cmd):
    dbg(f"# {' '.join(cmd)}")
    proc = _popen(cmd)
    sout, serr = proc.communicate()
    if proc.returncode != 0:
        dbg(f"# command returned {proc.returncode}")
        if sout:
            print_stderr("stdout:")
            print_stderr(sout.decode("utf-8"), color="red")
        if serr:
            print_stderr("stderr:")
            print_stderr(serr.decode("utf-8"), color="red")
        err(f"command returned {proc.returncode}")
    dbg(sout.decode("utf-8") if sout else "")
    dbg(serr.decode("utf-8") if serr else "")

    return sout, serr


UBSAN_FLAGS = ["-fsanitize=signed-integer-overflow", "-fsanitize=shift"]
ASAN_FLAGS = ["-Xclang", "-fsanitize-address-use-after-scope"]


class CompilationOptions:
    def __init__(self):
        self._cflags = []
        self._cppflags = []
        self._compiler_defs = []
        self._sanitize = []


class CompileUnitTask(ProcessTask):
    """
    Compile given programs and possibly catch warnings from the compiler.
    The result are compiled programs into LLVM, one LLVM bitcode made by
    linking all programs, and possibly a set of warnings.
    """

    def __init__(self, input_file, options):
        super().__init__(name=f"Compile {input_file}")

        self._input = input_file
        self._bitcode = None
        self._options = options

        _sanitize = self._options._sanitize
        args = self._options._compiler_defs
        if "asan" in _sanitize or "memory" in _sanitize:
            args.extend(ASAN_FLAGS)
        if "ubsan" in _sanitize or "undef" in _sanitize:
            args.extend(UBSAN_FLAGS)

        assert self._input, "No input files"
        assert self._input.lang == "C", self._input

        self._warnings_and_errors = []

        def __parse_stderr(event, line):
            assert event == "line-stderr", event
            if b"warning" in line or b"error" in line:
                self._warnings_and_errors.append(line.decode("utf-8"))

        self.add_event_listener("line-stderr", self, __parse_stderr)

    def warnings_and_errors(self):
        return self._warnings_and_errors

    def cmd(self):
        cu = self._input
        assert cu.lang == "C"
        dbg(f"## compiling {cu}")

        path = cu.path
        bitcode = pathjoin(self._env.workdir, basename(path) + ".bc")

        self._bitcode = bitcode

        cflags = [
            "-D__inline=",
            "-fgnu89-inline",
            "-Xclang",
            "-disable-O0-optnone",
            "-fno-vectorize",
            "-fno-slp-vectorize",
            "-finline-functions",
        ] + self._options._cflags
        cppflags = self._options._cppflags

        cmd = (
            ["clang", "-emit-llvm", "-c", "-g"]
            + cflags
            + cppflags
            + ["-o", self._bitcode, path]
            + self._options._compiler_defs
        )

        return cmd

    def finish(self):
        result = super().finish()
        if result.status == "DONE":
            return TaskResult("DONE", output=self._bitcode)
        return result

        # dbg(f"## compiled to {outp}")

        # bad_lines = []
        # if not warnings:
        #    return outp, []

        # for line in map(str, serr.splitlines()):
        #    for fail in warnings:
        #        if fail in line:
        #            bad_lines.append(line)

        # return outp, bad_lines


class LinkingTask(ProcessTask):
    def __init__(self, input_files, options):
        super().__init__()

        assert all((f is not None for f in input_files)), input_files

        self._input = input_files.copy()
        self._bitcode = None
        self._options = options

        assert self._input, self._input

    def finish(self):
        result = super().finish()
        if result.status == "DONE":
            return TaskResult("DONE", output=self._bitcode)
        return result

    def cmd(self):
        outd = self._env.workdir
        outp = pathjoin(outd, "code.bc")
        self._bitcode = outp

        assert outp not in self._input, self._input
        cmd = ["llvm-link", "-o", outp] + self._input

        return cmd


# class OptTask(ProcessTask):
#     def _opt(self, path, opts=None, outp=None, outd="/tmp/"):
#         dbg(f"Optimizing {path}")

#         if outp is None:
#             outp = pathjoin(outd, "optcode.bc")

#         cmd = ["opt", "-o", outp, path] + (opts or [])
#         _run(cmd)

#         dbg(f"Optimized files to {outp}")

#         return outp


class CompileFilesTask(AggregateTask):
    def __init__(self, inputs, options=None):
        super().__init__([])

        if not inputs:
            raise RuntimeError(f"No input files to the compilation task {self}")

        self._options = options or CompilationOptions()
        self._output_files = []
        self._bitcode = None
        self._warnings = []
        self._inputs = inputs

        _sanitize = self._options._sanitize
        if "asan" in _sanitize or "memory" in _sanitize:
            self.compile_argument(ASAN_FLAGS)
        if "ubsan" in _sanitize or "undef" in _sanitize:
            self.compile_argument(UBSAN_FLAGS)

    def output_files(self):
        return self._output_files

    def warnings(self):
        return self._warnings

    def execute(self):
        for cu in self._inputs:
            if cu.lang == "llvm":
                self._output_files.append(cu.path)
            elif cu.lang == "C":
                self.add_subtask(CompileUnitTask(cu, self._options))
            else:
                raise RuntimeError(f"Unsupported unit to compile: {cu}")

    def compile_argument(self, args):
        self._options._compiler_defs.extend(args)

    def cflags_append(self, flag):
        self._options._cflags.append(flag)

    def cppflags_append(self, flag):
        self._options._cflags.append(flag)

    def add_include_dirs(self, *args):
        for d in args:
            if d.startswith("-I"):
                self._options._cppflags.append(d)
            else:
                self._options._cppflags.append(f"-I{d}")

    def finish(self):
        # no sub-task failed, so we're done
        return TaskResult("DONE", output=self._output_files)

    def aggregate(self, task, result):
        self._warnings.extend(task.warnings_and_errors())

        if result.status != "DONE":
            return result

        assert task._bitcode is not None
        self._output_files.append(task._bitcode)

        return None


class CompilerTask(CompileFilesTask):
    def __init__(self, inputs, options=None, include_dirs=None):
        super().__init__(inputs, options=options)
        if include_dirs:
            self.add_include_dirs(include_dirs)

    def finish(self):
        super().finish()

        outfiles = self.output_files()
        if len(outfiles) > 1:
            return TaskResult(
                "REPLACE_TASK", output=LinkingTask(outfiles, self._options)
            )
        elif len(outfiles) == 0:
            return TaskResult("ERROR", descr="Compilation failed")
        return TaskResult("DONE", outfiles[0])
