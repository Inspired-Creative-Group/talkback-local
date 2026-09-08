"""Compatibility shim (one release): the code lives in talkback.chunk."""
import sys

from talkback.chunk import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
