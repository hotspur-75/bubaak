from bbk.workflow import Workflow

from workflows.flows.predictive_split import PeriodicMergeTask, heuristic_predictor

from workflows.flows.verifiers import (
    CPAPredicateAnalysis,
    CPAkInduction,
    KLEEVerifier,
    SVCOMPVerifier,
)

# ============================================================================
# GLOBAL CONFIGURATIONS
# ============================================================================
PERIODIC_MERGE_TIMEOUT = 1000
MAX_SPLIT_DEPTH = 10  

INDIVIDUAL_TIMEOUTS = {
    "CPAPredicateAnalysis": 40,
    "CPAkInduction": 40,  # 100s prevents CPA's early heuristic drop!
    "KLEEVerifier": 20,
    "SVCOMPVerifier": 40
}
# ============================================================================

def workflow(programs, workflow_args, args, properties):
    target_property = properties[0]
    
    # 1. Pass the timeouts NATIVELY to the wrappers. 
    # Bubaak's OS-killer will now correctly monitor the internal ProcessTasks!
    verifiers = [
        #PA
        CPAPredicateAnalysis(args, target_property, timeout=INDIVIDUAL_TIMEOUTS["CPAPredicateAnalysis"]),
        #KI
        CPAkInduction(args, target_property, timeout=INDIVIDUAL_TIMEOUTS["CPAkInduction"]),
        #SE
        KLEEVerifier(args, target_property, timeout=INDIVIDUAL_TIMEOUTS["KLEEVerifier"]),
        #BMC
        SVCOMPVerifier("esbmc-incr", args, target_property, timeout=INDIVIDUAL_TIMEOUTS["SVCOMPVerifier"]),
    ]
    
    main_task = PeriodicMergeTask(
        verifiers=verifiers,
        predictor=heuristic_predictor,
        programs=programs,
        properties=properties,
        split_timeout=PERIODIC_MERGE_TIMEOUT,
        max_width=16,
        max_height=MAX_SPLIT_DEPTH
    )
    
    return Workflow([main_task])