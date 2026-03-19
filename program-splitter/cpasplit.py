#!/usr/bin/env python3
from utils import init_local_libs
init_local_libs()
import os
import re
import random
import collections
# Silence Git Python Warning
os.environ["GIT_PYTHON_REFRESH"] = "quiet"

import tree_sitter
import tree_sitter.binding

import pycpa
import pycpa.splitting
from pycpa.splitting import run_splitter, run_deepening_splitter

import pycpa.env
from pycpa.env import global_timeout

from pretransforms import support_extensions
from utils import main


def program_splitter(
        input_file : str,
        left_split : str = None,
        right_split : str = None,
        allowed_unrolls : int = -1,
        allowed_function_clones : int = -1,
        max_line_limit : int = -1,
        deepening : bool = False,
        timeout   : int = 60,
):
    if max_line_limit >= 0: _check_line_limit(input_file, max_line_limit)

    file_name = os.path.basename(input_file)

    if left_split is None:
        name, ext  = os.path.splitext(file_name)
        left_split = f"{name}.left{ext}"
    
    if right_split is None:
        name, ext = os.path.splitext(file_name)
        right_split = f"{name}.right{ext}"

    with open(input_file, "r") as f:
        source_code = f.read()

    left, right = split_program(source_code, 
                                allowed_unrolls = allowed_unrolls, 
                                allowed_function_clones = allowed_function_clones,
                                deepening = deepening,
                                timeout = timeout)

    if left_split != "":
        with open(left_split, "w") as o:
            o.write(left)
    
    if right_split != "":
        with open(right_split, "w") as o:
            o.write(right)
    
    print("Done.")


def split_program(source_code : str, allowed_unrolls : int = -1, allowed_function_clones : int = -1, deepening : bool = False, timeout = 60):
    """
    A function that splits a given source code into two parts. For splitting, we split if branches
    in an then part and else part. The first program contains only the then part and the second program
    only the else part. If we can verify both the then-program and the else-program, the original program
    should be correct.

    Args:
    --------
        source_code : str
        The source code written in GNU C. Currently, we might not support the complete C syntax.
        Not supported are switch statements and variadic functions. In addition, we do not support
        compiler annotations such as static, inline, extern, etc.

        allowed_unrolls: int = -1
        Restricts how often loops are unrolled during the splitting process. (Default: -1 = infinity)

        allowed_function_clones : int = -1
        Restricts how often functions can be cloned during the splitting process. (Default: -1 = infinity)

        deepening : bool = False
        Iteratively allow unrolling and function clones (i.e. split branches outside of loops first)


    Result:
    -------
        left, right
        The source code of the then-program (left) and the else-program (right)

    """
    
    with global_timeout(timeout):
        try:
            return support_extensions(source_code, _split_program_fn, 
                                    unrolls = allowed_unrolls, 
                                    clones = allowed_function_clones, 
                                    deepening = deepening)
        except TimeoutError as e:
            raise ValueError(str(e))


# CPA Splitter --------------------------

def _split_program_fn(source_code, unrolls = -1, clones = -1, deepening = False, trials = 10):
    
    splits = [source_code]
    for _ in range(trials):
        try:
            if deepening:
                splits = run_deepening_splitter(source_code, loop_bound = unrolls, clone_bound = clones)
            else:
                splits = run_splitter(source_code, loop_bound = unrolls, clone_bound = clones)
        except TimeoutError as e:
            raise e
        except Exception as e:
            raise ValueError(f"Exception: {str(e)}")
        
        splits = [sp.root_ast_node.text.decode("utf-8") for sp in splits]
        if len(splits) == 2: break
        if len(splits) == 0: raise ValueError("Strange error appeared during splitting")

        program_split = splits[0]
        if source_code == program_split: raise ValueError("No progress made by splitting")

        source_code = program_split
    
    return splits


# Helper --------------------------------

def _check_line_limit(input_file, line_limit):
    with open(input_file, 'r') as lines:
        if sum(1 for _ in lines) >= line_limit: 
            raise ValueError('File %s contains more than %d lines of code. Splitting might be unperforming. Abort.' % (input_file, line_limit))



if __name__ == "__main__":
    main(program_splitter, version = "0.1")