#!/usr/bin/env python
"""
Diagnostic script: for every file listed in a downloaded_exposures.ecsv's
`path` column (as written by make_exposures.py), check two independent
things:

  1. astropy.io.fits can open the file and its header is actually
     populated -- i.e. this is a plain file-access / file-content check,
     completely independent of the LSST pipeline.
  2. Every per-detector header astro_metadata_translator would hand to
     lsst.obs.base.RawIngestTask can be translated into an ObservationInfo.

Splitting these into two separate checks isolates *where* a problem is:
if (1) fails, it's a file-access/corruption problem upstream of anything
LSST-specific (bad symlink, truncated LFS pull, etc.). If (1) succeeds but
(2) fails, the file itself is fine and the problem is specific to how the
pipeline's metadata translation reads these headers.

Check (2) deliberately mirrors lsst.obs.base.RawIngestTask.extractMetadata
rather than doing a single translation attempt:

    header = readMetadata(path, 0)
    translator_class = MetadataTranslator.determine_translator(header, ...)
    headers = list(translator_class.determine_translatable_headers(path, header))
    datasets = [self._calculate_dataset_info(h, filename) for h in headers]

For DECam, determine_translatable_headers() (see
astro_metadata_translator.translators.decam.DecamTranslator) walks *every*
HDU that carries a CCDNUM <= 62 (i.e. every science detector, skipping
guide CCDs), merges each with the primary header, and yields one header
per detector -- commonly around 60 for a full-focal-plane exposure. Because
extractMetadata builds datasets for all of them in a single list
comprehension with no per-header try/except, a single bad detector header
anywhere in the file fails metadata extraction for the *whole file*, which
is exactly the generic "Could not extract observation metadata" warning
ingest logs with no further detail. A check against only the primary or
primary+first-extension header (as an earlier version of this script did)
can pass while this file-wide check fails, if the problem is on some other
detector extension.

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
    "EXPNUM",
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


def check_all_detector_headers(path):
    """
    Reproduce lsst.obs.base.RawIngestTask.extractMetadata's metadata pass
    over *path*: determine the translator class from the primary header,
    ask it for every per-detector header it would hand to ingest, and try
    to build an ObservationInfo from each one.

    Returns (translator_available, per_detector_results, error) where:
      - translator_available is False (with an explanatory error) if
        astro_metadata_translator itself couldn't be imported/used at all
      - per_detector_results is a list of (label, ok, message) tuples, one
        per header determine_translatable_headers() yielded
      - error is set if determining the translator or the header list
        itself failed (i.e. we never got as far as per-detector headers)
    """
    try:
        from astro_metadata_translator import MetadataTranslator, ObservationInfo
    except ImportError as e:
        return False, [], f"astro_metadata_translator not importable: {e}"

    try:
        with fits.open(path) as hdul:
            primary_header = hdul[0].header
    except Exception as e:
        return True, [], f"could not open file: {type(e).__name__}: {e}"

    try:
        translator_class = MetadataTranslator.determine_translator(primary_header, filename=path)
    except Exception as e:
        return True, [], f"determine_translator(primary header) failed: {type(e).__name__}: {e}"

    try:
        headers = list(translator_class.determine_translatable_headers(path, primary_header))
    except Exception as e:
        return True, [], f"determine_translatable_headers() failed: {type(e).__name__}: {e}"

    if not headers:
        return True, [], "determine_translatable_headers() yielded no per-detector headers"

    results = []
    for i, header in enumerate(headers):
        ccdnum = header.get("CCDNUM", "?")
        label = f"header {i + 1}/{len(headers)} (CCDNUM={ccdnum})"
        try:
            obs_info = ObservationInfo(header, filename=path)
        except Exception as e:
            results.append((label, False, f"{type(e).__name__}: {e}"))
            continue
        results.append(
            (
                label,
                True,
                f"obs_type={obs_info.observation_type!r} exposure_id={obs_info.exposure_id!r}",
            )
        )
    return True, results, None


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
    translator_failures = []  # (path, reason) for whole-file-level failures
    translator_available = True

    for row in table:
        path = row["path"]
        print(f"\n=== {path} ===")

        ok, msg = check_fits_header(path)
        print(f"  fits.open:                  {'OK' if ok else 'FAIL'} - {msg}")
        if not ok:
            fits_failures.append((path, msg))
            continue  # nothing to translate if the file itself won't open

        available, results, error = check_all_detector_headers(path)
        if not available:
            translator_available = False
            print(f"  per-detector translation:   SKIPPED - {error}")
            continue
        if error is not None:
            print(f"  per-detector translation:   FAIL - {error}")
            translator_failures.append((path, error))
            continue

        n_ok = sum(1 for _, ok, _ in results if ok)
        n_bad = len(results) - n_ok
        if n_bad == 0:
            print(f"  per-detector translation:   OK - all {len(results)} detector header(s) translated")
        else:
            print(
                f"  per-detector translation:   FAIL - {n_bad}/{len(results)} detector header(s) "
                f"did not translate (RawIngestTask fails the whole file when this happens):"
            )
            for label, ok, msg in results:
                if not ok:
                    print(f"    - {label}: {msg}")
            translator_failures.append((path, f"{n_bad}/{len(results)} detector headers failed"))

    n_total = len(table)
    print(f"\n{n_total} file(s) checked")
    print(f"{len(fits_failures)} fits.io failure(s)")
    if translator_available:
        print(f"{len(translator_failures)} file(s) that RawIngestTask would report as metadata failures")
    else:
        print("per-detector translation checks skipped (astro_metadata_translator not importable)")

    if fits_failures:
        print("\nFITS access failures (file-access/content problem, not LSST-specific):")
        for path, msg in fits_failures:
            print(f"  {path}: {msg}")

    if translator_failures:
        print("\nFiles that fail RawIngestTask-style metadata extraction:")
        for path, msg in translator_failures:
            print(f"  {path}: {msg}")

    if fits_failures or translator_failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
