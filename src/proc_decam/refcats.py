"""
Get trimmed refcats for the survey
"""

import atexit
from subprocess import Popen, PIPE
import selectors
import sys

processes = []

def cleanup():
    for p in processes:
        p.kill()

atexit.register(cleanup)

def popen(*args, **kwargs):
    global processes
    p = Popen(*args, **kwargs)
    print("popen: " + " ".join(p.args), file=sys.stderr)
    processes.append(p)
    return p

def _print(p):
    sel = selectors.DefaultSelector()
    if p.stdout is PIPE:
        sel.register(p.stdout, selectors.EVENT_READ)
    
    if p.stderr is PIPE:
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

def main():
    import argparse
    import astropy.table
    from pathlib import Path
    from subprocess import Popen

    parser = argparse.ArgumentParser()
    parser.add_argument("repo", type=str)
    parser.add_argument("exposures", type=Path)

    args = parser.parse_args()

    for dataset in ["ps1_pv3_3pi_20170110", "gaia_dr2_20200414", "gaia_dr3_20230707"]:
        cmd = [
            "butler",
            "register-dataset-type",
            args.repo,
            dataset,
            "SimpleCatalog",
            "htm7"
        ]
        register = popen(cmd)
        register.wait()

    data_dir = args.exposures.resolve().parent
    exposures = astropy.table.Table.read(args.exposures)
    downloaded = astropy.table.Table.read(data_dir / ("downloaded_" + args.exposures.name))
    exposures = astropy.table.join(exposures, downloaded, keys=["md5sum"])
    exposures = exposures[
        (
        exposures['obs_type'] == "object"
        )
        & (
            exposures['valid_on_disk']
        )
        & (
            exposures['proc_type'] == "raw"
        )
    ]
    paths = exposures['path']
    
    cmd = [
        "lsst-refcats",
        "ps1",
        "--import-file",
        "--output",
        str(data_dir / "refcats"),
        "-J", "24",
        "--paths",
    ] + list(map(str, paths))
    with open(data_dir / "refcats_ps1.out", "w") as o, open(data_dir / "refcats_ps1.err", "w") as e:
        ps1 = popen(cmd, stdout=o, stderr=e)
    
    cmd = [
        "lsst-refcats",
        "gaia_dr3",
        "--import-file",
        "--output",
        str(data_dir / "refcats"),
        "-J", "24",
        "--paths",
    ] + list(map(str, paths))
    with open(data_dir / "refcats_gaia_dr3.out", "w") as o, open(data_dir / "refcats_gaia_dr3.err", "w") as e:
        gaia_dr3 = popen(cmd, stdout=o, stderr=e)
    
    cmd = [
        "lsst-refcats",
        "gaia_dr2",
        "--import-file",
        "--output",
        str(data_dir / "refcats"),
        "-J", "24",
        "--paths",
    ] + list(map(str, paths))
    with open(data_dir / "refcats_gaia_dr2.out", "w") as o, open(data_dir / "refcats_gaia_dr2.err", "w") as e:
        gaia_dr2 = popen(cmd, stdout=o, stderr=e)
    
    ps1.wait()
    gaia_dr3.wait()
    gaia_dr2.wait()

    for shortname, dataset in [("ps1", "ps1_pv3_3pi_20170110"), ("gaia_dr2", "gaia_dr2_20200414"), ("gaia_dr3", "gaia_dr3_20230707")]:
        cmd = [
            "butler",
            "ingest-files",
            args.repo,
            dataset,
            f"refcats/{shortname}",
            str(data_dir / "refcats" / f"{dataset}.ecsv"),
        ]
        ingest = popen(cmd)
        ingest.wait()

    cmd = [
        "butler",
        "collection-chain",
        args.repo,
        "refcats",
        "refcats/gaia_dr3",
        "refcats/gaia_dr2",
        "refcats/ps1",
    ]
    chain = popen(cmd)
    chain.wait()


if __name__ == "__main__":
    main()
