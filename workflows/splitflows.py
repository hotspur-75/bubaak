""" Workflow for configurable splitting workflows (splitflows)"""

from bbk.timeout import TimeoutWatchdog
from bbk.workflow import Workflow

from bbk.dbg import dbg
from bbk.task.result import TaskResult
from bbk.task.continuationtask import ContinuationTask

from .flows import task_result_is_conclusive
from .flows import DynamicSplittingVerifier, naive_merge, cpa_merge, export_residual, create_custom_cpa_merge
from .flows import KLEEVerifier, SlowBeastVerifier, KLEESlowBeastVerifier
from .flows import WorkStealingComposition, ParallelComposition

# Extra tools
from .flows import CPAcheckerVerifier, CPAPredicateAnalysis, CPAkInduction, CPASymbolicExecution, CPABoundedModelChecking 
from .flows import SVCOMPVerifier, CBMCVerifier

STEPS_LIMIT = 4
WEAK_VERIFIER_TIMEOUT = 4 # Make this configurable
PARALLEL = -1

# Number of unrolls until we skip
LOOP_UNROLLS = 2
MAX_CLONES   = 2

# Line limit (allowed for splitting)
SPLIT_LINE_LIMIT = 100_000

# Split configuration ------------------------------------

def _splitting_config(splitop):
    if splitop == "split" or splitop == "split2":
        return {"splitting": 2}

    if splitop == "split0": return {"splitting": 0}
    if splitop == "split1": return {"splitting": 1}
    if splitop == "split4": return {"splitting": 4}
    if splitop == "split8": return {"splitting": 8}

    if splitop == "splitd" or splitop == "split2d":
        return {"splitting": 2, "deepening": True}
    
    if splitop == "split4d":
        return {"splitting": 4, "deepening": True}
    
    if splitop == "split8d":
        return {"splitting": 8, "deepening": True}

    if splitop == "splitinf": 
        return {"splitting": 128, "steps_limit": 1024}
    
    if splitop == "split2_u2":
        return {"splitting": 128, "loop_unrolls": 2, "function_clones": 2}
    
    if splitop == "split100s":
        return {"splitting": 128, "steps_limit": 1024, 
                "split_timeout": 100,
                "parallel": 2}
    
    if splitop == "split100d":
        return {"splitting": 128, "steps_limit": 1024, 
                "split_timeout": 100, "deepening": True,
                "parallel": 2}
    
    if splitop == "split100dx8":
        return {"splitting": 128, "steps_limit": 1024, 
                "split_timeout": 100, "deepening": True,
                "parallel": 2, "split_verifier_timeout": 8}
    
    if splitop == "split100dx16":
        return {"splitting": 128, "steps_limit": 1024, 
                "split_timeout": 100, "deepening": True,
                "parallel": 2, "split_verifier_timeout": 16}
    
    if splitop == "split100dx32":
        return {"splitting": 128, "steps_limit": 1024, 
                "split_timeout": 100, "deepening": True,
                "parallel": 2, "split_verifier_timeout": 32}
    
    if splitop == "split100dx16nl":
        return {"splitting": 128, "steps_limit": 1024, 
                "split_timeout": 100, "deepening": True,
                "parallel": 2, "loop_unrolls": 0, "split_verifier_timeout": 16}
    
    if splitop == "split100dx16nc":
        return {"splitting": 128, "steps_limit": 1024, 
                "split_timeout": 100, "deepening": True,
                "parallel": 2, "function_clones": 0, "split_verifier_timeout": 16}
    
    if splitop == "split2x16":
        return {"splitting": 2, "steps_limit": 1024, 
                "split_timeout": 100, 
                "parallel": 2, "split_verifier_timeout": 16}
    
    if splitop == "split4x16":
        return {"splitting": 4, "steps_limit": 1024, 
                "split_timeout": 100, 
                "parallel": 2, "split_verifier_timeout": 16}
    
    if splitop == "split8x16":
        return {"splitting": 8, "steps_limit": 1024,
                "split_timeout": 100,
                "parallel": 2, "split_verifier_timeout": 16}
    
    if splitop == "split100sx16":
        return {"splitting": 128, "steps_limit": 1024, 
                "split_timeout": 100, 
                "parallel": 2, "split_verifier_timeout": 16}
    
    if splitop == "split2dx16":
        return {"splitting": 2, "steps_limit": 1024, 
                "split_timeout": 100, "deepening": True,
                "parallel": 2, "split_verifier_timeout": 16}
    
    if splitop == "split4dx16":
        return {"splitting": 4, "steps_limit": 1024, 
                "split_timeout": 100, "deepening": True,
                "parallel": 2, "split_verifier_timeout": 16}
    
    if splitop == "split8dx16":
        return {"splitting": 8, "steps_limit": 1024, 
                "split_timeout": 100, "deepening": True,
                "parallel": 2, "split_verifier_timeout": 16}

    raise ValueError("Unknown split operator: %s" % splitop)


def get_splitting_config(splitop, cmdargs, properties):
    split_config = _splitting_config(splitop)

    split_config.update({
        "args": cmdargs,
        "properties": properties,
        "steps_limit": split_config.get("steps_limit", STEPS_LIMIT),
        "split_verifier_timeout": split_config.get("split_verifier_timeout", WEAK_VERIFIER_TIMEOUT),
        "loop_unrolls": split_config.get("loop_unrolls", LOOP_UNROLLS),
        "function_clones": split_config.get("function_clones", MAX_CLONES),
        "deepening": split_config.get("deepening", False),
        "split_line_limit": SPLIT_LINE_LIMIT,
        "split_timeout": split_config.get("split_timeout", -1),
        "parallel": split_config.get("parallel", PARALLEL),
    })

    return split_config

# Backup solver -------------------------------------------------------

# A backup solver for the case that splitting does not work
class CheckWithBackup(ContinuationTask):
    def __init__(self, check_task, backup_task):
        super(CheckWithBackup, self).__init__(check_task, name="CheckWithBackup")
        self._backup = backup_task

    def continuation(self, result):
        # Actually, we want to try again with the backup if we run into an error.
        if result.is_error():
            dbg("Splitter ran into an error. Continue with backup.")
            # return result

        if task_result_is_conclusive(result):
            return result

        return TaskResult("REPLACE_TASK", self._backup)


# Split verifier ------------------------------------------------------

def create_verifier(verifier_name, args, properties, timeout = None, ws = False):
    assert len(properties) == 1

    if "+" in verifier_name:
        subverifiers = verifier_name.split("+")
        subverifiers = [create_verifier(subv, args, properties, timeout) for subv in subverifiers]
        return WorkStealingComposition(subverifiers) if ws else ParallelComposition(subverifiers)

    if ws:
        return WorkStealingComposition([create_verifier(verifier_name, args, properties, timeout, ws = False)])

    if verifier_name == "klee":
        return KLEEVerifier(args, properties[0], timeout = timeout)
    
    if verifier_name == "klee100":
        if timeout is not None: dbg(f"Ignoring timeout of {timeout}s and use 100s instead.")
        return KLEEVerifier(args, properties[0], timeout = 100)

    if verifier_name == "sb":
        return SlowBeastVerifier(args, properties[0], timeout=timeout)
    
    if verifier_name == "ksb":
        return KLEESlowBeastVerifier(args, properties[0], timeout=timeout)

    if verifier_name == "pa":
        return CPAPredicateAnalysis(args, properties[0], timeout=timeout)
    
    if verifier_name == "ki":
        return CPAkInduction(args, properties[0], timeout=timeout)
    
    if verifier_name == "cpase":
        return CPASymbolicExecution(args, properties[0], timeout=timeout)
    
    if verifier_name == "cpabmc":
        return CPABoundedModelChecking(args, properties[0], timeout=timeout)
    
    if verifier_name == "esbmc": verifier_name = "esbmc-kind"
    if verifier_name == "bmc"  : verifier_name = "esbmc-incr"

    if verifier_name in ["cpachecker", "cbmc", "uautomizer", "esbmc-kind", "esbmc-incr", "theta"]:
        return SVCOMPVerifier(verifier_name, args, properties[0], timeout=timeout)

    if verifier_name == "klee100":
        if timeout is not None: dbg(f"Ignoring timeout of {timeout}s and use 100s instead.")
        return KLEEVerifier(args, properties[0], timeout = 100)
    
    if verifier_name == "pa100":
        if timeout is not None: dbg(f"Ignoring timeout of {timeout}s and use 100s instead.")
        return CPAPredicateAnalysis(args, properties[0], timeout = 100)
    
    if verifier_name == "esbmc100":
        if timeout is not None: dbg(f"Ignoring timeout of {timeout}s and use 100s instead.")
        return SVCOMPVerifier("esbmc-kind", args, properties[0], timeout = 100)
    
    if verifier_name == "bmc100":
        if timeout is not None: dbg(f"Ignoring timeout of {timeout}s and use 100s instead.")
        return SVCOMPVerifier("esbmc-incr", args, properties[0], timeout = 100)

    raise NotImplementedError("Unknown split verifier: %s" % verifier_name)


def collect_results(result):
    if result.is_done() and hasattr(result, "verdicts"):
        return result.verdicts()
    else:
        return result
    

# Create dynamic splitter --------------------------------------------

def create_dynamic_splitter(splitop, splitv, cmdargs, properties, mergeop = None):
    split_config = get_splitting_config(splitop, cmdargs, properties)

    if split_config["splitting"] == 0: return None
    
    split_verifier = create_verifier(
        splitv, cmdargs, properties, timeout = split_config["split_verifier_timeout"]
    )

    split_scheduler = DynamicSplittingVerifier(
        split_verifier, 
        max_width = split_config["splitting"],
        max_height = split_config["steps_limit"],
        unroll_limit = split_config["loop_unrolls"],
        clone_limit  = split_config["function_clones"],
        deepening    = split_config["deepening"],
        split_line_limit = split_config["split_line_limit"],
        timeout = split_config["split_timeout"],
        parallelization_limit = split_config["parallel"],
    )

    workflow = split_scheduler

    if mergeop == "merge":
        workflow = workflow >> create_custom_cpa_merge(20)
    elif mergeop in ["cpamerge", "cmerge", "cmerge10"]:
        workflow = workflow >> cpa_merge
    elif mergeop == "cmerge20":
        workflow = workflow >> create_custom_cpa_merge(20)
    elif mergeop == "cmerge40":
        workflow = workflow >> create_custom_cpa_merge(40)
    elif mergeop == "cmerge60":
        workflow = workflow >> create_custom_cpa_merge(60)

    return workflow


def create_backup_verifier(backup_verifiers, cmdargs, properties):
    workflow = None

    for backupv in backup_verifiers:
        backup_verifier = create_verifier(
            backupv, cmdargs, properties,
            timeout = None, ws = False
        )
        if workflow is None:
            workflow = backup_verifier
        else:
            workflow = workflow >> backup_verifier

    return workflow


# Main ----------------------------------------------------------------


def create_task(programs, args, cmdargs, properties):
    """
    Create a task for a configurable splitting workflow.
    The workflow is configured based on the workflow name. 
    For example, to run a workflow together with klee and slowbeast,
    the configuration should be:
    split-klee-merge-sb

    This configuration splits the program with the help of KLEE 
    until two splits are generated. Then it merges the results
    and executes slowbeast on the merged program.

    Split options:
        - split: Shorthand for split2
        - split0: Skips the splitter completely
        - split1: Never executes the splitter 
        - split2: Stops the splitter after 2 splits are generated (or timeout)
        - split4: Stops the splitter after 4 splits are generated (or timeout)
        - split8: Stops the splitter after 8 splits are generated (or timeout)
    
    Split verifier options:
        - klee: Executes KLEE for 4 seconds to determine splits
        - cbmc: Executes CBMC for 4 seconds to determine splits
        - esbmc: Executes ESBMC-kInd for 4 seconds to determine splits

    Merge options:
        - merge: Applies a hierarchical merge that select the parent in the split tree 
                  that contains all remaining splits
        - mp:   Does not merge and executes the verifier on all splits  

    Backup verifier options:
        - sb: Runs a single instance of SlowBeast
        - klee: Runs a single instance of KLEE
        - cbmc: Runs a single instance of CBMC
        - esbmc: Runs a single instance of ESBMC-kInd
        - cpapred: CPAchecker with Predicate Abstraction
        - cpaki: CPAchecker with k-Induction
        - mopsa: A single instance of MOPSA

        It is possible to run multiple verifier as backup verifier. For
        example, sb-klee would run SlowBeast and KLEE in parallel. When
        the merge option 'mp' is selected, then the different verifiers
        are executed on different splits (with WorkStealing). In this
        case, it can make sense to run multiple instances of the same verifier.

    """

    assert len(programs) == 1, "Multiple programs not supported by the splitter"

    workflow       = None
    verifier_chain = cmdargs.workflow.split("-")
    
    while verifier_chain and verifier_chain[0].startswith("split"):
        splitop, splitv, *verifier_chain = verifier_chain

        mergeop = None
        if verifier_chain[0] in ["merge", "cpamerge", "cmerge", "cmerge10", "cmerge20", "cmerge40", "cmerge60"]:
            mergeop        = verifier_chain[0]
            verifier_chain = verifier_chain[1:]
        
        split_scheduler = create_dynamic_splitter(
            splitop, splitv, cmdargs, properties, mergeop = mergeop
        )

        if split_scheduler is None: continue

        if workflow is None:
            workflow = split_scheduler(programs[0])
        else:
            workflow = workflow >> split_scheduler

    assert len(verifier_chain) > 0, "You need to define a backup verifier"

    backup_verifier = create_backup_verifier(
        verifier_chain, cmdargs, properties,
    )

    if workflow is None:
        workflow = backup_verifier(programs) >> collect_results
    else:
        workflow = workflow >> export_residual >> backup_verifier >> collect_results
    
        workflow = CheckWithBackup(
            workflow, 
            backup_verifier(programs) >> collect_results
        )

    timeout = None
    if cmdargs.timeout is not None:
        timeout = cmdargs.timeout

    return TimeoutWatchdog(
        workflow,
        timeout=timeout
    )



def workflow(programs, args, cmdargs, properties):
    return workflow_splitflow(programs, args, cmdargs, properties)


def workflow_splitflow(programs, args, cmdargs, properties):
    return Workflow([create_task(programs, args, cmdargs, properties)])
