import code_ast as ca
from pycpa.graph import ControlFlowGraph

from pycpa.nodes import ProgramEntryNode, ProgramExitNode
from pycpa.nodes import FunctionEntryNode, FunctionExitNode

from pycpa.nodes import DeclarationNode

def _cfg(program_code):
    ast = ca.ast(program_code, lang = "c")
    root_node = ast.root_node()
    return ControlFlowGraph(root_node)


def _in_main(code_block):
    code = f"""
int main() {
{code_block}
}
    """
    return _cfg(code)

def _hist(start_node):
    histogram = {}

    seen = set()

    stack = [start_node]
    while len(stack) > 0:
        node = stack.pop(0)

        if node in seen: continue
        seen.add(node)

        histogram[node.type] = histogram.get(node.type, 0) + 1

        stack.extend(node.successors())
    
    return histogram


def _nodes(start_node):
    seen = set()

    stack = [start_node]
    while len(stack) > 0:
        node = stack.pop(0)

        if node in seen: continue
        seen.add(node)

        stack.extend(node.successors())
    
    return seen


# Program parsing -------------------------------------------------------------

def test_program_block_1():

    test = """

int main(void){
    return 0;
}

"""
    cfg = _cfg(test)
    entry_node = cfg.entry_node()

    assert isinstance(entry_node, ProgramEntryNode)
    assert len(entry_node.parent_block.inner_blocks()) == 1

    successors = entry_node.successors()

    assert len(successors) == 1
    assert isinstance(successors[0], FunctionEntryNode)

    assert len(entry_node.predecessors()) == 0


def test_program_block_2():

    test = """

int main(void){
    return 0;
}

"""
    cfg = _cfg(test)
    exit_node = cfg.exit_node()

    assert isinstance(exit_node, ProgramExitNode)
    assert len(exit_node.parent_block.inner_blocks()) == 1

    predecessors = exit_node.predecessors()

    assert len(predecessors) == 1
    assert isinstance(predecessors[0], FunctionExitNode)

    assert len(exit_node.successors()) == 0


def test_program_block_3():

    test = """

int a = 0;
int b = 1;

int main(void){
    return 0;
}

"""
    cfg = _cfg(test)
    entry_node = cfg.entry_node()
    
    assert len(entry_node.parent_block.inner_blocks()) == 1

    successors = entry_node.successors()
    assert len(successors) == 1
    assert isinstance(successors[0], DeclarationNode)

    predecessors = cfg.exit_node().predecessors()
    assert len(predecessors) == 1
    assert isinstance(predecessors[0], FunctionExitNode)

    histogram = _hist(entry_node)

    assert histogram["DeclarationNode"] == 2
    assert histogram["FunctionEntryNode"] == 1
    assert histogram["ReturnStatementNode"] == 1
    assert histogram["FunctionExitNode"] == 1
    assert histogram["ProgramExitNode"] == 1


def test_program_block_4():

    test = """

int a = 0;
int b = 1;

void test(int x){
    a += x;
}

int main(void){
    return 0;
}

"""
    cfg = _cfg(test)
    entry_node = cfg.entry_node()
    
    assert len(entry_node.parent_block.inner_blocks()) == 1

    successors = entry_node.successors()
    assert len(successors) == 1
    assert isinstance(successors[0], DeclarationNode)

    predecessors = cfg.exit_node().predecessors()
    assert len(predecessors) == 1
    assert isinstance(predecessors[0], FunctionExitNode)

    histogram = _hist(entry_node)

    assert histogram["DeclarationNode"] == 2
    assert histogram["FunctionEntryNode"] == 1
    assert histogram["ReturnStatementNode"] == 1
    assert histogram["FunctionExitNode"] == 1
    assert histogram["ProgramExitNode"] == 1



def test_program_block_5():

    test = """

void test1(){

}

int a = 0;
int b = 1;

void test2(int x){
    a += x;
}

int main(void){
    return 0;
}

"""
    cfg = _cfg(test)
    entry_node = cfg.entry_node()
    
    assert len(entry_node.parent_block.inner_blocks()) == 1

    successors = entry_node.successors()
    assert len(successors) == 1
    assert isinstance(successors[0], DeclarationNode)

    predecessors = cfg.exit_node().predecessors()
    assert len(predecessors) == 1
    assert isinstance(predecessors[0], FunctionExitNode)

    histogram = _hist(entry_node)

    assert histogram["DeclarationNode"] == 2
    assert histogram["FunctionEntryNode"] == 1
    assert histogram["ReturnStatementNode"] == 1
    assert histogram["FunctionExitNode"] == 1
    assert histogram["ProgramExitNode"] == 1


# Function blocks -------------------------------------------------------------


# Randomized jumps -------------------------------------------------------------

import random

def test_randomized_jump1():

    test = """

int main(){
    int a = 0;
    int b = 1;

    while(1){
        a = a + b;
        b = a;
    }

    l1: if(a == 0){
        goto l2;
    }

    if(0){
       l2: a++;
       goto l1;
    }
}

"""
    base_cfg = _cfg(test)
    jump_cfg = ControlFlowGraph(base_cfg.root_node)
    
    nodes = list(_nodes(base_cfg.entry_node()))

    for _ in range(100):
        random_node = random.choice(nodes)
        random_ast  = random_node.ast_node

        new_node = jump_cfg.attach(random_ast, init = True).entry_node()

        old_successors = random_node.successors()
        new_succesors  = new_node.successors()

        assert len(old_successors) == len(new_succesors)
        
        for s1, s2 in zip(old_successors, new_succesors):
            assert s1.type == s2.type

        old_predecessors = random_node.predecessors()
        new_predecessors  = new_node.predecessors()

        assert len(old_predecessors) == len(new_predecessors)

        for p1, p2 in zip(old_predecessors, new_predecessors):
            assert p1.type == p2.type