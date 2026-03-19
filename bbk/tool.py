from bbk.task.processtask import ProcessTask


class Tool(ProcessTask):
    def __init__(self, exe, inputs, args=None, name=None, envir=None, timeout=None):
        super().__init__(timeout=timeout, name=name, envir=envir)
        self._exe = exe
        self._args = args or []
        self._inputs = inputs
        self._add_options = None

    def exe(self):
        return self._exe

    def args(self):
        return self._args

    def resultsdir(self):
        return None

    def add_options(self, opts):
        self._add_options = opts

    def cmd(self):
        return [self._exe] + self._args + (self._add_options or []) + self._inputs

    def cleanup(self):
        """Override for running some cleanup after running the tool."""
        pass
