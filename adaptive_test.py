"""Causal experimental screening of machine flow; no clinical classification."""
from collections import deque
from datetime import datetime,timedelta,timezone
import hashlib,json,math
import numpy as np
import pandas as pd

VERSION='adaptive-flow-test-v2-causal'
A_CHANNEL='测试幅度A（5秒鲁棒）'
TYPES=('测试近消失候选','测试减弱候选','数据缺失','信号异常')

def settings(min_duration=7,reduction_ratio=.5,event_types=None):
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (min_duration,reduction_ratio)) or not 3<=min_duration<=120 or not .1<reduction_ratio<=.8:
        raise ValueError('测试参数无效：最短3～120秒，减弱比例必须大于10%且不超过80%')
    if event_types is not None and (not isinstance(event_types,list) or not event_types or any(t not in TYPES for t in event_types)):raise ValueError('请选择有效的测试事件类型')
    return dict(version=VERSION,profile='adaptive_test',flow_source='csv_flow',event_types=[t for t in TYPES if event_types is None or t in event_types],min_duration=min_duration,reduction_ratio=reduction_ratio,
        window_seconds=5,step_seconds=1,quantiles=[.02,.98],baseline_seconds=120,baseline_min_seconds=30,baseline_quantile=.75,
        baseline_min_amplitude=12,noise_floor=3,near_ratio=.1,near_exit_multiplier=1.3,reduced_exit_ratio=min(.95,max(reduction_ratio+.05,min(.75,reduction_ratio*1.2))),
        baseline_policy='prior valid non-episode seconds; freeze during episode; reset on invalid data/state/treatment changes',
        timing='causal window-end half-open interval; recovery evaluation closes event',scope_policy='full-record context; return complete events intersecting scan range',
        boundary_reference='preceding5s context only; never automatically extend event',flow_unit='L/min',clinical_scoring=False,device_control=False)

def features(recording):
    cached=getattr(recording,'_adaptive_features',None)
    if cached is not None:return cached
    if '流量' not in recording.channels:raise ValueError('自适应测试算法需要CSV流量列')
    index=np.arange(int(np.floor(recording.meta['raw_duration'])))
    raw=np.asarray(recording.channels['流量'],float)
    frame=pd.DataFrame(dict(sec=np.floor(recording.x).astype(int),flow=np.where(np.isfinite(raw),raw,np.nan)))
    names=[n for n in ('运行状态','模式','治疗压','压力','漏气量') if n in recording.channels]
    for name in names:frame[name]=recording.channels[name]
    grouped=frame.groupby('sec');med=grouped[names].median().reindex(index)
    count=grouped['flow'].count().reindex(index,fill_value=0)
    low=grouped['flow'].quantile(.02).reindex(index);high=grouped['flow'].quantile(.98).reindex(index)
    valid=count.ge(5)&low.notna()&high.notna();change=pd.Series(False,index=index)
    for name in ('运行状态','模式'):
        if name in med:
            change|=med[name].diff().fillna(0).ne(0)
            valid&=grouped[name].nunique().reindex(index,fill_value=0).le(1)
    if '治疗压' in med:
        typical=med['治疗压'].abs().rolling(30,min_periods=1).median()
        change|=med['治疗压'].diff().abs().gt(np.maximum(20,typical*.15)).fillna(False)
    segment=(~valid|~valid.shift(1,fill_value=False)|change).cumsum()
    a=high.groupby(segment).transform(lambda v:v.rolling(5,min_periods=5).max())-low.groupby(segment).transform(lambda v:v.rolling(5,min_periods=5).min())
    result=dict(index=index,clock=index+1,a=a.to_numpy(),valid=valid.to_numpy(),segment=segment.to_numpy(),med=med,count=count.to_numpy())
    recording._adaptive_features=result
    return result

def amplitude_channel(recording):
    f=features(recording);lookup=np.full(int(np.floor(recording.meta['raw_duration']))+1,np.nan)
    lookup[f['clock']]=f['a']
    return lookup[np.floor(recording.x).astype(int)]

def _runs(mask):
    edges=np.diff(np.r_[False,np.asarray(mask,dtype=bool),False].astype(int))
    return zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1))

def detect(recording,start=0,end=None,min_duration=7,reduction_ratio=.5,event_types=None):
    config=settings(min_duration,reduction_ratio,event_types)
    duration=recording.meta['raw_duration'];end=duration if end is None else end
    if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in (start,end)) or not 0<=start<end<=duration:raise ValueError('测试时间范围无效')
    f=features(recording);clock=f['clock'];med=f['med'];history=deque();episode=None;events=[];eligible=0;previous_segment=None
    def wall(t):return str(datetime.fromisoformat(recording.meta['start'])+timedelta(seconds=float(t)))
    def add(a,b,kind,note):
        if kind not in config['event_types'] or b<=a or b<=start or a>=end:return
        identity_settings={k:v for k,v in config.items() if k!='event_types'}
        key=json.dumps([recording.meta['file_id'],a,b,kind,identity_settings],sort_keys=True)
        events.append(dict(id='det-'+hashlib.sha256(key.encode()).hexdigest()[:32],start=float(a),end=float(b),mode='raw',type=kind,channels=['流量',A_CHANNEL] if A_CHANNEL in recording.channels else ['流量'],
            source='detector',status='candidate',detection_profile='adaptive_test',detection_input='csv_flow',detection_version=VERSION,updated_at=datetime.now(timezone.utc).isoformat(),note=note))
    def close(t,reason='恢复至退出阈值'):
        nonlocal episode
        if episode is None:return
        e=episode;episode=None
        if e['near_start'] is not None:
            e['near_max']=max(e['near_max'],t-e['near_start']);e['near_runs'].append((e['near_start'],float(t)))
        if t-e['start']<min_duration:return
        cores=[(a,b) for a,b in e['near_runs'] if b-a>=min_duration]
        kind=TYPES[0] if cores else TYPES[1]
        # Near-absence labels use their own qualified state, not recovery shoulders.
        # Multiple qualifying cores remain separately visible within one weak episode.
        for a,b in cores or [(e['start'],t)]:
            before=med.loc[max(0,int(a)-30):int(a)-1];during=med.loc[int(a):int(np.ceil(b))-1]
            flags=[]
            for name,threshold in (('压力',20),('治疗压',20),('漏气量',10)):
                if name in med and before[name].notna().any() and during[name].notna().any():
                    delta=float(during[name].median()-before[name].median())
                    if abs(delta)>=threshold:flags.append(f'{name}中位变化{delta:.1f}'+(' L/min' if name=='漏气量' else ' Pa'))
            if reason!='恢复至退出阈值':flags.append(reason)
            if a<start or b>end:flags.append('与选区边界相交，保留完整事件边界')
            vals=np.asarray([v for tick,v in e['values'] if a<=tick<b]);near=max(3,e['base']*.1)
            note=f'自适应测试 {VERSION}；CSV流量；2%～98%分位/5秒因果窗口；冻结参考幅度={e["base"]:.2f} L/min；近消失进入≤{near:.2f}/退出>{near*1.3:.2f}，减弱进入≤{e["enter"]:.2f}/退出>{e["exit"]:.2f} L/min；A中位比={np.median(vals)/e["base"]:.3f}；整体减弱过程{wall(e["start"])}—{wall(t)}；判定{wall(a)}—{wall(b)}；波形参考{wall(max(0,a-5))}—{wall(b)}（前5秒仅供核对，不计入事件时长）；复核提示：'+('；'.join(flags) or '无上述提示，仍待人工确认')+'。门限含3 L/min下限；迟滞区计入状态持续时间，近消失仅标达标核心区；非临床分类或真实边界恢复。'
            add(a,b,kind,note)
    for i,t in enumerate(clock):
        a=f['a'][i];seg=f['segment'][i]
        if seg!=previous_segment or not np.isfinite(a):
            close(float(t),'缺口、状态/治疗压变化或参考不足导致截断');history.clear();previous_segment=seg
            if not np.isfinite(a):continue
        while history and history[0][0]<t-120:history.popleft()
        if episode is None:
            baseline=float(np.quantile([v for _,v in history],.75)) if len(history)>=30 else None
            if baseline is None or baseline<12:
                history.append((t,a));continue
            enter=max(3,baseline*reduction_ratio);leave=max(3.9,baseline*config['reduced_exit_ratio'])
            if start<=t<end:eligible+=1
            if a<=enter:
                episode=dict(start=float(t),base=baseline,enter=enter,exit=leave,values=[],near_start=None,near_max=0.,near_runs=[])
            else:
                history.append((t,a));continue
        else:
            if start<=t<end:eligible+=1
            if a>episode['exit']:
                close(float(t));history.append((t,a));continue
        episode['values'].append((float(t),float(a)));near=max(3,episode['base']*.1)
        if episode['near_start'] is None:
            if a<=near:episode['near_start']=float(t)
        elif a>near*1.3:
            episode['near_max']=max(episode['near_max'],t-episode['near_start']);episode['near_runs'].append((episode['near_start'],float(t)));episode['near_start']=None
    close(float(duration),'记录末端尚未确认恢复，终点截断')
    for a,b in recording.gaps:
        add(a,b,'数据缺失',f'原始时间戳间隔{b-a:g}秒；不等于已证实丢失同样时长的样本。')
    for a,b in _runs(~f['valid']):
        if b-a>=3:add(float(f['index'][a]),float(clock[b-1]),'信号异常','有效流量样本不足或状态混合，测试窗口与参考已重置。')
    return dict(file_id=recording.meta['file_id'],events=sorted(events,key=lambda e:(e['start'],e['type'])),settings=config,scan_start=start,scan_end=end,
        analyzed_rows=len(recording.x),eligible_seconds=eligible,warnings=['自适应测试候选未经人工标注集验证；检出更多不等于准确率更高。',
            '使用完整CSV机器总流量，因果5秒鲁棒幅度；至少30秒有效参考，缺口/状态或治疗设置变化后重置。',
            '一次连续减弱过程先完整计时，再按最长近消失持续时间分型；参考冻结、迟滞保持会影响边界。',
            '使用完整记录上下文；返回与选区相交的完整事件。波形参考范围仅供检查，不自动扩展事件或确认OA/CA、低通气、AHI。'])
