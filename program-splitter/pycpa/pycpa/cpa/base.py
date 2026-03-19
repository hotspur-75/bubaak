
from .lattice import NumberDomain, CompositeDomain, SetDomain, FlatDomain
from .lattice import NumberElement, CompositeElement, SetElement, FlatElement
from .lattice import BottomElement

from ..visitors import visit_tree

CALL_STACK_DEPTH = 64


class ProgramAnalysis:

    def domain(self):
        raise NotImplementedError()
    
    def init_state(self, cfa):
        raise NotImplementedError()

    def merge(self, first_state, second_state, precision = None):
        return second_state # Merge SEP

    def stop(self, state, reached_set, precision = None):
        return state in reached_set

    def refine(self, state, precision, reached_set):
        return state, precision # No refinement

    # Transfer relation --------------------------------

    def handle_edge(self, state, cfa_edge):
        return state, None
    
    def _handle(self, state, cfa_edge):
        return getattr(self, f"handle_{cfa_edge.type}", self.handle_edge)(state, cfa_edge)

    def next(self, state):
        for next_edge in state.cfa_node.successors():
            next_state = self._handle(state, next_edge)
            if next_state is None or next_state[0] is None: continue
            yield next_state


class AnalysisState(object):

    def __init__(self, cfa_node = None):
        self.cfa_node = cfa_node

    def abstraction(self):
        raise NotImplementedError()

    def _state_key_(self):
        return self.__class__.__name__
    
    def _index_key_(self):
        return self.__class__.__name__
    
    def __eq__(self, other):
        return self._state_key_() == other._state_key_()
    
    def __neq__(self, other):
        return not self.__eq__(other)
    
    def __hash__(self):
        return hash(self._state_key_())
    

def find_compatible(state, reached_set):
    try:
        return reached_set.compatible_states(state)
    except TypeError:
        return reached_set
    

# Composite Analysis --------------------------------


class CompositeState(AnalysisState):
    
    def __init__(self, *elements, cfa_node = None):
        super().__init__(cfa_node or elements[0].cfa_node)
        self.elements = elements

    def abstraction(self):
        return CompositeElement(*[e.abstraction() for e in self.elements])
    
    def _state_key_(self):
        return self.elements
    
    def _index_key_(self):
        return tuple(e._index_key_() for e in self.elements)
    
    def __getitem__(self, index):
        return self.elements[index]
    
    def __len__(self):
        return len(self.elements)
    
    def __iter__(self):
        for element in self.elements: yield element

    def __repr__(self):
        return str(self.elements)
    
    def __getattr__(self, key):
        for element in self.elements:
            try:
                return getattr(element, key)
            except AttributeError:
                pass
        raise AttributeError(f"'CompositeState' object has no attribute '{key}'")


class CompositeAnalysis(ProgramAnalysis):
    
    def __init__(self, *subanalyses):
        super().__init__()
        self.subanalyses = subanalyses

    def domain(self):
        return CompositeDomain(*[analysis.domain() for analysis in self.subanalyses])
    
    def init_state(self, cfa):
        init_states = [analysis.init_state(cfa) for analysis in self.subanalyses]
        return CompositeState(*[state[0] for state in init_states]), tuple(state[1] for state in init_states)
    
    def merge(self, first_state, second_state, precision = None):
        
        has_changed = False
        merged_state = []
        for i, (first_substate, second_substate) in enumerate(zip(first_state, second_state)):
            subprecision = precision[i] if precision is not None else None
            merged_substate = self.subanalyses[i].merge(first_substate, second_substate, subprecision)
            merged_state.append(merged_substate)
            has_changed = merged_substate != second_substate

        if has_changed: return CompositeState(*merged_state)
        return second_state # Merg SEP

    def stop(self, state, reached_set, precision = None):
        if state in reached_set: return True

        for second_state in find_compatible(state, reached_set):
            for i, (first_substate, second_substate) in enumerate(zip(state, second_state)):
                subprecision = precision[i] if precision is not None else None
                if not self.subanalyses[i].stop(first_substate, [second_substate], subprecision):
                    return False
        
        return True

    def refine(self, state, precision, reached_set):
        new_states = []
        new_precisions = []
        
        for i, substate in enumerate(state):
            subanalysis = self.subanalyses[i]
            new_state, new_precision = subanalysis.refine(substate, precision[i], {(r[0][i], r[1][i]) for r in reached_set})
            new_states.append(new_state)
            new_precisions.append(new_precision)

        return CompositeState(*new_states), tuple(new_precisions)


    # Transfer relation --------------------------------

    def handle_edge(self, state, cfa_edge):
        new_states = []
        new_precisions = []
        
        for i, substate in enumerate(state):
            subanalysis = self.subanalyses[i]
            new_state, new_precision = subanalysis._handle(substate, cfa_edge)
            if new_state is None: return None, None
            new_states.append(new_state)
            new_precisions.append(new_precision)

        return CompositeState(*new_states), tuple(new_precisions)


# Basic analysis --------------------------------

class LocationState(AnalysisState):

    def __init__(self, location, cfa_node = None):
        super().__init__(cfa_node)
        self.location = location

    def abstraction(self):
        return self.location

    def _state_key_(self):
        return self.location.value
    
    def _index_key_(self):
        return self.location.value

    def __repr__(self) -> str:
        return f"Loc({self.location.value})"


class LocationAnalysis(ProgramAnalysis):

    def domain(self):
        return NumberDomain()
    
    def init_state(self, cfa):
        init_node = cfa.init_node()
        location = self.domain().abstract(init_node.node_id)
        return LocationState(location, cfa_node = init_node), None
    
    def handle_edge(self, state, cfa_edge):
        next_node = cfa_edge.successor
        location  = self.domain().abstract(next_node.node_id)
        return LocationState(location, cfa_node = next_node), None
    
# Call stack analysis --------------------------------


class FunctionCall:

    def __init__(self, call_node):
        self.call_node = call_node
    
    def called_function(self):
        return self.call_node.cfg_node.called_function()
    
    def called_function_name(self):
        return self.called_function().function_name()
    
    def return_node(self):
        return self.call_node.call_exit()
    
    def call_sides(self):
        return self.called_function().call_sides()
    
    def __repr__(self):
        function_name = self.called_function_name()
        position      = self.call_node.ast_node.start_point[0]
        return f"{function_name}@{position}"


class CallStackState(AnalysisState):

    def __init__(self, callstack, cfa_node = None):
        super().__init__(cfa_node)
        self.callstack = callstack

    def abstraction(self):
        return self.callstack

    def _state_key_(self):
        return tuple(self.callstack.value)
    
    def _index_key_(self):
        return ".".join([str(c) for c in self.callstack.value])

    def __repr__(self) -> str:
        return str(self.callstack)
    

class CallStackAnalysis(ProgramAnalysis):

    def __init__(self, max_call_depth = -1, stop_recursion = False):
        super().__init__()
        self.max_call_depth = max_call_depth # Stops the analysis if exceeded
        self.stop_recursion = stop_recursion # Stops the analysis if a recursive call is found

    def domain(self):
        return FlatDomain()
    
    def init_state(self, cfa):
        init_node = cfa.init_node()
        call_stack = self.domain().abstract([])
        return CallStackState(call_stack, cfa_node = init_node), None
    
    def handle_FunctionCallEdge(self, state, cfa_edge):
        call_node = cfa_edge.predecessor
        call_cfg  = call_node.cfg_node
        called_function = call_cfg.called_function()
        if called_function is None: return state, None

        if self.stop_recursion and call_node in set(f.call_node for f in state.callstack.value):
            print("Detected recursion. Abort.")
            return None, None

        #callee = cfa_edge.predecessor.ast_node
        called_function = FunctionCall(call_node)

        call_stack = state.callstack.value + [called_function]

        if self.max_call_depth >= 0 and len(call_stack) > self.max_call_depth:
            return None, None # We stop here!

        if len(call_stack) >= CALL_STACK_DEPTH:
            print("Call stack size exceeded. Becoming imprecise.")
            return state, None

        call_stack = self.domain().abstract(call_stack)

        return CallStackState(call_stack, cfa_node = cfa_edge.successor), None
    
    def handle_FunctionReturnEdge(self, state, cfa_edge):
        return self.handle_FunctionExitEdge(state, cfa_edge)
    
    def handle_FunctionExitEdge(self, state, cfa_edge):
        target = cfa_edge.successor

        call_stack_size = len(state.callstack.value)
        if call_stack_size == 0 or call_stack_size >= CALL_STACK_DEPTH: 
            return state, None
      
        return_node = state.callstack.value[-1].return_node()

        if target.is_composed():
            target_nodes = target.nodes()
            return_nodes = return_node.nodes()

            for i in range(len(target_nodes)):
                if target_nodes[i] != return_nodes[i]:
                    if target_nodes[i].type != "TraceExitingNode":
                        return None, None
        else:
            if target != return_node: return None, None

        call_stack = state.callstack.value[:-1]
        call_stack = self.domain().abstract(call_stack)

        return CallStackState(call_stack, cfa_node = cfa_edge.successor), None

# Loop Analysis ---------------------------------------------------------------

class LoopAnalysisState(AnalysisState):
    def __init__(self, loops, stop = False, cfa_node = None):
        super().__init__(cfa_node)
        self.loops = loops
        self._stop  = stop

    def abstraction(self):
        return self.loops
    
    def entry(self, domain, loop):
        identifier = f"loop@{loop.head.node_id}"
        loops = dict(self.loops.value)
        loops[identifier] = loops.get(identifier, 0) + 1
        loops = domain.abstract(loops)
        return LoopAnalysisState(loops, cfa_node = loop.head)
    
    def visit_head(self, domain, loop):
        return self.entry(domain, loop) # Is currently the same. Change in future.

    def exit(self, domain, loop):
        identifier = f"loop@{loop.head.node_id}"
        loops = dict(self.loops.value)
        loops.pop(identifier, None)
        loops = domain.abstract(loops)
        return LoopAnalysisState(loops, cfa_node = loop.head)

    def abstract(self, domain, max_loop_unrolls):
        if max_loop_unrolls < 0: return self
        if not any(c > max_loop_unrolls for c in self.loops.value.values()): return self
        loops = dict(self.loops.value)
        for identifier, count in loops.items():
            if count > max_loop_unrolls:
                loops[identifier] = max_loop_unrolls
        
        loops = domain.abstract(loops)
        return LoopAnalysisState(loops, cfa_node = self.cfa_node)

    def stop(self):
        if self._stop: return self
        return LoopAnalysisState(self.loops, stop = True, cfa_node=self.cfa_node)
    
    def is_stop(self):
        return self._stop
    
    def size(self):
        return self.num_loops()
    
    def num_loops(self):
        return sum(c for c in self.loops.value.values())

    def _state_key_(self):
        return tuple(self.loops.value.items())
    
    def _index_key_(self):
        return ".".join(self.loops.value)

    def __repr__(self) -> str:
        return str(self.loops)


class LoopAnalysis(ProgramAnalysis):

    def __init__(self, unroll_loop = -1, max_loop_iterations = -1):
        super().__init__()
        self.unroll_loop = unroll_loop # Becomes imprecise afterwards
        self.max_loop_iterations = max_loop_iterations # Stops the analysis if exceeded

        self._loop_heads  = {}
        self._entry_edges = {}
        self._exit_edges  = {}

    def _register_loop(self, loop):
        if loop.head in self._loop_heads: return self._loop_heads[loop.head]
        self._loop_heads[loop.head] = loop

        for entry_edge in loop.incoming():
            if entry_edge.successor != loop.head: continue
            self._entry_edges[entry_edge] = loop

        for exit_edge in loop.outgoing():
            self._exit_edges[exit_edge] = loop

        return loop


    def domain(self):
        return FlatDomain()
    
    def init_state(self, cfa):
        init_node = cfa.init_node()
        loops = self.domain().abstract({})
        state = LoopAnalysisState(loops, cfa_node = init_node)
        if self.max_loop_iterations == 0: state = state.stop()
        return state, None
    
    def handle_FunctionCallEdge(self, state, cfa_edge):
        return state, None
    
    def handle_edge(self, state, cfa_edge):

        start_state = state

        if cfa_edge in self._exit_edges:
            exit_loop = self._exit_edges[cfa_edge]
            state = state.exit(self.domain(), exit_loop)

        loc = cfa_edge.successor
        if cfa_edge.is_function_exit():
            return state, None
        
        ast_node = loc.ast_node
        if ast_node is not None and ast_node.type in ["labeled_statement", "while_statement", 
                                                        "do_statement", "for_statement"]:
            for loop in loc.loop_info():
                loop = self._register_loop(loop)

        if loc in self._loop_heads:
            if state.is_stop(): return None, None
            entry_loop = self._loop_heads[loc]
            state = state.visit_head(self.domain(), entry_loop)
            state = state.abstract(self.domain(), self.unroll_loop)

            if state.abstraction() == start_state.abstraction():
                return start_state, None

        if (self.max_loop_iterations >= 0 
            and any(count >= self.max_loop_iterations for count in state.abstraction().value.values())):
            return state.stop(), None

        return state, None


# ARG Analysis ----------------------------------------------------------------


class ARGState(AnalysisState):
    
    def __init__(self, base, parents, cfa_node = None):
        super().__init__(cfa_node or base.cfa_node)
        self.base = base
        self.parents = parents
        self.children = []

        if self.parents is not None:
            for parent in self.parents:
                parent.children.append(self)


    def abstraction(self):
        return self.base.abstraction()

    
    def _state_key_(self):
        return self.base._state_key_()
    
    def _index_key_(self):
        return self.base._index_key_()

    def __repr__(self):
        return str(self.base)
    
    def __getattr__(self, key):
        return getattr(self.base, key)


class ARGAnalysis(ProgramAnalysis):

    def __init__(self, subanalysis):
        self.subanalysis = subanalysis

    def domain(self):
        return FlatDomain()
    
    def init_state(self, cfa):
        init_state, init_precision = self.subanalysis.init_state(cfa)
        return ARGState(init_state, None), init_precision
    
    def merge(self, first_state, second_state, precision = None):
        merged_substate = self.subanalysis.merge(first_state.base, second_state.base, precision)

        if merged_substate is None or merged_substate == second_state.base:
            return second_state
        
        return ARGState(merged_substate, parents = [first_state, second_state])
    
    def refine(self, state, precision, reached_set):
        new_state, new_precision = self.subanalysis.refine(
            state.base,
            precision,
            {(r[0].base, r[1]) for r in reached_set}
        )

        if new_state != state.base or new_precision != precision:
            return ARGState(new_state, parents = [state]), new_precision

        return state, precision
    
    def handle_edge(self, state, cfa_edge):
        new_state, new_precision = self.subanalysis.handle_edge(
            state.base,
            cfa_edge
        )

        if new_state is None: return None, None
        return ARGState(new_state, parents = [state]), new_precision



# Target CPA ----------------------------------------

class TargetState(AnalysisState):

    def __init__(self, target = False, cfa_node = None):
        super().__init__(cfa_node)
        self.target = target

    def abstraction(self):    
        return self.target
    
    def is_target(self):
        return self.target.value
    
    def _state_key_(self):
        return "E" if self.target.value else "C"
    
    def _index_key_(self):
        return self._state_key_()

    def __repr__(self):
        return str(self._state_key_())


class TargetAnalysis(ProgramAnalysis):

    def __init__(self, target_function = None):
        if target_function is None: 
            target_function = lambda node: node.is_error_node()
        self.target_function = target_function

    def domain(self):
        return FlatDomain()
    
    def init_state(self, cfa):
        is_target = self.target_function(cfa.init_node())
        return TargetState(target = self.domain().abstract(is_target), cfa_node = cfa.init_node()), None
    
    def handle_edge(self, state, cfa_edge):
        if state.is_target(): return None, None

        new_is_target = self.target_function(cfa_edge.successor)
        if not new_is_target: return state, None
        
        new_is_target = self.domain().abstract(new_is_target)
        return TargetState(target = new_is_target, cfa_node = cfa_edge.successor), None
        

# Variable definition --------------------------------

class DefVarState(AnalysisState):

    def __init__(self, vars, cfa_node = None):
        super().__init__(cfa_node)
        self.vars = vars

    def abstraction(self):
        return self.vars
    
    def _state_key_(self):
        return tuple(self.vars.current_set)
    
    def _index_key_(self):
        return "DefVarState"

    def __repr__(self) -> str:
        return str(self.vars.current_set)


class DefVarAnalysis(ProgramAnalysis):
    
    def domain(self):
        return SetDomain()
    
    def init_state(self, cfa):
        empty_set = self.domain().abstract(set())
        return DefVarState(empty_set, cfa_node = cfa.init_node()), None
    
    def handle_DeclarationEdge(self, state, cfa_edge):
        declaration = cfa_edge.declaration
        
        defined_vars = set()
        declarator = declaration.children[1]
        if declarator.type == "init_declarator":
            var_declarator = declarator.child_by_field_name("declarator")
            defined_vars.add(var_declarator.text.decode('utf-8'))
        
        if declarator.type == "identifier":
            defined_vars.add(declarator.text.decode('utf-8'))

        new_element = self.domain().abstract(defined_vars)
        next_element = state.abstraction().union(new_element)
        return DefVarState(next_element, cfa_node = cfa_edge.successor), None
    
    def handle_FunctionCallEdge(self, state, cfa_edge):
        declaration = cfa_edge.successor.cfg_node.ast_node
        defined_vars = set()
        declarator = declaration.children[1]

        if declarator.type == "function_declarator":
            for parameter in declarator.child_by_field_name("parameters").children:
                if parameter.type == "parameter_declaration":
                    var_declarator = parameter.child_by_field_name("declarator")
                    defined_vars.add(var_declarator.text.decode('utf-8'))
        
        new_element = self.domain().abstract(defined_vars)
        next_element = state.abstraction().union(new_element)
        return DefVarState(next_element, cfa_node = cfa_edge.successor), None

    def handle_edge(self, state, cfa_edge):
        return state, None
    
# Path Analysis --------------------------------

class PathState(AnalysisState):

    def __init__(self, path, cfa_node):
        super().__init__(cfa_node)
        self.path = path
    
    def abstraction(self):
        return self.path
    
    def current_decision(self):
        return self.path.value[0]
    
    def is_unconstrained(self):
        return len(self.path.value) == 0
    
    def _state_key_(self):
        return self._index_key_()
    
    def _index_key_(self):
        if len(self.path.value) == 0: return "0"
        return "".join("T" if v else "F" for v in self.path.value)

    def __repr__(self) -> str:
        return self._index_key_()


class PathAnalysis(ProgramAnalysis):

    def __init__(self, branch_decisions):
        self.branch_decisions = branch_decisions

    def domain(self):
        return FlatDomain()
    
    def init_state(self, cfa):
        path = self.domain().abstract(self.branch_decisions)
        return PathState(path, cfa_node = cfa.init_node()), None
    
    def handle_AssumeEdge(self, state, cfa_edge):
        if state.is_unconstrained(): return state, None
        truth_value = cfa_edge.truth_value
        
        if truth_value != state.current_decision(): return None, None

        new_path = state.path.value[1:]
        new_path = self.domain().abstract(new_path)

        return PathState(new_path, cfa_node = cfa_edge.successor), None


# Decision Tracking ---------------------------------------------

class DecisionTrackingState(AnalysisState):

    def __init__(self, path, cfa_node):
        super().__init__(cfa_node)
        self.path = path
    
    def abstraction(self):
        return self.path
    
    def decisions(self):
        return [e.truth_value for e in self.path.value]
    
    def has_branching_decision(self):
        return any(
            len(e.predecessor.successors()) > 1
            for e in self.path.value
        )
    
    def last_decision_edge(self):
        return self.path.value[-1]
    
    def last_branching_decision(self):
        for e in reversed(self.path.value):
            if len(e.predecessor.successors()) > 1:
                return e
        return None
    
    def _state_key_(self):
        return self._index_key_()
    
    def _index_key_(self):
        if len(self.path.value) == 0: return "0"
        return "".join("T" if v else "F" for v in self.decisions())

    def __repr__(self) -> str:
        return self._index_key_()


class DecisionTrackingAnalysis(ProgramAnalysis):

    def domain(self):
        return FlatDomain()
    
    def init_state(self, cfa):
        path = self.domain().abstract([])
        return DecisionTrackingState(path, cfa_node = cfa.init_node()), None
    
    def handle_AssumeEdge(self, state, cfa_edge):     
        new_path = state.path.value + [cfa_edge]
        new_path = self.domain().abstract(new_path)

        return DecisionTrackingState(new_path, cfa_node = cfa_edge.successor), None
    

# Side Effect Analysis ---------------------------------------------

class SideEffectState(AnalysisState):

    def __init__(self, side_effect_edge, cfa_node = None, parent = None):
        super().__init__(cfa_node)
        self.side_effect_edge = side_effect_edge
        self.parent = parent

    def abstraction(self):
        return self.side_effect_edge
    
    def has_side_effect(self):
        return not self.side_effect_edge.is_bottom()
    
    def _state_key_(self):
        if self.side_effect_edge.is_bottom():
            return "_"
        return self.side_effect_edge.value
    
    def _index_key_(self):
        return "SideEffectState"

    def __repr__(self) -> str:
        return str(self._state_key_())


def has_function_calls(ast_node, scope = None):
    for call in visit_tree(ast_node, lambda node: node.type == "call_expression"):
        if scope is None: return True
        function_node = call.child_by_field_name("function")
        if function_node.type != "identifier": return True

        function_name = function_node.text.decode('utf-8')
        return function_name in scope.function_definitions()

    return False

class SideEffectAnalysis(ProgramAnalysis):

    def domain(self):
        return FlatDomain()
    
    def init_state(self, cfa):
        side_effect = BottomElement()
        return SideEffectState(side_effect, cfa_node = cfa.init_node()), None
    
    def _handle_side_effect(self, state, cfa_edge):
        side_effect_edge = self.domain().abstract(cfa_edge)
        return SideEffectState(side_effect_edge, cfa_node = cfa_edge.successor, parent = state)

    def handle_DeclarationEdge(self, state, cfa_edge):
        if has_function_calls(cfa_edge.declaration, cfa_edge.cfg_node.scope):
            return self._handle_side_effect(state, cfa_edge), None
        return state, None
    
    def handle_StatementEdge(self, state, cfa_edge):
        if has_function_calls(cfa_edge.statement, cfa_edge.cfg_node.scope):
            return self._handle_side_effect(state, cfa_edge), None
        return state, None
    
    def handle_AssumeEdge(self, state, cfa_edge):
        if has_function_calls(cfa_edge.condition, cfa_edge.cfg_node.scope):
            return self._handle_side_effect(state, cfa_edge), None
        return state, None
    
    def handle_FunctionCallEdge(self, state, cfa_edge):
        call_node = cfa_edge.call_node.ast_node
        
        for call_expression in visit_tree(call_node, 
                                          lambda node: node.type == "call_expression", 
                                          lambda node: node.type != "call_expression"):
            parameters = call_expression.child_by_field_name("arguments")
            if has_function_calls(parameters, cfa_edge.cfg_node.scope): 
                return self._handle_side_effect(state, cfa_edge), None
        
        return state, None