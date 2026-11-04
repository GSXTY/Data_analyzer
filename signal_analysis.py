"""Read-only evidence summaries from complete source rows, not display points."""
from datetime import datetime,timedelta
import re
import numpy as np
import pandas as pd
from detector import detect

def analyze_recording(recording,start=0,end=None,max_examples=10,profile='device_candidates',flow_source='pressure_q',reduction_ratio=None,dynamic_method=None,window_seconds=None,min_duration=None):
    if isinstance(max_examples,bool) or not isinstance(max_examples,int) or not 1<=max_examples<=50:raise ValueError('最多返回1～50条代表事件')
    if profile in ('company_rules','adaptive_test','adaptive_multifeature','dynamic_baseline','dynamic_baseline_v2','dynamic_baseline_v3'):
        if profile in ('dynamic_baseline','dynamic_baseline_v2','dynamic_baseline_v3'):out=detect(recording,start,end,10 if min_duration is None else min_duration,.5,profile,dynamic_method=dynamic_method,window_seconds=window_seconds)
        elif profile in ('adaptive_test','adaptive_multifeature'):out=detect(recording,start,end,7,.5 if reduction_ratio is None else reduction_ratio,profile)
        else:
            from company_rules import detect_company
            out=detect_company(recording,start,end,7,flow_source=flow_source)
        events=out.pop('events')
        out.update(read_only=True,name=recording.meta['name'],counts={t:sum(e['type']==t for e in events) for t in out['settings']['event_types']},
            representative_events=events[:max_examples],omitted_event_details=max(0,len(events)-max_examples),clinical_classification=dict(apnea=None,OA=None,CA=None,hypopnea=None,normal=None,ahi=None),
            note='只读试验分析；代表事件按时间排序，未更改标注或扫描历史。')
        return out
    if profile!='device_candidates':raise ValueError('分析方案无效')
    end=recording.meta['raw_duration'] if end is None else end
    result=detect(recording,start,end,10,.7,'device_candidates')
    names=[n for n in ('流量','漏气量','压力','控制压力','治疗压','气压1−气压3（补偿）','气压2−气压3（补偿）','模式','运行状态') if n in recording.channels]
    lo=np.searchsorted(recording.x,max(0,start-150));hi=np.searchsorted(recording.x,end,'right')
    frame=pd.DataFrame({n:recording.channels[n][lo:hi] for n in names});frame['sec']=np.floor(recording.x[lo:hi]).astype(int)
    g=frame.groupby('sec');idx=np.arange(int(np.floor(max(0,start-150))),int(np.ceil(end)))
    med=g[names].median().reindex(idx);amp=(g['流量'].quantile(.95)-g['流量'].quantile(.05)).reindex(idx)
    def number(value):return float(value) if np.isfinite(value) else None
    def stats(values):
        a=np.asarray(values,dtype=float);a=a[np.isfinite(a)]
        return dict(min=float(a.min()),median=float(np.median(a)),p90=float(np.quantile(a,.9)),max=float(a.max())) if len(a) else None
    def wall(t):return str(datetime.fromisoformat(recording.meta['start'])+timedelta(seconds=float(t)))
    details=[]
    for e in result['events']:
        if not e['type'].startswith('流量波动'):continue
        a,b=e['start'],e['end'];before=med.loc[max(0,int(a)-30):int(a)-1];during=med.loc[int(a):int(b)-1]
        prior_amp=number(amp.loc[max(0,int(a)-30):int(a)-1].median());event_amp=number(amp.loc[int(a):int(b)-1].median())
        values_before={n:number(before[n].median()) for n in names};values_during={n:number(during[n].median()) for n in names}
        flags=[]
        prior_valid_seconds=int(before['流量'].notna().sum())
        if prior_valid_seconds<30:flags.append('紧邻前30秒参考不完整，短窗对比仅供复核')
        if prior_amp is not None and prior_amp>0 and event_amp is not None and event_amp>=prior_amp:flags.append('紧邻前30秒幅度未降低：结果依赖更长的滚动参考，请复核')
        changes={n:number(values_during[n]-values_before[n]) if values_during[n] is not None and values_before[n] is not None else None for n in names}
        if changes.get('压力') is not None and abs(changes['压力'])>=20:flags.append('实测压力中位变化至少20Pa，需联合查看控制响应；不是病因结论')
        if changes.get('治疗压') is not None and abs(changes['治疗压'])>=20:flags.append('治疗压中位变化至少20Pa，需复核设置变化影响')
        ratio=re.search(r'中位比=([\d.]+)',e['note'])
        details.append(dict(event_id=e['id'],type=e['type'],start=a,end=b,mode='raw',start_time=wall(a),end_time=wall(b),duration_seconds=b-a,
                            envelope_baseline_ratio_median=float(ratio.group(1)) if ratio else None,
                            before_30s_medians=values_before,during_medians=values_during,changes=changes,
                            before_30s_available_seconds=prior_valid_seconds,
                            per_second_amplitude_before=prior_amp,per_second_amplitude_during=event_amp,
                            review_flags=flags,assessment='机器波形候选；不能仅凭幅度比确定病因或临床类别'))
    hours=[]
    for a in range(int(start),int(np.ceil(end)),3600):
        b=min(a+3600,end)
        hours.append(dict(start=wall(a),end=wall(b),candidate_count_by_start=sum(a<=e['start']<b for e in details),candidate_seconds=sum(max(0,min(e['end'],b)-max(e['start'],a)) for e in details)))
    return dict(file_id=recording.meta['file_id'],name=recording.meta['name'],source_start=recording.meta['start'],scan_start=start,scan_end=end,time_basis='raw',
                channel_units=recording.meta.get('channel_units',{}),channel_roles=recording.meta.get('channel_roles',{}),sampling_info=recording.meta.get('sampling_info',{}),
                analyzed_rows=result['analyzed_rows'],eligible_seconds=result['eligible_seconds'],settings=result['settings'],
                counts={t:sum(e['type']==t for e in result['events']) for t in ('流量波动近消失候选','流量波动减弱候选','总漏气升高','数据缺失','信号异常')},
                duration_stats=stats([e['duration_seconds'] for e in details]),device_metrics=result['device_metrics'],hourly=hours,
                channel_median_stats={n:stats(med.loc[int(np.ceil(start)):int(np.ceil(end))-1,n]) for n in names},
                review_flag_counts={flag:sum(flag in e['review_flags'] for e in details) for flag in sorted({f for e in details for f in e['review_flags']})},
                representative_events=sorted(details,key=lambda e:(-len(e['review_flags']),-e['duration_seconds'],e['start']))[:max_examples],
                omitted_event_details=max(0,len(details)-max_examples),source_gaps=[g for g in recording.gaps if g[1]>start and g[0]<end],
                clinical_classification=dict(apnea=None,OA=None,CA=None,hypopnea=None,normal=None,
                    reason='患者流量含义和临床评分未验证；无呼吸努力、血氧/觉醒证据。未命中规则不等于正常。'),
                warnings=result['warnings'],read_only=True,note='由完整原始行计算，逐秒统计采用每秒中位数。前30秒幅度对比与检测器120秒滚动参考是两种不同指标；20Pa仅为复核提示门限。没有修改事件、确认状态或扫描批次。')
