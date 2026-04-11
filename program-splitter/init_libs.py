#!/usr/bin/env python3
import os
import sys

lib_dir = os.path.join(os.path.dirname(__file__), "lib")
sys.path.insert(0, lib_dir)

if "PYTHONPATH" not in os.environ:
    os.environ["PYTHONPATH"] = ""
os.environ["PYTHONPATH"] = str(
    lib_dir
) + os.pathsep + os.environ["PYTHONPATH"]

# Init code_ast
import code_ast
code_ast.ast("int main(){}", lang = "c")