import argparse

import os
from glob import glob
from tqdm import tqdm

import subprocess
import time

import numpy as np

def execute(command, timeout = 10):
    start_time = time.time()
    #print(" ".join(command))
    process = subprocess.Popen(command, shell = False, stdout=subprocess.PIPE, stderr = subprocess.PIPE)
    
    try:
        stdout, stderr = process.communicate(timeout = timeout)
        end_time = time.time()
        return end_time - start_time
    except subprocess.TimeoutExpired:
        process.kill()
        end_time = time.time()
        return end_time - start_time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_dir")
    args = parser.parse_args()

    files = glob(os.path.join(args.input_dir, "**", "*.c"), recursive = True)
    files = sorted(files, key = lambda x: os.stat(x).st_size)

    moving_average = [0]*100

    pbar = tqdm(files)
    for file in pbar:
        with open(file, "r") as lines:
            num_lines = sum(1 for _ in lines)

        moving_average.pop(0)
        moving_average.append(num_lines)
        average, std = np.mean(moving_average), np.std(moving_average)

        zscore = (num_lines - average) / std
        pbar.set_description("Lines: %d, ZScore: %.2f" % (num_lines, zscore))

        if abs(zscore) < 1.0: continue

        try:
            exec_time = execute(["python3", "split.py", file, "--left_split", "l.c", "--right_split", "r.c"])
            print(num_lines, exec_time)
        except Exception as e:
            print(e)
            continue

if __name__ == '__main__':
    main()