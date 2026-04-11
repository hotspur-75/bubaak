class Witness:
    """
    Class representing a counter-example to the property or
    a proof that a property holds
    """

    def is_graphml(self):
        return False

    def is_harness(self):
        return False


class WitnessGraphML(Witness):
    """
    SV-COMP GraphMLWitness. It is either a link to a file
    or the whole XML data.
    """

    def __init__(self, path=None, data=None):
        self.path = path
        self.data = data

    def is_graphml(self):
        return True


class WitnessHarness(Witness):
    """
    C file that fills in non-deterministic behavior when linked with the
    program.
    """

    def __init__(self, path=None, data=None):
        self.path = path
        self.data = data

    def is_harness(self):
        return True
