from subprocess import Popen, PIPE
from .dbg import print_stderr
from .env import get_env
from os.path import isfile
from os import environ


def err(msg):
    print_stderr(msg, color="RED")
    exit(1)


def find_file_in_dirs(name, dirs):
    for path in dirs:
        pathfile = f"{path}/{name}"
        if isfile(pathfile):
            return pathfile
    if dirs:
        if isfile(pathfile):
            return pathfile

    return None


def find_program(name, dirs=None):
    if dirs:
        pathfile = find_file_in_dirs(name, dirs)
    if pathfile is None:
        tmp = f"{get_env().srcdir}/{name}/bin/{name}"
        if isfile(tmp):
            return tmp

        pathfile = find_file_in_path(name)

    return pathfile


def find_file_in_path(name):
    return find_file_in_dirs(name, environ["PATH"].split(":"))


def _popen(cmd, env=None, cwd=None):
    try:
        p = Popen(cmd, stdout=PIPE, stderr=PIPE, env=env, cwd=cwd)
    except FileNotFoundError as e:
        print_stderr(str(e), color="red")
        return None
    return p
