#!/bin/bash
set -uex

# Renders every subset defined in flat.yaml, bias.yaml and DRP.yaml via
# viz.sh. Subsets are discovered from each file rather than hardcoded, so
# this doesn't go stale as subsets are added/renamed/removed. Building a
# whole pipeline file directly (no subset) can fail even when every one of
# its subsets builds fine - e.g. flat.yaml as a whole raises
# ConnectionTypeConsistencyError because 'overscanRaw' is both a prerequisite
# input to a later step and produced by an earlier one, which is only a
# problem once both steps are in the same graph - so only subsets are built.
#
# Excludes commented-out subsets (e.g. DRP.yaml's step3e-h, step4c) since
# those aren't valid `^\s+step...:` key lines.
for yaml in pipelines/flat.yaml pipelines/bias.yaml pipelines/DRP.yaml; do
  for subset in $(grep -E '^\s+step[0-9]+[a-zA-Z]?:' "$yaml" | sed -E 's/^\s+(step[0-9]+[a-zA-Z]?):.*/\1/'); do
    bash pipelines/viz.sh "$yaml#$subset"
  done
done
