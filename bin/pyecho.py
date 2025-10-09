#!/usr/bin/env python3
"""
Simple Python script that echoes its arguments.
Can be used as a replacement for the echo command in Siril scripts.
"""

import sys

def main():
    """Echo all command line arguments."""
    if len(sys.argv) > 1:
        # Join all arguments with spaces and print them
        message = ' '.join(sys.argv[1:])
        print(message)
    else:
        # If no arguments, just print a newline (like echo without args)
        print()

if __name__ == "__main__":
    main()