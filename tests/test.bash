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
proc-decam fakes ${REPO} \
  fakes_fakeSrcCat.fits \
  --format fits

# process night through calibrated exposures
cd ${WORK}/proc_decam
export PROC_DECAM_DIR=$PWD
J=1 proc-decam night ${REPO} ${DATA}/exposures.ecsv --nights 20210318 \
  --image-dir ${DATA}/images \
  --where "instrument='DECam' and detector=35" \
  --workers 1 \
  --debug

# coadd calibrated exposures into a template
J=1 proc-decam coadd ${REPO} 20210318 --coadd-subset 20210318 \
  --template-type meanclip \
  --warp-coadd-name deep \
  --debug

# produce difference images
J=1 proc-decam night ${REPO} ${DATA}/exposures.ecsv --nights 20210318 \
   --proc-type diff_drp \
   --coadd-subset 20210318 \
   --where "instrument='DECam' and detector=35" \
   --workers 1 \
   --template-type meanclip  \
  --debug

proc-decam visualize ${REPO} drp calexp
proc-decam visualize ${REPO} diff_drp deepDiff_differenceExp