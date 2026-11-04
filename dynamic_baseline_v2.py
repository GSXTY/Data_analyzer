"""Independent engineering experiment: causal background, rollback, 1s review.

No patient-flow reconstruction, clinical scoring or device control. Estimated
boundaries cover source-second bins; no fixed W-dependent onset compensation.
"""
from collections import deque
from datetime import datetime,timedelta,timezone
import hashlib
import json
import math
import numpy as np
import pandas as pd
from dynamic_baseline_comparison import source_seconds,WINDOWS

PROFILE='dynamic_baseline_v2'
VERSION='dynamic-baseline-optimized-v2'
TYPES=('强度显著下降候选','强度减弱候选','数据缺失','信号异常')
METHODS={'detrended_abs':'去背景绝对值均值÷2（试验）','centered_rms':'窗口去均值RMS（对照）','absolute_mean':'绝对值均值÷2（未去背景）'}


def names(window):
    return [f'优化{window}秒'+n for n in ('强度','参考','25%门限','50%门限','剩余比例','背景估计','1秒复核强度','1秒复核参考')]


CHANNELS=tuple(n for w in WINDOWS for n in names(w))


def settings(min_duration=10,event_types=None,dynamic_method=None,window_seconds=None):
    method='detrended_abs' if dynamic_method is None else dynamic_method
    window=5 if window_seconds is None else window_seconds
    if not isinstance(method,str) or method not in METHODS:raise ValueError('优化测试方法无效')
    if isinstance(window,bool) or window not in WINDOWS:raise ValueError('优化测试请选择2秒、5秒或8秒窗口')
    if isinstance(min_duration,bool) or not isinstance(min_duration,(int,float)) or not math.isfinite(min_duration) or min_duration!=10:raise ValueError('优化测试的1秒复核区间固定连续达到10秒')
    if event_types is not None and (not isinstance(event_types,list) or not event_types or any(t not in TYPES for t in event_types)):raise ValueError('优化测试事件类型无效')
    return dict(profile=PROFILE,version=VERSION,method=method,window_seconds=int(window),min_duration=10,
                reduction_ratio=.5,near_ratio=.25,extremely_low_ratio=.1,pretrigger_seconds=3,
                background_seconds=30,baseline_seconds=100,min_reference_seconds=30,long_episode_seconds=120,
                background_estimator='causal previous30 source seconds; raw-sample weighted median, equal total weight per source second',
                event_types=[t for t in TYPES if event_types is None or t in event_types],flow_source='csv_flow',
                timing='estimated source-bin coverage; main-window qualified intervals stored separately',
                boundary_review_seconds=1,baseline_policy='freeze at main OR 1s remaining<50%; restore valid non-event dips at original times',
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


def compute(recording,method=None,window=None):
    config=settings(dynamic_method=method,window_seconds=window);method=config['method'];window=config['window_seconds']
    cache=getattr(recording,'_dynamic_v2_cache',None)
    if cache is None:cache={};recording._dynamic_v2_cache=cache
    if (method,window) in cache:return cache[(method,window)]
    source,raw_groups=_source(recording);size=len(source['index'])
    traces={n:np.full(size,np.nan) for n in ('strength','reference','remaining','background','fast','fast_reference')}
    history=deque();background_history=deque();short=deque(maxlen=window)
    episode=None;events=[];previous_segment=None;restored=0;last_tick=0.
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
        if parent['background_held']:flags.append('背景沿用最近有效估计，待有效背景历史补足；需复核')
        if rows[-1]['tick']-parent['start']>=120:flags.append('长段减弱/基线变化待查（≥120秒）；未自动重建正常基线')
        if reason!='恢复':flags.append(reason)
        cores=[v for v in _intervals(rows,lambda r:r['fast_ratio']<.25) if v[1]-v[0]>=10]
        spans=cores or [v for v in _intervals(rows,lambda r:r['fast_ratio']<.5) if v[1]-v[0]>=10]
        emitted=False
        for a,b in spans:
            if parent['pretrigger_at'] is None:continue
            # The selected main window must provide corroborating evidence.
            support=[r for r in rows if a<=r['tick']-1<b+window and r['ratio']<.5]
            if not support:continue
            selected=[r for r in rows if a<=r['tick']-1<b]
            threshold=.25 if cores else .5
            qualified=_intervals(rows,lambda r:r['ratio']<threshold,clock_end=True)
            qualified=[[x,min(y,last_tick,recording.meta['raw_duration'])] for x,y in qualified if min(y,last_tick,recording.meta['raw_duration'])>x]
            extreme=[v for v in _intervals(selected,lambda r:r['fast_ratio']<=.1) if v[1]-v[0]>=10]
            confirmed=max(a+10,support[0]['tick'])
            if confirmed>last_tick:continue
            evidence=dict(schema=1,algorithm_version=VERSION,method=method,window_seconds=window,
                estimated_waveform_interval=[a,b],boundary_duration_seconds=b-a,boundary_resolution_seconds=1,
                boundary_rule='连续1秒复核分箱覆盖范围；不是按W固定前移；不是已验证真实生理边界',
                main_qualified_intervals=qualified[:30],main_interval_count=len(qualified),main_intervals_truncated=len(qualified)>30,
                main_qualified_total_seconds=sum(y-x for x,y in qualified),main_interval_scope='父过程；多个核心可能共享，不能求和去重',
                reference_amplitude=parent['reference'],fast_reference_amplitude=parent['fast_reference'],
                strength_median=float(np.median([r['strength'] for r in selected])),
                remaining_ratio_median=float(np.median([r['ratio'] for r in selected])),
                boundary_remaining_max=max(r['fast_ratio'] for r in selected),boundary_threshold=threshold,
                significant_threshold=parent['reference']*.25,reduced_threshold=parent['reference']*.5,
                minimum_duration=10,pretrigger_seconds=3,pretrigger_confirmed_at=parent['pretrigger_at'],confirmed_at=confirmed,
                parent_start=parent['start'],parent_end=rows[-1]['tick'],extremely_low_strength=bool(extreme),
                extremely_low_intervals=extreme[:30],review_flags=flags,channel_median_changes=changes,
                boundary_censored=reason!='恢复',signal_basis='机器总流量；背景估计不是漏气量，未验证Patient Flow')
            evidence.update(background_from_held_estimate=parent['background_held'],background_age_at_trigger_seconds=parent['background_age'])
            events.append(dict(start=a,end=b,type='强度显著下降候选' if cores else '强度减弱候选',evidence=evidence))
            emitted=True
        # Restore valid rejected dips after recovery. Pressure pulsatility is
        # a review hint, not invalid data; persistent total-leak shifts block
        # rollback. Never bridge quality resets or rewrite past decisions.
        persistent_leak_shift=abs(changes.get('漏气量',0))>=10
        if persistent_leak_shift:last_background=None;last_background_at=None;history.clear();short.clear();background_history.clear()
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
            episode=dict(start=tick-1,reference=ref,fast_reference=fast_ref,background=background,rows=[],pretrigger_count=0,pretrigger_at=None,
                         background_held=held_background,background_age=None if last_background_at is None else tick-last_background_at)
        elif row['ratio']>=.6 and row['fast_ratio']>=.6:
            close('恢复');accept(row);continue
        episode['rows'].append(row)
        episode['pretrigger_count']=episode['pretrigger_count']+1 if row['ratio']<.5 or row['fast_ratio']<.5 else 0
        if episode['pretrigger_count']>=3 and episode['pretrigger_at'] is None:episode['pretrigger_at']=tick
    close('记录末端截断')
    result=dict(source=source,traces=traces,events=events,restored_seconds=restored,settings=config)
    cache[(method,window)]=result
    return result


def detect(recording,start=0,end=None,min_duration=10,event_types=None,dynamic_method=None,window_seconds=None):
    config=settings(min_duration,event_types,dynamic_method,window_seconds)
    end=recording.meta['raw_duration'] if end is None else end
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (start,end)) or not 0<=start<end<=recording.meta['raw_duration']:raise ValueError('优化测试分析范围无效')
    result=compute(recording,config['method'],config['window_seconds']);events=[]
    origin=datetime.fromisoformat(recording.meta['start']);wall=lambda t:str(origin+timedelta(seconds=float(t)))
    identity={k:v for k,v in config.items() if k!='event_types'}
    def add(a,b,kind,note,evidence=None):
        if kind not in config['event_types'] or b<=start or a>=end or b<=a:return
        e=dict(id='det-'+hashlib.sha256(json.dumps([recording.meta['file_id'],a,b,kind,identity],sort_keys=True).encode()).hexdigest()[:32],
               start=float(a),end=float(b),type=kind,channels=['流量'],mode='raw',source='detector',status='candidate',
               detection_profile=PROFILE,detection_input='csv_flow',detection_version=VERSION,
               detection_method=config['method'],detection_window_seconds=config['window_seconds'],note=note,
               updated_at=datetime.now(timezone.utc).isoformat())
        if evidence:
            e['evidence']=evidence
            e['channels'] += [n for n in (names(config['window_seconds'])[0],names(config['window_seconds'])[6]) if n in recording.channels]
        events.append(e)
    for original in result['events']:
        a,b=original['start'],original['end'];evidence=json.loads(json.dumps(original['evidence'],allow_nan=False))
        if a<start or b>end:evidence['review_flags'].append('跨选区，保留完整估计区间')
        note=(f'动态基线优化测试 {VERSION}；{METHODS[config["method"]]} / {config["window_seconds"]}秒；'
              f'估计波形区间 {wall(a)}—{wall(b)}（{b-a:g}秒，1秒分辨率）；'
              f'父过程主窗口达标共{evidence["main_qualified_total_seconds"]:g}秒，与估计时长分开；'
              f'确认时刻{wall(evidence["confirmed_at"])}；背景/参考在预候选期冻结，无事件且质量有效时回填；'
              +('存在连续10秒≤10%极低强度标记；' if evidence['extremely_low_strength'] else '')
              +'复核提示：'+('；'.join(evidence['review_flags']) or '需人工核对波形')+'。未做固定窗口前移，未验证患者流量/临床分类。')
        add(a,b,original['type'],note,evidence)
    source=result['source']
    for a,b in recording.gaps:add(float(a),float(b),'数据缺失','原始时间戳间隔；已重置窗口、背景和参考，不跨缺口复核。')
    edges=np.diff(np.r_[False,~source['valid'],False].astype(int))
    for a,b in zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1)):
        if b-a>=10:add(float(a),float(b),'信号异常','源秒有效性连续不足10秒以上；不用于背景/参考回填。')
    scope=(source['clock']>=start)&(source['clock']<end)
    return dict(file_id=recording.meta['file_id'],settings=config,scan_start=start,scan_end=end,events=sorted(events,key=lambda e:(e['start'],e['type'])),
                analyzed_rows=len(recording.x),eligible_seconds=int(np.sum(np.isfinite(result['traces']['remaining'])&scope)),
                diagnostics=dict(restored_source_seconds=result['restored_seconds']),
                warnings=['独立优化测试版：3秒预触发不直接成为事件，最终1秒复核分箱须严格连续达到10秒，并有主窗口减弱证据。',
                          '起止是源秒级波形估计；主窗口达标区间与确认时刻另存，不用固定W补偿，不保证精确真实边界。',
                          '去背景不是漏气补偿或患者流量重建；无临床分类、无设备控制；新旧版本/不同窗口的重叠候选不能累计。'])


def waveform_channels(recording,requested,method='detrended_abs'):
    answer={};positions=np.searchsorted(np.arange(int(recording.meta['raw_duration']))+1,recording.x,side='right')-1
    for window in WINDOWS:
        keys=names(window)
        if not any(n in requested for n in keys):continue
        t=compute(recording,method,window)['traces'];base=t['reference']
        series=(t['strength'],base,base*.25,base*.5,t['remaining']*100,t['background'],t['fast'],t['fast_reference'])
        for name,values in zip(keys,series):
            if name not in requested:continue
            out=np.full(len(recording.x),np.nan);valid=(positions>=0)&(positions<len(values));out[valid]=values[positions[valid]];answer[name]=out
    return answer
