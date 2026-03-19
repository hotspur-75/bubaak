from .utils import cached
from .visitors import visit_tree


class ParentScope:

    def __init__(self, base_scope):
        self.base_scope = base_scope

    def __getattr__(self, name):
        
        if self.base_scope is None: 
            raise AttributeError(name)
        
        try:
            return self.base_scope.__getattribute__(name)
        except AttributeError:
            grandparent_scope = self.base_scope.parent_scope()
            if grandparent_scope is None: return None
            attr = getattr(grandparent_scope, name)
            return attr


class Scope:
    
    def __init__(self, graph, root_ast_node, parent = None):
        self.graph = graph
        self.root_ast_node = root_ast_node
        self.parent = parent

    def parent_scope(self):
        return ParentScope(self.parent)
    
    def __getattr__(self, name):
        return getattr(self.parent_scope(), name)
    
    def __repr__(self):
        return f"{self.__class__.__name__}({self.root_ast_node.type}) --> {self.parent}"
    

class RootScope(Scope):
    pass


class ProgramScope(Scope):
    
    def global_declarations(self):
        global_declarations = visit_tree(
            self.root_ast_node,
            lambda node: node.type == "declaration",
            lambda node: node.type != "function_definition",
        )

        declarations = {}
        for declaration_node in global_declarations:
            declaration_name = _declaration_name(declaration_node)
            assert declaration_name is not None
            declarations[declaration_name] = self.graph.attach(declaration_node, scope = self, init = True)

        return declarations
    
    def defined_variables(self):
        return self.global_declarations()
    
    def program_aborts(self):
        return []

    @cached
    def external_functions(self):
        global_declarations = visit_tree(
            self.root_ast_node,
            lambda node: node.type == "declaration",
            lambda node: node.type != "function_definition",
        )

        declarations = {}
        for declaration_node in global_declarations:
            declarator = declaration_node.child_by_field_name("declarator")
            if declarator is None: continue
            for function_declarator in visit_tree(declarator, lambda node: node.type == "function_declarator"):
                name_declarator = function_declarator.child_by_field_name("declarator")

                if name_declarator.type == "parenthesized_declarator":
                    name_declarator = name_declarator.children[1]

                if name_declarator is None or name_declarator.type!= "identifier": 
                    continue

                function_name = name_declarator.text.decode('utf-8')
                declarations[function_name] = declaration_node
        
        return declarations


    @cached
    def function_definitions(self):
        function_definition_nodes = visit_tree(
            self.root_ast_node,
            lambda node: node.type == "function_definition",
            lambda node: node.type != "function_definition",
        )

        function_defs = {}

        for function_definition_node in function_definition_nodes:
            declarator = function_definition_node.child_by_field_name("declarator")
            
            while declarator and declarator.type != "function_declarator":
                declarator = declarator.child_by_field_name("declarator")

            if declarator is None: continue
            name_declarator = declarator.child_by_field_name("declarator")

            if name_declarator.type == "parenthesized_declarator":
                name_declarator = name_declarator.children[1]

            if name_declarator is None or name_declarator.type!= "identifier": 
                continue

            function_name = name_declarator.text.decode('utf-8')
            function_defs[function_name] = self.graph.attach(function_definition_node, scope = self, init = True)

        return function_defs

    @cached
    def function_calls(self):
        call_expression_nodes = visit_tree(
            self.root_ast_node,
            lambda node: node.type == "call_expression",
            lambda node: node.type not in ["attribute_specifier"]
        )

        function_calls = {}
        for call_expression_node in call_expression_nodes:
            function = call_expression_node.child_by_field_name("function")
            if function is None or function.type != "identifier": continue
            function_name = function.text.decode('utf-8')
            if function_name not in function_calls: function_calls[function_name] = []

            call_parent = call_expression_node
            while call_parent is not None:
                if call_parent.type.endswith("statement"): break
                if call_parent.type.endswith("declaration"): break
                call_parent = call_parent.parent

            function_calls[function_name].append(
                self.graph.attach(call_parent, scope = self, init = True)
            )

        return function_calls
    
    def program_block(self):
        return self.graph.attach(self.root_ast_node, scope = self, init = True)

    def main_function(self):
        return self.function_definitions().get("main", None)


class FunctionScope(Scope):

    def function_block(self):
        return self.graph.attach(self.root_ast_node, scope = self, init = True)
    
    def local_function_calls(self):
        call_expression_nodes = visit_tree(
            self.root_ast_node,
            lambda node: node.type == "call_expression"
        )

        function_calls = {}
        for call_expression_node in call_expression_nodes:
            function = call_expression_node.child_by_field_name("function")
            if function is None or function.type != "identifier": continue
            function_name = function.text.decode('utf-8')
            if function_name not in function_calls: function_calls[function_name] = []

            call_parent = call_expression_node
            while call_parent is not None:
                if call_parent.type.endswith("statement"): break
                if call_parent.type.endswith("declaration"): break
                call_parent = call_parent.parent

            function_calls[function_name].append(
                self.graph.attach(call_parent)
            )

        return function_calls


    @cached
    def labeled_statements(self):
        labeled_statement_nodes = visit_tree(
            self.root_ast_node,
            lambda node: node.type == "labeled_statement"
        )

        labeled_statements = {}
        for labeled_statement_node in labeled_statement_nodes:
            name = labeled_statement_node.children[0].text.decode('utf-8')
            node = self.graph.attach(labeled_statement_node, scope = self, init = True)
            labeled_statements[name] = node
        
        return labeled_statements

    @cached
    def gotos(self):
        goto_statement_nodes = visit_tree(
            self.root_ast_node,
            lambda node: node.type == "goto_statement"
        )

        goto_statements = {}
        for goto_statement_node in goto_statement_nodes:
            name = goto_statement_node.children[1].text.decode('utf-8')
            node = self.graph.attach(goto_statement_node, scope = self, init = True)
            if name not in goto_statements: goto_statements[name] = []
            goto_statements[name].append(node)
        
        return goto_statements

    @cached
    def returns(self):
        return_statement_nodes = visit_tree(
            self.root_ast_node,
            lambda node: node.type == "return_statement"
        )

        return_statements = []
        for return_statement_node in return_statement_nodes:
            return_block = self.graph.attach(return_statement_node, scope = self, init = True)
            if return_block.parent.type() == "AssumeBlock": continue # This only happens for if(!cond) return 0; in main function

            return_statements.append(
                return_block
            )
        
        return return_statements


class LoopScope(Scope):
    
    @cached
    def break_statements(self):
        break_statement_nodes = visit_tree(
            self.root_ast_node.child_by_field_name("body"),
            lambda node: node.type == "break_statement",
            lambda node: node.type not in ["while_statement", "for_statement", "do_statement", "switch_statement"]
        )

        break_statements = []
        for break_statement_node in break_statement_nodes:
            break_statements.append(
                self.graph.attach(break_statement_node, scope = self, init = True)
            )
        
        return break_statements

    @cached
    def continue_statements(self):
        continue_statement_nodes = visit_tree(
            self.root_ast_node.child_by_field_name("body"),
            lambda node: node.type == "continue_statement",
            lambda node: node.type not in ["while_statement", "for_statement", "do_statement", "switch_statement"]
        )

        continue_statements = []
        for continue_statement_node in continue_statement_nodes:
            continue_statements.append(
                self.graph.attach(continue_statement_node, scope = self, init = True)
            )
        
        return continue_statements


class CompoundScope(Scope):
    
    @cached
    def defined_variables(self):
        parent_declarations = self.parent_scope().defined_variables()

        declarations = dict(parent_declarations)
        local_declaration_nodes = visit_tree(
            self.root_ast_node.children,
            lambda node: node.type == "declaration",
            lambda node: node.type != "compound_statement",
        )

        for declaration_node in local_declaration_nodes:
            declaration_name = _declaration_name(declaration_node)
            assert declaration_name is not None
            declarations[declaration_name] = self.graph.attach(declaration_node, scope = self, init = True)

        return declarations


class SwitchScope(CompoundScope):
    
    @cached
    def break_statements(self):
        break_statement_nodes = visit_tree(
            self.root_ast_node.child_by_field_name("body"),
            lambda node: node.type == "break_statement",
            lambda node: node.type not in ["while_statement", "for_statement", "do_statement", "switch_statement"]
        )

        break_statements = []
        for break_statement_node in break_statement_nodes:
            break_statements.append(
                self.graph.attach(break_statement_node, scope = self, init = True)
            )
        
        return break_statements
    

# Util -------------

def _declaration_name(declaration):
    while declaration is not None and declaration.type != "identifier":
        declaration = declaration.child_by_field_name("declarator")
    
    if declaration is None: return None
    return declaration.text.decode('utf-8')