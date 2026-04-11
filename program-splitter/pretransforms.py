import re
from typing import List

import code_ast

def remove_comments(text):
    """
    removes comments from a piece of source code.
    source: https://stackoverflow.com/questions/241327/remove-c-and-c-comments-using-python
    """
    def replacer(match):
        s = match.group(0)
        if s.startswith('/'):
            return " "  # note: a space and not an empty string
        else:
            return s
    pattern = re.compile(
        r'//.*?$|/\*.*?\*/|\'(?:\\.|[^\\\'])*\'|"(?:\\.|[^\\"])*"',
        re.DOTALL | re.MULTILINE
    )
    return re.sub(pattern, replacer, text)


def regex(code: str) -> str:
    r"""
    creates a regex expression matching the code with changed whitespace
    does not support string and char values:
    "" would become "\s*", the same for ''
    """
    # shrink whitespace
    code = re.sub(r"\s+", " ", code)
    code = re.sub(r"(?! )(\W)(?! )(\W)", r"\1 \2", code)
    code = re.sub(r"(\w)(?! )(\W)", r"\1 \2", code)
    code = re.sub(r"(?! )(\W)(\w)", r"\1 \2", code)
    code = re.sub(r"(\w) (\w)", r"\1\n\n\2", code)

    # escape special symbols
    code = re.sub(r"(?=[\\(){}\[\].+$^*?|])", r"\\", code)

    # replace whitespace
    code = re.sub(r" ", r"\\s*", code)
    code = re.sub(r"\n\n", r"\\s+", code)

    return code


def unsupported_to_extern(code: str, replacings: List, unsupported: str) -> str:
    """replaces any method containing the unsupported string with an extern method"""
    while r := re.search(unsupported, code):
        start = open = code.find("{")
        depth = 1
        close = code.find("}")
        while True:
            if close < open:
                end = close
                close = code.find("}", close) + 1
                depth -= 1
                if depth == 0:
                    if start < r.start() < close:
                        break
                    start = open
                    depth = 1
            else:
                open = code.find("{", open + 1)
                if open == -1: raise ValueError("Something went wrong")
                depth += 1
        previous_end = max(code[:start].rfind(";"), code[:start].rfind("}")) + 1
        save = code[previous_end:end]
        code = ";".join([code[:start], code[end:]])
        code = "extern ".join([code[:previous_end], code[previous_end:]])
        replacings += [(regex(code[previous_end:start + 8]), save)]
    
    return code


def support_extensions(code: str, func, **kwargs):
    """remove code which can not be parsed by pycparser, do func and reconstruct incompatible code afterwards"""
    #code = remove_comments(code)
    replacings = []  # pattern-string combinations which later must be replaced

    # remove unsupported parts
    try:
        code = unsupported_to_extern(code, replacings, "__extension__")
    except ValueError:
        replacings = []

    for keyword in ("inline", "restrict"):
        if f"__{keyword} " in code and not re.search(f"(?<!__){keyword}", code):
            code = code.replace(f"__{keyword}", keyword)
            replacings += [(keyword, f"__{keyword}")]

    any = "[a-zA-Z0-9()_*, \n]*"
    attr_or_const = r"(__attribute__ *\(\([a-zA-Z0-9_, ]*\)\)|__const )"
    while r := re.search(f"extern{any}{attr_or_const}{any};", code):
        replacings += [(regex(re.sub(attr_or_const, "", r.group())), r.group())]
        code = re.sub(f"extern{any}{attr_or_const}{any};", re.sub(attr_or_const, " ", r.group()), code, 1)

    # execute func
    results = func(code, **kwargs)
    if isinstance(results, str): results = [results]

    output  = ()

    for code in results:
        # reconstruct incompatible code
        for pair in replacings:
            code = re.sub(pair[0], pair[1], code)
        output += (code,)

    if len(output) == 1: return output[0]
    return output

# Add helper functions ----------------------------------------------------------------

ASSUME = """
extern void abort(void);
void assume_abort_if_not(int cond) {
  if(!cond) {abort();}
}
"""

ASSUME0 = """
extern void abort(void);
void assume_abort_if_not0(int cond) {
  if(!cond) {abort();}
}
"""


HELPER_FNS = {
    "assume_abort_if_not": ASSUME,
    "assume_abort_if_not0": ASSUME0,
}

class FunctionDeclarationVisitor(code_ast.ASTVisitor):
    def __init__(self):
        self.fns = []

    def visit_function_definition(self, node):
        return self.visit(node.child_by_field_name("declarator"))
    
    def visit_function_declarator(self, node):
        name_node = node.child_by_field_name("declarator")
        if name_node is None or name_node.type != "identifier": return False
        self.fns.append(name_node)
        return False


def _track_fn_declarations(code, ast = None):
    if ast is None:
        ast = code_ast.ast(code, lang = "c", syntax_error = "warn")
    visitor = FunctionDeclarationVisitor()
    ast.visit(visitor)

    return [ast.match(fn_node) for fn_node in visitor.fns]


def add_helper_functions(code, fns, ast = None):
    assert all(fn in HELPER_FNS for fn in fns), "Cannot create all functions: %s" % str(fns)

    all_declared_fns = set(_track_fn_declarations(code, ast = ast))
    
    code_prefix = []
    for fn in fns:
        if fn not in all_declared_fns:
            code_prefix.append(HELPER_FNS[fn])
    
    code_prefix.append(code)
    return "".join(code_prefix)

# Compound everything ----------------------------------------------------------------
def _name_node(function_node):
    declarator = function_node.child_by_field_name('declarator')
    if declarator is not None:
        name_node = declarator.child_by_field_name('declarator')
        if name_node is None or name_node.type != "identifier": return None
        return name_node
    return None

class CompoundCheckingVisitor(code_ast.ASTVisitor):
    def __init__(self, ast, target_fn = None):
        self.noncompounds = []
        self.ast = ast
        self.target_fn = target_fn

    def visit_function_definition(self, node):
        if self.target_fn is None: return True
        function_name_node = _name_node(node)
        if function_name_node is None: return True
        function_name = self.ast.match(function_name_node)
        if function_name != self.target_fn: 
            return False

    def _check_non_compound(self, node):
        if node.type != "compound_statement":
            self.noncompounds.append(node)

    def visit_if_statement(self, node):
        
        consequence = node.child_by_field_name("consequence")
        if consequence is not None:
            self._check_non_compound(consequence)
        
        alternative = node.child_by_field_name("alternative")
        if alternative is not None:
            if alternative.type == "else_clause":
                alternative = alternative.children[1]
            self._check_non_compound(alternative)

    def _handle_loop_node(self, node):
        body = node.child_by_field_name("body")
        if body is not None:
            self._check_non_compound(body)

    def visit_for_statement(self, node):
        self._handle_loop_node(node)

    def visit_while_statement(self, node):
        self._handle_loop_node(node)

    def visit_do_statement(self, node):
        self._handle_loop_node(node)


def _find_compound_violations(ast, target_fn = None):
    visitor = CompoundCheckingVisitor(ast, target_fn)
    ast.visit(visitor)
    return visitor.noncompounds

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

# Very hacky solution (please forgive me!)
def add_compounds(code, ast = None, target_fn = None):
    # Ensures that everything is in a compound
    if ast is None:
        ast = code_ast.ast(code, lang = "c", syntax_error = "warn")

    non_compounds = _find_compound_violations(ast, target_fn)
    if len(non_compounds) == 0: return code

    print("Found %d nodes that should be compounds" % len(non_compounds))

    # Remove overlapping noncompounds
    biggest_node = max(non_compounds, key = lambda x: x.end_point[0] - x.start_point[0])
    start, end   = biggest_node.start_point[0], biggest_node.end_point[0]
    change_nodes = [biggest_node]

    for node in non_compounds:
        if node.end_point[0] < start:
            change_nodes.append(node)
            start = node.start_point[0]
        if node.start_point[0] > end:
            change_nodes.append(node)
            end = node.end_point[0]

    targets  = [f"{{ {ast.match(n)} }}" for n in change_nodes]
    new_code = _replace_all(ast, change_nodes, targets)

    return add_compounds(new_code, target_fn=target_fn)