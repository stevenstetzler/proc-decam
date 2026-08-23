#!/usr/bin/env python
"""
Create exposures.ecsv and downloaded_exposures.ecsv from local FITS files
in kbmod_mastercals_recipe and kbmod_imdiff_recipe clone directories.

exposures.ecsv columns required by proc-decam subcommands:
    obs_type, proc_type, night, md5sum

downloaded_exposures.ecsv columns (mirrors proc-decam download output):
    path, md5sum, did_download, valid_in_archive, valid_on_disk,
    did_check_archive, did_check_disk

The 'path' values in downloaded_exposures.ecsv point to the files on disk
after cloning the repositories (via symlinks placed in --image-dir).
"""

import argparse
import glob
import hashlib
import os

import astropy.table


_MD5_CHUNK_BYTES = 65536  # 64 KiB — balances memory use and I/O calls


def compute_md5(filepath):
    """Compute the MD5 hex digest of a file."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(_MD5_CHUNK_BYTES), b""):
            h.update(chunk)
    return h.hexdigest()


def find_fits_files(directory):
    """Return sorted list of FITS files in *directory* (non-recursive)."""
    files = []
    for pattern in ("*.fits", "*.fits.gz", "*.fits.fz", "*.fit"):
        files.extend(glob.glob(os.path.join(directory, pattern)))
    return sorted(files)


def _create_verified_symlink(filepath, dest):
    """
    Create (or refresh) a symlink at *dest* pointing to *filepath*, then
    verify it actually resolves to real, readable content.

    The LSST pipeline reads image data through this symlink, so a broken
    or stale one doesn't fail here -- it surfaces much later as a cryptic
    "could not extract observation metadata" warning deep inside the
    ingest task, with no indication that the symlink was ever the problem.
    Failing fast here instead gives an immediate, actionable error.
    """
    if not os.path.isfile(filepath):
        raise FileNotFoundError(
            f"Source FITS file does not exist or is not a regular file: "
            f"{filepath}"
        )

    # os.path.exists() follows symlinks, so a stale/dangling symlink left
    # at `dest` (e.g. from a previous run) would look "missing" and then
    # make os.symlink() below fail with FileExistsError. os.path.lexists()
    # sees the dirent itself, so we can clear it and always end up with a
    # fresh link to the current source file.
    if os.path.lexists(dest):
        os.remove(dest)
    os.symlink(filepath, dest)

    if not os.path.isfile(dest):
        raise RuntimeError(
            f"Symlink {dest} -> {filepath} does not resolve to a regular "
            f"file"
        )
    if not os.access(dest, os.R_OK):
        raise RuntimeError(f"Symlink {dest} -> {filepath} is not readable")
    if not os.path.samefile(dest, filepath):
        raise RuntimeError(f"Symlink {dest} does not point back at {filepath}")
    if os.path.getsize(dest) == 0:
        raise RuntimeError(f"Symlink {dest} -> {filepath} resolves to an empty file")


def add_files(rows, downloaded_rows, filepaths, obs_type, proc_type, night,
              image_dir):
    """
    For each FITS file in *filepaths*:
      - compute md5sum
      - create a verified symlink inside *image_dir* preserving the
        original basename
      - append a row to *rows* (exposures table)
      - append a row to *downloaded_rows* (downloaded table)
    """
    for filepath in filepaths:
        filepath = os.path.abspath(filepath)
        md5 = compute_md5(filepath)
        basename = os.path.basename(filepath)
        dest = os.path.join(image_dir, basename)
        _create_verified_symlink(filepath, dest)
        rows.append(
            {
                "obs_type": obs_type,
                "proc_type": proc_type,
                "night": night,
                "md5sum": md5,
            }
        )
        downloaded_rows.append(
            {
                "path": dest,
                "md5sum": md5,
                "did_download": False,
                "valid_in_archive": True,
                "valid_on_disk": True,
                "did_check_archive": False,
                "did_check_disk": True,
            }
        )


def write_tables(rows, downloaded_rows, output):
    """Write exposures.ecsv and downloaded_exposures.ecsv next to *output*."""
    output = os.path.abspath(output)
    output_dir = os.path.dirname(output)
    output_basename = os.path.basename(output)
    downloaded_output = os.path.join(output_dir, "downloaded_" + output_basename)

    exposures = astropy.table.Table(rows)
    downloaded = astropy.table.Table(downloaded_rows)

    exposures.write(output, format="ascii.ecsv", overwrite=True)
    downloaded.write(downloaded_output, format="ascii.ecsv", overwrite=True)

    print(f"Wrote exposures    → {output}")
    print(f"Wrote downloaded   → {downloaded_output}")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Create exposures.ecsv and downloaded_exposures.ecsv from "
            "local FITS files in the kbmod calibration and science repos."
        )
    )
    parser.add_argument(
        "--mastercals-dir",
        required=True,
        help=(
            "Path to the calibration base directory, e.g. "
            "kbmod_mastercals_recipe/trimmedRawData/210318/calib"
        ),
    )
    parser.add_argument(
        "--science-dir",
        required=True,
        help=(
            "Path to the science data directory, e.g. "
            "kbmod_imdiff_recipe/trimmedRawData/210318/science"
        ),
    )
    parser.add_argument(
        "--output",
        default="./data/exposures.ecsv",
        help="Output path for exposures.ecsv (default: ./data/exposures.ecsv)",
    )
    parser.add_argument(
        "--image-dir",
        default="./data/images",
        help=(
            "Directory into which symlinks to the FITS files are created. "
            "Must match the --image-dir used by proc-decam ingest "
            "(default: ./data/images)"
        ),
    )
    parser.add_argument(
        "--night",
        type=int,
        default=20210318,
        help=(
            "Observation night as an integer YYYYMMDD used to populate the "
            "'night' column (default: 20210318)"
        ),
    )
    args = parser.parse_args()

    image_dir = os.path.abspath(args.image_dir)
    os.makedirs(image_dir, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)

    rows = []
    downloaded_rows = []

    bias_dir = os.path.join(args.mastercals_dir, "bias")
    flat_dir = os.path.join(args.mastercals_dir, "flat")

    bias_files = find_fits_files(bias_dir)
    flat_files = find_fits_files(flat_dir)
    science_files = find_fits_files(args.science_dir)

    print(f"Found {len(bias_files)} bias file(s) in {bias_dir}")
    print(f"Found {len(flat_files)} flat file(s) in {flat_dir}")
    print(f"Found {len(science_files)} science file(s) in {args.science_dir}")

    add_files(rows, downloaded_rows, bias_files, "zero", "raw", args.night,
              image_dir)
    add_files(rows, downloaded_rows, flat_files, "dome flat", "raw",
              args.night, image_dir)
    add_files(rows, downloaded_rows, science_files, "object", "raw",
              args.night, image_dir)

    write_tables(rows, downloaded_rows, args.output)


if __name__ == "__main__":
    main()
