const assert=require('node:assert/strict'),vm=require('node:vm'),{readFileSync}=require('node:fs');
const elements=new Map();
function element(){return {value:'',textContent:'',clientWidth:1000,children:[],dataset:{},style:{},checked:false,addEventListener(){},setAttribute(){},replaceChildren(){this.children=[]},append(...v){this.children.push(...v)},querySelectorAll(){return []}}}
const context=vm.createContext({document:{getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id)},createElement:element,createTextNode:text=>({textContent:text})},window:{addEventListener(){}},fetch(){return new Promise(()=>{})},URLSearchParams,AbortController,requestAnimationFrame(){return 1},setTimeout(){return 1},clearTimeout(){},console,devicePixelRatio:1});
vm.runInContext(readFileSync('viewer.js','utf8'),context);
const read=code=>vm.runInContext(code,context),ids=()=>JSON.parse(read('JSON.stringify(visibleEvents(annotations.items).map(e=>e.id))'));
(async()=>{
read(`
meta={file_id:'f',start:'2026-01-01 00:00:00',raw_duration:200,duration:200,dynamic_baseline_optimized_v3_version:'dynamic-baseline-optimized-v3-configurable-v13',channels:['流量',...allOptimizedNames]};
const base={start:80,end:90,type:'强度显著下降候选',source:'detector',status:'candidate',mode:'raw',note:'test',channels:['流量'],detection_profile:'dynamic_baseline_v3',detection_method:'detrended_abs',detection_window_seconds:1,detection_min_duration:5};
annotations={items:[{...base,id:'current'},{...base,id:'old-same-config'},{...base,id:'other',detection_profile:'dynamic_baseline_v2'},{...base,id:'manual',source:'manual',status:'reviewed'}]};
$('baseline-window').options=Array.from({length:10},(_,i)=>({value:String(i+1)}));$('baseline-method').options=[{value:'detrended_abs'}];
selected=['流量'];groups=[['流量']];eventSyncReady=true;filterChannels=()=>{};loadWave=()=>{};render=()=>{};loadFeedbackRuns=async()=>{};loadDeviceMetrics=async()=>{};
applyShared=p=>{annotations.items=p.events};
$('scan-profile').value='dynamic_baseline_v3';$('scan-profile').onchange();
$('baseline-window').value='1';$('baseline-duration').value='5';$('baseline-duration').onchange();
const configuration={profile:'dynamic_baseline_v3',version:'dynamic-baseline-optimized-v3-configurable-v13',method:'detrended_abs',window_seconds:1,min_duration:5,event_types:['强度显著下降候选']};
let snapshot={settings:configuration,events:[{id:'current'}],event_links:{}},reuse=false;
api=async(url,options)=>{if(url==='/api/scans/match')return {match:reuse?{run_id:'reuse'}:null};if(url==='/api/feedback')return snapshot;if(url==='/api/detect')return {detected_count:snapshot.events.length,added:0,skipped:snapshot.events.length,eligible_seconds:150,warnings:[],packet:{run_id:'run',events:annotations.items}};throw Error(url)};
chooseDuplicateScan=async()=> 'reuse';
`);
assert.equal(read("$('baseline-duration').hidden"),false);assert.equal(read("$('scan-duration').hidden"),true);assert.equal(read("$('scan-duration').value"),5);assert(read("$('event-mode-label').textContent.includes('v3')"));assert(read("$('event-mode-config').textContent.includes('1秒窗口')"));
await read('scanRecording(true)');assert.deepEqual(ids(),['current']);assert.equal(read('annotations.items.length'),4,'batch display cannot delete old annotations');
assert.equal(read("$('baseline-all-configs').checked"),false);
read("selected=['优化v31秒强度'];const priorKey=selectionKey();$('baseline-duration').value='6';$('baseline-duration').onchange()");
assert.notEqual(read('selectionKey()'),read('priorKey'));assert.deepEqual(ids(),[],'new duration must not display a previous configuration');
read("$('baseline-duration').value='5';$('baseline-duration').onchange();snapshot={...snapshot,events:[]}");
await read('scanRecording(true)');assert.deepEqual(ids(),[],'zero new detections must show zero, not prior events');
read("snapshot={...snapshot,events:[{id:'current'}]};reuse=true");await read('scanRecording(true)');assert.deepEqual(ids(),['current'],'reuse must scope to that batch too');
read("$('baseline-all-configs').checked=true;$('baseline-all-configs').onchange()");assert.deepEqual(ids(),['current','old-same-config']);
read("annotations.items.push({id:'alias',start:10,end:12,type:'数据缺失',source:'detector',detection_profile:'engineering',status:'reviewed',note:'',channels:['流量'],mode:'raw'});snapshot={settings:{profile:'device_candidates',min_duration:10,event_types:['数据缺失']},events:[{id:'original-id'}],event_links:{'original-id':'alias'}}");
await read("showScanBatch('history',true)");assert.equal(read("$('scan-profile').value"),'device_candidates');assert.deepEqual(ids(),['alias'],'linked reviewed duplicates must remain visible in their scan');
assert.equal(read('annotations.items.find(e=>e.id==="alias").detection_profile'),'engineering','display aliases must not rewrite original provenance');
assert.equal(read("$('scan-all').disabled"),true,'retired schemes cannot be scanned');
read("$('result-baseline').onclick()");assert.equal(read("$('scan-profile').value"),'dynamic_baseline_v3');assert.equal(read("$('scan-all').disabled"),false);
read("$('result-company').onclick()");assert.equal(read("$('scan-profile').value"),'company_rules');assert.equal(read("$('scan-duration').value"),7);assert.equal(read("$('event-mode-label').textContent"),'幅度规则（试验）');
read("$('result-manual').onclick()");assert.equal(read("$('result-current').hidden"),false);read("$('result-current').onclick()");assert.equal(read('activeResultGroup'),'company');assert.equal(read("$('scan-profile').value"),'company_rules');
const html=readFileSync('index.html','utf8'),picker=html.match(/<select id="scan-profile"[^>]*>([\s\S]*?)<\/select>/)[1];
assert.deepEqual([...picker.matchAll(/<option value="([^"]+)"/g)].map(m=>m[1]),['company_rules','dynamic_baseline_v3']);
assert(picker.includes('value="dynamic_baseline_v3" selected'));

read("meta={...meta,file_id:'other-file'}");assert.equal(read('currentScanView()'),null,'batch IDs cannot leak across files');
console.log('PASS: 1–10s controls, 5–10s duration, batch-only display, zero detections, reuse, explicit history, aliases, immutable storage and file isolation');
})().catch(e=>{console.error(e);process.exitCode=1});
