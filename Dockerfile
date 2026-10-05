FROM lsstsqre/centos:7-stack-lsst_distrib-w_2024_34
ARG PROC_DECAM_REPO=https://github.com/dirac-institute/proc-decam.git
ARG PROC_DECAM_VERSION=w.2024.34
USER root
# install packages
RUN yum -y install curl ca-certificates && yum clean all && \
    update-ca-trust force-enable || true && \
    curl -s https://packagecloud.io/install/repositories/github/git-lfs/script.rpm.sh | bash && \
    yum -y install git-lfs && \
    git lfs install --system
RUN yum -y install postgresql-server postgresql-contrib && yum clean all

USER lsst
SHELL ["/bin/bash", "-lc"]
WORKDIR /home/lsst

ENV REPO="/home/lsst/repo" DATA="/home/lsst/data"
RUN source /opt/lsst/software/stack/loadLSST.bash && \
    setup lsst_distrib && \
    python -m pip install --no-cache-dir "git+${PROC_DECAM_REPO}@${PROC_DECAM_VERSION}" && \
    proc-decam db create ${REPO} && \
    proc-decam db start ${REPO} && \
    butler register-instrument ${REPO} lsst.obs.decam.DarkEnergyCamera && \
    butler write-curated-calibrations ${REPO} lsst.obs.decam.DarkEnergyCamera && \
    proc-decam defects ${REPO} ${DATA}/bpm && \
    butler register-skymap ${REPO} -c name='discrete' && \
    proc-decam db stop ${REPO}
