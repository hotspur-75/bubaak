from typing import Any
from code_ast import ASTVisitor

def visit_tree(root, visitor_fn, filter_fn = None):
    """
    Function to traverse a tree easily from the root.

    This function creates an AST visitor on the fly
    based on the given visitor function. The visitor
    function should return either a boolean or
    a value. Boolean indicate whether the current
    node should included in the result or not. If the
    function returns value other than None, this also
    will be included in the result.
    
    A visitor function cannot skip subtree. Use an
    ASTVisitor() to implement this functionality.

    Parameters
    ----------
    root : TSNode, SourceCodeAst
        A tree-sitter node that acts as the root
        to the subtree.
    
    visitor_fn : function TSNode -> object or str
        A function that maps each node in the 
        subtree to an object.
        Alternatively a string can be provided. This
        is equivalent to a visitor 
        lambda node: node.type == visitor_fn
    
    filter_fn : function TSNode -> bool
        A function that decides whether subtrees should be included.
        default: None (All subtrees should be included)

    Returns
    -------
    List[object]
        A list of all objects computed by traversing the tree 
     
    """
    if not filter_fn: filter_fn = lambda x: True

    if hasattr(root, 'source_tree'):
        root = root.source_tree
    
    if isinstance(visitor_fn, ASTVisitor):
        visitor_fn = visitor.walk(root)
        return None

    if isinstance(visitor_fn, str):
        node_type  = visitor_fn
        visitor_fn = lambda node: node.type == node_type
    
    assert callable(visitor_fn), "visitor_fn must be a callable"
    
    visitor = CollectingVisitor(visitor_fn, filter_fn)

    if not _is_iterable(root): root = [root]

    for node in root:
        visitor.walk(node)
    
    return visitor._results


class StatefulAbort:

    def __init__(self, filter_fn):
        self.abort = False
        self.filter_fn = filter_fn
    
    def __call__(self, node):
        if self.abort: return False
        self.abort = self.filter_fn(node)
        return not self.abort

def contains_tree(root, filter_fn): 
    return len(visit_tree(root, filter_fn, StatefulAbort(filter_fn))) > 0


class CollectingVisitor(ASTVisitor):

    def __init__(self, map_fn, filter_fn):
        self._map_fn  = map_fn
        self.filter_fn = filter_fn
        self._results = []
    
    def visit(self, node):
        result = self._map_fn(node)

        if result:
            if result is True: result = node
            self._results.append(result)

        return self.filter_fn(node)
    

def _is_iterable(iterable):
    try:
        for x in iterable: return True
    except TypeError:
        return False