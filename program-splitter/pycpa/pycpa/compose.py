import itertools 

from .cfa import CFANode, CFAEdge, LoopInfo, SigmaEdge, to_dot
from .cfa import EdgeCollection, _match_edge

from .optimizers import is_trivial_true, is_trivial_false


# Nested automata ----------------------------------------------------------------


class NestedNode(CFANode):
    
    def __init__(self, automata, base_node):
        super().__init__(automata, ast_node = base_node.ast_node)

        self._base_node       = base_node
        self._nested_automata = automata

        self._fwd_expanded = False
        self._bwd_expanded = False

    def base(self):
        return self._base_node
    
    # Type checks --------------------------------
    
    def is_accepting(self):
        return self.base().is_accepting()

    def is_error_node(self): return self.base().is_error_node()
    
    def is_function_entry(self): return self.base().is_function_entry()

    def is_function_exit(self): return self.base().is_function_exit()

    def is_branching_node(self): return self.base().is_branching_node()

    def is_labeled_node(self): return self.base().is_labeled_node()

    def is_composed(self): return self.base().is_composed()

    def is_function_call_init(self): return self.base().is_function_call_init()

    def is_function_call_exit(self): return self.base().is_function_exit()

    @property
    def type(self):
        return self.base().type

    def __getattr__(self, key):
        return getattr(self.base(), key)
    
    # Creation operations -----------------------

    def function_entry(self):
        return self.automata.attach(self.base().function_entry())
    
    def function_exit(self):
        return self.automata.attach(self.base().function_exit())
    
    def call_init(self):
        return self.automata.attach(self.base().call_init())
    
    def call_exit(self):
        return self.automata.attach(self.base().call_exit())

    # --------------------------------------------

    def predecessors(self):
        if self._bwd_expanded: return self.incoming

        seen_incoming = set(i.predecessor for i in self.incoming)

        for edge in self.base().predecessors():
            pred_node = self.automata.attach(edge.predecessor)
            if pred_node not in seen_incoming:
                self.automata.edge(pred_node, edge, self).attach()
        
            
        self._bwd_expanded = True
        return self.incoming

    def successors(self):
        if self._fwd_expanded: return self.outgoing

        seen_outgoing = set(o.successor for o in self.outgoing)

        for edge in self.base().successors():
            succ_node = self.automata.attach(edge.successor)
            if succ_node not in seen_outgoing:
                self.automata.edge(self, edge, succ_node).attach()

        self._fwd_expanded = True
        return self.outgoing


    def __repr__(self):
        return str(self.base())


class NestedEdge(CFAEdge):

    def __init__(self, predecessor, base_edge, successor):
        super().__init__(predecessor, successor, base_edge.ast_node, cfg_node = base_edge.cfg_node)
        self._base_edge = base_edge

    def base(self):
        return self._base_edge
    
    # Type checks --------------------------------

    def is_declaration(self): return self.base().is_declaration()

    def is_statement(self): return self.base().is_statement()

    def is_assume(self): return self.base().is_assume()

    def is_function_call(self): return self.base().is_function_call()

    def is_function_return(self): return self.base().is_function_return()

    def is_function_summary(self): return self.base().is_function_summary()

    def is_function_entry(self): return self.base().is_function_entry()

    def is_function_exit(self): return self.base().is_function_exit()

    def is_sigma_edge(self): return self.base().is_sigma_edge()

    @property
    def type(self):
        return self.base().type

    def __getattr__(self, key):
        return getattr(self.base(), key)
    
    def _edge_repr_(self):
        return self.base()._edge_repr_()

    
    # --------------------------------------------


class NestedAutomata:

    def __init__(self, base_automata):
        self.base_automata = base_automata

        self._id_counter = 0
        self._node_cache = {}

        self.loop_info = LoopInfo(self)

    def _next_id(self):
        self._id_counter += 1
        return self._id_counter - 1
    
    def nest_cfa_node(self, cfa_node):
        return NestedNode(self, cfa_node)

    def attach(self, cfg_node):
        default_cfa = None
        if isinstance(cfg_node, CFANode): 
            default_cfa = cfg_node
            cfg_node = cfg_node.cfg_node

        try:
            return self._node_cache[cfg_node]
        except KeyError:
            cfa_node = self.nest_cfa_node(
                default_cfa or self.base_automata.attach(cfg_node)
            )
            cfa_node.node_id = cfa_node.base().node_id
            self._node_cache[cfg_node] = cfa_node
            return cfa_node

    def edge(self, predecessor, base_edge, successor, **kwargs):
        try:
            assert base_edge.is_edge()
        except (AttributeError, AssertionError):
            base_edge = self.base_automata.edge(predecessor.base(), base_edge, successor.base(), **kwargs)

        if isinstance(base_edge, EdgeCollection):
            return EdgeCollection(
                NestedEdge(predecessor, e, successor)
                for e in base_edge
            )

        return NestedEdge(predecessor, base_edge, successor)
    
    def init_node(self):
        init_node = self.base_automata.init_node()
        return self.attach(init_node.cfg_node)
    
    def exit_node(self):
        exit_node = self.base_automata.exit_node()
        return self.attach(exit_node.cfg_node)
    
    def __getattr__(self, key):
        return getattr(self.base_automata, key)
    
    def to_dot(self):
        return to_dot(self.init_node())


# Product ----------------------------------------------------------------

class ProductNode(NestedNode):
    
    def __init__(self, automata, left_node, right_node):
        base_node = left_node if left_node.is_accepting() else right_node
        super().__init__(automata, base_node)

        self._left_node = left_node
        self._right_node = right_node

        self._fwd_expanded = False
        self._bwd_expanded = False

        self._desync = False

    def is_composed(self): return True # TODO: This is bad. Change this in future

    def left(self):
        return self._left_node
    
    def right(self):
        return self._right_node
    
    def nodes(self):
        return (self.left(), self.right())

    # Creation operations ------------------

    def _compose(self, op_name):
        try:
            left_node = getattr(self.left(), op_name)()
        except AttributeError:
            left_node = self.left().automata.attach(None)
        
        try:
            right_node = getattr(self.right(), op_name)()
        except AttributeError:
            right_node = self.right().automata.attach(None)

        return self.automata.compose_node(left_node, right_node)

    def function_entry(self):
        return self._compose('function_entry')
    
    def function_exit(self):
        return self._compose('function_exit')
    
    def call_init(self):
        return self._compose('call_init')
    
    def call_exit(self):
        return self._compose('call_exit')

    # Desync --------------------------------

    def is_desync(self):
        if not self._fwd_expanded: self.successors()
        return self._desync
    
    def is_accepting(self):
        return self._left_node.is_accepting() and self._right_node.is_accepting()

    # --------------------------------------------
    
    def predecessors(self):
        if self._bwd_expanded: return self.incoming

        left_edges  = self._left_node.predecessors()
        right_edges = self._right_node.predecessors()

        unused_left  = set(left_edges)
        unused_right = set(right_edges)

        for left_edge, right_edge in itertools.product(left_edges, right_edges):
            if _match_edge(left_edge, right_edge):
                unused_left.discard(left_edge)
                unused_right.discard(right_edge)

                predecessor = self.automata.compose_node(
                    left_edge.predecessor, 
                    right_edge.predecessor
                )
                predecessor.successors()

        for left_edge in unused_left:
            right_sigma = self.right().prev(left_edge.cfg_node)
            predecessor = self.automata.compose_node(
                left_edge.predecessor, 
                right_sigma.predecessor
            )
            predecessor.successors()

        for right_edge in unused_right:
            left_sigma = self.left().next(right_edge.cfg_node)
            predecessor = self.automata.compose_node(
                left_sigma.predecessor, 
                right_edge.predecessor
            )
            predecessor.successors()
            
        self._bwd_expanded = True
        return self.incoming

    def successors(self):
        if self._fwd_expanded: return self.outgoing

        seen_successors = set(o.successor for o in self.outgoing)

        left_successors  = self._left_node.successors()
        right_successors = self._right_node.successors()

        unused_left  = set(left_successors)
        unused_right = set(right_successors)

        for left_edge, right_edge in itertools.product(left_successors, right_successors):
            if _match_edge(left_edge, right_edge):
                unused_left.discard(left_edge)
                unused_right.discard(right_edge)

                successor = self.automata.compose_node(
                    left_edge.successor, 
                    right_edge.successor
                )

                if successor in seen_successors: continue

                self.automata.compose_edge(left_edge, right_edge).attach()

        for left_edge in unused_left:
            right_sigma = self.right().next(None)
            self.automata.compose_edge(left_edge, right_sigma).attach()

        for right_edge in unused_right:
            left_sigma = self.left().next(None)
            self.automata.compose_edge(left_sigma, right_edge).attach()

        self._desync = len(unused_left) > 0 or len(unused_right) > 0

        self._fwd_expanded = True
        return self.outgoing


    def __repr__(self):
        left_cfa_id = self._left_node.node_id
        if left_cfa_id == -1 and self.ast_node is not None:
            left_cfa_id = f"{self.ast_node.start_point[0]}, {self.ast_node.start_point[1]} - {self.ast_node.end_point[0]}, {self.ast_node.end_point[1]}"

        right_cfa_id = self._right_node.node_id
        if right_cfa_id == -1 and self.ast_node is not None:
            right_cfa_id = f"{self.ast_node.start_point[0]}, {self.ast_node.start_point[1]} - {self.ast_node.end_point[0]}, {self.ast_node.end_point[1]}"

        return f"PNode({left_cfa_id}; {right_cfa_id})"


class ProductEdge(NestedEdge):
    def __init__(self, predecessor, successor, left_edge, right_edge):
        base_edge = left_edge if not left_edge.is_sigma_edge() else right_edge 
        super().__init__(predecessor, base_edge, successor)

        self._left_edge = left_edge
        self._right_edge = right_edge

    def left(self):
        return self._left_edge
    
    def right(self):
        return self._right_edge


class ProductAutomata(NestedAutomata):
    
    def __init__(self, left_automata, right_automata):
        super().__init__(left_automata)
        self._left_automata = left_automata
        self._right_automata = right_automata

    def attach(self, cfg_node):
        candidates = []
        for left_node, right_node in self._node_cache:
            if left_node.cfg_node == cfg_node:
                candidates.append(self._node_cache[(left_node, right_node)])
            elif right_node.cfg_node == cfg_node:
                candidates.append(self._node_cache[(left_node, right_node)])

        if len(candidates) != 1:
            raise ValueError(f"No unique candidate for CFG node {cfg_node}")

        return candidates[0]

    def compose_node(self, left_node, right_node):
        try:
            return self._node_cache[(left_node, right_node)]
        except KeyError:
            Product_node = ProductNode(self, left_node, right_node)
            self._node_cache[(left_node, right_node)] = Product_node
            Product_node.node_id = self._next_id()
            return Product_node

    def compose_edge(self, left_edge, right_edge):
        predecessor = self.compose_node(
            left_edge.predecessor, 
            right_edge.predecessor
        )

        successor = self.compose_node(
            left_edge.successor, 
            right_edge.successor
        )

        return self.edge(predecessor, left_edge, successor, right_edge)
    
    def edge(self, predecessor, base_edge, successor, other_edge = None):
        if other_edge is None: other_edge = SigmaEdge(predecessor.right(), successor.right())
        return ProductEdge(predecessor, successor, base_edge, other_edge)

    def init_node(self):
        return self.compose_node(
            self._left_automata.init_node(), 
            self._right_automata.init_node()
        )
    
    def exit_node(self):
        return self.compose_node(
            self._left_automata.exit_node(), 
            self._right_automata.exit_node()
        )

# Negation automata ----------------------------------------------------------------

class NegationNode(NestedNode):

    def is_accepting(self):
        return not self.base().is_accepting()

class NegationAutomata(NestedAutomata): 

    def nest_cfa_node(self, cfa_node):
        return NegationNode(self, cfa_node)
    

# Trace automata ---------------------------------------------------------------
# For a trace, only defintions, statements and assumes are relevant


def _is_real_assume(edge):
    if not edge.is_assume(): return False
    
    if edge.truth_value: 
        return not is_trivial_true(edge.condition)
    else:
        return not is_trivial_false(edge.condition)

def _is_nonempty_statement(edge):
    if not edge.is_statement(): return False

    children = edge.statement.children
    return len(children) != 1 or children[0].type != ";"


class TraceNode(NestedNode):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.callstack = []
    
    def successors(self):
        if self._fwd_expanded: return self.outgoing

        seen       = set()
        successors = []
        stack      = list(self.base().successors())

        while len(stack) > 0:
            edge = stack.pop()

            if edge in seen: continue
            seen.add(edge)

            if edge.is_declaration() or _is_nonempty_statement(edge) or _is_real_assume(edge):
                successors.append(edge)
            else:
                for next_successor in edge.successor.successors():
                    stack.append(next_successor)

        seen_outgoing = set(o.successor for o in self.outgoing)
        
        for edge in successors:
            succ_node = self.automata.attach(edge.successor)
            if succ_node not in seen_outgoing:
                self.automata.edge(self, edge, succ_node).attach()

        self._fwd_expanded = True
        return self.outgoing


class TraceAutomata(NestedAutomata):

    def nest_cfa_node(self, cfa_node):
        return TraceNode(self, cfa_node)

# Operations ----------------------------------------------------------------

def intersect(left_automata, right_automata):
    return ProductAutomata(left_automata, right_automata)

def product(left_automata, right_automata):
    return ProductAutomata(left_automata, right_automata)

def negate(automata):
    return NegationAutomata(automata)

def trace(automata):
    return TraceAutomata(automata)

def _search(start_node, target_fn):
    seen  = set()
    stack = [start_node]

    while len(stack) > 0:
        node = stack.pop()
        if node in seen: continue
        seen.add(node)

        if target_fn(node): return True

        for edge in node.successors():
            stack.append(edge.successor)
    
    return False

def is_empty(automata):
    return not _search(automata.init_node(), lambda x: x.is_accepting())

def has_desync(automata):
    return _search(automata.init_node(), lambda x: x.is_desync())

def is_subset(left_automata, right_automata):
    return is_empty(
        intersect(left_automata, negate(right_automata))
    )

def is_equal(left_automata, right_automata):
    return not has_desync(
        intersect(left_automata, right_automata)
    )