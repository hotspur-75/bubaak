#!/usr/bin/env python3
from utils import init_local_libs
init_local_libs()
import os
import re
import random
import collections
# Silence Git Python Warning
os.environ["GIT_PYTHON_REFRESH"] = "quiet"


import tree_sitter
import tree_sitter.binding

import code_ast
from code_ast.visitor import ASTVisitor

from pretransforms import support_extensions, add_helper_functions, add_compounds

from utils import main

FUNCTION_BLACKLIST = {}
FUNCTION_BLACKLIST_PATTERNS = []

def program_splitter(
        input_file  : str,
        left_split  : str = None,
        right_split : str = None,
        blacklist   : str  = "__VERIFIER_*,assume_abort_if_not,assume",
        allowed_unrolls : int = -1,
        max_line_limit : int = -1,
):
    if max_line_limit >= 0: _check_line_limit(input_file, max_line_limit)

    file_name = os.path.basename(input_file)

    if left_split is None:
        name, ext  = os.path.splitext(file_name)
        left_split = f"{name}.left{ext}"
    
    if right_split is None:
        name, ext = os.path.splitext(file_name)
        right_split = f"{name}.right{ext}"

    with open(input_file, "r") as f:
        source_code = f.read()

    source_code = add_helper_functions(source_code, ["assume_abort_if_not"])
    source_code = add_compounds(source_code, target_fn = "main")

    left, right = split_program(source_code, allowed_unrolls = allowed_unrolls, blacklist = blacklist)

    if left_split != "":
        with open(left_split, "w") as o:
            o.write(left)
    
    if right_split != "":
        with open(right_split, "w") as o:
            o.write(right)
    
    print("Done.")


def split_program(source_code : str, allowed_unrolls : int = -1, blacklist   : str  = "__VERIFIER_*,assume_abort_if_not"):
    """
    A function that splits a given source code into two parts. For splitting, we split if branches
    in an then part and else part. The first program contains only the then part and the second program
    only the else part. If we can verify both the then-program and the else-program, the original program
    should be correct.

    Args:
    --------
        source_code : str
        The source code written in GNU C. Currently, we might not support the complete C syntax.
        Not supported are switch statements and variadic functions. In addition, we do not support
        compiler annotations such as static, inline, extern, etc.

        allowed_unrolls: int = -1
        Restricts how often loops are unrolled during the splitting process. (Default: -1 = infinity)

        blacklist : str = "__VERIFIER_*,assume_abort_if_not"
        We assume that some functions are atomic and can therefore not be split. 
        Default: assume_abort_if_not and all functions starting with __VERIFIER_ are assumed to be atomic.
    
    Result:
    -------
        left, right
        The source code of the then-program (left) and the else-program (right)

    """
    setup_blacklist(blacklist)
    return support_extensions(source_code, _split_program_fn, unrolls = allowed_unrolls)

# Setup blacklist --------------------------------

def setup_blacklist(blacklist_string):
    FUNCTION_BLACKLIST = {}

    if len(blacklist_string) == 0: return

    if "," in blacklist_string:
        blacklist = [bstring.strip() for bstring in blacklist_string.split(",")]
    else:
        blacklist = [blacklist_string.strip()]

    for pattern in blacklist:
        try:
            FUNCTION_BLACKLIST_PATTERNS.append(re.compile(pattern))
        except re.error:
            FUNCTION_BLACKLIST.add(pattern)


# Helper --------------------------------

def _check_line_limit(input_file, line_limit):
    with open(input_file, 'r') as lines:
        if sum(1 for _ in lines) >= line_limit: 
            raise ValueError('File %s contains more than %d lines of code. Splitting might be unperforming. Abort.' % (input_file, line_limit))

def _replace(program_ast, node, target):
    source_lines = list(program_ast.source_lines)

    start_line, end_line = node.start_point[0], node.end_point[0]
    prefix  = source_lines[start_line][:node.start_point[1]]
    postfix = source_lines[end_line][node.end_point[1]:] 

    source_lines[start_line:end_line+1] = [prefix + target + postfix]
    return "\n".join(source_lines)


def _replace_all(program_ast, nodes, targets):
    source_lines = list(program_ast.source_lines)

    spans = []
    for node, target in zip(nodes, targets):
        spans.append((node.start_point, node.end_point, target))
    
    for start, end, target in sorted(spans, reverse=True):
        start_line, end_line = start[0], end[0]
        prefix  = source_lines[start_line][:start[1]]
        postfix = source_lines[end_line][end[1]:] 
        source_lines[start_line:end_line+1] = [prefix + target + postfix]
    
    return "\n".join(source_lines)


def _gen_label(target_node, postfix = ""):
    return f"L{target_node.start_point[0]}{postfix}"

# Structure splitter --------------------------------

SKIP_ANNOTATION = re.compile("\/\/(\s)?SKIP(\s(?P<skip>[0-9]+))?")

def _check_skip_annotation(program_ast, split_node):
    siblings      = split_node.parent.children
    next_siblings = 0
    if siblings[next_siblings] == split_node: return None

    while siblings[next_siblings + 1] != split_node:
        next_siblings += 1

    next_sibling = siblings[next_siblings]
    if next_sibling.type != "comment": return None
    annotation = program_ast.match(next_sibling)
    annotation_match = SKIP_ANNOTATION.match(annotation)
    if annotation_match is None: return None

    try:
        return int(annotation_match.group("skip"))
    except Exception:
        return 0
    

def _replace_break(loop_body, target):
    ast = code_ast.ast(loop_body, lang = "c", syntax_error = "ignore")

    nodes, targets  = [], []
    for break_stmt in _find_loop_nodes(ast, "break_statement"):
        nodes.append(break_stmt)
        targets.append(target)
    
    return _replace_all(ast, nodes, targets)


def _replace_continue(loop_body, target):
    ast = code_ast.ast(loop_body, lang = "c", syntax_error = "ignore")

    nodes, targets  = [], []
    for continue_stmt in _find_loop_nodes(ast, "continue_statement"):
        nodes.append(continue_stmt)
        targets.append(target)
    
    return _replace_all(ast, nodes, targets)



def _handle_while_split(program_ast, split_node, unrolls = -1):
    skip_annotation = _check_skip_annotation(program_ast, split_node)
    if skip_annotation is not None: unrolls = skip_annotation

    condition_node = split_node.child_by_field_name("condition")
    condition      = program_ast.match(condition_node)
    
    body_node      = split_node.child_by_field_name("body")
    body           = program_ast.match(body_node)

    target  = f"{body}"
    loop    = program_ast.match(split_node)
    postfix = ""

    if "break" in body:
        break_label = _gen_label(split_node, "_exit")
        target  = _replace_break(target, f"goto {break_label};")
        postfix = f"\n{break_label}:;"

    if "continue" in body:
        continue_label = _gen_label(split_node, "_contd")
        target = _replace_continue(target, f"goto {continue_label};")
        target = f"{target}\n{continue_label}:;"

    skip = ""
    if unrolls != -1: skip = f"//SKIP {unrolls - 1}"

    target = f"if{condition}{{\n{target}\n{skip}\n{loop}\n{postfix}}}"
    return _replace(program_ast, split_node, target)


def _handle_do_split(program_ast, split_node):
    
    condition_node = split_node.child_by_field_name("condition")
    condition      = program_ast.match(condition_node)
    
    body_node      = split_node.child_by_field_name("body")
    body           = program_ast.match(body_node)

    target  = f"{body}"
    loop    = f"while({condition}){body}"
    postfix = ""

    if "break" in body:
        break_label = _gen_label(split_node, "_exit")
        target  = _replace_break(target, f"goto {break_label};")
        postfix = f"\n{break_label}:;"

    if "continue" in body:
        continue_label = _gen_label(split_node, "_contd")
        target = _replace_continue(target, f"goto {continue_label};")
        target = f"{target}\n{continue_label}:;"

    target = f"{target}\n{loop}\n{postfix}"
    return _replace(program_ast, split_node, target)


def _handle_for_split(program_ast, split_node):

    initializer = split_node.child_by_field_name("initializer")
    scoping_needed = False
    if initializer is not None:
        scoping_needed = initializer.type.endswith("declaration")
        initializer = program_ast.match(initializer) + ";"
    else:
        initializer = ""

    condition_node = split_node.child_by_field_name("condition")
    if condition_node is not None:
        condition      = program_ast.match(condition_node)
    else:
        condition = "1"

    update        = split_node.child_by_field_name("update")
    if update is not None: 
        update = program_ast.match(update) + ";"
    else:
        update = ""

    body_node      = split_node.child_by_field_name("body")
    body           = program_ast.match(body_node)
    
    loop    = f"while({condition}){{ {body} \n {update} }}"
    target = f"{initializer}\n{loop}"
    if scoping_needed: target = f"{{\n{target}\n}}"
    return _replace(program_ast, split_node, target)

# Function calls ----------------------------------------------------------------


def _check_if_void_indicator(program_ast, parameter_definitions):
    if len(parameter_definitions) != 1: return False
    param_def = parameter_definitions[0]
    if param_def.child_by_field_name("declarator") is not None: return False
    param_type = param_def.child_by_field_name("type")
    return program_ast.match(param_type) == "void"


def _validate_function_definition(program_ast, function_definition):
    num_lines = len(program_ast.match(function_definition).splitlines())

    if num_lines > 500:
        raise ValueError("[INLINE] Function definition contains more than 500 lines. Abort.")

    function_signature    = function_definition.child_by_field_name("declarator")
    parameter_definitions = function_signature.child_by_field_name("parameters")
    parameter_definitions = [pdef for pdef in parameter_definitions.children if pdef.type == "parameter_declaration"]

    if _check_if_void_indicator(program_ast, parameter_definitions): return

    if any(param.type == "variadic_parameter" for param in parameter_definitions):
        raise ValueError("Variadic parameters are not supported for function inlining...")


def _map_actual_to_formal(ast, call_node, function_signature):
    parameter_definitions = function_signature.child_by_field_name("parameters")
    parameter_definitions = [pdef for pdef in parameter_definitions.children if pdef.type == "parameter_declaration"]
    
    if _check_if_void_indicator(ast, parameter_definitions): return {}

    actual_parameters = call_node.child_by_field_name("arguments")
    actual_parameters = [arg for arg in actual_parameters.children if arg.type not in ["(", ")", ","]]

    assert len(actual_parameters) == len(parameter_definitions)

    return list(zip(parameter_definitions, actual_parameters))


def _formal_type(ast, formal_param):
    param_type = formal_param.child_by_field_name("type")
    param_type = ast.match(param_type)

    declarator = formal_param.child_by_field_name("declarator")
    while declarator.type != "identifier":
        if declarator.type != "pointer_declarator": raise ValueError("Unsupported declarator: %s" % declarator.type)
        param_type += "*"
        declarator = declarator.child_by_field_name("declarator")
    
    return param_type


def _param_name(ast, formal_param):
    declarator = formal_param.child_by_field_name("declarator")
    while declarator.type != "identifier":
        if declarator.type != "pointer_declarator": raise ValueError("Unsupported declarator: %s" % declarator.type)
        declarator = declarator.child_by_field_name("declarator")
    
    return ast.match(declarator)


def _handle_direct_mapping(ast, formal_param, actual_param):
    formal_name = _param_name(ast, formal_param)
    return None, formal_name, "(%s)" % ast.match(actual_param)


def _handle_actual_formal_mapping(call_id, ast, formal_param, actual_param):
    formal_type = _formal_type(ast, formal_param)
    if formal_type.startswith("void"): # Only direct replacement possible
        return _handle_direct_mapping(ast, formal_param, actual_param)
    
    if actual_param.type == "identifier":
        return _handle_direct_mapping(ast, formal_param, actual_param)
    
    actual_param_decl = ast.match(actual_param)

    formal_name    = _param_name(ast, formal_param)
    new_param_name = call_id + "_" + formal_name
    new_definition = f"{formal_type} {new_param_name} = ( {actual_param_decl} );"
    
    return new_definition, formal_name, new_param_name


def _handle_mapping_from_actual_to_formal(call_id, program_ast, param_mapping):
    definitions = []
    mapping     = {}

    for formal_param, actual_param in param_mapping:
        definition, src, target = _handle_actual_formal_mapping(call_id, program_ast, formal_param, actual_param)
        if definition is not None: definitions.append(definition)
        mapping[src] = target

    return definitions, mapping


# Hacky solution might not scale
def _replace_identifier(function_body, mapping):
    ast = code_ast.ast(function_body, lang = "c", syntax_error = "ignore")

    nodes, targets  = [], []
    for identifier in _find_nodes(ast, "identifier"):
        name = ast.match(identifier)
        if name in mapping:
            nodes.append(identifier)
            targets.append(mapping[name])
    
    return _replace_all(ast, nodes, targets)


def _replace_returns(function_body, target, suffix = ""):
    ast = code_ast.ast(function_body, lang = "c", syntax_error = "ignore")

    nodes, targets  = [], []
    for return_stmt in _find_nodes(ast, "return_statement"):
        return_stmt_match = ast.match(return_stmt)
        return_stmt_match = return_stmt_match.replace("return", target) + suffix
        nodes.append(return_stmt)
        targets.append(return_stmt_match)

    return _replace_all(ast, nodes, targets)


def _prefix_labels(function_body, prefix):
    ast = code_ast.ast(function_body, lang = "c", syntax_error = "ignore")

    nodes, targets  = [], []
    for label_stmt in _find_nodes(ast, "labeled_statement"):
        label_node = label_stmt.child_by_field_name("label")
        label      = ast.match(label_node)
        nodes.append(label_node)
        targets.append(prefix + label)

    for goto_stmt in _find_nodes(ast, "goto_statement"):
        label_node = goto_stmt.child_by_field_name("label")
        label      = ast.match(label_node)
        nodes.append(label_node)
        targets.append(prefix + label)
    
    return _replace_all(ast, nodes, targets)


def _handle_call_expr(program_ast, split_node):
    definitions = _parse_func_definitions(program_ast)
    call_name   = program_ast.match(split_node.child_by_field_name("function"))

    if call_name not in definitions: raise ValueError("%s is not defined [Defined Functions: %s]" % (call_name, ", ".join(definitions.keys())))

    function_definition = definitions[call_name]
    function_declarator = function_definition.child_by_field_name("declarator")

    _validate_function_definition(program_ast, function_definition)

    call_id = "CID_L%d%d" % (split_node.start_point[0], random.randrange(0, 100))
    exit_label = call_id + "_exit"

    parameter_mapping = _map_actual_to_formal(program_ast, split_node, function_declarator)
    
    additional_definitions, mapping = _handle_mapping_from_actual_to_formal(call_id, program_ast, parameter_mapping)

    function_body       = function_definition.child_by_field_name("body")
    function_body       = program_ast.match(function_body)
    function_body       = add_compounds(function_body)
    function_body       = _prefix_labels(function_body, call_id + "_")
    function_body       = _replace_identifier(function_body, mapping)

    return_type = function_definition.child_by_field_name("type")
    return_type = program_ast.match(return_type)

    result_var    = call_id+'_result'
    result_target = ""
    if return_type != "void":
        result_statement = f"{return_type} {result_var};"
        additional_definitions.append(result_statement)
        result_target =  f"{result_var} ="
    
    function_body = _replace_returns(function_body, result_target, f" goto {exit_label};")
    function_body = "\n".join(additional_definitions + [function_body])

    # Parent statement
    parent = split_node
    while not parent.type.endswith("statement"):
        if parent.type.endswith("declarator"):
            raise ValueError("Inlining of function calls in declarations are not supported.")

        parent = parent.parent
    
    call         = program_ast.match(split_node)
    parent_stmt  = program_ast.match(parent)

    call_stmt = ";"
    if return_type != "void":
        call_stmt = parent_stmt.replace(call, result_var)

    function_body += "\n" + exit_label + ": " + call_stmt

    return _replace(program_ast, parent, function_body)


def handle_split_condition(program_ast, split_node, unrolls = -1):
    if split_node.type == "while_statement":
        return _handle_while_split(program_ast, split_node, unrolls = unrolls)
    
    if split_node.type == "do_statement":
        return _handle_do_split(program_ast, split_node)

    if split_node.type == "for_statement":
        return _handle_for_split(program_ast, split_node)

    if split_node.type == "call_expression":
        return _handle_call_expr(program_ast, split_node)

    raise ValueError("Unsupported split condition: %s" % split_node.type)


# SPLIT IF ------------------------------------------------------------------------------------------------

def _handle_left_if_split(program_ast, split_node):
    condition_node = split_node.child_by_field_name("condition")
    condition      = program_ast.match(condition_node)
    
    consequence_node  = split_node.child_by_field_name("consequence")

    if condition_node is None:
        consequence = ""
    else:
        if _has_goto_violations(program_ast, consequence_node, split_node.child_by_field_name("alternative")):
            raise ValueError("Consequence defines goto that links to an arbitrary location")
    
        consequence = program_ast.match(consequence_node)
    
    target = f"assume_abort_if_not({condition});\n{consequence}"
    return _replace(program_ast, split_node, target)


def _handle_right_if_split(program_ast, split_node):
    condition_node = split_node.child_by_field_name("condition")
    condition      = program_ast.match(condition_node)
    
    alternative_node  = split_node.child_by_field_name("alternative")

    if alternative_node is None:
        alternative = ""
    else:
        if alternative_node.type == "else_clause":
            alternative_node = alternative_node.children[1]
        
        if _has_goto_violations(program_ast, alternative_node, split_node.child_by_field_name("consequence")):
            raise ValueError("Alternative defines goto that links to an arbitrary location")

        alternative = program_ast.match(alternative_node)
    
    target = f"assume_abort_if_not(!({condition}));\n{alternative}"
    return _replace(program_ast, split_node, target)

class DomainSplitFinder(ASTVisitor):
    def __init__(self, ast):
        self.ast = ast
        self.candidates = []
        self._current_func = None

    def visit_function_definition(self, node):
        function_name_node = _name_node(node)
        if function_name_node:
            self._current_func = self.ast.match(function_name_node)
        return True

    def leave_function_definition(self, node):
        self._current_func = None
        return True

    def visit_binary_expression(self, node):
        if self._current_func != "main": return True
        
        op = self.ast.match(node.children[1]) 
        if op in ["<", "<=", ">", ">=", "==", "!="]:
            left = node.children[0]
            right = node.children[2]
            
            # Extract the variable name
            var_name = None
            pivot_node = None
            if left.type == "identifier" and right.type == "number_literal":
                var_name = self.ast.match(left)
                pivot_node = self.ast.match(right)
            elif right.type == "identifier" and left.type == "number_literal":
                var_name = self.ast.match(right)
                pivot_node = self.ast.match(left)
                
            if var_name:
                # SAFETY CHECK: Ignore floating point numbers (e.g., 100.0, 1e-5)
                if "." in pivot_node or "e" in pivot_node.lower():
                    return True
                self.candidates.append((var_name, pivot_node))
        return True

def _try_domain_split(program_ast):
    source_str = "\n".join(program_ast.source_lines)

    if "/* BUBAAK_DOMAIN_SPLIT_APPLIED */" in source_str:
        return None 
        
    finder = DomainSplitFinder(program_ast)
    program_ast.visit(finder)
    
    if not finder.candidates:
        return None
        
    from collections import Counter
    ranked_candidates = Counter(finder.candidates).most_common()
    
    best_candidate = ranked_candidates[0][0]
    key_var, pivot = best_candidate
    
    split_node = _find_split_point(program_ast)
    if split_node is None: return None
    
    print(f"[*] Applied Domain Split on '{key_var}' at pivot '{pivot}'")
    original_stmt = program_ast.match(split_node)
    
    # 1. Inject the assume natively without the extern declaration here
    left_target = f"/* BUBAAK_DOMAIN_SPLIT_APPLIED */\n__VERIFIER_assume({key_var} < {pivot});\n{original_stmt}"
    right_target = f"/* BUBAAK_DOMAIN_SPLIT_APPLIED */\n__VERIFIER_assume(!({key_var} < {pivot}));\n{original_stmt}"
    
    left_code = _replace(program_ast, split_node, left_target)
    right_code = _replace(program_ast, split_node, right_target)
    
    # 2. Safely prepend the extern declaration to the global scope (top of the file)
    extern_decl = "extern void __VERIFIER_assume(int);\n"
    return extern_decl + left_code, extern_decl + right_code

def _split_program_fn(source_code, unrolls = -1):
    program_ast = code_ast.ast(source_code, lang = "c", syntax_error = "warn")
    
    # 1. ATTEMPT DOMAIN SPLIT FIRST
    domain_split_result = _try_domain_split(program_ast)
    if domain_split_result is not None:
        return domain_split_result # Returns (left, right)
        
    # 2. FALLBACK TO STANDARD SPLIT
    split_node = _find_split_point(program_ast)
    if split_node is None: raise ValueError("Function cannot be split")

    while split_node.type != "if_statement":
        source_code = handle_split_condition(program_ast, split_node, unrolls = unrolls)
        program_ast = code_ast.ast(source_code, lang = "c", syntax_error = "warn")
        split_node  = _find_split_point(program_ast)
        if split_node is None: raise ValueError("Function cannot be split")

    left  = _handle_left_if_split(program_ast, split_node)
    right = _handle_right_if_split(program_ast, split_node)

    return left, right



# Split point --------------------------------


def _error(node, message):
    error_message = f"{message} (at {node.type} [Line: {node.start_point[0]}])"
    raise ValueError(error_message)

def _name_node(function_node):
    declarator = function_node.child_by_field_name('declarator')
    if declarator is not None:
        name_node = declarator.child_by_field_name('declarator')
        if name_node is None or name_node.type != "identifier": return None
        return name_node
    return None

class SplitFinder(ASTVisitor):

    def __init__(self, ast):
        self.ast = ast
        self.split_node = None
        self._current_func  = None
        self._func_defs = _parse_func_definitions(ast)

    # Visitor functions -----------------------------

    def visit_function_definition(self, node):
        function_name_node = _name_node(node)
        if function_name_node is None: return True
        function_name = self.ast.match(function_name_node)
        self._current_func = function_name
    
    def leave_function_definition(self, node):
        function_name_node = _name_node(node)
        if function_name_node is None: return True
        function_name = self.ast.match(function_name_node)
        if self._current_func == function_name:
            self._current_func = None

    # Visit branch --------------------------------

    def _check_skip(self, node):
        skip_annotation = _check_skip_annotation(self.ast, node)
        return skip_annotation == 0

    def visit_if_statement(self, node):
        if self.split_node is not None : return False
        if self._current_func != "main": return False
        self.split_node = node

    def visit_for_statement(self, node):
        if self.split_node is not None : return False
        if self._current_func != "main": return False
        self.split_node = node

    def visit_while_statement(self, node):
        if self.split_node is not None : return False
        if self._current_func != "main": return False
        if self._check_skip(node)      : return False
        self.split_node = node

    def visit_do_statement(self, node):
        if self.split_node is not None : return False
        if self._current_func != "main": return False
        self.split_node = node

    def visit_call_expression(self, node):
        if self.split_node is not None : return False
        if self._current_func != "main": return False

        call_name_node = node.child_by_field_name("function")
        if call_name_node.type != "identifier": _error(node, "Attributes are not supported")

        call_name = self.ast.match(call_name_node)
        if call_name in FUNCTION_BLACKLIST: return False
        if call_name not in self._func_defs: return False
        if any(pattern.match(call_name) for pattern in FUNCTION_BLACKLIST_PATTERNS): return False

        self.split_node = node
    
    # Ignore statements -------------------------------------
   
    def visit_switch_statement(self, node):
        if self.split_node is not None : return False
        if self._current_func != "main": return False
        _error(node, "Switch statement are not supported")


def _find_split_point(program_ast):
    finder = SplitFinder(program_ast)
    program_ast.visit(finder)
    return finder.split_node


# Function definitions ----------------------------------------------------

class FunctionFinder(ASTVisitor):

    def __init__(self, ast):
        self.ast = ast
        self._func_definitions = {}

    # Visitor functions -----------------------------

    def visit_function_definition(self, node):
        function_name_node = _name_node(node)
        if function_name_node is None: return False
        function_name = self.ast.match(function_name_node)
        self._func_definitions[function_name] = node
        return False

def _parse_func_definitions(ast):
    finder = FunctionFinder(ast)
    ast.visit(finder)
    return finder._func_definitions

# Identifier finder --------------------------------

class NodeFinder(ASTVisitor):

    def __init__(self, ast, node_type):
        self.ast = ast
        self.node_type = node_type
        self._nodes = []
    
    def visit(self, node):
        if node.type == self.node_type:
            self._nodes.append(node)

def _find_nodes(ast, node_type):
    finder = NodeFinder(ast, node_type)
    ast.visit(finder)
    return finder._nodes


# Find scoped for loop body --------------------

class ScopedNodeFinder(ASTVisitor):

    def __init__(self, ast, node_type):
        self.ast = ast
        self.node_type = node_type
        self._nodes = []
    
    def visit_for_statement(self, node):
        return False
    
    def visit_while_statement(self, node):
        return False

    def visit_do_statement(self, node):
        return False
    
    def visit(self, node):
        if node.type == self.node_type:
            self._nodes.append(node)

def _find_loop_nodes(ast, node_type):
    finder = ScopedNodeFinder(ast, node_type)
    ast.visit(finder)
    return finder._nodes

# Check for goto violations --------------------------------

class GotoFinder(ASTVisitor):

    def __init__(self, ast):
        self.ast = ast
        self.gotos = collections.defaultdict(list)

    def visit_goto_statement(self, node):
        goto_label = node.child_by_field_name("label")
        goto_label = self.ast.match(goto_label)
        self.gotos[goto_label].append(node)


class GotoTargetFinder(ASTVisitor):

    def __init__(self, ast, stop = None):
        self.ast = ast
        self.labels = {}
        
        self._stop = stop

    def visit_labeled_statement(self, node):
        label = node.child_by_field_name("label")
        label = self.ast.match(label)
        self.labels[label] = node

    def visit(self, node):
        return node != self._stop  

def _has_goto_violations(ast, keep_node, delete_node = None):
    goto_finder = GotoFinder(ast)
    goto_finder.walk(keep_node)
    gotos = goto_finder.gotos
    if len(gotos) == 0: return False

    if delete_node is not None:
        target_finder = GotoTargetFinder(ast)
        target_finder.walk(delete_node)
        if any(g in target_finder.labels for g in gotos):
            return True

    # Goto to function definition
    parent = keep_node
    while parent and parent.type != "function_definition":
        parent = parent.parent
    
    if not parent: return True # Just to be safe

    target_finder = GotoTargetFinder(ast, stop = keep_node.parent)
    target_finder.walk(parent)
    targets = target_finder.labels

    for goto_label in gotos:
        if goto_label not in targets: return True # Jump target can be anywhere
        goto_target = targets[goto_label]

        if any(goto_target.start_point < g.start_point for g in gotos[goto_label]):
            return True 
    return False


if __name__ == "__main__":
    main(program_splitter, version = "0.1")