

# This most be immutable
class AbstractElement:

    def is_top(self): return False
    def is_bottom(self): return False
    
    def is_less_or_equal(self, other):
        raise NotImplementedError()
    
    def union(self, other):
        raise NotImplementedError()
    
    def __leq__(self, other):
        return self.is_less_or_equal(other)
    
    def __or__(self, other):
        return self.union(other)
    

class AbstractDomain:

    def abstract(self, element):
        raise NotImplementedError()

    def concretize_once(self, abstract_element):
        """It is often sufficient to have a single concrete element"""
        raise NotImplementedError() 


# Special elements --------------------------------


class TopElement(AbstractElement):

    def is_top(self):
        return True

    def is_less_or_equal(self, other):
        return False
    
    def union(self, other):
        return self
    
    def __repr__(self) -> str:
        return "TOP"
    

class BottomElement(AbstractElement):

    def is_bottom(self):
        return True

    def is_less_or_equal(self, other):
        return True
    
    def union(self, other):
        return other
    
    def __repr__(self) -> str:
        return "BOT"
    

TOP, BOTTOM = TopElement(), BottomElement()

# Composite domain --------------------------------

class CompositeElement(AbstractElement):

    def __init__(self, *elements):
        self.elements = tuple(elements)
    
    def is_less_or_equal(self, other):
        if other.is_top(): return True
        if other.is_bottom(): return False
        return all(self.elements[i].is_less_or_equal(other.elements[i]) for i in range(len(self.elements)))

    def union(self, other):
        if other.is_top(): return other
        if other.is_bottom(): return self
        return CompositeElement(*(
            self.elements[i].union(other.elements[i])
            for i in range(len(self.elements))
        ))
    
    def __iter__(self):
        for element in self.elements:
            yield element

    def __repr__(self):
        return str(self.elements)

    def __eq__(self, other):
        if other.is_top() or other.is_bottom(): return False
        return self.elements == other.elements

    def __neq__(self, other):
        return not self.__eq__(other)
    
    def __hash__(self):
        return hash(self.elements)


class CompositeDomain(AbstractDomain):
    
    def __init__(self, *domains):
        self.domains = tuple(domains)

    def abstract(self, element):
        assert isinstance(element, tuple) and len(element) == len(self.domains)
        return CompositeElement(*[self.domains[i].abstract(e) for i, e in enumerate(element)])

    def concretize_once(self, abstract_element):
        return tuple(self.domains[i].concretize_once(e) for i, e in enumerate(abstract_element.elements))

    
# Flat domain -------------------------------------------------------------
# This domain can be used for arbitrary Python objects

class FlatElement(AbstractElement):

    def __init__(self, value):
        self.value = value

    def _wrap(self, value):
        if isinstance(value, FlatElement): return value
        return FlatElement(value)
    
    def is_less_or_equal(self, other):
        if other.is_top(): return True
        if other.is_bottom(): return False
        return self.value == self._wrap(other).value
    
    def union(self, other):
        if other.is_top() or other.is_bottom():
            return other.union(self)
        
        if self.value == self._wrap(other).value: return self
        return TOP

    def __eq__(self, other):
        if other.is_top() or other.is_bottom(): return False
        return self.value == self._wrap(other).value
    
    def __neq__(self, other):
        if other.is_top() or other.is_bottom(): return True
        return not self.__eq__(other)
    
    def __hash__(self):
        return hash(self.value)
    
    def __repr__(self):
        return str(self.value)


class FlatDomain(AbstractDomain):

    def abstract(self, number):
        if number is None: return TOP
        return FlatElement(number)

    def concretize_once(self, abstract_element):
        if abstract_element.is_top(): return None
        if abstract_element.is_bottom(): raise ValueError("No concrete value for bottom exists.")
        return abstract_element.value
    
# Number lattices ----------------------------------------------------------------

class NumberElement(FlatElement):

    def __init__(self, value):
        self.value = value

    def _wrap(self, value):
        if isinstance(value, NumberElement): return value
        return NumberElement(value)
    
    def __add__(self, other):
        if other.is_top() or other.is_bottom(): return other
        return NumberElement(self.value + self._wrap(other).value)
    
    def __radd__(self, other):
        return self.__add__(other)
    
    def __sub__(self, other):
        if other.is_top() or other.is_bottom(): return other
        return NumberElement(self.value - self._wrap(other).value)
    
    def __rsub__(self, other):
        return self.__sub__(other) # TODO

    def __mul__(self, other):
        if other.is_top() or other.is_bottom(): return other
        return NumberElement(self.value * self._wrap(other).value)
    
    def __rmul__(self, other):
        return self.__mul__(other)
    
    def __div__(self, other):
        if other.is_top() or other.is_bottom(): return other
        return NumberElement(self.value / self._wrap(other).value)
    
    def __rdiv__(self, other):
        return self.__div__(other) # TODO
    

class NumberDomain(FlatDomain):

    def abstract(self, number):
        if number is None: return TOP
        return NumberElement(number)

    def concretize_once(self, abstract_element):
        if abstract_element.is_top(): return 0
        if abstract_element.is_bottom(): raise ValueError("No concrete value for bottom exists.")
        return abstract_element.value
    
# Set domain --------------------------------------------------------------

class SetElement(AbstractElement):

    def __init__(self, current_set = set()):
        self.current_set = current_set

    def is_less_or_equal(self, other):
        if other.is_top(): return True
        if other.is_bottom(): return False
        if self.current_set == other.current_set: return True
        return self.current_set.issubset(other.current_set)
    
    def union(self, other):
        if other.is_top(): return other
        if other.is_bottom(): return self
        return SetElement(set.union(self.current_set, other.current_set))
    
    def __eq__(self, other):
        if other.is_top() or other.is_bottom(): return False
        return self.current_set == other.current_set
    
    def __neq__(self, other):
        if other.is_top() or other.is_bottom(): return True
        return not self.__eq__(other)
    
    def __hash__(self):
        return hash(tuple(self.current_set))
    
    def __repr__(self):
        return str(self.current_set)
    

class SetDomain(AbstractDomain):
    
    def abstract(self, element):
        if not isinstance(element, set): raise TypeError()
        return SetElement(element)
    
    def concretize_once(self, abstract_element):
        if abstract_element.is_top(): return {}
        if abstract_element.is_bottom(): raise ValueError("No concrete value for bottom exists.")
        return abstract_element.current_set
    
  


# Variables ----------------------------------------------------------------

class VariableSet(AbstractElement):

    def __init__(self, assigns = {}, value_type = NumberElement):
        self._value_type = value_type
        self._assigns = assigns
    
    def __getitem__(self, key):
        return self._assigns.get(key, TOP)
    
    def __setitem__(self, key, value):
        if not isinstance(value, self._value_type): raise ValueError("")

        new_assigns = dict(self._assigns)
        new_assigns[key] = value
        return VariableSet(new_assigns, self._value_type)

    def is_less_or_equal(self, other):
        if other.is_top(): return True
        if other.is_bottom(): return False
        if not isinstance(other, VariableSet): return False

        for key in set.union(*[set(self._assigns.keys()), set(other._assigns.keys())]):
            if not (self[key] <= other[key]): return False

        return True
    
    def union(self, other):
        if other.is_top() or other.is_bottom():
            return other.union(self)
        
        if not isinstance(other, VariableSet): raise ValueError("")

        new_assigns = dict(self._assigns)
        for key, value in other._assigns.items():
            new_assigns[key] = new_assigns.get(key, TOP).union(value)

        return VariableSet(new_assigns, self._value_type)
    
    def _index_key(self):
        return tuple(self._assigns.items())
    
    def __eq__(self, other):
        if other.is_top() or other.is_bottom(): return False
        return self._index_key() == other._index_key()
    
    def __neq__(self, other):
        if other.is_top() or other.is_bottom(): return True
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self._index_key_())
    

class VariableSetDomain(AbstractDomain):

    def abstract(self, element):
        if element is None: return BOTTOM
        if not isinstance(element, dict): raise ValueError("Expected dictionary")
        if len(element) == 0: return BOTTOM

        variable_type = None
        for value in element.values():
            variable_type = type(value)
            break

        return VariableSet(element, variable_type)
    
    def concretize_once(self, abstract_element):
        if abstract_element.is_top(): return {}
        if abstract_element.is_bottom(): raise ValueError("No concrete value for bottom exists.")
       
        if abstract_element._value_type != NumberElement:
            raise ValueError("Unknown value type:", abstract_element._value_type)
        
        number_domain = NumberDomain()
        return {
            var: number_domain.concretize_once(value)
            for var, value in abstract_element.items()
        }

