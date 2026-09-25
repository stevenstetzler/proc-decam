# given an input collection and a pipeline
# execute the pipeline on the inputs and place the outputs into a run collection:
# {input}/{pipeline_name}/{pipeline_step}/{datetime}

from datetime import datetime
import re
import os
import lsst.daf.butler as dafButler
from lsst.daf.butler.registry import CollectionType
import atexit
from subprocess import Popen, PIPE
import selectors
import sys
import argparse

if not os.path.exists(os.path.join(os.getcwd(), "pipelines")):
    raise RuntimeError("Cannot find directory 'pipelines' in the current working directory")

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

run_date_format = "%Y%m%dT%H%M%SZ"
def generate_date():
    return datetime.now().strftime(run_date_format)

def fixup_chain(repo, collection):
    butler = dafButler.Butler(repo, writeable=True)
    runs = butler.registry.queryCollections(re.compile(collection + "/.*/\d{8}T\d{6}Z$"), collectionTypes=CollectionType.RUN)
    existing_children = butler.registry.getCollectionChain(collection)
    other_children = [c for c in existing_children if c not in runs]
    runs = sorted(runs, key=lambda x : datetime.fromisoformat(re.compile(".*/(?P<date>\d{8}T\d{6}Z)$").match(x).groupdict()['date']), reverse=True)
    children = runs + other_children
    print("setting", collection, "=", children, file=sys.stderr)
    butler.registry.setCollectionChain(collection, children)

def normalize_pipeline(pipeline_path):
    filename = os.path.basename(pipeline_path)
    pipeline_name, pipeline_step = filename.split(".yaml")
    if pipeline_step == "":
        pipeline_step = "_"
    else:
        pipeline_step = pipeline_step.replace("#", "")
    return pipeline_name, pipeline_step


def construct_run(parent, pipeline_path):
    pipeline_name, pipeline_step = normalize_pipeline(pipeline_path)
    run = f"{parent}/{pipeline_name}/{pipeline_step}/{generate_date()}"
    return run

def should_run(repo, collection, pipeline, data_query=None, skip_existing=True, skip_failures=True):
    cmd = [
        "proc-decam",
        "qgraph",
        "-b", repo,
        "-i", collection,
        "--output-run", "dummy",
        "-p", pipeline,
    ]
    if data_query:
        cmd += ["-d", data_query]
    if skip_existing:
        cmd += ["--skip-existing-in", collection]
    if skip_failures:
        cmd += ["--skip-failures"]

    p = popen(cmd, stdout=PIPE, stderr=PIPE)
    stdout, stderr = p.communicate()
    print(stdout.decode())
    p.wait()
    if p.returncode != 0:
        for line in stderr.decode().split("\n"):
            if "quantum graph is empty" in line:
                return False
        raise RuntimeError("should run failure: " + stderr.decode())
    return True


def submit(repo, parent, pipeline_path, data_query=None, skip_existing=True, skip_failures=True, trigger_retry=False, loop=False):
    
    fixup_chain(repo, parent)

    def inner():
        run = construct_run(parent, pipeline_path)
        qgraph_file = os.path.join(os.environ.get('TMPDIR', "/tmp"), run.replace("/", "_") + ".qgraph")
        cmd = [
            "pipetask",
            "--long-log", "--log-level", "VERBOSE",
            "qgraph",
            "-b", repo,
            "-p", pipeline_path,
            "-i", parent,
            "--output-run", run,
            "--save-qgraph", qgraph_file,
            "--qgraph-datastore-records", # for quantum-backed butler
        ]
        if data_query:
            cmd += ["-d", data_query]
        if skip_existing:
            cmd += ["--skip-existing-in", parent]

        p = run_and_pipe(cmd)
        p.wait()
        if p.returncode != 0:
            raise RuntimeError("quantum graph generation failed")
        
        cmd = [
            "bps", 
            "--long-log", "--log-level", "VERBOSE",
            "submit",
            f"{os.getcwd()}/pipelines/submit.yaml",
            # "--wms-service-class", "proc_lsst.shared.service.SharedParslService",
            "-b", repo,
            "-i", parent,
            "--output-run", run,
            "--qgraph", qgraph_file,
        ]
        p = run_and_pipe(cmd)
        p.wait()
        if p.returncode != 0:
            raise RuntimeError("bps submit failed")
        
        if trigger_retry:
            cmd = [
                "proc-decam",
                "retries",
                repo,
                run
            ]
            p = run_and_pipe(cmd)
            p.wait()
            if p.returncode != 0:
                raise RuntimeError("bps submit failed")

        fixup_chain(repo, parent) # append run to chain

    check = lambda : should_run(repo, parent, pipeline_path, data_query=data_query, skip_existing=skip_existing, skip_failures=skip_failures)

    if loop:
        if skip_existing and skip_failures:
            # continue generating qgraph and running until there are no more failures
            while check():
                inner()
    else:
        # run once
        if (not skip_existing and not skip_failures) or check():
            inner()


def main():
    """
    Executes a pipeline on an input collection chain
    Constructs a run name by appending the pipeline name and the current date to the input collection
    Updates the collection chain to include the completed run
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("parent")
    parser.add_argument("--pipeline")
    parser.add_argument("--where")
    parser.add_argument("--no-skip-existing", action="store_true")
    parser.add_argument("--no-skip-failures", action="store_true")
    parser.add_argument("--no-loop", action="store_true")
    parser.add_argument("--no-trigger-retry", action="store_true")

    args = parser.parse_args()
    # print(args)
    # return
    submit(
        args.repo, 
        args.parent, 
        args.pipeline, 
        data_query=args.where,
        skip_existing=not args.no_skip_existing,
        skip_failures=not args.no_skip_failures,
        trigger_retry=not args.no_trigger_retry,
        loop=not args.no_loop,
    )

if __name__ == "__main__":
    main()
