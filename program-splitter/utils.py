import os
import inspect
import argparse
import sys
import multiprocessing as mp

import typing
from typing import List

# Silence Git Python Warning
os.environ["GIT_PYTHON_REFRESH"] = "quiet"

# Add local dependencies

def init_local_libs():
    lib_dir = os.path.join(os.path.dirname(__file__), "lib")

    sys.dont_write_bytecode = True  # prevent creation of .pyc files
    sys.path.insert(0, lib_dir)

    if "PYTHONPATH" not in os.environ:
        os.environ["PYTHONPATH"] = ""
    os.environ["PYTHONPATH"] += os.pathsep + str(
        lib_dir
    )  # necessary so subprocesses also use libraries

    pycpa_dir = os.path.join(os.path.dirname(__file__), "pycpa")
    if os.path.exists(pycpa_dir):
        sys.path.insert(0, pycpa_dir)

        print("REGISTERED pycpa ...")
        os.environ["PYTHONPATH"] += os.pathsep + str(
            pycpa_dir
        )

# Map multiprocessing ----------------------------------------------------------------

def pmap(map_fn, data, cpu_limit = -1):

    cpu_count = mp.cpu_count()
    if cpu_limit > 0: cpu_count = min(cpu_count, cpu_limit)

    if cpu_count <= 4: # Too few CPUs for multiprocessing
        for output in map(map_fn, data):
            yield output

    with mp.Pool(processes = cpu_count) as pool:
        for output in pool.imap_unordered(map_fn, data, chunksize = 4 * cpu_count):
            yield output


# Argparser ----------------------------------------------------------------

def main(fn, argv = None, version = None):
    if argv is None: argv = sys.argv[1:]

    if "--version" in argv:
        if version is None: version = "Undetermined"
        print(f"Tool '{fn.__name__}' (Version {version})")
        return 

    signature = inspect.signature(fn)
    parser    = argparse.ArgumentParser(description = fn.__doc__)

    for name, arg in signature.parameters.items():
        argdef = [name]
        kwargdef = {}
        
        if arg.annotation is not inspect.Signature.empty:
            arg_type = arg.annotation
            if hasattr(arg_type, "__origin__") and arg_type.__origin__ is list:
                kwargdef["type"] = typing.get_args(arg_type)[0]
                kwargdef["nargs"] = "+"
            else:
                kwargdef["type"] = arg_type

        if arg.default is not inspect.Signature.empty:
            argdef[0]    = "--%s" % name
            kwargdef["default"] = arg.default
        
        if kwargdef["type"] is bool:
            action = "store_true"
            if "default" in kwargdef and kwargdef["default"]: action = "store_false"
            kwargdef["action"] = action
            del kwargdef["type"]

        parser.add_argument(*argdef, **kwargdef)
    
    args = parser.parse_args()
    return fn(**args.__dict__)