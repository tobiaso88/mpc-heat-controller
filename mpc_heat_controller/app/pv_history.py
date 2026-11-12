"""Bounded reuse of observed zero production, never synthesis of missing values."""
import math
from datetime import datetime, timedelta

STALE_PV = 'Solcellsproduktionen är äldre än 30 minuter'
MAX_ZERO_AGE = timedelta(hours=18)
MIN_ZERO_PLATEAU = timedelta(hours=4)
MAX_SAMPLE_GAP = timedelta(minutes=15)


def is_zero(reading):
    value = reading.get('value')
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0


def confirmed_night_zero(moment, reading):
    """Use observed zero only with fresh, saved darkness evidence at that sample."""
    if not is_zero(reading) or reading.get('quality') != STALE_PV or reading.get('restored'):
        return False
    sun = reading.get('sun') or {}
    if sun.get('state') != 'below_horizon' or sun.get('restored'):
        return False
    try:
        reported = datetime.fromisoformat(reading['reported_at'].replace('Z', '+00:00'))
        solar_reported = datetime.fromisoformat(sun['reported_at'].replace('Z', '+00:00'))
        return (1800 < (moment-reported).total_seconds() <= MAX_ZERO_AGE.total_seconds()
                and -60 <= (moment-solar_reported).total_seconds() <= 1800)
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def recovered_zeros(samples):
    """Reuse stale zeros with saved darkness evidence or a bounded legacy plateau.

    Legacy logs lack solar elevation. This is a conservative plateau heuristic,
    not proof of darkness: require a fresh zero before the plateau, a fresh
    nonnegative report after it, 4–18 hours total, and continuous observations.
    Missing/restored/nonzero/other-invalid readings interrupt the plateau.
    """
    recovered = []
    left = previous = None
    pending = []
    for moment, reading in samples:
        if previous is not None and not timedelta(0) < moment-previous <= MAX_SAMPLE_GAP:
            left, pending = None, []
        previous = moment
        if confirmed_night_zero(moment, reading):
            recovered.append(moment)
            left, pending = moment, []
            continue
        # Explicit solar evidence must never be overridden by the legacy plateau heuristic.
        if reading.get('quality') == STALE_PV and 'sun' in reading:
            left, pending = None, []
            continue
        value = reading.get('value')
        fresh = (reading.get('quality') == 'OK' and isinstance(value, (int, float))
                 and not isinstance(value, bool) and math.isfinite(value) and value >= 0)
        if fresh:
            if left is not None and pending and MIN_ZERO_PLATEAU <= moment-left <= MAX_ZERO_AGE:
                recovered.extend(pending)
            left = moment if is_zero(reading) else None
            pending = []
        elif reading.get('quality') == STALE_PV and is_zero(reading) and left is not None:
            if moment-left <= MAX_ZERO_AGE:
                pending.append(moment)
            else:
                left, pending = None, []
        else:
            left, pending = None, []
    return recovered
