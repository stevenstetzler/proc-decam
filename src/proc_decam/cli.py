import importlib
import argparse
import logging
import os
import sys

logging.basicConfig()
log = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("subcommand", type=str)
    parser.add_argument("--log-level", type=str, default="INFO")

    args, unknown = parser.parse_known_args()
    logging.getLogger().setLevel(getattr(logging, args.log_level.upper()))

    if args.subcommand is None:
        if "-h" in unknown or "--help" in unknown:
            parser.print_help()
            return
        parser.error("a subcommand is required")
    
    sub_idx = sys.argv.index(args.subcommand)
    prog = os.path.basename(sys.argv[0])
    sys.argv = [f"{prog} {args.subcommand}"] + sys.argv[sub_idx + 1:]

    module = importlib.import_module(f".{args.subcommand}", "proc_decam")
    if hasattr(module, "log"):
        module.log.setLevel(getattr(logging, args.log_level.upper()))
    if hasattr(module, "main"):
        module.main()
    else:
        raise RuntimeError(f"main is not defined for module proc_decam.{args.subcommand}")

if __name__ == "__main__":
    main()
