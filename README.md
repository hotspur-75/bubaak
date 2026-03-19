# Bubaak

Bubaak is a set of scripts that run program verifiers in a dynamically changing
combination of sequential and parallel portfolios. A run of Bubaak on input files is defined
by a *workflow* that defines a set of initial tasks and how tasks *rewrite*
themselves upon they are finished. The goal of this architecture is to support
efficient *cooperative verification* enhanced via *(active) runtime monitoring*
of the executed verifiers to gain more control over the cooperation of tools.

Right now, the integrated verifiers are [BubaaK-LEE](https://github.com/mchalupa/bubaak-lee)
(a fork of [JetKLEE](https://github.com/staticafi/JetKlee)) and
[SlowBeast](https://gitlab.com/mchalupa/slowbeast).

### Status of the project

The task-based architecture is implemented. There will be changes to the API in the future, though.
Sharing the information is possible between some configurations of Slowbeast, but everything
else is still a work in progress, including proper monitoring of the verifiers (so far
we are reliably able to monitor only standard (error) output and the generated files)
and Bubaak-mediated information exchange.


# Building

## Using docker

```
git clone https://gitlab.com/mchalupa/bubaak
cd bubaak
git submodule update --init
docker build .
```

## Manual build

Cloning and initial setup:

```
git clone https://gitlab.com/mchalupa/bubaak
cd bubaak
git submodule update --init
make setup
```

The command `make setup` tries to get and build dependencies.
If the command for some reason fails, you can try to obtain and build
the dependencies manually:

```
## We'll need curl or wget:
apt-get install curl

## Basic dependencies
apt-get install cmake clang llvm pip

## Dependencies of BubaaK-LEE
apt-get install libsqlite3-dev libz3-dev zlib1g-dev ncurses-dev
pip install lit # to run tests with KLEE, can be skipped

## Setup build dir for  Bubaak-LEE
cd klee; mkdir build && cd build

## Configure Bubaak-LEE.
# Also, either install tcmalloc or use
# -DENABLE_TCMALLOC=off in the following command
cmake .. -DCMAKE_INSTALL_PREFIX=$(pwd)/install

## Build Bubaak-LEE
make -j4
make install
```
```
### Building slowbeast package
cd ../..
cd slowbeast
pip install z3-solver
git clone https://github.com/mchalupa/llvmlite
cd llvmlite && python3 ./setup.py build && cd ..

# that's it for slowbeast, but if you want to build a package:
pip install pyinstaller
pyinstaller sb
```

Once the project is all setup, you can build a zip with `make archive`.


### Optional dependencies
```
# To read SV-COMP .yml files on input instead of C programs
pip install pyyaml
```

# Usage

To run Bubaak with default settings, simply run
```
./bubaak program.c
```

If flags are needed to compile the program, you can use `-I` and `-D`
flags as with gcc or clang:

```
./bubaak -Iinclude_dir -DUSE_XY=1 program.c
```

Multiple C (or LLVM) files can be passed, but exactly one must contain
the `main` function (the main function can be changed via the `-entry` argument).

To run Bubaak with a selected workflow, use `-cfg workflow`:
```
./bubaak -cfg svcomp program.c
./bubaak -cfg klee program.c
```

Workflows can take arguments:
```
./bubaak -cfg 'cpachecker -svcomp -timelimit 900s' program.c
./bubaak -cfg 'slowbeast -bself' program.c
```

Note that it is up to the workflow how it interprets arguments and they
do not need to correspond directly to the arguments of the used tools.

For the full set of options use `--help`.


## Author & Support
Marek Chalupa, mchqwerty@gmail.com
