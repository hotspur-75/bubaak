class ToolOutputParser:
    def __init__(self, store_stdout=None, store_stderr=None):
        """
        Parameters:
          store_stdout: str, opened file, or None: store stdout to the given file
          store_stderr: str, opened file, or None: store stderr to the given file
        """
        self._log_stdout = store_stdout
        self._log_stderr = store_stderr

    def log_stdout(self, line):
        if self._log_stdout is None:
            return

        # In ctor we store only the path and open it lazily only now.
        # The main reason is that the tools may overwrite or clean
        # the directory where the log is going to be on their
        # startup and this way we'll create the file only after the
        # tool were initialized
        if isinstance(self._log_stdout, str):
            self._log_stdout = open(self._log_stdout, "w")

        self._log_stdout.write(line)
        self._log_stdout.write("\n")

    def log_stderr(self, line):
        if self._log_stderr is None:
            return

        # In ctor we store only the path and open it lazily only now.
        # The main reason is that the tools may overwrite or clean
        # the directory where the log is going to be on their
        # startup and this way we'll create the file only after the
        # tool were initialized
        if isinstance(self._log_stderr, str):
            self._log_stderr = open(self._log_stderr, "w")

        self._log_stderr.write(line)
        self._log_stderr.write("\n")

    def __del__(self):
        if self._log_stdout and hasattr(self._log_stdout, "close"):
            self._log_stdout.close()
        if self._log_stderr and hasattr(self._log_stderr, "close"):
            self._log_stderr.close()
        del self

    def parse(self, line: bytes, stream: str):
        """
        Called for each line of the tool's output.
        @param stream is either "stdout" or "stderr".
        If something else None is returned,
        it is assumed to be the TaskResult()
        """
        line_s = line.decode("utf-8", "ignore")
        if stream == "stdout":
            self.log_stdout(line_s)
            return self._parse_stdout(line_s.strip())
        elif stream == "stderr":
            self.log_stderr(line_s)
            return self._parse_stderr(line_s.strip())
        else:
            raise RuntimeError(f"Unknown stream: {stream}")

    def _parse_stdout(self, _):
        return None

    def _parse_stderr(self, _):
        return None

    def finish(self, retcode):
        """Called when the tool finishes"""
        raise NotImplementedError(f"{self.__class__}.finish() not implemented")
