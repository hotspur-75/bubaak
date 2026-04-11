from bbk.dbg import print_stderr

PRP_UNREACH_KEY = "unreach"
PRP_VALID_DEREF_KEY = "valid-deref"
PRP_VALID_FREE_KEY = "valid-free"
PRP_NO_MEMLEAK_KEY = "no-memleak"
PRP_VALID_MEMCLEANUP = "memcleanup"
PRP_NO_SIGNED_OVERFLOW_KEY = "no-signed-overflow"
PRP_DEF_BEHAVIOR_KEY = "def-behavior"
PRP_TERMINATION_KEY = "termination"


class Property:
    """
    Class representing a checked property of a program
    """

    def __init__(self, key):
        self._key = key

    def is_unreach(self):
        return self.key() == PRP_UNREACH_KEY

    def is_valid_deref(self):
        return self.key() == PRP_VALID_DEREF_KEY

    def is_valid_free(self):
        return self.key() == PRP_VALID_FREE_KEY

    def is_no_memleak(self):
        return self.key() == PRP_NO_MEMLEAK_KEY

    def is_memcleanup(self):
        return self.key() == PRP_VALID_MEMCLEANUP

    def is_memsafety(self):
        return self.key() in (
            PRP_VALID_FREE_KEY,
            PRP_VALID_DEREF_KEY,
            PRP_NO_MEMLEAK_KEY,
        )

    def is_no_signed_overflow(self):
        return self.key() == PRP_NO_SIGNED_OVERFLOW_KEY

    def is_def_behavior(self):
        return self.key() == PRP_DEF_BEHAVIOR_KEY

    def is_termination(self):
        return self.key() == PRP_TERMINATION_KEY

    def descr(self):
        """Human-readable description of the property"""
        return self.__doc__

    def key(self):
        """A unique key representing the property"""
        return self._key


class PropertyUnreach(Property):
    """specified calls are not reachable"""

    def __init__(self, error_fns=None):
        super().__init__(PRP_UNREACH_KEY)
        self._error_funs = error_fns

    def error_funs(self):
        return self._error_funs

    def set_error_funs(self, fns):
        self._error_funs = fns

    def descr(self):
        if not self._error_funs:
            return super().descr()
        return f" calls to {', '.join(self.error_funs())} are unreachable"


class PropertyValidDeref(Property):
    """all memory dereferences in the program are valid"""

    def __init__(self):
        super().__init__(PRP_VALID_DEREF_KEY)


class PropertyValidFree(Property):
    """all memory deallocations  are valid"""

    def __init__(self):
        super().__init__(PRP_VALID_FREE_KEY)


class PropertyNoMemleak(Property):
    """all allocated memory is properly traced till the end of the program"""

    # (the memory may be unfreed when program exits, though)

    def __init__(self):
        super().__init__(PRP_NO_MEMLEAK_KEY)


class PropertyMemcleanup(Property):
    """all allocated memory is propertly freed"""

    def __init__(self):
        super().__init__(PRP_VALID_MEMCLEANUP)


class PropertyNoSignedOverflow(Property):
    """no signed-integer operation can overflow"""

    def __init__(self):
        super().__init__(PRP_NO_SIGNED_OVERFLOW_KEY)


class PropertyDefBehavior(Property):
    """program contains no undefined behavior"""

    def __init__(self):
        super().__init__(PRP_DEF_BEHAVIOR_KEY)


class PropertyTermination(Property):
    """program terminates"""

    def __init__(self):
        super().__init__(PRP_TERMINATION_KEY)


supported_properties = {
    PRP_UNREACH_KEY: PropertyUnreach,
    PRP_VALID_DEREF_KEY: PropertyValidDeref,
    PRP_VALID_FREE_KEY: PropertyValidFree,
    PRP_NO_MEMLEAK_KEY: PropertyNoMemleak,
    PRP_VALID_MEMCLEANUP: PropertyMemcleanup,
    PRP_NO_SIGNED_OVERFLOW_KEY: PropertyNoSignedOverflow,
    PRP_DEF_BEHAVIOR_KEY: PropertyDefBehavior,
    PRP_TERMINATION_KEY: PropertyTermination,
}


def get_properties(args):
    ret = PropertiesList()
    if args.prp is None:
        ret.append(PropertyUnreach(error_fns=["__assert_fail"]))
        return ret

    prps = set()
    for prp in args.prp:
        if prp not in supported_properties:
            print_stderr("----------------")
            print_stderr("Supported properties:")
            for key, p in supported_properties.items():
                print_stderr(f"  - {key: <20}: {p.__doc__}")
            raise RuntimeError(f"Unsupported property: {prp}")

        p = supported_properties[prp]()
        prps.add(p)

        if p.is_unreach():
            p.set_error_funs(args.error_fn or ["__assert_fail"])

    ret.extend(prps)
    return ret


class PropertiesList(list):
    def get(self, key):
        for item in self:
            if key == item.key():
                return item
        return None


class PropertiesSet:
    """
    A wrapper around multiple properties for easily handling them
    """

    def __init__(self, *props):
        if not props:
            raise RuntimeError("No properties")

        self._properties = {}
        self._num = 0

        for p in props:
            self.add(p)

    def is_single(self):
        """
        Is the set singleton, i.e., only one property?
        """
        return self._num == 1

    def get_single(self):
        assert self.is_single()
        return next((x for x in self._properties.values()))[0]

    def add(self, p):
        """
        Add a new property to this set
        """
        self._properties.setdefault(p.key(), []).append(p)
        self._num += 1

    def has_unreach(self):
        return self._properties.get(PRP_UNREACH_KEY) is not None

    def has_termination(self):
        return self._properties.get(PRP_TERMINATION_KEY) is not None

    def has_memcleanup(self):
        return self._properties.get(PRP_VALID_MEMCLEANUP) is not None

    def has_memsafety(self):
        return (
            self._properties.get(PRP_VALID_FREE_KEY) is not None
            or self._properties.get(PRP_VALID_DEREF_KEY) is not None
            or self._properties.get(PRP_NO_MEMLEAK_KEY) is not None
        )

    def has_def_behavior(self):
        return self._properties.get(PRP_DEF_BEHAVIOR_KEY) is not None

    def has_no_overflow(self):
        return self._properties.get(PRP_NO_SIGNED_OVERFLOW_KEY) is not None

    def __iter__(self):
        return (p for vals in self._properties.values() for p in vals)
