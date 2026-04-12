

def eval_trivial(condition_root):
    if condition_root.type == 'number_literal':
        literal = condition_root.text.decode('utf-8')
        if literal != '0' and literal != '1':
            return None
        else:
            return literal

    if condition_root.type == "parenthesized_expression":
        return eval_trivial(condition_root.children[1])

    if condition_root.type == "binary_expression":
        operator = condition_root.children[1].type
        left = eval_trivial(condition_root.children[0])

        if "|" in operator:
            if left == "1": return "1"
            if left == "0": return eval_trivial(condition_root.children[-1])
            if left is None: return None
        
        if "&" in operator:
            if left == "1": return eval_trivial(condition_root.children[-1])
            if left == "0": return "0"
            if left is None: return None
        
        return None    
    

# Simplify expression ----------------------------------------------------------------

class ExpressionSimplifier:
    OPERATORS = {"&&": "sand", "||": "sor", 
                 "&": "and", "|": "or", 
                 "+" : "plus", "-": "minus",
                 "*" : "mult", "/": "div",
                 "~": "negate", "<<": "lshift",
                 ">>": "rshift",
                 "==": "eq", "!=": "neq",
                 "<": "lt", "<=": "lte",
                 ">": "gt", ">=": "gte",
                 }
    
    def __init__(self, env=None):
        self.env = env or {}
    
    def value(self, literal):
        try:
            return int(literal)
        except ValueError: 
            return None

    def identify(self, expression):
        return expression.text.decode("utf-8")
    
    def simplify_number_literal(self, expression):
        return expression.text.decode("utf-8")
    
    def simplify_identifier(self, expression):
        name = expression.text.decode("utf-8")
        # If we know the constant value, substitute it!
        if name in self.env:
            return str(self.env[name])
        return name
    
    def simplify_parenthesized_expression(self, expression):
        return self.simplify(expression.children[1])
    
    def simplify_unary_expression(self, expression):
        op = expression.children[0].type
        operand = self.simplify(expression.children[1])
        value   = self.value(operand)

        if op == "!" and value is not None:
            return "1" if value == 0 else "0"

        return f"{op}({operand})"
    
    def simplify_binary_expression(self, expression):
        left, op, right = expression.children

        top  = op.type
        lop = self.simplify(left)
        rop = self.simplify(right)

        vlop = self.value(lop)
        vrop = self.value(rop)

        if top in ["|", "||"]:
            if vlop == 0: return rop
            if vrop == 0: return lop

            if vlop is not None and vlop != 0: return "1"
            if vrop is not None and vrop != 0: return "1"

        if top in ["&", "&&"]:
            if vlop is not None and vlop != 0: return rop
            if vrop is not None and vrop != 0: return lop
            if vlop == 0 or rop == 0: return "0"

        if lop == rop and vlop is not None:
            if top in ["==", ">=", "<="]: return "1"
            if top in ["!=", "<", ">"]  : return "0"
        
        if vlop is not None and vrop is not None:
            if top == "<": return "1" if vlop < vrop else "0"
            if top == "<=": return "1" if vlop <= vrop else "0"
            if top == ">": return "1" if vlop > vrop else "0"
            if top == ">=": return "1" if vlop >= vrop else "0"
            if top == "==": return "1" if vlop == vrop else "0"
            if top == "!=": return "1" if vlop != vrop else "0"

        if right.type == "parenthesized_expression":  rop = f"({rop})"
        if left.type  == "parenthesized_expression":  lop = f"({lop})"

        return f"{lop} {top} {rop}"


    def simplify(self, expression):
        if expression is None: return "1"
        return getattr(self, f"simplify_{expression.type}", self.identify)(expression)


# Update the helper functions to accept the environment
def simplify(expression, env=None):
    return ExpressionSimplifier(env).simplify(expression)

def is_trivial_true(condition_root, env=None):
    return simplify(condition_root, env) == "1"

def is_trivial_false(condition_root, env=None):
    return simplify(condition_root, env) == "0"