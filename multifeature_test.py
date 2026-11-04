"""Independent, causal machine-flow experiment; never a clinical/device controller.

The manufacturers' public mechanisms motivate the checks, not these parameters.
Source-second bins preserve the recording clock; no assumed 50 Hz resampling.
"""
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math

import numpy as np
import pandas as pd

VERSION = 'adaptive-flow-test-v3-multifeature'
PROFILE = 'adaptive_multifeature'
TYPES = ('测试近消失候选', '测试减弱候选', '数据缺失', '信号异常')
A = '多特征幅度A（5秒鲁棒）'
B = '多特征参考B'
NEAR = '多特征近消失门限'
WEAK = '多特征减弱门限'
RATIO = '多特征剩余比例'
DROP = '多特征下降百分比'
NOISE = '多特征噪声代理（未标定）'
CHANNELS = (A, B, NEAR, WEAK, RATIO, DROP, NOISE)


def settings(min_duration=7, reduction_ratio=.5, event_types=None):
    values = (min_duration, reduction_ratio)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError('多特征参数必须为有限数值')
    if not 3 <= min_duration <= 120 or not .1 < reduction_ratio <= .8:
        raise ValueError('最短持续3～120秒，减弱剩余比例须大于10%且不超过80%')
    if event_types is not None and (not isinstance(event_types, list) or not event_types or any(t not in TYPES for t in event_types)):
        raise ValueError('请选择有效的多特征测试类型')
    return dict(version=VERSION, profile=PROFILE, flow_source='csv_flow',
                event_types=[t for t in TYPES if event_types is None or t in event_types],
                min_duration=min_duration, reduction_ratio=reduction_ratio,
                near_ratio=.1, window_seconds=5, step_seconds=1, quantiles=[.02, .98],
                baseline_seconds=120, baseline_min_seconds=30, baseline_quantile=.75,
                noise_policy='unvalidated adjacent-row second-difference proxy; quality flag only, never raises ratio thresholds',
                noise_proxy_multiplier=2., noise_contrast=3., minimum_finite_fraction=.8,
                reduced_exit_ratio=min(.95, max(reduction_ratio+.05, min(.75, reduction_ratio*1.2))),
                baseline_policy='prior valid non-episode seconds; frozen in episode; reset on gaps/mode/state, pressure is a review flag',
                timing='strict consecutive qualifying window-end intervals; confirm at start+duration; no onset backdating',
                scope_policy='full-record context; complete events intersecting requested scope',
                morphology_policy='source-second median turning points and flat/spike/jump evidence only; no inspiratory diagnosis',
                flow_unit='L/min', clinical_scoring=False, device_control=False)


def features(recording):
    cached = getattr(recording, '_multifeature_features', None)
    if cached is not None:
        return cached
    if '流量' not in recording.channels:
        raise ValueError('多特征测试需要CSV流量列')
    idx = np.arange(int(np.floor(recording.meta['raw_duration'])))
    values = np.asarray(recording.channels['流量'], float)
    frame = pd.DataFrame({'sec': np.floor(recording.x).astype(int),
                          'flow': np.where(np.isfinite(values), values, np.nan)})
    names = [n for n in ('模式', '运行状态', '压力', '治疗压', '控制压力', '漏气量') if n in recording.channels]
    for name in names:
        frame[name] = recording.channels[name]
    g = frame.groupby('sec', sort=True)
    count = g['flow'].count().reindex(idx, fill_value=0)
    total = g.size().reindex(idx, fill_value=0)
    low = g['flow'].quantile(.02).reindex(idx)
    high = g['flow'].quantile(.98).reindex(idx)
    med = g[['flow', *names]].median().reindex(idx)
    valid = count.ge(5) & count.ge(total*.8) & low.notna() & high.notna()
    change = pd.Series(False, index=idx)
    for name in ('模式', '运行状态'):
        if name in med:
            valid &= g[name].count().reindex(idx, fill_value=0).ge(total*.8)
            valid &= g[name].nunique().reindex(idx, fill_value=0).eq(1)
            change |= med[name].diff().fillna(0).ne(0)
    segment = (~valid | ~valid.shift(1, fill_value=False) | change).cumsum()
    amp = high.groupby(segment).transform(lambda s: s.rolling(5, min_periods=5).max()) - low.groupby(segment).transform(lambda s: s.rolling(5, min_periods=5).min())
    amp = amp.where(valid)
    # Adjacent rows are used only as a descriptive roughness proxy, never as Hz.
    # Do not bridge source-second or nonfinite boundaries.
    second_diff = frame['flow'].groupby(frame['sec']).diff().groupby(frame['sec']).diff().abs()
    noise = (second_diff.groupby(frame['sec']).median()*2).reindex(idx).where(count.ge(10))
    spread = (g['flow'].max()-g['flow'].min()).reindex(idx)
    result = dict(index=idx, clock=idx+1, a=amp.to_numpy(), noise=noise.to_numpy(),
                  valid=valid.to_numpy(), segment=segment.to_numpy(), med=med,
                  count=count.to_numpy(), spread=spread.to_numpy())
    recording._multifeature_features = result
    return result


def _runs(mask):
    edges = np.diff(np.r_[False, np.asarray(mask, dtype=bool), False].astype(int))
    return zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))


def _summary_evidence(f, start, end, baseline):
    # Supporting description may inspect the complete event; it never changes
    # prior detection thresholds or pretends these coarse turns are true breaths.
    idx = f['index']
    mask = (idx+1 >= start) & (idx+1 < end)
    vals = f['med']['flow'].to_numpy()[mask]
    finite = vals[np.isfinite(vals)]
    turns = []
    if len(finite) == len(vals) and len(vals) >= 3:
        for i in range(1, len(vals)-1):
            if vals[i] > vals[i-1] and vals[i] >= vals[i+1] and vals[i]-min(vals[i-1], vals[i+1]) > baseline*.02:
                turns.append(i)
    periods = np.diff(turns)
    regular = bool(len(periods) >= 2 and np.mean(periods) >= 2 and np.std(periods)/np.mean(periods) <= .35)
    spreads = f['spread'][mask]
    flat = sum(b-a for a, b in _runs(np.isfinite(spreads) & (spreads <= max(1e-9, baseline*1e-6))))
    pressure = {}
    flags = []
    before = f['med'].loc[max(0, int(start)-31):int(start)-2]
    during = f['med'].loc[max(0, int(start)-1):max(0, int(math.ceil(end))-2)]
    for name in ('压力', '治疗压', '控制压力', '漏气量'):
        if name not in f['med']:
            continue
        pre, cur = before[name].dropna(), during[name].dropna()
        if not len(pre) or not len(cur):
            continue
        delta = float(cur.median()-pre.median())
        threshold = 10 if name == '漏气量' else 20
        pressure[name] = delta
        combined = f['med'].loc[max(0, int(start)-2):max(0, int(math.ceil(end))-2), name]
        jump = float(combined.diff().abs().max()) if len(combined) > 1 else 0
        if abs(delta) >= threshold or jump >= threshold:
            flags.append(f'{name}变化需复核（中位差{delta:.1f}，相邻源秒最大变化{jump:.1f}）')
    if flat >= 7:
        flags.append('持续平直：仅靠流量无法区分真实低波动与信号冻结')
    amps = f['a'][mask]
    if np.any(spreads > np.maximum(amps*3, baseline*.2)):
        flags.append('存在孤立尖峰/异常极差，已使用鲁棒幅度')
    if len(finite) > 1 and np.max(np.abs(np.diff(finite))) > baseline*.5:
        flags.append('源秒流量中位值突跳需复核')
    return dict(coarse_turn_count=len(turns), coarse_periodic_hint=regular,
                flat_source_seconds=int(flat), channel_median_changes=pressure,
                review_flags=flags,
                morphology_basis='源秒中位序列；不是已验证呼吸次数、吸气形态或呼吸努力')


def _compute(recording, config):
    # Small bounded cache; selected types and scan range never affect identity.
    key = (float(config['min_duration']), float(config['reduction_ratio']))
    cache = getattr(recording, '_multifeature_cache', None)
    if cache is None:
        cache = recording._multifeature_cache = OrderedDict()
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    f = features(recording)
    n = len(f['clock'])
    trace = {name: np.full(n, np.nan) for name in CHANNELS}
    trace[A][:] = f['a']
    trace[NOISE][:] = f['noise']
    quality = np.zeros(n, dtype=int)
    history = deque()
    episode = None
    previous_segment = None
    last_tick = 0.
    events = []
    insufficient = np.zeros(n, dtype=bool)
    identity_settings = {k: v for k, v in config.items() if k != 'event_types'}

    def wall(t):
        return str(datetime.fromisoformat(recording.meta['start'])+timedelta(seconds=float(t)))

    def add(a, b, kind, note, evidence=None):
        if b <= a:
            return
        token = json.dumps([recording.meta['file_id'], a, b, kind, identity_settings], sort_keys=True)
        item = dict(id='det-'+hashlib.sha256(token.encode()).hexdigest()[:32], start=float(a), end=float(b),
                    mode='raw', type=kind, channels=['流量', A] if A in recording.channels else ['流量'],
                    source='detector', status='candidate', detection_profile=PROFILE, detection_input='csv_flow',
                    detection_version=VERSION, updated_at=datetime.now(timezone.utc).isoformat(), note=note)
        if evidence is not None:
            item['evidence'] = evidence
        events.append(item)

    def close(t, reason='恢复'):
        nonlocal episode
        if episode is None:
            return
        e, episode = episode, None
        for name in ('near', 'weak'):
            if e[name+'_start'] is not None:
                e[name+'_runs'].append((e[name+'_start'], t))
        near_runs = [(a, b) for a, b in e['near_runs'] if b-a >= config['min_duration']]
        weak_runs = [(a, b) for a, b in e['weak_runs'] if b-a >= config['min_duration']]
        # A near-absence label only covers its strict <=10% core. The enclosing
        # weak process remains evidence, not an overlapping second event count.
        for a, b in near_runs or weak_runs:
            near = bool(near_runs)
            samples = [(tick, val, ok) for tick, val, ok in e['values'] if a <= tick < b]
            ratios = [val/e['base'] for _, val, _ in samples]
            evidence = _summary_evidence(f, a, b, e['base'])
            if not all(ok for _, _, ok in samples):
                evidence['review_flags'].append('噪声/分辨率不足，不能可靠分型为近消失')
            if e['noise'] == 0:
                evidence['review_flags'].append('噪声代理为0可能来自量化/重复值，不代表真实噪声为0')
            if reason != '恢复':
                evidence['review_flags'].append(reason)
            evidence.update(schema=1, algorithm_version=VERSION, reference_amplitude=e['base'],
                            amplitude_median=float(np.median([v for _, v, _ in samples])),
                            remaining_ratio_median=float(np.median(ratios)), remaining_ratio_max=float(max(ratios)),
                            near_threshold=e['base']*.1, reduced_threshold=e['base']*config['reduction_ratio'],
                            remaining_entry_ratio=config['reduction_ratio'], minimum_duration=config['min_duration'],
                            strict_duration_seconds=float(b-a), confirmed_at=float(a+config['min_duration']),
                            episode_start=e['start'], episode_end=float(t),
                            reference_noise_proxy=e['noise'], signal_basis='机器总流量波动；非患者流量',
                            boundary_censored=reason != '恢复')
            flag_text='；'.join(evidence['review_flags']) or '未触发上述提示，仍需人工复核'
            note=(f'多特征自适应测试 {VERSION}；CSV机器总流量；5秒因果鲁棒幅度；'
                  f'冻结参考B={e["base"]:.2f} L/min；A中位={evidence["amplitude_median"]:.2f} L/min；'
                  f'剩余中位={evidence["remaining_ratio_median"]*100:.1f}%/最大={max(ratios)*100:.1f}%；'
                  f'近消失门限={e["base"]*.1:.2f}，减弱门限={e["base"]*config["reduction_ratio"]:.2f} L/min，无绝对下限；'
                  f'严格达标{b-a:g}秒，首次达标{wall(a)}，确认时刻{wall(a+config["min_duration"])}；'
                  f'判定{wall(a)}—{wall(b)}；不回溯5秒；'
                  f'源秒转折数={evidence["coarse_turn_count"]}（非呼吸次数）；复核提示：{flag_text}。'
                  '噪声代理未标定，非临床暂停/低通气/OA/CA/AHI。')
            add(a, b, TYPES[0] if near else TYPES[1], note, evidence)

    for i, tick in enumerate(f['clock']):
        tick = float(tick)
        amp, seg, noise = f['a'][i], f['segment'][i], f['noise'][i]
        if seg != previous_segment or not np.isfinite(amp):
            close(last_tick, '数据缺口、无效信号或模式/运行状态变化造成截断')
            history.clear()
            previous_segment = seg
            if not np.isfinite(amp):
                last_tick = tick
                continue
        last_tick = tick
        while history and history[0][0] < tick-120:
            history.popleft()
        if episode is None:
            if len(history) < 30:
                history.append((tick, amp, noise))
                continue
            baseline = float(np.quantile([v for _, v, _ in history], .75))
            noises = [v for _, _, v in history if np.isfinite(v)]
            noise_reference = float(np.quantile(noises, .75)) if len(noises) >= 15 else None
        else:
            baseline, noise_reference = episode['base'], episode['noise']
        trace[B][i] = baseline
        trace[NEAR][i] = baseline*.1
        trace[WEAK][i] = baseline*config['reduction_ratio']
        if baseline <= 1e-9 or (noise_reference is not None and baseline <= noise_reference*3):
            insufficient[i] = True
            close(tick, '参考幅度接近噪声，无法可靠分析')
            history.append((tick, amp, noise))
            continue
        ratio = amp/baseline
        trace[RATIO][i], trace[DROP][i] = ratio*100, (1-ratio)*100
        near_resolvable = (noise_reference is not None and np.isfinite(noise)
                           and baseline*.1 > 3*max(noise_reference, float(noise)))
        quality[i] = 2 if near_resolvable else 1
        if episode is None:
            if ratio > config['reduction_ratio']:
                history.append((tick, amp, noise))
                continue
            episode = dict(start=tick, base=baseline, noise=noise_reference, values=[],
                           near_start=None, weak_start=None, near_runs=[], weak_runs=[])
        elif ratio > config['reduced_exit_ratio']:
            close(tick)
            history.append((tick, amp, noise))
            continue
        episode['values'].append((tick, amp, near_resolvable))
        # Hysteresis freezes the enclosing context only. Every exported interval
        # must independently satisfy a strict continuous threshold for >=7s.
        for name, condition in (('near', ratio <= .1 and near_resolvable),
                                ('weak', ratio <= config['reduction_ratio'])):
            begin = episode[name+'_start']
            if condition and begin is None:
                episode[name+'_start'] = tick
            elif not condition and begin is not None:
                episode[name+'_runs'].append((begin, tick))
                episode[name+'_start'] = None
    close(min(float(recording.meta['raw_duration']), last_tick), '记录末端截断，尚未确认恢复')
    for a, b in recording.gaps:
        add(a, b, '数据缺失', f'原始时间戳间隔{b-a:g}秒，不等于已证实丢失同样时长样本；不跨缺口判定。')
    for mask, reason in ((~f['valid'], '有效样本不足/非有限数据过多/模式状态混合'),
                         (insufficient, '参考接近噪声或无参考波动，无法可靠分析；不能视为正常')):
        for a, b in _runs(mask):
            if b-a >= config['min_duration']:
                add(float(f['index'][a]), float(f['clock'][b-1]), '信号异常', reason+'；噪声代理及质量门限待人工验证。')
    result = dict(events=sorted(events, key=lambda e: (e['start'], e['type'])), trace=trace, quality=quality)
    cache[key] = result
    while len(cache) > 2:
        cache.popitem(last=False)
    return result


def detect(recording, start=0, end=None, min_duration=7, reduction_ratio=.5, event_types=None):
    config = settings(min_duration, reduction_ratio, event_types)
    duration = recording.meta['raw_duration']
    end = duration if end is None else end
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (start, end)) or not 0 <= start < end <= duration:
        raise ValueError('多特征测试时间范围无效')
    result = _compute(recording, config)
    f = features(recording)
    scope = (f['clock'] >= start) & (f['clock'] < end)
    events = [dict(e) for e in result['events'] if e['type'] in config['event_types'] and e['end'] > start and e['start'] < end]
    return dict(file_id=recording.meta['file_id'], events=events, settings=config,
                scan_start=start, scan_end=end, analyzed_rows=len(recording.x),
                eligible_seconds=int(np.sum((result['quality'] > 0) & scope)),
                near_resolvable_seconds=int(np.sum((result['quality'] == 2) & scope)),
                warnings=['独立多特征测试，不是复现厂商完整算法，尚未用人工完整标注集验证。',
                          '近消失严格剩余≤10%；减弱严格按所选比例；持续达标后确认，不回溯5秒。',
                          '噪声代理是未标定工程线索，重复源时间戳不代表真实采样率；无法分辨时保留减弱及复核提示。',
                          '压力/总漏气变化作为复核依据，不直接删除流量候选；不执行调压、不判OA/CA、临床低通气或AHI。',
                          '完整上下文分析，返回与选区相交的完整事件；图中参考及门限随当前参数计算。'])


def waveform_channels(recording, min_duration=7, reduction_ratio=.5):
    result = _compute(recording, settings(min_duration, reduction_ratio))
    f = features(recording)
    source_index = np.floor(recording.x).astype(int)
    channels = {}
    for name, values in result['trace'].items():
        lookup = np.full(int(np.floor(recording.meta['raw_duration']))+1, np.nan)
        lookup[f['clock']] = values
        channels[name] = lookup[source_index]
    return channels
