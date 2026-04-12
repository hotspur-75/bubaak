from .nodes import IfBranchNode, WhileLoopNode, DoWhileLoopNode, ForLoopConditionNode
from .nodes import FunctionEntryNode as CFGFunctionEntryNode
from .nodes import FunctionExitNode as CFGFunctionExitNode
from .nodes import SwitchEntryNode, CaseStatementEntryNode, LabeledStatementNode
from .nodes import ErrorNode as CFGErrorNode
from .nodes import ReturnStatementNode as CFGReturnNode
from .nodes import FunctionCallInitNode as CFGFunctionCallInit
from .nodes import FunctionCallExitNode as CFGFunctionCallExit
from .nodes import Node as CFGNode

from .optimizers import is_trivial_true, is_trivial_false

from .visitors import contains_tree


FUNCTION_SUMMARY_EDGE = False

# Nodes --------------------------------------------------------------------------------


class CFANode:
    
    def __init__(self, automata, ast_node = None):
        self.automata = automata
        self.ast_node = ast_node
        
        self.incoming = []
        self.outgoing = []

        self.node_id = -1

    def intra(self):
        return IntraproceduralView(self)
    
    def loop_info(self):
        assert self.automata.loop_info is not None, "To use this function, a loop info must be attached to the automata"
        return self.automata.loop_info.find_loop(self)

    def successors(self):
        return self.outgoing
    
    def predecessors(self):
        return self.incoming
    
    def next(self, cfg_node, **kwargs):
        dummy_edge = self.automata.edge(
            self, cfg_node, self.automata.attach(None), **kwargs
        )

        if isinstance(dummy_edge, EdgeCollection): dummy_edge = dummy_edge[0]

        for edge in self.successors():
            if _match_edge(edge, dummy_edge): return edge
            
        return dummy_edge
    
    def prev(self, cfg_node, **kwargs):
        dummy_edge = self.automata.edge(
            self.automata.attach(None), cfg_node, self, **kwargs
        )

        if isinstance(dummy_edge, EdgeCollection): dummy_edge = dummy_edge[0]

        for edge in self.predecessors():
            if _match_edge(edge, dummy_edge): return edge
        
        return dummy_edge 
    
    def is_accepting(self): return True
    
    def is_error_node(self): return False
    
    def is_function_entry(self): return False

    def is_function_exit(self): return False

    def is_branching_node(self): return False

    def is_labeled_node(self): return False

    def is_composed(self): return False

    def is_function_call_init(self): return False

    def is_function_call_exit(self): return False

    @property
    def type(self):
        return self.__class__.__name__
    
    def __repr__(self):
        cfa_id = self.node_id
        if cfa_id == -1 and self.ast_node is not None:
            cfa_id = f"{self.ast_node.start_point[0]}, {self.ast_node.start_point[1]} - {self.ast_node.end_point[0]}, {self.ast_node.end_point[1]}"

        return f"CFANode({cfa_id})"


class ExpansionNode(CFANode):
    
    def __init__(self, automata, cfg_node):
        super().__init__(automata, cfg_node.ast_node)
        self.cfg_node = cfg_node

        assert isinstance(cfg_node, CFGNode)

        self._fwd_expanded = False
        self._bwd_expanded = False

    def function_entry(self):
        return self.automata.attach(self.cfg_node.scope.function_block().entry_node())
    
    def function_exit(self):
        return self.automata.attach(self.cfg_node.scope.function_block().exit_node())
    
    def predecessors(self):
        if self._bwd_expanded: return self.incoming

        seen_predecessors = set(incoming.predecessor for incoming in self.incoming)
        for pred in self.cfg_node.predecessors():
            pred_node = self.automata.attach(pred)
            if pred_node not in seen_predecessors:
                # Sometimes predecessors are not accurate
                # So check first the successors
                pred_node.successors() # This automatically computes all necessary edges

        self._bwd_expanded = True
        return self.incoming

    def successors(self):
        if self._fwd_expanded: return self.outgoing

        seen_succesors = set(out.successor for out in self.outgoing)

        for succ in self.cfg_node.successors():
            succ_node = self.automata.attach(succ)
            if succ_node not in seen_succesors:
                self.automata.edge(self, self.cfg_node, succ_node).attach()

        self._fwd_expanded = True
        return self.outgoing
    

class BranchingExpansionNode(ExpansionNode):
    def is_branching_node(self): return True

    def condition(self):
        return self.cfg_node.ast_node.child_by_field_name("condition")

    def successors(self):
        if self._fwd_expanded: return self.outgoing

        seen_succesors = set(out.successor for out in self.outgoing)

        successors = self.cfg_node.successors()
        if len(successors) == 1:
            if is_trivial_true(self.condition()):
                then_branch, else_branch = successors[0], None
            elif is_trivial_false(self.condition()):
                then_branch, else_branch = None, successors[0]
        else:
            then_branch, else_branch = successors[0], successors[1]

        if else_branch is not None:
            else_node = self.automata.attach(else_branch)
            if else_node not in seen_succesors:
                self.automata.edge(self, self.cfg_node, else_node, truth_value = False).attach()

        if then_branch is not None:
            then_node = self.automata.attach(then_branch)
            if then_node not in seen_succesors:
                self.automata.edge(self, self.cfg_node, then_node, truth_value = True).attach()

        self._fwd_expanded = True
        return self.outgoing
    

class FunctionEntryNode(ExpansionNode):
    def is_function_entry(self): return True


class FunctionExitNode(ExpansionNode):
    def is_function_exit(self): return True


class FunctionCallInitNode(ExpansionNode):

    def is_function_call_init(self): return True

    def call_exit(self):
        return self.automata.attach(self.cfg_node.exit_node())


class FunctionCallExitNode(ExpansionNode):

    def is_function_call_exit(self): return True

    def call_init(self):
        return self.automata.attach(self.cfg_node.entry_node())


class LabeledNode(ExpansionNode):
    def is_labeled_node(self): return True


class ErrorNode(ExpansionNode):
    def is_error_node(self): return True


class TraceExitingNode(CFANode):
    def __init__(self, automata):
        super().__init__(automata, None)
        self.cfg_node = None
        SigmaEdge(self, self).attach()

    def is_accepting(self): return False


# Edges --------------------------------------------------------------------------------


class CFAEdge:

    def __init__(self, predecessor, successor, ast_node = None, cfg_node = None):
        self.predecessor = predecessor
        self.successor = successor
        self.ast_node = ast_node
        self.cfg_node = cfg_node

    def attach(self):
        self.predecessor.outgoing.append(self)
        self.successor.incoming.append(self)
        return self

    def scope(self):
        if self.cfg_node is None: return None
        return self.cfg_node.scope
    
    def _edge_repr_(self):
        return self.ast_node.text.decode('utf-8') if self.ast_node else self.type

    # Type checks ----------------------------------------------------------------

    def is_edge(self): return True
    
    def is_declaration(self): return False

    def is_statement(self): return False

    def is_assume(self): return False

    def is_function_call(self): return False

    def is_function_return(self): return False

    def is_function_summary(self): return False

    def is_function_entry(self): return False

    def is_function_exit(self): return False

    def is_sigma_edge(self): return False

    def is_abort(self): return False
    
    @property
    def type(self):
        return self.__class__.__name__
    
    # ----------------------------------------------------------------------------

    def __repr__(self):
        edge_label = self._edge_repr_()
        return f"{self.predecessor} -- {edge_label} --> {self.successor}"
    

class EdgeCollection(list):

    def attach(self):
        for edge in self:
            edge.attach()
        return self

    
class SigmaEdge(CFAEdge):
    def is_sigma_edge(self): return True


class DeclarationEdge(CFAEdge):
    
    def __init__(self, predecessor, declaration, successor, cfg_node = None):
        super().__init__(predecessor, successor, declaration, cfg_node=cfg_node)
        self.declaration = declaration

    def is_declaration(self): return True


class StatementEdge(CFAEdge):
    def __init__(self, predecessor, statement, successor, cfg_node = None):
        super().__init__(predecessor, successor, statement, cfg_node=cfg_node)
        self.statement = statement
    
    def is_statement(self): return True


class AssumeEdge(CFAEdge):

    def __init__(self, predecessor, condition, successor, truth_value = True, cfg_node = None):
        super().__init__(predecessor, successor, condition, cfg_node=cfg_node)
        self.condition = condition
        self.truth_value = truth_value

    def is_assume(self): return True

    def _edge_repr_(self):
        condition = self.condition.text.decode('utf-8')
        if not self.truth_value:
            condition = f"!({condition})"
        return f"assume({condition});"


class BlankEdge(CFAEdge):
    
    def __init__(self, predecessor, successor, cfg_node = None, ast_node = None):
        if ast_node is None and cfg_node is not None:
            ast_node = cfg_node.ast_node

        super().__init__(predecessor, successor, ast_node)
        self.cfg_node = cfg_node

    def _edge_repr_(self):
        return ""

    def __repr__(self):
        return f"{self.predecessor} ----> {self.successor}"


class FunctionCallEdge(CFAEdge):
    def __init__(self, predecessor, call_node, successor):
        super().__init__(predecessor, successor, call_node.ast_node, cfg_node = call_node)
        self.call_node = call_node

    def is_function_call(self): return True

    def _edge_repr_(self):
        call_expression = self.ast_node.children[0]
        if call_expression.type == "assignment_expression":
            call_expression = call_expression.children[-1]
        call_expression = call_expression.text.decode('utf-8')

        return f"call {call_expression};"
    
    
class FunctionReturnEdge(CFAEdge):
    def is_function_return(self): return True
    def is_function_exit(self): return True
    

class FunctionSummaryEdge(CFAEdge):
    def __init__(self, predecessor, call_node, successor):
        super().__init__(predecessor, successor, call_node.ast_node, cfg_node = call_node)
        self.call_node = call_node

    def is_function_summary(self): return True
    
    def _edge_repr_(self):
        call_expression = self.ast_node.children[0]
        if call_expression.type == "assignment_expression":
            call_expression = call_expression.children[-1]
        call_expression = call_expression.text.decode('utf-8')

        return f"join {call_expression};"


class FunctionEntryEdge(CFAEdge):
    def is_function_entry(self): return True

    def _edge_repr_(self): return self.type


class FunctionExitEdge(CFAEdge):
    def is_function_exit(self): return True

    def _edge_repr_(self): return self.type


class AbortEdge(CFAEdge):
    def is_function_exit(self): return True

    def is_abort(self): return True

    def _edge_repr_(self): return self.type


# Instructions ----------------------------------------------------------


class FakeASTNode:

    def __init__(self, type):
        self.type = type
        self.children = []
    
    def child_by_field_name(self, name):
        return None
    
    @property
    def text(self):
        return self.type.encode('utf-8')
    
    @property
    def start_point(self):
        return (-1, -1)


class Op(FakeASTNode):
    pass


class Binary(FakeASTNode):

    def __init__(self, op, left, right):
        super().__init__("binary_expression")
        
        self.op = op
        self.left = left
        self.right = right
        self.children = [self.left, Op(op), self.right]

    @property
    def text(self):
        left_text = self.left.text.decode('utf-8')
        right_text = self.right.text.decode('utf-8')
        return f"{left_text} {self.op} {right_text}".encode('utf-8')


class Unary(FakeASTNode):

    def __init__(self, op, value):
        super().__init__("unary_expression")

        self.op = op
        self.value = value
        self.children = [Op(op), self.value]

    @property
    def text(self):
        value = self.right.text.decode('utf-8')
        return f"{self.op} {value}".encode('utf-8')


class Identifier(FakeASTNode):

    def __init__(self, name):
        super().__init__("identifier")
        self.name = name

    @property
    def text(self):
        return self.name.encode('utf-8')


class NumberLiteral(FakeASTNode):

    def __init__(self, value):
        super().__init__("number_literal")
        self.value = value

    @property
    def text(self):
        return str(self.value).encode('utf-8')


# -----------------------------------------------------------------------


class ControlFlowAutomata:

    def __init__(self, control_flow_graph, root_node = None):
         # Maps CFG nodes to CFA nodes
        self._id_counter = 0
        self._node_cache = {}

        self.control_flow_graph = control_flow_graph
        self.root_cfg_node = root_node or control_flow_graph.entry_node()
        self.root_cfa_node = self.attach(self.root_cfg_node)
        self.root_ast_node = self.root_cfg_node.ast_node

        self.exit_cfg_node = control_flow_graph.exit_node()
        self.exit_cfa_node = self.attach(self.exit_cfg_node)

        self.loop_info = LoopInfo(self)

    def source_code(self):
        return self.control_flow_graph.source_code()

    def scope(self):
        return self.root_cfg_node.scope      

    def _next_id(self):
        self._id_counter += 1
        return self._id_counter - 1

    def create_cfa_node(self, cfg_node):
        if cfg_node is None: return TraceExitingNode(self)

        assert isinstance(cfg_node, CFGNode), f"Expected {cfg_node} to be a CFGNode"

        if isinstance(cfg_node, (IfBranchNode, WhileLoopNode, DoWhileLoopNode, ForLoopConditionNode)):
            return BranchingExpansionNode(self, cfg_node)
        
        if isinstance(cfg_node, CFGFunctionEntryNode):
            return FunctionEntryNode(self, cfg_node)

        if isinstance(cfg_node, CFGFunctionExitNode):
            return FunctionExitNode(self, cfg_node)

        if isinstance(cfg_node, CFGReturnNode):
            return FunctionExitNode(self, cfg_node)
        
        if isinstance(cfg_node, LabeledStatementNode):
            return LabeledNode(self, cfg_node)
        
        if isinstance(cfg_node, CFGErrorNode):
            return ErrorNode(self, cfg_node)
        
        if isinstance(cfg_node, CFGFunctionCallInit):
            return FunctionCallInitNode(self, cfg_node)
        
        if isinstance(cfg_node, CFGFunctionCallExit):
            return FunctionCallExitNode(self, cfg_node)

        return ExpansionNode(self, cfg_node)

    def attach(self, cfg_node):
        try:
            return self._node_cache[cfg_node]
        except KeyError:
            cfa_node = self.create_cfa_node(cfg_node)
            cfa_node.node_id = self._next_id()
            self._node_cache[cfg_node] = cfa_node
            return cfa_node
        
    # Edges -----------------------------------------------------------------

    def edge_ProgramEntryNode(self, predecessor, cfg_node, successor, **kwargs):
        return BlankEdge(predecessor, successor, cfg_node=cfg_node)
    
    def edge_FunctionEntryNode(self, predecessor, cfg_node, successor, **kwargs):
        return FunctionEntryEdge(predecessor, successor, cfg_node=cfg_node)
    
    def edge_FunctionExitNode(self, predecessor, cfg_node, successor, **kwargs):
        return FunctionExitEdge(predecessor, successor, cfg_node=cfg_node)
    
    def edge_DeclarationNode(self, predecessor, cfg_node, successor, **kwargs):
        return DeclarationEdge(predecessor, cfg_node.ast_node, successor, cfg_node = cfg_node)
    
    def edge_ExpressionNode(self, predecessor, cfg_node, successor, **kwargs):
        expression = cfg_node.ast_node
        return StatementEdge(predecessor, expression, successor, cfg_node = cfg_node)

    # Branches
    def edge_IfBranchNode(self, predecessor, cfg_node, successor, truth_value = True):
        condition_ast = cfg_node.ast_node.child_by_field_name("condition")
        return AssumeEdge(predecessor, condition_ast, successor, truth_value, cfg_node = cfg_node)
    
    def edge_IfJoinNode(self, predecessor, cfg_node, successor):
        return BlankEdge(predecessor, successor, cfg_node = cfg_node)

    # For node
    def edge_ForInitNode(self, predecessor, cfg_node, successor, **kwargs):
        init = cfg_node.ast_node.child_by_field_name("initializer")
        if init.type == "declaration":
            return DeclarationEdge(predecessor, init, successor, cfg_node = cfg_node)
        return StatementEdge(predecessor, init, successor, cfg_node = cfg_node)
    
    def edge_ForLoopConditionNode(self, predecessor, cfg_node, successor, truth_value = True):
        condition = cfg_node.ast_node.child_by_field_name("condition")
        return AssumeEdge(predecessor, condition, successor, truth_value=truth_value, cfg_node = cfg_node)

    def edge_ForLoopUpdateNode(self, predecessor, cfg_node, successor, **kwargs):
        update = cfg_node.ast_node.child_by_field_name("update")
        return StatementEdge(predecessor, update, successor, cfg_node = cfg_node)
    
    # Loop nodes 
    def edge_WhileLoopNode(self, predecessor, cfg_node, successor, truth_value = True):
        condition = cfg_node.ast_node.child_by_field_name("condition")
        return AssumeEdge(predecessor, condition, successor, truth_value=truth_value, cfg_node = cfg_node)
    
    def edge_LoopExitNode(self, predecessor, cfg_node, successor):
        # Since gotos and whiles should produce the same CFA
        # we don't need to do anything here
        return BlankEdge(predecessor, successor, cfg_node=cfg_node)

    # Do While
    def edge_DoWhileInitNode(self, predecessor, cfg_node, successor):
        return BlankEdge(predecessor, successor,cfg_node = cfg_node)
    
    def edge_DoWhileLoopNode(self, predecessor, cfg_node, successor, truth_value = True):
        condition = cfg_node.ast_node.child_by_field_name("condition")
        return AssumeEdge(predecessor, condition, successor, truth_value=truth_value, cfg_node = cfg_node)
    

    # Return statement
    def edge_ReturnStatementNode(self, predecessor, cfg_node, successor, **kwargs):
        assert isinstance(successor, TraceExitingNode) or isinstance(successor.cfg_node, CFGFunctionExitNode)

        # We actually want to jump over the function boundary
        return EdgeCollection([
            FunctionReturnEdge(predecessor, child.successor, ast_node = cfg_node.ast_node, cfg_node = cfg_node)
            for child in successor.successors()
        ])
        
            
    # Switch case
    def edge_SwitchEntryNode(self, predecessor, cfg_node, successor, **kwargs):
        if isinstance(successor, TraceExitingNode):
            case_node = self.attach(cfg_node.successors()[0])
            switch_edge = self.edge_SwitchEntryNode(predecessor, cfg_node, case_node)
            switch_edge.successor = successor
            return switch_edge

        succ_cfg = successor.cfg_node
        assert isinstance(succ_cfg, CaseStatementEntryNode)

        switch_condition = cfg_node.ast_node.child_by_field_name("condition")

        case_ast = succ_cfg.ast_node
        if case_ast.children[0].type == "case":
            case_condition = case_ast.children[1]
            return AssumeEdge(predecessor, Binary("==", switch_condition, case_condition), successor, cfg_node = cfg_node)
        
        if case_ast.children[0].type == "default":
            # This is more complicated
            switch_body = cfg_node.ast_node.child_by_field_name("body")
            case_conditions = [
                Binary("!=", switch_condition, stmt_node.children[1])
                for stmt_node in switch_body.children
                if stmt_node.type == "case_statement" and stmt_node.children[0].type != "default"
            ]

            result, case_conditions = case_conditions[0], case_conditions[1:]
            while len(case_conditions) > 0:
                result = Binary("&", result, case_conditions.pop(0))
            
            return AssumeEdge(predecessor, result, successor, cfg_node = cfg_node)

    def edge_CaseStatementEntryNode(self, predecessor, cfg_node, successor, **kwargs):
        return BlankEdge(predecessor, successor, cfg_node = cfg_node)

    def edge_BreakStatementNode(self, predecessor, cfg_node, successor, **kwargs):
        return BlankEdge(predecessor, successor, cfg_node = cfg_node)
    
    def edge_ContinueStatementNode(self, predecessor, cfg_node, successor, **kwargs):
        return BlankEdge(predecessor, successor, cfg_node = cfg_node)
    
    # Gotos ----------------------------------------------------------------

    def edge_LabeledStatementNode(self, predecessor, cfg_node, successor, **kwargs):
        return BlankEdge(predecessor, successor, cfg_node = cfg_node)

    def edge_GotoStatementNode(self, predecessor, cfg_node, successor, **kwargs):
        return BlankEdge(predecessor, successor, cfg_node = cfg_node)

    # Functions ------------------------------------------------------------

    def edge_FunctionCallInitNode(self, predecessor, cfg_node, successor, **kwargs):
        
        if FUNCTION_SUMMARY_EDGE:
            function_exit = cfg_node.parent_block.exit_node()
            return EdgeCollection([
                FunctionSummaryEdge(predecessor, cfg_node, self.attach(function_exit)),
                FunctionCallEdge(predecessor, cfg_node, successor)
            ])
            
        return FunctionCallEdge(predecessor, cfg_node, successor)
    
    def edge_FunctionCallExitNode(self, predecessor, cfg_node, successor, **kwargs):
        return BlankEdge(predecessor, successor, cfg_node = cfg_node)

    # Special functions ------------------------------------------------------------

    def edge_ErrorNode(self, predecessor, cfg_node, successor, **kwargs):
        return BlankEdge(predecessor, successor, cfg_node = cfg_node)
    
    def edge_AbortNode(self, predecessor, cfg_node, successor, **kwargs):
        return AbortEdge(predecessor, successor, cfg_node = cfg_node)
    
    def edge_AssumeNode(self, predecessor, cfg_node, successor, **kwargs):
        condition_ast = cfg_node.ast_node.child_by_field_name("condition")
        return AssumeEdge(predecessor, condition_ast, successor, False, cfg_node = cfg_node)

    def edge(self, predecessor, cfg_node, successor, **kwargs):
        if cfg_node is None: return SigmaEdge(predecessor, successor)
        return getattr(self, f"edge_{cfg_node.type}")(predecessor, cfg_node, successor, **kwargs)

    # API methods ----------------------------------------------------------

    def init_node(self):
        return self.root_cfa_node
    
    def exit_node(self):
        return self.exit_cfa_node
    
    def to_dot(self, intra = False):
        return to_dot(self.init_node(), intra = intra)


def to_dot(start_node, intra = False):
    edges = []

    seen = set()
    worklist = [start_node]

    while len(worklist) > 0:
        node = worklist.pop()
        if node in seen:
            continue
        seen.add(node)

        successors = node.intra().successors() if intra else node.successors()

        for edge in successors:
            worklist.append(edge.successor)

            edge_label = edge._edge_repr_().replace("\n", " ")
            edges.append(
                f'N{node.node_id} -> N{edge.successor.node_id} [label="{edge_label}"]'
            )
    
    nodes = []
    for node in seen:
        label = ""
        if node.cfg_node: label = f"({node.cfg_node.type[:10]})"

        if not node.is_accepting():
            nodes.append(f'N{node.node_id} [label="N{node.node_id}", color="white"]')
        elif node.is_error_node():
            nodes.append(f'N{node.node_id} [label="N{node.node_id}", color="red"]')
        elif node.is_branching_node(): # <--- ADD THIS
            nodes.append(f'N{node.node_id} [label="N{node.node_id}{label}", style="filled", fillcolor="lightblue", color="blue"]') # <--- ADD THIS
        else:
            nodes.append(f'N{node.node_id} [label="N{node.node_id}{label}"]')

    node_str = "\n".join(nodes)
    edge_str = "\n".join(edges)

    return f"""
digraph G {{
    {node_str}
    {edge_str}
}}
"""


# Intraprocedural --------------------------------------------------------------

class IntraproceduralView:

    def __init__(self, node):
        self._node = node

    def _handle_predecessor_edge(self, edge):

        if edge.successor.is_function_entry(): return None        

        if edge.successor.is_function_call_exit():
            call_entry_node = edge.successor.call_init()
            return BlankEdge(call_entry_node, self._node, cfg_node = edge.cfg_node)

        return edge

    def _handle_successor_call(self, call_edge):
        call_exit      = call_edge.predecessor.call_exit()
        return BlankEdge(self._node, call_exit, cfg_node = call_edge.cfg_node)
    
    def _handle_successor_return(self, return_edge):
        return_exit  = return_edge.predecessor.function_exit()
        return FunctionReturnEdge(self._node, return_exit, cfg_node = return_edge.cfg_node)

    def _handle_successor_abort(self, abort_edge):
        function_exit = abort_edge.predecessor.function_exit()
        return AbortEdge(self._node, function_exit, cfg_node = abort_edge.cfg_node)

    def _handle_successor_edge(self, edge):
        
        if edge.is_function_call():   return self._handle_successor_call(edge)
        if edge.is_function_return(): return self._handle_successor_return(edge)
        if edge.is_abort():           return self._handle_successor_abort(edge)

        if edge.predecessor.is_function_exit(): return None

        return edge
        

    # API methods --------------------------------

    def predecessors(self):
        output = []

        # Hacky! Need to be resolved in future
        if self._node.is_function_exit():
            for pred in self._node.cfg_node.predecessors():
                if pred.type != "ReturnStatementNode": continue
                pred_node = self._node.automata.attach(pred)
                output.append(
                    FunctionReturnEdge(pred_node, self._node, cfg_node = pred)
                )

        if self._node.is_function_call_exit():
            call_entry_node = self._node.call_init()
            return [BlankEdge(call_entry_node, self._node, cfg_node = self._node.cfg_node)]

        for edge in self._node.predecessors():
            edge = self._handle_predecessor_edge(edge)
            if edge is not None: output.append(edge)

        return output 

    def successors(self):
        output = []

        for edge in self._node.successors():
            edge = self._handle_successor_edge(edge)
            if edge is not None: output.append(edge)

        return output

# Utils ----------------------------------------------------------------------------------------

def intra_search(cfa_node, end = None):
    seen = set()

    if end is not None: seen.add(end)

    stack = [cfa_node]
    while len(stack) > 0:
        node = stack.pop()
        if node in seen: continue
        seen.add(node)

        yield node

        for succ in node.intra().successors():
            stack.append(succ.successor)


def intra_search_bwd(cfa_node, end = None):
    seen = set()

    if end is not None: seen.add(end)

    stack = [cfa_node]
    while len(stack) > 0:
        node = stack.pop()
        if node in seen: continue
        seen.add(node)

        yield node

        for pred in node.intra().predecessors():
            stack.append(pred.predecessor)


# Loop structure --------------------------------------------------------------

class Loop:

    def __init__(self, head, nodes):
        self.head = head
        self.nodes = nodes

        self._incoming = None
        self._outgoing = None


    def is_contained(self, loop):
        return loop.head in self.nodes
    
    def _compute_sets(self):
        if self._incoming is not None: return self._incoming, self._outgoing

        incoming = set.union(*[set(n.predecessors()) for n in self.nodes])
        outgoing = set.union(*[set(n.successors()) for n in self.nodes])

        inner = set.intersection(incoming, outgoing)
        call_edges   = set(e for e in outgoing if e.is_function_call())
        return_edges = set(e for e in incoming if e.is_function_exit()) 

        self._incoming = incoming - inner - return_edges
        self._outgoing = outgoing - inner - call_edges
        return self._incoming, self._outgoing


    def incoming(self):
        return self._compute_sets()[0]

    def outgoing(self):
        return self._compute_sets()[1]

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(head: {self.head}, nodes: {self.nodes})"
    

class LoopIndex(list):

    def __init__(self, loops):
        super().__init__(loops)

    def __getitem__(self, key):
        try:
            return super().__getitem__(key)
        except TypeError:
            return [l for l in self if key in l.nodes]


class LoopInfo:

    def __init__(self, cfa):
        self.cfa = cfa

        self._interproc_loops = {}

    def _function_head(self, cfa_node):
        return cfa_node.function_entry()
    
    def _find_loops(self, start_node):
        return _find_loops_in_function(start_node)
    
    def _compute_interproc_loop(self, function_name, function_start):
        try:
            return self._interproc_loops[function_name]
        except KeyError:
            self._interproc_loops[function_name] = self._find_loops(function_start)
            return self._interproc_loops[function_name]

    def find_loop(self, cfa_node):
        assert cfa_node.automata == self.cfa

        function_head = self._function_head(cfa_node)
        if function_head is None: return None
        function_name = function_head.cfg_node.function_name()
        interproc_loops = self._compute_interproc_loop(function_name, function_head)

        return interproc_loops[cfa_node]
    
# Loop identification ------------------------------------------------------

def _successors(cfa_node):
    return [e.successor for e in cfa_node.intra().successors()]

def _span_tree(chain_finder, start_node):
    fwd_edges = {start_node: []}
    bwd_edges = set()

    instack = set()
    call_stack = [(start_node, True, 0)]
    while len(call_stack) > 0:
        node, entry, pi = call_stack.pop(-1)
        if pi == 0: instack.add(node)

        if entry:
            if pi == 1:
                instack.discard(node)
                continue
            
            exit_node = chain_finder.exit_node(node)
            if exit_node != node:
                fwd_edges[node].append(exit_node)
                fwd_edges[exit_node] = []

            call_stack.append((node, True, 1))
            call_stack.append((exit_node, False, 0))
            continue
        
        adj = _successors(node)
        if pi < len(adj):
            call_stack.append((node, False, pi + 1))
            
            successor = adj[pi] # must be an entry node
            if successor not in fwd_edges:
                fwd_edges[node].append(successor)
                fwd_edges[successor] = []
                call_stack.append((successor, True, 0))
            elif successor in instack:
                bwd_edges.add((node, successor))
            else:
                fwd_edges[node].append(successor)
            continue
        
        instack.discard(node)
    
    return fwd_edges, bwd_edges


def _find_loops_in_function(function_start):
    chain_finder = ChainFinder()
    fwd_edges, bwd_edges = _span_tree(chain_finder, function_start)

    inv_fwd_edges = {}
    for node, edges in fwd_edges.items():
        for succ in edges:
            if succ not in inv_fwd_edges: inv_fwd_edges[succ] = []
            inv_fwd_edges[succ].append(node)
    
    loop_index = {}
    for end, head in bwd_edges:
        loop = chain_finder.exit_chain(head)
        loop.discard(end)
        
        stack = [end]
        while len(stack) > 0:
            node = stack.pop()
            if node in loop: continue
            loop.add(node)
            chain = chain_finder.exit_chain(node)

            for pred in inv_fwd_edges.get(node, []):
                chain.discard(pred)
                stack.append(pred)
            
            loop |= chain

        assert head.ast_node.type in [
            "labeled_statement", "while_statement", "for_statement", "do_statement"
        ], f"Detected {head} ({head.ast_node}) as loop head with incompatible type."
        
        if head not in loop_index: loop_index[head] = set()
        loop_index[head] |= loop

    return LoopIndex([Loop(head, loop) for head, loop in loop_index.items()])
        


# Chain finder ----------------------------------------------------

def _skip_silent_entry(entry_node):
    if not entry_node.is_silent(): return entry_node

    predecessors = entry_node.predecessors()
    assert len(predecessors) == 1
    return predecessors[0]


def _skip_silent_exit(exit_node):
    if not exit_node.is_silent(): return exit_node

    successors = exit_node.successors()
    assert len(successors) == 1
    return successors[0]


def _has_linear_flow(ast_node):
    """Test if the current subtree contains any code element that disturbes the linear flow"""
    disturbances = {"goto_statement", "labeled_statement", "while_statement", "do_statement", "for_statement"}
    return not contains_tree(ast_node, lambda node: node.type in disturbances)


def _linear_predecessors(cfa_node):
    predecessor_edges = cfa_node.intra().predecessors()
    predecessors = set(e.predecessor for e in predecessor_edges)

    if len(predecessors) == 0: return []

    if len(predecessors) == 1:
        predecessor_edge = predecessor_edges[0]
        return [predecessor_edge.predecessor]
    
    return list(predecessors) 

    cfg_node = cfa_node.cfg_node
    if cfg_node is None: return list(predecessors)
    if not _has_linear_flow(cfg_node.ast_node): return list(predecessors)

    init_node = _skip_silent_entry(cfg_node.entry_node())
    if init_node == cfg_node: return list(predecessors) # No jump possible
    return [cfa_node.automata.attach(init_node)]


def _linear_successors(cfa_node):
    successor_edges = cfa_node.intra().successors()
    successors = set(e.successor for e in successor_edges)

    if len(successors) == 0: return []

    if len(successors) == 1:
        successor_edge = successor_edges[0]
        return [successor_edge.successor]
    
    return list(successors)  
    
    cfg_node = cfa_node.cfg_node
    if cfg_node is None: return list(successors)
    if not _has_linear_flow(cfg_node.ast_node): return list(successors)

    exit_node = _skip_silent_exit(cfg_node.exit_node())
    if exit_node == cfg_node: return list(successors) # No jump possible
    return [cfa_node.automata.attach(exit_node)]

# Isolated ----------------------------------------------------------------

def _is_block_leader(cfa_node):
    ast_node = cfa_node.ast_node
    return ast_node.type in {"goto_statement", 
                             "labeled_statement", 
                             "while_statement", 
                             "do_statement", 
                             "for_statement",
                             "break_statement",
                             "continue_statement"}


def _isolated_predecessor(cfa_node):
    predecessors = _linear_predecessors(cfa_node)
    if len(predecessors) != 1: return None
    predecessor = predecessors[0]

    successors = _linear_successors(predecessor)
    if len(successors) > 1: return None
    return predecessor


def _isolated_successor(cfa_node):
    successors = _linear_successors(cfa_node)
    if len(successors) != 1: return None
    successor = successors[0]

    predecessors = _linear_predecessors(successor)
    if len(predecessors) > 1: return None

    return successor


class ChainFinder:

    def __init__(self):
        self._entry_cache = {}
        self._exit_cache = {}

    def _entry_node(self, cfa_node):
        chain = []

        predecessor = _isolated_predecessor(cfa_node)
        while predecessor is not None:
            assert predecessor != cfa_node, "Self loop?"

            if _is_block_leader(cfa_node): break

            chain.append(cfa_node)
            cfa_node = predecessor

            try:
                predecessor = self._entry_cache[cfa_node]
            except KeyError:
                predecessor = _isolated_predecessor(cfa_node)
    
        for node in chain:
            if node == cfa_node: continue
            self._entry_cache[node] = cfa_node

        return cfa_node

    def _exit_node(self, cfa_node):
        chain = []

        successor = _isolated_successor(cfa_node)
        while successor is not None:
            assert successor != cfa_node, "Self loop?"

            if _is_block_leader(cfa_node): break
    
            chain.append(cfa_node)
            cfa_node = successor

            try:
                successor = self._exit_cache[cfa_node]
            except KeyError:
                successor = _isolated_successor(cfa_node)

        for node in chain:
            if node == cfa_node: continue
            self._exit_cache[node] = cfa_node

        return cfa_node
    
    def entry_node(self, cfa_node):
        try:
            return self._entry_cache[cfa_node]
        except KeyError:
            return self._entry_node(cfa_node)
    
    def exit_node(self, cfa_node):
        try:
            return self._exit_cache[cfa_node]
        except KeyError:
            return self._exit_node(cfa_node)
        
    def exit_chain(self, exit_node):
        result = {k for k, v in self._exit_cache.items() if v == exit_node}
        result.add(exit_node)
        return result

# Edge matching --------------------------------------------------------------

def _match_edge(left_edge, right_edge):

    if left_edge.type != right_edge.type: return False

    if left_edge.is_declaration():     return _match_declaration(left_edge, right_edge)
    if left_edge.is_statement():       return _match_statement(left_edge, right_edge)
    if left_edge.is_assume():          return _match_assume(left_edge, right_edge)
    if left_edge.is_function_return(): return _match_function_return(left_edge, right_edge)
    if left_edge.is_function_call()  : return _match_function_call(left_edge, right_edge)

    return True


def _match_declaration(left_edge, right_edge):
    left_declaration, right_declaration = left_edge.declaration, right_edge.declaration

    if left_declaration.type.endswith("statement"):
        left_declaration = left_declaration.children[0]
    
    if right_declaration.type.endswith("statement"):
        right_declaration = right_declaration.children[0]

    return _match_ast(left_declaration, right_declaration)


def _match_statement(left_edge, right_edge):
    left_statement, right_statement = left_edge.statement, right_edge.statement

    if left_statement.type.endswith("statement"):
        left_statement = left_statement.children[0]
    
    if right_statement.type.endswith("statement"):
        right_statement = right_statement.children[0]

    return _match_ast(left_statement, right_statement)


def _match_assume(left_edge, right_edge):
    if left_edge.truth_value == right_edge.truth_value:
        return _match_ast(left_edge.condition, right_edge.condition, True)

    if not left_edge.truth_value:
        return _is_negation_of(left_edge.condition, right_edge.condition)

    return _is_negation_of(right_edge.condition, left_edge.condition)


def _match_function_return(left_edge, right_edge):
    if left_edge.ast_node is None:
        return left_edge.ast_node == right_edge.ast_node
    
    return _match_ast(left_edge.ast_node, right_edge.ast_node, True)

def _match_function_call(left_edge, right_edge):
    return _match_ast(left_edge.ast_node, right_edge.ast_node, True)


# AST matching --------------------------------------------------------------

def _match_ast(left_ast, right_ast, simplify = False):
    if simplify:
        left_ast = _simplify(left_ast)
        right_ast = _simplify(right_ast)
        if left_ast is None or right_ast is None: return False  # Simplified ASTs may be None if the original AST was a parenthesized expression with a single child
    
    if left_ast.type != right_ast.type: return False

    left_children, right_children = left_ast.children, right_ast.children
    if len(left_children) != len(right_children): return False

    if len(left_children) == 0:
        return left_ast.text.decode('utf-8') == right_ast.text.decode('utf-8')
    
    for left_child, right_child in zip(left_children, right_children):
        if not _match_ast(left_child, right_child, simplify = simplify): return False
    
    return True

def _simplify(ast_node):
    if ast_node.type == "parenthesized_expression":
        return _simplify(ast_node.children[1])
    
    if ast_node.type == "unary_expression" and ast_node.children[0].type == "!":
        double_negation = _find_negation(ast_node.children[1])
        if double_negation is not None:
            return _simplify(double_negation)
    
    return ast_node

def _find_negation(ast_node):
    if ast_node.type == "parenthesized_expression":
        return _find_negation(ast_node.children[1])
    
    if ast_node.type == "unary_expression" and ast_node.children[0].type == "!":
        return ast_node.children[1]
    
    return None

def _is_negation_of(left_ast, right_ast):
    left_ast = _simplify(left_ast)
    right_ast = _simplify(right_ast)
    
    if left_ast.type == "unary_expression" and left_ast.children[0].type == "!":
        return _match_ast(left_ast.children[1], right_ast, True)
    
    if right_ast.type == "unary_expression" and right_ast.children[0].type == "!":
        return _match_ast(left_ast, right_ast.children[1], True)

    return False