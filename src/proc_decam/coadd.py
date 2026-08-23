"""
Aggregate over available input warps to make a coadd

This should select input warps with the where query and collections
Associate those into {coadd_name}/warps
Make a new chained collection {coadd_name} and execute.py on the input
"""
import parsl
from parsl import bash_app
from parsl.executors import HighThroughputExecutor
from functools import partial
from .parsl import run_command, EpycProvider, KloneAstroProvider, KloneA40Provider
from subprocess import Popen, PIPE
import selectors
import sys
import atexit

processes = []

def cleanup():
    for p in processes:
        p.kill()

atexit.register(cleanup)

def popen(*args, **kwargs):
    global processes
    p = Popen(*args, **kwargs)
    # p = Popen("echo", **kwargs)
    print("popen: " + " ".join(*args), file=sys.stderr)
    processes.append(p)
    return p

def _print(p):
    sel = selectors.DefaultSelector()
    sel.register(p.stdout, selectors.EVENT_READ)
    sel.register(p.stderr, selectors.EVENT_READ)

    while True:
        for key, _ in sel.select():
            data = key.fileobj.read1().decode()
            if not data:
                return p
            if key.fileobj is p.stdout:
                print(data, end="")
            else:
                print(data, end="", file=sys.stderr)

def run_and_pipe(*args, **kwargs):
    if 'stdout' not in kwargs:
        kwargs['stdout'] = PIPE
    if 'stderr' not in kwargs:
        kwargs['stderr'] = PIPE
    p = popen(*args, **kwargs)
    return _print(p)

pipeline_lookup = {
    "mean": "mean-template.yaml",
    "median": "median-template.yaml",
    "meanclip": "meanclip-template.yaml",
    "min": "min-template.yaml",
    "": "template.yaml",
    None: "template.yaml",
}

def main():
    # loop over subsets?
    import argparse
    import os

    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("subset")
    parser.add_argument("--template-type", default="")
    parser.add_argument("--coadd-subset", default="")
    parser.add_argument("--warp-coadd-name", default="deep")
    parser.add_argument("--where")
    parser.add_argument("--collections", nargs="+", default=[])
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--slurm", action="store_true")
    parser.add_argument("--pipeline-slurm", action="store_true")
    parser.add_argument("--provider", default="EpycProvider")
    parser.add_argument("--workers", "-J", type=int, default=4)
    parser.add_argument("--debug", action="store_true")
    
    args = parser.parse_args()

    htex_label = "htex"
    executor_kwargs = dict()
    
    if args.slurm:
        provider = KloneA40Provider(max_blocks=args.workers)
    else:
        provider = EpycProvider(max_blocks=1)
        executor_kwargs = dict(
            max_workers_per_node=args.workers
        )
    
    executor_kwargs['provider'] = provider
    config = parsl.Config(
        executors=[
            HighThroughputExecutor(
                label=htex_label,
                **executor_kwargs,
            )
        ],
        run_dir=os.path.join("runinfo", "coadd"),
    )
    parsl.load(config)

    inputs_collection = os.path.normpath(f"{args.coadd_subset}/{args.template_type}/coadd/inputs")

    futures = [] # chage to dictionary
    inputs = []
    for dataset in [f"{args.warp_coadd_name}Coadd_directWarp", f"{args.warp_coadd_name}Coadd_psfMatchedWarp", "preSourceTable_visit", "finalized_src_table"]:
    # associate dataset
        cmd = [
            "proc-decam",
            "associate",
            args.repo,
            inputs_collection,
            "--collections", f"{args.subset}/drp",
            "--datasets", dataset
        ]
        cmd = " ".join(map(str, cmd))
        func = partial(run_command)
        setattr(func, "__name__", f"associate_{dataset}")
        future = bash_app(func)(cmd, inputs=inputs)
        futures.append(future)
    
    inputs = [f for f in futures]
    cmd = [
        "proc-decam",
        "collection",
        args.repo,
        "coadd",
        args.coadd_subset,
    ]
    cmd += ["--template-type", args.template_type] if args.template_type else []
    cmd = " ".join(map(str, cmd))
    func = partial(run_command)
    setattr(func, "__name__", f"collection")
    future = bash_app(func)(cmd, inputs=inputs)
    inputs = [future]
    futures.append(future)
        
    # execute coadd
    # cmd = [
    #     "proc-decam",
    #     "execute",
    #     args.repo,
    #     os.path.normpath(f"{args.coadd_name}/{args.coadd_subset}/coadd/{args.template_type}"),
    #     "--pipeline", f"{os.environ.get('PROC_DECAM_DIR')}/pipelines/{pipeline_lookup[args.template_type]}#assembleCoadd",
    # ]
    # if args.where:
    #     cmd += [f"--where \"{args.where}\""]
    # 
    # coadd pipeline
    steps = ["step3b", "step3c", "step3d"]#, "step3e", "step3f", "step3g", "step3h"]
    cmd = [
        "proc-decam",
        "pipeline",
        args.repo,
        "coadd",
        args.coadd_subset,
        "--steps", 
    ] + steps
    cmd += ["--template-type", args.template_type] if args.template_type else []
    cmd += ["--slurm"] if args.pipeline_slurm else []
    cmd += [f"--where \"{args.where}\""] if args.where else []
        
    # collection
    cmd = " ".join(map(str, cmd))
    func = partial(run_command)
    setattr(func, "__name__", f"execute_coadd")
    future = bash_app(func)(cmd, inputs=inputs)
    inputs = [future]
    futures.append(future)

    cmd = [
        "proc-decam",
        "collection",
        args.repo,
        "coadd",
        args.coadd_subset,
    ]
    cmd += ["--template-type", args.template_type] if args.template_type else []
        
    cmd = " ".join(map(str, cmd))
    func = partial(run_command)
    setattr(func, "__name__", f"collection")
    future = bash_app(func)(cmd, inputs=inputs)
    inputs = [future]
    futures.append(future)

    def _print_task_logs(future):
        for log_attr in ('stdout', 'stderr'):
            log_path = getattr(future, log_attr, None)
            if log_path and os.path.exists(log_path):
                print(f"\n=== Task {log_attr} ({log_path}) ===", file=sys.stderr)
                with open(log_path) as f:
                    print(f.read(), file=sys.stderr)

    for future in futures:
        if future:
            try:
                future.result()
                if args.debug:
                    _print_task_logs(future)
            except Exception:
                _print_task_logs(future)
                raise
    
    parsl.dfk().cleanup()


if __name__ == "__main__":
    main()
