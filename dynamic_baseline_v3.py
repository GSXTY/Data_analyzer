"""Engineering experiment v3: leak review hints and window-based weak events.

No patient-flow reconstruction, clinical scoring or device control. Strong
boundaries cover source-second bins; weak intervals use qualified main-window
ends. Neither uses fixed W-dependent onset compensation.
"""
from collections import deque
from datetime import datetime,timedelta,timezone
import hashlib
import json
import math
import numpy as np
import pandas as pd
from dynamic_baseline_comparison import source_seconds

PROFILE='dynamic_baseline_v3'
VERSION='dynamic-baseline-optimized-v3-configurable-v13'
WINDOWS=tuple(range(1,11))
TYPES=('强度显著下降候选','强度减弱候选','数据缺失','信号异常')
METHODS={'detrended_abs':'去背景绝对值均值÷2（试验）','centered_rms':'窗口去均值RMS（对照）','absolute_mean':'绝对值均值÷2（未去背景）'}


def names(window):
    return [f'优化v3{window}秒'+n for n in ('强度','参考','25%门限','50%门限','剩余比例','背景估计','1秒复核强度','1秒复核参考')]


CHANNELS=tuple(n for w in WINDOWS for n in names(w))


def settings(min_duration=10,event_types=None,dynamic_method=None,window_seconds=None):
    method='detrended_abs' if dynamic_method is None else dynamic_method
    window=5 if window_seconds is None else window_seconds
    if not isinstance(method,str) or method not in METHODS:raise ValueError('优化测试方法无效')
    if isinstance(window,bool) or not isinstance(window,(int,float)) or not math.isfinite(window) or window not in WINDOWS:raise ValueError('优化v3检测窗口请选择1～10秒整数')
    if isinstance(min_duration,bool) or not isinstance(min_duration,(int,float)) or not math.isfinite(min_duration) or min_duration not in range(5,11):raise ValueError('优化v3最短持续请选择5～10秒整数')
    if event_types is not None and (not isinstance(event_types,list) or not event_types or any(t not in TYPES for t in event_types)):raise ValueError('优化测试事件类型无效')
    return dict(profile=PROFILE,version=VERSION,method=method,window_seconds=int(window),min_duration=int(min_duration),
                reduction_ratio=.5,near_ratio=.25,extremely_low_ratio=.1,pretrigger_seconds=3,
                background_seconds=30,baseline_seconds=100,min_reference_seconds=30,long_episode_seconds=120,
                background_estimator='causal previous30 source seconds; raw-sample weighted median, equal total weight per source second',
                event_types=[t for t in TYPES if event_types is None or t in event_types],flow_source='csv_flow',
                timing='strong: source-bin coverage after supported flow-tail correction; weak: continuous main-window-end interval',
                boundary_review_seconds=1,baseline_policy='freeze at main OR 1s remaining<50%; restore valid non-event dips except persistent leak shifts; no leak-only reset',
                strong_rule=f'1s review <25% with main-window support and >={int(min_duration)}s after any flow-tail correction',
                weak_rule=f'selected main-window ratio <50% continuously >={int(min_duration)}s; window-end clock, no fixed backdating',
                leak_policy='changes >=10 L/min are review hints, not reference resets; no total-leak subtraction',
                patient_flow_verified=False,leak_subtraction=False,lowpass_1hz=False,clinical_scoring=False,device_control=False)


def _source(recording):
    cached=getattr(recording,'_dynamic_v2_source',None)
    if cached is None:
        source=source_seconds(recording)
        raw=np.asarray(recording.channels['流量'],float)
        indices=pd.Series(raw).groupby(np.floor(recording.x).astype(int)).indices
        groups=[]
        for i in source['index']:
            values=raw[indices[i]] if i in indices else np.empty(0)
            groups.append(values[np.isfinite(values)])
        cached=(source,groups)
        source['sample_weights']=[np.full(len(v),1/len(v)) if len(v) else np.empty(0) for v in groups]
        recording._dynamic_v2_source=cached
    return cached


def _intervals(records,predicate,clock_end=False):
    result=[];start=None;last=None
    for row in records:
        if predicate(row):
            if start is None:start=row['tick'] if clock_end else row['tick']-1
            last=row['tick']+1 if clock_end else row['tick']
        elif start is not None:
            result.append([float(start),float(last)]);start=None
    if start is not None:result.append([float(start),float(last)])
    return result


def _review_flags(source,start,end):
    flags=[];changes={}
    before=source['med'].loc[max(0,int(start)-30):max(0,int(start)-1)]
    during=source['med'].loc[int(start):max(int(start),int(math.ceil(end))-1)]
    for name,limit in (('压力',20),('治疗压',20),('漏气量',10)):
        if name not in before:continue
        delta=float(during[name].median()-before[name].median())
        steps=source['med'].loc[max(0,int(start)-1):int(math.ceil(end)),name].diff().abs().max()
        if math.isfinite(delta):changes[name]=delta
        if math.isfinite(delta) and abs(delta)>=limit or np.isfinite(steps) and steps>=limit:flags.append(name+'变化需复核')
    return flags,changes


def _flow_shape(groups):
    # Equal weight per source second. Row groups provide shape, not subsecond timestamps.
    return np.array([[np.median(part) for part in np.array_split(group, 4)] for group in groups])


def _early_recovery_evidence(source, raw_groups, end, fast_reference):
    """Check for a directional departure spanning the last bin and the next bin."""
    stop = int(end)
    if stop < 4 or stop >= len(raw_groups):
        return None
    indices = slice(stop - 4, stop + 1)
    if (not np.all(source['valid'][indices])
            or not np.all(source['segment'][indices] == source['segment'][stop])
            or any(len(group) < 8 for group in raw_groups[indices])):
        return None
    shape = _flow_shape(raw_groups[indices])
    plateau = shape[:3].ravel()
    level = float(np.median(plateau))
    # Require a departure larger than both local variation and a reference-relative floor.
    threshold = max(2 * np.ptp(plateau), .2 * fast_reference)
    last, following = shape[-2:] - level
    direction = np.sign(last[-1])
    early = direction * last
    continued = direction * following[:2]
    if (abs(last[0]) <= threshold and early[-1] > threshold
            and early[-1] > early[-2] >= early[0]
            and np.all(continued >= early[-1])):
        return dict(alternative_end=float(stop-1), plateau_flow=level,
                    last_second_flow=shape[-2].tolist(), next_second_flow=shape[-1].tolist(),
                    departure_threshold=float(threshold))
    return None


def _confirm_recovery_cycle(source, raw_groups, end, period, evidence, fast_reference):
    """Confirm a rise, return and repeated excursion at a compatible cadence."""
    if period is None:
        return None
    first = int(end) - 1
    limit = min(len(raw_groups), first + math.ceil(1.75 * period) + 1)
    stop = first
    while (stop < limit and source['valid'][stop]
           and source['segment'][stop] == source['segment'][first] and len(raw_groups[stop]) >= 8):
        stop += 1
    level = evidence['plateau_flow']
    direction = np.sign(evidence['last_second_flow'][-1] - level)
    shape = direction * (_flow_shape(raw_groups[first:stop]).ravel() - level)
    high = max(2 * evidence['departure_threshold'], fast_reference)
    low = high / 2
    peaks = np.flatnonzero(shape >= high)
    if not len(peaks) or peaks[0] / 4 > period / 2:
        return None
    first_crossing = peaks[0]
    returns = np.flatnonzero(shape[first_crossing+1:] <= low)
    if not len(returns):
        return None
    returned_at = first_crossing + 1 + returns[0]
    for crossing in peaks[peaks > returned_at]:
        interval = (crossing - first_crossing) / 4
        if .65 * period <= interval <= 1.35 * period:
            return dict(recovery_cycle_seconds=float(interval),
                        recovery_confirmed_at=float(first + crossing//4 + 1))
        break
    return None


def _rhythm_reference(groups):
    if len(groups) < 8 or any(len(group) < 8 for group in groups):
        return None, None, 0, None
    shape = _flow_shape(groups).ravel()
    low, high = np.quantile(shape, [.1, .9])
    span = high - low
    if span <= 1e-9:
        return None, None, 0, None
    lower = low + .3 * span
    upper = low + .7 * span
    crossings = []
    armed = False
    for index, value in enumerate(shape):
        if value <= lower:
            armed = True
        elif armed and value >= upper:
            crossings.append(index)
            armed = False
    for i in range(len(crossings)-1, 2, -1):
        earlier = None
        for count in range(min(5, i), 2, -1):
            periods = np.diff(crossings[i-count:i+1]) / 4
            period = float(np.median(periods))
            age = (len(shape) - crossings[i]) / 4
            if not 2 <= period <= 16 or np.max(np.abs(periods-period)) > .25 * period:
                continue
            if count >= 4 and i == len(crossings)-1 and age <= 1.5 * period:
                lows = []
                for left, right in zip(crossings[i-count:i], crossings[i-count+1:i+1]):
                    below = np.flatnonzero(shape[left:right] <= lower)
                    if len(below):
                        extent = below[-1] - below[0] + 1
                        if len(below) >= .75 * extent:
                            lows.append(extent / 4)
                phase = None
                if len(lows) >= 4:
                    typical = float(np.median(lows))
                    consistent = sum(abs(length-typical) <= .4 * typical for length in lows)
                    if consistent >= 4:
                        phase = dict(low_phase_seconds=typical, low_phase_count=int(consistent),
                                     low_flow_threshold=float(lower), high_flow_threshold=float(upper))
                return period, 'recent', count, phase
            if age <= 2 * period and earlier is None:
                earlier = period, 'earlier', count, None
        if earlier is not None:
            return earlier
    return None, None, 0, None


def _normal_low_phase_end(source, raw_groups, start, end, phase):
    """Require an observed falling edge and sustained low flow before excluding a normal phase."""
    first = max(0, int(start)-2)
    stop = min(int(end), int(start) + math.ceil(phase['low_phase_seconds']) + 3)
    if stop <= start:
        return start, None
    segment = source['segment'][int(start)]
    groups = raw_groups[first:stop]
    if (not np.all(source['valid'][first:stop])
            or not np.all(source['segment'][first:stop] == segment)
            or any(len(group) < 8 for group in groups)):
        return start, None
    shape = _flow_shape(groups).ravel()
    low = phase['low_flow_threshold']
    high = phase['high_flow_threshold']
    # Find a high-to-low transition at or just before the estimated onset.
    onset = 4 * (int(start)-first)
    high_points = np.flatnonzero(shape[:onset+4] >= high)
    if not len(high_points):
        return start, None
    low_points = np.flatnonzero(shape[high_points[-1]+1:onset+4] <= low)
    if not len(low_points):
        return start, None
    falling = int(high_points[-1]+1+low_points[0])
    projected = falling + 4 * phase['low_phase_seconds']
    # Row groups estimate the phase; the reported boundary remains a whole source second.
    boundary = int(math.floor(projected / 4 + .5))
    if (first+boundary <= start or 4*(boundary+2) > len(shape)
            or np.any(shape[falling:4*(boundary+2)] > low)):
        return start, None
    return float(first+boundary), float(first+falling//4)


def _flow_tail_end(source, raw_groups, start, end, limit):
    """Estimate a settling tail from ordered rows, reporting only whole seconds."""
    first, stop = int(start), int(end)
    if first < 6:
        return start, False
    segment = source['segment'][first]
    history_start = max(0, first - 12)
    while history_start < first and (
        not source['valid'][history_start] or source['segment'][history_start] != segment
    ):
        history_start += 1
    if first - history_start < 6:
        return start, False

    probe_end = min(stop, first + limit + 2)
    groups = raw_groups[history_start:probe_end]
    if any(len(group) < 8 for group in groups):
        return start, False
    shape = _flow_shape(groups)
    history_size = first - history_start
    low, high = np.quantile(shape[:history_size], [.1, .9])
    reference_range = high - low
    if reference_range <= 1e-9:
        return start, False

    edge = shape[history_size:].ravel()
    for shift in range(min(limit, probe_end - first - 2) + 1):
        plateau = edge[shift * 4:shift * 4 + 8]
        if len(plateau) < 8 or np.ptp(plateau) > .05 * reference_range:
            continue
        if shift == 0:
            return start, True
        level = np.median(plateau)
        tail = np.r_[shape[history_size - 1, -1], edge[:shift * 4], plateau[0]] - level
        distance = tail * np.sign(tail[1])
        tolerance = .02 * reference_range
        # A single undershoot can settle within the first source second.
        first_second = tail[1:5] * np.sign(tail[0])
        if (shift == 1 and abs(tail[0]) >= .2 * reference_range
                and np.min(first_second) < -tolerance
                and abs(first_second[-1]) <= .06 * reference_range
                and np.min(first_second) > -.2 * reference_range
                and np.argmin(first_second) in (1, 2)):
            return float(first + shift), True
        if (distance[0] >= tolerance and distance[1] >= .04 * reference_range
                and np.all(distance >= -tolerance)
                and np.all(np.diff(distance) <= tolerance)
                and np.count_nonzero(np.diff(distance) < -tolerance / 4) >= 3):
            return float(first + shift), True
        break
    return start, False


def _weak_onset(source, raw_groups, parent_start, start, lookback, settle_seconds):
    """Find a sustained contraction of raw flow range after a visible excursion."""
    first = max(1, int(start) - lookback)
    stop = int(start) + settle_seconds
    history_start = max(0, first - 20)
    segment = source['segment'][int(parent_start)]
    while history_start < first and (
        source['segment'][history_start] != segment or not source['valid'][history_start]
    ):
        history_start += 1
    if first - history_start < 6 or stop > len(raw_groups):
        return start
    groups = raw_groups[history_start:stop]
    if (not np.all(source['valid'][history_start:stop])
            or not np.all(source['segment'][history_start:stop] == segment)
            or any(len(group) < 8 for group in groups)):
        return start
    shape = _flow_shape(groups)
    history_size = first - history_start
    span = float(np.diff(np.quantile(shape[:history_size], [.1, .9]))[0])
    if span <= 1e-9:
        return start
    for i in range(first, int(start)):
        j = i - history_start
        quiet = shape[j:j+settle_seconds]
        level = np.median(quiet)
        if (np.ptp(quiet) <= .45 * span
                and np.max(np.abs(shape[j-1] - level)) >= .5 * span
                and np.max(np.abs(shape[j] - level)) <= .25 * span):
            return float(i)
    return start


def _weak_recovery_start(source, raw_groups, rows, parent_start, start, end, window):
    """Find a recovered excursion after a quiet stretch, not an isolated level step."""
    first = int(parent_start)
    history = [i for i in range(max(0, first-20), first)
               if source['segment'][i] == source['segment'][first] and len(raw_groups[i]) >= 8]
    if len(history) < 6:
        return end
    low, high = np.quantile(_flow_shape([raw_groups[i] for i in history]), [.1, .9])
    span = high - low
    if span <= 1e-9:
        return end
    for row in rows:
        i = row['source_index']
        if not max(start+3, end-window) <= i < end or row['fast_ratio'] < .6:
            continue
        if i+3 > len(raw_groups) or any(
            source['segment'][j] != source['segment'][i] or len(raw_groups[j]) < 8
            for j in range(i-3, i+3)
        ):
            continue
        shape = _flow_shape(raw_groups[i-3:i+3])
        quiet, recovery = shape[:3], shape[3:].ravel()
        if np.ptp(quiet) > .45 * span:
            continue
        offset = recovery - np.median(quiet)
        peak = int(np.argmax(np.abs(offset)))
        direction = np.sign(offset[peak])
        if (peak < 8 and abs(offset[peak]) >= .45 * span
                and np.max(np.abs(offset[:4])) >= .15 * span
                and np.any((offset[peak]-offset[peak+1:])*direction >= .30 * span)):
            return float(i)
    return end


def _leak_steps(source):
    """Only compare adjacent valid source seconds within the same segment."""
    if '漏气量' not in source['med']:return []
    values=source['med']['漏气量'].to_numpy(dtype=float)
    valid=source['valid'];segment=source['segment']
    answer=[]
    for i in np.flatnonzero(np.isfinite(values[1:]-values[:-1]) & (np.abs(np.diff(values))>=10))+1:
        if valid[i] and valid[i-1] and segment[i]==segment[i-1]:
            answer.append(dict(time=float(source['clock'][i]-1),before=float(values[i-1]),after=float(values[i]),delta=float(values[i]-values[i-1])))
    return answer


def compute(recording,method=None,window=None,min_duration=10):
    config=settings(min_duration,dynamic_method=method,window_seconds=window);method=config['method'];window=config['window_seconds'];duration=config['min_duration']
    cache=getattr(recording,'_dynamic_v3_cache',None)
    if cache is None:cache={};recording._dynamic_v3_cache=cache
    if (method,window,duration) in cache:return cache[(method,window,duration)]
    source,raw_groups=_source(recording);size=len(source['index'])
    leak_steps=_leak_steps(source)
    traces={n:np.full(size,np.nan) for n in ('strength','reference','remaining','background','fast','fast_reference')}
    history=deque();background_history=deque();short=deque(maxlen=window)
    episode=None;events=[];boundary_rejections=[];previous_segment=None;restored=0;last_tick=0.
    last_background=None;last_background_at=None
    anchor=next((float(v[0]) for v in raw_groups if len(v)),0.)

    def accept(row):
        history.append((row['tick'],row['strength'],row['fast']))
        background_history.append((row['tick'],row['source_index']))

    def close(reason):
        nonlocal episode,restored,last_background,last_background_at
        if episode is None:return
        parent=episode;episode=None;rows=parent['rows']
        flags,changes=_review_flags(source,parent['start'],rows[-1]['tick'])
        leak_context=[s for s in leak_steps if max(0,parent['start']-30)<=s['time']<rows[-1]['tick']]
        if leak_context or abs(changes.get('漏气量',0))>=10:
            flags.append('总漏气变化需复核；保留强度参考，未减总漏气')
        if parent['background_held']:flags.append('背景沿用最近有效估计，待有效背景历史补足；需复核')
        if rows[-1]['tick']-parent['start']>=120:flags.append('长段减弱/基线变化待查（≥120秒）；未自动重建正常基线')
        if reason!='恢复':flags.append(reason)
        cores=[v for v in _intervals(rows,lambda r:r['fast_ratio']<.25) if v[1]-v[0]>=duration]
        weak=[[a,min(b,last_tick,recording.meta['raw_duration'])] for a,b in _intervals(rows,lambda r:r['ratio']<.5,clock_end=True)]
        spans=cores or [v for v in weak if v[1]-v[0]>=duration]
        period = rhythm_basis = phase = None
        rhythm_count = 0
        tail_limit = 3
        if spans:
            period, rhythm_basis, rhythm_count, phase = _rhythm_reference(raw_groups[parent['rhythm_start']:int(parent['start'])])
            if period is not None:
                tail_limit = min(6, max(2, math.ceil(period / 2)))
        weak_lookback = min(window, math.ceil(period)) if period is not None else window
        weak_settle = min(6, max(3, math.ceil(period))) if period is not None else 3
        emitted=False
        for span_index,(a,b) in enumerate(spans):
            if parent['pretrigger_at'] is None:continue
            unadjusted_start = a
            unadjusted_end = b
            recovery = recovery_cycle = None
            onset_search = weak_lookback
            boundary_period, boundary_basis, boundary_count, boundary_phase = period, rhythm_basis, rhythm_count, phase
            if cores:
                a, onset_supported = _flow_tail_end(source, raw_groups, a, b, tail_limit)
                if reason == '恢复':
                    recovery = _early_recovery_evidence(source, raw_groups, b, parent['fast_reference'])
                    if recovery is not None and rhythm_basis == 'recent':
                        recovery_cycle = _confirm_recovery_cycle(
                            source, raw_groups, b, period, recovery, parent['fast_reference'])
                        if recovery_cycle is not None:
                            b = recovery['alternative_end']
            else:
                if span_index == 0 and not parent['reference_censored']:
                    available = min(window, int(a-events[-1]['end'])) if events else window
                    if available > 0:
                        onset_search = min(weak_lookback, available)
                        a = _weak_onset(source, raw_groups, parent['start'], a, onset_search, weak_settle)
                        if a == unadjusted_start and onset_search < available:
                            onset_search = available
                            a = _weak_onset(source, raw_groups, parent['start'], a, onset_search, weak_settle)
                if reason == '恢复':
                    b = _weak_recovery_start(source, raw_groups, rows, parent['start'], unadjusted_start, b, window)
                if a < parent['start']:
                    # The window can trigger after the quiet stretch has already started.
                    # Judge cadence freshness at the observed onset, before that stretch.
                    boundary_period, boundary_basis, boundary_count, boundary_phase = _rhythm_reference(
                        raw_groups[parent['rhythm_start']:int(a)])
            waveform_start = a
            phase_anchor = None
            if boundary_phase is not None and (cores or span_index == 0) and not parent['reference_censored']:
                a, phase_anchor = _normal_low_phase_end(source, raw_groups, a, b, boundary_phase)
            if b - a < duration:
                boundary_rejections.append(dict(original=[unadjusted_start,unadjusted_end], corrected=[a,b]))
                continue
            # The selected main window must provide corroborating evidence.
            support=[r for r in rows if a<=r['tick']-1<b+window and r['ratio']<.5]
            if not support:continue
            selected=[r for r in rows if (a<=r['tick']-1<b if cores else max(a,unadjusted_start)<=r['tick']<b)]
            if not selected:continue
            threshold=.25 if cores else .5
            qualified=_intervals(rows,lambda r:r['ratio']<threshold,clock_end=True)
            qualified=[[x,min(y,last_tick,recording.meta['raw_duration'])] for x,y in qualified if min(y,last_tick,recording.meta['raw_duration'])>x]
            extreme=[v for v in _intervals(selected,lambda r:r['fast_ratio']<=.1) if v[1]-v[0]>=duration]
            confirmed=max(a+duration,support[0]['tick'],unadjusted_start if not cores else a)
            if confirmed>last_tick:continue
            evidence=dict(schema=1,algorithm_version=VERSION,method=method,window_seconds=window,
                estimated_waveform_interval=[a,b],boundary_duration_seconds=b-a,boundary_resolution_seconds=1,
                boundary_rule=('连续1秒复核分箱；检测到流量尾段时从趋稳后计时' if cores else f'主窗口圈定减弱；按原始流量复核起止；修正后仍需≥{duration}秒'),
                boundary_basis='one_second_review' if cores else 'main_window_end',
                main_qualified_intervals=qualified[:30],main_interval_count=len(qualified),main_intervals_truncated=len(qualified)>30,
                main_qualified_total_seconds=sum(y-x for x,y in qualified),main_interval_scope='父过程；多个核心可能共享，不能求和去重',
                reference_amplitude=parent['reference'],fast_reference_amplitude=parent['fast_reference'],
                strength_median=float(np.median([r['strength'] for r in selected])),
                remaining_ratio_median=float(np.median([r['ratio'] for r in selected])),
                boundary_remaining_max=max(r['fast_ratio'] if cores else r['ratio'] for r in selected),boundary_threshold=threshold,
                significant_threshold=parent['reference']*.25,reduced_threshold=parent['reference']*.5,
                minimum_duration=duration,pretrigger_seconds=3,pretrigger_confirmed_at=parent['pretrigger_at'],confirmed_at=confirmed,
                parent_start=parent['start'],parent_end=rows[-1]['tick'],extremely_low_strength=bool(extreme),
                extremely_low_intervals=extreme[:30],review_flags=flags,channel_median_changes=changes,
                boundary_censored=reason!='恢复',signal_basis='机器总流量；背景估计不是漏气量，未验证Patient Flow',
                leak_policy='review_only_no_reference_reset',leak_change_context=leak_context[:20],
                leak_change_count=len(leak_context),leak_context_truncated=len(leak_context)>20)
            evidence.update(background_from_held_estimate=parent['background_held'],background_age_at_trigger_seconds=parent['background_age'])
            if waveform_start > unadjusted_start:
                evidence.update(unadjusted_start=unadjusted_start, flow_tail_trim_seconds=waveform_start-unadjusted_start)
            if phase_anchor is not None:
                evidence['normal_low_phase_review'] = dict(
                    **boundary_phase, falling_edge_second=phase_anchor, waveform_start=waveform_start,
                    delayed_seconds=a-waveform_start)
            if not cores:
                evidence.update(rhythm_period_seconds=boundary_period, rhythm_basis=boundary_basis, rhythm_interval_count=boundary_count,
                                weak_onset_search_seconds=onset_search)
                if waveform_start < unadjusted_start:
                    evidence.update(unadjusted_start=unadjusted_start, onset_backtrack_seconds=unadjusted_start-waveform_start)
            if b < unadjusted_end:
                evidence.update(unadjusted_end=unadjusted_end, recovery_trim_seconds=unadjusted_end-b)
            if recovery_cycle is not None:
                evidence.update(recovery_cycle, recovery_flow_evidence=recovery)
            if parent['reference_censored']:
                evidence['review_flags'] = [*flags, '起点参考刚建立，波形下降可能早于标注']
            if cores:
                evidence.update(rhythm_period_seconds=period, rhythm_basis=rhythm_basis, rhythm_interval_count=rhythm_count,
                                flow_tail_search_seconds=tail_limit)
                if not onset_supported and b-a < duration+tail_limit:
                    evidence['review_flags'] = [*evidence['review_flags'], '起点待复核：流量趋稳证据不足，起点偏移可能改变是否达标']
                if b-a == duration and recovery is not None and recovery_cycle is None:
                    evidence['threshold_boundary_review'] = dict(
                        **recovery, alternative_duration_seconds=b-a-1)
                    evidence['review_flags'] = [*evidence['review_flags'],
                        f'临界待复核：末秒流量已开始持续偏离平台，按较早终点为{duration-1}秒']
            events.append(dict(start=a,end=b,type='强度显著下降候选' if cores else '强度减弱候选',evidence=evidence))
            emitted=True
        # Restore valid rejected dips after recovery. Pressure pulsatility is
        # a review hint, not invalid data; persistent total-leak shifts block
        # rollback. Never bridge quality resets or rewrite past decisions.
        persistent_leak_shift=abs(changes.get('漏气量',0))>=10
        # A leak estimate change alone is not invalid source data. Keep the
        # strength reference and background; retain confound evidence. Do not
        # backfill a rejected leak-shift dip as known normal breathing.
        if not emitted and reason=='恢复' and not persistent_leak_shift and rows[-1]['tick']-parent['start']<120:
            for row in rows:accept(row)
            restored+=len(rows)

    for i,tick in enumerate(source['clock']):
        tick=float(tick);segment=source['segment'][i]
        if segment!=previous_segment or not source['valid'][i]:
            close('缺口/无效数据/状态变化截断');history.clear();background_history.clear();short.clear();previous_segment=segment
            last_background=None;last_background_at=None
        last_tick=tick
        if not source['valid'][i]:continue
        while history and history[0][0]<tick-100:history.popleft()
        while background_history and background_history[0][0]<tick-30:background_history.popleft()
        raw=raw_groups[i]
        background=None
        held_background=False
        if episode:background=episode['background']
        elif len(background_history)>=30:
            pooled=np.concatenate([raw_groups[j] for _,j in background_history])
            weights=np.concatenate([source['sample_weights'][j] for _,j in background_history])
            values,inverse=np.unique(pooled,return_inverse=True)
            mass=np.bincount(inverse,weights=weights);cumulative=np.cumsum(mass);target=mass.sum()*.5
            middle=min(int(np.searchsorted(cumulative,target)),len(values)-1)
            # Aggregate duplicate values before finding the central gap;
            # row density or tie ordering must not move a balanced baseline.
            if middle+1<len(values) and abs(cumulative[middle]-target)<=max(1e-10,mass.sum()*1e-10):
                background=float((values[middle]+values[middle+1])*.5)
            else:background=float(values[middle])
            last_background=background;last_background_at=tick
        elif last_background is not None:
            background=last_background;held_background=True
        if method=='detrended_abs' and background is None:
            short.clear();history.clear()
            background_history.append((tick,i));continue
        if method=='detrended_abs':fast=float(np.mean(np.abs(raw-background))*.5)
        elif method=='absolute_mean':fast=float(np.mean(np.abs(raw))*.5)
        else:fast=float(np.std(raw))
        delta=raw-anchor
        short.append((fast,float(np.mean(delta)),float(np.mean(delta*delta))))
        if len(short)<window:
            background_history.append((tick,i));continue
        strength=float(np.sqrt(max(0,np.mean([r[2] for r in short])-np.mean([r[1] for r in short])**2))) if method=='centered_rms' else float(np.mean([r[0] for r in short]))
        traces['strength'][i]=strength;traces['fast'][i]=fast
        if method=='detrended_abs':traces['background'][i]=background
        ref=episode['reference'] if episode else float(np.mean([v for _,v,_ in history])) if len(history)>=30 else None
        fast_ref=episode['fast_reference'] if episode else float(np.mean([v for _,_,v in history])) if len(history)>=30 else None
        row=dict(tick=tick,source_index=i,strength=strength,fast=fast)
        if ref is None or fast_ref is None or min(ref,fast_ref)<=1e-9:
            accept(row);continue
        row.update(ratio=strength/ref,fast_ratio=fast/fast_ref)
        traces['reference'][i]=ref;traces['fast_reference'][i]=fast_ref;traces['remaining'][i]=row['ratio']
        if episode is None:
            if row['ratio']>=.5 and row['fast_ratio']>=.5:accept(row);continue
            rhythm_start = int(tick) - 1
            for previous_tick, _, _ in reversed(history):
                if previous_tick != rhythm_start or int(tick) - 1 - rhythm_start >= 75:
                    break
                rhythm_start -= 1
            episode=dict(start=tick-1,reference=ref,fast_reference=fast_ref,background=background,rows=[],pretrigger_count=0,pretrigger_at=None,
                         rhythm_start=rhythm_start,
                         reference_censored=i == 0 or not np.isfinite(traces['remaining'][i-1]),
                         background_held=held_background,background_age=None if last_background_at is None else tick-last_background_at)
        elif row['ratio']>=.6 and row['fast_ratio']>=.6:
            close('恢复');accept(row);continue
        episode['rows'].append(row)
        episode['pretrigger_count']=episode['pretrigger_count']+1 if row['ratio']<.5 or row['fast_ratio']<.5 else 0
        if episode['pretrigger_count']>=3 and episode['pretrigger_at'] is None:episode['pretrigger_at']=tick
    close('记录末端截断')
    result=dict(source=source,traces=traces,events=events,boundary_rejections=boundary_rejections,restored_seconds=restored,settings=config,leak_steps=leak_steps)
    cache[(method,window,duration)]=result
    return result


def detect(recording,start=0,end=None,min_duration=10,event_types=None,dynamic_method=None,window_seconds=None):
    config=settings(min_duration,event_types,dynamic_method,window_seconds)
    end=recording.meta['raw_duration'] if end is None else end
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (start,end)) or not 0<=start<end<=recording.meta['raw_duration']:raise ValueError('优化测试分析范围无效')
    result=compute(recording,config['method'],config['window_seconds'],config['min_duration']);events=[]
    origin=datetime.fromisoformat(recording.meta['start']);wall=lambda t:str(origin+timedelta(seconds=float(t)))
    identity={k:v for k,v in config.items() if k!='event_types'}
    def add(a,b,kind,note,evidence=None):
        if kind not in config['event_types'] or b<=start or a>=end or b<=a:return
        e=dict(id='det-'+hashlib.sha256(json.dumps([recording.meta['file_id'],a,b,kind,identity],sort_keys=True).encode()).hexdigest()[:32],
               start=float(a),end=float(b),type=kind,channels=['流量'],mode='raw',source='detector',status='candidate',
               detection_profile=PROFILE,detection_input='csv_flow',detection_version=VERSION,
               detection_method=config['method'],detection_window_seconds=config['window_seconds'],detection_min_duration=config['min_duration'],note=note,
               updated_at=datetime.now(timezone.utc).isoformat())
        if evidence:
            e['evidence']=evidence
            e['channels'] += [n for n in (names(config['window_seconds'])[0],names(config['window_seconds'])[6]) if n in recording.channels]
        events.append(e)
    for original in result['events']:
        a,b=original['start'],original['end'];evidence=json.loads(json.dumps(original['evidence'],allow_nan=False))
        if a<start or b>end:evidence['review_flags'].append('跨选区，保留完整估计区间')
        note=(f'动态基线优化测试 {VERSION}；{METHODS[config["method"]]} / {config["window_seconds"]}秒；'
              f'{"估计波形区间" if evidence["boundary_basis"]=="one_second_review" or evidence.get("recovery_trim_seconds") or evidence.get("onset_backtrack_seconds") or evidence.get("normal_low_phase_review") else "主窗口连续达标区间"} {wall(a)}—{wall(b)}（{b-a:g}秒）；'
              f'父过程主窗口达标共{evidence["main_qualified_total_seconds"]:g}秒，与估计时长分开；'
              f'确认时刻{wall(evidence["confirmed_at"])}；背景/参考在预候选期冻结，无事件且质量有效时回填；'
              +(f'存在连续{config["min_duration"]}秒≤10%极低强度标记；' if evidence['extremely_low_strength'] else '')
              +'复核提示：'+('；'.join(evidence['review_flags']) or '需人工核对波形')+'。未做固定窗口前移，未验证患者流量/临床分类。')
        add(a,b,original['type'],note,evidence)
    source=result['source']
    for a,b in recording.gaps:add(float(a),float(b),'数据缺失','原始时间戳间隔；已重置窗口、背景和参考，不跨缺口复核。')
    edges=np.diff(np.r_[False,~source['valid'],False].astype(int))
    for a,b in zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1)):
        if b-a>=config['min_duration']:add(float(a),float(b),'信号异常',f'无效源秒持续至少{config["min_duration"]}秒；不用于背景/参考回填。')
    scope=(source['clock']>=start)&(source['clock']<end)
    return dict(file_id=recording.meta['file_id'],settings=config,scan_start=start,scan_end=end,events=sorted(events,key=lambda e:(e['start'],e['type'])),
                analyzed_rows=len(recording.x),eligible_seconds=int(np.sum(np.isfinite(result['traces']['remaining'])&scope)),
                diagnostics=dict(restored_source_seconds=result['restored_seconds'],leak_change_count=sum(start<=s['time']<end for s in result['leak_steps'])),
                warnings=[f'优化v3：检测窗口{config["window_seconds"]}秒，最短持续{config["min_duration"]}秒。显著下降由1秒复核<25%连续达标并有主窗口佐证；减弱由所选主窗口<50%连续达标，不再要求每个1秒分箱达标。',
                          f'本次范围总漏气字段相邻源秒变化≥10 L/min共{sum(start<=s["time"]<end for s in result["leak_steps"])}处；候选保留相关时间与变化量供复核。',
                          '总漏气变化仅提示复核，不因此清空强度参考；数据缺失、无效及状态变化仍重置。',
                          '显著下降起止是源秒级估计；减弱起止按主窗口连续达标末端计时。确认时刻另存，不用固定W补偿，不保证真实生理边界。',
                          '去背景不是漏气补偿或患者流量重建；无临床分类、无设备控制；新旧版本/不同窗口的重叠候选不能累计。'])


def waveform_channels(recording,requested,method='detrended_abs',min_duration=10):
    answer={};positions=np.searchsorted(np.arange(int(recording.meta['raw_duration']))+1,recording.x,side='right')-1
    for window in WINDOWS:
        keys=names(window)
        if not any(n in requested for n in keys):continue
        t=compute(recording,method,window,min_duration)['traces'];base=t['reference']
        series=(t['strength'],base,base*.25,base*.5,t['remaining']*100,t['background'],t['fast'],t['fast_reference'])
        for name,values in zip(keys,series):
            if name not in requested:continue
            out=np.full(len(recording.x),np.nan);valid=(positions>=0)&(positions<len(values));out[valid]=values[positions[valid]];answer[name]=out
    return answer
