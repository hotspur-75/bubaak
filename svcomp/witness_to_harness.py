from hashlib import sha256 as hashfunc
import datetime

no_lxml = False
try:
    from lxml import etree as ET
except ImportError:
    no_lxml = True
if no_lxml:
    # if this fails, then we're screwed, so let the script die
    from xml.etree import ElementTree as ET


HARNESS_PATTERN = """
#include <assert.h>
void abort(void) __attribute__((noreturn));
void exit(int) __attribute__((noreturn));
void __VERIFIER_error(void) { assert(0 && "__VERIFIER_error called"); }
void __VERIFIER_assume(int c) { assert(c && "__VERIFIER_assume(0) called"); }
"""

BODY = """
%s {
        static int pos = 0;
        switch(pos++) {
                %s
                default: return 0;
        }
}
"""

ENTRY = "case %d: return %s;\n"

SIGNATURES = {
    "__VERIFIER_nondet_int": "int __VERIFIER_nondet_int(void)",
    "__VERIFIER_nondet_char": "char __VERIFIER_nondet_char(void)",
    "__VERIFIER_nondet_uchar": "unsigned char __VERIFIER_nondet_uchar(void)",
    "__VERIFIER_nondet_short": "short __VERIFIER_nondet_short(void)",
    "__VERIFIER_nondet_ushort": "unsigned short __VERIFIER_nondet_ushort(void)",
    "__VERIFIER_nondet_int": "int __VERIFIER_nondet_int(void)",
    "__VERIFIER_nondet_uint": "unsigned int __VERIFIER_nondet_uint(void)",
    "__VERIFIER_nondet_long": "long __VERIFIER_nondet_long(void)",
    "__VERIFIER_nondet_ulong": "unsigned long __VERIFIER_nondet_ulong(void)",
    "__VERIFIER_nondet_longlong": "long long __VERIFIER_nondet_longlong(void)",
    "__VERIFIER_nondet_ulonglong": "unsigned long long __VERIFIER_nondet_ulonglong(void)",
    "__VERIFIER_nondet_float": "float __VERIFIER_nondet_float(void)",
    "__VERIFIER_nondet_double": "double __VERIFIER_nondet_double(void)",
    "__VERIFIER_nondet_bool": "_Bool __VERIFIER_nondet_bool(void)",
    # FIXME: Add more signatures
}

# Read graphml format --------------------------------


class Node:
    def __init__(self, node_id, attributes=None):
        self.node_id = node_id
        self.attributes = attributes


class Edge:
    def __init__(self, src_id, target_id, attributes=None):
        self.src_id = src_id
        self.target_id = target_id
        self.attributes = attributes


class Graph:
    def __init__(self):
        self.nodes = {}
        self.fwd_edges = {}
        self.bwd_edges = {}

    def register_node(self, node):
        self.nodes[node.node_id] = node

    def register_edge(self, edge):
        if edge.src_id not in self.fwd_edges:
            self.fwd_edges[edge.src_id] = {}
        self.fwd_edges[edge.src_id][edge.target_id] = edge

        if edge.target_id not in self.bwd_edges:
            self.bwd_edges[edge.target_id] = {}
        self.bwd_edges[edge.target_id][edge.src_id] = edge

    def index(self, attrib_name, target=None):
        for node in self.nodes.values():
            if attrib_name in node.attributes:
                if target is None or target == node.attributes[attrib_name]:
                    return node
        return None

    def next(self, node):
        for edge in self.fwd_edges.get(node.node_id, {}).values():
            yield edge

    def previous(self, node):
        for edge in self.bwd_edges.get(node.node_id, {}).values():
            yield edge


def _build_node(xml_node):
    node_id = xml_node.attrib["id"]

    attributes = {}
    for child in xml_node.iter():
        if child.tag.endswith("data"):
            key = child.attrib["key"]
            value = child.text
            attributes[key] = value

    return Node(node_id, attributes)


def _build_edge(xml_node):
    source_id = xml_node.attrib["source"]
    target_id = xml_node.attrib["target"]

    attributes = {}
    for child in xml_node.iter():
        if child.tag.endswith("data"):
            key = child.attrib["key"]
            value = child.text
            attributes[key] = value

    return Edge(source_id, target_id, attributes)


def _build_automata(xml_root):
    automata = Graph()
    for node in xml_root.iter():
        if node.tag.endswith("node"):
            graph_node = _build_node(node)
            automata.register_node(graph_node)

    for node in xml_root.iter():
        if node.tag.endswith("edge"):
            graph_edge = _build_edge(node)
            automata.register_edge(graph_edge)

    return automata


# ----------------------------------------------------


def _traverse_automata(automata):
    entry_node = automata.index("entry", "true")
    if entry_node is None:
        raise ValueError("Witness automata has no entry node.")

    violation_node = automata.index("violation", "true")
    if violation_node is None:
        raise ValueError("Witness automata has no violation node.")

    seen_ids = set()

    stack = [(violation_node, ())]
    while len(stack) > 0:
        current_node, path = stack.pop(-1)
        if current_node == entry_node:
            return path[::-1]

        if current_node.node_id in seen_ids:
            raise ValueError("Cycle detected in violation witness")
        seen_ids.add(current_node.node_id)

        for edge in automata.previous(current_node):
            stack.append((automata.nodes[edge.src_id], path + (edge,)))


def convert_executable_witness_to_harness(path_to_witness):
    graphml = ET.parse(path_to_witness)
    root = graphml.getroot()

    witness_automata = _build_automata(root)

    assumptions = {}

    for edge in _traverse_automata(witness_automata):
        edge_attrib = edge.attributes
        if "assumption" not in edge_attrib:
            continue
        assumption = edge_attrib["assumption"]
        resultfn = edge_attrib["assumption.resultfunction"]

        if resultfn not in assumptions:
            assumptions[resultfn] = []

        assumption = assumption.replace("\\result==", "")
        assumptions[resultfn].append(assumption)

    result = [HARNESS_PATTERN]
    for resultfn, results in assumptions.items():
        if resultfn not in SIGNATURES:
            raise ValueError(
                "Function %s is currently not supported for witness generation"
                % resultfn
            )
        cases = [ENTRY % (i, res) for i, res in enumerate(results)]
        signature = SIGNATURES[resultfn]
        implementation = BODY % (signature, "".join(cases))
        result.append(implementation)

    return "\n".join(result)
