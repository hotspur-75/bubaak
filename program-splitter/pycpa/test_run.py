# Directions for use -
# 1. Just place the C code you want to test as test.c in this directory.
# 2. Run `python test_run.py`

import os
import subprocess
from pycpa import cfg
from pycpa.cfa import ControlFlowAutomata
from pycpa.splitting import run_splitter
from pycpa.merge import run_merger

import time
from contextlib import contextmanager

@contextmanager
def time_phase(phase_name):
    print(f"\n--- {phase_name} ---")
    start_time = time.perf_counter()
    yield
    elapsed_time = time.perf_counter() - start_time
    print(f"[TIMER] {phase_name} completed in {elapsed_time:.4f} seconds.")

def export_cfa(cfa, name):
    """Helper function to export both DOT and PNG files"""
    dot_filename = f"{name}.dot"
    png_filename = f"{name}.png"
    
    with open(dot_filename, "w") as f:
        f.write(cfa.to_dot())
        
    try:
        subprocess.run(["dot", "-Tpng", dot_filename, "-o", png_filename], check=True)
        print(f" -> Generated visual: {png_filename}")
    except subprocess.CalledProcessError as e:
        print(f" -> Error generating graph for {name}: {e}")

def main():
    # File reading is I/O, kept outside the timer
    with open("test.c", "r") as f:
        c_code = f.read()

    # --- 1. INITIALIZATION PHASE ---
    with time_phase("1. Initialization (Parsing & CFA Generation)"):
        program_cfg = cfg(c_code)
        original_cfa = ControlFlowAutomata(program_cfg)
    
    # Graph export is I/O, kept outside the timer
    export_cfa(original_cfa, "0_original")

    # --- 2. SPLITTING PHASE ---
    with time_phase("2. Splitting (Dynamic Program Splitting)"):
        splits = run_splitter(original_cfa)
    
    print(f"Success! The algorithm divided the program into {len(splits)} splits.")
    
    # File writing and graph generation are I/O, kept outside the timer
    for i, split_cfa in enumerate(splits):
        export_cfa(split_cfa, f"1_split_{i}")
        with open(f"1_split_{i}.c", "w") as f:
            f.write(split_cfa.source_code())
            print(f" -> Generated code: 1_split_{i}.c")

    # --- 3. MERGING PHASE ---
    if len(splits) >= 2:
        with time_phase("3. Merging (Integrating Splits 0 and 1)"):
            merged_code = run_merger(splits[0], splits[1])
        
        # Re-parsing and exporting the merged code is kept outside the timer
        with open("2_merged.c", "w") as f:
            f.write(merged_code)
            print(" -> Generated code: 2_merged.c")
            
        merged_cfa = ControlFlowAutomata(cfg(merged_code))
        export_cfa(merged_cfa, "2_merged")
        
    print("\nPipeline complete! Check your directory for the generated files.")

if __name__ == "__main__":
    main()