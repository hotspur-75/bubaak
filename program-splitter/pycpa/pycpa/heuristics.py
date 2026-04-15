_STRUCTURAL_CACHE = {}

def is_structurally_trivial(cfa_node, threshold=3):
    # Check cache first
    if cfa_node in _STRUCTURAL_CACHE:
        return _STRUCTURAL_CACHE[cfa_node]
    
    # --- FIX 1: LOOP CONDITION IMMUNITY ---
    # Never bypass splits on loop evaluation headers. Splitting here is required to unroll loops!
    if "Loop" in cfa_node.__class__.__name__ or "For" in cfa_node.__class__.__name__:
        _STRUCTURAL_CACHE[cfa_node] = False
        return False
        
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
            
            # Note: We now keep DEADEND and EXIT strictly separate
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
            
            if depth < threshold:
                for edge in curr.successors():
                    if edge.successor not in visited:
                        visited.add(edge.successor)
                        queue.append((edge.successor, depth + 1))
        
        return float('inf'), "LONG"

    left_len, left_type = path_length_to_target(successors[0], successors[1])
    right_len, right_type = path_length_to_target(successors[1], successors[0])

    # --- FIX 2: STRICT ERROR-ONLY EARLY EXITS ---
    # 1. Early-Exit Rule: Only bypass if it hits a DEADEND (Error/Sanitization). 
    # Do NOT bypass normal EXITS (Returns), because splitting returns is highly beneficial.
    if (left_type == "DEADEND" and left_len <= threshold) or \
       (right_type == "DEADEND" and right_len <= threshold):
        is_trivial = True

    # 2. Bypass Rule: If it's a merge, ONLY mark trivial if BOTH paths are short (Symmetric).
    # This saves "Heavy/Light" Asymmetric splits like in `terminator` while catching simple `if/else` diamonds.
    elif left_type == "JOIN" and right_type == "JOIN":
        is_trivial = left_len <= threshold and right_len <= threshold
        
    else:
        is_trivial = False
    
    # Save to cache before returning
    _STRUCTURAL_CACHE[cfa_node] = is_trivial
    return is_trivial