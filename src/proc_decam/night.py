import logging
import sys

logging.basicConfig()
logger = logging.getLogger(__name__)

import parsl
from parsl import bash_app
from parsl.executors import HighThroughputExecutor
from .parsl import EpycProvider, KloneAstroProvider, KloneA40Provider, run_command
from functools import partial

proc_to_obs = dict(
    bias="zero",
    flat="dome flat",
    science="object",
    drp="object",
)

def main():
    import argparse
    import astropy.table
    import re
    import os

    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("exposures")
    parser.add_argument("--nights", default=".*")
    # parser.add_argument("--steps", nargs="+")
    parser.add_argument("--image-dir", default="./data/images")
    parser.add_argument("--proc-types", nargs="+", default=["bias", "flat", "drp"])
    parser.add_argument("--coadd-subset", default=None)
    parser.add_argument("--template-type", default=None)
    parser.add_argument("--where")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--slurm", action="store_true")
    parser.add_argument("--pipeline-slurm", action="store_true")
    parser.add_argument("--provider", default="EpycProvider")
    parser.add_argument("--workers", "-J", type=int, default=4)
    parser.add_argument("--debug", action="store_true")

    args = parser.parse_args()
    
    logging.getLogger().setLevel(args.log_level)

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
        run_dir=os.path.join("runinfo", "night"),
    )
    parsl.load(config)

    exposures = astropy.table.Table.read(args.exposures)
    nights = sorted(map(int, list(set(list(filter(lambda x : re.compile(args.nights).match(x), map(str, exposures['night'])))))))
    
    futures = [] # chage to dictionary
    for night in nights:
        inputs = []
        for proc_type in args.proc_types:
            if proc_type in ['bias', 'flat', 'drp']:
                cmd = [
                    "proc-decam",
                    "ingest",
                    args.exposures,
                    "-b", args.repo,
                    "--image-dir", args.image_dir,
                    "--select", f"night={night} obs_type='{proc_to_obs[proc_type]}'",
                ]
                cmd = " ".join(map(str, cmd))
                func = partial(run_command)
                setattr(func, "__name__", f"ingest_{night}_{proc_type}")
                future = bash_app(func)(cmd, inputs=inputs)
                inputs = [future]
                futures.append(future)
            
                cmd = [
                    "proc-decam",
                    "raw",
                    args.repo,
                    proc_type,
                    night
                ]
                cmd = " ".join(map(str, cmd))
                func = partial(run_command)
                setattr(func, "__name__", f"raw_{night}_{proc_type}")
                future = bash_app(func)(cmd, inputs=inputs)
                inputs = [future]
                futures.append(future)
            
                cmd = [
                    "proc-decam",
                    "collection",
                    args.repo,
                    proc_type,
                    night
                ]
                cmd = " ".join(map(str, cmd))
                func = partial(run_command)
                setattr(func, "__name__", f"collection_{night}_{proc_type}")
                future = bash_app(func)(cmd, inputs=inputs)
                inputs = [future]
                futures.append(future)

                cmd = [
                    "butler",
                    "define-visits",
                    args.repo,
                    "lsst.obs.decam.DarkEnergyCamera",
                    "--collections", f"{night}/{proc_type}",
                ]
                cmd = " ".join(map(str, cmd))
                func = partial(run_command)
                setattr(func, "__name__", f"define_visits_{night}_{proc_type}")
                future = bash_app(func)(cmd, inputs=inputs)
                inputs = [future]
                futures.append(future)

            if proc_type == "bias":
                steps = ["step1", "step2"]
                cmd = [
                    "proc-decam",
                    "pipeline",
                    args.repo,
                    proc_type,
                    night,
                    "--steps"
                ] + steps
                cmd += ["--slurm"] if args.pipeline_slurm else []
                cmd += [f"--where \"{args.where}\""] if args.where else []

                cmd = " ".join(map(str, cmd))
                func = partial(run_command)
                setattr(func, "__name__", f"pipeline_{night}_{proc_type}")
                future = bash_app(func)(cmd, inputs=inputs)
                inputs = [future]
                futures.append(future)

                cmd = [
                    "proc-decam",
                    "decertify",
                    args.repo,
                    f"{night}/calib/{proc_type}",
                    proc_type,
                ]
                cmd = " ".join(map(str, cmd))
                func = partial(run_command)
                setattr(func, "__name__", f"decertify_{night}_{proc_type}")
                future = bash_app(func)(cmd, inputs=inputs)
                inputs = [future]
                futures.append(future)

                cmd = [
                    "butler", 
                    "certify-calibrations", 
                    args.repo,
                    f"{night}/{proc_type}",
                    f"{night}/calib/{proc_type}",
                    proc_type,
                    "--begin-date",
                    "2000-01-01T00:00:00",
                    "--end-date",
                    "2050-01-01T00:00:00",
                    "--search-all-inputs"
                ]
                cmd = " ".join(map(str, cmd))
                func = partial(run_command)
                setattr(func, "__name__", f"certify_{night}_{proc_type}")
                future = bash_app(func)(cmd, inputs=inputs)
                inputs = [future]
                futures.append(future)

            elif proc_type == "flat":
                steps = ["step0", "step1", "step2", "step3"]
                cmd = [
                    "proc-decam",
                    "pipeline",
                    args.repo,
                    proc_type,
                    night,
                    "--steps"
                ] + steps
                cmd += ["--slurm"] if args.pipeline_slurm else []
                cmd += [f"--where \"{args.where}\""] if args.where else []

                cmd = " ".join(map(str, cmd))
                func = partial(run_command)
                setattr(func, "__name__", f"pipeline_{night}_{proc_type}")
                future = bash_app(func)(cmd, inputs=inputs)
                inputs = [future]
                futures.append(future)

                cmd = [
                    "proc-decam",
                    "decertify",
                    args.repo,
                    f"{night}/calib/{proc_type}",
                    proc_type,
                ]
                cmd = " ".join(map(str, cmd))
                func = partial(run_command)
                setattr(func, "__name__", f"decertify_{night}_{proc_type}")
                future = bash_app(func)(cmd, inputs=inputs)
                inputs = [future]
                futures.append(future)

                cmd = [
                    "butler", 
                    "certify-calibrations", 
                    args.repo,
                    f"{night}/{proc_type}",
                    f"{night}/calib/{proc_type}",
                    proc_type,
                    "--begin-date",
                    "2000-01-01T00:00:00",
                    "--end-date",
                    "2050-01-01T00:00:00",
                    "--search-all-inputs"
                ]
                cmd = " ".join(map(str, cmd))
                func = partial(run_command)
                setattr(func, "__name__", f"certify_{night}_{proc_type}")
                future = bash_app(func)(cmd, inputs=inputs)
                inputs = [future]
                futures.append(future)

            elif proc_type in ["science", "drp", "diff_drp"]:
                if proc_type == "science":
                    steps = ["step0", "step1"]
                elif proc_type == "drp":
                    steps = ["step0", "step1", "step2a", "step2b", "step2c", "step2d", "step2e", "step2f", "step3a"]
                elif proc_type == "diff_drp":
                    steps = ["step4a", "step4b", "step4d", "step4e", "step4f"] # "step4c", 
                else:
                    raise Exception(f"unsupported proc type {proc_type}")
                
                if proc_type == "diff_drp":
                    cmd = [
                        "proc-decam",
                        "collection",
                        args.repo,
                        proc_type,
                        night
                    ]
                    cmd += ["--coadd-subset", args.coadd_subset] if args.coadd_subset else []
                    cmd += ["--template-type", args.template_type] if args.template_type else []
                    cmd = " ".join(map(str, cmd))
                    func = partial(run_command)
                    setattr(func, "__name__", f"collection_{night}_{proc_type}")
                    future = bash_app(func)(cmd, inputs=inputs)
                    inputs = [future]
                    futures.append(future)

                cmd = [
                    "proc-decam",
                    "pipeline",
                    args.repo,
                    proc_type,
                    night,
                    "--steps", 
                ] + steps
                cmd += ["--slurm"] if args.pipeline_slurm else []
                cmd += [f"--where \"{args.where}\""] if args.where else []
                cmd += ["--coadd-subset", args.coadd_subset] if args.coadd_subset else []
                cmd += ["--template-type", args.template_type] if args.template_type else []

                cmd = " ".join(map(str, cmd))
                func = partial(run_command)
                setattr(func, "__name__", f"pipeline_{night}_{proc_type}")
                future = bash_app(func)(cmd, inputs=inputs)
                inputs = [future]
                futures.append(future)
            else:
                raise Exception(f"unsupported proc type {proc_type}")
    
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
    # tag bias
    # make collection
    # make bias
    # certify bias
    # tag flat
    # make flat
    # certify flat
        
if __name__ == "__main__":
    main()
