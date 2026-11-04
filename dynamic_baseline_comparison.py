"""Read-only source-second window comparison on unverified MACHINE total flow.

This offline experiment does not reconstruct patient flow, subtract total leak,
implement a manufacturer algorithm, or write viewer annotations/scan batches.
"""
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
import argparse
import hashlib
import json
import math

import numpy as np
import pandas as pd

VERSION = 'dynamic-baseline-comparison-v1'
WINDOWS = (2, 5, 8)
METHODS = {'absolute_mean': '总流量绝对值均值÷2（未去背景）',
           'centered_rms': '窗口去均值RMS（机器波动对照）'}


def source_seconds(recording):
    idx = np.arange(int(np.floor(recording.meta['raw_duration'])))
    raw = np.asarray(recording.channels['流量'], float)
    finite = np.isfinite(raw)
    values = np.where(finite, raw, np.nan)
    anchor = float(raw[np.flatnonzero(finite)[0]]) if finite.any() else 0.
    # A constant anchor improves numerical stability without estimating leak.
    delta = values-anchor
    with np.errstate(over='ignore', invalid='ignore'):
        frame = pd.DataFrame(dict(sec=np.floor(recording.x).astype(int), delta=delta,
                                  square=delta*delta, absolute=np.abs(values)*.5))
    names = [n for n in ('模式', '运行状态', '流量', '漏气量', '压力', '治疗压') if n in recording.channels]
    for name in names:
        frame[name] = recording.channels[name]
    grouped = frame.groupby('sec', sort=True)
    med = grouped[names].median().reindex(idx)
    count = grouped['delta'].count().reindex(idx, fill_value=0)
    size = grouped.size().reindex(idx, fill_value=0)
    means = grouped[['delta', 'square', 'absolute']].mean().reindex(idx)
    valid = count.ge(5) & count.ge(size*.8) & np.isfinite(means).all(axis=1)
    change = pd.Series(False, index=idx)
    for name in ('模式', '运行状态'):
        if name in med:
            valid &= grouped[name].nunique().reindex(idx, fill_value=0).eq(1)
            valid &= grouped[name].count().reindex(idx, fill_value=0).ge(size*.8)
            change |= med[name].diff().fillna(0).ne(0)
    segment = (~valid | ~valid.shift(1, fill_value=False) | change).cumsum()
    return dict(index=idx, clock=idx+1, means=means, med=med,
                valid=valid.to_numpy(), segment=segment.to_numpy())


def short_strength(source, window, method):
    if isinstance(window, bool) or window not in WINDOWS or method not in METHODS:
        raise ValueError('对照仅支持2/5/8秒及absolute_mean/centered_rms')
    groups = source['segment']
    def average(s):
        return s.groupby(groups).transform(lambda p: p.rolling(window, min_periods=window).mean())
    if method == 'absolute_mean':
        value = average(source['means']['absolute'])
    else:
        # Each source second receives equal weight, not each CSV row. This is
        # a descriptive spread, NOT RMS of calibrated zero-centred patient flow.
        variance = average(source['means']['square'])-average(source['means']['delta'])**2
        value = np.sqrt(variance.clip(lower=0))
    return value.where(source['valid']).to_numpy()


def evaluate(strength, source, duration, minimum=10, baseline_seconds=100):
    if not math.isfinite(minimum) or minimum <= 0:
        raise ValueError('持续时间必须为正数')
    baseline_trace = np.full(len(strength), np.nan)
    ratio_trace = np.full(len(strength), np.nan)
    history, events = deque(), []
    episode, previous_segment, last_tick = None, None, 0.
    def close(t, reason):
        nonlocal episode
        if episode is None:
            return
        e, episode = episode, None
        if e['core_start'] is not None:
            e['cores'].append((e['core_start'], t))
        cores = [(a, b) for a, b in e['cores'] if b-a >= minimum]
        intervals = cores or ([(e['start'], t)] if t-e['start'] >= minimum else [])
        for a, b in intervals:
            events.append(dict(start=float(a), end=float(b), duration=float(b-a),
                               type='强度显著下降候选' if cores else '强度减弱候选',
                               reference=e['reference'], confirmed_at=float(a+minimum),
                               parent_start=e['start'], parent_end=float(t), end_reason=reason,
                               clinical_classification=None))
    for i, tick in enumerate(source['clock']):
        tick, value, segment = float(tick), strength[i], source['segment'][i]
        if segment != previous_segment or not np.isfinite(value):
            close(last_tick, '无效信号/缺口/状态变化截断')
            history.clear()
            previous_segment = segment
            if not np.isfinite(value):
                last_tick = tick
                continue
        last_tick = tick
        while history and history[0][0] < tick-baseline_seconds:
            history.popleft()
        reference = episode['reference'] if episode else float(np.mean([v for _, v in history])) if len(history) >= 30 else None
        if reference is None or reference <= 1e-9:
            history.append((tick, value))
            continue
        baseline_trace[i], ratio_trace[i] = reference, value/reference
        if episode is None:
            if value/reference >= .5:
                history.append((tick, value))
                continue
            episode = dict(start=tick, reference=reference, core_start=None, cores=[])
        elif value/reference >= .5:
            close(tick, '恢复至剩余≥50%')
            history.append((tick, value))
            continue
        if value/reference < .25 and episode['core_start'] is None:
            episode['core_start'] = tick
        elif value/reference >= .25 and episode['core_start'] is not None:
            episode['cores'].append((episode['core_start'], tick))
            episode['core_start'] = None
    close(min(float(duration), last_tick), '记录末端截断')
    return dict(events=events, baseline=baseline_trace, remaining=ratio_trace,
                eligible_seconds=int(np.isfinite(ratio_trace).sum()))


def ideal_stop(window, stop_seconds, minimum=10, step=.01):
    """Isolate window distortion with a constant true reference, no extra filter."""
    t=np.arange(-20, stop_seconds+20, step)
    strength=np.where((t >= -1e-8) & (t < stop_seconds-1e-8),0.,1.)
    averaged=pd.Series(strength).rolling(round(window/step),min_periods=round(window/step)).mean().to_numpy()
    mask=np.isfinite(averaged) & (averaged < .25)
    selected=np.flatnonzero(mask)
    start=float(t[selected[0]]) if len(selected) else None
    end=float(t[selected[-1]]+step) if len(selected) else None
    seconds=end-start if start is not None else 0.
    return dict(window=window,true_stop_seconds=stop_seconds,threshold_remaining=.25,
                first_qualifying=round(start,3) if start is not None else None,
                end_qualifying=round(end,3) if end is not None else None,
                qualifying_seconds=round(seconds,3),meets_ten_seconds=seconds >= minimum-1e-7,
                assumption='ideal strength step, trailing mean, frozen reference=1; no1Hz filter')


def audit(recording):
    result={}
    for name in ('流量','漏气量'):
        if name not in recording.channels:
            continue
        values=np.asarray(recording.channels[name],float)
        values=values[np.isfinite(values)]
        result[name]=dict(min=float(values.min()),p05=float(np.quantile(values,.05)),
                          median=float(np.median(values)),p95=float(np.quantile(values,.95)),max=float(values.max()),
                          negative_fraction=float(np.mean(values<0)),zero_fraction=float(np.mean(values==0)))
    result['interpretation']='负值与零值不能证明流量语义、漏气组成或Patient Flow公式；需要研发确认符号、排气成分、估算与时间同步。'
    return result


def compare(recording):
    source=source_seconds(recording)
    trials=[]
    for method in METHODS:
        for window in WINDOWS:
            strength=short_strength(source,window,method)
            out=evaluate(strength,source,recording.meta['raw_duration'])
            trials.append(dict(method=method,label=METHODS[method],window_seconds=window,
                               settings=dict(version=VERSION,minimum_seconds=10,baseline_seconds=100,
                                             warmup_valid_seconds=30,remaining_thresholds=[.25,.5],
                                             freeze_at='first remaining<.5, before confirmation',
                                             lowpass_1hz=False,source_time='original whole-second bins',
                                             leak_subtraction=False,patient_flow_verified=False),
                               **out,strength=strength))
    return source,trials


def _finite_list(values):
    return [float(v) if np.isfinite(v) else None for v in values]


def run(path,output):
    # Import the loader only for offline use; no viewer server or event store.
    from viewer import Recording
    raw=path.read_bytes();recording=Recording(raw,path.name)
    source,trials=compare(recording)
    folder=output/recording.meta['file_id'][:12];folder.mkdir(parents=True,exist_ok=True)
    origin=datetime.fromisoformat(recording.meta['start'])
    def wall(t):return str(origin+timedelta(seconds=t))
    summaries=[]
    for trial in trials:
        for event in trial['events']:
            event.update(start_time=wall(event['start']),end_time=wall(event['end']))
        summaries.append({k:v for k,v in trial.items() if k not in ('strength','baseline','remaining')})
    examples=[ideal_stop(window,seconds) for seconds in (10,13,20) for window in WINDOWS]
    result=dict(name=path.name,file_id=recording.meta['file_id'],start=recording.meta['start'],
                rows=recording.meta['rows'],read_only=True,source_csv_copied=False,input_role='unverified_machine_total_flow',
                audit=audit(recording),ideal_window_examples=examples,trials=summaries,
                limitations=['无已验证患者流量，不能认定任何分支复现ResMed或提供临床分类。',
                             '去窗口均值RMS仅为机器波动工程对照，不是漏气校正或患者通气量。',
                             '原始时间戳重复，未假定50Hz；未实现可验证的1Hz采样滤波，源秒内均匀记录假设也未验证。',
                             '当前比较采用10秒以评估所给文档，不修改网页现有7秒检测。',
                             '压力/漏气突变应复核，不能将这份未屏蔽对照当成可直接使用的事件真值。',
                             '没有完整人工标注，候选数量不能表示准确率；未写入事件库。'])
    (folder/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    plot_data=dict(name=path.name,start=recording.meta['start'],clock=source['clock'].tolist(),
                   source_medians={name:_finite_list(source['med'][name]) for name in ('流量','漏气量') if name in source['med']},
                   trials=[dict(method=t['method'],window=t['window_seconds'],strength=_finite_list(t['strength']),
                                baseline=_finite_list(t['baseline']),remaining=_finite_list(t['remaining'])) for t in trials])
    (folder/'curves.json').write_text(json.dumps(plot_data,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    lines=['# 动态基线独立对照报告','',f'文件：{path.name}',f'原始时间：{recording.meta["start"]}—{recording.meta["end"]}',
           '', '这是离线工程对照。没有确认Patient Flow，没有从机器流量扣总漏气，没有写入网页标注或扫描批次。',
           '', '## 输入审查','', '| 字段 | 最小 | 中位 | P95 | 最大 | 负值比例 | 零值比例 |',
           '|---|---:|---:|---:|---:|---:|---:|']
    for name,s in result['audit'].items():
        if isinstance(s,dict):lines.append(f'| {name} | {s["min"]:.2f} | {s["median"]:.2f} | {s["p95"]:.2f} | {s["max"]:.2f} | {s["negative_fraction"]:.1%} | {s["zero_fraction"]:.1%} |')
    lines+=['',result['audit']['interpretation'],'','## 窗口与方法对照','',
            '所有分支：前100秒参考均值（至少30秒有效强度），首次低于50%即冻结；严格低于25%的核心≥10秒输出显著下降候选，否则整体低于50%过程≥10秒输出减弱候选。均为机器信号线索。',
            '', '| 方法 | 窗口 | 显著下降候选 | 减弱候选 | 有参考秒数 |','|---|---:|---:|---:|---:|']
    for t in summaries:
        counts={kind:sum(e['type']==kind for e in t['events']) for kind in ('强度显著下降候选','强度减弱候选')}
        lines.append(f'| {t["label"]} | {t["window_seconds"]}秒 | {counts["强度显著下降候选"]} | {counts["强度减弱候选"]} | {t["eligible_seconds"]} |')
    lines+=['','## 纯窗口时长实验','','此处使用已知参考=1的理想强度骤降，不含呼吸波形、漏气、1Hz滤波或参考漂移，用来单独观察平滑造成的延迟。',
            '', '| 真实停止 | 窗口 | 首次低于25% | 恢复后终点（相对停止起点） | 达标时长 | 是否达到10秒 |',
            '|---:|---:|---:|---:|---:|---|']
    for e in examples:lines.append(f'| {e["true_stop_seconds"]}秒 | {e["window"]}秒 | {e["first_qualifying"]}秒 | {e["end_qualifying"]}秒 | {e["qualifying_seconds"]}秒 | {"是" if e["meets_ten_seconds"] else "否"} |')
    lines+=['','短窗口也有边界延迟，接近最短持续时间的事件仍可能漏检；不自动前移起点或补造持续时间。',
            '', '## 候选样例','', '| 方法/窗口 | 类型 | 原始开始时间 | 原始结束时间 | 持续 |', '|---|---|---|---|---:|']
    for t in summaries:
        for e in t['events'][:6]:lines.append(f'| {t["label"]}/{t["window_seconds"]}秒 | {e["type"]} | {e["start_time"]} | {e["end_time"]} | {e["duration"]:g}秒 |')
    lines+=['','完整候选在comparison.json；样例不是全列表。','','## 使用限制','',*['- '+s for s in result['limitations']],
            '', '下一步需研发确认：流量公式/符号/是否已扣排气，漏气是否包含面罩排气与其平滑延迟，两个通道的实际采样或更新时钟。']
    (folder/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(name=path.name,folder=str(folder),audit=result['audit'],
                         trials=[dict(method=t['method'],window=t['window_seconds'],counts={k:sum(e['type']==k for e in t['events']) for k in ('强度显著下降候选','强度减弱候选')}) for t in trials],
                         ideal_examples=examples),ensure_ascii=True),flush=True)
    return folder


def main():
    parser=argparse.ArgumentParser(description='离线动态基线窗口对照；不写入网页事件，不重建患者流量')
    parser.add_argument('csv',type=Path,nargs='+')
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parent/'data/reports/dynamic-baseline')
    args=parser.parse_args()
    for path in args.csv:run(path,args.output)


if __name__=='__main__':main()
