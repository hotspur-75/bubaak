#!/bin/bash

FILE="archives/instrumentor.zip"
if [ -f "$FILE" ]; then
    echo "$FILE exists. Will delete."
    rm -r "$FILE"
fi

BASEDIR="archives"
ZIPDIR="$BASEDIR/splitter"
mkdir -p $ZIPDIR

cp *.py $ZIPDIR

# Install local dependencies
echo "Installing local dependencies..."
LIBDIR="$ZIPDIR/lib"
mkdir -p $LIBDIR

pip install -t $LIBDIR --upgrade -r requirements.txt

echo "Install python lib"
pushd "$ZIPDIR"
python3 init_libs.py
rm -rf "lib/build/tree-sitter-c"
popd

# Build zip archive
echo "Zip archive"
pushd "$BASEDIR"
zip -r "splitter.zip" "splitter"
popd
rm -r "$ZIPDIR"