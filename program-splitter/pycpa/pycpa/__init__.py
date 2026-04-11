import code_ast as ca

from .graph import ControlFlowGraph

def cfg(source_code_or_node, syntax_error = "raise"):
    
    root_node = None
    if isinstance(source_code_or_node, str):
        ast = ca.ast(source_code_or_node.strip(), lang = "c", syntax_error = syntax_error)
        root_node = ast.root_node()
    
    return ControlFlowGraph(root_node)