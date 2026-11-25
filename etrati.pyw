"""Starts ETRATI from anywhere (Explorer starts programs in the folder of the opened file)."""
import multiprocessing
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from furceli.__main__ import main
    sys.exit(main())
