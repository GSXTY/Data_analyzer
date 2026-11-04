"""Persistable candidate adapter; same engine as the read-only comparison."""
from datetime import datetime,timezone
import hashlib
import json
import math
import numpy as np

from dynamic_baseline_web import analyze,cached_comparison,names,validate
from dynamic_baseline_comparison import VERSION as ENGINE_VERSION, WINDOWS

PROFILE='dynamic_baseline'
VERSION='dynamic-baseline-detector-v1'
TYPES=('强度显著下降候选','强度减弱候选','数据缺失','信号异常')


def settings(min_duration=10,event_types=None,dynamic_method='centered_rms',window_seconds=2):
    validate(dynamic_method,window_seconds)
    if isinstance(window_seconds,bool) or window_seconds not in WINDOWS:
        raise ValueError('保存检测每次请选择一个窗口：2秒、5秒或8秒；同时显示只用于对照')
    if isinstance(min_duration,bool) or not isinstance(min_duration,(float,int)) or not math.isfinite(min_duration) or min_duration!=10:
        raise ValueError('当前动态基线检测固定连续10秒；其他方案的7秒设置不适用于本方案')
    if event_types is not None and (not isinstance(event_types,list) or not event_types or any(t not in TYPES for t in event_types)):
        raise ValueError('请选择有效的动态基线检测类型')
    return dict(version=VERSION,engine_version=ENGINE_VERSION,profile=PROFILE,method=dynamic_method,
                window_seconds=window_seconds,min_duration=10,reduction_ratio=.5,near_ratio=.25,
                baseline_seconds=100,min_reference_seconds=30,flow_source='csv_flow',
                event_types=[t for t in TYPES if event_types is None or t in event_types],
                baseline_policy='first remaining<50% freezes reference before confirmation',
                scope_policy='full-record context; keep complete events intersecting scope',
                timing='source-second window-end; no onset backdating',
                patient_flow_verified=False,leak_subtraction=False,lowpass_1hz=False,
                clinical_scoring=False,device_control=False)


def detect(recording,start=0,end=None,min_duration=10,event_types=None,dynamic_method='centered_rms',window_seconds=2):
    config=settings(min_duration,event_types,dynamic_method,window_seconds)
    out=analyze(recording,start,end,dynamic_method,window_seconds)
    source,trials,_=cached_comparison(recording)
    trial=next(t for t in trials if t['method']==dynamic_method and t['window_seconds']==window_seconds)
    identity_settings={k:v for k,v in config.items() if k!='event_types'}
    events=[]
    def add(a,b,kind,note,evidence=None,channels=None):
        if kind not in config['event_types'] or b<=a or b<=out['scan_start'] or a>=out['scan_end']:return
        token=json.dumps([recording.meta['file_id'],float(a),float(b),kind,identity_settings],sort_keys=True)
        item=dict(id='det-'+hashlib.sha256(token.encode()).hexdigest()[:32],start=float(a),end=float(b),
                  type=kind,mode='raw',source='detector',status='candidate',
                  channels=channels or ['流量'],note=note,updated_at=datetime.now(timezone.utc).isoformat(),
                  detection_profile=PROFILE,detection_input='csv_flow',detection_version=VERSION,
                  detection_method=dynamic_method,detection_window_seconds=window_seconds)
        if evidence is not None:item['evidence']=evidence
        events.append(item)
    for e in out['events']:
        mask=(source['clock']>=e['start'])&(source['clock']<e['end'])
        ratios=trial['remaining'][mask];strength=trial['strength'][mask]
        ratios=ratios[np.isfinite(ratios)];strength=strength[np.isfinite(strength)]
        evidence=dict(schema=1,algorithm_version=VERSION,method=dynamic_method,window_seconds=window_seconds,
                      reference_amplitude=e['reference'],strength_median=float(np.median(strength)),
                      remaining_ratio_median=float(np.median(ratios)),remaining_ratio_max=float(np.max(ratios)),
                      significant_threshold=e['reference']*.25,reduced_threshold=e['reference']*.5,
                      minimum_duration=10,strict_duration_seconds=e['duration'],confirmed_at=e['confirmed_at'],
                      parent_start=e['parent_start'],parent_end=e['parent_end'],
                      channel_median_changes=e['channel_median_changes'],review_flags=e['review_flags'],
                      signal_basis='CSV机器总流量，Patient Flow未验证',boundary_censored=e['end_reason']!='恢复至剩余≥50%')
        note=(f'动态基线检测 {VERSION}；{out["method_label"]}，{window_seconds}秒窗口；'
              f'前100秒参考（至少30秒有效），首次剩余<50%即冻结；参考={e["reference"]:.2f} L/min；'
              f'强度中位={evidence["strength_median"]:.2f}，剩余中位={evidence["remaining_ratio_median"]*100:.1f}%/最大={evidence["remaining_ratio_max"]*100:.1f}%；'
              f'持续{e["duration"]:g}秒，确认时刻{e["confirmed_time"]}；{e["start_time"]}—{e["end_time"]}；不前移窗口；'
              '复核提示：'+('；'.join(e['review_flags']) or '未触发上述提示，仍需人工复核')+'。'
              '机器信号候选，未减总漏气、未验证患者流量、未实现可验证1Hz滤波，非临床分类。')
        channels=[c for c in ['流量',names(window_seconds)[0],names(window_seconds)[4]] if c in recording.channels]
        add(e['start'],e['end'],e['type'],note,evidence,channels)
    for a,b in recording.gaps:
        add(a,b,'数据缺失',f'原始时间戳间隔{b-a:g}秒，不等于已证实丢失同样时长样本；未跨缺口检测。')
    bad=~source['valid'];edges=np.diff(np.r_[False,bad,False].astype(int))
    for a,b in zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1)):
        if b-a>=10:add(float(source['index'][a]),float(source['clock'][b-1]),'信号异常','有效流量样本不足、无效比例过高或秒内状态混合；候选/参考已重置。')
    return dict(file_id=recording.meta['file_id'],events=sorted(events,key=lambda e:(e['start'],e['type'])),
                settings=config,scan_start=out['scan_start'],scan_end=out['scan_end'],
                analyzed_rows=len(recording.x),eligible_seconds=out['by_window'][0]['eligible_seconds'],
                warnings=['动态基线试验检测：候选可保存、复核和导出，未经完整人工标注集验证。',
                          '本次只使用一个方法和一个窗口；不同方法/窗口的重叠候选不能相加为独立事件。',
                          '剩余<25%核心连续≥10秒为显著下降；否则整体剩余<50%过程连续≥10秒为减弱候选。',
                          '输入是CSV机器总流量，未减总漏气、未验证Patient Flow、未实现可验证1Hz滤波；不是ResMed原版算法，不输出临床暂停、低通气、OA/CA或AHI。'])
