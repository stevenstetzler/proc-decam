#!/usr/bin/env bash
#
# Pull Git LFS content for a checked-out recipe repo and verify it actually
# landed before letting the workflow proceed. This exists because a silent,
# partial `git lfs pull` (transient network blip, LFS bandwidth error, etc.)
# used to leave pointer stubs or empty files in place while still exiting 0.
# Combined with actions/cache, that broken tree got cached under a key that
# never changes and every subsequent CI run restored it forever. Failing
# loudly here means a bad pull can never be cached (the cache/save step only
# runs after this script succeeds).
set -euo pipefail

repo_dir="${1:?usage: lfs_pull_verify.sh <repo_dir>}"
cd "${repo_dir}"

git lfs install

attempts=3
for attempt in $(seq 1 "${attempts}"); do
  if git lfs pull; then
    break
  fi
  if [ "${attempt}" -eq "${attempts}" ]; then
    echo "::error::git lfs pull failed after ${attempts} attempts in ${repo_dir}"
    exit 1
  fi
  echo "git lfs pull failed (attempt ${attempt}/${attempts}) in ${repo_dir}; retrying..."
  sleep $((attempt * 5))
done

# Validate checked-out objects against their recorded oids/sizes.
git lfs fsck

bad=0
while IFS= read -r f; do
  if [ ! -s "${f}" ]; then
    echo "::error::${f} is empty after git lfs pull"
    bad=1
  elif head -c 200 "${f}" | grep -q "git-lfs.github.com/spec"; then
    echo "::error::${f} is still an LFS pointer, not real content"
    bad=1
  fi
done < <(git lfs ls-files -n)

if [ "${bad}" -ne 0 ]; then
  echo "::error::LFS verification failed for ${repo_dir}; refusing to continue (this state will not be cached)"
  exit 1
fi

echo "LFS verification passed for ${repo_dir}"
