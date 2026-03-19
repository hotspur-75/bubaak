from .utils import cached

from .scopes import ProgramScope, FunctionScope, LoopScope, CompoundScope, SwitchScope
from .optimizers import is_trivial_true, is_trivial_false

SILENT_COMMENTS = True
ERROR_FN = "reach_error"

# ----------------------------------------------------------------

class Node:

    def __init__(self, parent_block) -> None:
        self.parent_block = parent_block

    # Helper -------------------------------------

    def _grandparent_block(self):
        return self.parent_block.parent

    def _backtrack_successors(self):
        grandparent = self._grandparent_block()
        if grandparent is not None:
            return [
                grandparent.exit_node()
            ]

        return []
    
    def _backtrack_predecessors(self):
        grandparent = self._grandparent_block()
        if grandparent is not None:
            return [
                grandparent.entry_node()
            ]

        return []

    # API methods  --------------------------------

    def is_silent(self): return False

    @cached
    def successors(self):
        successors = self._successors()
        if len(successors) == 0: 
            successors = self._backtrack_successors()
        
        result = []
        for successor in successors:
            if successor.is_silent(): 
                result.extend(successor.successors()) 
            else:
                result.append(successor)
        
        return result
        

    @cached
    def predecessors(self):
        predecessors = self._predecessors()
        if len(predecessors)== 0: 
            predecessors = self._backtrack_predecessors()
        
        result = []
        for predecessor in predecessors:
            if predecessor.is_silent(): 
                result.extend(predecessor.predecessors()) 
            else:
                result.append(predecessor)
        
        return result
    
    @property
    def type(self):
        return self.__class__.__name__
    
    def is_entry_node(self): return False
    def is_exit_node(self) : return False
    
    def __getattr__(self, name):
        return getattr(self.parent_block, name)
    
    def __repr__(self) -> str:
        if self.ast_node is None:
            return f"{self.type}()"

        return f"{self.type}({self.ast_node.text.decode('utf-8')[:10]})"
    
    def __hash__(self):
        return hash((self.type, self.parent_block))
    
    def __eq__(self, other):
        return self.__class__ == other.__class__ and self.parent_block == other.parent_block

    # Abstract methods --------------------------------

    def _successors(self):
        raise NotImplementedError()

    def _predecessors(self):
        raise NotImplementedError()


class EntryNode(Node):
    def is_entry_node(self): return True

    def _successors(self):
        return [block.entry_node() for block in self.inner_blocks()]
    
    def _predecessors(self):
        return []


class ExitNode(Node):
    def is_exit_node(self): return True

    def _successors(self):
        return []
    
    def _predecessors(self):
        return [block.exit_node() for block in self.inner_blocks()]


class SimpleNode(Node):

    def _successors(self):
        return []
    
    def _predecessors(self):
        return []

# Blocks ----------------------------------------------------------------

class BasicBlock:

    def __init__(self, graph, ast_node, scope = None, parent = None):
        self.graph = graph
        self.ast_node = ast_node
        self.scope = self.new_scope(scope)
        self.parent = parent

    def new_scope(self, parent_scope):
        return parent_scope
    
    def inner_blocks(self):
        raise []
    
    def type(self):
        return self.__class__.__name__
    
    def entry_node(self):
        raise NotImplementedError()
    
    def exit_node(self):
        return self.entry_node()

    def __hash__(self):
        return hash((self.type(), self.ast_node))
    
    def __eq__(self, other):
        return self.__class__ == other.__class__ and self.ast_node == other.ast_node

    # Abstract methods -----
    

class EmptyBlock(BasicBlock):
    def __init__(self, graph, scope = None, parent = None):
        super().__init__(graph, None, scope, parent)

    def entry_node(self):
        return EmptyNode(self)


class EmptyNode(Node):
    def is_silent(self): return True
    def _successors(self): return []
    def _predecessors(self): return []


class SequenceBlocks(BasicBlock):

    def __init__(self, *block_sequence):
       first = block_sequence[0]
       super().__init__(first.graph, None, first.scope, first.parent)
       self.block_sequence = block_sequence

       
    def entry_node(self):
        return SequenceIterBlock(self, 0).entry_node()
    
    def exit_node(self):
        return SequenceIterBlock(self, -1).exit_node()


class SequenceIterBlock(BasicBlock):
    
    def __init__(self, sequence_block, idx):
        super().__init__(sequence_block.graph, None, sequence_block.scope, sequence_block.parent)
        self.sequence_block = sequence_block
        self.idx = idx if idx >= 0 else len(sequence_block.block_sequence) + idx

        if self.idx < len(self.sequence_block.block_sequence):
            self.sequence_block.block_sequence[self.idx].parent = self


    def entry_node(self):
        return SequenceIterEntryNode(self)
    
    def exit_node(self):
        return SequenceIterExitNode(self)
    

class SequenceIterEntryNode(Node):

    def is_silent(self): return True

    def _successors(self):
        if self.idx >= len(self.sequence_block.block_sequence): return []
        return [self.sequence_block.block_sequence[self.idx].entry_node()]

    def _predecessors(self):
        if self.idx < 0: return []
        return [
            SequenceIterBlock(self.sequence_block, self.idx - 1).exit_node()
        ]
    
    def __repr__(self) -> str:
        if self.ast_node is None:
            return f"{self.type}()"

        return f"{self.type}({self.idx}, {self.ast_node.text.decode('utf-8')[:10]})"



class SequenceIterExitNode(Node):

    def is_silent(self): return True

    def _successors(self):
        if self.idx >= len(self.sequence_block.block_sequence): return []
        return [
            SequenceIterBlock(self.sequence_block, self.idx + 1).entry_node()
        ]

    def _predecessors(self):
        if self.idx < 0: return []
        return [self.sequence_block.block_sequence[self.idx].exit_node()]

    def __repr__(self) -> str:
        if self.ast_node is None:
            return f"{self.type}()"

        return f"{self.type}({self.idx}, {self.ast_node.text.decode('utf-8')[:10]})"



    
# Program ----------------------------------------------------------------

class ProgramBlock(BasicBlock):


    def new_scope(self, parent_scope):
        return ProgramScope(self.graph, self.ast_node, parent = parent_scope)

    @cached
    def inner_blocks(self):
        program_scope = self.scope

        global_declarations = program_scope.global_declarations()
        main_function = program_scope.main_function()
        main_function.parent = self

        assert main_function is not None, "Expect the implementation to have a main function!"

        if len(global_declarations) == 0:
            return [main_function]
        
        return [
            SequenceBlocks(
                *(list(global_declarations.values()) + [main_function])
            )
        ]


    @cached
    def entry_node(self):
        return ProgramEntryNode(self)
    
    @cached
    def exit_node(self):
        return ProgramExitNode(self)


class ProgramEntryNode(EntryNode):
    pass

class ProgramExitNode(ExitNode):
    
    def _predecessors(self):
        predecessors = super()._predecessors()
        predecessors += exit_nodes(*self.scope.program_aborts())
        return predecessors 


# Functions --------------------------------------------------------------


class FunctionBlock(BasicBlock):

    def new_scope(self, parent_scope):
        return FunctionScope(self.graph, self.ast_node, parent = parent_scope)
    

    def function_name(self):
        ast_node = self.ast_node
        declarator = ast_node.child_by_field_name('declarator')

        while declarator and declarator.type != "function_declarator":
            declarator = declarator.child_by_field_name("declarator")

        assert declarator is not None
        declarator_name = declarator.child_by_field_name('declarator')

        if declarator_name.type == "parenthesized_declarator":
            declarator_name = declarator_name.children[1]

        assert declarator_name is not None and declarator_name.type == "identifier"

        return declarator_name.text.decode('utf-8')
    
    def return_type(self):
        return self.ast_node.child_by_field_name('type')

    @cached
    def call_sides(self):
        function_name = self.function_name()
        return self.scope.function_calls().get(function_name, [])


    @cached
    def inner_blocks(self):
        function_scope = self.scope

        function_body = self.ast_node.child_by_field_name("body")
        return [
            self.graph.attach(function_body, scope = function_scope, parent = self)
        ]
    

    @cached
    def entry_node(self):
        return FunctionEntryNode(self)
    
    @cached
    def exit_node(self):
        return FunctionExitNode(self)


class FunctionEntryNode(EntryNode):
    pass


class FunctionExitNode(ExitNode):
    
    def _predecessors(self):
        predecessors = super()._predecessors()
        predecessors += exit_nodes(*self.scope.returns())
        return predecessors
    
    def _successors(self):
        return [c.exit_node() for c in self.call_sides() if isinstance(c, FunctionCallBlock)]

class ReturnStatementBlock(BasicBlock):

    @cached
    def entry_node(self):
        return ReturnStatementNode(self)
    
class ReturnStatementNode(SimpleNode):
    def _successors(self):
        func_block = self.parent_block

        while func_block and not isinstance(func_block, FunctionBlock):
            func_block = func_block.parent
        
        assert func_block, "Return statement can only be inside of function definitions"
        return [func_block.exit_node()]

# Compound block --------------------------------------------------------------

class CompoundBlock(BasicBlock):

    def new_scope(self, parent_scope):
        return CompoundScope(self.graph, self.ast_node, parent = parent_scope)
    
    def inner_blocks(self):
        return [
            CompoundIterBlock(self.graph, self.ast_node, i, scope = self.scope, parent = self.parent)
            for i in range(1, self.ast_node.child_count - 1)
        ]

    def entry_node(self):
        return CompoundIterBlock(
            self.graph, self.ast_node, 1, scope = self.scope, parent = self.parent
        ).entry_node()
    

    def exit_node(self):
        return CompoundIterBlock(
            self.graph, self.ast_node, -2, scope = self.scope, parent = self.parent
        ).exit_node()


class CompoundIterBlock(BasicBlock):
    
    def __init__(self, graph, ast_node, child_idx, scope = None, parent = None):
        super().__init__(graph, ast_node, scope = scope, parent = parent)
        self.num_children = self.ast_node.child_count
        self.child_idx = self.num_children + child_idx if child_idx < 0 else child_idx


    def inner_blocks(self):
        if self.num_children <= 2: return []
        if self.child_idx >= self.num_children - 1: return []
        current_child = self.ast_node.children[self.child_idx]

        return [
            self.graph.attach(current_child, scope = self.scope, parent = self)
        ]

    def entry_node(self):
        return CompoundIterEntryNode(self)
    
    def exit_node(self):
        return CompoundIterExitNode(self)
    
    def next_block(self):
        return CompoundIterBlock(
            self.graph, self.ast_node, self.child_idx + 1, scope = self.scope, parent = self.parent
        )

    def prev_block(self):
        return CompoundIterBlock(
            self.graph, self.ast_node, self.child_idx - 1, scope = self.scope, parent = self.parent
        )


class CompoundIterEntryNode(EntryNode):

    def is_silent(self): return True
    
    def _predecessors(self):
        if self.num_children <= 2: return []
        if self.child_idx <= 1: return []

        return [
            self.prev_block().exit_node()
        ]
    
    def __repr__(self) -> str:
        if self.ast_node is None:
            return f"{self.type}()"

        return f"{self.type}({self.child_idx}, {self.ast_node.text.decode('utf-8')[:10]})"


class CompoundIterExitNode(ExitNode):

    def is_silent(self): return True
    
    def _successors(self):
        if self.num_children <= 2: return []
        if self.child_idx >= self.num_children - 1: return []

        return [
            self.next_block().entry_node()
        ]
    
    def __repr__(self) -> str:
        if self.ast_node is None:
            return f"{self.type}()"

        return f"{self.type}({self.child_idx}, {self.ast_node.text.decode('utf-8')[:10]})"


    
# Declaration ------------------------------------------------

class DeclarationBlock(BasicBlock):

    @cached
    def entry_node(self):
        return DeclarationNode(self)

class DeclarationNode(SimpleNode):
    pass

# Expressions ----------------------------------------------------------------

class ExpressionBlock(BasicBlock):

    @cached
    def entry_node(self):
        return ExpressionNode(self)

class ExpressionNode(SimpleNode):
    pass


class FunctionCallBlock(BasicBlock):

    def called_function(self):
        if self.ast_node.type == "declaration":
            call_expression = self.ast_node.children[1].children[-1]
        else:
            call_expression = self.ast_node.children[0]
            if call_expression.type == "assignment_expression":
                call_expression = call_expression.children[-1]
        
        function_node = call_expression.child_by_field_name("function")
        assert function_node.type == "identifier", "Cannot handle complex function call patterns"

        function_name = function_node.text.decode('utf-8')
        function_definitions = self.scope.function_definitions()
        if function_name not in function_definitions:
            if function_name not in self.scope.external_functions():
                print(f"Function {function_name} is not defined. Assume pure function.")
            return None
        return function_definitions[function_name]

    @cached
    def entry_node(self):
        return FunctionCallInitNode(self)

    @cached
    def exit_node(self):
        return FunctionCallExitNode(self)


class FunctionCallInitNode(SimpleNode):
    def _successors(self):
        called_function = self.called_function()
        if called_function is None: 
            return [self.exit_node()]
        return [called_function.entry_node()]


class FunctionCallExitNode(SimpleNode):
    pass

# Branches ----------------------------------------------------------------

class IfStatementBlock(BasicBlock):
    
    @cached
    def inner_blocks(self):
        condition   = self.ast_node.child_by_field_name("condition")
        consequence = self.ast_node.child_by_field_name("consequence")
        alternative = self.ast_node.child_by_field_name("alternative")

        blocks = []
        if not is_trivial_false(condition):
            blocks.append(
                self.graph.attach(consequence, scope = self.scope, parent = self)
            )
        
        if not is_trivial_true(condition):
            if alternative: alternative = alternative.children[1]

            if alternative is None:
                blocks.append(EmptyBlock(self.graph, scope = self.scope, parent = self))
            else:
                blocks.append(
                    self.graph.attach(alternative, scope = self.scope, parent = self)
                )
    
        return blocks

    @cached
    def entry_node(self):
        return IfBranchNode(self)
    
    @cached
    def exit_node(self):
        return IfJoinNode(self)


class IfBranchNode(EntryNode):
    pass

class IfJoinNode(ExitNode):
    def is_silent(self): return True

# Switch ----------------------------------------------------------------

class SwitchStatementBlock(BasicBlock):

    def new_scope(self, parent_scope):
        return SwitchScope(self.graph, self.ast_node, parent = parent_scope)

    @cached
    def inner_blocks(self):
        compound = self.graph.attach(self.ast_node.children[-1], scope = self.scope, parent = self)
        return compound.inner_blocks()

    @cached
    def entry_node(self):
        return SwitchEntryNode(self)
    
    @cached
    def exit_node(self):
        return SwitchExitNode(self)
    

class SwitchEntryNode(EntryNode):
    pass


class SwitchExitNode(ExitNode):
    def is_silent(self): return True
    def _predecessors(self):
        switch_exits = exit_nodes(self.inner_blocks()[-1])
        switch_exits += exit_nodes(*self.scope.break_statements())
        return switch_exits
    

class CaseStatementBlock(BasicBlock):
    def inner_blocks(self):
        if self.ast_node.children[0].type == "default":
            exec_sequence = self.ast_node.children[2:]
        else:
            exec_sequence = self.ast_node.children[3:]

        if len(exec_sequence) == 0: return []

        inner_blocks =  [
            SequenceBlocks(
                *[self.graph.attach(node, scope = self.scope, parent = self)
                    for node in exec_sequence]
            )
        ]
        return inner_blocks

    @cached
    def entry_node(self):
        return CaseStatementEntryNode(self)
    
    @cached
    def exit_node(self):
        return CaseStatementExitNode(self)


class CaseStatementEntryNode(EntryNode):
    pass   

class CaseStatementExitNode(ExitNode):
    def is_silent(self): return True    


# Loops -------------------------------------------------------------------------

class LoopBlock(BasicBlock):

    def new_scope(self, parent_scope):
        return LoopScope(self.graph, self.ast_node, parent = parent_scope)

    @cached
    def inner_blocks(self):
        loop_scope = self.scope
        loop_body = self.ast_node.child_by_field_name("body")
        return [
            self.graph.attach(loop_body, scope = loop_scope, parent = self)
        ]
    
    def _loop_node(self):
        return self.entry_node()
    
    @cached
    def _loop_exit_node(self):
        return LoopExitNode(self)
    

class LoopNode(Node):

    def _predecessors(self):
        predecessors = super()._backtrack_predecessors()
        predecessors += exit_nodes(*[block.exit_node() for block in self.inner_blocks()])
        predecessors += exit_nodes(*self.scope.continue_statements())
        return predecessors
    

class LoopExitNode(ExitNode):

    def is_silent(self): return True

    def _predecessors(self):
        loop_entry = self._loop_node()
        loop_exits = exit_nodes(*self.inner_blocks())
        loop_exits += exit_nodes(*self.scope.break_statements())
        return [loop_entry] + loop_exits
    

class BreakStatementBlock(BasicBlock):
    @cached
    def entry_node(self):
        return BreakStatementNode(self)


class BreakStatementNode(SimpleNode):
    
    def _successors(self):
        loop_switch_block = self.parent_block

        while loop_switch_block:
            if isinstance(loop_switch_block, LoopBlock): break
            if isinstance(loop_switch_block, SwitchStatementBlock): break
            loop_switch_block = loop_switch_block.parent
        
        assert loop_switch_block, "Break statement can only be inside of loop or switch statements"
        
        if isinstance(loop_switch_block, LoopBlock):
            exit_node = loop_switch_block._loop_exit_node()
        else:
            exit_node = loop_switch_block.exit_node()
        
        return [exit_node]
    

class ContinueStatementBlock(BasicBlock):
    @cached
    def entry_node(self):
        return ContinueStatementNode(self)
    

class ContinueStatementNode(SimpleNode):
    def _successors(self):
        loop_block = self.parent_block

        while loop_block and not isinstance(loop_block, LoopBlock):
            loop_block = loop_block.parent
        
        assert loop_block, "Continue statements can only be inside of loop statements"
        return [loop_block._loop_node()]


# While loop statements ----------------------------------------------------------------

class WhileLoopBlock(LoopBlock):
    
    @cached
    def entry_node(self):
        return WhileLoopNode(self)
    
    
class WhileLoopNode(LoopNode):
    def _successors(self):
        body_successors = [block.entry_node() for block in self.inner_blocks()]
        exit_node = self._loop_exit_node()

        loop_condition = self.ast_node.child_by_field_name("condition")
        if is_trivial_true(loop_condition):
            return body_successors
        
        if is_trivial_false(loop_condition):
            return [exit_node]

        return body_successors + [exit_node]

# Do while loop ----------------------------------------------------------------


class DoWhileStatementBlock(LoopBlock):
    
    @cached
    def entry_node(self):
        return DoWhileInitNode(self)
    
    @cached
    def exit_node(self):
        return DoWhileLoopNode(self)
    
    def _loop_node(self):
        return self.exit_node()
    
class DoWhileInitNode(EntryNode):
    pass

class DoWhileLoopNode(WhileLoopNode):
    def _predecessors(self):
        predecessors = [self.entry_node()]
        predecessors += exit_nodes(*[block.exit_node() for block in self.inner_blocks()])
        predecessors += exit_nodes(*self.scope.continue_statements())
        return predecessors

# For loop statements ----------------------------------------------------------------

class ForStatementBlock(LoopBlock):
    
    @cached
    def entry_node(self):
        if self.ast_node.child_by_field_name("initializer") is None:
            return self._loop_node()

        return ForInitNode(self)
    
    @cached
    def _loop_node(self):
        return ForLoopIterBlock(
                self.graph, self.ast_node, scope = self.scope, parent = self.parent
            ).entry_node()


class ForLoopIterBlock(LoopBlock):

    @cached
    def entry_node(self):
        return ForLoopConditionNode(self)
    
    @cached
    def exit_node(self):
        if self.ast_node.child_by_field_name("update") is None:
            return self.entry_node()

        return ForLoopUpdateNode(self)


class ForInitNode(EntryNode):

    def _successors(self):
        return [self._loop_node()]


class ForLoopConditionNode(EntryNode):

    def _predecessors(self):
        predecessors = super()._backtrack_predecessors()

        if self.ast_node.child_by_field_name("update") is None:
            predecessors += exit_nodes(*[block.exit_node() for block in self.inner_blocks()])
            predecessors += exit_nodes(*self.scope.continue_statements())
        else:
            predecessors += [self.exit_node()]

        return predecessors

    def _successors(self):
        body_successors = super()._successors()
        loop_exit = self.parent_block._loop_exit_node()

        loop_condition = self.ast_node.child_by_field_name("condition")
        if is_trivial_true(loop_condition):
            return body_successors
        
        if is_trivial_false(loop_condition):
            return [loop_exit]

        return  super()._successors() + [loop_exit]


class ForLoopUpdateNode(ExitNode):
    def _predecessors(self):
        predecessors = []
        predecessors += exit_nodes(*[block.exit_node() for block in self.inner_blocks()])
        predecessors += exit_nodes(*self.scope.continue_statements())
        return predecessors

    def _successors(self):
        return [self.entry_node()]
    
# Gotos ------------------------------------------------------------------------------------------------

class LabeledStatementBlock(BasicBlock):

    def label(self):
        return self.ast_node.children[0].text.decode('utf-8')
    
    @cached
    def inner_blocks(self):
        return [
            self.graph.attach(self.ast_node.children[-1], scope = self.scope, parent = self)
        ]

    @cached
    def entry_node(self):
        return LabeledStatementNode(self)
    
    @cached
    def exit_node(self):
        return LabeledStatementExitNode(self)


class LabeledStatementNode(EntryNode):
    
    def _predecessors(self):
        predecessors = super()._predecessors()
        predecessors += exit_nodes(*self.scope.gotos().get(self.label(), []))
        return predecessors


class LabeledStatementExitNode(ExitNode):
    def is_silent(self): return True


class GotoStatementBlock(BasicBlock):

    def label(self):
        return self.ast_node.children[1].text.decode('utf-8')

    @cached
    def entry_node(self):
        return GotoStatementNode(self)


class GotoStatementNode(SimpleNode):
    def _successors(self):
        label = self.label()
        labeled_statements = self.scope.labeled_statements()
        if label in labeled_statements:
            return [labeled_statements[label].entry_node()]
        else:
            return []

# Comments ----------------------------------------------------------------

class CommentBlock(BasicBlock):
    def entry_node(self):
        return CommentNode(self)

class CommentNode(SimpleNode):
    def is_silent(self): return SILENT_COMMENTS

# Error ----------------------------------------------------------------

class ErrorBlock(BasicBlock):
    def entry_node(self):
        return ErrorNode(self)

class ErrorNode(SimpleNode):
    pass


class AbortBlock(BasicBlock):
    def entry_node(self):
        return AbortNode(self)
    
class AbortNode(SimpleNode):
    def _successors(self):
        return [self.scope.program_block().exit_node()]


class AssumeBlock(BasicBlock):
    def entry_node(self):
        return AssumeNode(self)
    
class AssumeNode(SimpleNode):
    
    def condition(self):
        return self.ast_node.child_by_field_name("condition")


# Optimizations ----------------------------------------------------------

def handle_function_call(graph, ast_node, call_expression, **kwargs):
    function_node = call_expression.child_by_field_name("function")
    assert function_node.type == "identifier", "Cannot handle complex function call patterns"
    function_name = function_node.text.decode('utf-8')

    if function_name == "reach_error":
        return ErrorBlock(graph, ast_node, **kwargs)
    
    if function_name in ["abort", "exit"]:
        return AbortBlock(graph, ast_node, **kwargs)
    
    return FunctionCallBlock(graph, ast_node, **kwargs)


def expression_or_call_statement(graph, ast_node, **kwargs):

    expression = ast_node.children[0]
    if expression.type == "call_expression":
        return handle_function_call(graph, ast_node, expression, **kwargs)
    
    if expression.type == "assignment_expression" and expression.children[-1].type == "call_expression":
        return handle_function_call(graph, ast_node, expression.children[-1], **kwargs)
    
    return ExpressionBlock(graph, ast_node, **kwargs)


def declaration_or_call_statement(graph, ast_node, **kwargs):
    
    if len(ast_node.children) == 3 and ast_node.children[1].type == "init_declarator":
        init_declarator = ast_node.children[1]
        if init_declarator.children[-1].type == "call_expression":
            return handle_function_call(graph, ast_node, init_declarator.children[-1], **kwargs)

    return DeclarationBlock(graph, ast_node, **kwargs)

# if-return and if-abort are directly handled as assumes

def if_or_assume(graph, ast_node, **kwargs):
    consequence = ast_node.child_by_field_name("consequence")
    alternative = ast_node.child_by_field_name("alternative")
    
    if alternative is None:
        if consequence.type == "compound_statement":
            if len(consequence.children) != 3: return IfStatementBlock(graph, ast_node, **kwargs)
            consequence = consequence.children[1]
        
        preview_block = create_block_by_type(graph, consequence, **kwargs)
        if isinstance(preview_block, AbortBlock):
            return AssumeBlock(graph, ast_node, **kwargs)
        elif isinstance(preview_block, ReturnStatementBlock):
            #preview_successors = preview_block.exit_node().successors()
            #next_block = preview_successors[0]
            #if next_block == preview_block.scope.main_function().exit_node():
            ast_node = preview_block.ast_node
            if len(ast_node.children) == 1 or ast_node.children[1].type == "number_literal":
                return AssumeBlock(graph, ast_node, **kwargs)

    return IfStatementBlock(graph, ast_node, **kwargs)

# Factory ----------------------------------------------------------------


BLOCK_REGISTRY = {
    "translation_unit": ProgramBlock,
    "function_definition": FunctionBlock,
    "return_statement": ReturnStatementBlock,
    "if_statement": if_or_assume,
    "while_statement": WhileLoopBlock,
    "for_statement": ForStatementBlock,
    "do_statement": DoWhileStatementBlock,
    "break_statement": BreakStatementBlock,
    "continue_statement": ContinueStatementBlock,
    "declaration": declaration_or_call_statement,
    "expression_statement": expression_or_call_statement,
    "compound_statement": CompoundBlock,
    "comment": CommentBlock,

    "switch_statement": SwitchStatementBlock,
    "case_statement": CaseStatementBlock,

    "labeled_statement": LabeledStatementBlock,
    "goto_statement": GotoStatementBlock,
}

def create_block_by_type(graph, ast_node, **kwargs):
    if ast_node is None: return EmptyBlock(graph, **kwargs)
    ast_node_type = ast_node.type

    if ast_node_type not in BLOCK_REGISTRY:
        raise NotImplementedError("CFG Node for type %s is not implemented" % ast_node_type)

    return BLOCK_REGISTRY[ast_node_type](graph, ast_node, **kwargs)

# Utils ----------------------------------------------------------------

def exit_nodes(*blocks):
    return [block.exit_node() for block in blocks]

def entry_nodes(*blocks):
    return [block.entry_node() for block in blocks] 
