#!/bin/bash

# Install local dependencies
echo "Installing local dependencies..."
LIBDIR="lib"
mkdir -p $LIBDIR

pip install -t $LIBDIR --upgrade -r requirements.txt

echo "Install python lib"
python3 init_libs.py
rm -rf "lib/build/tree-sitter-c"