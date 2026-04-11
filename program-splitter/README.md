# Program Splitter for (GNU) C code

------------------------------------------------
> Syntactic splitter for programs written in ANSI C

This splitter is mainly designed to split verification tasks written in ANSI C. The key idea is to identify the syntactic root of the execution tree (i.e. the first branch in the programs execution) and then generate two new programs representing the different branching decisions.

*WARNING:* This is a research prototype and its applicablity might be limited. A key limitations is currently the syntactic identification of the execution tree root. For programs with complicated control flow, this might lead to unsound program splits.

## Installation
The package is tested under Python 3. It can be installed via:
```bash
$ ./install.sh
```

It is also possible to build an executable package which can be embed in other projects with:
```bash
$ ./deploy.sh
```

## Usage
The program splitter can be used as a command line tool by running:
```bash
$ python split.py [file_path] 
--left_split [left_target_path]
--right_split [right_target_path]
--allowed_unrolls [num_loop_unrolls]
```

The script can be executed on a C program written in ANSI C and given by [file_path]. The options `left_split` and `right_split` determine the path where the left and right split will be stored. The option `allowed_unrolls` is optional argument (default: -1). If it is set to a value k greater zero, loops in the program will only be unrolled for k times in the program (if we apply the splitter multiple times).


## Project Info
This is currently developed as a helper library for internal research projects. Therefore, it will only be updated as needed.

Feel free to open an issue if anything unexpected
happens. 

Distributed under the MIT license. See ``LICENSE`` for more information.