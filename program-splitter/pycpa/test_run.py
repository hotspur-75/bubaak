import os
import subprocess
from pycpa import cfg
from pycpa.cfa import ControlFlowAutomata
from pycpa.splitting import run_splitter
from pycpa.merge import run_merger

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
    # 1. Parse Original
    with open("test.c", "r") as f:
        c_code = f.read()

    print("--- 1. Initialization ---")
    print("Parsing C code and generating Original CFA...")
    program_cfg = cfg(c_code)
    original_cfa = ControlFlowAutomata(program_cfg)
    export_cfa(original_cfa, "0_original")

    # 2. Split
    print("\n--- 2. Splitting ---")
    print("Invoking Dynamic Program Splitting...")
    splits = run_splitter(original_cfa)
    
    print(f"Success! The algorithm divided the program into {len(splits)} splits.")
    for i, split_cfa in enumerate(splits):
        export_cfa(split_cfa, f"1_split_{i}")
        with open(f"1_split_{i}.c", "w") as f:
            f.write(split_cfa.source_code())
            print(f" -> Generated code: 1_split_{i}.c")

    # 3. Merge
    if len(splits) >= 2:
        print("\n--- 3. Merging ---")
        print("Invoking Program Merging on splits 0 and 1...")
        
        # run_merger returns the raw C code string!
        merged_code = run_merger(splits[0], splits[1])
        
        # Save the C code first
        with open("2_merged.c", "w") as f:
            f.write(merged_code)
            print(" -> Generated code: 2_merged.c")
            
        # Parse the new merged string back into a CFA to visualize it
        merged_cfa = ControlFlowAutomata(cfg(merged_code))
        export_cfa(merged_cfa, "2_merged")
        
        print("\nPipeline complete! Check your directory for the generated files.")

if __name__ == "__main__":
    main()