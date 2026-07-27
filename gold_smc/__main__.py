import sys

from .launcher import main

if __name__ == "__main__":
    sys.argv.insert(1, "analysis")
    main()
