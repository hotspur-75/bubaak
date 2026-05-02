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
import pycpa.merge
from pycpa.merge import run_merger

import pycpa.env
from pycpa.env import global_timeout

from pretransforms import support_extensions
from utils import main

import re

def sanitize_for_merger(code):
    """
    Strips GNU C extensions and complex macros that crash the pycpa AST parser 
    during the merge phase.
    """
    if not isinstance(code, str):
        return code
        
    # 1. Strip attributes safely (handles nested parentheses)
    code = re.sub(r'__attribute__\s*\(\([^()]*(\([^()]*\)[^()]*)*\)\)', '', code)
    
    # 2. Fix reach_error() statement expression crashes
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

def program_merger(
        left_split : str,
        right_split : str,
        output_file : str = "merged_program.c",
        max_line_limit : int = -1,
        timeout : int = 60,
):
    if max_line_limit >= 0: 
        _check_line_limit(left_split, max_line_limit)
        _check_line_limit(right_split, max_line_limit)

    with open(left_split, "r") as f:
        left_source_code = f.read()

    with open(right_split, "r") as f:
        right_source_code = f.read()

    program = merge_programs(left_source_code, right_source_code, timeout = timeout)

    if program != "":
        with open(output_file, "w") as o:
            o.write(program)

    print("Done.")


def merge_programs(left_program, right_program, timeout = 60):
    """
    A function that merge given programs. For merging, we search
    for assumes with matching conditions (e.g. created by the splitter). Then,
    we compute the merge of the two programs.

    Args:
    --------
        left_program : str
        The source code written in GNU C. Currently, we might not support the complete C syntax.
        Not supported are switch statements and variadic functions. In addition, we do not support
        compiler annotations such as static, inline, extern, etc.

        right_program : str
        The source code of the right program written in GNU C

    Result:
    -------
        merged_program : str
        The merged result of the given programs

    """

    with global_timeout(timeout):
        try:
            left_program = sanitize_for_merger(left_program)
            right_program = sanitize_for_merger(right_program)
            
            return _merge_program_fn(left_program, right_program)
        except TimeoutError as e:
            raise ValueError(str(e))


# CPA Splitter --------------------------

def _merge_program_fn(left_program, right_program):
    try:
        return run_merger(left_program, right_program)
    except TimeoutError as e:
        raise e
    except Exception as e:
        raise ValueError(f"Exception: {str(e)}")

# Helper --------------------------------

def _check_line_limit(input_file, line_limit):
    with open(input_file, 'r') as lines:
        if sum(1 for _ in lines) >= line_limit: 
            raise ValueError('File %s contains more than %d lines of code. Splitting might be unperforming. Abort.' % (input_file, line_limit))



if __name__ == "__main__":
    main(program_merger, version = "0.1")