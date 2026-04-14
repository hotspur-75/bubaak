_STRUCTURAL_CACHE = {}

def is_structurally_trivial(cfa_node, threshold=3):
    # Check cache first
    if cfa_node in _STRUCTURAL_CACHE:
        return _STRUCTURAL_CACHE[cfa_node]
    
    edges = cfa_node.successors()
    if len(edges) < 2:
        return False
        
    successors = [edge.successor for edge in edges]
        
    def path_length_to_target(start_node, peer_start_node):
        peer_reachable = {peer_start_node}
        peer_queue = [(peer_start_node, 0)]
        while peer_queue:
            curr, depth = peer_queue.pop(0)
            if depth > threshold + 2: continue 
            for edge in curr.successors():
                if edge.successor not in peer_reachable:
                    peer_reachable.add(edge.successor)
                    peer_queue.append((edge.successor, depth + 1))

        queue = [(start_node, 0)]
        visited = {start_node}
        
        while queue:
            curr, depth = queue.pop(0)
            
            is_dead_end = len(curr.successors()) == 0 or "Error" in curr.__class__.__name__
            is_exit = "Exit" in curr.__class__.__name__
            is_join = curr in peer_reachable
            
            if is_dead_end: return depth, "DEADEND"
            if is_exit: return depth, "EXIT"
            if is_join:
                # Loop-Join Illusion Immunity
                c_cfg = getattr(curr, 'cfg_node', None)
                c_block = getattr(c_cfg, 'parent_block', None) if c_cfg else None
                if c_block:
                    b_name = c_block.__class__.__name__
                    if "Loop" in b_name or "For" in b_name or "While" in b_name:
                        return float('inf'), "LOOP"
                return depth, "JOIN"
        
        # (End of the while queue loop)
        return float('inf'), "LONG"

    left_len, left_type = path_length_to_target(successors[0], successors[1])
    right_len, right_type = path_length_to_target(successors[1], successors[0])

    # 1. Early-Exit Rule: If either path quickly terminates, it's trivial sanitization.
    if (left_type in ["DEADEND", "EXIT"] and left_len <= threshold) or \
       (right_type in ["DEADEND", "EXIT"] and right_len <= threshold):
        is_trivial = True

    # 2. Bypass Rule: If it's a merge, ONLY mark trivial if BOTH paths are short (Symmetric).
    # This saves "Heavy/Light" Asymmetric splits like in `terminator`.
    elif left_type == "JOIN" and right_type == "JOIN":
        is_trivial = left_len <= threshold and right_len <= threshold
        
    else:
        is_trivial = False
    
    # Save to cache before returning
    _STRUCTURAL_CACHE[cfa_node] = is_trivial
    return is_trivial