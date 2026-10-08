"""The exam's series: the images of each run, with where each lies."""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .geometry import ImagePlane


@dataclass(frozen=True)
class Series:
    """The images of one run of the exam.

    Attributes
    ----------
    number
        The run's place in the exam, from 1.
    description
        What acquired it: the plugin's name, or ``Localizer``.
    name
        The name MaRGE gives the run, by which its history entry knows it.
    images
        Each image, ``(rows, columns)``, its pixel values rescaled.
    planes
        Where each image lies.
    """

    number: int
    description: str
    name: str
    images: tuple[np.ndarray, ...]
    planes: tuple[ImagePlane, ...]

    @classmethod
    def of(cls, number: int, description: str, name: str, files: Sequence[bytes]) -> Series:
        """Return the series of a run's DICOM files.

        The images of a stack, all parallel, are ordered along its normal;
        others keep their instance order.
        """
        import pydicom

        datasets = [pydicom.dcmread(io.BytesIO(data)) for data in files]
        datasets.sort(key=lambda d: int(getattr(d, "InstanceNumber", 0) or 0))
        planes = [ImagePlane.of(d) for d in datasets]
        if len(planes) > 1 and all(abs(p.normal @ planes[0].normal) > 1.0 - 1e-6 for p in planes):
            depth = [p.origin @ planes[0].normal for p in planes]
            order = np.argsort(depth, kind="stable")
            datasets, planes = [datasets[k] for k in order], [planes[k] for k in order]
        images = tuple(
            d.pixel_array.astype(float) * float(getattr(d, "RescaleSlope", 1.0))
            + float(getattr(d, "RescaleIntercept", 0.0))
            for d in datasets
        )
        return cls(number, description, name, images, tuple(planes))

    @property
    def label(self) -> str:
        return f"S{self.number} {self.description}"
