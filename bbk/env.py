from os import chdir, getcwd, environ
from os.path import abspath, dirname
from shutil import rmtree
from tempfile import mkdtemp
from time import clock_gettime, CLOCK_REALTIME


class Environment:
    def __init__(self):
        self.workdir = None
        self.srcdir = None
        self.cwd = None
        self.start_time = None


_global_env = None


def get_env():
    global _global_env
    assert _global_env
    return _global_env


def init_env(bubaak_path):
    global _global_env
    assert _global_env is None
    _global_env = Environment()
    _global_env.start_time = clock_gettime(CLOCK_REALTIME)
    _global_env.cwd = getcwd()
    _global_env.srcdir = abspath(dirname(bubaak_path))
    _global_env.workdir = mkdtemp(dir="/tmp/", prefix="bubaak.")

    if "LD_LIBRARY_PATH" in environ:
        environ[
            "LD_LIBRARY_PATH"
        ] = f"{_global_env.srcdir}/lib:{environ['LD_LIBRARY_PATH']}"
    else:
        environ["LD_LIBRARY_PATH"] = f"{_global_env.srcdir}/lib"


def cleanup_env():
    chdir("/tmp")
    rmtree(get_env().workdir)


def change_to_env():
    env = get_env()
    chdir(env.workdir)
