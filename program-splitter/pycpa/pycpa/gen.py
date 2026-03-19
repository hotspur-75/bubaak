from .nodes import BLOCK_REGISTRY

RELEVANT_NODES = set(BLOCK_REGISTRY.keys()) - {"comment"}

def slice_program(cfa_nodes, root = None, return_root = False):
    if len(cfa_nodes) == 0: return ""
    if root is None: 
        root = next(iter(cfa_nodes)).automata.root_cfa_node.ast_node

    ast_nodes = ast_nodes_from_cfa(cfa_nodes)
    root      = _find_enclosing_ast_node(root, ast_nodes)
    _include_parents(root, ast_nodes)
    _include_compounds(ast_nodes)

    ranges = _compute_ranges(root, ast_nodes)

    program_root = next(iter(cfa_nodes)).automata.root_cfa_node.ast_node
    program_slice =  _slice_program(program_root.text.decode("utf-8"), ranges)

    if return_root: return program_slice, root
    return program_slice


def _find_enclosing_ast_node(root, ast_nodes):
    
    seen       = set()
    candidates = sorted(ast_nodes, key = lambda x: x.start_point)

    while len(candidates) > 0:
        candidate = candidates.pop(0)
        if candidate in seen: continue

        discovered   = set()
        search_stack = [candidate]
        while len(search_stack) > 0:
            node = search_stack.pop()
            discovered.add(node)
            search_stack.extend(node.children)
        
        if len(ast_nodes - discovered) == 0: return candidate
        seen |= discovered

        parent = candidate.parent
        if parent is None or parent == root: return root

        candidates.append(candidate.parent)
    
    return root


def _include_parents(root, ast_nodes):
    
    for ast_node in list(ast_nodes):
        if ast_node == root: continue
        parent = ast_node.parent

        if parent == root: continue

        while parent and parent not in ast_nodes:
            ast_nodes.add(parent)
            parent = parent.parent
            if parent == root: break
    
    return ast_nodes


def _include_compounds(ast_nodes):
    for node in list(ast_nodes):
        for child in node.children:
            if child.type == "compound_statement":
                ast_nodes.add(child)


def _is_relevant_node(parent_node, child_node):
    if child_node.type in RELEVANT_NODES: return True

    if parent_node.type == "for_statement":
        if any(child_node == parent_node.child_by_field_name(key) for key in ["initializer", "condition", "update"]):
            return True

    return False



def _compute_ranges(root, ast_nodes):
    ranges = []
    
    start_location   = root.start_point
    current_location = start_location

    stack = [(root, 0)]

    while len(stack) > 0:
        node, position = stack.pop(-1)
        children      = node.children

        if position >= len(children): continue

        new_position = -1
        for i in range(position, len(children)):
            child = children[i]

            if _is_relevant_node(node, child):
                new_position = i; break
            else:
                current_location = child.end_point

        if new_position != -1:
            stack.append((node, new_position + 1))
            child = children[i]
            if child in ast_nodes:
                stack.append((child, 0))
            else:
                if current_location != start_location:
                    ranges.append((start_location, current_location))
                
                start_location   = child.end_point
                current_location = start_location

    if start_location != current_location:
        ranges.append((start_location, current_location))

    return ranges


def _slice_program(program, ranges):
    program_lines = program.splitlines(True)
    output = []

    for _range in ranges:
        lines    = program_lines[_range[0][0] : _range[1][0] + 1]
        if _range[0][0] == _range[1][0]:
            lines[0] = lines[0][_range[0][1]: _range[1][1]]
        else:
            lines[0]  = lines[0][_range[0][1]:]
            lines[-1] = lines[-1][:_range[1][1]]

        content = "".join(lines).rstrip()
        output.append(content)

    return "".join(output)


# Helper ----------------------------------------------------------------

def _handle_assumes(cfa_nodes, ast_nodes):

    for node in cfa_nodes:
        if node.cfg_node.type == "AssumeNode" and node.ast_node.type == "if_statement":
            children = list(node.ast_node.children)

            while len(children) > 0:
                child = children.pop(0)
                ast_nodes.add(child)
                children.extend(child.children)


def ast_nodes_from_cfa(cfa_nodes):
    ast_nodes = set(c.ast_node for c in cfa_nodes if c.ast_node is not None)
    for node in cfa_nodes:
        for edge in node.intra().successors():
            if edge.successor in cfa_nodes and edge.ast_node is not None:
                ast_nodes.add(edge.ast_node)

    _handle_assumes(cfa_nodes, ast_nodes)
    return ast_nodes
