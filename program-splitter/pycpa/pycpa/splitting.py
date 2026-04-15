from . import cfg
from .cfa import ControlFlowAutomata

from .algorithm import run_analysis 

from .cpa.base import ARGAnalysis, CompositeAnalysis, LocationAnalysis, CallStackAnalysis, LoopAnalysis, DecisionTrackingAnalysis, SideEffectAnalysis
from .cpa.base import PathAnalysis, TargetAnalysis
from .cpa.rewrites import LoopUnrollingRewriter, FunctionCloningRewriter, SplittingRewriter, SideEffectRewriter, _replace_all
from .cpa.rewrites import clean_skip_annotations

from .heuristics import is_structurally_trivial

from .env import GLOBAL_TIMER, global_timeout

def run_splitter(program, cpas = None, split_fn = None, **kwargs):

    if isinstance(program, str): program = ControlFlowAutomata(cfg(program))
    
    # --- UPDATED SPLIT CONDITION ---
    if split_fn is None: 
        def default_split_fn(state):
            branch_state = last_branching_state(state)
            if branch_state is None: 
                return False
            
            # Pure structural check. No is_in_loop bypass!
            if is_structurally_trivial(branch_state.cfa_node, threshold=3):
                return False
            
            return True
            
        split_fn = default_split_fn
    # -------------------------------

    counterexamples = set()

    target_fn = lambda state: (state.has_side_effect() or 
                                (branching_decisions(state) not in counterexamples) and split_fn(state))

    analysis = _init_cpas(cpas)
    
    # ... rest of the run_splitter logic ...

    split_state    = None
    while split_state is None:
        GLOBAL_TIMER.tick()

        split_state = run_analysis(analysis, program, target_fn = target_fn)
        if split_state is None: return [program]

        if split_state.has_side_effect():
            program     = handle_side_effect(program, split_state)
            split_state = None
            continue
        
        try:
            split_state, program   = _unroll_or_backtrack(split_state, program, config = kwargs)
            if split_state is None: counterexamples = set()
        except ValueError as e:
            print("Exception: %s" % str(e))
            counterexamples.add(branching_decisions(split_state))
            split_state = None
        
    splits = _split_program(program, split_state)
    assert len(splits) == 1 or len(splits) == 2, "Something went wrong during splitting"
    return splits


def run_deepening_splitter(program, cpas = None, split_fn = None, 
                            loop_bound = -1, clone_bound = -1, **kwargs):
    
    max_iter = max(loop_bound, clone_bound)
    if max_iter == -1: max_iter = 1e9

    splits = run_splitter(program, 
                          cpas = cpas, 
                          split_fn = split_fn, 
                          loop_bound = 0, 
                          clone_bound = 0,
                          **kwargs)
    
    if len(splits) != 1: return splits
    
    program = splits[0]
    for it in range(1, max_iter):
        GLOBAL_TIMER.tick()
     
        prev_program = program
   
        # Run splitter
        splits = run_splitter(program,
                                  cpas = cpas, 
                                  split_fn = split_fn, 
                                  loop_bound = 1  if loop_bound == -1 or it <= loop_bound else 0, 
                                  clone_bound = 1 if clone_bound == -1 or it <= clone_bound else 0, 
                                  **kwargs)

        if len(splits) != 1: return splits

        program = splits[0]
        if prev_program == program:
            print(f"[INCREMENTAL SPLITTER] Increase unwinding iteration")
            # Reset annotations
            program = clean_skip_annotations(program.source_code(), root_node = program.root_ast_node)
            program = ControlFlowAutomata(cfg(program))
    
    return splits


def run_deep_splitter(program, path = [], cpas = [], **kwargs):
    cpas     = [PathAnalysis(path)] + cpas
    split_fn = lambda state: state.is_unconstrained() and state.has_branching_decision()
    return run_splitter(program, cpas, split_fn, **kwargs)


def run_target_splitter(program, cpas = None, target_fn = None, **kwargs):
    if cpas is None: cpas = []  
    cpas.append(TargetAnalysis(target_fn))
    split_fn = lambda state: state.is_target() and state.has_branching_decision()
    return run_splitter(program, cpas, split_fn, **kwargs)

# Backtracking ----------------------------------------------------------

def _unroll_or_backtrack(split_state, program, config = None):
    GLOBAL_TIMER.tick()
    if split_state is None: raise ValueError("Cannot find a feasible split state")
   
    branching_state = last_branching_state(split_state)
    if branching_state is None: 
        raise ValueError("Stopped before finding a suitable split location")

    if is_location_modifiable(branching_state): return branching_state, program
   
    new_program = unroll_split_location(program, branching_state, config = config)

    if program != new_program: return None, new_program

    return _unroll_or_backtrack(branching_state.parents[0], program, config = config)


# Checks ----------------------------------------------------------------

def is_location_modifiable(state):

    if any(_is_forbidden_function(call.called_function_name()) 
           for call in state.callstack.value):
        return False
    
    if any(len(call.call_sides()) > 1 for call in state.callstack.value):
        return False
    
    if state.num_loops() > 0: return False

    return True


def _is_forbidden_function(function_name):
    return "_clone_" not in function_name and function_name.startswith("__VERIFIER")


def unroll_split_location(program, split_state, config = None):

    parent_states = split_state.parents
    while parent_states and not is_location_modifiable(parent_states[0]):
        split_state = parent_states[0]
        parent_states = split_state.parents
    
    if config is None: config = {}

    for call in reversed(split_state.callstack.value):
        if len(call.call_sides()) > 1:
            return _clone_call(program, split_state, call, clone_bound = config.get("clone_bound", -1))

    if split_state.num_loops() > 0:
        return _unroll_loop(program, split_state, split_state.loops.value, loop_bound = config.get('loop_bound', -1))
        
    return program
        
    
def handle_side_effect(program, split_state):
    side_effect_edge = split_state.side_effect_edge.value
    
    rewriter = SideEffectRewriter()
    rewrites = rewriter.rewrite_edge(split_state, side_effect_edge)

    return rebuild_cfa(program, rewrites)


def _clone_call(program, split_state, call, clone_bound = -1):
    call_start = split_state
    while call_start.cfa_node != call.call_node:
        call_start = call_start.parents[0]
    
    pred_edge = call_start.cfa_node.predecessors()[0]

    rewriter = FunctionCloningRewriter(clone_iter_bound = clone_bound)
    rewrites = rewriter.rewrite_edge(split_state, pred_edge)

    return rebuild_cfa(program, rewrites)


def _unroll_loop(program, split_state, loops, loop_bound = -1):
    loop_head = split_state
    while f"loop@{loop_head.location.value}" not in loops:
        loop_head = loop_head.parents[0]

    pred_edge = loop_head.cfa_node.predecessors()[0]

    rewriter = LoopUnrollingRewriter(loop_iter_bound = loop_bound)
    rewrites = rewriter.rewrite_edge(loop_head, pred_edge)

    return rebuild_cfa(program, rewrites)


def _split_program(program, split_state):
    successors = split_state.cfa_node.successors()
    rewriter   = SplittingRewriter() 

    return [
        rebuild_cfa(program, rewriter.rewrite_edge(split_state, successor))
        for successor in successors
    ]


# Helper ----------------------------------------------------------------

def _init_cpas(cpas = None):
    if cpas is None: cpas = []

    if not any(isinstance(cpa, SideEffectAnalysis) for cpa in cpas):
        cpas = [SideEffectAnalysis()] + cpas
    
    #if not any(isinstance(cpa, DecisionTrackingAnalysis) for cpa in cpas):
    #    cpas = [DecisionTrackingAnalysis()] + cpas
    
    if not any(isinstance(cpa, LoopAnalysis) for cpa in cpas):
        cpas = [LoopAnalysis(max_loop_iterations = 1)] + cpas
    
    if not any(isinstance(cpa, CallStackAnalysis) for cpa in cpas):
        cpas = [CallStackAnalysis(stop_recursion = True)] + cpas
    
    if not any(isinstance(cpa, LocationAnalysis) for cpa in cpas):
        cpas = [LocationAnalysis()] + cpas    
    
    return ARGAnalysis(CompositeAnalysis(*cpas))


def rebuild_cfa(program, rewrites):
    if rewrites is None or len(rewrites) == 0: return program      
    current_source_code = program.control_flow_graph.root_node.text.decode('utf-8')
    
    # Apply rewrites --------------------------------

    rewrite_nodes   = [rew[0] for rew in rewrites]
    rewrite_targets = [rew[1] for rew in rewrites]
    new_source_code = _replace_all(current_source_code, rewrite_nodes, rewrite_targets)

    # Build new CFA --------------------------------

    return ControlFlowAutomata(cfg(new_source_code))


# ----------------------------------------------------------------

def arg_path(arg_state):

    path = []
    while arg_state is not None:
        path.append(arg_state)
        if arg_state.parents is None or len(arg_state.parents) == 0: break
        arg_state = arg_state.parents[0]
    
    return path[::-1]


def branching_decisions(arg_state):
    path      = arg_path(arg_state)
    decisions = []

    for i in range(len(path) - 1):
        if len(path[i].children) > 1:
            decisions.append((path[i].cfa_node.node_id, path[i + 1].cfa_node.node_id))

    return tuple(decisions)

def last_branching_state(arg_state):

    while arg_state and len(arg_state.children) <= 1:
        if arg_state.parents is None or len(arg_state.parents) == 0: return None
        arg_state = arg_state.parents[0]

    return arg_state
