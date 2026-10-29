// Regression checks for the pan/cache scheduler, with a minimal browser host.
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const elements=new Map();
const calls=[];let pendingFrames=0,pendingTimers=0;
const context=vm.createContext({
 document:{getElementById(id){if(!elements.has(id))elements.set(id,{textContent:'',clientWidth:1000,addEventListener(){}});return elements.get(id)}},
 window:{addEventListener(){}},URLSearchParams,console,
 fetch(url){calls.push(url);return new Promise(()=>{})},
 requestAnimationFrame(){pendingFrames++;return pendingFrames},
 setTimeout(){pendingTimers++;return pendingTimers},clearTimeout(){},
 AbortController,devicePixelRatio:1,
});
vm.runInContext(readFileSync('viewer.js','utf8'),context);
calls.length=0;
vm.runInContext(`
meta={duration:1000};selected=['flow'];baseSecond=3600;displayMode='raw';
view=[100,160];cache={start:40,end:220,width:60,pixels:1000,key:'flow'};
drag={range:[100,160],limits:new Map()};
for(let i=0;i<100;i++)setView(100+i*.1,160+i*.1);
`,context);
assert.equal(pendingFrames,1,'pointer updates should coalesce to one pending animation frame');
assert.equal(pendingTimers,0,'dragging must not schedule delayed server reads');
assert.equal(calls.length,0,'dragging should render locally');
assert.equal(vm.runInContext('cacheCovers()',context),true);
vm.runInContext('drag=null;loadWave()',context);
assert.equal(calls.length,0,'cached nearby pans should need no server request');
vm.runInContext('view=[400,460];loadWave()',context);
assert.equal(calls.length,1,'uncached view must fetch once');
const q=new URL('http://localhost'+calls[0]).searchParams;
assert.equal(q.get('start'),'340');assert.equal(q.get('end'),'520');
assert.equal(q.get('pixels'),'3000','prefetch must preserve visible-window resolution');
assert.equal(vm.runInContext('clock(1)',context),'01:00:01');
assert.equal(vm.runInContext('lowerBound([[0,1],[2,3],[2,4],[4,5]],2)',context),1);
console.log('PASS: frame coalescing, request suppression, cache reuse, padded resolution, clock and search');

// Group operations must preserve each selected channel exactly once and reuse waveform data.
vm.runInContext(`
render=()=>{};
meta={duration:1000,channels:['flow','pressure','leak']};
selected=['flow','pressure','leak'];groups=[];reconcileGroups();
moveChannel('flow','pressure');
`,context);
assert.equal(vm.runInContext('JSON.stringify(groups)',context),'[["pressure","flow"],["leak"]]');
vm.runInContext("moveChannel('leak','pressure')",context);
assert.equal(vm.runInContext('JSON.stringify(groups)',context),'[["pressure","flow","leak"]]');
vm.runInContext("moveChannel('flow',null)",context);
assert.equal(vm.runInContext('JSON.stringify(groups)',context),'[["pressure","leak"],["flow"]]');
vm.runInContext("selected=['pressure','flow'];reconcileGroups()",context);
assert.equal(vm.runInContext('JSON.stringify(groups)',context),'[["pressure"],["flow"]]');
vm.runInContext("selected.push('leak');reconcileGroups();moveChannel('flow','flow');moveChannel('missing','flow');moveChannel('flow','missing')",context);
assert.equal(vm.runInContext('JSON.stringify(groups)',context),'[["pressure"],["flow"],["leak"]]');
assert.equal(calls.length,1,'group changes must not fetch or modify source data');

// Identical numeric values in different layers must have identical screen Y positions.
vm.runInContext(`
drag=null;meta.gaps=[];
var traces=[],currentTrace=[];
var ctx={setTransform(){},clearRect(){},beginPath(){currentTrace=[]},moveTo(x,y){currentTrace.push([x,y])},lineTo(x,y){currentTrace.push([x,y])},stroke(){traces.push(currentTrace)},fillText(){},save(){},restore(){},rect(){},clip(){},fillRect(){}};
var canvas={clientWidth:1000,clientHeight:180,width:1000,height:180,getContext(){return ctx}};
paint(canvas,[],[0,10],'',false,[{points:[[0,10,0],[10,20,1]],color:'red'},{points:[[0,10,0],[10,100,1]],color:'blue'}]);
`,context);
assert.equal(vm.runInContext('traces.at(-2)[0][1]===traces.at(-1)[0][1]',context),true);
assert.equal(vm.runInContext('traces.at(-2)[1][1]>traces.at(-1)[1][1]',context),true);
console.log('PASS: overlay merge, multi-channel merge, split, deselection, invalid moves and shared numerical axis');
