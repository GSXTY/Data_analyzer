"""Read-only web adapter for the offline dynamic-baseline comparison."""
from datetime import datetime, timedelta
import hashlib
import math
import numpy as np

from dynamic_baseline_comparison import VERSION, METHODS, WINDOWS, compare, audit


def names(window):
    prefix=f'动态{window}秒'
    return [prefix+'强度',prefix+'参考',prefix+'25%门限',prefix+'50%门限',prefix+'剩余比例']


CHANNELS=tuple(n for window in WINDOWS for n in names(window))


def cached_comparison(recording):
    cached=getattr(recording,'_dynamic_baseline_comparison',None)
    if cached is None:
        source,trials=compare(recording)
        cached=(source,trials,audit(recording))
        recording._dynamic_baseline_comparison=cached
    return cached


def validate(method,window):
    if not isinstance(method,str) or method not in METHODS:
        raise ValueError('请选择绝对值均值或窗口去均值RMS方法')
    if isinstance(window,bool) or window not in (*WINDOWS,'both'):
        raise ValueError('请选择2秒、5秒、8秒或同时对照2秒和8秒')


def analyze(recording,start=0,end=None,method='absolute_mean',window='both'):
    validate(method,window)
    end=recording.meta['raw_duration'] if end is None else end
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (start,end)) or not 0<=start<end<=recording.meta['raw_duration']:
        raise ValueError('动态基线分析区间无效')
    source,trials,input_audit=cached_comparison(recording)
    origin=datetime.fromisoformat(recording.meta['start'])
    wall=lambda t:str(origin+timedelta(seconds=float(t)))
    events=[];by_window=[]
    windows=(2,8) if window=='both' else (window,)
    for trial in trials:
        seconds=trial['window_seconds']
        if trial['method']!=method or seconds not in windows:
            continue
        selected=[e for e in trial['events'] if e['end']>start and e['start']<end]
        scope=(source['clock']>=start)&(source['clock']<end)
        by_window.append(dict(window_seconds=seconds,count=len(selected),
                              counts={kind:sum(e['type']==kind for e in selected) for kind in ('强度显著下降候选','强度减弱候选')},
                              eligible_seconds=int(np.sum(np.isfinite(trial['remaining'])&scope))))
        for original in selected:
            e=dict(original)
            e.update(id='preview-'+hashlib.sha256(str([recording.meta['file_id'],VERSION,method,seconds,e['start'],e['end'],e['type']]).encode()).hexdigest()[:24],
                     window_seconds=seconds,method=method,mode='raw',source='read_only_comparison',
                     channels=['流量',names(seconds)[0],names(seconds)[4]],
                     start_time=wall(e['start']),end_time=wall(e['end']),confirmed_time=wall(e['confirmed_at']))
            flags=[]
            before=source['med'].loc[max(0,int(e['start'])-31):int(e['start'])-2]
            during=source['med'].loc[max(0,int(e['start'])-1):max(0,int(math.ceil(e['end']))-2)]
            changes={}
            for name,threshold in (('压力',20),('治疗压',20),('漏气量',10)):
                if name in before and len(before[name].dropna()) and len(during[name].dropna()):
                    delta=float(during[name].median()-before[name].median())
                    if not np.isfinite(delta):continue
                    changes[name]=delta
                    # Description only; pressure/leak changes are not deletion rules.
                    steps=source['med'].loc[max(0,int(e['start'])-2):max(0,int(math.ceil(e['end']))-2),name].diff().abs().max()
                    if abs(delta)>=threshold or np.isfinite(steps) and steps>=threshold:
                        flags.append(name+'变化需复核')
            if e['end_reason']!='恢复至剩余≥50%':flags.append(e['end_reason'])
            if e['start']<start or e['end']>end:flags.append('跨选区，保留完整候选边界')
            e.update(channel_median_changes=changes,review_flags=flags)
            events.append(e)
    return dict(file_id=recording.meta['file_id'],name=recording.meta['name'],version=VERSION,read_only=True,
                scan_start=start,scan_end=end,method=method,method_label=METHODS[method],window=window,
                settings=dict(baseline_seconds=100,min_reference_seconds=30,min_duration=10,remaining_thresholds=[.25,.5],
                              freeze_at='首次剩余<50%即冻结，不等10秒确认',patient_flow_verified=False,leak_subtraction=False,lowpass_1hz=False),
                by_window=by_window,events=sorted(events,key=lambda e:(e['start'],e['window_seconds'])),input_audit=input_audit,
                warnings=['只读对照：不创建标注或扫描批次，不自动确认事件；2秒和8秒可能重叠，不能相加为独立事件。',
                          '输入仍是CSV机器总流量，未减总漏气、未验证患者流量；窗口去均值RMS只是机器波动指标。',
                          '原始重复秒级时间戳下未实现可验证的1Hz滤波；不是ResMed完整算法，不输出临床暂停、低通气、OA/CA或AHI。',
                          '当前测试固定连续10秒，独立于网页其他7秒算法；压力/漏气变化仅作复核线索，未直接屏蔽。'])


def waveform_channels(recording,requested,method='absolute_mean'):
    validate(method,'both')
    source,trials,_=cached_comparison(recording)
    mapping={}
    for trial in trials:
        if trial['method']!=method:continue
        keys=names(trial['window_seconds']);base=trial['baseline']
        series=(trial['strength'],base,base*.25,base*.5,trial['remaining']*100)
        for key,values in zip(keys,series):
            if key not in requested:continue
            lookup=np.full(int(np.floor(recording.meta['raw_duration']))+1,np.nan)
            lookup[source['clock']]=values
            mapping[key]=lookup[np.floor(recording.x).astype(int)]
    return mapping
