from importlib import import_module
from os import makedirs, listdir
from os.path import exists, isdir, isfile, basename
from urllib.request import urlretrieve
from tempfile import mkdtemp
from shutil import move as move_file

from benchexec.tooladapter import create_tool_locator, CURRENT_BASETOOL
from benchexec.util import ProcessExitCode
from yaml import safe_load as yaml_safe_load, YAMLError
import re

from bbk.dbg import dbg
from bbk.env import get_env
from bbk.properties import PropertiesSet, Property
from bbk.task.aggregatetask import AggregateTask
from bbk.task.processtask import ProcessTask
from bbk.task.result import TaskResult
from bbk.utils import find_file_in_path, _popen
from bbk.verdict import Verdict

CACHEDIR = f"{get_env().srcdir}/svcomptools"


class Download(ProcessTask):
    """
    Download a file. This task uses `wget` or `curl`
    and thus is suitable for larger files that we want to download
    asynchronously. For small files that are downloaded instanteously,
    you can use the python urllib module that blocks util the file
    is downloaded (but that is not a problem if it takes say milliseconds).
    """

    def __init__(self, url, path):
        """
        Download file given by `url` and save it to `path`.
        """
        super().__init__()

        self._url = url
        self._path = path

        exe = find_file_in_path("curl")
        if exe is None:
            exe = find_file_in_path("wget")
            self._cmd = [exe, url, "-o", path]
        else:
            self._cmd = [exe, "-LR", url, "-o", path]

    def cmd(self):
        return self._cmd

    def finish(self):
        result = super().finish()
        if result.status == "DONE":
            return TaskResult("DONE", output=self._path)
        return result


class Unzip(ProcessTask):
    """
    Unzip a file. This task uses `unzip` program.
    """

    def __init__(self, zipfile, path, opts=None):
        """
        Download file given by `url` and save it to `path`.
        """
        super().__init__(name="unzip")

        self._zipfile = zipfile
        self._path = path

        exe = find_file_in_path("unzip")
        if exe is None:
            raise RuntimeError("unzip not found")
        else:
            self._cmd = [exe] + (opts or []) + [zipfile, "-d", path]

    def cmd(self):
        return self._cmd

    def finish(self):
        result = super().finish()
        if result.status == "DONE":
            return TaskResult("DONE", output=self._path)
        return result


def get_tool_yml_file(toolname, use_cache):
    """
    Download the SV-COMP tool's yaml file. It is small,
    so do it directy via python, not via a task.
    """
    path = f"{CACHEDIR}/{toolname}.yml"
    if use_cache and exists(path):
        return path

    urlretrieve(
        f"https://gitlab.com/sosy-lab/benchmarking/fm-tools/-/raw/main/data/{toolname}.yml",
        path,
    )
    assert exists(path)
    return path


def get_tool_yml(toolname, use_cache):
    path = get_tool_yml_file(toolname, use_cache)
    with open(path, "r") as f:
        try:
            yml = yaml_safe_load(f)
        except YAMLError as exc:
            yml = None

    return yml


def get_tool_url(yml, toolname, year=None):
    if year is None:
        # use the top-most version which is likely
        # the newest
        data = yml["versions"][0]
    else:
        RuntimeError("Selecting a year is not implemented yet")

    doi_url = f"https://doi.org/{data['doi']}"

    # XXX: Doesn't work for some reason
    # with urlopen(doi_url) as url:
    #    return url.geturl()
    p = _popen(["curl", doi_url])
    stdout, stderr = p.communicate()
    assert p.returncode == 0
    r = re.search(b'href=".*zenodo\.(.*)"', stdout)
    assert r
    identifier = int(r[1])
    return f"https://zenodo.org/api/records/{identifier}/files-archive"


def prp_to_file(prp: Property):
    if prp.is_unreach():
        return "unreach-call.prp"
    if prp.is_memcleanup():
        return "valid-memcleanup.prp"
    if prp.is_memsafety():
        return "valid-memsafety.prp"
    if prp.is_termination():
        return "termination.prp"
    if prp.is_def_behavior():
        return "def-behavior.prp"
    if prp.is_no_signed_overflow():
        return "no-overflow.prp"

    raise RuntimeError("Unknown property")


class GetSVCompTool(AggregateTask):
    def __init__(self, toolname, properties, year=None, use_cache=True):
        super().__init__([])
        self._use_cache = use_cache
        self._year = year
        self._toolname = toolname
        self._properties = PropertiesSet(*properties)
        self._zipfile = f"{CACHEDIR}/{toolname}.zip"
        self._tooldir = f"{CACHEDIR}/{toolname}"

        assert self._properties.is_single()
        self._prpfile = f"{CACHEDIR}/{prp_to_file(self._properties.get_single())}"

        makedirs(CACHEDIR, exist_ok=True)

    def execute(self):
        toolname = self._toolname

        # TODO: move this into its own task?
        if not self._use_cache or not exists(self._prpfile):
            prpfile = prp_to_file(self._properties.get_single())
            dld = Download(
                f"https://gitlab.com/sosy-lab/benchmarking/sv-benchmarks/-/raw/main/c/properties/{prpfile}",
                self._prpfile,
            )
            self.add_subtask(dld)

        if self._use_cache and isdir(f"{CACHEDIR}/{toolname}"):
            dbg("GetSVCompTool: the tool is in cache")
            return

        def unzip_cb(ev, task, result):
            # we unzipped a zip that contains a zip
            # into a temporary directory. Unzipped the zip to our cache.
            listdir(result.output)
            tmpdir = result.output
            unzipped_dir = f"{tmpdir}/unzipped"

            def move_unzipped_file(result):
                if result.is_done():
                    flname = listdir(unzipped_dir)[0]
                    dbg(f"Moving {unzipped_dir}/{flname} -> {self._tooldir}")
                    move_file(f"{unzipped_dir}/{flname}", self._tooldir)
                return result

            for fl in listdir(tmpdir):
                if fl.endswith(".zip"):
                    unzip = (
                        Unzip(f"{result.output}/{fl}", unzipped_dir)
                        >> move_unzipped_file
                    )
                    self.add_subtask(unzip)
                    return
            raise RuntimeError("Downloaded zip has wrong structure")

        if not self._use_cache or not exists(self._zipfile):
            yml = get_tool_yml(toolname, self._use_cache)
            url = get_tool_url(yml, self._toolname)

            dbg(f"Downloading {self._toolname} from {url}")
            dld = Download(url, self._zipfile)
            self.add_subtask(dld)

            def finish_cb(ev: str, task, result):
                assert ev == "finish", ev
                # we downloaded, so we need to also unzip
                unzip = Unzip(self._zipfile, mkdtemp(dir="/tmp"))
                unzip.add_event_listener("finish", self, unzip_cb)
                self.add_subtask(unzip)

            dld.add_event_listener("finish", self, finish_cb)
        else:
            if not self._use_cache or not isdir(f"{CACHEDIR}/{toolname}"):
                # use the Unzip task instead of the python zip module, because the
                # python module removes file permissions
                unzip = Unzip(self._zipfile, mkdtemp(dir="/tmp"))
                unzip.add_event_listener("finish", self, unzip_cb)
                self.add_subtask(unzip)

    def aggregate(self, task, result):
        if not result.is_done():
            return result

    def finish(self):
        if not isdir(f"{CACHEDIR}/{self._toolname}"):
            return TaskResult("ERROR", "Failed getting the SV-COMP tool")

        return TaskResult("DONE", output=f"{CACHEDIR}/{self._toolname}")


def svcomp_result_to_verdict(result, property):
    if result.startswith("false"):
        return Verdict(Verdict.INCORRECT, property, info=result)
    if result.startswith("true"):
        return Verdict(Verdict.CORRECT, property, info=result)

    if result.lower().startswith("error"):
        return Verdict(Verdict.ERROR, property, info=result)

    return Verdict(Verdict.UNKNOWN, property, info=result)


class BenchexecConfig:
    def __init__(self, toolname):
        self.tool_directory = f"{CACHEDIR}/{toolname}"


class SVCompTool(ProcessTask):
    instance_counter = 0

    def __init__(self, toolname: str, programs, properties, args=None, timeout=None, bitwidth = 32):
        self._resultsdir = (
            f"{get_env().workdir}/svcomptool-{SVCompTool.instance_counter}-{toolname}"
        )
        SVCompTool.instance_counter += 1
        self._tooldir = f"{CACHEDIR}/{toolname}"

        self._inputs = [p.path for p in programs]

        self._output = []
        self._properties = PropertiesSet(*properties)
        assert self._properties.is_single()
        assert isdir(self._tooldir), f"Missing {self._tooldir}"
        self._prpfile = f"{CACHEDIR}/{prp_to_file(self._properties.get_single())}"
        assert isfile(self._prpfile)

        yml = get_tool_yml(toolname, use_cache=True)
        toolinfo = yml["benchexec_toolinfo_module"]
        if toolinfo.endswith(".py"):
            toolinfo = f"benchexec.tools.{basename(toolinfo)[:-3]}"
        elif not toolinfo.startswith("benchexec."):
            toolinfo = f"benchexec.tools.{toolname}"

        # FIXME: get the name of module from YML
        benchexec_mod = import_module(toolinfo)
        self._benchexec_tool = benchexec_mod.Tool()
        exe = self._benchexec_tool.executable(
            create_tool_locator(BenchexecConfig(toolname))
        )

        # TODO
        rlimits = CURRENT_BASETOOL.ResourceLimits(walltime=timeout or 0)
        # assume the top-most version is the current one
        options = yml["versions"][0]["benchexec_toolinfo_options"] + (args or [])

        task_options = {"language": "C", "data_model": "LP64" if bitwidth == 64 else "ILP32"}
        task = CURRENT_BASETOOL.Task(
            [p.path for p in programs], None, self._prpfile, task_options
        )

        cmd = self._benchexec_tool.cmdline(exe, options, task, rlimits)
        super().__init__(
            cmd,
            name=f"svcomptool-{toolname}",
            # timeout=timeout,
            envir=None,
            cwd=self._tooldir,
        )

        # track the output of the tool so that we can determine the result
        def gather_output(ev, line):
            self._output.append(line.decode("utf-8"))

        self.add_event_listener("line-stderr", self, gather_output)
        self.add_event_listener("line-stdout", self, gather_output)

    def get_verdict(self):
        run = CURRENT_BASETOOL.Run(
            cmdline=self.cmd(),
            exit_code=ProcessExitCode(
                value=self.retval().retval, signal=None, raw=self.retval().retval
            ),
            output=CURRENT_BASETOOL.RunOutput(lines=self._output),
            termination_reason="done",
        )
        result = svcomp_result_to_verdict(
            self._benchexec_tool.determine_result(run), self._properties.get_single()
        )
        return [result]

    def finish(self):
        super().finish()  # get the retval
        return TaskResult("DONE", output=self.get_verdict(), task=self)
