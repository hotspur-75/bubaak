# Foreword

I am Kartik, a student of Indian Institute of Technology, Delhi, working on my Bachelor 
Thesis Project under Professor Kumar Madhukar. My task is to improve the working of this
cooperative verifier, primarily by cutting down the time taken by this cooperative verifier.
Currently, my aims include - a minor task of eliminating the 'trivial splits' identified by
the authors, and a major one of implementing a dynamic dispatcher which studies the source
code of a passed split and assigns the optimal verifier. I shall be recording my progress
in this repository.

Please contact me at kartikgulia742@gmail.com in case of any queries. The original README.md
follows below.

# Changes

Started work on the minor task, and devised scripts to closely observe the working. Two kinds
of trivial splits have become apparent - splits in internal branches of functions like 
*__VERIFIER_assert()*, where one split immediately hits an error node and terminates - and cases
where the splitting is done at a location which can be seen to be trivially true, like the
first split in *lcm1_unwindbound5.c*, where the branching condition is *(counter++ < 5)*, despite
*int counter = 0* just being declared.

The first sort of trivial split can be resolved by a structural check which analyses the complexity 
of either branch. The second can be resolved with a quick value analysis, as stated by the authors  
themselves.

Successfully implemented a *Trivial Block*. This excises if-statements which are trivially true or
false, replacing it with the tautological branch. I applied this to the author's optimisation for
tautologies and fallacies in if-statement conditionals, and extended it from a simple mathematical
check to also utilising local variables. I will try extending this as much as I can safely.

Successfully implemented a split eliminator which discounts possible split locations with one or
more branches terminating within some *threshold* nodes. The definition may yet need to be tweaked,
but it seems to be working fine for now.

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
