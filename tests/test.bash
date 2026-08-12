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

# Diagnostic layer: confirm the symlinked FITS files both open cleanly
# (astropy.io.fits) and translate cleanly (astro_metadata_translator, the
# same machinery the ingest step below uses) before handing them to the
# pipeline. Isolates a plain file-access problem from something specific
# to LSST's header translation.
python ${WORK}/proc_decam/tests/verify_fits_metadata.py \
${DATA}/downloaded_exposures.ecsv

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
# J=1 proc-decam night ${REPO} ${DATA}/exposures.ecsv --nights 20210318 \
#   --where "instrument='DECam' and detector=35" \
#   --debug
proc-decam ingest /home/lsst/data/exposures.ecsv \
  -b /home/lsst/repo --image-dir ./data/images \
  --select night=20210318 obs_type='zero'
