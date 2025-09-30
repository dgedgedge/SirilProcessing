"""
Command-line interface for SirilProcessing.
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

from ..core import DarkLibrary, BiasLibrary
from ..utils import extract_fits_properties


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        description="Manage FITS calibration libraries for Siril",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dark library operations
  siril-processing dark --library ~/darks list
  siril-processing dark --library ~/darks add master_dark_300s.fit --exposure 300 --temp -10
  siril-processing dark --library ~/darks find --exposure 300 --temp -10
  
  # Bias library operations
  siril-processing bias --library ~/bias list
  siril-processing bias --library ~/bias add master_bias.fit --temp 20
  siril-processing bias --library ~/bias find --binning 1x1
        """
    )
    
    # Add subparsers for dark and bias libraries
    subparsers = parser.add_subparsers(dest="library_type", help="Library type")
    
    # Dark library commands
    dark_parser = subparsers.add_parser("dark", help="Manage dark library")
    dark_parser.add_argument(
        "--library", 
        required=True,
        help="Path to dark library directory"
    )
    
    dark_commands = dark_parser.add_subparsers(dest="command", help="Command")
    
    # Dark: list
    dark_commands.add_parser("list", help="List all dark masters")
    
    # Dark: info
    dark_commands.add_parser("info", help="Show library information")
    
    # Dark: add
    dark_add = dark_commands.add_parser("add", help="Add a dark master")
    dark_add.add_argument("file", help="Path to master FITS file")
    dark_add.add_argument("--exposure", type=float, required=True, help="Exposure time in seconds")
    dark_add.add_argument("--temp", type=float, required=True, help="Temperature in Celsius")
    dark_add.add_argument("--binning", default="1x1", help="Binning mode (default: 1x1)")
    dark_add.add_argument("--gain", type=int, help="Camera gain")
    dark_add.add_argument("--offset", type=int, help="Camera offset")
    dark_add.add_argument("--camera", help="Camera model/name")
    dark_add.add_argument("--date", help="Date taken (ISO format)")
    
    # Dark: find
    dark_find = dark_commands.add_parser("find", help="Find a dark master")
    dark_find.add_argument("--exposure", type=float, required=True, help="Exposure time in seconds")
    dark_find.add_argument("--temp", type=float, help="Temperature in Celsius")
    dark_find.add_argument("--binning", default="1x1", help="Binning mode (default: 1x1)")
    dark_find.add_argument("--tolerance", type=float, default=5.0, help="Temperature tolerance (default: 5.0)")
    
    # Dark: remove
    dark_remove = dark_commands.add_parser("remove", help="Remove a dark master")
    dark_remove.add_argument("filename", help="Filename to remove")
    
    # Bias library commands
    bias_parser = subparsers.add_parser("bias", help="Manage bias library")
    bias_parser.add_argument(
        "--library",
        required=True,
        help="Path to bias library directory"
    )
    
    bias_commands = bias_parser.add_subparsers(dest="command", help="Command")
    
    # Bias: list
    bias_commands.add_parser("list", help="List all bias masters")
    
    # Bias: info
    bias_commands.add_parser("info", help="Show library information")
    
    # Bias: add
    bias_add = bias_commands.add_parser("add", help="Add a bias master")
    bias_add.add_argument("file", help="Path to master FITS file")
    bias_add.add_argument("--temp", type=float, required=True, help="Temperature in Celsius")
    bias_add.add_argument("--binning", default="1x1", help="Binning mode (default: 1x1)")
    bias_add.add_argument("--gain", type=int, help="Camera gain")
    bias_add.add_argument("--offset", type=int, help="Camera offset")
    bias_add.add_argument("--camera", help="Camera model/name")
    bias_add.add_argument("--date", help="Date taken (ISO format)")
    
    # Bias: find
    bias_find = bias_commands.add_parser("find", help="Find a bias master")
    bias_find.add_argument("--temp", type=float, help="Temperature in Celsius")
    bias_find.add_argument("--binning", default="1x1", help="Binning mode (default: 1x1)")
    bias_find.add_argument("--tolerance", type=float, default=5.0, help="Temperature tolerance (default: 5.0)")
    
    # Bias: remove
    bias_remove = bias_commands.add_parser("remove", help="Remove a bias master")
    bias_remove.add_argument("filename", help="Filename to remove")
    
    return parser


def handle_dark_commands(args) -> int:
    """Handle dark library commands."""
    library = DarkLibrary(args.library)
    
    if args.command == "list":
        masters = library.list_masters()
        if not masters:
            print("No dark masters in library")
            return 0
        
        print(f"\nDark Masters ({len(masters)} total):\n")
        print(f"{'Filename':<30} {'Exposure':<10} {'Temp':<8} {'Binning':<8}")
        print("-" * 60)
        
        for master in masters:
            props = master.get("properties", {})
            print(f"{master['filename']:<30} "
                  f"{props.get('exposure', 'N/A'):<10} "
                  f"{props.get('temperature', 'N/A'):<8} "
                  f"{props.get('binning', 'N/A'):<8}")
        
        return 0
    
    elif args.command == "info":
        info = library.get_library_info()
        print("\nDark Library Information:")
        print(f"  Path: {info['library_path']}")
        print(f"  Total Masters: {info['total_masters']}")
        print(f"  Created: {info.get('created', 'N/A')}")
        print(f"  Last Updated: {info.get('last_updated', 'N/A')}")
        
        # Show grouped by exposure
        by_exposure = library.list_by_exposure()
        if by_exposure:
            print("\n  Masters by Exposure:")
            for exposure in sorted(by_exposure.keys()):
                print(f"    {exposure}s: {len(by_exposure[exposure])} master(s)")
        
        return 0
    
    elif args.command == "add":
        success = library.add_dark_master(
            master_path=args.file,
            exposure=args.exposure,
            temperature=args.temp,
            binning=args.binning,
            gain=args.gain,
            offset=args.offset,
            camera=args.camera,
            date_taken=args.date
        )
        
        if success:
            print(f"Successfully added dark master: {Path(args.file).name}")
            return 0
        else:
            print("Failed to add dark master", file=sys.stderr)
            return 1
    
    elif args.command == "find":
        master = library.find_dark_master(
            exposure=args.exposure,
            temperature=args.temp,
            binning=args.binning,
            temperature_tolerance=args.tolerance
        )
        
        if master:
            print(f"\nFound dark master: {master['filename']}")
            print(f"  Path: {master['path']}")
            props = master.get("properties", {})
            print(f"  Exposure: {props.get('exposure')}s")
            print(f"  Temperature: {props.get('temperature')}°C")
            print(f"  Binning: {props.get('binning')}")
            if props.get("gain"):
                print(f"  Gain: {props.get('gain')}")
            if props.get("offset"):
                print(f"  Offset: {props.get('offset')}")
            return 0
        else:
            print("No matching dark master found", file=sys.stderr)
            return 1
    
    elif args.command == "remove":
        success = library.remove_master(args.filename)
        
        if success:
            print(f"Successfully removed dark master: {args.filename}")
            return 0
        else:
            print("Failed to remove dark master", file=sys.stderr)
            return 1
    
    return 1


def handle_bias_commands(args) -> int:
    """Handle bias library commands."""
    library = BiasLibrary(args.library)
    
    if args.command == "list":
        masters = library.list_masters()
        if not masters:
            print("No bias masters in library")
            return 0
        
        print(f"\nBias Masters ({len(masters)} total):\n")
        print(f"{'Filename':<30} {'Temp':<8} {'Binning':<8}")
        print("-" * 50)
        
        for master in masters:
            props = master.get("properties", {})
            print(f"{master['filename']:<30} "
                  f"{props.get('temperature', 'N/A'):<8} "
                  f"{props.get('binning', 'N/A'):<8}")
        
        return 0
    
    elif args.command == "info":
        info = library.get_library_info()
        print("\nBias Library Information:")
        print(f"  Path: {info['library_path']}")
        print(f"  Total Masters: {info['total_masters']}")
        print(f"  Created: {info.get('created', 'N/A')}")
        print(f"  Last Updated: {info.get('last_updated', 'N/A')}")
        
        # Show grouped by binning
        by_binning = library.list_by_binning()
        if by_binning:
            print("\n  Masters by Binning:")
            for binning in sorted(by_binning.keys()):
                print(f"    {binning}: {len(by_binning[binning])} master(s)")
        
        return 0
    
    elif args.command == "add":
        success = library.add_bias_master(
            master_path=args.file,
            temperature=args.temp,
            binning=args.binning,
            gain=args.gain,
            offset=args.offset,
            camera=args.camera,
            date_taken=args.date
        )
        
        if success:
            print(f"Successfully added bias master: {Path(args.file).name}")
            return 0
        else:
            print("Failed to add bias master", file=sys.stderr)
            return 1
    
    elif args.command == "find":
        master = library.find_bias_master(
            temperature=args.temp,
            binning=args.binning,
            temperature_tolerance=args.tolerance
        )
        
        if master:
            print(f"\nFound bias master: {master['filename']}")
            print(f"  Path: {master['path']}")
            props = master.get("properties", {})
            print(f"  Temperature: {props.get('temperature')}°C")
            print(f"  Binning: {props.get('binning')}")
            if props.get("gain"):
                print(f"  Gain: {props.get('gain')}")
            if props.get("offset"):
                print(f"  Offset: {props.get('offset')}")
            return 0
        else:
            print("No matching bias master found", file=sys.stderr)
            return 1
    
    elif args.command == "remove":
        success = library.remove_master(args.filename)
        
        if success:
            print(f"Successfully removed bias master: {args.filename}")
            return 0
        else:
            print("Failed to remove bias master", file=sys.stderr)
            return 1
    
    return 1


def main(argv: Optional[list] = None) -> int:
    """
    Main entry point for the CLI.
    
    Args:
        argv: Command-line arguments (default: sys.argv[1:])
        
    Returns:
        Exit code (0 for success, non-zero for error)
    """
    parser = create_parser()
    args = parser.parse_args(argv)
    
    if not args.library_type:
        parser.print_help()
        return 1
    
    if not args.command:
        parser.print_help()
        return 1
    
    try:
        if args.library_type == "dark":
            return handle_dark_commands(args)
        elif args.library_type == "bias":
            return handle_bias_commands(args)
        else:
            print(f"Unknown library type: {args.library_type}", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
