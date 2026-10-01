"""Crop a Siril mosaic to a fully populated rectangle, retaining its full FITS."""

from __future__ import annotations

import re
import tempfile
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, NoConvergence

from lib.type_defs import JSONReport


def largest_covered_rectangle(coverage: np.ndarray) -> tuple[int, int, int, int]:
    """Largest axis-aligned all-True rectangle, returned as x, y, width, height.

    A histogram stack is evaluated at changes of height rather than at every
    column, keeping large, mostly filled astronomical mosaics inexpensive.
    Equal-area rectangles prefer the one nearest the image centre.
    """
    if coverage.ndim != 2 or not coverage.size:
        raise ValueError("Le masque de couverture doit être une image 2D non vide")
    rows, columns = coverage.shape
    heights = np.zeros(columns, dtype=np.int64)
    best, best_area, best_distance = None, 0, float("inf")
    for y, row in enumerate(coverage):
        heights = np.where(row, heights + 1, 0)
        changes = np.r_[0, np.flatnonzero(heights[1:] != heights[:-1]) + 1, columns]
        stack = []
        for x in changes:
            height = int(heights[x]) if x < columns else 0
            start = int(x)
            while stack and stack[-1][1] > height:
                left, previous_height = stack.pop()
                width = int(x) - left
                top = y + 1 - previous_height
                area = width * previous_height
                distance = (2 * left + width - columns) ** 2 + (
                    2 * top + previous_height - rows
                ) ** 2
                if area > best_area or (area == best_area and distance < best_distance):
                    best = (left, top, width, previous_height)
                    best_area, best_distance = area, distance
                start = left
            if height and (not stack or stack[-1][1] < height):
                stack.append((start, height))
    if best is None:
        raise ValueError(
            "Aucune zone couverte dans la mosaïque ; le FITS non recadré est conservé"
        )
    return best


def _shift_header(header: fits.Header, x: int, y: int) -> fits.Header:
    """Translate FITS pixel reference points, including alternate WCS and SIP."""
    header = header.copy()
    for key in list(header):
        match = re.fullmatch(r"CRPIX([12])[A-Z]?", key)
        if match:
            header[key] = (
                float(header[key]) - (x if match[1] == "1" else y),
                header.comments[key],
            )
    for key, shift in (("LTV1", x), ("LTV2", y)):
        if key in header:
            header[key] = (float(header[key]) - shift, header.comments[key])
    return header


def panel_retention(
    panel_paths: Sequence[Path | str],
    mosaic_header: fits.Header,
    bounds: tuple[int, int, int, int],
) -> list[JSONReport]:
    """Count valid panel pixel centres inside the proposed crop using celestial WCS."""
    mosaic_wcs = WCS(mosaic_header, naxis=2).celestial
    if not mosaic_wcs.has_celestial:
        raise ValueError("WCS céleste absent de la mosaïque")
    x, y, width, height = bounds
    results = []
    for path in panel_paths:
        total = retained = 0
        with fits.open(path, memmap=False) as hdul:
            data = hdul[0].data
            if data is None or data.ndim not in (2, 3):
                raise ValueError(f"Panneau FITS invalide : {path}")
            wcs = WCS(hdul[0].header, naxis=2).celestial
            if not wcs.has_celestial:
                raise ValueError(f"WCS céleste absent du panneau : {path}")
            planes = data[None] if data.ndim == 2 else data
            for start in range(0, data.shape[-2], 128):
                block = planes[:, start : start + 128, :]
                valid = np.all(np.isfinite(block), axis=0) & np.any(block != 0, axis=0)
                yy, xx = np.nonzero(valid)
                if not xx.size:
                    continue
                ra, dec = wcs.all_pix2world(xx, yy + start, 0, ra_dec_order=True)
                mx, my = mosaic_wcs.all_world2pix(ra, dec, 0, ra_dec_order=True)
                if not (np.isfinite(mx).all() and np.isfinite(my).all()):
                    raise ValueError(f"Projection WCS invalide : {path}")
                total += int(xx.size)
                retained += int(
                    np.count_nonzero(
                        (mx >= x - 0.5)
                        & (mx < x + width - 0.5)
                        & (my >= y - 0.5)
                        & (my < y + height - 0.5)
                    )
                )
        if not total:
            raise ValueError(f"Panneau sans pixels valides : {path}")
        results.append(
            dict(
                panel=str(path),
                total_pixels=total,
                retained_pixels=retained,
                removed_pixels=total - retained,
                removed_fraction=(total - retained) / total,
            )
        )
    if not results:
        raise ValueError("Aucun panneau disponible pour vérifier le recadrage")
    return results


def crop_mosaic(
    uncropped_path: Path | str,
    output_path: Path | str,
    panel_paths: Sequence[Path | str] | None = None,
) -> JSONReport:
    """Write a cropped copy; never write to or delete the original mosaic.

    Siril fills pixels without a panel contribution with zero. No brightness
    threshold is used: finite negative pixels and RGB pixels with at least one
    nonzero channel remain eligible. Nonfinite pixels are excluded in all cases.
    """
    source, destination = Path(uncropped_path).resolve(), Path(output_path).resolve()
    if source == destination or (destination.exists() and source.samefile(destination)):
        raise ValueError("Le FITS recadré doit être distinct de la mosaïque complète")
    with fits.open(source, memmap=False) as hdul:
        data = hdul[0].data
        if (
            data is None
            or data.ndim not in (2, 3)
            or (data.ndim == 3 and data.shape[0] != 3)
        ):
            raise ValueError(
                "Mosaïque FITS monochrome ou RGB requise ; original conservé"
            )
        planes = data[None] if data.ndim == 2 else data
        shape = data.shape[-2:]
        coverage = np.empty(shape, dtype=bool)
        for start in range(0, shape[0], 128):
            block = planes[:, start : start + 128, :]
            coverage[start : start + 128] = np.all(np.isfinite(block), axis=0) & np.any(
                block != 0, axis=0
            )
        x, y, width, height = largest_covered_rectangle(coverage)
        panel_results = []
        if panel_paths is not None:
            reason = None
            detail = None
            try:
                panel_results = panel_retention(
                    panel_paths, hdul[0].header, (x, y, width, height)
                )
                if any(
                    2 * p["removed_pixels"] > p["total_pixels"] for p in panel_results
                ):
                    reason = "panel_loss_exceeds_half"
            except (ValueError, OSError, RuntimeError, NoConvergence) as exc:
                reason, detail = "panel_coverage_unavailable", str(exc)
            if reason:
                return dict(
                    enabled=True,
                    applied=False,
                    reason=reason,
                    detail=detail,
                    uncropped_image=str(source),
                    output_image=str(source),
                    original_shape=list(shape),
                    cropped_shape=list(shape),
                    bounds=dict(x=0, y=0, width=shape[1], height=shape[0]),
                    proposed_bounds=dict(x=x, y=y, width=width, height=height),
                    retained_fraction=1.0,
                    panel_retention=panel_results,
                    max_panel_removed_fraction=0.5,
                )
        rows, columns = slice(y, y + height), slice(x, x + width)
        cropped = []
        for hdu in hdul:
            if (
                isinstance(hdu, (fits.PrimaryHDU, fits.ImageHDU, fits.CompImageHDU))
                and hdu.data is not None
                and hdu.data.shape[-2:] == shape
            ):
                header = _shift_header(hdu.header, x, y)
                header.add_history(
                    f"Automatic mosaic crop: x={x}, y={y}, width={width}, height={height} (0-based)"
                )
                cropped.append(
                    type(hdu)(data=hdu.data[..., rows, columns].copy(), header=header)
                )
            else:
                cropped.append(hdu.copy())
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        output = fits.HDUList(cropped)
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".fits",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
            output.writeto(temporary, overwrite=True, checksum=True)
            temporary.replace(destination)
        finally:
            output.close()
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return dict(
        panel_retention=panel_results,
        max_panel_removed_fraction=0.5,
        enabled=True,
        applied=(x, y, width, height) != (0, 0, shape[1], shape[0]),
        method="largest_covered_rectangle",
        coverage_method="siril_zero_padding",
        uncropped_image=str(source),
        output_image=str(destination),
        original_shape=list(shape),
        cropped_shape=[height, width],
        bounds=dict(x=x, y=y, width=width, height=height),
        retained_fraction=width * height / (shape[0] * shape[1]),
    )
