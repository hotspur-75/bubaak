#!/usr/bin/env python3
from utils import init_local_libs
init_local_libs()
import os
import re
import random
# Silence Git Python Warning
os.environ["GIT_PYTHON_REFRESH"] = "quiet"

import tree_sitter
import tree_sitter.binding

import code_ast
from code_ast.visitor import ASTVisitor, ResumingVisitorComposition

import code_diff as cd

from pretransforms import support_extensions, add_helper_functions

from utils import main

def program_merger(
    left_split : str,
    right_split : str,
    output_file : str = "merged_program.c",
    max_line_limit: int = -1,
):
    if max_line_limit >= 0: 
        _check_line_limit(left_split, max_line_limit)
        _check_line_limit(right_split, max_line_limit)
    
    with open(left_split, 'r') as f:
        left_code = f.read()
    
    with open(right_split, 'r') as f:
        right_code = f.read()

    merged = merge_program(left_code, right_code)

    with open(output_file, 'w') as o:
        o.write(merged)

    print("Done.")


# Helper --------------------------------

def _check_line_limit(input_file, line_limit):
    with open(input_file, 'r') as lines:
        if sum(1 for _ in lines) >= line_limit: 
            raise ValueError('File %s contains more than %d lines of code. Splitting might be unperforming. Abort.' % (input_file, line_limit))

def _replace(program_ast, node, target):
    source_lines = list(program_ast.source_lines)

    start_line, end_line = node.start_point[0], node.end_point[0]
    prefix  = source_lines[start_line][:node.start_point[1]]
    postfix = source_lines[end_line][node.end_point[1]:] 

    source_lines[start_line:end_line+1] = [prefix + target + postfix]
    return "\n".join(source_lines)

# Merge code --------------------------------     

def _extract_difference(left_code, right_code):
    try:
        diff = cd.difference(left_code, right_code, lang = "c", syntax_error = "warn")
    except Exception as e:
        print(e)
        return left_code, None, None

    left_ast = diff.source_ast
    right_ast = diff.target_ast

    # Split left diff from base
    base_lines = left_code.splitlines(True)
    left_start, left_end = left_ast.position
    
    left_lines = base_lines[left_start[0]: left_end[0] + 1]

    prefix = left_lines[0][:left_start[1]]
    suffix = left_lines[-1][left_end[1]:]

    left_lines[0]  = left_lines[0][left_start[1]:]
    left_lines[-1] = left_lines[-1][:left_end[1]]

    base_lines[left_start[0]: left_end[0] + 1] = [prefix + "[MASK]" + suffix]

    # Compute right diff
    right_lines = right_code.splitlines(True)
    right_start, right_end = right_ast.position
    right_lines = right_lines[right_start[0]: right_end[0] + 1]
    right_lines[0]  = right_lines[0][right_start[1]:]
    right_lines[-1] = right_lines[-1][:right_end[1]]

    return "".join(base_lines), "".join(left_lines), "".join(right_lines)


def merge_program(left_code, right_code):
    # It is sufficient to focus on the diff
    base, left, right = _extract_difference(left_code, right_code)

    while left is not None and right is not None:
        base, left, right = _merge_differences(base, left, right)
        print(base)

        print("### LEFT ####")
        print(left)

        print("#### RIGHT ####")
        print(right)

    return base

def _merge_differences(base, left, right):
    insert, left, right = _merge_diff(left, right)
    base = base.replace("[MASK]", insert)
    return base, left, right

def _merge_diff(left, right):
    left_ast  = code_ast.ast(left, lang = "c", syntax_error = "ignore")
    right_ast = code_ast.ast(right, lang = "c", syntax_error = "ignore")

    left_root, right_root = left_ast.root_node(), right_ast.root_node()

    return _merge(left_ast, right_ast, left_root, right_root)

# Merge operations ----------------------------------------------------------------

def _is_equal(node_a, node_b, ignore_parentheses = False):
    if ignore_parentheses:
        if node_a.type == "parenthesized_expression":
            return _is_equal(node_a.children[1], node_b, True)
        if node_b.type == "parenthesized_expression":
            return _is_equal(node_a, node_b.children[1], True)

    if node_a.type != node_b.type: return False
    if len(node_a.children) != len(node_b.children): return False

    for a, b in zip(node_a.children, node_b.children):
        if not _is_equal(a, b): return False

    return True

def _default_merge(left_ast, right_ast, left_node, right_node):
    common_prefix, common_suffix = [], []

    left_children, right_children = left_node.children, right_node.children

    for start_ix in range(min(len(left_children), len(right_children))):
        left_child, right_child = left_children[start_ix], right_children[start_ix]
        if not _is_equal(left_child, right_child): break
        common_prefix.append(left_child)
    
    for end_ix in range(min(len(left_children), len(right_children))):
        left_child, right_child = left_children[-end_ix], right_children[-end_ix]
        if not _is_equal(left_child, right_child): break
        common_suffix.append(left_child)
    
    common_prefix = "\n".join([left_ast.match(a) for a in common_prefix])
    common_suffix = "\n".join([left_ast.match(a) for a in common_suffix])
    
    left_content = "\n".join([left_ast.match(a) for a in left_children[start_ix:-end_ix + 1]])
    right_content = "\n".join([right_ast.match(a) for a in right_children[start_ix:-end_ix + 1]])
 
    return common_prefix + "[MASK]" + common_suffix, left_content, right_content


def _merge_unit(left_ast, right_ast, left_node, right_node):
    return _merge(left_ast, right_ast, left_node.children[0], right_node.children[0])

MERGE_OPERATORS = {
    "translation_unit": _merge_unit,
}

def _merge(left_ast, right_ast, left_node, right_node):
    if left_node.type != right_node.type:
        return "[MASK]", left_ast.match(left_node), right_ast.match(right_node)
    
    merge_type = left_node.type
    merge_operator = MERGE_OPERATORS.get(merge_type, _default_merge)
    return merge_operator(left_ast, right_ast, left_node, right_node)

# Joint rewrite --------------------------------

def _joint_assume_rewrite(left, right):
    left_ast  = code_ast.ast(left, lang = "c", syntax_error = "ignore")
    right_ast = code_ast.ast(right, lang = "c", syntax_error = "ignore")

    conditions = _identify_interesting_assumes(left_ast, right_ast)
    if len(conditions) == 0: return left, right

    left_rew  = _rewrite(left_ast, conditions)
    right_rew = _rewrite(right_ast, conditions)

    return left_rew, right_rew 

def _rewrite(ast, conditions):
    pass

# Interesting assumes ------------------------
    
class AssumeVisitor(ASTVisitor):
    
    def __init__(self, ast):
        self.ast = ast
        self._assume_conds = []
    
    def visit_call_expression(self, node):
        name = node.child_by_field_name("function")
        name = self.ast.match(node)
        if not name.startswith("assume"): return False
        condition = node.child_by_field_name("arguments")
        if len(condition.children) != 3: return False
        condition = condition.children[1]
        self._assume_conds.append(condition)
        return False

def _identify_interesting_assumes(left, right):
    left_visit = AssumeVisitor(left)
    right_visit = AssumeVisitor(right)
    left.visit(left_visit)
    right.visit(right_visit)

    left_assumes, right_assumes = left_visit._assume_conds, right_visit._assume_conds

    right_normal  = [cond for cond in right_assumes if cond.type != "unary_expression"]
    right_negated = [cond.children[1] for cond in right_assumes if cond.type == "unary_expression"]

    interesting = []

    for assume in left_assumes:
        if assume.type == "unary_expression":
            exp = assume.children[1]
            if any(_is_equal(exp, right_exp, True) for right_exp in right_normal):
                interesting.append(exp)
        else:
            if any(_is_equal(assume, right_exp, True) for right_exp in right_negated):
                interesting.append(assume)

    return interesting

    

if __name__ == "__main__":
    main(program_merger, version = "0.1")