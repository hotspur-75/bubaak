""" A common interface for verifiers """
# A verifier is a tool that checks a C program for a certain property
# Definition (verifier_type, property, resource limits)

from bbk.compiler import CompilationUnit
from bbk.properties import Property, PropertiesList

from bbk.dbg import dbg
from bbk.task.task import Task
from bbk.task.result import TaskResult
from bbk.task.continuationtask import ContinuationTask
from bbk.task.aggregatetask import AggregateTask

from bbk.verdict import Verdict

from bbk.compiler import CompilerTask, CompilationOptions

from bbk.timeout import TimeoutWatchdog
from bbk.tools.klee import Klee, get_klee_args
from bbk.tools.slowbeast import SlowBeast, get_slowbeast_args

from bbk.tools.cbmc import Cbmc


def found_bug(result):
    return isinstance(result.output, list) and any(
        (isinstance(r, Verdict) and r.is_incorrect()) for r in result.output
    )


def task_result_is_conclusive(result):
    return result.is_done() and (
        any((isinstance(r, Verdict) and r.is_incorrect() for r in result.output))
        or all((isinstance(r, Verdict) and r.is_correct() for r in result.output))
    )


def is_iterable(object):
    try:
        for _ in object: return True
    except Exception:
        return False

class SoftwareVerifier:
    
    def __init__(self, args, property, timeout = None, name = None):
        self.args = args
        self.property = property
        self.timeout = timeout
        self.name = name or self.__class__.__name__

    def create_batch(self, inputfiles, **kwargs):
        try:
            if len(inputfiles) == 1: return self.create_task(inputfiles[0])
        except Exception:
            pass

        tasks = [self.create_task(inputfile, **kwargs) for inputfile in inputfiles]
        return ParallelCompositionTask(tasks)
    
    def create_task(self, inputfile : CompilationUnit) -> Task:
        raise NotImplementedError("You need to define a verification task here")

    def __call__(self, inputobj, **kwargs):
        if isinstance(inputobj, TaskResult):
            if hasattr(inputobj, 'residuals'):
                residuals = inputobj.residuals()
                if len(residuals) == 0: return inputobj
                inputobj = residuals
            else:
                return inputobj

        if is_iterable(inputobj):
            return self.create_batch(inputobj, **kwargs)
        
        return self.create_task(inputobj, **kwargs)

    def __rshift__(self, other):
        return SequentialComposition([self, other])
    
    def __or__(self, other):
        if isinstance(other, ParallelComposition):
            return other.__or__(self)

        return ParallelComposition([self, other])


# Parallel composition -----------------------------

class ParallelComposition(SoftwareVerifier):
    
    def __init__(self, verifiers, timeout = None):
        super().__init__(verifiers[0].args, verifiers[0].property, timeout = timeout)
        self._verifiers = verifiers

    def __or__(self, other):
        if isinstance(other, ParallelComposition):
            others = other._verifiers
        else:
            others = [other]
        
        return ParallelComposition(self._verifiers + others)
    
    def create_task(self, inputfile: CompilationUnit) -> Task:
        verification_tasks = [verifier(inputfile) for verifier in self._verifiers]
        return ParallelCompositionTask(verification_tasks, timeout = self.timeout)


class ParallelCompositionTask(AggregateTask):
    def __init__(self, tasks, timeout = None):
        super().__init__(tasks, timeout = timeout, name = "ParallelCompositionTask")
        self._tasks = tasks
        self._task_num = len(tasks)
        self._results = []

    def aggregate(self, task, result):
        if not task.is_done():
            dbg(f"{task.name()} ran into an error. Ignore result.")
            return None
            
        if found_bug(result):
            return result

        self._results.append(result.output[0])
        self._task_num -= 1
        if self._task_num == 0:
            return TaskResult("DONE", output=self._results, task = self)

        # no result yet
        return None
    
# Sequential composition ---------------------------

class SequentialComposition(SoftwareVerifier):
    
    def __init__(self, verifiers, timeout = None):
        super().__init__(verifiers[0].args, verifiers[0].property, timeout = timeout, name = "SequentialCompositionTask")
        self._verifiers = verifiers
    
    def create_task(self, inputfile: CompilationUnit) -> Task:
        verification_tasks = [verifier(inputfile) for verifier in self._verifiers]
        
        sequential_composition = verification_tasks[-1]
        for i in range(len(verification_tasks) - 2, -1, -1):
            sequential_composition = SequentialCompositionTask(
                verification_tasks[i], sequential_composition
            )

        return sequential_composition


class SequentialCompositionTask(ContinuationTask):
    
    def __init__(self, base_verification_task, backup_verification_task):
        self._backup_verification_task = backup_verification_task
        super().__init__(base_verification_task)
        
    def continuation(self, result):
        if not result.is_done():
            dbg(f"{self._task.name()} ran into an error. Continue with backup.")
            #return result
        
        if task_result_is_conclusive(result):
            return result
        
        return TaskResult("REPLACE_TASK", self._backup_verification_task)
    
    def __str__(self) -> str:
        return f"{self._task} >> {self._backup_verification_task}"

# KLEE ----------------------------------------------

class KLEEVerifier(SoftwareVerifier):

    def create_task(self, inputfile : CompilationUnit, check = False) -> Task:
        return KLEEVerificationTask(self, inputfile, check = check)


class KLEEVerificationTask(ContinuationTask):
    
    def __init__(self, klee_verifier : KLEEVerifier, inputfile : CompilationUnit, check = False):
        if check:
            compiler_task = CompileAndCheck([inputfile], PropertiesList([klee_verifier.property]))
        else:
            compiler_task = CompilerTask([inputfile])
        
        super().__init__(compiler_task, name = "KLEEVerificationTask")
        self._verifier = klee_verifier

        self._args = klee_verifier.args
        self._property = klee_verifier.property
        self._inputfile  = inputfile
        self._tool_timeout = klee_verifier.timeout
    
    def continuation(self, result):
        if not result.is_done():
            return TaskResult("ERROR", output=result, descr="Compiling {self._inputfile} failed")

        return TaskResult("REPLACE_TASK",
                          Klee(result.output, 
                               [self._property], 
                               get_klee_args(self._args, [self._property]), 
                               timeout=self._tool_timeout))


def klee_failed_on_floats(result):
    if not result.is_done():
        return False

    for r in result.output:
        if (
            r is not None
            and r.is_unknown()
            and "silently concretizing (reason: floating point)" in r.info()
        ):
            return True
    return False

# SlowBeast verifier ----------------------------------------------------------------

class SlowBeastVerifier(SoftwareVerifier):

    def create_task(self, inputfile: CompilationUnit, handle_floats = False) -> Task:
        return SlowBeastVerificationTask(self, inputfile, handle_floats = handle_floats)
    

class SlowBeastVerificationTask(ContinuationTask):
    def __init__(self, sb_verifier: SlowBeastVerifier, inputfile : CompilationUnit, handle_floats=False):
        super().__init__(
            CompileAndCheck([inputfile], PropertiesList([sb_verifier.property]), include_dirs=sb_verifier.args.I),
            name="SlowBeastVerificationTask",
        )

        self._verifer = sb_verifier

        self._args = sb_verifier.args
        self._property = sb_verifier.property
        self._inputfile = inputfile
        self._tool_timeout = sb_verifier.timeout
        self._handle_floats = handle_floats
    
    def continuation(self, result):
        if not result.is_done():
            return TaskResult(
                "ERROR", output=result, descr="Compiling {self._inputfile} failed"
            )

        if found_bug(result):
            return result
        
        self._bitcode = result.output

        properties = [self._property]

        if self._handle_floats:
            args = get_slowbeast_args(self._args, properties)
            name = "sb-se"
        else:
            args = get_slowbeast_args(self._args, properties, ["-bself"])
            name = "sb-bself"
        
        if any((p.is_no_signed_overflow() for p in properties)) or any(
            (p.is_valid_deref() for p in properties)
        ):
            # we need to recompile the input file, because KLEE used sanitizers
            task = ContinuationTask(
                CompileAndCheck(
                    [self._inputfile], properties, include_dirs=self._args.I
                ),
                continuation=lambda result: TaskResult(
                    "REPLACE_TASK",
                    SlowBeast(
                        self._bitcode,
                        properties,
                        args=args,
                        name=name,
                        timeout=self._tool_timeout,
                    ),
                )
                # Compilation succeeded and bug was not found, so start verifiers
                if result.is_done() else result,
                descr="Compile and Start Slowbeast",
            )
        else:
            task = SlowBeast(
                self._bitcode, properties, args=args, name=name, timeout=self._tool_timeout
            )

        return TaskResult("REPLACE_TASK", task)


class KLEESlowBeastVerifier(SlowBeastVerifier):

    def create_task(self, inputfile):
        sb_verifier = SlowBeastVerifier(
            args = self.args, property = self.property, timeout = self.timeout
        )

        return KLEESlowBeastVerificationTask(sb_verifier, inputfile)


class KLEESlowBeastVerificationTask(ContinuationTask):

    def __init__(self, sb_verifier: SlowBeastVerifier, inputfile : CompilationUnit):
        klee_verifier = KLEEVerifier(
            sb_verifier.args, sb_verifier.property, timeout = 4
        )
        
        super().__init__(
            klee_verifier(inputfile),
            name="KLEESlowBeastVerificationTask",
        )

        self._sb_verifer = sb_verifier
        self._inputfile = inputfile

    def continuation(self, result):
        if task_result_is_conclusive(result):
            return result
        
        handle_floats = klee_failed_on_floats(result)

        return TaskResult("REPLACE_TASK",
                          self._sb_verifer(self._inputfile, handle_floats = handle_floats))


# CPAchecker ---------------------------------------------------------------------------

from bbk.tools.cpachecker import CPAchecker

class CPAcheckerVerifier(SoftwareVerifier):

    def __init__(self, config_name, args, property, timeout = None):
        super(CPAcheckerVerifier, self).__init__(args, property, timeout, name = "CPA_" + config_name)
        self.config_name = config_name

    def create_task(self, inputfile):
        the_args = [f"-{self.config_name}"] 
        if self.timeout:
            the_args += ["-timelimit", f"{self.timeout}s"]
        if self.args.pointer_bitwidth:
            the_args += [f"-{self.args.pointer_bitwidth}"]
        the_args +=  self.args.X.copy()
        return CPAchecker([inputfile], [self.property], the_args)


class CPAPredicateAnalysis(CPAcheckerVerifier):

    def __init__(self, args, property, timeout = None):
        super(CPAPredicateAnalysis, self).__init__("predicateAnalysis", args, property, timeout)


class CPAkInduction(CPAcheckerVerifier):

    def __init__(self, args, property, timeout = None):
        super(CPAkInduction, self).__init__("kInduction", args, property, timeout)


class CPASymbolicExecution(CPAcheckerVerifier):

    def __init__(self, args, property, timeout = None):
        super(CPASymbolicExecution, self).__init__("symbolicExecution", args, property, timeout)


class CPABoundedModelChecking(CPAcheckerVerifier):

    def __init__(self, args, property, timeout = None):
        super(CPABoundedModelChecking, self).__init__("bmc", args, property, timeout)

# CBMC Verifier -----------------------------------------------------------------------

class CBMCVerifier(SoftwareVerifier):

    def create_task(self, inputfile : CompilationUnit) -> Task:
         return TimeoutWatchdog(
                Cbmc([inputfile], [self.property], args = ["--unwind", "3"] + self.args.X), self.timeout
            )

# SVCOMP Verifier ----------------------------------------------------------------------

from bbk.tools.svcomptool import GetSVCompTool, SVCompTool

class SVCOMPVerifier(SoftwareVerifier):

    def __init__(self, tool_name, args, property, timeout = None, tool_args = None):
        super(SVCOMPVerifier, self).__init__(args, property, timeout, name = tool_name)
        self.tool_name = tool_name
        self.tool_args = tool_args or []
        self.bitwidth  = args.pointer_bitwidth

    def create_task(self, inputfile: CompilationUnit) -> Task:
        return TimeoutWatchdog( GetSVCompTool(self.tool_name, [self.property], year=None)
                    >> (
                        lambda r: SVCompTool(
                            self.tool_name, [inputfile], [self.property], self.tool_args, self.timeout, self.bitwidth
                        )
                        if r.is_done()
                        else r
                    ),
                    timeout = self.timeout,
        )



# Helper tasks -------------------------------------------------------------------------

class CompileAndCheck(CompilerTask):
    def __init__(self, inputs, properties, include_dirs=None, options=None):
        options = options or CompilationOptions()
        if any((p.is_no_signed_overflow() for p in properties)):
            options._sanitize.append("ubsan")
        if any((p.is_valid_deref() for p in properties)):
            options._sanitize.append("asan")

        super().__init__(inputs, options=options, include_dirs=include_dirs)
        self._properties = properties

    def finish(self):
        result = super().finish()
        warnings = self.warnings()
        if warnings:
            overflow = self._properties.get("no-signed-overflow")
            if overflow:
                for line in warnings:
                    if "warning: overflow in expression;" in line or (
                        "implicit conversion" in line
                        and "changes value from" in line
                        and "to 'float'" not in line
                    ):
                        # This is a work-around for the problem that the translation to LLVM looses
                        # some information -- in this case, an overflow is detected by clang, reported,
                        # but LLVM is generated without this overflow
                        return TaskResult(
                            "DONE",
                            [
                                Verdict(
                                    Verdict.INCORRECT,
                                    overflow,
                                    "Signed overflow detected",
                                )
                            ],
                        )

        return result
