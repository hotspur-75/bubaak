import argparse
from os import getcwd
from os.path import abspath, join, dirname

from bbk.compiler import CompilationUnit
from bbk.version import get_version
from bbk.utils import err
from bbk.dbg import set_debugging, warn, start_workflow_log
from svcomp.helpers import parse_yml_input


def create_arg_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("prog", nargs="+", help="program to be analyzed")
    parser.add_argument(
        "-version",
        "--version",
        action="version",
        version=get_version(),
        help="Show version",
    )
    parser.add_argument("-dbg", action="store_true", help="write debugging messages")
    parser.add_argument(
        "-save-files", action="store_true", help="do not delete generated files"
    )
    parser.add_argument("-entry", default="main", help="entry function")
    parser.add_argument(
        "-cfg",
        "-workflow",
        default="default",
        dest="workflow",
        metavar="NAME_OR_FILE",
        help="A configuration to use. May override other arguments. "
        "The argument is either a name of the configuration (if it is named) or a file.",
    )
    parser.add_argument(
        "-timeout", type=int, default=None, help="Set timeout (in seconds)"
    )
    parser.add_argument("-out-dir", default="sb-out", help="Directory for output files")
    parser.add_argument("-dbgv", action="store_true", help="Shortcut for -dbg -v")
    parser.add_argument("-dbgvv", action="store_true", help="Shortcut for -dbg -vv")
    parser.add_argument(
        "-exec-witness", help="Generate executable witness into the given file"
    )
    parser.add_argument(
        "-harness", action="store", help="Generate test harness for found bugs"
    )
    parser.add_argument(
        "-I",
        action="append",
        metavar="DIR",
        help="Add an include directory (can be used multiple times)",
    )
    parser.add_argument(
        "-32",
        "--32",
        "-m32",
        action="store_true",
        help="Set the bitwidth of pointers to 32 bits",
    )
    parser.add_argument(
        "-64",
        "--64",
        action="store_true",
        help="Set the bitwidth of pointers to 64 bits (default)",
    )
    parser.add_argument(
        "-pointer-bitwidth",
        action="store",
        type=int,
        help="Set the bitwidth of pointers",
    )
    parser.add_argument(
        "-prp",
        "--prp",
        action="append",
        metavar="PRP",
        help="The property to check. Can be given as a keyword or .prp file with SV-COMP LTL spec.",
    )

    parser.add_argument(
        "-D",
        action="append",
        metavar="OPT",
        help="Parameter to pass to the configuration",
    )

    parser.add_argument(
        "-X",
        action="append",
        metavar="OPT",
        help="Parameter to pass to the tools ran by configuration",
    )

    parser.add_argument(
        "-error-fn",
        "--error-fn",
        action="append",
        metavar="FUN",
        help="Set error function (can be used multiple times)",
    )
    parser.add_argument(
        "-sv-comp",
        "--sv-comp",
        action="store_true",
        help="Assume intput and generate output for SV-COMP",
    )
    parser.add_argument(
        "-sv-comp-witness",
        "--sv-comp-witness",
        action="store",
        default=f"{getcwd()}/witness.graphml",
        help="The path to store the witness to",
    )

    return parser


def parse_arguments():
    parser = create_arg_parser()
    args = parser.parse_args()

    bw32 = ("32", True) in args._get_kwargs()
    bw64 = ("64", True) in args._get_kwargs()

    if args.timeout is not None and args.timeout == 0:
        err(f"Invalid timeout: {args.timeout}")

    if args.pointer_bitwidth is not None:
        if bw32 or bw64:
            err("Only one of -32, -64, or -pointer-bitwidth is expected")
    else:
        args.pointer_bitwidth = 64  # default

    if bw32 and bw64:
        err("Only one of -32, -64, or -pointer-bitwidth is expected")

    if bw32:
        # dbg("Pointer bitwidth: 32 bits")
        args.pointer_bitwidth = 32

    # map programs to absolute paths
    cwd = getcwd()
    args.prog = [
        prog if prog[0] == "/" else abspath(join(cwd, prog)) for prog in args.prog
    ]
    if args.I:
        args.I = [d if d[0] == "/" else abspath(join(cwd, d)) for d in args.I]
    args.sv_comp_witness = (
        args.sv_comp_witness
        if args.sv_comp_witness[0] == "/"
        else abspath(join(cwd, args.sv_comp_witness))
    )

    args.X = args.X or []
    args.D = args.D or []

    def get_D(var):
        for d in args.D:
            dd = d.split("=")
            if dd[0] == var:
                return (dd[0], dd[1] if len(dd) > 1 else None)
        return None

    def get_D_value(var):
        x = get_D(var)
        if isinstance(x, tuple):
            return x[1]
        return None

    args.get_D = get_D
    args.get_D_value = get_D_value

    return args


def setup_debugging(args):
    if args.sv_comp:
        args.dbgv = True

    if args.dbgvv:
        set_debugging(3)
    elif args.dbgv:
        set_debugging(2)
    elif args.dbg:
        set_debugging(1)

    start_workflow_log(args)


def get_source_files(args):
    programs = []
    for prog in args.prog:
        if prog.endswith(".yml"):
            yaml_spec = parse_yml_input(prog)
            if yaml_spec is None:
                raise RuntimeError(f"Failed parsing {prog}")
            files = yaml_spec["input_files"]  # it is one file right now...
            path = f"{(abspath(dirname(prog)))}/{files}"
            if int(yaml_spec["options"]["data_model"][-2:]) != args.pointer_bitwidth:
                warn(
                    "YAML input file has different architecture: "
                    f"{yaml_spec['options']['data_model']} != ILP{args.pointer_bitwidth}"
                )
            programs.append(CompilationUnit(path, yaml_spec["options"]["language"]))
        elif prog.endswith(".c") or prog.endswith(".i"):
            programs.append(CompilationUnit(prog, lang="C"))
        elif prog.endswith(".bc") or prog.endswith(".ll"):
            programs.append(CompilationUnit(prog, lang="llvm"))
        else:
            raise RuntimeError(f"Unsupported input file: {prog}")

    return programs
