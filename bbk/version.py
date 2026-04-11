from os.path import isdir, isfile, dirname, abspath
from os import chdir, getcwd
from subprocess import run, PIPE
from sys import argv

VERSION_STRING = "0.9.3"


def get_git_version(srcdir):
    if isfile(f"{srcdir}/git-version.txt"):
        with open(f"{srcdir}/git-version.txt", "r") as f:
            line = f.readline()
            return line
    if isdir(f"{srcdir}/.git"):
        olddir = getcwd()
        chdir(srcdir)
        res = run(["git", "rev-parse", "HEAD"], stdout=PIPE)
        chdir(olddir)
        if res.returncode == 0:
            return res.stdout[:40].decode("ascii", "ignore")
    return None


def get_version():
    srcdir = abspath(dirname(argv[0]))
    gitvers = get_git_version(srcdir)
    if gitvers:
        return f"{VERSION_STRING}-{gitvers[:6]}"
    return VERSION_STRING
