_STRUCTURAL_CACHE = {}

def is_structurally_trivial(cfa_node, threshold=3):
    # Check cache first
    if cfa_node in _STRUCTURAL_CACHE:
        return _STRUCTURAL_CACHE[cfa_node]

    edges = cfa_node.successors()
    if len(edges) < 2:
        return False
        
    # --- CONTEXT AWARENESS 1: Loop Shield ---
    def is_in_loop(node):
        if hasattr(node, 'loop_info'):
            try:
                if node.loop_info() is not None: return True
            except: pass
            
        c_cfg = getattr(node, 'cfg_node', None)
        block = getattr(c_cfg, 'parent_block', None) if c_cfg else None
        while block:
            name = block.__class__.__name__
            if "Loop" in name or "For" in name or "While" in name or "Do" in name:
                return True
            block = getattr(block, 'parent_block', None)
        return False

    in_loop_context = is_in_loop(cfa_node)
    successors = [edge.successor for edge in edges]
        
    def analyze_paths(start_node, peer_start_node):
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
        
        # --- CONTEXT AWARENESS 2: Semantic Scanning ---
        has_complex_math = False
        has_func_call = False
        
        while queue:
            curr, depth = queue.pop(0)
            
            is_dead_end = len(curr.successors()) == 0 or "Error" in curr.__class__.__name__
            is_exit = "Exit" in curr.__class__.__name__
            is_join = curr in peer_reachable
            
            if is_dead_end: return depth, "DEADEND", has_complex_math, has_func_call
            if is_exit: return depth, "EXIT", has_complex_math, has_func_call
            if is_join: return depth, "JOIN", has_complex_math, has_func_call
            
            if depth < threshold:
                for edge in curr.successors():
                    # Scan the edge for semantic complexity
                    edge_code = str(getattr(edge, 'statement', edge)).lower()
                    if any(op in edge_code for op in ['float', 'double', '/', '%', '<<', '>>']):
                        has_complex_math = True
                    if '(' in edge_code and ')' in edge_code and not any(kw in edge_code for kw in ['if', 'while', 'for', 'assert']):
                        has_func_call = True

                    if edge.successor not in visited:
                        visited.add(edge.successor)
                        queue.append((edge.successor, depth + 1))
        
        return float('inf'), "LONG", has_complex_math, has_func_call

    l_len, l_type, l_math, l_func = analyze_paths(successors[0], successors[1])
    r_len, r_type, r_math, r_func = analyze_paths(successors[1], successors[0])

    # --- THE SITUATIONAL DECISION MATRIX ---

    # 1. EARLY PRUNING: Isolate bug-states and fast-paths.
    if l_type in ["DEADEND", "EXIT"] or r_type in ["DEADEND", "EXIT"]:
        is_trivial = False

    # 2. SYMMETRIC DIAMONDS (Context Dependent)
    elif l_type == "JOIN" and r_type == "JOIN":
        if in_loop_context:
            # Inside a loop: Splitting causes 2^N explosion. BYPASS.
            is_trivial = (l_len <= threshold) and (r_len <= threshold)
        else:
            # Linear Code: Check the semantic context
            if l_math or r_math or l_func or r_func:
                # SMT Theory overload or Asymmetric Function -> FORCE SPLIT
                is_trivial = False
            else:
                # Pure simple data assignment (The 'ofuf' pattern) -> BYPASS
                is_trivial = (l_len <= threshold) and (r_len <= threshold)
            
    # 3. DEFAULT: Complex Branches
    else:
        is_trivial = False
    
    # Save to cache before returning
    _STRUCTURAL_CACHE[cfa_node] = is_trivial
    return is_trivial