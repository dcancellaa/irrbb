"""Instruments: the :class:`Leg` primitive and its skeleton generators.

The portfolio factory lives in :mod:`irrbb.instruments.factory` (imported
explicitly to avoid a circular import with :mod:`irrbb.behavioral`).
"""

from irrbb.instruments.leg import Leg, LegSkeleton, leg_skeleton, nii_skeleton

__all__ = ["Leg", "LegSkeleton", "leg_skeleton", "nii_skeleton"]
