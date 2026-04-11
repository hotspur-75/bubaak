from bbk.dbg import print_stdout


def result_kind_to_str(val):
    if val == Verdict.CORRECT:
        return "CORRECT"
    if val == Verdict.INCORRECT:
        return "INCORRECT"
    if val == Verdict.UNKNOWN:
        return "UNKNOWN"
    if val == Verdict.ERROR:
        return "ERROR"
    if val == Verdict.TIMEOUT:
        return "TIMEOUT"
    raise RuntimeError(f"Unknown result code: {val}")


class Verdict:
    CORRECT = 1
    INCORRECT = 2
    UNKNOWN = 3
    ERROR = 4
    TIMEOUT = 5

    def __init__(self, kind, prp, info: str = "", witness=None):
        """
        @param ty is from the enum
        """
        self._kind = kind
        self._prp = prp
        self._info = info
        self._witness = witness

    def is_correct(self):
        return self._kind == Verdict.CORRECT

    def is_incorrect(self):
        return self._kind == Verdict.INCORRECT

    def is_unknown(self):
        return self._kind == Verdict.UNKNOWN

    def is_error(self):
        return self._kind == Verdict.ERROR

    def is_timeout(self):
        return self._kind == Verdict.TIMEOUT

    def prp(self):
        return self._prp

    def info(self):
        return self._info

    def witness(self):
        return self._witness

    def __repr__(self):
        return f"Verdict({result_kind_to_str(self._kind)}, {self._prp}, {self._witness}, '{self._info[:20]}')"

    def describe(self):
        def prp_key(prp):
            return prp.key() if prp else "_any_"

        if self.is_incorrect():
            print_stdout(
                f"Property {prp_key(self.prp())} is violated\n{self.info() or ''}",
                color="red",
            )
        elif self.is_correct():
            print_stdout(
                f"Property {prp_key(self.prp())} holds\n{self.info() or ''}",
                color="green",
            )
        else:
            print_stdout(
                f"Validity of property {prp_key(self.prp())} is unknown\n{self.info() or ''}",
                color="orange",
            )
