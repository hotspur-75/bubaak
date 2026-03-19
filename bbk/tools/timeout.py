from bbk.verdict import Verdict
from bbk.utils import _popen
from bbk.tool import ToolOutputParser, Tool


class TimeoutParser(ToolOutputParser):
    def __init__(self):
        self._finished = False

    def parse(self, line, stream):
        pass

    def finish(self, _):
        self._finished = True

    def result(self):
        if self._finished:
            return [Verdict(Verdict.UNKNOWN, None, "timeout")]
        return None


class Timeout(Tool):
    def __init__(self, args=None, parser=None, name="timeout"):
        assert isinstance(args, int), args
        super().__init__("sleep", [str(args)], parser or TimeoutParser(), name)

    def start(self, progs, add_options=None):
        cmd = [self.exe(), self._args[0]] + (add_options or [])
        print(f"## Run {self.name()}")
        print("#", " ".join(cmd))
        self._proc = _popen(cmd)
        return self._proc
