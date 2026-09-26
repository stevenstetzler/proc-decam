# proc-decam

Process a DECam imaging survey using the LSST Science Pipelines.

Install LSST Science Pipelines: https://pipelines.lsst.io/
- This package has been tested with version `w_2024_34` of the Science Pipelines. 
- Later version may break compatibility with this package due to the shared dependence on `parsl`, which has a quickly changing API.

## Usage

Checkout this repository and install the code on top of the pipelines:
```
$ git clone https://github.com/dirac-institute/proc-decam.git
$ cd proc-decam
$ python -m pip install .
$ source ./bin/setup.sh
```

Create LSST repository:
```bash
$ butler create ./repo
$ butler register-instrument ./repo lsst.obs.decam.DarkEnergyCamera
$ butler write-curated-calibrations ./repo lsst.obs.decam.DarkEnergyCamera
```

Register Skymap
```bash
$ butler register-skymap ./repo -c name='discrete'
```

Query for survey images:
```bash
$ proc-decam exposures ./data --proposal-id 2019A-0337
```

Download survey images:
```bash
$ proc-decam download ./data/exposures.ecsv --download-dir ./data/images
```

Ingest defects:
```bash
$ proc-decam defects ./repo ./data/bpm
```

Get reference catalogs:
```bash
$ proc-decam refcats ./repo ./data/exposures.ecsv
```

Ingest fakes:
```bash
$ proc-decam fakes ./repo path/to/fakes.fits # astropy readable table with columns RA/DEC/MAG/BAND/EXPNUM
```

# Processing

A single (or multiple) night(s) of the survey (or a subset of the data contained thereof) can be processed using the `proc-decam night` command, which constructs and executes a Parsl workflow that runs survey pipelines over the nights specified:
```bash
$ proc-decam night --help
usage: proc-decam night [-h] [--nights NIGHTS] [--image-dir IMAGE_DIR]
                        [--proc-types {bias,flat,drp,diff_drp} [{bias,flat,drp,diff_drp} ...]]
                        [--coadd-subset COADD_SUBSET]
                        [--template-type TEMPLATE_TYPE] [--where WHERE]
                        [--log-level LOG_LEVEL] [--slurm] [--pipeline-slurm]
                        [--provider PROVIDER] [--workers WORKERS] [--debug]
                        repo exposures

positional arguments:
  repo
  exposures

options:
  -h, --help            show this help message and exit
  --nights NIGHTS
  --image-dir IMAGE_DIR
  --proc-types {bias,flat,drp,diff_drp} [{bias,flat,drp,diff_drp} ...]
  --coadd-subset COADD_SUBSET
  --template-type TEMPLATE_TYPE
  --where WHERE
  --log-level LOG_LEVEL
  --slurm
  --pipeline-slurm
  --provider PROVIDER
  --workers WORKERS, -J WORKERS
  --debug
```
Nights processed are controlled with the a regular expression passed to `--nights`, while data subsets are controlled with `--where`, respecting the semantics of the LSST Science Pipelines Butler query system. Parallelism of the survey processing pipeline is controlled with `-J`, while parallelism of the execution of the LSST Science Pipelines is controlled with the `J` environment variable, e.g.:
```
$ J=24 proc-decam night ./repo ./data/exposures.ecsv --nights 20190401 --where "instrument='DECam' and detector=1" -J 4
```
will take advantage of at most `24*4 = 96` cores to process DECam detector 1 from the night `20190401` of the survey.

The nightly pipeline will execute (via the `proc-decam pipeline` command) a master bias construction pipeline ([pipelines/bias.yaml](pipelines/bias.yaml)), a master flat construction pipeline ([pipelines/flat.yaml](pipelines/flat.yaml)), and a science exposure calibration pipeline ([pipelines/DRP.yaml](pipelines/DRP.yaml)).

The `proc-decam pipeline` command constructs a Parsl workflow for executing one or more pipelines, the definition of which are stored in the `pipelines` top-level directory. 
```bash
$ proc-decam pipeline --help
usage: proc-decam pipeline [-h] [--template-type TEMPLATE_TYPE] [--coadd-subset COADD_SUBSET]
                           [--steps STEPS [STEPS ...]] [--where WHERE] [--log-level LOG_LEVEL] [--slurm]
                           [--workers WORKERS]
                           repo proc_type subset

positional arguments:
  repo
  proc_type
  subset

options:
  -h, --help            show this help message and exit
  --template-type TEMPLATE_TYPE
  --coadd-subset COADD_SUBSET
  --steps STEPS [STEPS ...]
  --where WHERE
  --log-level LOG_LEVEL
  --slurm
  --workers WORKERS, -J WORKERS
```
The pipelines are collections of individual tasks from the LSST Science Pipelines that will be executed on a provided set of input data. Tasks are groups into `steps` of the pipeline that have a common set of input data dimensions (e.g. tasks that apply per-detector or per-exposure of the survey). An example invocation is
```bash
$ J=24 proc-decam pipeline ./repo bias 2019401 --where "instrument='DECam' and detector=1"
```
which will execute the master bias construction pipeline for night `20190401` of the survey. The `proc-decam pipeline` creates a workflow of `proc-decam execute` commands to execute a single step (or an entire pipeline definition):
```bash
$ proc-decam execute --help
usage: proc-decam execute [-h] [--pipeline PIPELINE] [--where WHERE] [--no-skip-existing] [--no-skip-failures]
                          [--no-loop] [--no-trigger-retry]
                          repo parent

positional arguments:
  repo
  parent

options:
  -h, --help           show this help message and exit
  --pipeline PIPELINE
  --where WHERE
  --no-skip-existing
  --no-skip-failures
  --no-loop
  --no-trigger-retry
```
The `proc-decam execute` command handles executing a pipeline definition file (or a subset of it), handling the requirement to skip datasets that have already been produced, skip tasks which result in non-recoverable failures, and retry execution of tasks that have recoverable failures. An example invocation is
```
$ proc-decam execute ./repo 20190401/bias --pipeline pipelines/bias.yaml#step1 --where "instrument='DECam' and detector=1"
```

An example task graph created and executed by `proc-decam night` looks like:
```
[night] 20190401
  [pipeline] bias 20190401
    [execute] 20190401 pipelines/bias.yaml#step1
    - until complete: bps submit pipelines/submit.yaml -p pipelines/bias.yaml#step1
    [execute] 20190401 pipelines/bias.yaml#step2
    - until complete: bps submit pipelines/submit.yaml -p pipelines/bias.yaml#step2
  [pipeline] flat 20190401
  ...
  [pipeline] DRP 20190401
  ...
[night] 20190402
...
```

## Templates and Difference Images

Additional commands are available for creating templates and difference images for the survey. For example, the following commands can be used to create a template for a single year of data, selecting data (warps) from all nights of the survey from 2019:
```
$ proc-decam coadd ./repo "2019*/drp" --coadd-subset 2019 --template-type meanclip --warp-coadd-name deep 
```

Difference images can be produced on a nightly basis by executing the `proc-decam night` command again, but specifying the `--proc-type`, `--coadd-subset`, and `--template-type`:
```
$ proc-decam night ./repo ./data/exposures.ecsv --nights 20190401 --proc-type diff_drp --coadd-subset 2019 --template-type meanclip 
```

## Data dependencies

Data dependences across subsets of the data (nights and coadd subsets) are chained together using Butler CHAINED collections with the following naming scheme:
```
{subset}/{coadd_subset}/{template_type}/{proc_type}
```
For example, master bias calibrations for night `20190401` are stored in the collection `20190401/bias` while a mean clip coadd for the year `2019` is stored in the collection `2019/meanclip/coadd` and difference images for night `20190401` using the `2019` coadd will be in `20190401/2019/meanclip/diff_drp`. The different components of the `proc-decam` package are internally aware of this naming scheme and keeps the collection naming consistent to handle automatic data dependency chaining. The `proc-decam collection` command can also be used to create collections that utilize this naming scheme:
```bash
$ proc-decam collection --help
usage: proc-decam collection [-h] [--coadd-subset COADD_SUBSET]
                             [--template-type TEMPLATE_TYPE]
                             [--log-level LOG_LEVEL] [--overwrite]
                             repo proc_type subset

positional arguments:
  repo
  proc_type
  subset

options:
  -h, --help            show this help message and exit
  --coadd-subset COADD_SUBSET
  --template-type TEMPLATE_TYPE
  --log-level LOG_LEVEL
  --overwrite
```

# Reference Catalogs

The calibration of science exposures requires reference catalogs which enable photometric and astrometric calibration. These reference catalogs are available via the `get-lsst-refcats` package: [https://github.com/dirac-institute/lsst_refcats](https://github.com/dirac-institute/lsst_refcats).

If providing and ingesting your own reference catalogs, update the reference catalog configurations of the `CalibrateTask` in `pipelines/DRP.yaml`.

# Using Postgres

To enable scalable and distributed processing, it is recommended to use a Postgres database for the LSST Science Pipelines registry. We provide a utility to set up a Postgres database when creating Butler repository:
```bash
$ proc-decam db create ./repo
```

One should execute
```bash
$ proc-decam db start ./repo
```
to start the database and
```bash
$ proc-decam db stop ./repo
```
to stop it.
