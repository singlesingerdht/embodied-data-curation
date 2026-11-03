"""Action smoothness: end-effector shake, measured as acceleration in a fixed band of motion frequency.

1. Deliberate motion is the part slower than band_hz[0] (2 Hz). Shake is the part between band_hz[0] and
   top = min(band_hz[1], band_rate_ratio x sampling rate): 2-10 Hz at 25 Hz sampling and above, 2-6 Hz at 15 Hz.
   The band is fixed in Hz (motion frequency), so the result does not grow with the sampling rate.
   Below min_rate_hz the band is too narrow: not applicable.
2. Shake acceleration (m/s^2): the position's band part differentiated twice, exactly, in the frequency domain.
3. Still = deliberate speed below still_speed_m_s for at least still_min_s; everything else is motion.
4. Verdict from the still frames only: RMS shake acceleration above still_review_m_s2 -> review. Motion frames
   get the same number for reference, not judged: a fast deliberate reach or stop also reaches the band
   (a 20 cm reach in 0.5 s alone gives 4-5 m/s^2), so there it measures briskness as much as shake.
5. Freeze: position exactly unchanged for freeze_min_s while it was moving just before (reading stopped) -> review.
No still stretch and no freeze: not applicable. The method never excludes on its own.

Rows are split at time gaps (> gap_factor x median interval and > half the shortest shake period); each piece is put on an even grid at its own
median interval (no upsampling) and edge_s is dropped at both ends, where filters are unreliable.
"""
import numpy as np
from scipy.signal import butter, sosfiltfilt, sosfreqz

DEFAULTS = {'band_hz': [2.0, 10.0], 'band_rate_ratio': 0.4, 'min_rate_hz': 10.0,
            'still_speed_m_s': 0.01, 'still_min_s': 0.3,
            'still_review_m_s2': 1.0,
            'freeze_min_s': 1.0, 'freeze_speed_m_s': 0.05, 'gap_factor': 3.0, 'edge_s': 0.5, 'min_piece_s': 2.0}
VERSION = 'shake_acceleration_band_v2'


def band_of(rate, p):
    low, high = p['band_hz']
    return low, min(high, p['band_rate_ratio'] * rate)


def pieces(t, gap_factor, high):
    """Index ranges between time gaps. A gap must also exceed half the shortest shake period (0.05 s at
    10 Hz): shorter holes cannot hide shake in the band, and high-rate topics jitter by more than 3 rows."""
    dt = np.diff(t)
    cut = np.flatnonzero(dt > max(gap_factor * np.median(dt), 0.5 / high)) + 1
    edges = [0, *cut, len(t)]
    return [(a, b) for a, b in zip(edges[:-1], edges[1:])]


def band_acceleration(x, rate, low, high):
    """Zero-phase band part of x (N x 3), differentiated twice: |Butterworth|^2 x (2 pi f)^2, via FFT."""
    pad = min(len(x) - 1, int(rate))
    trend = np.linspace(x[0], x[-1], len(x))
    y = np.pad(x - trend, ((pad, pad), (0, 0)), mode='reflect', reflect_type='odd')
    f = np.fft.rfftfreq(len(y), 1 / rate)
    sos = butter(4, [low, high], btype='band', fs=rate, output='sos')
    gain = np.abs(sosfreqz(sos, worN=f, fs=rate)[1]) ** 2 * (2 * np.pi * f) ** 2
    acc = np.fft.irfft(np.fft.rfft(y, axis=0) * gain[:, None], n=len(y), axis=0)
    return acc[pad:pad + len(x)]


def runs(mask):
    """(start, end) of each True run."""
    d = np.diff(np.r_[0, mask.astype(int), 0])
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def worst_window(a2, mask, t, width):
    """Largest RMS over windows of `width` samples inside mask -> (rms, time)."""
    k = np.ones(width)
    num, den = np.convolve(a2 * mask, k, 'same'), np.convolve(mask.astype(float), k, 'same')
    ok = mask & (den >= width / 2)
    if not ok.any():
        return None, None
    rms = np.sqrt(num[ok] / den[ok])
    i = int(np.argmax(rms))
    return float(rms[i]), float(t[ok][i])


def freezes(positions, t, p):
    """Position exactly unchanged >= freeze_min_s while the last 0.1 s before it moved faster than
    freeze_speed_m_s: the reading stopped mid-motion. A real stop slows down first and is not counted."""
    same = np.r_[False, np.all(np.diff(positions, axis=0) == 0, axis=1)]
    found = []
    for a, b in runs(same):
        a -= 1  # The first of the identical rows.
        if t[b - 1] - t[a] < p['freeze_min_s'] or a < 1:
            continue
        back = max(0, a - max(2, int(round(0.1 / np.median(np.diff(t))))))
        speed = np.linalg.norm(positions[a] - positions[back]) / max(t[a] - t[back], 1e-9)
        if speed > p['freeze_speed_m_s']:
            found.append({'start_s': float(t[a] - t[0]), 'duration_s': float(t[b - 1] - t[a]),
                          'speed_before_m_s': float(speed)})
    return found


def assess_arrays(positions, timestamps, params=None):
    """positions N x 3 in metres, timestamps N in seconds (strictly increasing) -> report."""
    p = {**DEFAULTS, **(params or {})}
    x, t = np.asarray(positions, float), np.asarray(timestamps, float)
    rate = float(1 / np.median(np.diff(t)))
    low, high = band_of(rate, p)
    report = {'method_version': VERSION, 'score': None, 'assessment_complete': True,
              'smoothness': {'rate_hz': rate, 'band_hz': [low, high], 'params': p}}
    if rate < p['min_rate_hz'] or high <= low:
        return {**report, 'verdict': 'NOT_APPLICABLE', 'not_applicable': 'low_rate',
                'reasons': [f"采样率只有 {rate:.1f} Hz，低于 {p['min_rate_hz']:g} Hz，看不清 2 Hz 以上的抖动，不检查平滑度"]}

    acc, still, valid, grid = [], [], [], []
    for a, b in pieces(t, p['gap_factor'], high):
        if t[b - 1] - t[a] < p['min_piece_s']:
            continue
        dt = float(np.median(np.diff(t[a:b])))
        g = np.arange(t[a], t[b - 1] + dt / 2, dt)
        xg = np.column_stack([np.interp(g, t[a:b], x[a:b, i]) for i in range(3)])
        acc.append(np.linalg.norm(band_acceleration(xg, 1 / dt, low, high), axis=1))
        slow = sosfiltfilt(butter(2, low, fs=1 / dt, output='sos'), xg, axis=0)
        speed = np.linalg.norm(np.gradient(slow, dt, axis=0), axis=1)
        mask = speed < p['still_speed_m_s']
        for s, e in runs(mask):
            if g[e - 1] - g[s] < p['still_min_s']:
                mask[s:e] = False
        still.append(mask)
        valid.append((g >= g[0] + p['edge_s']) & (g <= g[-1] - p['edge_s']))
        grid.append(g)
    if not grid or not np.concatenate(valid).any():
        return {**report, 'verdict': 'NOT_APPLICABLE', 'not_applicable': 'too_short',
                'reasons': [f"去掉时间断档和首尾各 {p['edge_s']:g} 秒后，没有足够长的连续数据"]}

    acc, still, valid, grid = map(np.concatenate, (acc, still, valid, grid))
    width = max(3, int(round(0.5 * rate)))
    parts = {}
    for name, mask in (('still', still & valid), ('motion', ~still & valid)):
        n = int(mask.sum())
        enough = n * (1 / rate) >= p['still_min_s']
        rms = float(np.sqrt(np.mean(acc[mask] ** 2))) if enough else None
        peak, at = worst_window(acc ** 2, mask, grid, width) if enough else (None, None)
        parts[name] = {'rms_m_s2': rms, 'seconds': n / rate, 'worst_0p5s_m_s2': peak,
                       'worst_at_s': None if at is None else at - t[0]}
    still_rms, frozen = parts['still']['rms_m_s2'], freezes(x, t, p)
    parts['still']['review_m_s2'] = p['still_review_m_s2']
    report['smoothness'].update(parts, freezes=frozen)
    report['item_metrics'] = {f'{k}_shake_m_s2': round(v['rms_m_s2'], 3) for k, v in parts.items() if v['rms_m_s2'] is not None}
    if still_rms is None and not frozen:
        return {**report, 'verdict': 'NOT_APPLICABLE', 'not_applicable': 'no_still',
                'reasons': [f"手没有停住过（慢于 {p['still_speed_m_s'] * 100:g} 厘米/秒、{p['still_min_s']:g} 秒以上），"
                            '抖动只在停住时判断，这条不判']}
    shaking = still_rms is not None and still_rms > p['still_review_m_s2']
    return {**report, 'verdict': 'NEEDS_REVIEW' if shaking or frozen else 'ACCEPTABLE', 'reasons': []}
