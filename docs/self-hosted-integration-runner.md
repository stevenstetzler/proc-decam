# Design: move `integration-test` to a self-hosted runner

Status: proposed
Scope: `.github/workflows/test.yml`'s `integration-test` job only

## 1. Summary

Move the `integration-test` job in `.github/workflows/test.yml` from
`runs-on: ubuntu-latest` (GitHub-hosted) to a dedicated self-hosted runner.
The `build-image` job (and the `Build and Push Docker Image` workflow it
calls) stays on GitHub-hosted runners — it's a lightweight `docker build`
+ push with no unusual resource needs.

This is a plan and setup guide, not a completed migration. The workflow
change described in §4 should not be merged until a runner registered with
the label used there is actually online (§5) — otherwise the job just
queues forever with "Waiting for a runner to pick up this job…".

## 2. Motivation

Everything below is evidence gathered debugging this exact job over the
last several rounds of this branch, not a hypothetical:

- **The job has already killed a GitHub-hosted runner outright.** After
  fixing an `--image-dir` bug that was silently failing every ingest, the
  next failure mode was parsl scaling out to dozens of worker blocks
  (block IDs past 25, 88 "connected" workers reported) and the runner
  eventually reporting *"The hosted runner lost communication with the
  server. Anything in your workflow that terminates the runner process,
  starves it for CPU/Memory, or blocks its network access can cause this
  error."* That's not a job failure, it's the whole VM going unresponsive.
  We've since fixed the specific bugs that caused that runaway worker
  count (a `J=1` env var that didn't do anything, a typo that neutered
  `EpycProvider`'s block-count defaults), but the underlying fact remains:
  this job runs a real LSST Science Pipelines stack, and standard
  GitHub-hosted runners (2 cores / 7 GB RAM, `ubuntu-latest`) are a tight
  fit for that even at reduced parallelism, with no headroom for mistakes.
- **The Docker image is large and gets re-pulled from scratch every run.**
  `ghcr.io/stevenstetzler/proc-decam:lsst-w_2024_34` is built from a full
  CentOS7 + LSST Science Pipelines stack. GitHub-hosted runners are
  ephemeral, so every single run pays the full pull cost again — we
  already had to add a `build-image` prerequisite job (see the earlier
  fix on this branch) just to guarantee the tag exists; we haven't yet
  had to deal with pull time/flakiness directly, but it's a recurring
  cost that a persistent host removes entirely (pull once, reuse
  indefinitely, only re-pull when the tag content actually changes).
- **LFS payloads are large and external.** `kbmod_imdiff_recipe` and
  `kbmod_mastercals_recipe` are pulled via `git lfs` from
  `dirac-institute` repos on every run; we've already had to build cache
  verification tooling (`.github/scripts/lfs_pull_verify.sh`) to guard
  against a poisoned `actions/cache` entry. A persistent runner disk
  means these clones can potentially be reused across runs instead of
  re-fetched, though see §4.3 for why we're not proposing that yet.
- **Job/step timeouts.** The full pipeline (once we're running more than
  a single detector) will plausibly need longer than is comfortable to
  babysit inside a shared, ephemeral runner pool.

None of this is a knock on GitHub-hosted runners in general — it's that
this specific job is a real scientific pipeline with real resource needs,
and a fixed-size, persistent machine we control is a better fit than an
ephemeral 2-core/7 GB VM.

## 3. Goals / non-goals

**Goals**
- Run `integration-test` on hardware sized for an LSST Science Pipelines
  workload, with headroom so a parsl misconfiguration degrades a job
  instead of taking down the runner.
- Stop paying the full multi-GB image pull cost on every single run.
- Keep the rest of CI (`build-image`) exactly as-is.
- Make the change reversible in one line if it doesn't work out.

**Non-goals (for this doc)**
- Autoscaling / ephemeral self-hosted runner fleets (e.g. via
  `actions/runner-controller` on Kubernetes). One fixed machine is enough
  to start; revisit if queueing becomes a problem (§7).
- Migrating `build-image` or any other workflow.
- Switching parsl to actually run distributed via `KloneAstroProvider` /
  Slurm from CI. Worth calling out though: `src/proc_decam/parsl/` already
  has `KloneAstroProvider`/`KloneA40Provider` classes for UW's "Klone"
  Hyak cluster, which is what this pipeline is really designed to run on.
  If the self-hosted runner host has network access to submit Slurm jobs
  to that cluster, `night.py --slurm` becomes a real option instead of
  the `EpycProvider` local-fallback that caused the OOM/runaway-worker
  problems this doc exists because of. That's a bigger change than "stand
  up a self-hosted runner" and deliberately out of scope here, but it's
  the more scalable long-term answer and worth a follow-up design doc.

## 4. Proposed workflow changes

### 4.1 `runs-on`

```diff
   integration-test:
     needs: build-image
-    runs-on: ubuntu-latest
+    runs-on: [self-hosted, linux, x64, proc-decam-integration]
```

`proc-decam-integration` is a custom label assigned to the runner at
registration time (§5.3) so that only this job (and nothing else that
might get added to the repo later) lands on this specific machine. Using
an array of labels (rather than a single generic `self-hosted` label)
means a future second runner with different specs can't accidentally
pick up this job unless it's deliberately given the same label.

### 4.2 Concurrency guard

A self-hosted runner is one fixed machine, not an elastic pool. Two PRs
pushed close together would otherwise queue safely (GitHub serializes
jobs on a single runner automatically) — but a *cancelled and re-pushed*
PR can otherwise leave a stale run occupying the runner while a newer one
waits behind it. Add a concurrency group so a new push cancels the
in-flight run for the same ref instead of waiting behind it:

```diff
   integration-test:
     needs: build-image
     runs-on: [self-hosted, linux, x64, proc-decam-integration]
+    concurrency:
+      group: integration-test-${{ github.ref }}
+      cancel-in-progress: true
```

### 4.3 Everything else stays the same (for now)

- `container:`, the LFS checkout/cache steps, and the pipeline run step
  are unchanged. Self-hosted runners support the `container:` job syntax
  identically to hosted ones (they still shell out to a local Docker
  daemon), so no step-level changes are needed for that.
- `actions/cache` for the LFS objects keeps working as-is — it hits
  GitHub's hosted cache service over the network regardless of where the
  runner physically lives. We are *not* proposing switching to a
  runner-local disk cache in this pass, to keep this change to exactly
  one thing (where the job runs) rather than bundling in a second
  variable (how caching works). If cache round-trip time or the 10 GB
  per-repo cache limit becomes a problem, that's a good follow-up, not
  part of this migration.
- Secrets (`secrets.GITHUB_TOKEN`) and the `packages: read` permission
  used to pull the image work identically on self-hosted runners.

### 4.4 Explicit timeout

Add a `timeout-minutes` so a hung job doesn't sit on the runner
indefinitely and block every subsequent PR's `integration-test` run
(there's no autoscaling to fall back on with one fixed machine):

```diff
   integration-test:
     needs: build-image
     runs-on: [self-hosted, linux, x64, proc-decam-integration]
     concurrency:
       group: integration-test-${{ github.ref }}
       cancel-in-progress: true
+    timeout-minutes: 90
```

(90 minutes is a placeholder — set it from the first few real run
durations once the full `night` pipeline is running end to end.)

## 5. Self-hosted runner setup instructions

### 5.1 Host requirements

Sizing rationale: the parsl worker-count investigation on this branch
found that even a single reduced-parallelism run of the LSST stack was
enough to threaten a 2-core/7 GB GitHub-hosted runner. Recommended
starting point, to be tuned from real usage once running:

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 4 vCPU | 8+ vCPU |
| RAM | 16 GB | 32 GB+ |
| Disk | 100 GB SSD | 200 GB+ SSD |
| OS | Linux (Ubuntu 22.04/24.04 LTS or similar), x86_64 | same |

Rough disk budget: the LSST-stack Docker image alone is likely in the
5–15 GB range once pulled and unpacked; add the two LFS-backed recipe
checkouts, Postgres data files created by `proc-decam db start`, parsl's
`runinfo/` run directories, and normal Docker build-cache growth over
time. 200 GB gives real headroom and room for the cleanup cadence in
§6.2 to lag without an emergency.

This does not need to be a dedicated physical machine — a persistent
cloud VM (EC2, GCE, etc.) or a spare on-prem/lab box works equally well,
as long as it stays up continuously (or is brought back up and the
runner service restarted) and someone owns patching it.

### 5.2 Install dependencies

As root or via sudo on the host:

```bash
# Docker
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker

# git + git-lfs (needed for actions/checkout's lfs: true / lfs: false
# checkouts -- the runner uses the *host's* git binary, this is not
# provided by the runner package itself)
sudo apt-get update
sudo apt-get install -y git git-lfs
git lfs install --system
```

Create a dedicated, unprivileged user to run the Actions runner service
as (don't run it as root):

```bash
sudo useradd -m -s /bin/bash gha-runner
sudo usermod -aG docker gha-runner   # so the runner user can talk to the Docker daemon
```

### 5.3 Register the runner

1. In the browser, go to the repo's
   `Settings → Actions → Runners → New self-hosted runner`
   (`https://github.com/stevenstetzler/proc-decam/settings/actions/runners/new`).
   Pick Linux / x64. This page gives you a per-repo, time-limited
   registration token — copy the `./config.sh ... --token <TOKEN>` line
   it shows you; the token expires in about an hour, so do this right
   before step 2.

2. As the `gha-runner` user on the host:

   ```bash
   sudo -iu gha-runner
   mkdir actions-runner && cd actions-runner

   # Use the current version + checksum shown on the "New self-hosted
   # runner" page above rather than hardcoding one here.
   curl -o actions-runner-linux-x64.tar.gz -L \
     https://github.com/actions/runner/releases/download/v<VERSION>/actions-runner-linux-x64-<VERSION>.tar.gz
   tar xzf actions-runner-linux-x64.tar.gz

   ./config.sh \
     --url https://github.com/stevenstetzler/proc-decam \
     --token <TOKEN_FROM_STEP_1> \
     --name proc-decam-integration-01 \
     --labels proc-decam-integration \
     --work _work
   ```

   Accept the defaults for everything else (runner group `Default` is
   fine for a repo-scoped runner).

3. Install and start it as a system service, so it survives reboots and
   restarts automatically if the process dies:

   ```bash
   sudo ./svc.sh install gha-runner
   sudo ./svc.sh start
   sudo ./svc.sh status
   ```

4. Confirm it shows up **Idle** under
   `Settings → Actions → Runners` in the repo before merging §4's
   workflow change.

### 5.4 Pre-warm the Docker image (optional but recommended)

Saves the first post-migration run from paying the full pull cost:

```bash
sudo -iu gha-runner
echo "<A_TOKEN_WITH_packages:read>" | docker login ghcr.io -u <github-username> --password-stdin
docker pull ghcr.io/stevenstetzler/proc-decam:lsst-w_2024_34
```

After this, Docker's local layer cache means subsequent job runs only
pull layers that actually changed (i.e. essentially nothing, until
`build-image` pushes a new tag content).

## 6. Security considerations

**This is the part to get right before flipping §4's `runs-on` line, not
after.** Self-hosted runners execute arbitrary workflow (and, by
extension, PR-triggered) code directly on a machine you own, with no
per-job VM isolation. GitHub's own docs are explicit that self-hosted
runners should not be used with public repositories that accept outside
contributions, because a `pull_request`-triggered workflow from a
malicious fork can run arbitrary code on the runner.

Before merging §4:

1. **Confirm this repo's visibility and contribution model.** If
   `stevenstetzler/proc-decam` is public and accepts PRs from forks,
   either:
   - Enable `Settings → Actions → General → Fork pull request
     workflows → "Require approval for all outside collaborators"`
     (a maintainer must click Approve before a fork PR's workflow runs
     on the self-hosted runner), **or**
   - Change `test.yml`'s trigger so the self-hosted job only runs for
     trusted contexts (e.g. gate it behind `pull_request_target` with an
     explicit approval step, or only run on pushes to branches within
     the repo, not from forks). `pull_request` alone, today, will run
     for any fork's PR.
   If the repo is private with a small, trusted contributor list, the
   risk is much lower, but still worth stating explicitly in the repo's
   own docs so it isn't silently assumed.
2. **Isolate the runner from anything sensitive.** No secrets beyond
   what this one workflow needs (`GITHUB_TOKEN`, already scoped by
   `permissions:` in `test.yml`), no shared credentials with other
   systems on the same host/network, no unrelated data on the box.
3. **Run the runner as an unprivileged user** (§5.2) — it's in the
   `docker` group, which is effectively root-equivalent for that host
   (containers can mount the host filesystem), so treat "who can push a
   workflow that runs on this box" as equivalent to "who has root on
   this box."
4. **Keep the runner and Docker updated.** The self-hosted runner binary
   auto-updates by default; make sure host OS/Docker patching has an
   owner too.

## 7. Rollout plan

1. Stand up the runner per §5, confirm **Idle** status, pre-warm the
   image (§5.4).
2. Push a throwaway branch with just the `runs-on` change from §4 (not
   this branch/PR) and trigger it manually or via a draft PR, to
   validate end-to-end before touching the real CI path. Watch for:
   - Container job startup working identically to hosted (it should —
     same `container:` block).
   - `actions/cache` restore/save working over the network from this
     host.
   - Confirm the resource ceiling problem is actually gone: watch
     `docker stats` / `free -h` on the host during a run instead of
     finding out via a dead runner again.
3. Once validated, apply §4's changes to `test.yml` for real (this can
   land as a follow-up commit on this PR, or a new one).
4. Watch the first several real runs closely; tune `timeout-minutes`
   and the resource table in §5.1 from actual observed usage.
5. Decide on §6.2's disk-cleanup cadence once you have a sense of real
   growth rate (Docker build cache + parsl `runinfo/` + Postgres data
   under repeated runs).

**Rollback**: revert `runs-on` to `ubuntu-latest` (and drop the
`concurrency`/`timeout-minutes` additions if desired) — a one-line
change, no other workflow structure depends on running self-hosted.

## 8. Operational notes

### 8.1 Monitoring

At minimum, keep an eye on:
- Runner online/offline status (`Settings → Actions → Runners`).
- Host disk usage (Docker images/build cache, Postgres data under
  `${REPO}/_registry`, parsl `runinfo/` directories all grow over time).
- Whether jobs are queueing behind each other (a sign it's time to think
  about a second runner or the autoscaling path called out as
  non-goal in §3).

### 8.2 Cleanup cadence

Nothing here auto-expires the way a fresh GitHub-hosted VM does. Plan
for periodic (e.g. weekly, via a simple cron job) cleanup:

```bash
docker system prune -f --filter "until=168h"   # unused images/layers older than a week
# plus whatever retention makes sense for old runinfo/ and repo work dirs
```

### 8.3 Updating the pinned image tag

When `Dockerfile` changes and `build-image` pushes a new
`lsst-w_2024_34` (or a future tag), the self-hosted runner's local
Docker cache will pull the new layers on the next job run automatically
— no manual action needed, this is exactly the layer-cache behavior
Docker already gives you.
