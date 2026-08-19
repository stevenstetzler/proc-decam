#!/usr/bin/env bash

source /opt/lsst/software/stack/loadLSST.bash
setup lsst_distrib
set -xeuo pipefail

# set up data
python ${WORK}/proc_decam/tests/make_exposures.py \
--mastercals-dir ${WORK}/kbmod_mastercals_recipe/trimmedRawData/210318/calib \
--science-dir ${WORK}/kbmod_imdiff_recipe/trimmedRawData/210318/science \
--output ${DATA}/exposures.ecsv \
--image-dir ${DATA}/images

# start repo
proc-decam db start ${REPO}

# Ingest refcats
proc-decam refcats ${REPO} ${DATA}/exposures.ecsv

# ingest fakes
cd ${WORK}/kbmod_imdiff_recipe/trimmedRawData/fakes
python create_fakes.py
cd ${WORK}

proc-decam fakes ${REPO} \
  ${WORK}/kbmod_imdiff_recipe/trimmedRawData/fakes/fakes_fakeSrcCat.fits \
  --format fits

# process night through calibrated exposures
# (night's own bias/flat/drp steps ingest raw frames themselves, so no
# separate `proc-decam ingest` call is needed here -- one would also be
# harmful: it would populate the DECam/raw/all collection early, and
# night's per-proc-type ingest calls skip outright once that collection
# already exists, silently leaving flat/science uningested.)
J=1 proc-decam night ${REPO} ${DATA}/exposures.ecsv --nights 20210318 \
  --image-dir ${DATA}/images \
  --where "instrument='DECam' and detector=35" \
  --debug
