"""Experimental reconstruction of the supplied company rule, not device control."""
import hashlib
import json
import math
from datetime import datetime,timezone
import numpy as np
import pandas as pd

VERSION='company-pressure-q-draft-v1'
Q_CHANNEL='公司公式Q（试验）'
A_CHANNEL='公司A（5秒极差）'
FLOW_A_CHANNEL='流量波动幅度A（5秒极差）'
TYPES=('公司暂停候选','公司低通气候选','数据缺失','信号异常')
COEFFICIENTS=(-8.26876272861618,.830695671071454,-.831894817072964,-.0000423257870468439,.0000406935371268139,-.000625461373311312)

def pressure_flow(p1,p2):
    """Inputs already have pressure3 subtracted; never compensate twice."""
    a,b,c,d,e,f=COEFFICIENTS
    with np.errstate(over='ignore',invalid='ignore'):
        q=a+b*p1+c*p2+d*p1*p1+e*p2*p2+f*(p1-p2)**2
    return np.where(np.isfinite(q),q,np.nan)

def settings(min_duration=7,event_types=None,flow_source='pressure_q'):
    if flow_source not in ('pressure_q','csv_flow'):raise ValueError('幅度规则输入无效')
    if flow_source=='csv_flow':
        return dict(version='csv-flow-amplitude-v1',profile='company_rules',flow_source=flow_source,input_channel='流量',min_duration=min_duration,reduction_ratio=.3,event_types=list(TYPES if event_types is None else event_types),
            flow_unit='L/min',window_seconds=5,step_seconds=1,amplitude='flow_max-flow_min',pause_condition='A<10',hypopnea_condition='10<A<17',boundary_policy='A=10或17均不归类',duration_comparison='strictly_greater',timing='source_seconds_window_end',draft_assumptions=True,clinical_scoring=False,device_control=False)
    return dict(version=VERSION,profile='company_rules',min_duration=min_duration,reduction_ratio=.3,event_types=list(TYPES if event_types is None else event_types),
                coefficients=list(COEFFICIENTS),pressure_inputs='P1=气压1-气压3; P2=气压2-气压3',pressure_unit='Pa',flow_unit='L/min',flow_unit_confirmation='按使用者推测暂定，待研发确认',
                csv_flow_equivalence_verified=False,window_seconds=5,step_seconds=1,amplitude='Qmax-Qmin',pause_condition='A<10',hypopnea_condition='10<A<17',
                boundary_policy='A=10或17均不归类',duration_comparison='strictly_greater',timing='source_seconds_window_end',
                draft_assumptions=True,clinical_scoring=False,device_control=False)

def window_statistics(x,q,begin,end):
    """Shared source-second window values for both detector and plot."""
    frame=pd.DataFrame(dict(sec=np.floor(x).astype(int),q=np.where(np.isfinite(q),q,np.nan)))
    grouped=frame.groupby('sec')['q'];index=np.arange(int(np.floor(begin)),int(np.floor(end)))
    count=grouped.count().reindex(index,fill_value=0);valid=count.ge(5)
    amplitude=grouped.max().reindex(index).rolling(5,min_periods=5).max()-grouped.min().reindex(index).rolling(5,min_periods=5).min()
    ready=valid.rolling(5,min_periods=5).sum().eq(5)&amplitude.notna()
    return index,amplitude,ready,valid

def amplitude_channel(x,q,end):
    index,amplitude,ready,_=window_statistics(x,q,0,end)
    lookup=np.full(int(np.floor(end))+1,np.nan)
    lookup[index+1]=amplitude.where(ready).to_numpy()
    return lookup[np.floor(x).astype(int)]

def detect_company(recording,start=0,end=None,min_duration=7,event_types=None,flow_source='pressure_q'):
    end=recording.meta['raw_duration'] if end is None else end
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (start,end,min_duration)) or not 0<=start<end<=recording.meta['raw_duration'] or not 3<=min_duration<=120:
        raise ValueError('公司试验方案时间范围无效；持续门限3～120秒，条件必须连续超过门限')
    if event_types is not None and (not isinstance(event_types,list) or not event_types or any(not isinstance(t,str) or t not in TYPES for t in event_types)):raise ValueError('公司试验方案检测类型无效')
    requested=[t for t in TYPES if event_types is None or t in event_types]
    config=settings(min_duration,requested,flow_source)
    if flow_source=='csv_flow' and '流量' not in recording.channels:raise ValueError('缺少CSV流量通道')
    if flow_source=='pressure_q' and not all(c in recording.channels for c in ('气压1','气压2','气压3')):raise ValueError('压力估算Q需要原始气压1、气压2、气压3（Pa）')
    lo=np.searchsorted(recording.x,max(0,start-min_duration-7));hi=np.searchsorted(recording.x,end,'right')
    ch=recording.channels
    q=ch['流量'][lo:hi] if flow_source=='csv_flow' else pressure_flow(ch['气压1'][lo:hi]-ch['气压3'][lo:hi],ch['气压2'][lo:hi]-ch['气压3'][lo:hi])
    index,amplitude,ready,valid=window_statistics(recording.x[lo:hi],q,max(0,start-min_duration-7),end)
    clock=index+1  # evaluation occurs at end of window, never backdate by 5s
    events=[]
    input_channels=('流量',FLOW_A_CHANNEL) if flow_source=='csv_flow' else (Q_CHANNEL,A_CHANNEL)
    input_note='直接使用CSV流量（L/min），不重新估算Q' if flow_source=='csv_flow' else 'P1/P2已减气压3；Q暂按L/min，与CSV流量公式一致性待确认'
    def add(a,b,kind,evidence):
        a=max(start,float(a));b=min(end,float(b))
        if kind not in requested or b<=a:return
        key=json.dumps([recording.meta['file_id'],a,b,kind,config],sort_keys=True)
        events.append(dict(id='det-'+hashlib.sha256(key.encode()).hexdigest()[:32],start=a,end=b,mode='raw',type=kind,
            channels=[n for n in input_channels if n in ch] or ['气压1','气压2','气压3'],source='detector',status='candidate',detection_profile='company_rules',detection_input=flow_source,updated_at=datetime.now(timezone.utc).isoformat(),
            note=f'幅度规则试验 {config["version"]}；{input_note}；5秒最大值−最小值、每秒更新；条件连续>{min_duration:g}秒；'+evidence+'；起止为条件成立窗口末端时刻，不回溯5秒；A=10/17不归类；窗口步长/边界口径暂定，非临床分类，不执行调压。'))
    def runs(mask):
        edges=np.diff(np.r_[False,np.asarray(mask,dtype=bool),False].astype(int))
        return zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1))
    for kind,mask in ((TYPES[0],amplitude.lt(10)),(TYPES[1],amplitude.gt(10)&amplitude.lt(17))):
        for a,b in runs(ready&mask):
            left=max(start,float(clock[a]));right=min(end,float(clock[b-1]))
            if right-left>min_duration:
                add(left,right,kind,f'A中位值={amplitude.iloc[a:b].median():.4f}，范围={amplitude.iloc[a:b].min():.4f}～{amplitude.iloc[a:b].max():.4f}')
    for a,b in recording.gaps:
        if b>start and a<end:add(a,b,'数据缺失','原始时间戳中断，窗口不得跨过缺失秒')
    for a,b in runs(~valid):
        if b-a>=3 and clock[b-1]>start:add(index[a],clock[b-1],'信号异常','连续秒有效压力推算Q样本不足5条；该区间不判呼吸事件')
    warnings=['幅度规则试验：窗口步长、阈值等号、连续计时口径暂定；不区分OA/CA，不计算AHI。',input_note,'按完整原始行和源时间秒计算；缺失/无效秒重置窗口；未额外滤波，需人工联合复核压力、状态和漏气。']
    return dict(file_id=recording.meta['file_id'],events=sorted(events,key=lambda e:(e['start'],e['type'])),settings=config,scan_start=start,scan_end=end,
        analyzed_rows=int(hi-lo),eligible_seconds=int((ready&pd.Series((clock>=start)&(clock<=end),index=index)).sum()),
        warnings=warnings if flow_source=='csv_flow' else ['幅度规则试验重建：滑动步长、阈值等号、连续计时口径暂定，尚非设备代码逐项复现。',
            'Q由已减标准气压3的P1/P2计算；Q单位、与CSV流量的公式一致性、压力校准有效范围待确认。10/17不是百分比。',
            '没有跨缺失秒补点或强制50Hz；每秒至少5个有效Q值仅为数据质量下限。极值法对尖峰敏感，当前不额外滤波。',
            '本方案不套用旧算法的压力/漏气排除，以免改变公司规则；应联合原始压力、状态和总漏气人工复核。',
            '公司暂停/低通气候选不是临床确诊，不区分OA/CA，不判正常、不计算AHI，不执行调压。'])
