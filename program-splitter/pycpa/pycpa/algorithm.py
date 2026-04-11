from . import cfg
from .nodes import Node as CFGNode
from .graph import ControlFlowGraph

from .cfa import ControlFlowAutomata

from .utils import PrioritySet

from .env import GLOBAL_TIMER


def run_analysis(cpa_analysis, init_code_object, target_fn = None):
    
    target_fn_is_none = target_fn is None
    if target_fn_is_none: target_fn = lambda x: False

    cfa = _parse_cfa_from_code_object(init_code_object)
    init_state, init_precision = cpa_analysis.init_state(cfa)

    if target_fn(init_state): return init_state
    
    reached_set = ReachedSet()
    reached_set.add(init_state, init_precision)

    waitlist = PrioritySet([(_priority(init_state, init_precision), init_state, init_precision)])
    while len(waitlist) > 0:
        GLOBAL_TIMER.tick()

        _, state, precision = waitlist.pop()

        new_state, _ = cpa_analysis.refine(state, precision, reached_set)
        for next_state, next_precision in cpa_analysis.next(new_state):
            GLOBAL_TIMER.tick()
            for existing_state, existing_precision in find_compatible_states(next_state, reached_set):
                merged_state = cpa_analysis.merge(next_state, existing_state, next_precision)
                if merged_state != existing_state:
                    
                    # New merge state found. Emit!
                    if target_fn(merged_state): return merged_state

                    waitlist.remove((_priority(existing_state, existing_precision), existing_state, existing_precision))
                    waitlist.add((_priority(merged_state, next_precision), next_state, next_precision))

                    reached_set.remove(existing_state, existing_precision)
                    reached_set.add(merged_state, next_precision)
        
            # Add to newly discovered state
            if not cpa_analysis.stop(next_state, reached_set, precision = next_precision):
            
                if target_fn(next_state): return next_state

                reached_set.add(next_state, next_precision)
                waitlist.add((_priority(next_state, next_precision), next_state, next_precision))

    # If we end here, target_fn was None or the program is save
    if not target_fn_is_none: return None
    return {e[0] for e in reached_set}
        

def _priority(state, precision = None): # Make configurable
    try:
        return (tuple("T" if d else "F" for d in state.decisions()), state.cfa_node.node_id, hash(state))
    except AttributeError:
        return (state.cfa_node.node_id, hash(state))

def _emit_state(cpa_analysis, next_state, emit_states = False):
    if emit_states: return next_state
    return cpa_analysis.domain().concretize_once(next_state.abstraction())

def find_compatible_states(state, reached_set):
    try:
        return reached_set.compatible_states(state)
    except TypeError:
        return reached_set

# Helper ----------------------------------------------------------------


def _parse_cfa_from_code_object(code_object):

    if isinstance(code_object, str):
        # Assume this is a code string
        code_object = cfg(code_object)

    if isinstance(code_object, CFGNode):
        code_object = ControlFlowGraph(code_object.ast_node)

    if isinstance(code_object, ControlFlowGraph):
        code_object = code_object.entry_node()
    
    # Is definetly a control flow node here
    if isinstance(code_object, CFGNode):
        code_object = ControlFlowAutomata(code_object.graph, root_node = code_object)

    return code_object


class ReachedSet:

    def __init__(self):
        self._indexed_states = {}

    def _index_state(self, state):
        index_key = state._index_key_()

        if isinstance(index_key, (int, str)):
            index_key = [index_key]

        current_index = self._indexed_states
        for sub_key in index_key:
            try:
                current_index = current_index[sub_key]
            except KeyError:
                current_index[sub_key] = {}
                current_index = current_index[sub_key]
        try:        
            return current_index["__values__"]
        except KeyError:
            current_index["__values__"] = set()
            return current_index["__values__"]


    def add(self, state, precision = None):
        self._index_state(state).add((state, precision))

    def remove(self, state, precision = None):
        self._index_state(state).remove((state, precision))

    def compatible_states(self, search_state):
        return self._index_state(search_state)

    def has_state(self, state):
        return any(state == ex_state for ex_state, _ in self._index_state(state))

    def __contains__(self, key):
        if not isinstance(key, tuple): return self.has_state(key)
        return key in self._index_state(key[0])

    def __iter__(self):
        stack = [self._indexed_states]
        while len(stack) > 0:
            state = stack.pop()
            if "__values__" in state:
                for value in state["__values__"]: yield value
            
            stack.extend([v for k, v in state.items() if k != "__values__"])