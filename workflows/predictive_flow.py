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
PERIODIC_MERGE_TIMEOUT = 300
MAX_SPLIT_DEPTH = 10  

INDIVIDUAL_TIMEOUTS = {
    "PA": 30,
    "KI": 150,
    "SE": 30,
    "BMC": 80
}
# ============================================================================

def workflow(programs, workflow_args, args, properties):
    target_property = properties[0]
    
    # 1. Pass the timeouts NATIVELY to the wrappers. 
    # Bubaak's OS-killer will now correctly monitor the internal ProcessTasks!
    verifiers = [
        #PA
        CPAPredicateAnalysis(args, target_property, timeout=INDIVIDUAL_TIMEOUTS["PA"]),
        #KI
        SVCOMPVerifier("esbmc-kind", args, target_property, timeout=INDIVIDUAL_TIMEOUTS["KI"]),
        #SE
        KLEEVerifier(args, target_property, timeout=INDIVIDUAL_TIMEOUTS["SE"]),
        #BMC
        SVCOMPVerifier("esbmc-incr", args, target_property, timeout=INDIVIDUAL_TIMEOUTS["BMC"]),
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