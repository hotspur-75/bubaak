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
            
            if is_dead_end or is_exit or is_join:
                return depth
                
            if depth >= threshold:
                continue
                
            for edge in curr.successors():
                if edge.successor not in visited:
                    visited.add(edge.successor)
                    queue.append((edge.successor, depth + 1))
        
        return float('inf')

    left_len = path_length_to_target(successors[0], successors[1])
    right_len = path_length_to_target(successors[1], successors[0])

    is_trivial = left_len <= threshold or right_len <= threshold
    
    # Save to cache before returning
    _STRUCTURAL_CACHE[cfa_node] = is_trivial
    return is_trivial