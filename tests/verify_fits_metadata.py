#!/usr/bin/env python
"""
Diagnostic script: for every file listed in a downloaded_exposures.ecsv's
`path` column (as written by make_exposures.py), check two independent
things:

  1. astropy.io.fits can open the file and its header is actually
     populated -- i.e. this is a plain file-access / file-content check,
     completely independent of the LSST pipeline.
  2. astro_metadata_translator -- the same library lsst.obs.base's raw
     ingest task uses under the hood to turn a FITS header into an
     ObservationInfo/exposure record -- can translate that header.

Splitting these into two separate checks isolates *where* a problem is:
if (1) fails, it's a file-access/corruption problem upstream of anything
LSST-specific (bad symlink, truncated LFS pull, etc.). If (1) succeeds but
(2) fails, the file itself is fine and the problem is specific to how the
pipeline's metadata translation reads these headers (wrong translator,
missing/unexpected keywords, etc.).

Since DECam raw files are multi-extension FITS (a primary HDU carrying the
observation-level header, plus one compressed-image extension per CCD),
translation is attempted twice: once against the primary header alone,
and once against the primary header merged with the first extension's
header, since instrument translators vary in which they expect.

Usage:
    python tests/verify_fits_metadata.py path/to/downloaded_exposures.ecsv
"""

import argparse
import sys

import astropy.io.fits as fits
import astropy.table

# A handful of keywords commonly needed to identify a DECam exposure.
# Not exhaustive -- just enough to eyeball whether a header looks complete
# or suspiciously empty/truncated.
_INTERESTING_KEYS = (
    "INSTRUME",
    "OBSTYPE",
    "OBS-TYPE",
    "DATE-OBS",
    "EXPTIME",
    "FILTER",
    "PROPID",
    "DTINSTRU",
)


def check_fits_header(path):
    """
    Open *path* with astropy.io.fits and confirm a readable, non-trivial
    header exists.

    Returns (ok, message).
    """
    try:
        with fits.open(path) as hdul:
            if len(hdul) == 0:
                return False, "FITS file has no HDUs"

            header = hdul[0].header
            used_hdu = 0
            if len(header) == 0 and len(hdul) > 1:
                # DECam .fits.fz primary HDUs are sometimes trivial; the
                # real header content can be on the first extension.
                header = hdul[1].header
                used_hdu = 1

            if len(header) == 0:
                return False, "primary and first-extension headers are both empty"

            present = [k for k in _INTERESTING_KEYS if k in header]
            missing = [k for k in _INTERESTING_KEYS if k not in header]
            return True, (
                f"{len(hdul)} HDU(s), {len(header)} card(s) in HDU[{used_hdu}]; "
                f"present={present} missing={missing}"
            )
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _merged_header(primary, extension):
    """Return a copy of *primary* with *extension*'s cards layered on top."""
    merged = primary.copy()
    merged.extend(extension, update=True)
    return merged


def check_metadata_translation(path):
    """
    Run astro_metadata_translator over *path*'s header(s), mirroring what
    LSST's raw-ingest task does to turn a FITS header into an exposure
    record.

    Tries the primary header alone, then the primary header merged with
    the first extension's header (if present), since which one an
    instrument's translator expects can vary.

    Returns a list of (attempt_label, ok, message) tuples.
    """
    try:
        from astro_metadata_translator import ObservationInfo
    except ImportError as e:
        return [(None, None, f"astro_metadata_translator not importable: {e}")]

    results = []
    try:
        with fits.open(path) as hdul:
            primary = hdul[0].header
            attempts = [("primary header", primary)]
            if len(hdul) > 1:
                attempts.append(
                    ("primary+extension[1] header", _merged_header(primary, hdul[1].header))
                )
    except Exception as e:
        return [("open", False, f"{type(e).__name__}: {e}")]

    for label, header in attempts:
        try:
            obs_info = ObservationInfo(header, filename=path)
        except Exception as e:
            results.append((label, False, f"{type(e).__name__}: {e}"))
            continue
        summary = (
            f"instrument={obs_info.instrument!r} "
            f"obs_id={obs_info.observation_id!r} "
            f"obs_type={obs_info.observation_type!r} "
            f"date_obs={obs_info.datetime_begin!r} "
            f"detector={obs_info.detector_num!r}"
        )
        results.append((label, True, summary))

    return results


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "downloaded_exposures", help="Path to downloaded_exposures.ecsv (must have a 'path' column)"
    )
    args = parser.parse_args()

    table = astropy.table.Table.read(args.downloaded_exposures, format="ascii.ecsv")
    if "path" not in table.colnames:
        print(
            f"::error::'path' column not found in {args.downloaded_exposures}",
            file=sys.stderr,
        )
        sys.exit(2)

    fits_failures = []
    translator_failures = []
    translator_available = True

    for row in table:
        path = row["path"]
        print(f"\n=== {path} ===")

        ok, msg = check_fits_header(path)
        print(f"  fits.open:            {'OK' if ok else 'FAIL'} - {msg}")
        if not ok:
            fits_failures.append((path, msg))
            continue  # nothing to translate if the file itself won't open

        any_translation_ok = False
        for label, t_ok, t_msg in check_metadata_translation(path):
            if t_ok is None:
                translator_available = False
                print(f"  metadata_translator:  SKIPPED - {t_msg}")
                continue
            print(f"  metadata_translator [{label}]: {'OK' if t_ok else 'FAIL'} - {t_msg}")
            any_translation_ok = any_translation_ok or t_ok

        if translator_available and not any_translation_ok:
            translator_failures.append(path)

    n_total = len(table)
    print(f"\n{n_total} file(s) checked")
    print(f"{len(fits_failures)} fits.io failure(s)")
    if translator_available:
        print(f"{len(translator_failures)} metadata_translator failure(s) (all attempts failed)")
    else:
        print("metadata_translator checks skipped (astro_metadata_translator not importable)")

    if fits_failures:
        print("\nFITS access failures (file-access/content problem, not LSST-specific):")
        for path, msg in fits_failures:
            print(f"  {path}: {msg}")

    if translator_failures:
        print(
            "\nMetadata translation failures (file opens fine, but the translator could "
            "not understand any header variant tried -- likely wrong translator / "
            "unexpected keywords):"
        )
        for path in translator_failures:
            print(f"  {path}")

    if fits_failures or translator_failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
