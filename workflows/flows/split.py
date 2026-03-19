""" Tasks for dynamically splitting programs """
import sys
import shutil
from os import makedirs
from os.path import join as pathjoin, basename, splitext

from time import clock_gettime, CLOCK_REALTIME

from bbk.dbg import dbg
from bbk.dbg import print_stderr
from bbk.env import get_env


from bbk.task.task import Task
from bbk.task.aggregatetask import AggregateTask
from bbk.task.continuationtask import ContinuationTask

from bbk.task.result import TaskResult
from bbk.verdict import Verdict 

from bbk.compiler import CompilationUnit

# import the program splitter
sys.path.insert(0, pathjoin(get_env().srcdir, "program-splitter"))
from split import program_splitter

try:
    from cpasplit import program_splitter as cpasplitter
except ImportError:
    cpasplitter = None

if cpasplitter: program_splitter = cpasplitter

try:
    from cpamerge import program_merger
except ImportError:
    program_merger = None

sys.path.pop(0)


from .verifiers import SoftwareVerifier
from .verifiers import found_bug, task_result_is_conclusive

# Splitting ---------------------------------

class SplitTask(Task):
    """
    Split the given input file.
    """

    def __init__(self, input_file, options=None):
        super().__init__(name="splitter", descr=f"Split '{input_file}'")

        self._input = input_file
        self._outputs = None
        self._options = options
        self._outdir = pathjoin(get_env().workdir, "splits/")

        makedirs(self._outdir, exist_ok=True)

    def execute(self):
        filename = basename(self._input.path)
        base, suffix = splitext(filename)

        outputs = [
            pathjoin(self._outdir, f"{base}-l{suffix}"),
            pathjoin(self._outdir, f"{base}-r{suffix}"),
        ]

        kwargs = {}
        if cpasplitter: 
            kwargs["allowed_function_clones"] = self._options.get("function_clones", -1)
            kwargs["deepening"]               = self._options.get("deepening", False)
            kwargs["timeout"]                 = 60 # Make this configurable

        try:
            program_splitter(
                self._input.path,
                outputs[0],
                outputs[1],
                allowed_unrolls=self._options.get("loop_unrolls", -1),
                max_line_limit=self._options.get("split_line_limit", 100_000),
                **kwargs
            )
        except ValueError as e:
            print_stderr(str(e))
            self._result = TaskResult("ERROR", descr=str(e), task = self)
            return

        self._outputs = outputs

    def is_done(self):
        return self._outputs or self._result

    def stop(self):
        pass

    def kill(self):
        pass

    def finish(self):
        if self._result:
            return self._result

        if self._outputs:
            return TaskResult(
                "DONE",
                output=[
                    CompilationUnit(path, self._input.lang) for path in self._outputs
                ], task = self
            )

        return TaskResult("ERROR", descr="Splitting produced nothing", task = self)

# Dynamic Splitting ----------------------------------------------------------------

class DynamicSplittingVerifier(SoftwareVerifier):
    """
    Run dynamic program splitting with the given
    split verifier. 
    """
    
    def __init__(self, split_verifier, 
                 max_width = 2, max_height = 32, 
                 unroll_limit = -1, clone_limit = -1, 
                 deepening = False, 
                 split_line_limit = 100_000,
                 timeout = -1,
                 parallelization_limit = -1):
        super().__init__(split_verifier.args, split_verifier.property)
        self.split_verifier = split_verifier
        self.max_width = max_width
        self.max_height = max_height
        self.unroll_limit = unroll_limit
        self.clone_limit  = clone_limit
        self.deepening     = deepening
        self.split_line_limit = split_line_limit
        self.split_timeout = timeout
        self.parallelization_limit = parallelization_limit

        self._fns = []

    def create_task(self, inputfile: CompilationUnit) -> Task:
        task = DynamicSplittingVerificationTask(
            self.split_verifier, inputfile, 
            max_width = self.max_width, 
            max_height = self.max_height, 
            unroll_limit = self.unroll_limit,
            clone_limit = self.clone_limit,
            deepening = self.deepening,
            split_line_limit = self.split_line_limit,
            timeout = self.split_timeout,
            parallelization_limit=self.parallelization_limit,
        )

        for fn in self._fns:
            task = task >> fn
        
        return task
    
    def __rshift__(self, other):
        if not isinstance(other, SoftwareVerifier):
            self._fns.append(other)
            return self
        
        return super().__rshift__(other)


class DynamicSplitResult(TaskResult):
    
    def __init__(self, output, splits = None, status = "DONE", **kwargs):
        super().__init__(status, output, **kwargs)
        self._splits = splits

    def has_work(self):
        return len(self.splits()) > 0
    
    def residuals(self):
        return self.splits()
    
    def splits(self):
        if not self._splits:
            unknown_results = [res for res in self.output if isinstance(res.output[0], Verdict) and res.output[0].is_unknown()]

            splits = []
            for unknown_result in unknown_results:
                assert isinstance(unknown_result.task, CheckTask), f"{unknown_result} is not a check task"
                task = unknown_result.task
                splits.append(task.inputfile())
            self._splits = splits

        return self._splits

    def verdicts(self):
        return TaskResult("DONE", output = [r.output[0] for r in self.output], task = self)


class DynamicSplittingVerificationTask(ContinuationTask):
    """
    Task for running dynamic program splitting with the given
    split verifier. 
    """
    
    def __init__(self, split_verifier, inputfile, 
                    max_width = 2, max_height = 32, 
                    unroll_limit = -1, clone_limit = -1, 
                    deepening = False, split_line_limit = 100_000, 
                    parallelization_limit = -1,
                    timeout = -1):
        
        # Run Line 1: Task on the whole program
        super().__init__(
            ParallelCompose(
                [CheckTask(split_verifier, inputfile)]
            ),
            name = "SplitScheduler"
        )
        self._inputfile = inputfile
        self._split_verifier = split_verifier

        self._open_tasks      = []
        self._executed_tasks  = set()

        self._results = []

        self._steps = 0

        self._split_config = {
            "splitting"  : max_width,
            "steps_limit": max_height,
            "loop_unrolls": unroll_limit,
            "function_clones": clone_limit,
            "deepening": deepening,
            "split_line_limit": split_line_limit
        }

        self._parallelization_limit = max_width if parallelization_limit == -1 else parallelization_limit
        self._split_timeout = timeout # Soft timeout for splitter

    def _stop_splitter(self):

        step_limit = self._split_config["steps_limit"]
        if step_limit >= 0 and self._steps >= self._split_config["steps_limit"]:
            dbg(f"[ABORT-SPLIT] Reached step limit of {step_limit} steps.", color = "green")
            return True
        
        split_limit = self._split_config["splitting"]
        if split_limit >= 0 and len(self._open_tasks) >= self._split_config["splitting"]:
            dbg(f"[ABORT-SPLIT] Reached split limit of {split_limit} splits.", color = "green")
            return True
        
        if self._split_timeout > 0:
            current_time = clock_gettime(CLOCK_REALTIME) - self._start_time
            if current_time >= self._split_timeout:
                dbg(f"[ABORT-SPLIT] Reached soft timeout of {current_time:.3f} seconds.", color = "green")
                return True
    
        return False
    
    def continuation(self, result):
        if not result.is_done(): return result

        # splits <- splits U {P_i | r_i = ?} (Line 12)
        for task_result in result.output:
            for sub_result in task_result.output:
                if isinstance(sub_result, CompilationUnit):
                    self._open_tasks.append(task_result)
                
                elif isinstance(sub_result, Verdict):
                    if sub_result.is_incorrect(): 
                        # Abort if error: if x \in results: return x, splits
                        return DynamicSplitResult([task_result], task = self)
                    self._results.append(task_result)

        # |splits| = 0 (Line 6: First abort criterium)
        if len(self._open_tasks) == 0:
            # Line 14: program is correct and we are finished
            return DynamicSplitResult(self._results, task = self)
        
        assert all(task.task is not None for task in self._open_tasks), f"Result {result} introduced invalid tasks"
        
        self._steps += 1

        # stop: (Line 6: Second abort criterium)
        if self._stop_splitter():
            open_tasks = self._open_tasks
            dbg("Split task into %d parts (%d parts solved in the process)" % (len(open_tasks), len(self._results)))
            dbg("[STATS] Splits: %d" % len(open_tasks), color = "green")

            open_tasks_verdicts = [
                TaskResult("DONE", output = [
                                            Verdict(Verdict.UNKNOWN,
                                                     prp = self._split_verifier.property)      
                            ], task = open_task.task)
                for open_task in open_tasks
            ]

            return DynamicSplitResult(self._results + open_tasks_verdicts, 
                                      splits = [t.output[0] for t in open_tasks],
                                      task = self)

        # Generalization of Line 7 - 9: Run split and verify on n tasks (default: n = 1)
        split_check_limit = self._parallelization_limit // 2
        exec_tasks = self._open_tasks[:split_check_limit]

        parallel_tasks = [
            SplitAndCheckTask(self._split_verifier, task.output[0], 
                            split_config = self._split_config,
                            split_parent = task.task)
            if task.output[0] not in self._executed_tasks else
            IdentityTask([task], parent_task = task.task)
            for task in exec_tasks
        ]
        
        self._open_tasks      = self._open_tasks[split_check_limit:]
        self._executed_tasks |= set(t.output[0] for t in exec_tasks) 

        return TaskResult(
            "REPLACE_TASK",
                ContinuationTask(
                    ComposeResults(
                        parallel_tasks
                    ),
                    continuation=self.continuation
                )
            )


class ParallelCompose(AggregateTask):
    def __init__(self, tasks):
        super().__init__(tasks)
        self._task_num = len(tasks)
        self._results = []

    def aggregate(self, task, result):
        if result.is_error(): return result

        if not result.is_done():
            self._results.append(result)
        else:
            for r in result.output:
                if isinstance(r, Verdict) and r.is_incorrect():
                    return TaskResult("DONE", output=[result], task = self)

                self._results.append(
                    TaskResult("DONE", output = [r], task = result.task)
                )

        self._task_num -= 1
        if self._task_num == 0:
            return TaskResult("DONE", output=self._results, task = self)

        # no result yet
        return None

# Split helper ---------------------------------------------------------------

class IdentityTask(Task):

    def __init__(self, result, parent_task = None):
        super().__init__(name = "identity", descr = f"id('{result}')")
        self._output = result if isinstance(result, TaskResult) else TaskResult("DONE", output = result, task = parent_task)

    def execute(self):
        pass
    
    def is_done(self):
        return self._start_time is not None

    def stop(self):
        pass

    def kill(self):
        pass

    def finish(self):
        if self._result:
            return self._result
        
        return self._output


class CheckTask(ContinuationTask):

    def __init__(self, split_verifier, inputfile, split_parent = None):
        super().__init__(
            split_verifier(inputfile),
            name = "CheckTask"
        )
        self._inputfile = inputfile
        self.split_parent = split_parent
    
    def inputfile(self):
        return self._inputfile
    
    def continuation(self, result):
        if not result.is_done() or task_result_is_conclusive(result):
            return result

        return TaskResult("DONE", [self._inputfile], task = self)
    

class SplitAndCheckTask(ContinuationTask):
    def __init__(self, split_verifier, inputfile, split_config = None, split_parent = None):
        super().__init__(
            SplitTask(inputfile, options = split_config),
            name = "SplitAndCont"
        )
        self._split_verifier = split_verifier
        self._inputfile = inputfile

        self.split_parent = split_parent

    def inputfile(self):
        return self._inputfile
    
    def continuation(self, result):
        if result.is_done():
            return TaskResult(
                "REPLACE_TASK",
                ParallelCompose([
                    CheckTask(self._split_verifier, result.output[0], split_parent=self),
                    CheckTask(self._split_verifier, result.output[1], split_parent=self),
                ])
            )

        return TaskResult("DONE", [TaskResult("DONE", [self._inputfile], task = self)], task = self) # We are done with this file (it is atomic)


class ComposeResults(AggregateTask):
    def __init__(self, tasks):
        super().__init__(tasks)
        self.tasks = tasks
        self._task_num = len(tasks)
        self._results = []

    def aggregate(self, task, result):
        if not result.is_done():
            return result
        
        for r in result.output:
            if isinstance(r, Verdict) and r.is_incorrect():
                return result
            self._results.append(r)

        self._task_num -= 1
        if self._task_num == 0:
            return TaskResult("DONE", output=self._results, task = self)

        # no result yet
        return None
    
# Join operator ----------------------------------------------------------------

def _next_split_parent(task):
    if isinstance(task, SplitAndCheckTask):
        return task.split_parent

    while task and not isinstance(task, CheckTask):
        task = task.parent()
    
    if not task: return None
    return task.split_parent


def _split_path(task):
    path = []

    while task is not None:
        file = task.inputfile()
        if not path or file != path[-1].inputfile(): 
            path.append(task)
        task = _next_split_parent(task)
    
    return path


def naive_merge_old(split_result):
    if not split_result.is_done() or not hasattr(split_result, "splits"):
        return split_result

    splits = split_result.splits()
    if len(splits) <= 1: return split_result

    split_tasks = [res for res in split_result.output 
                   if isinstance(res.output[0], Verdict) and res.output[0].is_unknown()]

    prp = split_tasks[0].output[0].prp()
    assert all(res.output[0].prp() == prp for res in split_tasks)

    split_paths = [_split_path(task.task) for task in split_tasks]

    while len(split_paths) > 1:
        left, right = split_paths.pop(0), split_paths.pop(0)

        right_files = set(r.inputfile() for r in right)

        for i, l in enumerate(left):
            if l.inputfile() in right_files:
                split_paths.append(left[i:])
                break

    known_results = [res for res in split_result.output 
                        if isinstance(res.output[0], Verdict) and not res.output[0].is_unknown()]

    fake_result     = TaskResult("DONE", output=[Verdict(Verdict.UNKNOWN, prp = prp)], task = split_paths[0][0])
    
    dbg("[STATS] Merged into %d tasks" % len(split_paths), color = "green")
    return DynamicSplitResult(output = known_results + [fake_result], splits = [split_paths[0][0].inputfile()], task = split_result.task)


def export_residual(split_result):
    if not split_result.is_done() or not hasattr(split_result, "splits"):
        return split_result

    result_path = f"{get_env().cwd}/residual_program.c"

    splits = split_result.splits()
    if len(splits) == 0: return split_result

    if len(splits) > 1: 
        dbg(f"Generated {len(splits)} splits. Cannot generate residual program. Try to use a merge operator.")
        return split_result
    
    program_split = splits[0]
    shutil.copy(program_split.path, result_path)
    dbg(f"Saved residual program to {result_path}")
    return split_result


# Merging splits ----------------------------------------------------------------

def _naive_merge_fn(original_code, left_split, right_split):
    return original_code


def _cpa_merge_fn(original_code, left_split, right_split, timeout = 10):
    if program_merger is None:
        dbg("Cannot import CPA merger. Run naive merger instead", color = "red")
        return _naive_merge_fn(original_code, left_split, right_split)
    
    output_dir = pathjoin(get_env().workdir, "merges/")
    makedirs(output_dir, exist_ok=True)

    filename = basename(original_code.path)
    base, suffix = splitext(filename)
    output_file = pathjoin(output_dir, f"{base}-merged{suffix}")

    try:
        program_merger(
            left_split.path,
            right_split.path,
            output_file,
            timeout = timeout,
        )
    except ValueError as e:
        print_stderr(str(e))
        dbg("Cannot merge programs. Run naive merger instead", color = "red")
        return _naive_merge_fn(original_code, left_split, right_split)
    
    return CompilationUnit(output_file, original_code.lang)


def _compute_split_tree(splits):
    child_parent = {}

    worklist = [sp.task for sp in splits]
    while len(worklist) > 0:
        task   = worklist.pop(0)
        parent = _next_split_parent(task)

        if parent is not None:
            child_parent[task] = parent
            worklist.append(parent)
    
    parent_childs = {}
    for child, parent in child_parent.items():
        if parent not in parent_childs: parent_childs[parent] = []
        parent_childs[parent].append(child)
    
    return parent_childs, child_parent


def _incremental_merge(merge_fn, splits, **kwargs):
    parent_childs, child_parent = _compute_split_tree(splits)
    
    root_node = None
    merge_results = {sp.task: sp.task.inputfile() for sp in splits}
    worklist      = list(merge_results.keys())

    while len(worklist) > 0:
        current_node = worklist.pop(0)

        if current_node not in child_parent:
            root_node = current_node
            continue

        parent_node = child_parent[current_node]
        if parent_node in merge_results: continue
        
        children       = parent_childs[parent_node]
        if not all(c in merge_results for c in children):
            continue

        merge_children = [merge_results[c] for c in children if merge_results[c] is not None]

        if len(merge_children) == 0   : merge_results[parent_node] = None 
        elif len(merge_children) == 1 : merge_results[parent_node] = merge_children[0]
        elif len(merge_children) == 2 :
            original_code = parent_node.inputfile()
            merge_results[parent_node] = merge_fn(
                original_code, *merge_children, **kwargs
            )
        else:
            raise ValueError("Splitter has produced more than two children")
        
        worklist.append(parent_node)
    
    assert root_node is not None, "Cannot identify a root node"
    return merge_results[root_node]


def _merge_result(merge_fn, split_result, **kwargs):
    if not split_result.is_done() or not hasattr(split_result, "splits"):
        return split_result

    splits = split_result.splits()
    if len(splits) <= 1: return split_result

    split_tasks = [res for res in split_result.output 
                   if isinstance(res.output[0], Verdict) and res.output[0].is_unknown()]

    prp = split_tasks[0].output[0].prp()
    assert all(res.output[0].prp() == prp for res in split_tasks)

    merge_path = _incremental_merge(merge_fn, split_tasks, **kwargs)

    known_results = [res for res in split_result.output 
                        if isinstance(res.output[0], Verdict) and not res.output[0].is_unknown()]

    fake_result     = TaskResult("DONE", output=[Verdict(Verdict.UNKNOWN, prp = prp)])
    
    dbg("[STATS] Merged into 1 task" , color = "green")
    return DynamicSplitResult(output = known_results + [fake_result], splits = [merge_path], task = split_result.task)


def naive_merge(split_result):
    return _merge_result(_naive_merge_fn, split_result)


def cpa_merge(split_result):
    return _merge_result(_cpa_merge_fn, split_result)


# Custom CPA Merges ----------------------------------------------------------------

class CPAMerge:

    def __init__(self, timeout = 10):
        self._timeout = timeout

    def __call__(self, split_result):
        return _merge_result(_cpa_merge_fn, split_result, timeout = self._timeout)
    

def create_custom_cpa_merge(timeout = 10):
    return CPAMerge(timeout = timeout)