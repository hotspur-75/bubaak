import os
import re

from bbk.dbg import warn, dbg
from bbk.env import get_env
from bbk.properties import *
from bbk.witness import WitnessGraphML


class SVCompProperty:
    def __init__(self, prp: Property, prpfile=None, ltl=None):
        self._prp: Property = prp
        self._prpfile = prpfile
        self._ltl = ltl

    def is_unreach(self):
        return self._prp.is_unreach()

    def is_valid_deref(self):
        return self._prp.is_valid_deref()

    def is_valid_free(self):
        return self._prp.is_valid_free()

    def is_no_memleak(self):
        return self._prp.is_no_memleak()

    def is_memcleanup(self):
        return self._prp.is_memcleanup()

    def is_no_signed_overflow(self):
        return self._prp.is_no_signed_overflow()

    def is_def_behavior(self):
        return self._prp.is_def_behavior()

    def is_termination(self):
        return self._prp.is_termination()

    def descr(self):
        return self._prp.descr()

    def key(self):
        return self._prp.key()

    def error_funs(self):
        return self._prp.error_funs()

    def prpfile(self):
        return self._prpfile

    def ltl(self):
        """Is the property described by a generic LTL formula(e)?"""
        return self._ltl

    def __repr__(self):
        return f"SVCompProperty({self._prp}, {os.path.basename(self._prpfile)}, {self._ltl}"


def ltl_to_prp(prp, prpfile):
    if prp == "CHECK( init(main()), LTL(G valid-free) )":
        return SVCompProperty(PropertyValidFree(), prpfile, prp)
    if prp == "CHECK( init(main()), LTL(G valid-deref) )":
        return SVCompProperty(PropertyValidDeref(), prpfile, prp)
    if prp == "CHECK( init(main()), LTL(G valid-memtrack) )":
        return SVCompProperty(PropertyNoMemleak(), prpfile, prp)
    if prp == "CHECK( init(main()), LTL(G valid-memcleanup) )":
        return SVCompProperty(PropertyMemcleanup(), prpfile, prp)
    if prp == "CHECK( init(main()), LTL(G ! overflow) )":
        return SVCompProperty(PropertyNoSignedOverflow(), prpfile, prp)
    if prp == "CHECK( init(main()), LTL(G def-behavior) )":
        return SVCompProperty(PropertyDefBehavior(), prpfile, prp)
    if prp == "CHECK( init(main()), LTL(F end) )":
        return SVCompProperty(PropertyTermination(), prpfile, prp)

    m = re.match(
        "CHECK\\(\\s*init\\(main\\(\\)\\),\\s*LTL\\(G\\s+!\\s*call\\((.*)\\)\\)\\s*\\)",
        prp,
    )
    if m:
        return SVCompProperty(PropertyUnreach([m[1][:-2]]), prpfile, prp)

    print_stderr(f"Unknown property: {prp} (from file {prpfile})")


def get_ltl_prp(prp):
    key = prp.key()
    if prp.is_unreach():
        return [
            f"CHECK( init(main()), LTL(G ! call({error_fn}())) )"
            for error_fn in prp.error_funs()
        ]
    if prp.is_valid_deref():
        return ["CHECK( init(main()), LTL(G valid-deref) )"]
    if prp.is_valid_free():
        return ["CHECK( init(main()), LTL(G valid-free) )"]
    if prp.is_no_memleak():
        return ["CHECK( init(main()), LTL(G valid-memtrack) )"]
    if prp.is_memcleanup():
        return ["CHECK( init(main()), LTL(G valid-memcleanup) )"]
    if prp.is_no_signed_overflow():
        return ["CHECK( init(main()), LTL(G ! overflow) )"]
    if prp.is_def_behavior():
        return ["CHECK( init(main()), LTL(G def-behavior) )"]
    if prp.is_termination():
        return ["CHECK( init(main()), LTL(F end) )"]
    raise RuntimeError(f"Invalid prp: {key}")


def parse_svcomp_prps(args, env, codedirpath, yaml_spec=None):
    files = []
    properties = []

    if args.prp:
        files = args.prp
    elif yaml_spec:
        for prp in yaml_spec["properties"]:
            files.append(f"{codedirpath}/{prp['property_file']}")

    for file in files:
        absfile = file if file[0] == "/" else f"{env.cwd}/{file}"
        with open(absfile, "r") as prpfile:
            svcomp_prps = prpfile.readlines()
            for prp in svcomp_prps:
                prp = prp.strip()
                if not prp:
                    continue
                p = ltl_to_prp(prp, file)
                if p:
                    properties.append(p)

    return properties


def result_to_sv_comp(results: list, prps: list):
    if all((r.is_correct() for r in results)):
        return "true"
    for result in results:
        if result.is_incorrect():
            prp = result.prp()
            if prp.is_unreach():
                return "false(unreach-call)"
            elif prp.is_memcleanup():
                return "false(valid-memcleanup)"
            elif prp.is_valid_free():
                return "false(valid-free)"
            elif prp.is_valid_deref():
                return "false(valid-deref)"
            elif prp.is_no_memleak():
                return "false(valid-memtrack)"
            elif prp.is_no_signed_overflow():
                return "false(no-overflow)"
            elif prp.is_termination():
                return "false(termination)"
            elif prp.is_def_behavior():
                return "false(def-behavior)"
            return "false"

    rstr = ""
    for result in results:
        if rstr:
            rstr += " "
        if result.is_error():
            rstr += "error"
        if result.is_unknown():
            rstr += "unknown"
        if result.is_timeout():
            rstr += "timeout"
    return rstr


def generate_witness(results, args):
    output = (
        args.sv_comp_witness
        if args.sv_comp_witness[0] == "/"
        else f"{get_env().cwd}/{args.sv_comp_witness}"
    )
    dbg(f"SV-COMP witness output: {output}")
    dbg(f"Found witnesses: {[r.witness() for r in results]}")

    witness = None

    # FIXME
    if len(results) > 1:
        if all((result.is_correct() for result in results)):
            warn(
                "Cannot combine multiple correctness witnesses. Generating trivial witness."
            )
            witness = None
        else:
            warn("Picking a random witness, not considering witnesses for all results")
            witness = results[0].witness()
    else:
        witness = results[0].witness()

    if witness is not None and isinstance(witness, list):
        witness = [w for w in witness if isinstance(w, WitnessGraphML)]
        if len(witness) > 1:
            warn("Cannot combine multiple witnesses, picking a random one")

        if len(witness) > 0:
            witness = witness[0]
        else:
            witness = None

    if witness is not None and not isinstance(witness, WitnessGraphML):
        print_stderr(
            f"Failed generating a witness from {witness} (no graphl witness found)"
        )
        return

    assert witness is None or isinstance(witness, WitnessGraphML), witness

    from svcomp.witnesses import GraphMLWriter

    prps = []
    for result in results:
        prps.extend(get_ltl_prp(result.prp()))

    graphmlwriter = GraphMLWriter(
        args.prog[0],
        prps,
        args.pointer_bitwidth == 32,
        all((result.is_correct() for result in results)),
    )
    if witness is None:
        dbg(f"Could not find witness in '{results}', generating trivial one.")
        graphmlwriter.generate_trivial_witness()
    else:
        # we do not support witness.data atm
        assert witness.path, witness
        graphmlwriter.generate_witness(witness.path, False)
    graphmlwriter.write(output)
    dbg("Writing the GraphML witness done")


def parse_yml_input(path):
    try:
        from yaml import safe_load as yaml_safe_load, YAMLError
    except ImportError:
        warn("Cannot import from YAML package")
        return None

    with open(path, "r") as stream:
        try:
            spec = yaml_safe_load(stream)
        except YAMLError as exc:
            warn(exc)
            return None
    return spec


def svcomp_merge_memsafety_results(results):
    memsafety_bad = []
    memsafety_good = []
    new_results = []
    for r in results:
        prp = r.prp()
        # filter out memsafety results
        if prp and prp.is_valid_free() or prp.is_valid_deref() or prp.is_no_memleak():
            got_memsafety = True
            if r.is_incorrect():
                memsafety_bad.append(r)
            else:
                memsafety_good.append(r)
        else:
            new_results.append(r)

    if memsafety_bad or memsafety_good:
        if len(memsafety_bad) > 1:
            # multiple results...
            return results
        else:
            return new_results + memsafety_bad
    return new_results
