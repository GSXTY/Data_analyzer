'use strict';
let meta, view=[0,1], data={}, overview=[], selected=[], hover=null;
let controller, serial=0, timer, drag=null, overviewDrag=null, raf=0;
let cache=null, overviewImage=null, baseSecond=0, generation=0;
let groups=[], movingChannel=null;
let displayMode='engineering';
let annotations=null,marking=false,markSelection=null,eventDraft=null;
let fileLibrary=[],activeLibraryId=null,librarySerial=0,fileSwitching=false;
let serverRevision=0,eventSyncReady=false,eventSaving=false;
const eventColors={'疑似暂停':'#ce5362','疑似低通气':'#9b69be','漏气':'#dc9a28','波形异常':'#328ca5','伪迹/干扰':'#728398','其他':'#619c59'};
Object.assign(eventColors,{'呼吸波动减弱':'#ce5362','总漏气升高':'#dc9a28','数据缺失':'#728398','信号异常':'#9b69be'});
Object.assign(eventColors,{'流量波动近消失候选':'#be394c','流量波动减弱候选':'#9460b0'});
Object.assign(eventColors,{'公司暂停候选':'#b53c54','公司低通气候选':'#a66abd'});
Object.assign(eventColors,{'测试近消失候选':'#b6345a','测试减弱候选':'#7054b3'});
Object.assign(eventColors,{'强度显著下降候选':'#b6345a','强度减弱候选':'#237d9f'});
const detectionTypes={adaptive_multifeature:['测试近消失候选','测试减弱候选','数据缺失','信号异常'],adaptive_test:['测试近消失候选','测试减弱候选','数据缺失','信号异常'],engineering:['呼吸波动减弱','总漏气升高','数据缺失','信号异常'],device_candidates:['流量波动近消失候选','流量波动减弱候选','总漏气升高','数据缺失','信号异常'],company_rules:['公司暂停候选','公司低通气候选','数据缺失','信号异常']};
let chosenDetectionTypes=new Set(detectionTypes.device_candidates),visibleEventTypes=new Set(Object.keys(eventColors));
detectionTypes.dynamic_baseline=['强度显著下降候选','强度减弱候选','数据缺失','信号异常'];
detectionTypes.dynamic_baseline_v2=[...detectionTypes.dynamic_baseline];
detectionTypes.dynamic_baseline_v3=[...detectionTypes.dynamic_baseline];
function isBaseline(profile){return ['dynamic_baseline','dynamic_baseline_v2','dynamic_baseline_v3'].includes(profile)}
function isOptimized(profile){return ['dynamic_baseline_v2','dynamic_baseline_v3'].includes(profile)}
function optimizedAvailable(profile){return profile==='dynamic_baseline_v3'?meta.dynamic_baseline_optimized_v3_version==='dynamic-baseline-optimized-v3-configurable-v13':meta.dynamic_baseline_optimized_version==='dynamic-baseline-optimized-v2'}
let activeBaselineProfile='dynamic_baseline',optimizedInitialized=false,v3Initialized=false;
let activeAutomaticProfile='device_candidates';
const primaryProfiles=['company_rules','dynamic_baseline_v3'];
function selectDetectionProfile(profile,history=false){
 const select=$('scan-profile');
 for(const option of [...(select.options||[])])if(option.dataset?.legacy==='true'&&primaryProfiles.includes(profile))option.remove?.();
 if(history&&!primaryProfiles.includes(profile)&&![...(select.options||[])].some(o=>o.value===profile)){
  const option=document.createElement('option');option.value=profile;option.textContent='历史算法（已停用检测）';option.hidden=true;option.disabled=true;option.dataset.legacy='true';select.append(option);
 }
 select.value=profile;
}
const scanViews=new Map();
function resultGroupForProfile(profile){return isBaseline(profile)?'baseline':isAdaptive(profile)?'adaptive':profile==='company_rules'?'company':'automatic'}
function currentScanView(){const v=scanViews.get(meta?.file_id);return v?.profile===$('scan-profile').value?v:null}
function actualEventProfile(e){return e.detection_profile||(e.type?.startsWith('公司')||(e.note||'').includes('公司规则试验')?'company_rules':e.type?.startsWith('流量波动')||(e.note||'').includes('machine-flow-candidates-v2')?'device_candidates':'engineering')}
function eventProfileForView(e){const v=currentScanView();return v?.ids.has(e.id)?v.profile:actualEventProfile(e)}
function matchesScanView(e,group){const v=currentScanView();return !v||group==='manual'||group!==resultGroupForProfile(v.profile)||v.ids.has(e.id)}
function eventDuration(e){return e.detection_min_duration??e.evidence?.minimum_duration??10}
function updateBaselineControls(){
 const v3=$('scan-profile').value==='dynamic_baseline_v3';
 for(const option of $('baseline-window').options||[])option.hidden=option.disabled=!v3&&![2,5,8].includes(Number(option.value));
 if(!v3&&![2,5,8].includes(Number($('baseline-window').value)))$('baseline-window').value='5';
 $('baseline-duration').hidden=!v3;$('scan-duration').hidden=v3;
 $('baseline-duration').value=String(Number($('scan-duration').value)||10);
 if(v3)$('optimized-v3-rule-info').textContent='优化v3：检测窗口'+$('baseline-window').value+'秒，最短持续'+$('scan-duration').value+'秒。显著下降：1秒复核剩余<25%连续达标并有主窗口佐证；减弱：所选主窗口剩余<50%连续达标，1秒小峰不打断弱事件。弱事件从窗口末端计时。总漏气变化仅提示复核，不清空参考；无效数据、缺口及状态变化仍重置。';
}
async function showScanBatch(runId,restore=false){
 if(!meta||!runId)return;const fileId=meta.file_id;
 const snapshot=await api('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file_id:fileId,run_id:runId,kind:'automatic'})});
 if(meta?.file_id!==fileId)return;
 const config=snapshot.settings;
 if(restore){selectDetectionProfile(config.profile,true);$('scan-profile').onchange();if(isBaseline(config.profile)){$('baseline-method').value=config.method;$('baseline-window').value=String(config.window_seconds)}$('scan-duration').value=config.min_duration;if(Number.isFinite(config.reduction_ratio))$('scan-ratio').value=Math.round(config.reduction_ratio*100);if(isAdaptive(config.profile))$('adaptive-version').value=config.version;chosenDetectionTypes=new Set(config.event_types||detectionTypes[config.profile]);renderDetectionChoices();if(config.profile==='company_rules')$('amplitude-source').value=config.flow_source||'pressure_q';updateBaselineControls();pruneProfileChannels()}
 scanViews.set(fileId,{runId,profile:config.profile,config,ids:new Set(snapshot.events.map(e=>snapshot.event_links?.[e.id]||e.id))});
 $('baseline-all-configs').checked=false;visibleEventTypes=new Set([...config.event_types,...(annotations?.items||[]).filter(e=>scanViews.get(fileId).ids.has(e.id)).map(e=>e.type)]);$('event-filter').value='active';
 setResultGroup(resultGroupForProfile(config.profile));renderDisplayChoices();refreshEventVisibility();

}
function baselineMethodLabel(method){return {detrended_abs:'去背景绝对值均值',centered_rms:'窗口去均值RMS',absolute_mean:'绝对值均值÷2'}[method]||method}
function prepareBaselineProfile(profile){
 if(!isBaseline(profile))return;
 activeBaselineProfile=profile;
 const optimized=isOptimized(profile);
 for(const option of $('baseline-method').options||[])if(option.value==='detrended_abs')option.hidden=option.disabled=!optimized;
 if(profile==='dynamic_baseline_v2'&&!optimizedInitialized){$('baseline-method').value='detrended_abs';$('baseline-window').value='5';optimizedInitialized=true}
 if(profile==='dynamic_baseline_v3'&&!v3Initialized){$('baseline-method').value='detrended_abs';$('baseline-window').value='8';v3Initialized=true}
 if(!optimized&&$('baseline-method').value==='detrended_abs')$('baseline-method').value='centered_rms';
}
function baselineArgs(){return {dynamic_method:$('baseline-method').value||'centered_rms',window_seconds:Number($('baseline-window').value)||2}}
function amplitudeSource(){return $('amplitude-source').value==='csv_flow'?'csv_flow':'pressure_q'}
const multiNames=['多特征幅度A（5秒鲁棒）','多特征参考B','多特征近消失门限','多特征减弱门限','多特征剩余比例','多特征下降百分比','多特征噪声代理（未标定）'];
function dynamicNames(window){return ['强度','参考','25%门限','50%门限','剩余比例'].map(s=>'动态'+window+'秒'+s)}
const allDynamicNames=[...dynamicNames(2),...dynamicNames(5),...dynamicNames(8)];
function optimizedNames(window,profile='dynamic_baseline_v2'){return ['强度','参考','25%门限','50%门限','剩余比例','背景估计','1秒复核强度','1秒复核参考'].map(n=>(profile==='dynamic_baseline_v3'?'优化v3':'优化')+window+'秒'+n)}
const allOptimizedNames=['dynamic_baseline_v2','dynamic_baseline_v3'].flatMap(p=>(p==='dynamic_baseline_v3'?Array.from({length:10},(_,i)=>i+1):[2,5,8]).flatMap(w=>optimizedNames(w,p)));
function dynamicMethod(){return baselineArgs().dynamic_method}
const defaultPlotGroups=[['气压1','气压2','气压1−气压2（差压）','控制压力','治疗压'],['流量'],['漏气量']];
const analysisDerivedNames=[...allDynamicNames,...allOptimizedNames,...multiNames,'测试幅度A（5秒鲁棒）','公司公式Q（试验）','公司A（5秒极差）','流量波动幅度A（5秒极差）'];
function profileChannels(){const profile=$('scan-profile').value,b=baselineArgs();return isBaseline(profile)?['流量',...(isOptimized(profile)?optimizedNames(b.window_seconds,profile).filter((n,i)=>i!==5||b.dynamic_method==='detrended_abs'):dynamicNames(b.window_seconds))]:['company_rules','adaptive_test','adaptive_multifeature'].includes(profile)?amplitudeChannels():['流量']}
function recommendedChannels(){
 const common=['气压1','气压2','气压1−气压2（差压）','控制压力','流量','漏气量','压力','治疗压'];
 const names=[...new Set([...common,...profileChannels()])].filter(n=>meta?.channels?.includes(n));
 return names.length?names:(meta?.channels||[]).slice(0,3);
}
function pruneProfileChannels(){
 if(!meta)return;
 const relevant=new Set(profileChannels()),before=selected.join('|');
 selected=selected.filter(n=>!analysisDerivedNames.includes(n)||relevant.has(n));
 groups=groups.map(g=>g.filter(n=>selected.includes(n))).filter(g=>g.length);reconcileGroups();
 $('channels').querySelectorAll('input').forEach(c=>c.checked=selected.includes(c.dataset.name));filterChannels();
 if(before!==selected.join('|')){cache=null;data={};render();loadWave(true)}
}
function isAdaptive(profile){return ['adaptive_test','adaptive_multifeature'].includes(profile)}
function multiPlotArgs(){return {test_duration:Number($('scan-duration').value)||7,test_ratio:Number($('scan-ratio').value)/100||.5}}
function amplitudeChannels(){if($('scan-profile').value==='adaptive_multifeature')return ['流量',...multiNames];if($('scan-profile').value==='adaptive_test')return ['流量','测试幅度A（5秒鲁棒）'];return amplitudeSource()==='csv_flow'?['流量','流量波动幅度A（5秒极差）']:['公司公式Q（试验）','公司A（5秒极差）']}
function testEventVersion(e){return e.detection_version||(e.note||'').match(/adaptive-flow-test-v[\w-]+/)?.[0]||'unknown'}
function matchesResultGroup(e,group){const version=$('adaptive-version').value,b=baselineArgs(),profile=$('scan-profile').value,origin=eventProfileForView(e);return eventResultGroup(e)===group&&matchesScanView(e,group)&&(group!=='automatic'||!['engineering','device_candidates'].includes(profile)||origin===profile)&&(group!=='company'||(e.detection_input||'pressure_q')===amplitudeSource())&&(group!=='adaptive'||!isAdaptive(profile)||origin===profile)&&(group!=='adaptive'||!version||version==='all'||testEventVersion(e)===version)&&(group!=='baseline'||origin===activeBaselineProfile&&($('baseline-all-configs').checked||e.detection_method===b.dynamic_method&&e.detection_window_seconds===b.window_seconds&&(origin!=='dynamic_baseline_v3'||eventDuration(e)===Number($('scan-duration').value))))}
let activeResultGroup='automatic';
const resultVisibility=new Map();
function eventResultGroup(e){if(e.source&&e.source!=='detector')return 'manual';if(isBaseline(e.detection_profile))return 'baseline';if(isAdaptive(e.detection_profile))return 'adaptive';return e.detection_profile==='company_rules'||(!e.detection_profile&&(e.type.startsWith('公司')||(e.note||'').includes('公司规则试验')))?'company':'automatic'}
function displayTypesForCurrentGroup(){const base=activeResultGroup==='baseline'?detectionTypes.dynamic_baseline:activeResultGroup==='adaptive'?detectionTypes.adaptive_test:activeResultGroup==='company'?detectionTypes.company_rules:activeResultGroup==='automatic'?[...detectionTypes.engineering,...detectionTypes.device_candidates]:Object.keys(eventColors);return [...new Set([...base,...(annotations?.items||[]).filter(e=>matchesResultGroup(e,activeResultGroup)).map(e=>e.type)])]}
function setResultGroup(group){if(!['automatic','company','manual','adaptive','baseline'].includes(group))return;$('device-metrics').hidden=group!=='automatic';$('adaptive-version-label').hidden=group!=='adaptive';$('baseline-history-label').hidden=group!=='baseline';if(group!==activeResultGroup){resultVisibility.set(activeResultGroup,{types:[...visibleEventTypes],status:$('event-filter').value||'active'});activeResultGroup=group;const saved=resultVisibility.get(group);visibleEventTypes=new Set(saved?.types||Object.keys(eventColors));$('event-filter').value=saved?.status||'active'}renderDisplayChoices();refreshEventVisibility()}
const $=id=>document.getElementById(id);
function displayText(value){return String(value).replaceAll('公司公式Q','压力估算Q').replaceAll('公司A','波动幅度A').replaceAll('公司判定同源A','检测用A').replaceAll('公司规则','幅度规则').replaceAll('公司暂停','幅度规则暂停').replaceAll('公司低通气','幅度规则低通气').replaceAll('公司','幅度')}
function channelLabel(name){const unit=meta?.channel_units?.[name],role=meta?.channel_roles?.[name];return displayText(name+(!multiNames.includes(name)&&!allDynamicNames.includes(name)&&!allOptimizedNames.includes(name)&&role&&role!==name?' · '+role:'')+(unit?' ['+unit+']':''))}
const colors=['#087e8b','#4263c7','#b47924','#9871b5','#d04b59','#538c36','#b65191','#397eaa'];
function channelColor(name){const reference={'气压1':'#4472c4','气压2':'#ed7d31','气压1−气压2（差压）':'#a5a5a5','控制压力':'#edaf00','治疗压':'#8752a3'};return reference[name]||colors[Math.max(0,meta.channels.indexOf(name))%colors.length]}
function selectionKey(){return selected.join('|')+(displayMode==='raw'?'':'|engineering')+(selected.some(n=>multiNames.includes(n))?'|'+JSON.stringify(multiPlotArgs()):'')+(selected.some(n=>allDynamicNames.includes(n)||allOptimizedNames.includes(n))?'|dynamic:'+dynamicMethod():'')+(selected.some(n=>n.startsWith('优化v3'))?'|duration:'+Number($('scan-duration').value):'')}
function filterChannels(){const query=$('channel-search').value.trim().toLowerCase(),only=$('selected-only').checked,all=$('show-all-channels').checked,scope=new Set([...recommendedChannels(),...selected]);let shown=0;for(const label of $('channels').children){const checkbox=label.querySelector('input'),name=checkbox.dataset.name;label.hidden=(!all&&!query&&!scope.has(name))||![name,displayText(name)].some(n=>n.toLowerCase().includes(query))||(only&&!checkbox.checked);if(!label.hidden)shown++}$('channel-count').textContent='已选 '+selected.length+' · 显示 '+shown+' 项';$('channel-empty').hidden=shown>0}
function channelSelectionChanged(){selected=[...$('channels').querySelectorAll('input:checked')].map(x=>x.dataset.name);filterChannels();cache=null;data={};render();loadWave(true)}
function reconcileGroups(){const seen=new Set();groups=groups.map(g=>g.filter(n=>selected.includes(n)&&!seen.has(n)&&(seen.add(n),true))).filter(g=>g.length);for(const name of selected)if(!seen.has(name)){groups.push([name]);seen.add(name)}}
function moveChannel(name,target){if(!selected.includes(name))return;reconcileGroups();const source=groups.find(g=>g.includes(name)),destination=target===null?null:groups.find(g=>g.includes(target));if(!source||source===destination||target!==null&&!destination)return;groups=groups.map(g=>g.filter(n=>n!==name)).filter(g=>g.length);if(destination){groups.find(g=>g.includes(target)).push(name)}else groups.push([name]);hover=null;render();$('status').textContent=destination?'已叠加：共用时间轴和左侧数值轴，可拖动标题继续组合':'已拆为独立图'}
async function api(url,options){const r=await fetch(url,options),d=await r.json();if(!r.ok)throw Error(d.error||r.statusText);return d}
function error(e){$('error').textContent=e.message||String(e)}
function clock(t){let s=((Math.floor(baseSecond+t)%86400)+86400)%86400;const text=[Math.floor(s/3600),Math.floor(s/60)%60,s%60].map(x=>String(x).padStart(2,'0')).join(':');return view[1]-view[0]<=10?text+'.'+String(Math.floor(((t%1)+1)%1*1000)).padStart(3,'0'):text}
function fit(c){const d=devicePixelRatio||1,w=c.clientWidth,h=c.clientHeight;if(c.width!==Math.round(w*d)||c.height!==Math.round(h*d)){c.width=Math.round(w*d);c.height=Math.round(h*d)}const ctx=c.getContext('2d');ctx.setTransform(d,0,0,d,0,0);ctx.clearRect(0,0,w,h);return [ctx,w,h]}
function bound(a,b){const total=Math.max(meta.duration,1),width=Math.min(total,Math.max(1,b-a));a=Math.max(0,Math.min(a,total-width));return [a,a+width]}
function scheduleRender(){if(!raf)raf=requestAnimationFrame(()=>{raf=0;render();drawOverview()})}
function setView(a,b,delay=120){if(!meta)return;view=bound(a,b);hover=null;markSelection=null;$('range').textContent=clock(view[0])+' — '+clock(view[1])+' · '+(view[1]-view[0]).toFixed(1)+' 秒';scheduleRender();clearTimeout(timer);if(!drag)timer=setTimeout(loadWave,delay)}
function cacheCovers(){return cache&&cache.key===selectionKey()&&cache.start<=view[0]&&cache.end>=view[1]&&cache.width<=3*(view[1]-view[0])&&cache.pixels>=$('plots').clientWidth*.8}
async function loadWave(force=false){
 if(!meta)return;
 if(!force&&cacheCovers()){ $('status').textContent='缓存浏览 · '+selected.length+' 个通道';return }
 const token=++serial,gen=generation,key=selectionKey(),names=[...selected],width=view[1]-view[0];
 const start=Math.max(0,view[0]-width),end=Math.min(Math.max(1,meta.duration),view[1]+width),pixels=Math.max(300,Math.floor($('plots').clientWidth));
 controller?.abort();controller=new AbortController();$('status').textContent='正在补充精细波形…';
 try{
  const q=new URLSearchParams({start,end,mode:displayMode,...multiPlotArgs(),dynamic_method:dynamicMethod()==='detrended_abs'?'centered_rms':dynamicMethod(),optimized_method:dynamicMethod(),optimized_duration:Number($('scan-duration').value)||10,pixels:Math.min(9000,Math.ceil(pixels*(end-start)/width))});names.forEach(n=>q.append('channel',n));
  // An empty channel list must not fall back to the server's default channels.
  if(!names.length){data={};cache=null;render();$('status').textContent='请选择通道';return}
  const d=await api('/api/wave?'+q,{signal:controller.signal});
  if(token!==serial||gen!==generation||key!==selectionKey())return;
  data=d.series;cache={start,end,key,width,pixels};scheduleRender();
  $('status').textContent='已缓存当前窗口及相邻区域 · 拖动即时预览 · '+selected.length+' 个通道';
 }catch(e){if(e.name!=='AbortError'){error(e);$('status').textContent='读取失败'}}
}
function lowerBound(points,t){let a=0,b=points.length;while(a<b){const m=(a+b)>>>1;if(points[m][0]<t)a=m+1;else b=m}return a}
function visibleIndices(points,range){return [Math.max(0,lowerBound(points,range[0])-1),Math.min(points.length,lowerBound(points,range[1])+2)]}
function paint(canvas,points,range,color,compact=false,layers=null){
 const [ctx,w,h]=fit(canvas),L=compact?12:68,R=16,T=12,B=compact?15:28,pw=w-L-R,ph=h-T-B;
 const series=layers||[{points,color}];const showA=!compact&&series.some(layer=>['公司A（5秒极差）','流量波动幅度A（5秒极差）'].includes(layer.name));let min=Infinity,max=-Infinity,count=0;
 for(const layer of series){const [lo,hi]=visibleIndices(layer.points,range);for(let i=lo;i<hi;i++){const p=layer.points[i];if(p[1]!==null&&p[0]>=range[0]&&p[0]<=range[1]){min=Math.min(min,p[1]);max=Math.max(max,p[1]);count++}}}
 if(!count){min=0;max=1}if(showA){min=Math.min(min,0);max=Math.max(max,17)}if(max===min){min-=1;max+=1}
 // Freeze vertical scale during a pan so horizontal motion remains visually stable.
 if(drag&&!compact){const limits=drag.limits.get(canvas);if(limits){[min,max]=limits}else drag.limits.set(canvas,[min,max])}
 const pad=(max-min)*.08;min-=pad;max+=pad;
 const X=x=>L+(x-range[0])/(range[1]-range[0])*pw,Y=y=>T+(max-y)/(max-min)*ph;
 ctx.font='11px Microsoft YaHei';ctx.strokeStyle='#e9eef3';ctx.fillStyle='#74879a';
 if(!compact){for(let i=0;i<5;i++){const y=T+i*ph/4;ctx.beginPath();ctx.moveTo(L,y);ctx.lineTo(w-R,y);ctx.stroke();ctx.fillText((max-i*(max-min)/4).toFixed(1),5,y+4)}for(let i=0;i<6;i++){const x=L+i*pw/5;ctx.fillText(clock(range[0]+i*(range[1]-range[0])/5),Math.min(w-65,Math.max(0,x-24)),h-7)}}
 ctx.save();ctx.beginPath();ctx.rect(L,T,pw,ph);ctx.clip();
 if(showA){for(const threshold of [10,17]){ctx.strokeStyle=threshold===10?'#bc5265':'#a27824';ctx.setLineDash([5,4]);ctx.beginPath();ctx.moveTo(L,Y(threshold));ctx.lineTo(w-R,Y(threshold));ctx.stroke();ctx.fillStyle=ctx.strokeStyle;ctx.fillText('A='+threshold,L+8,Y(threshold)-4)}ctx.setLineDash([])}
 for(const [a,b] of meta.gaps){if(b<range[0]||a>range[1])continue;ctx.fillStyle='#dce2e9';ctx.fillRect(X(a),T,X(b)-X(a),ph)}
 for(const layer of series){const points=layer.points,[lo,hi]=visibleIndices(points,range);
 ctx.strokeStyle=layer.color;ctx.lineWidth=1.25;ctx.beginPath();let previous=null,gapIndex=0;
 while(gapIndex<meta.gaps.length&&meta.gaps[gapIndex][1]<range[0])gapIndex++;
 for(let i=lo;i<hi;i++){
  const p=points[i];if(p[1]===null){previous=null;continue}
  let crossed=false;
  while(gapIndex<meta.gaps.length&&meta.gaps[gapIndex][1]<=p[0]){if(previous&&meta.gaps[gapIndex][0]>=previous[0])crossed=true;gapIndex++}
  if(!previous||crossed)ctx.moveTo(X(p[0]),Y(p[1]));else ctx.lineTo(X(p[0]),Y(p[1]));previous=p;
 }
 ctx.stroke();}ctx.restore();
 if(!count&&!compact){ctx.fillStyle='#758598';ctx.fillText(drag?'此区域尚未缓存，松开后加载':'此区间没有记录',L+20,T+30)}
 return {L,pw,w,h,X};
}
function makePlots(){
 reconcileGroups();const host=$('plots'),key=JSON.stringify(groups);if(host.dataset.layout===key)return;host.dataset.layout=key;host.replaceChildren();
 groups.forEach(group=>{
  const section=document.createElement('section');section.dataset.name=group[0];section.className='wave-panel'+(group.length>1?' merged':'');
  const title=document.createElement('div');title.className='title group-title';
  const legend=document.createElement('div');legend.className='legend';
  group.forEach(name=>{
   const chip=document.createElement('div');chip.className='channel-chip';
   const handle=document.createElement('span');handle.className='channel-handle';handle.draggable=true;handle.textContent='⠿ '+channelLabel(name)+(displayMode==='engineering'&&meta.engineering_pressure_compensated&&['气压1','气压2'].includes(name)?'（已减气压3）':'');handle.style.color=channelColor(name);handle.title='拖到另一张图上叠加';
   handle.ondragstart=e=>{movingChannel=name;e.dataTransfer.setData('text/plain',name);e.dataTransfer.effectAllowed='move';document.body.classList.add('channel-dragging')};
   handle.ondragend=()=>{movingChannel=null;document.body.classList.remove('channel-dragging');document.querySelectorAll('.drop-target').forEach(el=>el.classList.remove('drop-target'))};
   chip.append(handle);
   const select=document.createElement('select');select.setAttribute('aria-label',displayText(name)+'移动到');select.title='也可从这里选择叠加目标';
   const hint=document.createElement('option');hint.value='';hint.textContent='叠加到…';select.append(hint);
   groups.filter(g=>g!==group).forEach(g=>{const option=document.createElement('option');option.value=g[0];option.textContent=displayText(g.join(' + '));select.append(option)});
   select.onchange=()=>{if(select.value)moveChannel(name,select.value)};chip.append(select);
   if(group.length>1){const split=document.createElement('button');split.textContent='拆出';split.setAttribute('aria-label','拆出'+displayText(name));split.onclick=()=>moveChannel(name,null);chip.append(split)}
   legend.append(chip);
  });
  const axis=document.createElement('span');axis.className='axis-note';axis.textContent=(group.length>1?'叠加 · 共用数值轴':'拖动通道名称可叠加')+(displayMode==='engineering'?' · 旧脚本处理值':' · 原始值');
  const units=[...new Set(group.map(n=>meta.channel_units?.[n]||'未确认'))];axis.textContent+=' · '+(units.length===1&&units[0]==='Pa'?'纵轴 Pa':'单位 '+units.join(' / '));title.append(legend,axis);const value=document.createElement('div');value.className='value group-value';value.dataset.active='false';
  for(const name of group){const slot=document.createElement('span'),text=document.createElement('span');slot.className='readout-item';slot.style.color=channelColor(name);slot.title=channelLabel(name);text.className='readout-text';slot.append(text);value.append(slot)}
  const wrap=document.createElement('div');wrap.className='wave-wrap';const canvas=document.createElement('canvas'),overlay=document.createElement('canvas');overlay.className='cursor-layer';wrap.append(canvas,overlay);section.append(title,value,wrap);host.append(section);events(overlay);
  section.ondragover=e=>{if(movingChannel&&!group.includes(movingChannel)){e.preventDefault();e.dataTransfer.dropEffect='move';section.classList.add('drop-target')}};
  section.ondragleave=e=>{if(!section.contains(e.relatedTarget))section.classList.remove('drop-target')};
  section.ondrop=e=>{e.preventDefault();section.classList.remove('drop-target');if(!movingChannel)return;const name=movingChannel;movingChannel=null;document.body.classList.remove('channel-dragging');moveChannel(name,group[0])};
 });
}
function render(){if(!meta)return;makePlots();[...$('plots').children].forEach((section,i)=>{const layers=groups[i].map(name=>({name,points:data[name]||[],color:channelColor(name)}));paint(section.querySelector('canvas'),[],view,'',false,layers)});drawCursors()}
function drawCursors(){[...$('plots').children].forEach((section,i)=>{const c=section.querySelector('.cursor-layer'),[ctx,w,h]=fit(c),el=section.querySelector('.value');drawEventBands(ctx,w,h,view,groups[i],i===0);el.dataset.active=String(hover!==null&&!drag);if(hover===null||drag)return;ctx.strokeStyle='#8095a8';ctx.setLineDash([3,3]);const x=68+(hover-view[0])/(view[1]-view[0])*(w-84);ctx.beginPath();ctx.moveTo(x,12);ctx.lineTo(x,h-28);ctx.stroke();for(const [j,name] of groups[i].entries()){const slot=el.children[j],text=slot?.firstElementChild;if(!text)continue;const pts=data[name]||[];let index=lowerBound(pts,hover);if(index>=pts.length)index=pts.length-1;if(index>0&&Math.abs(pts[index-1][0]-hover)<Math.abs(pts[index][0]-hover))index--;const p=pts[index];if(p&&p[0]>=view[0]&&p[0]<=view[1]){const suffix=' '+(p[1]??'缺失')+' · '+clock(p[0])+' · #'+(p[2]+1).toLocaleString(),unit=meta.channel_units?.[name];text.textContent=displayText(name)+(unit?' ['+unit+']':'')+suffix;slot.title=channelLabel(name)+suffix}else{text.textContent=displayText(name)+' —';slot.title=channelLabel(name)+'：当前范围无显示点'}}})}
let cursorRAF=0;
function scheduleCursor(){if(!cursorRAF)cursorRAF=requestAnimationFrame(()=>{cursorRAF=0;drawCursors()})}
function pointer(c,e,range=view){const r=c.getBoundingClientRect(),fraction=Math.max(0,Math.min(1,(e.clientX-r.left-68)/(r.width-84)));return range[0]+fraction*(range[1]-range[0])}
function finishDrag(){if(!drag)return;drag=null;scheduleRender();clearTimeout(timer);loadWave()}
function events(c){
 c.addEventListener('wheel',e=>{e.preventDefault();if(!meta)return;if(e.ctrlKey){const t=pointer(c,e),factor=e.deltaY>0?1.2:1/1.2;setView(t-(t-view[0])*factor,t+(view[1]-t)*factor)}else{const shift=(e.deltaY+e.deltaX)/600*(view[1]-view[0]);setView(view[0]+shift,view[1]+shift)}},{passive:false});
 c.oncontextmenu=e=>e.preventDefault();
 c.onpointerdown=e=>{if(e.button!==0&&e.button!==2)return;if(marking&&e.button===0){if(!annotations)return error('事件存储尚不可用，请刷新页面');clearTimeout(timer);const name=c.closest('section').dataset.name;markSelection={start:pointer(c,e),end:pointer(c,e),channels:[...groups.find(g=>g.includes(name))],mode:displayMode};c.setPointerCapture(e.pointerId);scheduleCursor();return}clearTimeout(timer);controller?.abort();serial++;drag={x:e.clientX,range:[...view],limits:new Map()};hover=null;render();c.setPointerCapture(e.pointerId)};
 c.onpointermove=e=>{if(markSelection){markSelection.end=pointer(c,e);scheduleCursor()}else if(drag){const shift=(drag.x-e.clientX)/(c.clientWidth-84)*(drag.range[1]-drag.range[0]);setView(drag.range[0]+shift,drag.range[1]+shift)}else{hover=pointer(c,e);scheduleCursor()}};
 c.onpointerup=()=>{if(markSelection){const s=markSelection;markSelection=null;scheduleCursor();if(Math.abs(s.end-s.start)>.001)openEventEditor(Math.min(s.start,s.end),Math.max(s.start,s.end),s.channels);return}finishDrag()};
 c.onpointercancel=c.onlostpointercapture=()=>{markSelection=null;scheduleCursor();finishDrag()};
 c.onpointerleave=()=>{if(!drag){hover=null;scheduleCursor()}};c.ondblclick=()=>setView(0,Math.max(meta.duration,1));
}
function drawOverview(){if(!meta)return;const c=$('overview');if(!overviewImage||overviewImage.width!==Math.round(c.clientWidth*(devicePixelRatio||1))||overviewImage.height!==Math.round(c.clientHeight*(devicePixelRatio||1))){paint(c,overview,[0,Math.max(1,meta.duration)],'#547b98',true);overviewImage=document.createElement('canvas');overviewImage.width=c.width;overviewImage.height=c.height;overviewImage.getContext('2d').drawImage(c,0,0)}const [ctx,w,h]=fit(c);ctx.drawImage(overviewImage,0,0,w,h);const X=t=>12+t/Math.max(1,meta.duration)*(w-28),a=X(view[0]),b=X(view[1]);ctx.fillStyle='#167c8928';ctx.fillRect(a,0,b-a,h);ctx.strokeStyle='#167c89';ctx.strokeRect(a,1,Math.max(2,b-a),h-2);drawExternalEvents(ctx,X,[0,meta.duration],2,h-18,false)}
function showModeInfo(){
 meta.duration=displayMode==='engineering'?meta.display_duration:meta.raw_duration;
 meta.gaps=displayMode==='engineering'?meta.engineering_gaps:meta.raw_gaps;
 document.body.dataset.mode=displayMode;
 $('display-mode').value=displayMode;
 $('mode-note').textContent=displayMode==='engineering'?'旧脚本显示模式：'+(meta.engineering_pressure_compensated?'气压1、气压2均已减去气压3；':'当前文件无可用的气压1/2/3补偿组合；')+'重复时间戳按旧脚本算法估算展开，仅用于显示，不代表已确认的采样时间。断线阈值 '+meta.engineering_gap_threshold+' 秒（沿用旧脚本）；第14列 '+(meta.engineering_byte_column||'未应用字节转换')+' 沿用大于125减256规则。':'原始模式：保留原始压力和秒级时间戳，同一秒内可能呈竖线。相邻时间差超过1秒显示灰色间隔，不转换数值单位。';
 if(meta.channel_units&&Object.keys(meta.channel_units).length)$('mode-note').textContent+=' 压力单位Pa：气压1为内部、气压2为出风口、气压3为标准气压；仅气压1/2按现有规则扣除气压3；机器总流量、瞬时总漏气率均为L/min，不相减。';
 if(meta.sampling_info)$('mode-note').textContent+=' CSV每个有记录整秒的行数中位值 '+meta.median_records_per_second+'（P10–P90：'+meta.sampling_info.record_count_p10+'–'+meta.sampling_info.record_count_p90+'），不代表独立采样率。设备反馈压力约47–48Hz、预期50Hz；计算流量/漏气约30Hz待核实，不据此强制重采样。';
 $('info').replaceChildren();[meta.name,meta.rows.toLocaleString()+' 行',meta.start+' — '+meta.end,'当前模式断线 '+meta.gaps.length+' 处'].forEach(s=>{const span=document.createElement('span');span.textContent=s;$('info').append(span)});
}
async function loadOverview(){const gen=generation,channel=meta.channels.includes('流量')?'流量':selected[0]||meta.channels[0],q=new URLSearchParams({start:0,end:Math.max(meta.duration,1),pixels:1000,channel,mode:displayMode});try{const d=await api('/api/wave?'+q);if(gen!==generation)return;overview=d.series[channel]||[];overviewImage=null;drawOverview()}catch(e){if(gen===generation)error(e)}}
function changeMode(mode){if(!meta)return;markSelection=null;displayMode=mode;generation++;serial++;controller?.abort();cache=null;data={};overview=[];overviewImage=null;hover=null;drag=null;$('plots').dataset.layout='';showModeInfo();setView(...view,0);loadOverview();renderEventList()}
async function init(m){
 $('show-all-channels').checked=false;
 for(const name of annotationTypes){if(![...$('event-type').options].some(o=>o.value===name)){const o=document.createElement('option');o.value=name;o.textContent=displayText(name);$('event-type').append(o)}}
 generation++;const gen=generation;serial++;controller?.abort();clearTimeout(timer);meta=m;displayMode='engineering';cache=null;data={};overviewImage=null;hover=null;drag=null;groups=[];
 const parts=m.start.slice(11,19).split(':').map(Number);baseSecond=parts[0]*3600+parts[1]*60+parts[2];
 $('error').textContent='';if(duplicateScanResolve)finishDuplicateScan('cancel');if($('scan-history-dialog').open)$('scan-history-dialog').close();historySerial++;$('feedback-run').replaceChildren();annotations=null;restoreExternalEvents();eventDraft=null;markSelection=null;if($('event-dialog').open)$('event-dialog').close();try{if(!m.file_id)throw Error('请重启查看器后再使用事件保存');annotations=new AnnotationModel(m,localStorage);annotations.load()}catch(e){annotations=null;error(e)}renderEventList();
 $('info').replaceChildren();[m.name,m.rows.toLocaleString()+' 行',m.start+' — '+m.end,'时间间隔异常 '+m.gaps.length+' 处'].forEach(s=>{const span=document.createElement('span');span.textContent=s;$('info').append(span)});
 groups=defaultPlotGroups.map(group=>group.filter(n=>m.channels.includes(n))).filter(group=>group.length);
 if(!groups.length)groups=m.channels.slice(0,3).map(name=>[name]);
 selected=groups.flat();
 $('channels').replaceChildren();m.channels.filter(name=>!analysisDerivedNames.includes(name)).forEach(name=>{const l=document.createElement('label'),c=document.createElement('input');c.type='checkbox';c.checked=selected.includes(name);c.dataset.name=name;c.onchange=channelSelectionChanged;l.append(c,document.createTextNode(' '+channelLabel(name)));$('channels').append(l)});filterChannels();
 $('time').value=m.start.slice(11,19);overview=[];$('plots').dataset.layout='';showModeInfo();setView(0,Math.max(m.duration,1),0);loadOverview();eventSyncReady=false;serverRevision=0;refreshSharedEvents(true);loadFeedbackRuns();$('feedback-comparison').replaceChildren();$('device-metrics').replaceChildren();loadDeviceMetrics();renderDetectionChoices();renderDisplayChoices();reviewedTypeSelection.clear();renderReviewChoices();$('review-mode').value='spot';$('review-start').value=$('review-end').value='';$('package-result').textContent='';$('package-open').hidden=true;pendingPackage=null;if($('package-dialog').open)$('package-dialog').close();
 resizeSidebar();restoreFileView();if(!fileSwitching){let entry=fileLibrary.find(f=>f.file_id===m.file_id);if(!entry){entry={id:++librarySerial,name:m.name,file_id:m.file_id,file:null};fileLibrary.push(entry)}activeLibraryId=entry.id;renderFileLibrary()}
}
$('file').onchange=async e=>{const files=[...e.target.files];if(!files.length)return;if(fileSwitching||scanning||eventSaving||packageSaving){error('请等待扫描、保存或文件切换完成后再添加');e.target.value='';return}const added=[];for(const file of files){let entry=fileLibrary.find(f=>f.file&&f.name===file.name&&f.file.size===file.size&&f.file.lastModified===file.lastModified);if(!entry){entry={id:++librarySerial,name:file.name,file,file_id:null};fileLibrary.push(entry)}else entry.file=file;added.push(entry)}renderFileLibrary();try{await switchLibraryFile(added[0].id)}finally{e.target.value=''}};
function rememberFileView(){if(!meta)return;try{localStorage.setItem('respiratory-view-v1:'+meta.file_id,JSON.stringify({view,selected,groups,displayMode,channelScopeVersion:2,showAllChannels:$('show-all-channels').checked,window:$('window').value,time:$('time').value}))}catch(e){/* View preference storage must not block annotation or switching. */}}
function restoreFileView(){if(!meta)return;try{const saved=JSON.parse(localStorage.getItem('respiratory-view-v1:'+meta.file_id)||'null');if(!saved||!Array.isArray(saved.view)||saved.view.length!==2||!saved.view.every(Number.isFinite)||saved.view[1]<=saved.view[0])return;$('show-all-channels').checked=saved.channelScopeVersion===2&&saved.showAllChannels===true;const fallback=[...selected];if(saved.channelScopeVersion===2&&Array.isArray(saved.selected))selected=saved.selected.filter(n=>meta.channels.includes(n));selected=selected.filter(n=>!analysisDerivedNames.includes(n));if(saved.selected?.length&&!selected.length)selected=fallback;if(saved.channelScopeVersion===2&&Array.isArray(saved.groups))groups=saved.groups.filter(Array.isArray);reconcileGroups();displayMode=['raw','engineering'].includes(saved.displayMode)?saved.displayMode:displayMode;showModeInfo();$('channels').querySelectorAll('input').forEach(c=>c.checked=selected.includes(c.dataset.name));filterChannels();if(saved.window)$('window').value=saved.window;if(saved.time)$('time').value=saved.time;generation++;serial++;controller?.abort();cache=null;data={};overviewImage=null;$('plots').dataset.layout='';setView(...saved.view,0);loadOverview()}catch(e){/* Invalid preferences fall back to the normal initial view. */}}
function renderFileLibrary(){const host=$('file-list');host.replaceChildren();const query=$('file-search').value.trim().toLowerCase();for(const entry of fileLibrary){if(!entry.name.toLowerCase().includes(query))continue;const row=document.createElement('div');row.className='library-row';const button=document.createElement('button');button.type='button';button.className='library-file';button.textContent=(entry.id===activeLibraryId?'● ':'')+entry.name+(entry.error?' · 打开失败':'');button.title=entry.name+(entry.file_id?'\n内容指纹：'+entry.file_id:'\n点击时读取');button.disabled=fileSwitching;button.setAttribute('aria-pressed',String(entry.id===activeLibraryId));button.onclick=()=>switchLibraryFile(entry.id);const close=document.createElement('button');close.type='button';close.className='library-close';close.textContent='×';close.title='关闭文件，不删除CSV和标记';close.setAttribute('aria-label','关闭文件 '+entry.name);close.disabled=fileSwitching;close.onclick=()=>closeLibraryFile(entry.id);row.append(button,close);host.append(row)}$('file-library-count').textContent=fileLibrary.length+' 份';}
async function closeLibraryFile(id){
 if(fileSwitching)return;
 if(scanning||eventSaving||packageSaving||$('event-dialog').open||$('package-dialog').open)return error('请先完成扫描、保存或关闭编辑窗口，再关闭文件');
 const entry=fileLibrary.find(f=>f.id===id);if(!entry)return;
 if(id===activeLibraryId){const next=fileLibrary.find(f=>f.id!==id&&!f.error)||fileLibrary.find(f=>f.id!==id);
  if(next){await switchLibraryFile(next.id);if(activeLibraryId===id)return;}
  else{fileSwitching=true;renderFileLibrary();rememberFileView();try{await api('/api/close',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file_id:meta.file_id})});clearCurrentFileView()}catch(e){error(e);return}finally{fileSwitching=false}}
 }
 if(entry.file_id)try{await api('/api/cache/release',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file_id:entry.file_id})})}catch(e){/* A failed cache release must not remove saved annotations. */}
 entry.file=null;fileLibrary=fileLibrary.filter(f=>f.id!==id);renderFileLibrary();$('status').textContent=meta?'已关闭 '+entry.name+'；原始CSV和标记保留。':'所有文件已关闭，可重新添加CSV；标记和查看位置保留。';
}
function clearCurrentFileView(){generation++;serial++;feedbackSerial++;historySerial++;controller?.abort();clearTimeout(timer);meta=null;restoreExternalEvents();annotations=null;activeLibraryId=null;eventSyncReady=false;serverRevision=0;selected=[];groups=[];data={};overview=[];cache=null;overviewImage=null;hover=null;drag=null;markSelection=null;eventDraft=null;pendingPackage=null;lastPackageFileId=null;
 for(const id of ['plots','channels','feedback-run','feedback-comparison','device-metrics','scan-result','package-result'])$(id).replaceChildren();
 $('plots').dataset.layout='';$('info').textContent='尚未加载文件，请添加CSV';$('mode-note').textContent='请选择一份或多份CSV';$('channel-count').textContent='已选 0 / 0';$('range').textContent='';$('error').textContent='';$('package-open').hidden=true;if($('scan-history-dialog').open)$('scan-history-dialog').close();const canvas=$('overview');canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height);renderEventList();
}

async function switchLibraryFile(id){
 const entry=fileLibrary.find(f=>f.id===id);if(!entry||fileSwitching)return;
 if(scanning||eventSaving||packageSaving||$('event-dialog').open||$('package-dialog').open)return error('请先完成扫描、保存或关闭当前标记/导出窗口，再切换文件');
 if(id===activeLibraryId&&entry.file_id===meta?.file_id)return;
 fileSwitching=true;renderFileLibrary();rememberFileView();$('status').textContent='正在读取 '+entry.name+' …';
 try{const active=fileLibrary.find(f=>f.id===activeLibraryId);if(active&&!active.file&&meta?.file_id===active.file_id){const response=await fetch('/api/current-csv?file_id='+encodeURIComponent(active.file_id));if(!response.ok)throw Error('原文件已切换，请刷新后重新选择');active.file=new File([await response.blob()],active.name,{type:'text/csv'})}
 serial++;controller?.abort();clearTimeout(timer);let m,cacheHit=false;
 if(entry.file_id){const cached=await api('/api/activate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file_id:entry.file_id})});if(cached.hit){m=cached.meta;cacheHit=true}}
 if(!m){if(!entry.file)throw Error('此文件不在缓存中，请用“添加CSV”重新选择');$('status').textContent='首次读取或缓存已释放，正在解析 '+entry.name+' …';m=await api('/api/open',{method:'POST',headers:{'X-Filename':encodeURIComponent(entry.file.name)},body:entry.file})}
 const duplicate=fileLibrary.find(f=>f.id!==entry.id&&f.file_id===m.file_id);if(duplicate){duplicate.file=entry.file;duplicate.name=entry.name;fileLibrary=fileLibrary.filter(f=>f.id!==entry.id);activeLibraryId=duplicate.id}else{entry.file_id=m.file_id;entry.error=null;activeLibraryId=entry.id}
 await init(m);$('status').textContent=(cacheHit?'已从缓存快速切换到 ':'已读取 ')+m.name+'，已恢复该文件的标记与查看设置。';
 }catch(e){entry.error=e.message;error(e);$('status').textContent='文件切换失败，原有标记未删除';if(meta)loadWave(true)}finally{fileSwitching=false;renderFileLibrary()}
}
$('file-search').oninput=renderFileLibrary;
window.addEventListener('beforeunload',rememberFileView);
$('all').onclick=()=>meta&&setView(0,Math.max(meta.duration,1));
$('channel-search').oninput=filterChannels;
$('selected-only').onchange=filterChannels;
$('show-all-channels').onchange=filterChannels;
$('select-visible').onclick=()=>{if(!meta)return;for(const label of $('channels').children)if(!label.hidden)label.querySelector('input').checked=true;channelSelectionChanged()};
$('clear-channels').onclick=()=>{if(!meta)return;$('channels').querySelectorAll('input').forEach(c=>c.checked=false);channelSelectionChanged()};
$('display-mode').onchange=e=>changeMode(e.target.value);
$('reference-view').onclick=()=>{if(!meta)return;selected=['气压1','气压2','气压1−气压2（差压）','控制压力'].filter(n=>meta.channels.includes(n));if(!selected.length){error('当前文件没有这些压力通道');return}groups=[[...selected]];$('channels').querySelectorAll('input').forEach(c=>c.checked=selected.includes(c.dataset.name));filterChannels();$('window').value=5;changeMode('engineering');$('go').click()};
$('merge-all').onclick=()=>{if(!selected.length)return;groups=[[...selected]];hover=null;render()};
$('split-all').onclick=()=>{groups=selected.map(n=>[n]);hover=null;render()};
$('in').onclick=()=>{const c=(view[0]+view[1])/2,w=(view[1]-view[0])/4;setView(c-w,c+w)};
$('out').onclick=()=>{const c=(view[0]+view[1])/2,w=view[1]-view[0];setView(c-w,c+w)};
for(const [id,dir] of [['prev',-1],['next',1]])$(id).onclick=()=>{const d=(view[1]-view[0])*.8*dir;setView(view[0]+d,view[1]+d)};
$('go').onclick=()=>{if(!meta)return;const width=Number($('window').value);if(!Number.isFinite(width)||width<1)return error('窗口长度应为至少 1 秒');const base=new Date(meta.start.replace(' ','T')),parts=$('time').value.split(':').map(Number);if(parts.length<2)return error('请输入定位时间');const target=new Date(base);target.setHours(parts[0],parts[1],parts[2]||0,0);if(target<base)target.setDate(target.getDate()+1);const t=(target-base)/1000;setView(t-width/2,t+width/2)};
function overviewTime(e){const r=$('overview').getBoundingClientRect();return Math.max(0,Math.min(meta.duration,(e.clientX-r.left-12)/(r.width-28)*meta.duration))}
$('overview').onpointerdown=e=>{if(!meta)return;overviewDrag=overviewTime(e);$('overview').setPointerCapture(e.pointerId)};
$('overview').onpointerup=e=>{if(overviewDrag===null)return;const t=overviewTime(e),a=overviewDrag;overviewDrag=null;if(Math.abs(t-a)<1){const w=view[1]-view[0];setView(t-w/2,t+w/2)}else setView(Math.min(a,t),Math.max(a,t))};
$('overview').onpointercancel=()=>overviewDrag=null;
window.addEventListener('keydown',e=>{if(!meta||/INPUT|TEXTAREA|SELECT/.test(e.target.tagName))return;if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();$(e.key==='ArrowLeft'?'prev':'next').click()}else if(e.key==='Home'||e.key.toLowerCase()==='r'){$('all').click()}});
window.addEventListener('resize',()=>{overviewImage=null;scheduleRender();clearTimeout(timer);timer=setTimeout(()=>loadWave(true),150)});

function eventClock(t){const d=new Date(Date.parse(meta.start.replace(' ','T'))+Math.round(t*1000));return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')+' '+[d.getHours(),d.getMinutes(),d.getSeconds()].map(n=>String(n).padStart(2,'0')).join(':')+'.'+String(d.getMilliseconds()).padStart(3,'0')}
const externalKey=id=>'respiratory-external-events-v1:'+id;
let externalEvents=[];
function externalTime(value){
 if(typeof value!=='string'||!/^\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:\.\d{1,3})?$/.test(value))return NaN;
 const t=Date.parse(value.replace(' ','T')+'Z');
 return Number.isFinite(t)&&new Date(t).toISOString().slice(0,19)===value.replace(' ','T').slice(0,19)?t:NaN;
}
function parseExternalEvents(packet,recording){
 const rows=packet?.data?.records,origin=externalTime(recording.start);
 if(!Array.isArray(rows)||!Number.isFinite(origin))throw Error('需要包含 data.records 的设备事件 JSON，以及有效的 CSV 起始时间。');
 const events=new Map();let outside=0,unresolved=0;
 for(const row of rows){
  if(!row||Number(row.dataType)!==2)continue;
  for(const [key,label] of [['apnea','呼吸暂停'],['ca','中枢性暂停'],['hypopnea','低通气']]){
   const count=Number(row[key+'Count']);
   if(!(count>0))continue;
   const duration=Number(row[key+'Duration']),start=(externalTime(row.dateDate)-origin)/1000;
   if(count!==1||!Number.isFinite(start)||!(duration>0&&Number.isFinite(duration))){unresolved++;continue}
   if(start<0||start>recording.raw_duration){outside++;continue}
   const keyId=JSON.stringify([label,start,duration]);events.set(keyId,{label,start,duration,stamp:row.dateDate});
  }
 }
 return {events:[...events.values()].sort((a,b)=>a.start-b.start),outside,unresolved};
}
function renderExternalEvents(message=''){
 const list=$('external-jump');
 const option=(label,value)=>{const o=document.createElement('option');o.textContent=label;o.value=value;return o};
 list.replaceChildren(option('跳转到外部事件',''));
 externalEvents.forEach((e,i)=>list.append(option(e.stamp+' · '+e.label+' · '+e.duration+' 秒',String(i))));
 list.hidden=$('external-clear').hidden=!externalEvents.length;
 $('external-status').textContent=message||(externalEvents.length?'外部事件 '+externalEvents.length+' 条 · 竖线为设备报告时间，列表保留报告时长':'外部事件独立于自动检测');
}
function restoreExternalEvents(){
 externalEvents=[];
 if(meta)try{const saved=JSON.parse(localStorage.getItem(externalKey(meta.file_id))||'[]');if(Array.isArray(saved))externalEvents=saved}catch(e){}
 renderExternalEvents();
}
$('external-file').onchange=async e=>{
 const file=e.target.files?.[0],recording=meta;e.target.value='';
 if(!file)return;
 if(!recording)return renderExternalEvents('请先打开对应的 CSV。');
 try{
  const packet=JSON.parse((await file.text()).replace(/^﻿/,''));
  if(meta!==recording||fileSwitching)return;
  const {events,outside,unresolved}=parseExternalEvents(packet,recording);
  const details=(outside?'；'+outside+' 条在当前 CSV 时间范围外':'')+(unresolved?'；'+unresolved+' 条无法定位为单次事件':'');
  if(!events.length)return renderExternalEvents('未导入任何事件'+details+'。请核对 CSV 对应的录制日期和时间。');
  externalEvents=events;let saved=true;
  try{localStorage.setItem(externalKey(recording.file_id),JSON.stringify(events))}catch(e){saved=false}
  renderExternalEvents('已导入 '+events.length+' 条'+details+'。紫色竖线为报告时间'+(saved?'。':'；本次显示，刷新后需重新导入。'));scheduleRender();
 }catch(err){renderExternalEvents('导入失败：'+err.message)}
};
$('external-show').onchange=()=>scheduleRender();
$('external-clear').onclick=()=>{if(!meta)return;try{localStorage.removeItem(externalKey(meta.file_id))}catch(e){return renderExternalEvents('无法清除本机保存的外部事件。')}externalEvents=[];renderExternalEvents();scheduleRender()};
$('external-jump').onchange=e=>{if(e.target.value==='')return;const item=externalEvents[Number(e.target.value)];if(item)setView(item.start-15,item.start+15,0);e.target.value=''};
function drawExternalEvents(ctx,X,range,top,bottom,labels){
 if(!$('external-show').checked)return;
 ctx.save();ctx.strokeStyle=ctx.fillStyle='#7c3aed';ctx.lineWidth=1.5;ctx.setLineDash([5,3]);ctx.font='11px Microsoft YaHei';
 let labelEnd=-Infinity;
 for(const e of externalEvents){
  if(e.start<range[0]||e.start>range[1])continue;
  const x=X(e.start);ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,bottom);ctx.stroke();
  if(labels&&x>labelEnd){const label='外部 · '+e.label+' '+e.duration+'s';ctx.fillText(label,x+3,top+11);labelEnd=x+8+ctx.measureText(label).width}
 }
 ctx.restore();
}
const firstPlotEvents=$('first-plot-events');
try{firstPlotEvents.checked=localStorage.getItem('respiratory-first-plot-events')!=='false'}catch(e){firstPlotEvents.checked=true}
firstPlotEvents.onchange=()=>{try{localStorage.setItem('respiratory-first-plot-events',String(firstPlotEvents.checked))}catch(e){}scheduleCursor()};
function drawEventBands(ctx,w,h,range,channels,firstPlot=false){
 const left=68,right=84,X=t=>left+(t-range[0])/(range[1]-range[0])*(w-right);
 ctx.save();ctx.beginPath();ctx.rect(left,0,w-right,h-28);ctx.clip();
 // Both axes share wall-clock seconds from the same file origin. Project intervals
 // without changing their stored timing basis; engineering intra-second positions
 // remain estimates when shown over the raw timestamp axis.
 const events=firstPlot&&!firstPlotEvents.checked?[]:visibleEvents(annotations?.items||[]).filter(e=>e.end>=range[0]&&e.start<=range[1]&&(firstPlot||e.channels.some(c=>channels.includes(c)))&&(!isBaseline(e.detection_profile)||!channels.some(c=>allDynamicNames.includes(c)||allOptimizedNames.includes(c))||e.detection_method===dynamicMethod()));
 events.forEach((e,i)=>{const a=Math.max(left,X(e.start)),b=Math.min(w-16,X(e.end)),color=eventColors[e.type];ctx.fillStyle=color+'28';ctx.fillRect(a,12,Math.max(1,b-a),h-40);ctx.strokeStyle=color;ctx.setLineDash([]);ctx.strokeRect(a,12,Math.max(1,b-a),h-40);if(b-a>45){ctx.fillStyle=color;ctx.font='11px Microsoft YaHei';ctx.fillText(displayText(e.type)+' '+(e.end-e.start).toFixed(2)+'s',a+3,24+(i%3)*13)}});
 if(firstPlot)drawExternalEvents(ctx,X,range,30,h-29,true);
 if(markSelection&&markSelection.mode===displayMode){const a=X(Math.min(markSelection.start,markSelection.end)),b=X(Math.max(markSelection.start,markSelection.end));ctx.fillStyle='#167c8938';ctx.fillRect(a,12,b-a,h-40)}ctx.restore();
}
function openEventEditor(start,end,channels,event=null){if(fileSwitching)return error('文件正在切换，请稍候再标记');if(!annotations)return error('事件保存不可用，请刷新或重启查看器');eventDraft=event?{...event}: {id:crypto.randomUUID(),mode:displayMode};$('event-dialog-title').textContent=event?'修改事件标记':'新建事件标记';$('event-start').value=start.toFixed(3);$('event-end').value=Math.min(end,eventDraft.mode==='engineering'?meta.display_duration:meta.raw_duration).toFixed(3);$('event-type').value=event?.type||'波形异常';$('event-note').value=event?.note||'';$('event-error').textContent='';$('event-axis-note').textContent=eventDraft.mode==='engineering'?'当前标记基于估算展开时间，持续时间仅供人工复核，不是已验证的采样计时。':'当前标记基于文件原始时间轴，时间戳本身可能只有秒级精度。';$('event-channels').replaceChildren();meta.channels.forEach(name=>{const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=name;input.checked=channels.includes(name);label.append(input,document.createTextNode(displayText(name)));$('event-channels').append(label)});updateEventDuration();$('event-dialog').showModal()}
function updateEventDuration(){const a=Number($('event-start').value),b=Number($('event-end').value);$('event-duration').textContent=Number.isFinite(a)&&Number.isFinite(b)?eventClock(a)+' — '+eventClock(b)+' · 持续 '+(b-a).toFixed(3)+' 秒':''}
function renderEventModeLabel(){
 const profile=$('scan-profile').value,manual=activeResultGroup==='manual';
 const labels={company_rules:'幅度规则（试验）',dynamic_baseline_v3:'动态基线优化测试（v3 · 起点复核）'};
 $('event-mode-label').textContent=manual?'人工 / Agent 标记':$('scan-profile').selectedOptions?.[0]?.textContent||labels[profile]||'历史检测结果';
 $('event-mode-config').textContent=manual?'当前文件的人工与Agent标记':isBaseline(profile)?baselineMethodLabel(baselineArgs().dynamic_method)+' · '+baselineArgs().window_seconds+'秒窗口 · 最短持续'+$('scan-duration').value+'秒':profile==='company_rules'?(amplitudeSource()==='csv_flow'?'CSV流量':'压力估算Q')+' · 5秒幅度窗口 · 连续超过'+$('scan-duration').value+'秒':'与所选历史扫描批次一致';
 $('result-current').hidden=!manual;
}
function renderEventList(){
 const list=$('event-list'),previousScroll=list.scrollTop||0;list.replaceChildren();
 renderEventModeLabel();
 const saved=[...(annotations?.items||[])],all=saved.filter(e=>matchesResultGroup(e,activeResultGroup)).sort((a,b)=>a.start-b.start),filter=$('event-filter').value||'active';
 for(const [group,id,label] of [['automatic','result-auto','自动检测'],['company','result-company','幅度规则'],['adaptive','result-adaptive','自适应测试'],['baseline','result-baseline','动态基线优化v3'],['manual','result-manual','人工 / Agent']]){const button=$(id);button.textContent=label+(group==='company'?' · '+(amplitudeSource()==='csv_flow'?'CSV流量':'压力估算Q'):'')+'（'+saved.filter(e=>matchesResultGroup(e,group)).length+'）';button.ariaPressed=String(group===activeResultGroup&&(group!=='baseline'||activeBaselineProfile==='dynamic_baseline_v3'))}
 const items=visibleEvents(all),scope=currentScanView();$('scan-view-note').textContent=scope?(primaryProfiles.includes(scope.profile)?'仅显示所选扫描批次，其他批次保留在历史中。':'正在查看已停用算法的历史批次；选择幅度规则或v3可继续扫描。'):'当前算法历史；扫描完成后只显示本次结果。';
 $('event-count').textContent='显示 '+items.length+' / 共 '+all.length+' 条';
 $('event-summary').textContent=annotations?(activeResultGroup==='baseline'&&$('baseline-all-configs').checked?'所有配置对照：可能含重叠候选，请勿累计为独立事件。':'')+'待复核 '+all.filter(e=>e.status==='candidate').length+' · 已确认 '+all.filter(e=>e.status==='reviewed').length+' · 已忽略 '+all.filter(e=>e.status==='ignored').length+'。点击跳转可显示对应通道与时间轴；自动结果需人工复核。':'标记存储未就绪，请刷新页面。';
 const choices=displayTypesForCurrentGroup(),checked=choices.filter(t=>visibleEventTypes.has(t)).length;$('display-type-summary').textContent=checked===choices.length?'本页全部类型':checked?'本页已选 '+checked+' 种类型':'未选择显示类型';
 if(!items.length){const text=document.createElement('p');text.textContent=visibleEventTypes.size?'暂无符合类型及状态筛选的事件。可更改显示条件。':'未选择显示类型，事件已隐藏；点击“显示全部”恢复。';list.append(text)}
 items.forEach(e=>{
  const row=document.createElement('div');row.className='event-row';const text=document.createElement('div');text.className='event-text';
  const title=document.createElement('strong');title.style.color=eventColors[e.type];title.textContent=displayText(e.type)+' · '+(e.end-e.start).toFixed(3)+' 秒 · '+({agent:'Agent',manual:'人工',detector:'自动检测'}[e.source]||e.source)+' / '+({candidate:'待复核',reviewed:'已确认',ignored:'已忽略'}[e.status]);
  const detail=document.createElement('small');detail.textContent=eventClock(e.start)+' — '+eventClock(e.end)+' · '+(e.mode==='engineering'?'估算展开时间':'原始秒级时间')+' · '+displayText(e.channels.join('、'));
  if(isBaseline(e.detection_profile))detail.textContent+=' · '+(isOptimized(e.detection_profile)?(e.detection_profile==='dynamic_baseline_v3'?'优化v3 · ':'优化v2 · '):'')+baselineMethodLabel(e.detection_method)+' / '+e.detection_window_seconds+'秒窗口'+(e.detection_profile==='dynamic_baseline_v3'?' / 持续≥'+eventDuration(e)+'秒':'');
   const note=document.createElement('small');note.textContent=displayText(e.note);
   const noteBox=document.createElement('details'),noteTitle=document.createElement('summary');noteBox.className='event-note';noteTitle.textContent='算法说明 / 备注';noteBox.append(noteTitle,note);text.append(title,detail);
   if(e.evidence?.review_flags?.length){const hints=document.createElement('small');hints.className='event-hints';hints.textContent=displayText(e.evidence.review_flags.join('；'));text.append(hints)}
   text.append(noteBox);
  if(isOptimized(e.detection_profile)&&e.evidence){const v=e.evidence,box=document.createElement('details'),summary=document.createElement('summary'),body=document.createElement('small'),range=a=>Array.isArray(a)&&a.length===2&&a.every(Number.isFinite)?a.map(eventClock).join(' — '):'未记录';summary.textContent='查看原始优化判定依据（人工修改后不重算）';body.textContent=(v.boundary_basis==='main_window_end'&&!v.onset_backtrack_seconds&&!v.recovery_trim_seconds&&!v.normal_low_phase_review?'主窗口连续达标区间 ':'估计波形区间 ')+range(v.estimated_waveform_interval)+(v.onset_backtrack_seconds||v.recovery_trim_seconds||v.normal_low_phase_review?' · 修正后持续 ':v.boundary_basis==='main_window_end'?' · 主窗口连续 ':' · 1秒复核连续 ')+(v.boundary_duration_seconds??'未知')+'秒 · 主窗口父过程达标 '+(v.main_qualified_total_seconds??'未知')+'秒，区间：'+(Array.isArray(v.main_qualified_intervals)?v.main_qualified_intervals.map(range).join('；'):'未记录')+' · 确认 '+(Number.isFinite(v.confirmed_at)?eventClock(v.confirmed_at):'未记录')+(v.extremely_low_strength?' · 连续'+(v.minimum_duration??10)+'秒≤10%极低强度标记':'')+'。'+(Array.isArray(v.review_flags)?v.review_flags.join('；'):'');if(v.threshold_boundary_review)body.textContent+=' · 较早终点 '+eventClock(v.threshold_boundary_review.alternative_end)+'（'+v.threshold_boundary_review.alternative_duration_seconds+' 秒）；原终点 '+eventClock(v.estimated_waveform_interval[1]);if(Number.isFinite(v.recovery_trim_seconds))body.textContent+=' · 原判定终点 '+eventClock(v.unadjusted_end)+'，按流量恢复缩短 '+v.recovery_trim_seconds+' 秒';if(Number.isFinite(v.recovery_cycle_seconds))body.textContent+=' · 恢复波动间隔约 '+v.recovery_cycle_seconds+' 秒，复核于 '+eventClock(v.recovery_confirmed_at);if(Number.isFinite(v.rhythm_period_seconds))body.textContent+=(v.rhythm_basis==='earlier'?' · 较早节律（仅用于起点）约 ':' · 近期节律约 ')+v.rhythm_period_seconds+' 秒/周期'+(v.rhythm_interval_count?'（'+v.rhythm_interval_count+' 个一致间隔）':'')+'，起点回看范围 '+(v.flow_tail_search_seconds??v.weak_onset_search_seconds)+' 秒';if(Number.isFinite(v.onset_backtrack_seconds))body.textContent+=' · 原主窗口起点 '+eventClock(v.unadjusted_start)+'，按流量提前 '+v.onset_backtrack_seconds+' 秒';if(v.normal_low_phase_review){const phase=v.normal_low_phase_review;body.textContent+=' · 典型低流量阶段约 '+phase.low_phase_seconds+' 秒（'+phase.low_phase_count+' 个一致样本），从 '+eventClock(phase.waveform_start)+' 后移 '+phase.delayed_seconds+' 秒再计时'}if(Number.isFinite(v.flow_tail_trim_seconds))body.textContent+=' · 原始起点 '+eventClock(v.unadjusted_start)+'，流量趋稳后开始计时（缩短 '+v.flow_tail_trim_seconds+' 秒）';if(Array.isArray(v.leak_change_context)&&v.leak_change_context.length)body.textContent+=' · 总漏气字段变化：'+v.leak_change_context.map(s=>eventClock(s.time)+' '+s.before+'→'+s.after+' L/min').join('；');box.append(summary,body);text.append(box)}
  if(['adaptive_multifeature','dynamic_baseline'].includes(e.detection_profile)&&e.evidence){const v=e.evidence,box=document.createElement('details'),summary=document.createElement('summary'),body=document.createElement('small'),num=(n,d=2)=>Number.isFinite(n)?n.toFixed(d):'未知';summary.textContent='查看原始自动判定依据（人工修改后保留，不自动重算）';body.textContent='参考B '+num(v.reference_amplitude)+' L/min · 强度/A中位 '+num(v.amplitude_median??v.strength_median)+' L/min · 剩余中位 '+num(v.remaining_ratio_median*100,1)+'% / 最大 '+num(v.remaining_ratio_max*100,1)+'% · 严格达标 '+num(v.strict_duration_seconds,0)+' 秒 · 首次确认 '+(Number.isFinite(v.confirmed_at)?eventClock(v.confirmed_at):'未知')+'。'+(Array.isArray(v.review_flags)?v.review_flags.join('；'):'');box.append(summary,body);text.append(box)}row.append(text);
  const statusAction=status=>async()=>{try{await commitShared(m=>m.save({...e,status,updated_at:new Date().toISOString()}))}catch(err){error(err)}};
  const actions=[['跳转',()=>jumpEvent(e)],['修改',()=>openEventEditor(e.start,e.end,e.channels,e)]];
  if(e.status==='candidate')actions.push(['确认',statusAction('reviewed')],['忽略',statusAction('ignored')]);
  if(e.status==='ignored')actions.push(['恢复待复核',statusAction('candidate')]);
  actions.push(['删除',async()=>{try{await commitShared(m=>m.remove(e.id))}catch(err){error(err)}}]);
   for(const [label,action] of actions){const button=document.createElement('button');button.textContent=label;button.setAttribute('aria-label',label+'事件 '+displayText(e.type)+' '+e.start.toFixed(3));button.onclick=action;row.append(button)}list.append(row);
 });
 list.scrollTop=previousScroll;
}

function jumpEvent(e){const panelScroll=$('event-list').scrollTop||0;if(isBaseline(e.detection_profile)){if(e.detection_profile==='dynamic_baseline_v2')optimizedInitialized=true;if(e.detection_profile==='dynamic_baseline_v3')v3Initialized=true;$('baseline-method').value=e.detection_method;$('baseline-window').value=String(e.detection_window_seconds);selectDetectionProfile(e.detection_profile,true);$('scan-profile').onchange();$('scan-duration').value=eventDuration(e);updateBaselineControls()}if(e.detection_profile==='adaptive_multifeature'&&e.evidence){selectDetectionProfile('adaptive_multifeature',true);$('scan-profile').onchange();$('scan-duration').value=e.evidence.minimum_duration;$('scan-ratio').value=Math.round(e.evidence.remaining_entry_ratio*100);$('adaptive-version').value=e.detection_version;}
 const margin=Math.max(1,(e.end-e.start)*.5);setView(e.start-margin,e.end+margin,0);refreshEventVisibility();$('event-list').scrollTop=panelScroll;const fileId=meta.file_id;requestAnimationFrame(()=>{if(meta?.file_id===fileId)scrollToEventWaveform(e)});$('status').textContent='保持当前显示模式；事件计时依据：'+(e.mode==='raw'?'原始秒级时间':'估算展开时间')}
function scrollToEventWaveform(e){const panels=[...$('plots').querySelectorAll('.wave-panel')],priorities=[...e.channels.filter(n=>!analysisDerivedNames.includes(n)),...e.channels];let target;for(const channel of priorities){target=panels.find(p=>groups.find(g=>g[0]===p.dataset.name)?.includes(channel));if(target)break}(target||$('plots')).scrollIntoView?.({block:'start',behavior:'smooth'})}
function downloadEvents(kind){if(!annotations)return error('没有可导出的标记记录');let content,mime;if(kind==='json'){content=JSON.stringify(annotations.export(),null,2);mime='application/json'}else{const safe=v=>'"'+String(v).replace(/^[=+@\-]/,"'$&").replace(/"/g,'""')+'"';const rows=[['id','类型','起始时间','结束时间','持续秒数','起始偏移秒','结束偏移秒','通道','时间轴','来源','备注'],...annotations.items.map(e=>[e.id,e.type,eventClock(e.start),eventClock(e.end),(e.end-e.start).toFixed(3),e.start,e.end,e.channels.join(';'),e.mode,e.source+' / '+e.status,e.note])];content='\ufeff'+rows.map(row=>row.map(safe).join(',')).join('\r\n');mime='text/csv;charset=utf-8'}const url=URL.createObjectURL(new Blob([content],{type:mime})),a=document.createElement('a');a.href=url;a.download=meta.name.replace(/\.csv$/i,'')+'.events.'+kind;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
$('mark-mode').onclick=()=>{marking=!marking;$('mark-mode').setAttribute('aria-pressed',String(marking));$('mark-mode').textContent='标记模式：'+(marking?'开启':'关闭');markSelection=null;scheduleCursor()};
$('mark-window').onclick=()=>{if(meta&&selected.length)openEventEditor(view[0],view[1],[...selected]);else error('请先选择要标记的通道')};
$('event-start').oninput=$('event-end').oninput=updateEventDuration;
$('event-cancel').onclick=()=>$('event-dialog').close();
$('event-form').onsubmit=async e=>{e.preventDefault();try{if(!$('event-start').value||!$('event-end').value)throw Error('请输入起始和结束时间');await commitShared(m=>m.save({...eventDraft,start:Number($('event-start').value),end:Number($('event-end').value),type:$('event-type').value,note:$('event-note').value,channels:[...$('event-channels').querySelectorAll('input:checked')].map(c=>c.value),updated_at:new Date().toISOString()}));if(!eventDraft.source||eventDraft.source==='manual'||eventDraft.source==='agent')setResultGroup('manual');$('event-dialog').close();renderEventList();scheduleCursor();$('status').textContent='事件已保存到本机共享存储，Agent和网页均可读取'}catch(err){$('event-error').textContent=err.message}};
$('export-events-json').onclick=()=>downloadEvents('json');$('export-events-csv').onclick=()=>downloadEvents('csv');
$('import-events').onchange=async e=>{const file=e.target.files[0];if(!file)return;try{if(!annotations)throw Error('请先打开数据文件');if(file.size>10*1024*1024)throw Error('标记JSON请小于10MB');const packet=JSON.parse(await file.text());await commitShared(m=>m.import(packet));renderEventList();scheduleCursor();$('status').textContent='事件已导入并保存到本机共享存储'}catch(err){error(err)}finally{e.target.value=''}};
window.setInterval?.(()=>{if(annotations&&eventSyncReady&&!document.hidden&&!$('event-dialog').open&&!eventSaving)refreshSharedEvents(false)},3000);
api('/api/meta').then(init).catch(()=>{$('info').textContent='请点击右上角打开 CSV';$('status').textContent='尚未加载文件'});


function applyShared(packet){
 serverRevision=packet.revision;
 annotations.items=packet.events.map(e=>annotations.validate(e));
 eventSyncReady=true;renderEventList();scheduleCursor();
}
async function refreshSharedEvents(initial=false){
 if(!annotations||eventSaving)return;
 const model=annotations,fileId=meta.file_id;
 try{
  let packet=await api('/api/events?file_id='+encodeURIComponent(fileId));
  if(annotations!==model||eventSaving)return;
  const marker=model.key+':shared-migrated';
  if(initial&&!localStorage.getItem(marker)){
   const merged=new Map(packet.events.map(e=>[e.id,model.validate(e)]));
   for(const e of model.items){
    const normalized=model.validate(e);
    if(merged.has(e.id)&&JSON.stringify(merged.get(e.id))!==JSON.stringify(normalized))throw Error('旧浏览器标记与共享记录冲突，请先导出JSON备份后手动处理');
    merged.set(e.id,normalized);
   }
   if(merged.size!==packet.events.length)packet=await api('/api/events',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file_id:fileId,revision:packet.revision,events:[...merged.values()]})});
   if(annotations!==model)return;
   localStorage.setItem(marker,'1');
  }
  if(!eventSyncReady||packet.revision!==serverRevision)applyShared(packet);
 }catch(e){error(e)}
}
async function commitShared(mutate){
 if(!eventSyncReady||!annotations)throw Error('共享存储尚未同步，请稍候或刷新页面');
 if(eventSaving)throw Error('正在保存上一个操作，请稍候');
 eventSaving=true;
 const model=annotations,fileId=meta.file_id;
 try{
  const draft=new AnnotationModel(meta,{setItem(){},getItem(){return null}});
  draft.items=model.items.map(e=>({...e,channels:[...e.channels]}));mutate(draft);
  const packet=await api('/api/events',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file_id:fileId,revision:serverRevision,events:draft.items,reviewer:$('reviewer').value})});
  if(annotations!==model)return;
  applyShared(packet);
  try{model.commit(packet.events)}catch(e){error('事件已保存到本机，浏览器备份失败：'+e.message)}
 }finally{eventSaving=false}
}


let scanning=false;
async function scanRecording(whole){
 if(fileSwitching)return error('文件正在切换，请稍候再扫描');
 if(!meta||!eventSyncReady)return error('请先打开文件并等待标记同步完成');
 if(!primaryProfiles.includes($('scan-profile').value))return error('此算法仅保留历史查看，请选择“幅度规则”或“动态基线优化v3”后扫描');
 if(scanning||eventSaving)return;
 const minDuration=Number($('scan-duration').value),ratio=Number($('scan-ratio').value)/100;
 if(isOptimized($('scan-profile').value)&&!optimizedAvailable($('scan-profile').value))return error('请重启更新后的查看器并刷新网页后使用优化测试版');
 if($('scan-profile').value==='dynamic_baseline'&&meta.dynamic_baseline_detector_version!=='dynamic-baseline-detector-v1')return error('请重启更新后的查看器并刷新网页后使用动态基线检测');
 if($('scan-profile').value==='adaptive_test'&&(meta.adaptive_test_version!=='adaptive-flow-test-v2-causal'||!meta.channels.includes('测试幅度A（5秒鲁棒）')||ratio<=.1))return error('测试算法需要更新后的后台及CSV流量，减弱比例应大于10%。');
 if($('scan-profile').value==='adaptive_multifeature'&&(meta.multifeature_test_version!=='adaptive-flow-test-v3-multifeature'||ratio<=.1))return error('多特征测试需要更新后的后台及CSV流量，请重启查看器后刷新；减弱剩余比例须大于10%。');
 if($('scan-profile').value==='company_rules'&&amplitudeSource()==='csv_flow'&&!meta.channels.includes('流量波动幅度A（5秒极差）'))return error('当前文件或后台尚未提供流量幅度A。请确认有CSV流量列，并重启更新后的查看器、重新打开CSV。');
 const eventTypes=[...chosenDetectionTypes];if(!eventTypes.length)return error('请至少勾选一种检测事件，或点击“全检测”');
 if(!Number.isFinite(minDuration)||minDuration<3||minDuration>120||!Number.isFinite(ratio)||ratio<.05||ratio>.8)return error('最短持续需为3～120秒，波动比例需为5～80%');
 const model=annotations,fileId=meta.file_id;
 scanning=true;eventSaving=true;$('scan-all').disabled=$('scan-window').disabled=true;
 $('scan-types-fieldset').disabled=true;$('scan-profile').disabled=true;
 $('baseline-method').disabled=$('baseline-window').disabled=$('baseline-duration').disabled=true;
 $('device-metrics').replaceChildren();$('scan-result').textContent='正在分析原始样本，请稍候…';
 try{
  const start=whole?0:Math.min(view[0],meta.raw_duration),end=whole?meta.raw_duration:Math.min(view[1],meta.raw_duration);
  const baseline=isBaseline($('scan-profile').value);
  const scanArgs={file_id:fileId,start,end,min_duration:minDuration,reduction_ratio:baseline?.5:$('scan-profile').value==='company_rules'?.3:ratio,profile:$('scan-profile').value,event_types:eventTypes,flow_source:baseline||isAdaptive($('scan-profile').value)?'csv_flow':amplitudeSource(),...(baseline?baselineArgs():{})};
  const existing=await api('/api/scans/match',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(scanArgs)});
  if(annotations!==model)return;
  if(existing.match){const action=await chooseDuplicateScan(existing.match);if(annotations!==model)return;if(action!=='rescan'){if(action==='reuse'){await loadFeedbackRuns(existing.match.run_id);await showScanBatch(existing.match.run_id);await loadDeviceMetrics();$('scan-result').textContent='已选择已有批次用于反馈导出；未重新扫描，未新增批次。当前仅显示此批次的事件。'}else $('scan-result').textContent='已取消扫描，未新增批次。';return}}
  const result=await api('/api/detect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(scanArgs)});
  if(annotations!==model)return;
  applyShared(result.packet);
  await loadFeedbackRuns(result.packet.run_id);
  await showScanBatch(result.packet.run_id);
  $('scan-result').textContent='扫描完成：检出 '+result.detected_count+' 条，新增 '+result.added+' 条，跳过已有重叠记录 '+result.skipped+' 条。有效参考区间 '+result.eligible_seconds+' 秒。保持当前显示模式，候选按原始秒级时间定位；点击事件列表“跳转”复核。'+displayText(result.warnings.join(' '));
  showDeviceMetrics(result.device_metrics);
  renderEventList();
  if(scanArgs.profile==='adaptive_multifeature')$('adaptive-version').value='adaptive-flow-test-v3-multifeature';
  if(baseline)$('baseline-all-configs').checked=false;
  setResultGroup(baseline?'baseline':isAdaptive(scanArgs.profile)?'adaptive':scanArgs.profile==='company_rules'?'company':'automatic');
 }catch(err){const message=err.message.includes('检测方案无效')?'网页与运行中的后台版本不一致。请关闭旧查看器运行窗口，重新双击本项目的“启动查看器.cmd”，然后刷新网页并重新打开CSV。':err.message;error(message);$('scan-result').textContent='扫描未完成：'+message}
 finally{scanning=false;eventSaving=false;$('scan-all').disabled=$('scan-window').disabled=false;$('scan-types-fieldset').disabled=false;$('scan-profile').disabled=false;$('baseline-method').disabled=$('baseline-window').disabled=$('baseline-duration').disabled=false}
}
$('scan-all').onclick=()=>scanRecording(true);$('scan-window').onclick=()=>scanRecording(false);
$('scan-profile').onchange=()=>{const profile=$('scan-profile').value,testing=isAdaptive(profile),multi=profile==='adaptive_multifeature',baseline=isBaseline(profile);prepareBaselineProfile(profile);if(primaryProfiles.includes(profile)){selectDetectionProfile(profile);activeBaselineProfile='dynamic_baseline_v3';}if(!scanning)$('scan-all').disabled=$('scan-window').disabled=!primaryProfiles.includes(profile);if(['engineering','device_candidates'].includes(profile))activeAutomaticProfile=profile;$('optimized-rule-info').hidden=profile!=='dynamic_baseline_v2';$('optimized-v3-rule-info').hidden=profile!=='dynamic_baseline_v3';$('baseline-options').hidden=!baseline;$('baseline-rule-info').hidden=profile!=='dynamic_baseline';$('show-company-a').textContent=baseline?'显示强度与基线':'显示输入与A';$('scan-duration').disabled=baseline;$('test-rule-info').hidden=profile!=='adaptive_test';$('multi-rule-info').hidden=!multi;const company=profile==='company_rules';$('scan-ratio').value=testing||baseline?50:profile==='device_candidates'?70:30;$('scan-ratio-label').hidden=company||baseline;$('amplitude-source-label').hidden=!company;$('company-rule-info').hidden=!company;$('relative-rule-info').hidden=company||testing||baseline;$('scan-duration').value=company||testing?7:10;updateBaselineControls();$('device-metrics').replaceChildren();chosenDetectionTypes=new Set(detectionTypes[profile]);renderDetectionChoices();if(testing)$('adaptive-version').value=multi?'adaptive-flow-test-v3-multifeature':'adaptive-flow-test-v2-causal';setResultGroup(baseline?'baseline':testing?'adaptive':company?'company':'automatic');pruneProfileChannels();refreshMultiPlots()};
function refreshMultiPlots(){if(meta&&selected.some(n=>multiNames.includes(n))){cache=null;data={};loadWave(true)}}
$('scan-ratio').onchange=refreshMultiPlots;$('scan-duration').onchange=refreshMultiPlots;
function showDeviceMetrics(m){const host=$('device-metrics');host.hidden=activeResultGroup!=='automatic';host.replaceChildren();if(!m)return;host.textContent='设备方案最近一次扫描候选统计（未人工确认）：所选流量类型候选时长 '+(m.total_candidate_duration_seconds===null?'未检测':m.total_candidate_duration_seconds+' 秒')+'；'+Object.entries(m.by_type).map(([type,v])=>type+(v.count===null?' 未检测':' '+v.count+' 条 / '+v.duration_seconds+' 秒')).join('；')+'。中枢性暂停时长：不可判定；临床低通气时长：不可判定；AHI：不可计算。总时长按本次勾选的流量候选区间并集计算，不含漏气与数据质量事件。列表保留旧方案记录，请勿与本方案重复累计。人工修改后请查看事件列表，此处保留扫描原结果。'}
async function loadDeviceMetrics(){if(!meta)return;const fid=meta.file_id;try{const result=await api('/api/device-summary?file_id='+encodeURIComponent(fid));if(meta?.file_id===fid)showDeviceMetrics(result.device_metrics)}catch(e){if(meta?.file_id===fid)error(e)}}
$('event-filter').onchange=refreshEventVisibility;
function visibleEvents(items){const status=$('event-filter').value||'active';return items.filter(e=>matchesResultGroup(e,activeResultGroup)&&visibleEventTypes.has(e.type)&&(status==='all'||(status==='active'?e.status!=='ignored':e.status===status)))}
function refreshEventVisibility(){renderEventList();scheduleCursor()}
function renderDetectionChoices(){const host=$('scan-type-options');host.replaceChildren();const available=detectionTypes[$('scan-profile').value]||detectionTypes.device_candidates;for(const type of available){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.checked=chosenDetectionTypes.has(type);input.value=type;input.onchange=()=>{if(input.checked)chosenDetectionTypes.add(type);else chosenDetectionTypes.delete(type);updateDetectionSummary()};label.append(input,document.createTextNode(displayText(type)));host.append(label)}updateDetectionSummary()}
function updateDetectionSummary(){const count=chosenDetectionTypes.size,total=(detectionTypes[$('scan-profile').value]||detectionTypes.device_candidates).length;$('scan-type-summary').textContent=count===total?'全检测（'+total+' 类）':count?'已选 '+count+' / '+total+' 类':'请选择检测类型'}
function renderDisplayChoices(){const host=$('display-type-options');host.replaceChildren();for(const type of displayTypesForCurrentGroup()){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=type;input.checked=visibleEventTypes.has(type);input.onchange=()=>{if(input.checked)visibleEventTypes.add(type);else visibleEventTypes.delete(type);refreshEventVisibility()};label.append(input,document.createTextNode(displayText(type)));host.append(label)}}
$('adaptive-version').onchange=refreshEventVisibility;
$('result-adaptive').onclick=()=>setResultGroup('adaptive');$('result-auto').onclick=()=>{$('scan-profile').value=activeAutomaticProfile;$('scan-profile').onchange()};$('result-company').onclick=()=>{selectDetectionProfile('company_rules');$('scan-profile').onchange()};$('result-manual').onclick=()=>setResultGroup('manual');
$('result-baseline').onclick=()=>{selectDetectionProfile('dynamic_baseline_v3');$('scan-profile').onchange()};
$('baseline-all-configs').onchange=()=>{if($('baseline-all-configs').checked&&meta)scanViews.delete(meta.file_id);setResultGroup('baseline')};
for(const id of ['baseline-method','baseline-window'])$(id).onchange=()=>{if(scanning)return;updateBaselineControls();setResultGroup('baseline');const plotting=selected.some(n=>allDynamicNames.includes(n)||allOptimizedNames.includes(n));pruneProfileChannels();if(meta&&plotting)showBaselineDetectionCurves()};
function groupMultiEvidence(){groups=groups.map(g=>g.filter(n=>!multiNames.includes(n))).filter(g=>g.length);groups.push([multiNames[0],multiNames[1],multiNames[2],multiNames[3],multiNames[6]],[multiNames[4],multiNames[5]])}
function showBaselineDetectionCurves(){
 if(!meta||fileSwitching)return error('请先打开文件并等待切换完成');
 const profile=$('scan-profile').value,optimized=isOptimized(profile),b=baselineArgs(),wanted=optimized?optimizedNames(b.window_seconds,profile):dynamicNames(b.window_seconds);
 if((optimized?!optimizedAvailable(profile):meta.dynamic_baseline_version!=='dynamic-baseline-comparison-v1')||wanted.some(n=>!meta.channels.includes(n)))return error('请重启更新后的查看器，再刷新网页');
 selected=[...new Set([...selected.filter(n=>!analysisDerivedNames.includes(n)),'流量',...wanted])];
 if(optimized&&b.dynamic_method!=='detrended_abs')selected=selected.filter(n=>n!==wanted[5]);
 groups=groups.map(g=>g.filter(n=>selected.includes(n)&&!allDynamicNames.includes(n)&&!allOptimizedNames.includes(n))).filter(g=>g.length);
 groups.push(wanted.slice(0,4),[wanted[4]]);
 if(optimized){groups.push(wanted.slice(6));if(b.dynamic_method==='detrended_abs')groups.push([wanted[5]])}
 $('channels').querySelectorAll('input').forEach(c=>c.checked=selected.includes(c.dataset.name));filterChannels();cache=null;data={};loadWave(true);
 $('status').textContent=optimized?'已显示优化测试的主窗口、1秒复核强度/参考及背景估计（仅去背景法）。估计区间、主窗口达标区间和确认时刻分开记录。':'已显示当前检测方法和窗口对应的强度、参考、25%/50%门限和剩余比例；点击扫描后保存候选，在“动态基线检测”页签复核。';
}
$('show-company-a').onclick=()=>{if(isBaseline($('scan-profile').value))return showBaselineDetectionCurves();if(!meta)return error('请先打开CSV');const names=amplitudeChannels(),available=names.filter(n=>meta.channels.includes(n));if(available.length<2)return error('所选输入或A通道不可用，请重启更新后的查看器并重新打开CSV');selected=[...new Set([...selected,...available])];if($('scan-profile').value==='adaptive_multifeature')groupMultiEvidence();else{groups=groups.map(g=>g.filter(n=>n!==names[1])).filter(g=>g.length);groups.push([names[1]])}$('channels').querySelectorAll('input').forEach(c=>c.checked=selected.includes(c.dataset.name));filterChannels();cache=null;loadWave(true);$('status').textContent=$('scan-profile').value==='adaptive_multifeature'?'已显示A、参考B、严格比例门限和剩余/下降百分比；对应当前参数，噪声代理未标定。':$('scan-profile').value==='adaptive_test'?'已显示CSV流量和测试鲁棒A；实际冻结参考与迟滞门限见候选依据；不绘制固定10/17阈值。':'已显示所选输入及5秒幅度A；每秒更新，前5秒及无效窗口留空，虚线为10和17。'};
$('amplitude-source').onchange=()=>{pruneProfileChannels();if(activeResultGroup==='company')setResultGroup('company');if(meta)$('show-company-a').onclick()};
$('scan-types-all').onclick=()=>{chosenDetectionTypes=new Set(detectionTypes[$('scan-profile').value]||detectionTypes.device_candidates);renderDetectionChoices()};
$('scan-types-none').onclick=()=>{chosenDetectionTypes.clear();renderDetectionChoices()};
$('display-types-all').onclick=()=>{visibleEventTypes=new Set(Object.keys(eventColors));$('event-filter').value='all';renderDisplayChoices();refreshEventVisibility()};
$('display-types-none').onclick=()=>{visibleEventTypes.clear();renderDisplayChoices();refreshEventVisibility()};

async function loadFeedbackRuns(preferred){
 if(!meta)return;const fileId=meta.file_id;
 const serial=++feedbackSerial;
 try{const old=preferred||$('feedback-run').value,query=new URLSearchParams({file_id:fileId,limit:10,selected:old||''}),packet=await api('/api/scans?'+query);if(meta?.file_id!==fileId||serial!==feedbackSerial)return;const select=$('feedback-run');select.replaceChildren();
 const runs=[...packet.scans];if(packet.selected&&!runs.some(r=>r.run_id===packet.selected.run_id))runs.unshift(packet.selected);
 for(const run of runs){const option=document.createElement('option');option.value=run.run_id;option.textContent=scanLabel(run,true);option.title=scanLabel(run);select.append(option)}
 if([...select.options].some(o=>o.value===old))select.value=old;
 if(!runs.length){const option=document.createElement('option');option.value='';option.textContent='暂无未归档批次，可查看历史或执行扫描';select.append(option)}
 $('feedback-batch-info').textContent='最近10次未归档批次 · 共 '+packet.total+' 次'+(runs.length>packet.scans.length?'（另保留当前选中批次）':'');
 }catch(e){error(e)}
}
function scanLabel(run,compact=false){const local=new Date(run.created_at).toLocaleString('zh-CN',{hour12:false}),title=compact&&run.title.length>24?run.title.slice(0,24)+'…':run.title;return (run.settings.profile==='company_rules'?'['+(run.settings.flow_source==='csv_flow'?'CSV流量':'压力估算Q')+'] ':'')+displayText((run.archived?'[已归档] ':'')+title+' · '+local+'（本地） · '+run.detected_count+'条'+(compact?'':' · '+run.settings.version+' · '+(run.settings.event_types||['原方案全部类型']).join('、')))}
let historyOffset=0,historySerial=0,feedbackSerial=0,duplicateScanResolve=null;
function chooseDuplicateScan(run){$('duplicate-scan-text').textContent='相同文件、时间范围、规则版本、参数和检测类型已有扫描：'+scanLabel(run)+'。使用已有批次不会新建快照；仍然扫描会保留一份新的快照。';return new Promise(resolve=>{duplicateScanResolve=resolve;$('duplicate-scan-dialog').showModal()})}
function finishDuplicateScan(action){const resolve=duplicateScanResolve;duplicateScanResolve=null;$('duplicate-scan-dialog').close();resolve?.(action)}
$('duplicate-use').onclick=()=>finishDuplicateScan('reuse');$('duplicate-rescan').onclick=()=>finishDuplicateScan('rescan');$('duplicate-cancel').onclick=()=>finishDuplicateScan('cancel');
$('duplicate-scan-dialog').oncancel=e=>{e.preventDefault();finishDuplicateScan('cancel')};
async function loadScanHistory(){
 if(!meta)return;const fid=meta.file_id,serial=++historySerial;$('history-error').textContent='';
 try{const q=new URLSearchParams({file_id:fid,limit:20,offset:historyOffset,q:$('history-query').value.trim(),state:$('history-state').value||'active'}),packet=await api('/api/scans?'+q);if(meta?.file_id!==fid||serial!==historySerial)return;
 if(historyOffset>=packet.total&&historyOffset>0){historyOffset=Math.max(0,Math.floor((packet.total-1)/20)*20);return loadScanHistory()}
 const host=$('history-list');host.replaceChildren();
 for(const run of packet.scans){const row=document.createElement('div');row.className='history-row';const text=document.createElement('p');text.textContent=scanLabel(run);row.append(text);
 const name=document.createElement('input');name.value=run.title;name.maxLength=100;name.setAttribute('aria-label','批次名称 '+run.title);row.append(name);
 const button=(label,fn)=>{const b=document.createElement('button');b.type='button';b.textContent=label;b.onclick=async()=>{b.disabled=true;try{await fn()}catch(e){$('history-error').textContent=e.message}finally{b.disabled=false}};row.append(b)};
 const edit=async change=>{await api('/api/scans/edit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file_id:fid,run_id:run.run_id,...change})});if(meta?.file_id!==fid)return;if(change.archived&&$('feedback-run').value===run.run_id)$('feedback-run').value='';await loadFeedbackRuns();await loadScanHistory()};
 button('保存名称',()=>edit({title:name.value}));button('选用批次',async()=>{if(meta?.file_id!==fid)return;await loadFeedbackRuns(run.run_id);if(meta?.file_id===fid)$('scan-history-dialog').close()});button(run.archived?'恢复到常用列表':'归档',()=>edit({archived:!run.archived}));host.append(row)}
 if(!packet.scans.length){const p=document.createElement('p');p.textContent='暂无符合条件的批次。';host.append(p)}
 $('history-page').textContent='第 '+(packet.total?Math.floor(historyOffset/20)+1:0)+' / '+Math.ceil(packet.total/20)+' 页 · 共 '+packet.total+' 次';$('history-prev').disabled=historyOffset===0;$('history-next').disabled=historyOffset+20>=packet.total;
 }catch(e){if(meta?.file_id===fid)$('history-error').textContent=e.message}
}
$('feedback-history').onclick=()=>{if(!meta)return error('请先打开CSV');historyOffset=0;$('scan-history-dialog').showModal();loadScanHistory()};
$('history-close').onclick=()=>$('scan-history-dialog').close();
$('history-search').onclick=()=>{historyOffset=0;loadScanHistory()};$('history-state').onchange=$('history-search').onclick;
$('history-query').onkeydown=e=>{if(e.key==='Enter')$('history-search').onclick()};
$('history-prev').onclick=()=>{historyOffset=Math.max(0,historyOffset-20);loadScanHistory()};$('history-next').onclick=()=>{historyOffset+=20;loadScanHistory()};
function downloadJsonPacket(packet,suffix){const blob=new Blob([JSON.stringify(packet,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=meta.name.replace(/\.csv$/i,'')+'.'+packet.run_id+'.'+suffix+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
async function exportFeedback(kind){
 if(!meta||!$('feedback-run').value)return error('请先执行扫描并选择扫描批次');
 if(eventSaving||scanning)return error('请等待当前保存或扫描完成');
 const startText=$('review-start').value,endText=$('review-end').value;
 if(kind==='reviewed'&&(!startText||!endText||!$('reviewer').value.trim()))return error('请填写校验人和实际已检查的起止秒数');
 try{const args=kind==='reviewed'?reviewExportArgs():{file_id:meta.file_id,run_id:$('feedback-run').value};const fid=meta.file_id,packet=await api('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...args,kind})});if(meta?.file_id!==fid)return;downloadJsonPacket(packet,kind==='automatic'?'automatic':'reviewed')}catch(e){error(e)}
}
let reviewedTypeSelection=new Set(),pendingPackage=null,packageSaving=false,lastPackageFileId=null;
function renderReviewChoices(){const host=$('review-type-options');host.replaceChildren();for(const type of Object.keys(eventColors)){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=type;input.checked=reviewedTypeSelection.has(type);input.onchange=()=>{if(input.checked)reviewedTypeSelection.add(type);else reviewedTypeSelection.delete(type);$('review-type-summary').textContent=reviewedTypeSelection.size?'已选 '+reviewedTypeSelection.size+' 类':'请选择实际检查的类型'};label.append(input,document.createTextNode(displayText(type)));host.append(label)}$('review-type-summary').textContent=reviewedTypeSelection.size?'已选 '+reviewedTypeSelection.size+' 类':'请选择实际检查的类型'}
function reviewExportArgs(){if(fileSwitching)throw Error('文件正在切换，请稍候再导出');if(!meta||!$('feedback-run').value)throw Error('请先选择对应的自动扫描批次');if(!$('review-start').value||!$('review-end').value||!$('reviewer').value.trim())throw Error('请填写校验人和实际检查范围');if(!reviewedTypeSelection.size)throw Error('请勾选实际检查过的事件类型');return {file_id:meta.file_id,run_id:$('feedback-run').value,reviewer:$('reviewer').value.trim(),reviewed_range:[Number($('review-start').value),Number($('review-end').value)],reviewed_types:[...reviewedTypeSelection],review_mode:$('review-mode').value||'spot'}}
$('export-package').onclick=async()=>{if(scanning||eventSaving||packageSaving)return error('请等待扫描或保存完成');const button=$('export-package');button.disabled=true;try{const args=reviewExportArgs(),preview=await api('/api/feedback/prepare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(args)});if(meta?.file_id!==args.file_id)return;pendingPackage={...args,expected_revision:preview.revision};$('package-preview').textContent='文件：'+preview.file_name+'\n批次：'+preview.run_id+'\n校验人：'+preview.reviewer+'\n原始时间：第 '+preview.reviewed_range.join(' — ')+' 秒\n检查类型：'+displayText(preview.reviewed_types.join('、'))+'\n检查方式：'+(preview.review_mode==='complete'?'完整检查所选类型，包含主动补标漏检':'逐条复核；其余空白区间保持未知')+'\n范围内标记 '+preview.event_count+' 条，其中已确认 '+preview.confirmed_count+' 条、待复核 '+preview.pending_count+' 条；跨边界 '+preview.boundary_count+' 条单独记录，不直接训练。\n保存位置：'+preview.folder+'\n\n原始CSV只存一份；自动JSON按批次复用。校验JSON仅保存所选区间/类型，不重复嵌入整份自动快照和全文件历史；相同内容重复保存会复用。';$('package-agree').checked=false;$('package-save').disabled=true;$('package-error').textContent='';$('package-dialog').showModal()}catch(e){error(e)}finally{button.disabled=false}};
$('package-agree').onchange=()=>{$('package-save').disabled=!$('package-agree').checked};
$('package-cancel').onclick=()=>{if(!packageSaving){pendingPackage=null;$('package-dialog').close()}};
$('package-dialog').oncancel=e=>{if(packageSaving)e.preventDefault();else pendingPackage=null};
$('package-save').onclick=async()=>{if(!pendingPackage||!$('package-agree').checked||packageSaving)return;const args={...pendingPackage,confirmed:true};packageSaving=true;$('package-save').disabled=true;$('package-cancel').disabled=true;try{const result=await api('/api/feedback/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(args)});if(meta?.file_id!==args.file_id)return;$('package-dialog').close();pendingPackage=null;lastPackageFileId=args.file_id;$('package-result').textContent='反馈已保存到：'+result.folder+'\n新增 '+result.created.length+' 个文件，复用 '+result.reused.length+' 个文件。首次回传整个文件夹；之后回传新增文件及对应manifests清单。若选择新扫描批次，还需回传新增自动JSON；负责人已持有的CSV无需重复回传。训练时需按数据指纹、标注ID及校验版本去重，不能把重复或重叠快照当作新样本。';$('package-open').hidden=false}catch(e){$('package-error').textContent=e.message+'；如标记已变化，请取消并重新预览。'}finally{packageSaving=false;$('package-cancel').disabled=false;$('package-save').disabled=!$('package-agree').checked}};
$('package-open').onclick=async()=>{if(!lastPackageFileId||meta?.file_id!==lastPackageFileId)return;try{await api('/api/feedback/open-folder',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file_id:lastPackageFileId})})}catch(e){error(e)}};
$('feedback-refresh').onclick=()=>loadFeedbackRuns();$('export-automatic').onclick=()=>exportFeedback('automatic');$('export-reviewed').onclick=()=>exportFeedback('reviewed');
$('review-current-range').onclick=()=>{if(!meta)return;$('review-start').value=Math.min(view[0],meta.raw_duration).toFixed(3);$('review-end').value=Math.min(view[1],meta.raw_duration).toFixed(3)};
$('compare-feedback').onchange=async e=>{const file=e.target.files[0];if(!file)return;try{if(!meta)throw Error('请先打开对应原始CSV');if(file.size>50*1024*1024)throw Error('反馈JSON请小于50MB');const packet=JSON.parse(await file.text()),rows=compareReviewed(packet,meta),host=$('feedback-comparison');host.replaceChildren();const summary=document.createElement('p');summary.textContent='校验人：'+packet.reviewer+' · 程序：'+packet.app_version+' · 比较结果 '+rows.length+' 条（只读查看）';host.append(summary);for(const item of rows){const row=document.createElement('div');row.className='event-row';const text=document.createElement('div');text.className='event-text';text.textContent=item.result+' · '+displayText(item.event.type)+' · '+eventClock(item.event.start)+' — '+eventClock(item.event.end);if(item.original){const original=document.createElement('small');original.textContent='自动原区间：'+item.original.start+'—'+item.original.end+'秒 · '+displayText(item.original.type);text.append(original)}const button=document.createElement('button');button.textContent='查看波形';button.onclick=()=>jumpEvent(item.event);row.append(text,button);host.append(row)}}catch(err){error(err)}finally{e.target.value=''}};
api('/api/health').then(p=>{$('app-version').textContent='版本 '+p.app_version+' · 本机运行 · 研发试用'}).catch(()=>{});

let sidebarFrame=0;
function resizeSidebar(){for(const [selector,variable] of [['.channel-sidebar','--sidebar-height'],['#event-panel','--event-panel-height']]){const sidebar=document.querySelector(selector);if(!sidebar||sidebar.hidden)continue;const top=Math.max(16,sidebar.getBoundingClientRect().top);sidebar.style.setProperty(variable,Math.max(selector==='.channel-sidebar'?120:180,window.innerHeight-top-16)+'px')}}
function scheduleSidebarResize(){if(sidebarFrame)return;sidebarFrame=requestAnimationFrame(()=>{sidebarFrame=0;resizeSidebar()})}
window.addEventListener('scroll',scheduleSidebarResize,{passive:true});
window.addEventListener('resize',scheduleSidebarResize);

$('baseline-duration').onchange=()=>{if(scanning)return;$('scan-duration').value=Number($('baseline-duration').value);updateBaselineControls();cache=null;setResultGroup('baseline');const plotting=selected.some(n=>n.startsWith('优化v3'));if(plotting)showBaselineDetectionCurves()};
$('show-feedback-run').onclick=()=>{if(scanning||eventSaving||fileSwitching)return error('请等待当前扫描或保存完成');showScanBatch($('feedback-run').value,true).catch(error)};

if(primaryProfiles.includes($('scan-profile').value))$('scan-profile').onchange();

let eventPanelOpen=true;
function eventPanelIsDrawer(){return window.matchMedia?.('(max-width:1100px)').matches||false}
function updateEventPanelChrome(){
 $('event-panel').hidden=!eventPanelOpen;document.body?.classList?.toggle('event-panel-collapsed',!eventPanelOpen);
 $('event-panel-backdrop').hidden=!eventPanelOpen||!eventPanelIsDrawer();
 $('toggle-events').ariaExpanded=String(eventPanelOpen);$('toggle-events').textContent=eventPanelOpen?'收起事件列表':'事件列表';
}
function setEventPanelOpen(open,persist=true){eventPanelOpen=Boolean(open);updateEventPanelChrome();if(persist)try{localStorage.setItem('respiratory-event-panel-open-v1',String(eventPanelOpen))}catch(e){}overviewImage=null;cache=null;if(meta){scheduleSidebarResize();scheduleRender();loadWave(true)}}
$('toggle-events').onclick=()=>setEventPanelOpen(!eventPanelOpen);
$('result-current').onclick=()=>setResultGroup(resultGroupForProfile($('scan-profile').value));
$('close-event-panel').onclick=$('event-panel-backdrop').onclick=()=>setEventPanelOpen(false);
window.addEventListener('resize',()=>{updateEventPanelChrome();scheduleSidebarResize()});
window.addEventListener('keydown',e=>{if(e.key==='Escape'&&eventPanelOpen&&eventPanelIsDrawer()&&!document.querySelector('dialog[open]'))setEventPanelOpen(false)});
try{const saved=localStorage.getItem('respiratory-event-panel-open-v1');setEventPanelOpen(saved===null?!eventPanelIsDrawer():saved==='true',false)}catch(e){setEventPanelOpen(!eventPanelIsDrawer(),false)}
