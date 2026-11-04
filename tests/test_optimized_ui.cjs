const assert=require('node:assert/strict'),vm=require('node:vm'),{readFileSync}=require('node:fs');
const {AnnotationModel}=require('../annotation-model.js'),elements=new Map();
function element(){return {value:'',textContent:'',clientWidth:1000,children:[],dataset:{},style:{},checked:false,addEventListener(){},setAttribute(){},replaceChildren(){this.children=[]},append(...v){this.children.push(...v)},querySelectorAll(){return []}}}
const context=vm.createContext({document:{getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id)},createElement:element,createTextNode:text=>({textContent:text})},window:{addEventListener(){}},fetch(){return new Promise(()=>{})},URLSearchParams,AbortController,requestAnimationFrame(){return 1},setTimeout(){return 1},clearTimeout(){},console,devicePixelRatio:1});
vm.runInContext(readFileSync('viewer.js','utf8'),context);
vm.runInContext(`
meta={file_id:'a',start:'2026-01-01 00:00:00',raw_duration:200,dynamic_baseline_version:'dynamic-baseline-comparison-v1',dynamic_baseline_detector_version:'dynamic-baseline-detector-v1',dynamic_baseline_optimized_version:'dynamic-baseline-optimized-v2',channels:['流量',...allDynamicNames,...allOptimizedNames]};
const base={start:80,end:90,type:'强度显著下降候选',source:'detector',status:'candidate',mode:'raw',note:'test',channels:['流量'],detection_window_seconds:5};
const optimized={...base,id:'optimized',detection_profile:'dynamic_baseline_v2',detection_method:'detrended_abs',detection_version:'dynamic-baseline-optimized-v2',evidence:{schema:1,estimated_waveform_interval:[80,90],boundary_duration_seconds:10,main_qualified_total_seconds:9,main_qualified_intervals:[[84,93]],confirmed_at:90,review_flags:[]}};
annotations={items:[{...base,id:'old',detection_profile:'dynamic_baseline',detection_method:'centered_rms'},optimized]};
selected=['流量'];groups=[['流量']];$('scan-profile').value='dynamic_baseline_v2';$('baseline-method').value='centered_rms';$('baseline-window').value='2';$('baseline-method').options=[{value:'detrended_abs',hidden:true,disabled:true}];
filterChannels=()=>{};loadWave=()=>{};render=()=>{};setView=(a,b)=>view=[a,b];
$('scan-profile').onchange();
`,context);
const read=code=>vm.runInContext(code,context),ids=()=>JSON.parse(read('JSON.stringify(visibleEvents(annotations.items).map(e=>e.id))'));
assert.equal(read("$('baseline-method').value"),'detrended_abs');assert.equal(read("$('baseline-window').value"),'5');assert.equal(read("$('scan-duration').value"),10);
assert.deepEqual(ids(),['optimized']);assert.equal(read("$('baseline-method').options[0].disabled"),false);
read("$('show-company-a').onclick()");assert.equal(read("selected.includes('优化5秒强度')"),true);assert.equal(read("selected.includes('优化5秒1秒复核参考')"),true);assert.equal(read("selected.includes('动态5秒强度')"),false);
assert.equal(read("profileChannels().includes('优化5秒背景估计')"),true);
const oldKey=read('selectionKey()');read("$('baseline-method').value='centered_rms';$('baseline-method').onchange()");assert.notEqual(read('selectionKey()'),oldKey);assert.equal(read("profileChannels().includes('优化5秒背景估计')"),false);
read("$('baseline-all-configs').checked=true;$('baseline-all-configs').onchange()");assert.deepEqual(ids(),['optimized'],'all windows cannot mix V1 with optimized V2');
read("$('scan-profile').value='dynamic_baseline';$('scan-profile').onchange()");assert.deepEqual(ids(),['old']);assert.equal(read("$('baseline-method').options[0].disabled"),true);
read('jumpEvent(optimized)');assert.equal(read("$('scan-profile').value"),'dynamic_baseline_v2');assert.equal(read("$('baseline-method').value"),'detrended_abs');assert.equal(read("$('baseline-window').value"),'5');assert.deepEqual(ids(),['optimized']);
const model=new AnnotationModel({file_id:'a',raw_duration:200,display_duration:200,channels:['流量']},{getItem(){return null},setItem(){}}),event=JSON.parse(read('JSON.stringify(optimized)'));
model.save(event);model.save({...model.items[0],status:'reviewed',start:81,note:'human'});assert.deepEqual(model.export().events[0].evidence,event.evidence);
assert.equal(model.export().events[0].detection_profile,'dynamic_baseline_v2');assert.throws(()=>model.save({...event,detection_profile:'dynamic_baseline'}));
read("annotations.items=[{...optimized,evidence:{schema:1}}];renderEventList()");
const html=readFileSync('index.html','utf8');assert(!html.includes('value="dynamic_baseline_v2"'),'v2 is available only through historical batches');assert(!html.includes('id="dynamic-panel"'));
console.log('PASS: optimized dropdown defaults, matching diagnostic traces, method/slot isolation, jump restore, immutable reviewed evidence and safe rendering');
