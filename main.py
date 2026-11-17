"""Forecast GTA VI's launch-day Twitch viewers, hour by hour.

The program reads one case per run: everything recorded up to the end of
the release day's first UTC hour, that hour's print included, plus the
release-day curves of past big launches (the case's `comparables`).
It writes, for each of hours 2 to 24, five quantiles of that hour's
concurrent-viewer count in the game's Twitch category.

Model
-----
Every past launch is reduced to its release-day shape: the log ratio of
each hour's viewers to its own hour-1 print. GTA VI unlocks at midnight
local time, rolling country by country, so its hour 1 already holds a
large audience; the launches that behave that way are the ones whose
hour-1 print is already a substantial fraction of their release-day
peak. Launches that unlock all at once later in the UTC day (a tiny
hour-1 print, then a vertical ramp) are a different regime and are left
out of the peer pool: their shapes would badly distort the forecast.

Within the peer pool, the log ratio of each hour is regressed on the
log of the hour-1 print. Bigger launches have flatter days -- hour 1
already captured most of the world -- and the regression carries that
(the correlation between log hour-1 size and log peak ratio is about
-0.8 across the peers). The target's log hour-1 print is winsorised to
the peers' observed range, so a launch bigger than any peer is forecast
with the flattest observed shape rather than an extrapolation.

Each hour's quantiles are the regression's fitted log ratio plus normal
quantiles times the hour's residual spread (inflated 10%, floored at
0.20 log units), centred on the case's own hour-1 print. Everything is
computed from the case file at run time; nothing is hard-coded from any
particular launch, and the program is deterministic.

Fallbacks keep every case valid: if the peer pool is too small the
regression falls back to every usable comparable, then to pooled median
ratios, then to a persistence forecast; any unexpected error in the
model path also lands on the persistence forecast rather than crashing.
"""

import json
import math
import os
from datetime import datetime
from pathlib import Path

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

DEFAULT_LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]

# A peer launch is one whose hour-1 print already reached this fraction
# of its release-day peak: the signature of a rolling-midnight unlock.
PEER_PEAK_FRACTION = 0.15
# A comparable needs at least this many of the 24 release-day hours.
MIN_DAY_HOURS = 20
# Below this many peers, widen the pool to every usable comparable.
MIN_PEERS = 8
# Spread calibration: residual sd is inflated, then floored and capped.
SPREAD_INFLATION = 1.1
SPREAD_FLOOR = 0.20
SPREAD_CAP = 1.2
# Fallback persistence spread, in log units per square-root hour.
FALLBACK_SIGMA = 0.15
# Output bounds from the forecast schema.
Q_MIN = 0.01
Q_MAX = 1_000_000_000.0


def parse_instant(text):
    """Parse an ISO 8601 UTC timestamp into a datetime."""
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def norm_ppf(p):
    """Inverse of the standard normal CDF (Acklam's rational approximation).

    Accurate to about 1e-9 in absolute terms, far tighter than the
    forecast needs. Only used for levels strictly inside (0, 1).
    """
    a = [-3.969683028665376e01, 2.209460984245205e02,
         -2.759285104469687e02, 1.383577518672690e02,
         -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02,
         -1.556989798598866e02, 6.680131188771972e01,
         -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e00, -2.549732539343734e00,
         4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01,
         2.445134137142996e00, 3.754408661907416e00]
    plow = 0.02425
    phigh = 1.0 - plow
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
        ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)


def day_curve(comparable):
    """A comparable's release-day curve as {hour 1..24: viewers}.

    Returns None when the curve is unusable: no day_start, no hour-1
    point, a non-positive hour-1 print, or too few hours present.
    """
    try:
        start = parse_instant(comparable["day_start"])
    except (KeyError, TypeError, ValueError):
        return None
    totals = {}
    counts = {}
    for point in comparable.get("hourly") or []:
        try:
            when = parse_instant(point["from"])
            viewers = float(point["viewers"])
        except (KeyError, TypeError, ValueError):
            continue
        hour = int((when - start).total_seconds() // 3600) + 1
        if 1 <= hour <= 24 and viewers >= 0:
            totals[hour] = totals.get(hour, 0.0) + viewers
            counts[hour] = counts.get(hour, 0) + 1
    if len(totals) < MIN_DAY_HOURS:
        return None
    curve = {h: totals[h] / counts[h] for h in totals}
    if curve.get(1, 0.0) <= 0:
        return None
    return curve


def peer_pool(comparables):
    """Split usable comparables into rolling-unlock peers and the rest.

    Returns (peers, all_usable): each entry is (log hour-1 print, curve).
    The peer test is the hour-1 print's fraction of the release-day peak.
    """
    usable = []
    for comparable in comparables or []:
        curve = day_curve(comparable)
        if curve is None:
            continue
        peak = max(curve.values())
        if peak <= 0:
            continue
        usable.append((math.log(curve[1]), curve, curve[1] / peak))
    peers = [(x, curve) for (x, curve, frac) in usable if frac > PEER_PEAK_FRACTION]
    everyone = [(x, curve) for (x, curve, _frac) in usable]
    return peers, everyone


def fit_line(xs, ys):
    """Ordinary least squares of ys on xs; returns (intercept, slope, sd).

    sd is the residual standard deviation with n - 2 degrees of freedom.
    With fewer than 3 points the slope is 0 and sd is the plain spread
    of ys around its mean (or 0.5 when even that is unavailable).
    """
    n = len(xs)
    if n == 0:
        return 0.0, 0.0, 0.5
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if n < 3 or sxx <= 0:
        spread = math.sqrt(sum((y - mean_y) ** 2 for y in ys) / n) if n else 0.5
        return mean_y, 0.0, spread
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    sse = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    sd = math.sqrt(sse / (n - 2))
    return intercept, slope, sd


def median(values):
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def model_quantiles(pool, p1, hours):
    """Per-hour median log ratios and spreads from the peer pool.

    For each hour, regress log(viewers / hour-1 print) on the log
    hour-1 print across peers, evaluate at the target's (winsorised)
    log hour-1 print, and take the residual spread as the log spread.
    Returns {hour: (median_log_ratio, spread)}.
    """
    xs = [x for (x, _curve) in pool]
    lo, hi = min(xs), max(xs)
    target_x = min(max(math.log(p1), lo), hi)
    fitted = {}
    for hour in hours:
        pairs = [(x, math.log(curve[hour] / curve[1]))
                 for (x, curve) in pool
                 if hour in curve and curve[hour] > 0]
        if len(pairs) >= 3:
            intercept, slope, sd = fit_line([p[0] for p in pairs],
                                            [p[1] for p in pairs])
            centre = intercept + slope * target_x
        elif pairs:
            centre = median([p[1] for p in pairs])
            sd = math.sqrt(sum((p[1] - centre) ** 2 for p in pairs) / len(pairs))
        else:
            centre, sd = 0.0, 0.5
        spread = min(SPREAD_CAP, max(SPREAD_FLOOR, SPREAD_INFLATION * sd))
        fitted[hour] = (centre, spread)
    return fitted


def shape_rows(p1, hours, fitted, z_values):
    """Turn fitted log ratios into clamped, monotone quantile rows."""
    rows = []
    for hour in hours:
        centre, spread = fitted[hour]
        quantiles = []
        running = 0.0
        for z in z_values:
            value = p1 * math.exp(centre + z * spread)
            value = min(Q_MAX, max(Q_MIN, value))
            value = math.floor(value * 100.0 + 0.5) / 100.0
            value = max(value, running)
            running = value
            quantiles.append(value)
        rows.append({"hour": hour, "q": quantiles})
    return rows


def persistence_rows(p1, hours, z_values):
    """Fallback: every hour stays at the hour-1 print, spread widening
    with the square root of the hours since hour 1."""
    fitted = {}
    for hour in hours:
        fitted[hour] = (0.0, FALLBACK_SIGMA * math.sqrt(max(1, hour - 1)))
    return shape_rows(p1, hours, fitted, z_values)


def hour1_print(case):
    """The case's hour-1 print, at least 1 viewer."""
    known = case.get("known") or []
    if isinstance(known, dict):
        known = [known]
    for row in known:
        if isinstance(row, dict) and row.get("hour") == 1:
            try:
                return max(1.0, float(row["viewers"]))
            except (KeyError, TypeError, ValueError):
                continue
    return 1.0


def forecast_hours(case):
    """The hours to forecast, from the case's forecast rows (2..24)."""
    rows = case.get("forecast") or []
    hours = []
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("hour"), int):
            hours.append(row["hour"])
    hours = sorted(set(h for h in hours if 2 <= h <= 24))
    return hours if len(hours) == 23 else list(range(2, 25))


def forecast_case(case):
    """Forecast one case. Never raises: any failure falls back to
    persistence, so the output is always a valid forecast."""
    p1 = hour1_print(case)
    hours = forecast_hours(case)
    levels = case.get("levels") or DEFAULT_LEVELS
    try:
        z_values = [norm_ppf(float(level)) for level in levels]
    except (TypeError, ValueError):
        z_values = [norm_ppf(level) for level in DEFAULT_LEVELS]
    try:
        peers, everyone = peer_pool(case.get("comparables"))
        pool = peers if len(peers) >= MIN_PEERS else everyone
        if len(pool) >= 3:
            fitted = model_quantiles(pool, p1, hours)
            return {"hours": shape_rows(p1, hours, fitted, z_values)}
    except Exception:
        pass
    return {"hours": persistence_rows(p1, hours, z_values)}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not INPUT_DIR.is_dir():
        return
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case)
        out_path = OUTPUT_DIR / (path.stem + ".out")
        out_path.write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
