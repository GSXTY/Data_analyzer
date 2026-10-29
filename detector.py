"""Exploratory machine-total-flow screening, not a clinical apnea scorer."""
import hashlib
import json
import math
from datetime import datetime, timezone
import numpy as np
import pandas as pd

VERSION = 'machine-flow-v1'


def runs(mask):
    bounds = np.diff(np.r_[False, np.asarray(mask, dtype=bool), False].astype(int))
    return zip(np.flatnonzero(bounds == 1), np.flatnonzero(bounds == -1))


def detect(recording, start=0, end=None, min_duration=10, reduction_ratio=.3):
    """Analyze all original rows, aggregate within source seconds, preserve gaps.

    Both flow and leak are total machine signals; no subtraction or patient-flow
    reconstruction is attempted. Timing is source-second resolution, not 50 Hz.
    """
    duration=recording.meta['raw_duration']
    end=duration if end is None else end
    if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in (start,end,min_duration,reduction_ratio)):
        raise ValueError('检测参数必须为有限数值')
    if not 0 <= start < end <= duration or not 3 <= min_duration <= 120 or not .05 <= reduction_ratio <= .8:
        raise ValueError('时间范围无效；最短持续3～120秒，减弱比例0.05～0.8')
    if '流量' not in recording.channels:
        raise ValueError('缺少“流量”通道，无法扫描机器流量')
    # Include pre-window context for baseline; only save events wholly in scope.
    lo=np.searchsorted(recording.x,max(0,start-150),'left')
    hi=np.searchsorted(recording.x,end,'right')
    source_x=recording.x[lo:hi]
    frame=pd.DataFrame({'sec':np.floor(source_x).astype(int),'flow':recording.channels['流量'][lo:hi]})
    for name in ('漏气量','压力','治疗压','运行状态'):
        if name in recording.channels:frame[name]=recording.channels[name][lo:hi]
    grouped=frame.groupby('sec',sort=True)
    index=np.arange(int(math.floor(max(0,start-150))),int(math.ceil(end)))
    if not len(index):raise ValueError('检测范围太短')
    med=grouped.median(numeric_only=True).reindex(index)
    count=grouped['flow'].count().reindex(index,fill_value=0)
    raw_count=grouped.size().reindex(index,fill_value=0)
    amplitude=(grouped['flow'].quantile(.95)-grouped['flow'].quantile(.05)).reindex(index)
    # Rolling operations must not cross a missing second or recorded state change.
    state_change=med['运行状态'].diff().fillna(0).ne(0) if '运行状态' in med else pd.Series(False,index=index)
    valid=count.ge(5)&med['flow'].notna()
    segment=(~valid|~valid.shift(1,fill_value=False)|state_change).cumsum()
    env=amplitude.groupby(segment).transform(lambda x:x.rolling(3,min_periods=3).max())
    baseline=env.groupby(segment).transform(lambda x:x.shift(1).rolling(120,min_periods=30).quantile(.75))
    quality=valid&baseline.ge(3)
    pressure_bad=pd.Series(False,index=index)
    if '压力' in med or '治疗压' in med:
        # Do not treat normal breath-by-breath pressure cycling as setting changes.
        p=med['治疗压'] if '治疗压' in med else med['压力'].rolling(15,min_periods=10).median()
        delta=p.diff().abs();typical=p.abs().rolling(30,min_periods=5).median()
        pressure_bad=(delta>np.maximum(20,typical*.15)).rolling(7,center=True,min_periods=1).max().fillna(0).astype(bool)
    leak_bad=pd.Series(False,index=index);leak_baseline=None
    if '漏气量' in med:
        leak=med['漏气量'];leak_baseline=leak.groupby(segment).transform(lambda x:x.shift(1).rolling(120,min_periods=30).median())
        mad=(leak-leak_baseline).abs().groupby(segment).transform(lambda x:x.shift(1).rolling(120,min_periods=20).median())
        leak_bad=valid&((leak-leak_baseline)>np.maximum(10,np.maximum(leak_baseline.abs()*.5,mad*4)))
    state_bad=state_change.rolling(7,center=True,min_periods=1).max().fillna(0).astype(bool)
    ratio=env/baseline
    low=quality&ratio.le(reduction_ratio)&~pressure_bad&~leak_bad&~state_bad
    settings=dict(version=VERSION,min_duration=min_duration,reduction_ratio=reduction_ratio,flow='machine_total',leak='total')
    events=[]
    def add(a,b,kind,channels,evidence):
        a=max(float(a),start);b=min(float(b),end)
        if b<=a:return
        # ID is invariant to scanned scope. Re-scanning will preserve reviewed/ignored IDs.
        key=json.dumps([recording.meta['file_id'],VERSION,a,b,kind,settings],sort_keys=True)
        identifier='det-'+hashlib.sha256(key.encode()).hexdigest()[:32]
        events.append(dict(id=identifier,start=a,end=b,mode='raw',type=kind,channels=channels,
                           source='detector',status='candidate',updated_at=datetime.now(timezone.utc).isoformat(),
                           note='自动筛查 '+VERSION+'；机器流量/总漏气；'+evidence+'；原始秒级时间，非临床暂停或AHI判定。'))
    for a,b in runs(low.fillna(False)):
        if min(index[b-1]+1,end)-max(index[a],start)<min_duration:continue
        sec=index[a:b];r=float(ratio.iloc[a:b].median())
        add(sec[0],sec[-1]+1,'呼吸波动减弱',['流量'],f'3秒波动包络/前120秒参考波动75分位值中位比={r:.3f}，阈值≤{reduction_ratio}，持续≥{min_duration}秒；已排除检出的压力/状态突变与总漏气升高')
    if '漏气量' in med:
        for a,b in runs(leak_bad.fillna(False)):
            if min(index[b-1]+1,end)-max(index[a],start)<min_duration:continue
            add(index[a],index[b-1]+1,'总漏气升高',['漏气量'],f'总漏气相对前120秒基线升高，中位变化={float((med["漏气量"]-leak_baseline).iloc[a:b].median()):.2f}原始单位；阈值=max(10,基线绝对值×0.5,4×MAD)，不是非故意漏气量')
    # Explicit source-time gaps: include the gap start at the preceding second end.
    for a,b in recording.gaps:
        if b>start and a<end:add(a,b,'数据缺失',['流量'],f'相邻原始时间戳间隔{b-a:.1f}秒，不用于呼吸波动事件计时')
    for a,b in runs((~valid).to_numpy()):
        if b-a>=3 and raw_count.iloc[a:b].sum()>0:
            add(index[a],index[b-1]+1,'信号异常',['流量'],'连续秒缺少有效流量样本（每秒不足5条），从呼吸波动检测中排除')
    # Boundary-clipped candidates are omitted from low-flow detection, not inflated.
    result=sorted(events,key=lambda e:(e['start'],e['type']))
    warnings=['机器总流量不能直接代表患者流量；此算法是可解释的工程筛查，阈值尚未由人工标注集验证。',
              '未确认采样率/单位；秒级分组直接使用全部原始样本，不使用绘图降采样点。',
              '前30秒或中断/状态变化后基线不足的区间不产生流量减弱候选；总漏气不与机器流量相减。']
    eligible=int((quality&~pressure_bad&~leak_bad&~state_bad&pd.Series(index>=start,index=index)&pd.Series(index<end,index=index)).sum())
    if eligible<.5*(end-start):warnings.append('本次可筛查的有效秒数不足所选范围50%，未覆盖区间不可据此排除异常。')
    return dict(file_id=recording.meta['file_id'],events=result,settings=settings,
                scan_start=start,scan_end=end,analyzed_rows=int(hi-lo),
                eligible_seconds=eligible,warnings=warnings)
