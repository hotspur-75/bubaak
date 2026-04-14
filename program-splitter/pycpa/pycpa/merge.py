import random

from . import cfg
from .cfa import ControlFlowAutomata

from .cfa import _is_negation_of, _match_ast, _match_edge, intra_search, intra_search_bwd, to_dot
from .compose import intersect, negate

from .algorithm import run_analysis 

from .cpa.base import ARGAnalysis, CompositeAnalysis, LocationAnalysis, CallStackAnalysis, LoopAnalysis, SideEffectAnalysis, TargetAnalysis

from .gen import slice_program, ast_nodes_from_cfa

from .cpa.rewrites import _replace_all, _create_clone, _prefix_all_labels

from .nodes import BLOCK_REGISTRY

from .splitting import _unroll_loop, _clone_call

from .visitors import visit_tree

from .env import GLOBAL_TIMER


def run_merger(left_program, right_program):
    if isinstance(left_program, str): left_program = ControlFlowAutomata(cfg(left_program))
    if isinstance(right_program, str): right_program = ControlFlowAutomata(cfg(right_program))

    if len(left_program.source_code()) > len(right_program.source_code()):
        merged_program = left_program
        subprogram     = right_program
    else:
        merged_program = right_program
        subprogram     = left_program

    # We require that subprogram <= merged_program
    analysis = _init_intersection_cpas(lambda node: node.is_accepting()) # This should not appear
    difference_automata = intersect(subprogram, negate(merged_program))

    target_fn = lambda state: state.is_target()

    while True:
        GLOBAL_TIMER.tick()

        cex_state = run_analysis(analysis, difference_automata, target_fn = target_fn)
        if cex_state is None: break # We are happy
        
        # Preprocess code if necessary ----
        new_subprogram  = _preprocess_program(subprogram, cex_state.cfa_node.left())
        if new_subprogram.source_code() != subprogram.source_code():
            # We have to reanlyze
            subprogram = new_subprogram
            difference_automata = intersect(subprogram, negate(merged_program))
            continue

        # Integrate new trace in merged program ---
        merged_program = _integrate_paths(merged_program, subprogram, cex_state)
        difference_automata = intersect(subprogram, negate(merged_program))

    return merged_program.source_code()


# Integrate trace --------------------------------

def _integrate_paths(merged_program, subprogram, cex_state):
    branching_state = cex_state.parents[0]
    branching_point = branching_state.cfa_node

    # Preprocess merged program if necessary ---
    new_merged_program = _preprocess_program(merged_program, branching_point.right().base())
    if new_merged_program.source_code() != merged_program.source_code():
        return new_merged_program
    
    # Check if feasible branch location -------------------------
 
    if not is_feasible_merge_location(branching_state):
        raise ValueError("Stopped before finding a suitable merge location")

    # Find join point --------------
    intersection = intersect(merged_program, subprogram)

    ibranch_point  = intersection.compose_node(
        branching_point.right().base(),
        branching_point.left(), 
    )

    merged_program = _merge_programs(ibranch_point)
    return ControlFlowAutomata(cfg(merged_program))

# Seq merger --------------------------------------------------------------------

def run_seq_merger(left_program, right_program):

    if isinstance(left_program, str): left_program = ControlFlowAutomata(cfg(left_program))
    if isinstance(right_program, str): right_program = ControlFlowAutomata(cfg(right_program))

    left_program  = _introduce_fake_main(left_program, False)
    right_program = _introduce_fake_main(right_program, True)
    right_program = _resolve_clashing_definitions(right_program, left_program)

    return run_merger(left_program, right_program)


def _introduce_fake_main(source_program, negate_condition = False):

    main_node = source_program.control_flow_graph.scope().main_function()
    if main_node is None: raise ValueError("No main function found")

    function_ast_node = main_node.ast_node
    new_name          = f"main_clone_{random.randrange(10_000)}"

    main_function_clone = _create_clone(function_ast_node, new_name)

    condition = "__VERIFIER_nondet_int()"
    if negate_condition: condition = f"!({condition})"

    definition = ""
    if "__VERIFIER_nondet_int" not in source_program.source_code():
        definition = "int __VERIFIER_nondet_int();"

    new_main = f"""
{main_function_clone}
{definition}

int main(){{
    if({condition}) return 0;
    {new_name}();
}}
""".strip()
    
    result = _replace_all(source_program.source_code(),
                            [function_ast_node], [new_main])
    
    return ControlFlowAutomata(cfg(result))


def _find_clashing_definition(program, target_program):
    definitions         = program.control_flow_graph.scope().function_definitions()
    target_definitions = target_program.control_flow_graph.scope().function_definitions()
    
    for name, definition in definitions.items():
        definition_ast = definition.ast_node
        if name == "main": continue
        if name in target_definitions:
            target_definition = target_definitions[name]
            target_ast        = target_definition.ast_node
            if not _ast_match(definition_ast, target_ast):
                return definition
    
    return None


def _name_nodes(call_site):
    for call_expression_node in visit_tree(call_site, lambda node: node.type == "call_expression"):
        function = call_expression_node.child_by_field_name("function")
        if function is None or function.type != "identifier": raise ValueError("Something is wrong")
        yield function


def _resolve_clashing_definitions(program, target_program):
    
    clashing_definition = _find_clashing_definition(program, target_program)
    while clashing_definition is not None:
        function_name     = clashing_definition.function_name()
        new_name          = f"{function_name}_clone_{random.randrange(10_000)}"
        function_clone    = _create_clone(clashing_definition.ast_node, new_name)
        
        nodes   = [clashing_definition.ast_node]
        targets = [function_clone]

        calls = program.control_flow_graph.scope().function_calls()
        if function_name in calls:
            for call_site in calls[function_name]:
                for name_node in _name_nodes(call_site.ast_node):
                    nodes.append(name_node)
                    targets.append(new_name)

        program = _replace_all(program.source_code(), nodes, targets)
        program = ControlFlowAutomata(cfg(program))
        clashing_definition = _find_clashing_definition(program, target_program)

    return program
    


# Unroll program if necessary ---------------------------------------------------

def _preprocess_program(program, target_node):

    analysis  = _init_cpas(lambda cfa_node: cfa_node == target_node)
    state     = run_analysis(analysis, program, target_fn = lambda state: state.is_target())

    if state is None: raise ValueError("Cannot find the merge location in the original program.")

    if state.num_loops() > 0:
        return _unroll_loop(program, state, state.loops.value)
    
    for call in reversed(state.callstack.value):
        if len(call.call_sides()) > 1:
            return _clone_call(program, state, call)
    
    return _remove_deadcode(state.cfa_node)
    
    

# Program merging ---------------------------------------------------------------

def _merge_programs(merge_point):
    join_point, left_branch, right_branch = _find_join_after_merge(merge_point)

    if join_point is None:
        raise ValueError("Cannot find a feasible join location after merge")

    left_nodes = left_branch - {join_point.left()}
    replace_nodes = _deduplicate(
        ast_nodes_from_cfa(
            left_nodes
        )
    )

    target = _construct_branch(merge_point, join_point, left_branch, right_branch)

    if len(replace_nodes) == 1:
        if len(left_branch) > 1 or next(iter(left_branch)).ast_node.type != "compound_statement": # We need a compound
            target = f"{{\n{target}\n}}"

    targets = [target] + [""] * (len(replace_nodes) - 1)

    # Add unknown functions
    left_automata = merge_point.left().automata            
    fn_replacements, fn_targets = _import_functions(left_automata, right_branch)

    return _replace_all(
        left_automata.source_code(),
        replace_nodes + fn_replacements, targets + fn_targets
    )


def _construct_branch(branch_point, join_point, left_branch, right_branch):

    if _is_left_subset_right(branch_point, join_point):
        return slice_program(right_branch)

    left_control  = {branch_point.left(),  join_point.left()}
    right_control = {branch_point.right(), join_point.right()}

    left_slice   = slice_program(left_branch - left_control)
    right_slice  = slice_program(right_branch - right_control)

    condition   = branch_point.left().cfg_node.condition()
    right_slice = _rename_labels(right_slice) 

    while condition.type in ["parenthesized_expression", "unary_expression"]:
        if condition.type == "parenthesized_expression":
            condition = condition.children[1]
        elif condition.type == "unary_expression":
            if condition.children[0].type != "!": break
            condition = condition.children[1]
            left_slice, right_slice = right_slice, left_slice

    condition = condition.text.decode('utf-8')

    if len(left_slice.strip()) == 0:
        if_construct = f"if({condition}){{\n {right_slice}}}"
    elif len(right_slice.strip()) == 0:
        if_construct = f"if(!({condition})){{\n {left_slice}}}"
    else:
        if_construct = f"if({condition}){{\n {right_slice}}} else {{\n {left_slice}}}"
    
    return if_construct


def _find_join_after_merge(merge_point):
    automaton        = merge_point.automata
    left_dominators  = _compute_postdominators(merge_point.left())
    right_dominators = _compute_postdominators(merge_point.right())

    final_join_node = None
    seen  = set()
    stack = [(left_dominators[merge_point.left()], right_dominators[merge_point.right()])]
    while len(stack) > 0:
        dominator_left, dominator_right = stack.pop(0)
        join_node = automaton.compose_node(dominator_left, dominator_right)
        
        if join_node in seen: continue # To avoid duplicate computations
        seen.add(join_node)
    
        if _is_allowed_join(join_node) and not _has_desync_successor(join_node): 
            final_join_node = join_node
            break
        
        if left_dominators[dominator_left] != dominator_left:
            stack.append((left_dominators[dominator_left], dominator_right))
        
        if right_dominators[dominator_right] != dominator_right:
            stack.append((dominator_left, right_dominators[dominator_right]))
    
    assert final_join_node is not None

    # Only for backward compatibility 
    left_branch = set(intra_search(merge_point.left(), join_node.left()))
    right_branch = set(intra_search(merge_point.right(), join_node.right()))
    
    return join_node, left_branch, right_branch


def _is_allowed_join(join_node):
    return _is_dividable(join_node.left()) and _is_dividable(join_node.right())


def _is_dividable(cfa_node):
    ast_node = cfa_node.ast_node
    if ast_node.type == "goto_statement": return False

    while ast_node is not None:
        if ast_node.type in {"while_statement", "do_statement", "for_statement"}: 
            return False
        ast_node = ast_node.parent
    
    return True


def _is_left_subset_right(branch_point, join_point):
    # Currently the search is manual.
    seen  = set()
    stack = [(branch_point.left(), branch_point.right())]
    while len(stack) > 0:
        left_cfa_node, right_cfa_node = stack.pop(0)

        if (left_cfa_node, right_cfa_node) in seen: continue
        seen.add((left_cfa_node, right_cfa_node))

        if left_cfa_node == join_point.left()  : continue
        if right_cfa_node == join_point.right(): continue

        left_successors  = left_cfa_node.intra().successors()
        right_successors = right_cfa_node.intra().successors()

        # For every left successor there has to be a right successor
        for left_edge in left_successors:
            right_edge = None

            for _right_edge in right_successors:
                if _match_edge(left_edge, _right_edge):
                    right_edge = _right_edge
                    break

            if right_edge is None: return False
            stack.append((left_edge.successor, right_edge.successor))
    
    return True
        
# Seach helper ----------------------------------------------------------

def _has_desync_successor(intersection_node):
    if intersection_node.is_desync(): return True
    search_stack = [intersection_node]
    seen = set()

    while len(search_stack) > 0:
        node = search_stack.pop()

        if node in seen: continue
        seen.add(node)

        if node.is_desync(): return True

        for edge in node.intra().successors():
            search_stack.append(edge.successor)
    
    return False


# Slice ----------------------------------------------------------------


RELEVANT_NODES = set(BLOCK_REGISTRY.keys()) - {"comment"}

def _deduplicate(ast_nodes):
    index = set(ast_nodes)
    stack = sorted(index, key=lambda x: x.start_point)

    result = []

    while len(stack) > 0:
        node = stack.pop(0)
        if node.parent in index: continue

        if node.parent is None: continue

        if node.parent.type == "function_definition":
            result.append(node)
            continue

        if all(child in index 
               for child in node.parent.children 
               if child.type in RELEVANT_NODES):
         
            index.add(node.parent)
            while len(stack) > 0 and stack[0].parent in index: stack.pop(0)
            stack.insert(0, node.parent)
            stack  = stack + result
            result = []        
            continue

        result.append(node)

    return result

# Deadcode ----------------------------------------------------------------


def _has_deadcode(cfa_node):
    entry_node, exit_node = cfa_node.function_entry(), cfa_node.function_exit()
    reachable_nodes = set(intra_search(entry_node))
    bwd_nodes       = set(intra_search_bwd(exit_node))
    bwd_nodes       = {n for n in bwd_nodes if n.cfg_node.is_entry_node()}

    return len(bwd_nodes - reachable_nodes) > 0


def _remove_deadcode(cfa_node):
    program_root = cfa_node.automata.root_ast_node

    reachable_nodes = set(intra_search(cfa_node.function_entry()))
    _remove_empty_if(reachable_nodes)
    reachable_slice, root = slice_program(reachable_nodes, return_root = True)
    new_program = _replace_all(program_root.text.decode('utf-8'), [root], [reachable_slice])

    return ControlFlowAutomata(cfg(new_program))


def _remove_empty_if(cfa_nodes):
    remove_nodes = set()
    for node in cfa_nodes:
        ast_node = node.ast_node
        if ast_node.type == "if_statement":
            consequence = ast_node.child_by_field_name("consequence")
            alternative = ast_node.child_by_field_name("alternative")

            empty = consequence is None or (
                consequence.type == "compound_statement"
                and len(consequence.children) == 2
            )

            empty = empty and (
                alternative is None
                or (alternative.type == "compound_statement"
                and len(alternative.children) == 2)
            )
            
            if empty: remove_nodes.add(node)
    
    for node in remove_nodes: cfa_nodes.discard(node)


# Rename labels ---------------------------------------------------------

def _rename_labels(source_code):
    if len(source_code.strip()) == 0: return source_code

    prefix_id = 0
    while f"LP{prefix_id}_" in source_code: prefix_id += 1

    random_label_prefix = f"LP{prefix_id}_"
    return _prefix_all_labels(source_code, random_label_prefix)

# Helper ----------------------------------------------------------------

def _init_cpas(target_fn):
    analyses = [
        LocationAnalysis(),
        CallStackAnalysis(stop_recursion=True),
        LoopAnalysis(unroll_loop = 1),
        TargetAnalysis(target_fn)
    ]
    return ARGAnalysis(CompositeAnalysis(*analyses))


def _init_intersection_cpas(target_fn):
    analyses = [
        LocationAnalysis(),
        CallStackAnalysis(stop_recursion=True),
        TargetAnalysis(target_fn)
    ]
    return ARGAnalysis(CompositeAnalysis(*analyses))


def is_feasible_merge_location(split_state):
    if split_state is None: return False

    if any(len(call.call_sides()) > 1 for call in split_state.callstack.value):
        return False
    
    cfa_node = split_state.cfa_node
    if not _is_mergable_node(cfa_node):
        return False

    return True


def _is_mergable_node(cfa_node):

    successors = cfa_node.successors()

    if len(successors) != 2: return False

    left_edge, right_edge = successors
    if left_edge.type != right_edge.type: return False
    if not left_edge.is_assume(): return False
    
    if left_edge.truth_value == right_edge.truth_value:
        return (_is_negation_of(right_edge.condition, left_edge.condition) or
                _is_negation_of(left_edge.condition, right_edge.condition))

    return _match_ast(left_edge.condition, right_edge.condition, True)

# Callgraph ----------------------------------------------------------------

def _import_functions(target_automata, foreign_nodes):
    assignments = {}

    defined_functions = target_automata.control_flow_graph.scope().function_definitions()
    for function_entry in _callgraph(foreign_nodes):
        function_name = function_entry.cfg_node.function_name()
        function_ast  = function_entry.ast_node
        if function_name not in defined_functions:
            compatible_definition_ast = _find_compatible_definition(
                {v.ast_node for v in defined_functions.values()}, function_entry.ast_node
            )
            if compatible_definition_ast not in assignments:
                assignments[compatible_definition_ast] = [function_ast]
            else:
                assignments[compatible_definition_ast].append(function_ast)
    
    nodes, targets = [], []
    for definition_ast, target_asts in assignments.items():
        target = "\n\n".join(
            n.text.decode('utf-8') for n in target_asts + [definition_ast]
        )
        nodes.append(definition_ast)
        targets.append(target)

    return nodes, targets


def _callgraph(nodes):
    
    called_functions = set()
    for n in nodes:
        for edge in n.successors():
            if edge.is_function_call():
                if not edge.successor.is_function_entry(): continue
                called_functions.add(edge.successor)

    search_stack = list(called_functions)
    while len(search_stack) > 0:
        cfa_node = search_stack.pop()

        for node in intra_search(cfa_node):
            for edge in node.successors():
                if edge.is_function_call() and edge.successor not in called_functions:
                    if not edge.successor.is_function_entry(): continue
                    search_stack.append(edge.successor)
                    called_functions.add(edge.successor)

    return called_functions


def _find_compatible_definition(function_definitions, target_definition):
    return max(
        function_definitions, key = lambda d: _ast_id_jaccard(d, target_definition)    
    )


def _identifier(A):
    return set(n.text.decode('utf-8') 
               for n in visit_tree(A, lambda node: node.type == "identifier"))


def _jaccard(A, B):
    return len(A & B) / len(A | B)


def _ast_id_jaccard(A, B):
    return _jaccard(_identifier(A), _identifier(B))


def _ast_match(left_ast_root, right_ast_root):
    if left_ast_root.type != right_ast_root: return False
    left_children, right_children = left_ast_root.children, right_ast_root.children

    if len(left_children) != len(right_children): return False

    if len(left_children) == 0:
        return left_ast_root.text.decode('utf-8') == right_ast_root.text.decode('utf-8')
    
    for left_child, right_child in zip(left_children, right_children):
        if not _ast_match(left_child, right_child): return False

    return True


# Post Dominator ----------------------------------------------------------------

def _compute_predecessor_relation(cfa_node):
    predecessor_relation = {}

    worklist = [cfa_node]
    while len(worklist) > 0:
        node = worklist.pop()
        for successor_edge in node.intra().successors():
            successor = successor_edge.successor
            
            if successor in predecessor_relation:
                predecessor_relation[successor].append(node)
            else:
                predecessor_relation[successor] = [node]
                worklist.append(successor)
    
    return predecessor_relation


def _dfs_postorder_nodes(start_node, relation):
    visited = set()
    stack = [start_node]

    while len(stack) > 0:
        node = stack.pop(-1)

        if node in visited: 
            yield node; continue
        visited.add(node)

        stack.append(node)
        for predecessor in relation.get(node, []):
            if predecessor not in visited:
                stack.append(predecessor)

        
def _compute_postdominators(cfa_node):
    predecessors = _compute_predecessor_relation(cfa_node)
    start_node   = cfa_node.function_exit()

    # --- FIX 1: HANDLE UNREACHABLE FUNCTION EXITS ---
    if start_node not in predecessors:
        visited = set()
        rec_stack = set()
        latches = set()
        
        stack = [(cfa_node, False)]
        while stack:
            node, is_backtrack = stack.pop()
            if is_backtrack:
                rec_stack.remove(node)
                continue
            
            if node in visited: continue
                
            visited.add(node)
            rec_stack.add(node)
            stack.append((node, True))
            
            successors = node.intra().successors()
            if not successors:
                latches.add(node)
            else:
                for edge in successors:
                    succ = edge.successor
                    if succ not in visited:
                        stack.append((succ, False))
                    elif succ in rec_stack:
                        latches.add(node)
                        
        predecessors[start_node] = list(latches)
        for latch in latches:
            if latch not in predecessors:
                predecessors[latch] = []
    # ----------------------------------------------

    # --- FIX 2: BUILD SYMMETRIC SUCCESSORS MAP ---
    # The algorithm must use a forward-map that perfectly mirrors 
    # the predecessors map, including our artificial links!
    successors_map = {}
    for node, preds in predecessors.items():
        if node not in successors_map: successors_map[node] = []
        for p in preds:
            if p not in successors_map: successors_map[p] = []
            successors_map[p].append(node)
    # ---------------------------------------------

    idom = {start_node: start_node}

    order = list(_dfs_postorder_nodes(start_node, predecessors))
    dfn = {u: i for i, u in enumerate(order)}
    order.pop()
    order.reverse()

    def intersect(u, v):
        while u != v:
            while dfn[u] < dfn[v]:
                u = idom[u]
            while dfn[u] > dfn[v]:
                v = idom[v]
        return u

    changed = True
    while changed:
        changed = False
        for u in order:
            new_idom = None
            
            # --- OVERRIDE: Use the symmetric map instead of u.intra().successors()
            for succ in successors_map.get(u, []):
                if succ in idom:
                    if new_idom is None:
                        new_idom = succ
                    else:
                        new_idom = intersect(succ, new_idom)

            if new_idom is not None and idom.get(u) != new_idom:
                idom[u] = new_idom
                changed = True

    return idom