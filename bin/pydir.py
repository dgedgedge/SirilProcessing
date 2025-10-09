#!/usr/bin/env python3
"""
Python script that lists directory contents.
Can be used as a replacement for the dir command in Siril scripts.
Provides cross-platform directory listing functionality.
"""

import os
import sys
from pathlib import Path

def main():
    """List directory contents."""
    # Determine which directory to list
    if len(sys.argv) > 1:
        # Use the first argument as the directory path
        directory = sys.argv[1]
    else:
        # Use current directory if no argument provided
        directory = "."
    
    try:
        directory_path = Path(directory)
        
        # Check if the directory exists
        if not directory_path.exists():
            print(f"Directory not found: {directory}", file=sys.stderr)
            sys.exit(1)
        
        if not directory_path.is_dir():
            print(f"Not a directory: {directory}", file=sys.stderr)
            sys.exit(1)
        
        # List directory contents
        print(f"Directory listing of {directory_path.resolve()}:")
        
        # Get all items in the directory
        items = list(directory_path.iterdir())
        
        if not items:
            print("  (empty directory)")
            print("Total: 0 directories, 0 files")
        else:
            # Sort items: directories first, then files
            directories = [item for item in items if item.is_dir()]
            files = [item for item in items if item.is_file()]
            
            # Sort each category alphabetically
            directories.sort(key=lambda x: x.name.lower())
            files.sort(key=lambda x: x.name.lower())
            
            # Display directories first
            for item in directories:
                print(f"  {item.name}/")
            
            # Display files
            for item in files:
                try:
                    size = item.stat().st_size
                    if size < 1024:
                        size_str = f"{size} B"
                    elif size < 1024*1024:
                        size_str = f"{size/1024:.1f} KB"
                    elif size < 1024*1024*1024:
                        size_str = f"{size/(1024*1024):.1f} MB"
                    else:
                        size_str = f"{size/(1024*1024*1024):.1f} GB"
                    
                    print(f"  {item.name:<30} {size_str:>10}")
                except (OSError, PermissionError):
                    print(f"  {item.name}")
                    
            print(f"Total: {len(directories)} directories, {len(files)} files")
        
    except PermissionError:
        print(f"Permission denied: {directory}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error listing directory {directory}: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()