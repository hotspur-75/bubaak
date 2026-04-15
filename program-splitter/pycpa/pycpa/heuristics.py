_STRUCTURAL_CACHE = {}

def is_structurally_trivial(cfa_node, threshold=3):
    if cfa_node in _STRUCTURAL_CACHE:
        return _STRUCTURAL_CACHE[cfa_node]

    edges = cfa_node.successors()
    if len(edges) < 2:
        return False

    def path_length_to_target(start_node, peer_start_node):
        # THE IMPENETRABLE WALL: cfa_node is added to visited immediately.
        # This prevents loops from being mathematically mistaken for diamonds.
        peer_reachable = {peer_start_node}
        peer_queue = [(peer_start_node, 0)]
        peer_visited = {peer_start_node, cfa_node} 

        while peer_queue:
            curr, depth = peer_queue.pop(0)
            if depth > threshold + 2: continue 
            for edge in curr.successors():
                if edge.successor not in peer_visited:
                    peer_visited.add(edge.successor)
                    peer_reachable.add(edge.successor)
                    peer_queue.append((edge.successor, depth + 1))

        queue = [(start_node, 0)]
        visited = {start_node, cfa_node} 
        
        while queue:
            curr, depth = queue.pop(0)
            
            is_dead_end = len(curr.successors()) == 0 or "Error" in curr.__class__.__name__
            is_exit = "Exit" in curr.__class__.__name__
            is_join = curr in peer_reachable
            
            if is_dead_end: return depth, "DEADEND"
            if is_exit: return depth, "EXIT"
            if is_join: return depth, "JOIN"
            
            if depth < threshold:
                for edge in curr.successors():
                    if edge.successor not in visited:
                        visited.add(edge.successor)
                        queue.append((edge.successor, depth + 1))
        
        return float('inf'), "LONG"

    successors = [edge.successor for edge in edges]
    left_len, left_type = path_length_to_target(successors[0], successors[1])
    right_len, right_type = path_length_to_target(successors[1], successors[0])

    # 1. ERROR & EXIT ATTRACTION (Highest Priority - Solves Floats/Traps)
    if left_type in ["DEADEND", "EXIT"] or right_type in ["DEADEND", "EXIT"]:
        is_trivial = False

    # 2. TRUE SYMMETRIC DIAMONDS (Solves Combinatorial Arrays & FSMs)
    elif left_type == "JOIN" and right_type == "JOIN":
        is_trivial = (left_len <= threshold) and (right_len <= threshold)

    # 3. LOOPS & ASYMMETRIC COMPLEXITY (Solves gauss_sum & combinations)
    else:
        is_trivial = False
    
    _STRUCTURAL_CACHE[cfa_node] = is_trivial
    return is_trivial