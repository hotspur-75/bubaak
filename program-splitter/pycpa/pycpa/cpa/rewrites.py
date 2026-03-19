import re
import random

from collections import namedtuple

import code_ast as ca

from .lattice import CompositeDomain, CompositeElement, FlatDomain, FlatElement
from .base import ProgramAnalysis, AnalysisState, CallStackState, LoopAnalysisState

from .. import cfg
from ..cfa import ControlFlowAutomata, LabeledNode, FunctionSummaryEdge
from ..nodes import WhileLoopNode, DoWhileLoopNode, ForLoopConditionNode, IfBranchNode, AssumeNode, CaseStatementEntryNode
from ..nodes import FunctionCallInitNode
from ..visitors import visit_tree

from ..optimizers import simplify



# SKIP annotations --------------------------------

SKIP_ANNOTATION = re.compile("\/\/(\s)?SKIP(\s(?P<skip>[0-9]+))?")

def _next_sibling(node):
    siblings      = node.parent.children
    next_siblings = 0
    if siblings[next_siblings] == node: return None

    while siblings[next_siblings + 1] != node:
        next_siblings += 1

    return siblings[next_siblings]

def _check_skip_annotation(split_node, return_node = False):
    result       = None
    next_sibling = _next_sibling(split_node)
    if next_sibling is not None and next_sibling.type == "comment": 
        annotation = next_sibling.text.decode('utf-8')
        annotation_match = SKIP_ANNOTATION.match(annotation)
        if annotation_match is not None: 
            try:
                result = int(annotation_match.group("skip"))
            except Exception:
                result = 0

    if result is None: next_sibling = None
    if return_node: return result, next_sibling
    return result

def clean_skip_annotations(source_code, root_node = None):
    if root_node is None:
        source_ast = ca.ast(source_code, lang = "c",  syntax_error = "ignore")
        root_node = source_ast.root_node

    nodes = []
    for comment_node in visit_tree(root_node, lambda node: node.type == "comment"):
        annotation = comment_node.text.decode('utf-8')
        annotation_match = SKIP_ANNOTATION.match(annotation)
        if annotation_match is not None: nodes.append(comment_node)

    return _replace_all(source_code, nodes, [""] * len(nodes))

# Main rewrites ----------------------------------------

ASTRewrite = namedtuple('ASTRewrite', ['ast_node', 'text', 'semantic_preserving'])


class RewriteState(AnalysisState):
    
    def __init__(self, base, rewrites, sem_preserving = True, cfa_node = None):
        super().__init__(cfa_node or base.cfa_node)
        self.base = base
        self.rewrites = rewrites
        self.sem_preserving = sem_preserving

    def abstraction(self):
        return CompositeElement(self.rewrites, self.base.abstraction())
    
    def is_rewritten(self):
        return len(self.rewrites.value) > 0
    
    def is_semantic_preserving(self):
        return self.sem_preserving
    
    def source_code(self):
        current_cfa_node = self.cfa_node
        current_automata = current_cfa_node.automata
        current_graph    = current_automata.control_flow_graph
        root_node        = current_graph.root_node
        
        return root_node.text.decode('utf-8')


    def _state_key_(self):
        return (self.rewrites.value,) + self.base._state_key_()
    
    def _index_key_(self):
        return (self.rewrites.value,) + self.base._index_key_()

    def __repr__(self):
        rewrites = [
            f"({r[0][0]}:{r[0][1]} - {r[0][2]}:{r[0][3]}) -> {_shorten(r[1], 20)}"
            for r in self.rewrites.value
        ]

        if len(rewrites) == 0:
            rewrites = "[]"
        else:
            rewrites = f"{', '.join(rewrites)}"

        return f"{str(self.base)} with {rewrites}"


class RewriteAnalysis(ProgramAnalysis):

    def __init__(self, rewriter, subanalysis, abort_if_not_preserving = False):
        self.rewriter = rewriter
        self.subanalysis = subanalysis
        self.abort_if_not_preserving = abort_if_not_preserving

    def domain(self):
        return CompositeDomain(FlatDomain(), self.subanalysis.domain())
    
    def init_state(self, cfa):
        rewrites = FlatElement(tuple())
        init_node, init_precision = self.subanalysis.init_state(cfa)
        return RewriteState(init_node, rewrites), init_precision

    def merge(self, first_state, second_state, precision = None):
        if first_state.rewrites != second_state.rewrites:
            return second_state
        
        merged_substate = self.subanalysis.merge(first_state.base, second_state.base, precision)
        if merged_substate is None or merged_substate == second_state.base:
            return second_state
        
        return RewriteState(merged_substate, second_state.rewrites)
    
    def refine(self, state, precision, reached_set):
        new_state, new_precision = self.subanalysis.refine(
            state.base,
            precision,
            {(r[0].base, r[1]) for r in reached_set}
        )

        if new_state != state.base or new_precision != precision:
            return RewriteState(new_state, rewrites = state.rewrites), new_precision

        return state, precision
    
    def rebuild_cfa(self, state, rewrites):        
        current_source_code = state.source_code()
        
        # Apply rewrites --------------------------------

        rewrite_nodes   = [rew[0] for rew in rewrites]
        rewrite_targets = [rew[1] for rew in rewrites]
        new_source_code = _replace_all(current_source_code, rewrite_nodes, rewrite_targets)

        # Build new CFA --------------------------------

        return ControlFlowAutomata(cfg(new_source_code))
    
    def rewrite_edge(self, state, cfa_edge):
        return self.rewriter.rewrite_edge(state, cfa_edge)

    
    def handle_edge(self, state, cfa_edge):
        if self.abort_if_not_preserving and not state.is_semantic_preserving():
            return None, None
        
        new_state, new_precision = self.subanalysis.handle_edge(
            state.base,
            cfa_edge
        )

        if new_state is None: return None, None

        new_state = RewriteState(new_state, rewrites = state.rewrites)

        if rewrite_allowed(state) or rewrite_allowed(new_state):

            new_rewrites = self.rewrite_edge(new_state, cfa_edge)

            if new_rewrites is not None and len(new_rewrites) > 0:
                new_cfa = self.rebuild_cfa(new_state, new_rewrites)
                restart_node, restart_precision = self.subanalysis.init_state(new_cfa)

                sem_preserving = state.is_semantic_preserving() and all(
                    not isinstance(rew, ASTRewrite) or rew.semantic_preserving
                    for rew in new_rewrites
                )

                new_rewrites = [rew[:2] for rew in new_rewrites]
                new_rewrites = tuple((id.start_point + id.end_point, rewrite) for id, rewrite in new_rewrites)
                new_rewrites = FlatElement(state.rewrites.value + new_rewrites)
                return RewriteState(restart_node, rewrites = new_rewrites, sem_preserving = sem_preserving), restart_precision

        return new_state, new_precision
    

def rewrite_allowed(rewrite_state):
    state = rewrite_state.base
    
    for substate in state:
        if isinstance(substate, CallStackState):
            if sum(1 for call in substate.callstack.value if len(call.call_sides()) > 1) > 0: 
                return False
        elif isinstance(substate, LoopAnalysisState):
            if substate.size() > 0: return False

    return True


# Rewriter ------------------------------------------------------

class Rewriter(object):

    def default_rewrite_edge(self, state, cfa_edge):
        return None
    
    def rewrite_edge(self, state, cfa_edge):
        return getattr(self, f"rewrite_{cfa_edge.type}", self.default_rewrite_edge)(state, cfa_edge)


class CompositeRewriter(Rewriter):

    def __init__(self, rewriters):
        self.rewriters = rewriters

    def rewrite_edge(self, state, cfa_edge):
        for rewriter in self.rewriters:
            rewrites = rewriter.rewrite_edge(state, cfa_edge)
            if rewrites is not None and len(rewrites) > 0: return rewrites
        return self.default_rewrite_edge(state, cfa_edge)
    
# Main analyses --------------------------------


class SplittingRewritingAnalysis(RewriteAnalysis):
    
    def __init__(self, subanalysis, loop_iter_bound = -1, clone_iter_bound = -1, **kwargs):
        super().__init__(
            CompositeRewriter([
                SideEffectRewriter(),
                LoopUnrollingRewriter(loop_iter_bound=loop_iter_bound),
                FunctionCloningRewriter(clone_iter_bound=clone_iter_bound),
                SplittingRewriter(),
            ]),
            subanalysis = subanalysis,
            **kwargs
        )


# Side Effect Rewriting Analysis --------------------------------

def has_function_calls(ast_node):
    return len(visit_tree(ast_node, lambda node: node.type == "call_expression")) > 0

def has_variable_writes(ast_node):
    return len(visit_tree(ast_node, 
                        lambda node: node.type in ["assignmen_expression", 
                                                   "update_expression"])) > 0

class SideEffectRewriter(Rewriter):

    # Invariant: 
    # Function calls are not allowed in any expression, except for function call expression
    # This also holds for function call parameters

    # Actual rewrites --------------------------------

    def rewrite_call_expressions(self, ast_node, scope = None):
        rewrite_target, side_effects = rewrite_with_side_effects(scope, ast_node, target_fn = lambda node: node.type == "call_expression")
        rewrite_node, rewrite_target, need_compound = rewrite_statement(ast_node, rewrite_target)

        full_rewrite_target = "\n".join(side_effects + [rewrite_target])
        if need_compound: full_rewrite_target = f"{{\n{full_rewrite_target}\n}}"

        return [
            (rewrite_node, full_rewrite_target)
        ]

    # Explores ---------------------------------------

    def rewrite_DeclarationEdge(self, state, cfa_edge):
        declaration = cfa_edge.declaration
        if not has_function_calls(declaration): return
        return self.rewrite_call_expressions(declaration, scope = cfa_edge.scope())

    def rewrite_StatementEdge(self, state, cfa_edge):
        statement = cfa_edge.statement
        if not has_function_calls(statement): return
        return self.rewrite_call_expressions(statement, scope = cfa_edge.scope())

    def rewrite_AssumeEdge(self, state, cfa_edge):
        condition = cfa_edge.condition

        if has_function_calls(condition):
            return self.rewrite_call_expressions(condition, scope = cfa_edge.scope())
        

    def rewrite_FunctionCallEdge(self, state, cfa_edge):
        call_node = cfa_edge.call_node.ast_node
        
        for call_expression in visit_tree(call_node, 
                                          lambda node: node.type == "call_expression", 
                                          lambda node: node.type != "call_expression"):
            parameters = call_expression.child_by_field_name("arguments")
            if not has_function_calls(parameters): continue
            return self.rewrite_call_expressions(parameters, scope = cfa_edge.scope())

# Loop unrolling -------------------------------

def _gen_label(target_node, postfix = ""):
    return f"L{target_node.start_point[0]}{postfix}"


def _find_loop_nodes(ast_node, node_type):
    return visit_tree(ast_node, 
                      lambda node: node.type == node_type,
                      lambda node: node.type not in ["while_statement", "do_statement", "for_statement"])


def _prefix_all_labels(loop_body, prefix):
    ast = ca.ast(loop_body, lang = "c", syntax_error = "ignore")

    nodes, targets  = [], []
    for label_stmt in visit_tree(ast, lambda node: node.type in ["labeled_statement", "goto_statement"]):
        if label_stmt.type == "labeled_statement":
            label = label_stmt.children[0].text.decode('utf-8')
            new_label  = f"{prefix}{label}"
            nodes.append(label_stmt.children[0])
            targets.append(new_label)
        
        if label_stmt.type == "goto_statement":
            goto_label = label_stmt.children[1].text.decode('utf-8')
            new_label  = f"{prefix}{goto_label}"
            nodes.append(label_stmt.children[1])
            targets.append(new_label)
    
    return _replace_all(loop_body, nodes, targets)


def _replace_break(loop_body, target):
    ast = ca.ast(loop_body, lang = "c", syntax_error = "ignore")

    nodes, targets  = [], []
    for break_stmt in _find_loop_nodes(ast, "break_statement"):
        nodes.append(break_stmt)
        targets.append(target)
    
    return _replace_all(loop_body, nodes, targets)


def _replace_continue(loop_body, target):
    ast = ca.ast(loop_body, lang = "c", syntax_error = "ignore")

    nodes, targets  = [], []
    for continue_stmt in _find_loop_nodes(ast, "continue_statement"):
        nodes.append(continue_stmt)
        targets.append(target)
    
    return _replace_all(loop_body, nodes, targets)


def _unroll_loop(parent_node, condition, body, annotation = "", dowhile = False):
    if condition == "0": return ";"

    loop_prefix, suffix = annotation, ""

    loop_body, loop_iter = body, body

    loop_iter = _prefix_all_labels(loop_body, _gen_label(parent_node, "_"))

    if "break" in body:
        break_label = _gen_label(parent_node, "_exit")
        loop_iter   = _replace_break(loop_iter, f"goto {break_label};")
        suffix      = f"\n{break_label}:;"

    if "continue" in body:
        continue_label = _gen_label(parent_node, "_contd")
        loop_iter = _replace_continue(loop_iter, f"goto {continue_label};")
        loop_prefix = f"{loop_prefix}{continue_label}:"

    if condition == "1" or dowhile:
        return f"{loop_iter}\n{loop_prefix}while({condition}){loop_body}\n{suffix}"
    else:
        return f"if({condition}){{\n {loop_iter}\n{loop_prefix}while({condition}){loop_body}\n}}\n{suffix}"


class LoopUnrollingRewriter(Rewriter):

    def __init__(self, *args, loop_iter_bound = -1, **kwargs):
        super().__init__(*args, **kwargs)
        self.loop_iter_bound = loop_iter_bound

    def unroll_loop(self, state, cfa_edge, loop_cfg_node):
        if self.loop_iter_bound == 0: return []

        loop_node = loop_cfg_node.ast_node

        num_lines = loop_node.end_point[0] - loop_node.start_point[0] + 1
        assert num_lines <= 500, "[UNROLL] Loop contains more than 500 lines. Abort."

        loop_iter, annotation_node = _check_skip_annotation(loop_cfg_node.ast_node, return_node = True)
        if loop_iter is None: loop_iter = self.loop_iter_bound
        if loop_iter == 0: return None
        loop_iter -= 1

        condition_node = loop_node.child_by_field_name("condition")
        condition = simplify(condition_node)
        body      = loop_node.child_by_field_name("body").text.decode('utf-8')

        annotation = ""
        if loop_iter >= 0:
            annotation = f"// SKIP {loop_iter}\n"

        if isinstance(loop_cfg_node, DoWhileLoopNode):
       
            rewrites = [
                (loop_node, _unroll_loop(loop_node, condition, body, annotation = annotation, dowhile = True))
            ]
        
        elif isinstance(loop_cfg_node, ForLoopConditionNode):
            initializer = loop_node.child_by_field_name("initializer")
            update      = loop_node.child_by_field_name("update")

            if update is not None:
                update = update.text.decode('utf-8')
                for_body    = f"{{\n{body}\n{update};}}"
            else:
                for_body = body
        
            unroll_body = _unroll_loop(loop_node, condition, for_body, annotation = annotation)

            if initializer is not None:
                initializer = initializer.text.decode('utf-8')
                unroll_body = f"{initializer};\n{unroll_body}"

            rewrites = [
                (loop_node, unroll_body)
            ]
            
        elif isinstance(loop_cfg_node, WhileLoopNode):
            rewrites = [
                (loop_node, _unroll_loop(loop_node, condition, body, annotation = annotation))
            ]
        
        if annotation_node is not None:
            rewrites.append((annotation_node, ""))
        
        return rewrites
        
    def unroll_goto_loop(self, state, cfa_edge, labeled_node):
        if self.loop_iter_bound == 0: return []
        # TODO: Handle this case
        successor = cfa_edge.successor
        loop_info = successor.loop_info()

        if loop_info is not None and any(successor in loop.nodes for loop in loop_info):
            raise ValueError("We are currently not unrolling GOTO loops")

    def rewrite_edge(self, state, cfa_edge):
        if cfa_edge.successor.is_labeled_node():
            return self.unroll_goto_loop(state, cfa_edge, cfa_edge.successor)
        
        if isinstance(cfa_edge.successor.cfg_node, (WhileLoopNode, ForLoopConditionNode, DoWhileLoopNode)):
            return self.unroll_loop(state, cfa_edge, cfa_edge.successor.cfg_node)

        return getattr(self, f"rewrite_{cfa_edge.type}", self.default_rewrite_edge)(state, cfa_edge)

# Function inlining --------------------------------

def _extract_function_call(ast_node):
    if ast_node.type == "call_expression": return ast_node
    
    if ast_node.type == "expression_statement": 
        return _extract_function_call(ast_node.children[0])
    
    if ast_node.type == "assignment_expression":
        return _extract_function_call(ast_node.child_by_field_name("right"))
    
    if ast_node.type == "declaration":
        return _extract_function_call(
            ast_node.children[1].children[-1]
        )
    

def _extract_result_var(ast_node):
    if ast_node.type == "call_expression": return None, None
    if ast_node.type == "expression_statement":
        expression = ast_node.children[0]
        if expression.type == "assignment_expression":
            return expression.child_by_field_name("left").text.decode('utf-8'), None

    if ast_node.type == "declaration":
        decl_type = ast_node.child_by_field_name("type").text.decode('utf-8')
        var_name  = ast_node.children[1].children[0].text.decode('utf-8')

        return var_name, f"{decl_type} {var_name}"

    return None, None


def _check_if_void_indicator(parameter_definitions):
    if len(parameter_definitions) != 1: return False
    param_def = parameter_definitions[0]
    if param_def.child_by_field_name("declarator") is not None: return False
    param_type = param_def.child_by_field_name("type")
    return param_type.text.decode('utf-8') == "void"


def _validate_function_definition_for_inlining(function_definition):
    num_lines = len(function_definition.text.decode('utf-8').splitlines())

    if num_lines > 500:
        raise ValueError("[INLINE] Function definition contains more than 500 lines. Abort.")

    function_signature    = function_definition.child_by_field_name("declarator")
    parameter_definitions = function_signature.child_by_field_name("parameters")
    parameter_definitions = [pdef for pdef in parameter_definitions.children if pdef.type == "parameter_declaration"]

    if _check_if_void_indicator(parameter_definitions): return

    if any(param.type == "variadic_parameter" for param in parameter_definitions):
        raise ValueError("Variadic parameters are not supported for function inlining...")


def _map_actual_to_formal(call_node, function_signature):
    parameter_definitions = function_signature.child_by_field_name("parameters")
    parameter_definitions = [pdef for pdef in parameter_definitions.children if pdef.type == "parameter_declaration"]
    
    if _check_if_void_indicator(parameter_definitions): return {}

    actual_parameters = call_node.child_by_field_name("arguments")
    actual_parameters = [arg for arg in actual_parameters.children if arg.type not in ["(", ")", ","]]

    assert len(actual_parameters) == len(parameter_definitions)

    return list(zip(parameter_definitions, actual_parameters))


def _formal_type(formal_param):
    param_type = formal_param.child_by_field_name("type")
    param_type = param_type.text.decode('utf-8')

    declarator = formal_param.child_by_field_name("declarator")
    while declarator.type != "identifier":
        if declarator.type != "pointer_declarator": raise ValueError("Unsupported declarator: %s" % declarator.type)
        param_type += "*"
        declarator = declarator.child_by_field_name("declarator")
    
    return param_type


def _param_name(formal_param):
    declarator = formal_param.child_by_field_name("declarator")
    while declarator.type != "identifier":
        if declarator.type != "pointer_declarator": raise ValueError("Unsupported declarator: %s" % declarator.type)
        declarator = declarator.child_by_field_name("declarator")
    
    return declarator.text.decode('utf-8')


def _handle_direct_mapping(formal_param, actual_param):
    formal_name = _param_name(formal_param)
    return None, formal_name, "(%s)" % actual_param.text.decode('utf-8')


def _handle_actual_formal_mapping(call_id,formal_param, actual_param):
    formal_type = _formal_type(formal_param)
    if formal_type.startswith("void"): # Only direct replacement possible
        return _handle_direct_mapping(formal_param, actual_param)
    
    if actual_param.type == "identifier":
        return _handle_direct_mapping(formal_param, actual_param)
    
    actual_param_decl = actual_param.text.decode('utf-8')

    formal_name    = _param_name(formal_param)
    new_param_name = call_id + "_" + formal_name
    new_definition = f"{formal_type} {new_param_name} = ( {actual_param_decl} );"
    
    return new_definition, formal_name, new_param_name


def _handle_mapping_from_actual_to_formal(call_id, param_mapping):
    definitions = []
    mapping     = {}

    for formal_param, actual_param in param_mapping:
        definition, src, target = _handle_actual_formal_mapping(call_id, formal_param, actual_param)
        if definition is not None: definitions.append(definition)
        mapping[src] = target

    return definitions, mapping


def _replace_identifiers(function_body, call_id, mapping):
    function_body_ast = ca.ast(function_body, lang = "c", syntax_error = "ignore").root_node()

    nodes, targets = [], []
    
    for identifier in visit_tree(function_body_ast, lambda node: node.type == "identifier"):
        identifier_text = identifier.text.decode('utf-8')

        if identifier_text in mapping:
            nodes.append(identifier)
            targets.append(mapping[identifier_text])

        if identifier.parent.type in ["labeled_statement", "goto_statement"]:
            if identifier.parent.child_by_field_name("label") != identifier: continue
            nodes.append(identifier)
            targets.append(call_id + "_" + identifier_text)
    
    return _replace_all(function_body, nodes, targets)


def _replace_returns(function_body, target, suffix = ""):
    ast = ca.ast(function_body, lang = "c", syntax_error = "ignore").root_node()

    nodes, targets  = [], []
    for return_stmt in visit_tree(ast, lambda node: node.type == "return_statement"):
        return_stmt_match = return_stmt.text.decode('utf-8')
        return_stmt_match = return_stmt_match.replace("return", target) + suffix
        nodes.append(return_stmt)
        targets.append(return_stmt_match)

    return _replace_all(function_body, nodes, targets)



def _inline_function_call(function_definition, call_expression, result_var = None):
    _validate_function_definition_for_inlining(function_definition)

    call_id = "CID_L%d%d" % (call_expression.start_point[0], random.randrange(0, 100))

    function_signature    = function_definition.child_by_field_name("declarator")
    return_type = function_definition.child_by_field_name("type").text.decode('utf-8')
    
    parameter_mapping = _map_actual_to_formal(call_expression, function_signature)

    additional_definitions, mapping = _handle_mapping_from_actual_to_formal(call_id, parameter_mapping)

    # Hacky solution. Is there a better solution?
    function_body_ast = function_definition.child_by_field_name("body")
    function_body = function_body_ast.text.decode('utf-8')
    function_body = _replace_identifiers(function_body, call_id, mapping)

    goto_stmt, exit_stmt = "", ""
    if len(function_body_ast.children) > 3:
        exit_label = call_id + "_exit"
        goto_stmt = f" goto {exit_label};"
        exit_stmt = f"{exit_label}:;"

    if return_type != "void":
        result_target = ""
        if result_var is None:
            result_var    = call_id+'_result'
            result_statement = f"{return_type} {result_var};"
            additional_definitions.append(result_statement)
        
        result_target =  f"{result_var} ="
        function_body = _replace_returns(function_body, result_target, goto_stmt)
    else:
        assert len(visit_tree(function_definition, "return_statement")) == 0
    
    function_body = "\n".join(additional_definitions + [function_body, exit_stmt])

    return result_var, function_body


class FunctionInliningRewriter(Rewriter):

    def __init__(self, *args, inline_iter_bound = -1, **kwargs):
        super().__init__(*args, **kwargs)
        self.inline_iter_bound = inline_iter_bound

    def rewrite_edge(self, state, cfa_edge):
        if isinstance(cfa_edge.successor.cfg_node, FunctionCallInitNode):
            return self.inline_call(state, cfa_edge, cfa_edge.successor.cfg_node)

        return super().rewrite_edge(state, cfa_edge)

    def inline_call(self, state, cfa_edge, call_init_node):
        call_node = _extract_function_call(call_init_node.ast_node)
 
        function = call_node.child_by_field_name("function")
        assert function.type == "identifier"
        function_name = function.text.decode('utf-8')

        defined_functions = cfa_edge.scope().function_definitions()
        if function_name not in defined_functions: return []

        function_definition = defined_functions[function_name]

        function_definition_ast = function_definition.ast_node

        inline_iter = _check_skip_annotation(function_definition_ast)
        if inline_iter is None: inline_iter = self.inline_iter_bound
        if inline_iter == 0: return None
        inline_iter -= 1

        result_var, result_definition = _extract_result_var(call_init_node.ast_node)
        
        _, inline_text = _inline_function_call(
            function_definition_ast, call_node, result_var = result_var,
        )

        if result_definition is not None:
            inline_text = f"{result_definition};\n{inline_text}"

        if inline_iter >= 0:
        
            sibling = _next_sibling(function_definition_ast)
            if sibling.type == "comment":
                annotation = f"// SKIP {inline_iter}"
                replace = (sibling, annotation)
            else:
                function_text = function_definition_ast.text.decode('utf-8')
                annotation = f"// SKIP {inline_iter}\n{function_text}"
                replace = (function_definition_ast, annotation)

            return [
                replace,
                (call_init_node.ast_node, inline_text)
            ]

        return [
            (call_init_node.ast_node, inline_text)
        ]
    
# Function cloning --------------------------------

def _create_clone(function_definition_ast, clone_name):
    start_point = function_definition_ast.start_point
    declarator  = function_definition_ast.child_by_field_name("declarator")

    while declarator and declarator.type != "function_declarator":
        declarator = declarator.child_by_field_name("declarator")

    assert declarator is not None
    identifier  = declarator.child_by_field_name("declarator")
    assert identifier.type == "identifier"

    start_same_line = start_point[0] == identifier.start_point[0]
    end_same_line = start_point[0] == identifier.end_point[0]

    replace_range = (
        (identifier.start_point[0] - start_point[0], 
         identifier.start_point[1] - start_point[1] if start_same_line else 0),
        (identifier.end_point[0] - start_point[0],
         identifier.end_point[1] - start_point[1] if end_same_line else 0)
    )

    function_definition_text = function_definition_ast.text.decode('utf-8')
    function_definition_lines = function_definition_text.splitlines(True)
    if replace_range[0][0] == replace_range[1][0]:
        prefix = function_definition_lines[replace_range[0][0]][:replace_range[0][1]]
        suffix = function_definition_lines[replace_range[0][0]][replace_range[1][1]:]
        function_definition_lines[replace_range[0][0]] =  prefix + clone_name + suffix 
    else:
        raise ValueError("This should never happen")
    
    clone_definition_text = "".join(function_definition_lines)
    return  clone_definition_text

class LazyFunctionClone:
    """Proxy object to delay physical function copying until generation."""
    def __init__(self, function_definition_ast, clone_name, function_definition_text, clone_iter):
        self.ast = function_definition_ast
        self.clone_name = clone_name
        self.text = function_definition_text
        self.clone_iter = clone_iter
        self._materialized_text = None

    def __str__(self):
        # Materialize on demand only when the pipeline strictly requires it
        if self._materialized_text is None:
            declarator = self.ast.child_by_field_name("declarator")

            while declarator and declarator.type != "function_declarator":
                declarator = declarator.child_by_field_name("declarator")

            identifier = declarator.child_by_field_name("declarator")
            
            # FIX: Convert to bytes first because tree-sitter indices are strictly byte-offsets, 
            # not Python Character indices.
            func_bytes = self.text.encode('utf-8')
            clone_bytes = self.clone_name.encode('utf-8')
            
            sig_start = identifier.start_byte - self.ast.start_byte
            sig_end = identifier.end_byte - self.ast.start_byte
            
            # Splice on the byte-array
            definition_clone_bytes = func_bytes[:sig_start] + clone_bytes + func_bytes[sig_end:]
            definition_clone = definition_clone_bytes.decode('utf-8')

            if self.clone_iter > 0:
                self._materialized_text = f"{definition_clone}\n\n// SKIP {self.clone_iter}\n{self.text}"
            else:
                self._materialized_text = f"{definition_clone}\n\n{self.text}"
                
        return self._materialized_text

    def __add__(self, other):
        return str(self) + str(other)

    def __radd__(self, other):
        return str(other) + str(self)

    def __len__(self):
        return len(str(self))


def _function_call_node(function_call_ast):
    if function_call_ast.type == "declaration":
            call_expression = function_call_ast.children[1].children[-1]
    else:
        call_expression = function_call_ast.children[0]
        if call_expression.type == "assignment_expression":
            call_expression = call_expression.children[-1]
        
    return call_expression.child_by_field_name("function")


class FunctionCloningRewriter(Rewriter):
    def __init__(self, *args, clone_iter_bound = -1, **kwargs):
        super().__init__(*args, **kwargs)
        self.clone_iter_bound = clone_iter_bound
        self.lazy_clones = {} # Keep track of demand-driven proxies

    def rewrite_edge(self, state, cfa_edge):
        if isinstance(cfa_edge.successor.cfg_node, FunctionCallInitNode):
            if self.should_clone(state, cfa_edge):
                return self.clone_call(state, cfa_edge, cfa_edge.successor.cfg_node)

        return super().rewrite_edge(state, cfa_edge)
    
    def _clone_annotation(self, cfa_edge):
        function_definition = cfa_edge.successor.cfg_node.called_function()
        if function_definition is None: return 0, None
        function_definition_ast = function_definition.ast_node

        clone_iter, annotation_node = _check_skip_annotation(function_definition_ast, return_node = True)
        if clone_iter is None: clone_iter = self.clone_iter_bound
        return clone_iter, annotation_node
    
    def _is_forbidden(self, cfa_edge):
        function_definition = cfa_edge.successor.cfg_node.called_function()
        if function_definition is None: return True
        function_name = function_definition.function_name()

        if function_name.startswith("__VERIFIER_assume"): return True
        if function_name in ["assume", "assert", "assume_abort_if_not"]: return True

        return False

    def _check_annotation(self, cfa_edge):
        if self.clone_iter_bound == 0: return False
        return self._clone_annotation(cfa_edge)[0] != 0
    
    def _has_multiple_callees(self, cfa_edge):
        function_definition = cfa_edge.successor.cfg_node.called_function()
        if function_definition is None: return True # Overapproximation
        return len(function_definition.call_sides()) > 1 
    
    def _too_much_overhead(self, cfa_edge):
        function_definition = cfa_edge.successor.cfg_node.called_function()
        if function_definition is None: return False
        def_ast = function_definition.ast_node

        num_lines = def_ast.end_point[0] - def_ast.start_point[0] + 1
        assert num_lines <= 500, "[CLONE] Function definition contains more than 500 lines. Abort."

        return False
        
    def should_clone(self, state, cfa_edge):
        if self._is_forbidden(cfa_edge): return False
        if not self._check_annotation(cfa_edge): return False
        if not self._has_multiple_callees(cfa_edge): return False
        if self._too_much_overhead(cfa_edge): return False
        return True
    
    def _generate_clone_name(self, name, available_functions):
        clone_id = 0
        while f"{name}_clone_id{clone_id}" in available_functions:
            clone_id += 1
        return f"{name}_clone_id{clone_id}"

    def clone_call(self, state, cfa_edge, call_init_node):
        function_definition = call_init_node.called_function()
        if function_definition is None: return []

        clone_iter, annotation_node = self._clone_annotation(cfa_edge)

        replacements = []

        call_name_node = _function_call_node(call_init_node.ast_node)

        available_functions = function_definition.scope.function_definitions()

        function_name = function_definition.function_name()
        clone_name    = self._generate_clone_name(function_name, available_functions)

        replacements.append((call_name_node, clone_name))

        if annotation_node is not None:
            replacements.append((annotation_node, ""))

        function_definition_text  = function_definition.ast_node.text.decode('utf-8')
        
        # Cache and defer using Lazy Proxy
        cache_key = (function_name, clone_name)
        if cache_key not in self.lazy_clones:
            self.lazy_clones[cache_key] = LazyFunctionClone(
                function_definition.ast_node, 
                clone_name, 
                function_definition_text, 
                clone_iter
            )
        
        lazy_proxy = self.lazy_clones[cache_key]

        # Pass the proxy instead of eagerly generating the materialized string copy
        replacements.append((function_definition.ast_node, lazy_proxy))
        return replacements

# Splitting Analysis --------------------------------

def _build_assume(condition):
    if condition.startswith("!("):
        return f"if({condition[1:]}) abort();"

    return f"if(!({condition})) abort();"

def _is_undeletable(body_ast):
    return len(visit_tree(body_ast, lambda node: node.type == "labeled_statement")) > 0


class SplittingRewriter(Rewriter):

    def rewrite_if_branch(self, state, cfa_edge, condition):
        branch_node = cfa_edge.cfg_node.ast_node

        if condition == "0": return [
                ASTRewrite(branch_node, f"abort();", False)
            ]
        
        assume_statement = ""
        if condition != "1": assume_statement = _build_assume(condition)

        body = branch_node.child_by_field_name("consequence")
        alt  = branch_node.child_by_field_name("alternative")

        if not cfa_edge.truth_value: body, alt = alt, body

        if body is None: body = ""
        elif body.type == "else_clause":
            body = body.children[1].text.decode('utf-8')
        else:
            body = body.text.decode('utf-8') 

        if alt is not None and _is_undeletable(alt):
            if alt.type == "else_clause":
                alt = alt.children[1].text.decode('utf-8')
            else:
                alt = alt.text.decode('utf-8')

            body = f"{body}\nif(0){alt}"

        return [
            ASTRewrite(branch_node, f"{assume_statement}\n{body}", False)
        ]
    
        
    def rewrite_AssumeEdge(self, state, cfa_edge):

        cfg_node = cfa_edge.cfg_node

        if isinstance(cfg_node, AssumeNode): return None

        branch_node = cfg_node.ast_node
        condition = branch_node.child_by_field_name("condition")

        if condition is not None:
            if condition.children[1].text.decode('utf-8') == "0":
                # This is explicitly declared to be non-reachable
                # Therefore, we avoid processing this assume (since it might contain a jump target)
                return None

            condition = simplify(condition)
        else:
            condition = "1"

        if not cfa_edge.truth_value:
            if condition == "0": condition = "1"
            elif  condition == "1": condition = "0"
            else: condition = f"!({condition})"
        
        if isinstance(cfg_node, IfBranchNode):
            return self.rewrite_if_branch(state, cfa_edge, condition)
        
        if isinstance(cfg_node, CaseStatementEntryNode):
            raise ValueError("Switch cases are currently not supported")



# Rewrite helper --------------------------------


def _make_temp_var(prefix):
    return f"{prefix}_{random.randint(0, 10000)}"


def _determine_type(scope, ast_node):
    if ast_node.type == "call_expression":
        function = ast_node.child_by_field_name("function")
        assert function.type == "identifier"
        function_name = function.text.decode('utf-8')

        defined_functions = scope.function_definitions()

        if function_name in defined_functions:
            function_definition = defined_functions[function_name]
            return function_definition.return_type().text.decode('utf-8')

        if function_name in scope.external_functions():
            function_definition = scope.external_functions()[function_name]
            return function_definition.child_by_field_name('type').text.decode('utf-8')

    if ast_node.type == "identifier":
        identifier_name = ast_node.text.decode('utf-8')
        variables       = scope.defined_variables()
        assert identifier_name in variables
        variable_definition = variables[identifier_name]
        variable_type = variable_definition.child_by_field_name("type")
        return variable_type.text.decode('utf-8')

    raise ValueError(f"Cannot determine type of {ast_node.type}")


class SideEffectRewriterHelper:

    def __init__(self, scope, target_fn):
        self.scope = scope
        self.target_fn = target_fn

        self._side_effects = []

    def _has_target(self, ast_node):
        return len(visit_tree(ast_node, self.target_fn)) > 0
    
    def _rewrite_default(self, node):
        return node.text.decode('utf-8')

    def _rewrite_declaration(self, node):
        declaration_type = node.child_by_field_name("type")

        for child in node.children:
            if child.type.endswith("declarator"):
                target = self._rewrite(child)
                self._side_effects.append(
                    f"{declaration_type.text.decode('utf-8')} {target};"
                )
        
        return ""
    
    def _rewrite_init_declarator(self, node):
        value = self._rewrite(node.child_by_field_name("value"))
        declarator = node.child_by_field_name("declarator")
        return f"{declarator.text.decode('utf-8')} = {value}"
    
    # Expressions ----------------------------------------------------------------

    def _rewrite_binary_expression(self, node):
        left, op, right = node.children
        left_rew  = self._rewrite(left)

        need_rewrite = self._has_target(right)
        if need_rewrite and op.type == "&&": # Shortcut
            temp_var = _make_temp_var(f"_AND{node.start_point[0]}")
            self._side_effects.append(f"int {temp_var} = 0;")
            self._side_effects.append(f"if({left_rew}){{")
            right_rew = self._rewrite(right)
            self._side_effects.append(f"{temp_var} = {right_rew};")
            self._side_effects.append("}")
            return temp_var
        
        if need_rewrite and op.type == "||":
            temp_var = _make_temp_var(f"_OR{node.start_point[0]}")
            self._side_effects.append(f"int {temp_var} = 1;")
            self._side_effects.append(f"if(!({left_rew})){{")
            right_rew = self._rewrite(right)
            self._side_effects.append(f"{temp_var} = {right_rew};")
            self._side_effects.append("}")
            return temp_var

        right_rew = self._rewrite(right)
        return f"{left_rew} {op.type} {right_rew}"
    
    def _rewrite_conditional_expression(self, node):
        # Support of a? call(): call() because we can determine return types
        # However, more complicated cases cannot be supported
        condition = self._rewrite(node.child_by_field_name("condition"))
        consequence = node.child_by_field_name("consequence")
        alternative = node.child_by_field_name("alternative")

        need_rewrite = self._has_target(consequence) or self._has_target(alternative)
        if not need_rewrite: return self._rewrite_default(node)

        expression_type = None
        try:
            expression_type = _determine_type(self.scope, consequence)
        except ValueError:
            expression_type = _determine_type(self.scope, alternative)

        if expression_type is None: raise ValueError("Cannot determine type for conditional")

        temp_var = _make_temp_var(f"_COND{node.start_point[0]}")
        self._side_effects.append(f"{expression_type} {temp_var};")
        self._side_effects.append(f"if({condition}){{")
        self._side_effects.append(f"{temp_var} = {self._rewrite(consequence)};")
        self._side_effects.append("} else {")
        self._side_effects.append(f"{temp_var} = {self._rewrite(alternative)};")
        self._side_effects.append("}")

        return temp_var


    def _rewrite_assignment_expression(self, node):
        left, op, right = node.children
        left_rew  = self._rewrite(left)
        right_rew = self._rewrite(right)
        return f"{left_rew} {op.type} {right_rew}"


    def _rewrite_call_expression(self, node):
        arguments = node.child_by_field_name("arguments")
        rewrite_arguments = [self._rewrite(arg) for arg in arguments.children if arg.type not in ["(", ")", ","]]
        function = self._rewrite(node.child_by_field_name("function"))
        return f"{function}({', '.join(rewrite_arguments)})"

    def _rewrite_parenthesized_expression(self, node):
        return f"({self._rewrite(node.children[1])})"

    def _rewrite_subscript_expression(self, node):
        argument  = self._rewrite(node.child_by_field_name("argument"))
        subscript = self._rewrite(node.child_by_field_name("index"))
        return f"{argument}[{subscript}]"

    def _rewrite_unary_expression(self, node):
        op, value = node.children
        return f"{op.type} {self._rewrite(value)}"

    def _rewrite_update_expression(self, node):
        value, op = node.children
        return f"{self._rewrite(value)} {op.type}"
    
    # Special handling ---------------------------------------------------------------

    def _rewrite_cast_expression(self, node):
        type = node.child_by_field_name("type").text.decode('utf-8')
        value = self._rewrite(node.child_by_field_name("value"))
        return f"({type}) {value}"

    def _rewrite_field_expression(self, node):
        argument = node.child_by_field_name("argument")
        field    = node.child_by_field_name("field")
        return f"{self._rewrite(argument)}->{field.text.decode('utf-8')}"
    
    def _rewrite_pointer_expression(self, node):
        op, value = node.children
        return f"{op.type} {self._rewrite(value)}"
    
    # Statements ------------------------------------------------

    def _rewrite_expression_statement(self, node):
        return self._rewrite(node.children[0]) + ";"
    
    def _rewrite_argument_list(self, node):
        return "".join([self._rewrite(arg) for arg in node.children])

    # -----------------------------------------------------------


    def _rewrite(self, ast_node, check_target = True):
        if check_target and self.target_fn(ast_node):
            rewrite = self._rewrite(ast_node, check_target = False)
            node_type = _determine_type(self.scope, ast_node)
            new_var   = _make_temp_var(f"__LINE{ast_node.start_point[0]}")
            self._side_effects.append(f"{node_type} {new_var} = {rewrite};")
            return new_var
        
        if not self._has_target(ast_node):
            return self._rewrite_default(ast_node)

        return getattr(self, f"_rewrite_{ast_node.type}", self._rewrite_default)(ast_node)

    def rewrite(self, ast_node):
        rewrite_target = self._rewrite(ast_node)
        return rewrite_target, self._side_effects


def rewrite_with_side_effects(scope, ast_node, target_fn):
    return SideEffectRewriterHelper(scope, target_fn).rewrite(ast_node)


def rewrite_statement(ast_node, rewrite_target):
    # Returns statement_node + rewritten statement 
    
    statement_node = ast_node
    while statement_node.parent.type not in ["compound_statement", "if_statement", 
                                            "while_statement", "do_statement", "case_statement", 
                                            "labeled_statement"]:
        statement_node = statement_node.parent
    
    statement_text = statement_node.text.decode('utf-8')
    statement_text = statement_text.replace(ast_node.text.decode('utf-8'), rewrite_target) # This might replace too much?!

    return statement_node, statement_text, statement_node.parent.type != "compound_statement"



        
# Helper --------------------------------

def _is_overlapping(spans):
    sorted_spans = sorted(spans)

    for i in range(len(sorted_spans) - 1):
        if sorted_spans[i][1] > sorted_spans[i + 1][0]:
            return True

    return False


def _replace_all(source_code, nodes, targets):
    assert len(nodes) == len(targets), "Number of nodes and targets do not match"
    if len(nodes) == 0: return source_code

    source_lines = source_code.strip().splitlines(True)

    spans = []
    for node, target in zip(nodes, targets):
        spans.append((node.start_point, node.end_point, target))

    assert not _is_overlapping(spans), "Cannot edit overlapping spans at the same time."

    for start, end, target in sorted(spans, reverse=True):
        start_line, end_line = start[0], end[0]
        prefix  = source_lines[start_line][:start[1]]
        postfix = source_lines[end_line][end[1]:] 
        replace_line = prefix + target + postfix
        if len(replace_line.strip()) == 0: replace_line = ""
        source_lines[start_line:end_line+1] = [replace_line]

    return "".join(source_lines)


def _shorten(text, length):
    if len(text) <= length: return text
    return text[:length] + "..."