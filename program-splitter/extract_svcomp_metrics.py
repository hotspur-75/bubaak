import os
import sys
import glob
import csv
import traceback
import multiprocessing as mp
from collections import deque
import re

# Increase recursion depth for massive SV-COMP array initializations
sys.setrecursionlimit(50000)

# Import the PyCPA entry point for CFA parsing
try:
    from pycpa.algorithm import _parse_cfa_from_code_object
except ImportError:
    print("Error: Could not import pycpa. Ensure the 'pycpa' folder is in your Python path.")
    sys.exit(1)

def extract_cfa_metrics(cfa):
    """
    The CFA metric extractor formulated in the previous step.
    (Included here so the worker threads can access it directly).
    """
    metrics = {}
    init_node = cfa.init_node()

    active_nodes, active_edges = set(), set()
    distances, parent_map = {init_node: 0}, {} 
    queue = deque([(init_node, 0)])
    
    while queue:
        curr, depth = queue.popleft()
        if curr in active_nodes: continue
        active_nodes.add(curr)
        
        for edge in curr.successors():
            active_edges.add(edge)
            succ = edge.successor
            
            if succ not in distances:
                distances[succ] = depth + 1
                parent_map[succ] = (curr, edge)
                queue.append((succ, depth + 1))
            else:
                if succ not in active_nodes:
                    queue.append((succ, distances[succ]))

    # Category 1: Topology
    M1 = len(active_nodes)
    M2 = len(active_edges)
    metrics['M01_active_node_count'] = M1
    metrics['M02_active_edge_count'] = M2
    metrics['M03_active_cyclomatic_complexity'] = M2 - M1 + 2
    metrics['M04_max_out_degree'] = max((len(n.successors()) for n in active_nodes), default=0)
    metrics['M05_active_graph_diameter'] = max(distances.values(), default=0)

# ---------------------------------------------------------
    # 2. Category 2: Reachability & Distance to Error
    # ---------------------------------------------------------
    error_nodes = [n for n in active_nodes if n.is_error_node()]
    if error_nodes:
        e_node = error_nodes[0]
        metrics['M06_bfs_shortest_path_to_error'] = distances[e_node]
        
        path_edges = []
        curr = e_node
        while curr in parent_map:
            curr, edge = parent_map[curr]
            path_edges.append(edge)
            
        conditional_edges = sum(1 for e in path_edges if e.is_assume())
        metrics['M07_error_path_conditional_density'] = conditional_edges / len(path_edges) if path_edges else 0.0
        
        in_scc = False
        if hasattr(cfa, 'loop_info') and cfa.loop_info is not None:
            try:
                loops = cfa.loop_info.find_loop(e_node)
                # If the returned list is not empty, it's inside a loop
                if loops: 
                    in_scc = True
            except (KeyError, AttributeError):
                pass
                
        metrics['M08_error_node_scc_intersection'] = int(in_scc)
    else:
        metrics['M06_bfs_shortest_path_to_error'] = -1
        metrics['M07_error_path_conditional_density'] = 0.0
        metrics['M08_error_node_scc_intersection'] = 0


    # ---------------------------------------------------------
    # 3. Category 3: CFA Loop Properties
    # ---------------------------------------------------------
    active_loops = set()
    if hasattr(cfa, 'loop_info') and cfa.loop_info is not None:
        for node in active_nodes:
            try:
                loops = cfa.loop_info.find_loop(node)
                # Update the set with the elements of the loops list
                if loops:
                    active_loops.update(loops)
            except (KeyError, AttributeError):
                continue
                
    metrics['M09_active_scc_count'] = len(active_loops)
    
    # Proxy for Max Nesting Depth
    max_depth = 0
    for l in active_loops:
        # Count how many other loops contain this loop
        depth = sum(1 for other in active_loops if other.is_contained(l))
        if depth > max_depth: max_depth = depth
    metrics['M10_max_scc_nesting_depth'] = max_depth

    # Proxy for Unbounded Back-Edge Density: 
    unbounded_loops = 0
    for l in active_loops:
        loop_edges = [e for e in active_edges if e.predecessor in l.nodes and e.successor in l.nodes]
        # Check if loop body contains an assignment
        if any(e.is_statement() and b'=' in (e.ast_node.text if e.ast_node else b'') for e in loop_edges):
            unbounded_loops += 1
            
    metrics['M11_unbounded_back_edge_density'] = unbounded_loops / len(active_loops) if active_loops else 0.0

    # ---------------------------------------------------------
    # 4. Category 4: Data-Flow & State Space Complexity
    # ---------------------------------------------------------
    # Restored M12
    assignment_edges = sum(1 for e in active_edges if e.is_statement() and b'=' in (e.ast_node.text if e.ast_node else b''))
    metrics['M12_active_assignment_edge_density'] = assignment_edges / M2 if M2 > 0 else 0.0
    
    # Restored M13
    unique_identifiers = set()
    for e in active_edges:
        if e.ast_node:
            words = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', e.ast_node.text.decode('utf-8', errors='ignore'))
            keywords = {'if', 'else', 'while', 'for', 'int', 'float', 'double', 'char', 'void', 'return', 'unsigned', 'break'}
            unique_identifiers.update([w for w in words if w not in keywords])
            
    metrics['M13_reaching_definitions_proxy'] = len(unique_identifiers) / M1 if M1 > 0 else 0.0
    
    # Bug-free M14 (No standalone asterisks)
    pointer_edges = sum(1 for e in active_edges if e.ast_node and bool(re.search(r'(&[a-zA-Z_]|->|\[.*\])', e.ast_node.text.decode('utf-8', errors='ignore'))))
    metrics['M14_pointer_alias_edge_frequency'] = pointer_edges


    # ---------------------------------------------------------
    # 5. Category 6: Textual Metrics on Non-Dead Code
    # ---------------------------------------------------------
    # Deduplicate using the raw byte-string text rather than memory ID!
    seen_text = set()
    active_code_fragments = []
    
    for e in active_edges:
        if e.ast_node:
            raw_text = e.ast_node.text
            if raw_text not in seen_text:
                seen_text.add(raw_text)
                active_code_fragments.append(raw_text.decode('utf-8', errors='ignore'))
    
    active_code_str = "\n".join(active_code_fragments)
    
    metrics['M16_LOC_active'] = len(active_code_fragments)
    metrics['M17_loop_count'] = len(re.findall(r'\b(for|while)\s*\(', active_code_str))
    metrics['M18_cyclomatic_complexity_text'] = 1 + len(re.findall(r'\b(if|while|for|case)\b', active_code_str))
    metrics['M19_float_ops'] = len(re.findall(r'\b(float|double)\b', active_code_str))
    metrics['M20_bitwise_ops'] = len(re.findall(r'(<<|>>|[^&]&[^&]|[^|]\|[^|]|\^)', active_code_str))
    metrics['M21_linear_counters'] = len(re.findall(r'(\+\+|--)', active_code_str))
    metrics['M22_pointer_derefs'] = len(re.findall(r'(->|\bmalloc\b|\bcalloc\b)', active_code_str))
    metrics['M23_unstructured_jumps'] = len(re.findall(r'\bgoto\b', active_code_str))

    return metrics


def resolve_svcomp_benchmarks(sv_benchmarks_dir):
    """
    Reads the .set files targeted by the XML configuration.
    Since SV-COMP .set files point to .yml metadata files, this smartly 
    swaps the extension to target the underlying .c and .i source files.
    """
    target_sets = [
        "ReachSafety-Arrays.set", "ReachSafety-BitVectors.set",
        "ReachSafety-ControlFlow.set", "ReachSafety-Floats.set",
        "ReachSafety-Heap.set", "ReachSafety-Loops.set",
        "ReachSafety-ProductLines.set", "ReachSafety-Recursive.set",
        "ReachSafety-Sequentialized.set", "ReachSafety-XCSP.set",
        "ReachSafety-Combinations.set"
    ]
    
    c_dir = os.path.join(sv_benchmarks_dir, "c")
    all_benchmark_files = set()

    if not os.path.exists(c_dir):
        print(f"Directory not found: {c_dir}. Please check your sv-benchmarks path.")
        return []

    for set_file in target_sets:
        set_path = os.path.join(c_dir, set_file)
        if not os.path.exists(set_path):
            print(f"Warning: {set_file} not found in {c_dir}. Skipping.")
            continue
            
        with open(set_path, 'r') as f:
            for line in f:
                line = line.strip()
                # Ignore empty lines and comments
                if not line or line.startswith('#'):
                    continue
                
                # SV-COMP sets point to .yml files (e.g., "array-examples/*.yml" or "folder/task.yml")
                # We swap .yml for .c and .i to find the actual source codes
                if line.endswith('.yml'):
                    pattern_c = os.path.join(c_dir, line.replace('.yml', '.c'))
                    pattern_i = os.path.join(c_dir, line.replace('.yml', '.i'))
                    
                    matched_files = glob.glob(pattern_c, recursive=True) + glob.glob(pattern_i, recursive=True)
                else:
                    # Fallback just in case a set explicitly lists C files or directories
                    pattern = os.path.join(c_dir, line)
                    matched = glob.glob(pattern, recursive=True)
                    matched_files = [m for m in matched if m.endswith(('.c', '.i'))]
                
                all_benchmark_files.update(matched_files)
                
    return list(all_benchmark_files)

import subprocess
import re

def preprocess_c_code(filepath):
    try:
        # 1. To prevent glibc expansion crashes on .c files, we MUST mock the headers.
        # However, for SV-COMP, it's safer to just let GCC expand local macros but ignore system headers.
        # We add '-nostdinc' so it doesn't pull in host glibc headers for .c files.
        result = subprocess.run(
            ['gcc', '-E', '-P', '-xc', '-nostdinc', filepath],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        code = result.stdout
        
    except subprocess.CalledProcessError:
        # Fallback to reading the raw file
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            code = f.read()
            
        # Strip includes so pycparser doesn't choke on them
        code = re.sub(r'^\s*#include\s*[<"].*?[>"]\s*$', '', code, flags=re.MULTILINE)
        
        # INJECT DUMMY TYPEDEFS (Removed the fatal _Bool collision)
        dummy_typedefs = """
        typedef int size_t;
        typedef int ssize_t;
        typedef int uint8_t;
        typedef int int8_t;
        typedef int uint16_t;
        typedef int int16_t;
        typedef int uint32_t;
        typedef int int32_t;
        typedef int uint64_t;
        typedef int int64_t;
        typedef int bool;
        typedef int pthread_t;
        typedef int pthread_mutex_t;
        typedef int pthread_cond_t;
        typedef int pthread_attr_t;
        """
        code = dummy_typedefs + "\n" + code

    # --- ROBUST REGEX SANITIZATION ---
    
    # 1. Strip attributes safely (handles up to 1 level of nested parentheses like aligned(4))
    code = re.sub(r'__attribute__\s*\(\([^()]*(\([^()]*\)[^()]*)*\)\)', '', code)
    
    # 2. Fix reach_error() by matching specifically to the known SV-COMP structure end to avoid leftover braces
    code = re.sub(
        r'void\s+reach_error\(\)\s*\{.*?__PRETTY_FUNCTION__.*?\)\);\s*\}', 
        'void reach_error() { abort(); }', 
        code, 
        flags=re.DOTALL
    )
    
    # 3. Strip remaining standard GNU extensions
    code = re.sub(r'\b__extension__\b', '', code)
    code = re.sub(r'\b__inline__\b', 'inline', code)
    code = re.sub(r'\b__inline\b', 'inline', code)
    code = re.sub(r'\b__const\b', 'const', code)
    code = re.sub(r'\b__restrict\b', 'restrict', code)
    code = re.sub(r'\b__restrict__\b', 'restrict', code)
    code = re.sub(r'\b__asm__\b.*?(\(.*?\))', '', code, flags=re.DOTALL)
    code = re.sub(r'\b__int128\b', 'long long', code)
    code = re.sub(r'\b__PRETTY_FUNCTION__\b', '""', code)
    
    return code

def process_single_benchmark(filepath):
    base_name = os.path.basename(filepath)
    result = {
        'benchmark_name': base_name,
        'filepath': filepath,
        'status': 'SUCCESS',
        'error_msg': ''
    }
    
    try:
        # 1. Use the GCC preprocessor!
        clean_code = preprocess_c_code(filepath)
            
        # 2. Parse into CFA
        cfa = _parse_cfa_from_code_object(clean_code)
        
        # 3. Extract Features
        metrics = extract_cfa_metrics(cfa)
        result.update(metrics)
        
    except Exception as e:
        result['status'] = 'FAILED'
        result['error_msg'] = str(e).replace('\n', ' ')[:200]
        
    return result

# Add this to the bottom of pycpa/extract_svcomp_metrics.py

def get_metrics_for_file(filepath):
    """
    End-to-end wrapper for Bubaak integration.
    Takes a filepath string, builds the CFA, and extracts the metrics.
    """
    try:
        # 1. Use the GCC Preprocessor / Dummy Injection we built earlier
        code_string = preprocess_c_code(filepath)
        
        # 2. Add your existing PyCPA parsing lines here!
        # (e.g., ast = parse(code_string) -> cfa = build_cfa(ast))
        # cfa = ... 
        cfa = _parse_cfa_from_code_object(code_string)
        
        # 3. Extract and return the ML metrics
        return extract_cfa_metrics(cfa)
        
    except Exception as e:
        print(f"Extraction failed for {filepath}: {e}")
        return None

if __name__ == "__main__":
    sv_dir = sys.argv[1] if len(sys.argv) > 1 else "sv-benchmarks"
    output_csv = "check.csv"

    print(f"Resolving benchmark targets from {sv_dir}...")
    benchmarks = resolve_svcomp_benchmarks(sv_dir)

    # benchmarks = ['test.c', 'l.c', 'r.c']
    
    if not benchmarks:
        print("No benchmarks found. Exiting.")
        sys.exit(1)
        
    print(f"Found {len(benchmarks)} benchmark files. Beginning extraction...")

    # Set up CSV Fieldnames
    metric_keys = [
        'M01_active_node_count', 'M02_active_edge_count', 'M03_active_cyclomatic_complexity',
        'M04_max_out_degree', 'M05_active_graph_diameter', 'M06_bfs_shortest_path_to_error',
        'M07_error_path_conditional_density', 'M08_error_node_scc_intersection',
        'M09_active_scc_count', 'M10_max_scc_nesting_depth', 'M11_unbounded_back_edge_density',
        'M12_active_assignment_edge_density', 'M13_reaching_definitions_proxy',
        'M14_pointer_alias_edge_frequency', 'M16_LOC_active', 'M17_loop_count',
        'M18_cyclomatic_complexity_text', 'M19_float_ops', 'M20_bitwise_ops',
        'M21_linear_counters', 'M22_pointer_derefs', 'M23_unstructured_jumps'
    ]
    fieldnames = ['benchmark_name', 'filepath', 'status', 'error_msg'] + metric_keys

    # Multiprocessing
    cores = max(1, mp.cpu_count() - 1)  # Leave one core free
    print(f"Using {cores} CPU cores for parallel extraction...")
    
    with open(output_csv, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        with mp.Pool(cores) as pool:
            # imap_unordered is faster when we just want to write results as they finish
            for i, result in enumerate(pool.imap_unordered(process_single_benchmark, benchmarks), 1):
                writer.writerow(result)
                if i % 100 == 0:
                    print(f"Processed {i}/{len(benchmarks)} files...")
                    
    print(f"Done! Telemetry dataset saved to {output_csv}")