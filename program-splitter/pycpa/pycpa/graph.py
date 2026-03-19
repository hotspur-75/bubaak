from .nodes import create_block_by_type, BLOCK_REGISTRY, CompoundBlock, CompoundIterBlock
from .nodes import ForStatementBlock
from .scopes import RootScope

MAX_NESTING_DEPTH_FOR_JUMPS = 128


class ControlFlowGraph:

    def __init__(self, root_node):
        self.root_node = root_node
        self._node_cache = {}

        self.attach(root_node, scope = RootScope(self, root_node), init = True)

    def source_code(self):
        return self.root_node.text.decode('utf-8')

    def scope(self):
        return self.entry_node().scope

    def initialize_node(self, node, kwargs):
        assert _nesting_depth(node) <= MAX_NESTING_DEPTH_FOR_JUMPS, f"Node exceeds maximum nesting depth for initialization ({_nesting_depth(node)} > {MAX_NESTING_DEPTH_FOR_JUMPS})"

        parent_scope = kwargs.get("scope", self.attach(self.root_node).scope)
        root_node    = parent_scope.root_ast_node

        direct_parent_node = None

        current_node = node.parent
        while current_node is not None and current_node is not root_node:
            if current_node.type in BLOCK_REGISTRY:
                direct_parent_node = self.attach(
                    current_node, scope = parent_scope, init = True
                )
                break
            else:
                current_node = current_node.parent

        if direct_parent_node is None: return parent_scope, None

        direct_parent_node = _handle_parent(direct_parent_node, node)

        return direct_parent_node.scope, direct_parent_node

        
    def attach(self, ast_node, init = False, **kwargs):

        try:
            return self._node_cache[ast_node]
        except KeyError:
            if init:
                kwargs['scope'], kwargs['parent'] = self.initialize_node(ast_node, kwargs)

            node = create_block_by_type(self, ast_node, **kwargs)
            self._node_cache[ast_node] = node
            return node

    def entry_node(self):
        return self.attach(self.root_node).entry_node()

    def exit_node(self):
        return self.attach(self.root_node).exit_node()
    

# Helper ------------------------------------------------

def _handle_parent(parent, node):

    ops = [_handle_compounds, _handle_for_statement]

    changed = True
    while changed:
        changed = False
        
        for op in ops:
            new_parent = op(parent, node)
            if new_parent != parent:
                parent = new_parent
                changed = True
                break
    
    return parent


def _handle_for_statement(parent, node):
    if not isinstance(parent, ForStatementBlock): return parent
    return parent._loop_node().parent_block

def _handle_compounds(parent, node):
    if not isinstance(parent, CompoundBlock): return parent

    node_idx = -1
    root_node = parent.ast_node
    for i, child in enumerate(root_node.children):
        if child == node: node_idx = i; break
    
    assert node_idx != -1

    return CompoundIterBlock(
        parent.graph, parent.ast_node, node_idx, scope = parent.scope, parent = parent.parent
    )

# Nesting depth ----------------------------------------------------------------

def _nesting_depth(ast_node):
    if ast_node is None: return 0
    count = 0
    while ast_node.parent:
        count += 1
        ast_node = ast_node.parent
    return count