const assert=require('node:assert/strict'),vm=require('node:vm'),{readFileSync}=require('node:fs');
const elements=new Map();
function element(){return {value:'',textContent:'',clientWidth:1000,children:[],querySelectorAll(){return []},addEventListener(){},replaceChildren(){this.children=[]},append(...items){this.children.push(...items)}}}
const context=vm.createContext({document:{getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id)},createElement:element,createTextNode:text=>({textContent:text})},window:{addEventListener(){}},URLSearchParams,console,fetch(){return new Promise(()=>{})},requestAnimationFrame(){return 1},setTimeout(){return 1},clearTimeout(){},AbortController,devicePixelRatio:1});
vm.runInContext(readFileSync('viewer.js','utf8'),context);
vm.runInContext(`
const samples=[{id:1,type:'总漏气升高',status:'candidate'},{id:2,type:'总漏气升高',status:'reviewed'},{id:3,type:'流量波动减弱候选',status:'candidate'},{id:4,type:'总漏气升高',status:'ignored'}];
visibleEventTypes=new Set(['总漏气升高']);$('event-filter').value='active';
`,context);
const ids=()=>JSON.parse(vm.runInContext('JSON.stringify(visibleEvents(samples).map(e=>e.id))',context));
assert.deepEqual(ids(),[1,2]);
vm.runInContext("$('event-filter').value='reviewed'",context);assert.deepEqual(ids(),[2]);
vm.runInContext("$('event-filter').value='all'",context);assert.deepEqual(ids(),[1,2,4]);
vm.runInContext('visibleEventTypes.clear()',context);assert.deepEqual(ids(),[]);
vm.runInContext("$('display-types-all').onclick()",context);assert.equal(elements.get('event-filter').value,'all');assert.equal(ids().length,4);
vm.runInContext("$('display-types-none').onclick()",context);assert.deepEqual(ids(),[]);
assert.equal(vm.runInContext('samples.length',context),4,'visibility must not mutate stored events');
vm.runInContext("$('scan-profile').value='device_candidates';renderDetectionChoices()",context);
assert.equal(elements.get('scan-type-options').children.length,5);
vm.runInContext("$('scan-types-none').onclick()",context);assert.equal(vm.runInContext('chosenDetectionTypes.size',context),0);
vm.runInContext("$('scan-types-all').onclick()",context);assert.equal(vm.runInContext('chosenDetectionTypes.size',context),5);
vm.runInContext("$('scan-profile').value='engineering';$('scan-profile').onchange()",context);assert.equal(vm.runInContext('chosenDetectionTypes.size',context),4);
vm.runInContext("showDeviceMetrics({by_type:{'流量波动近消失候选':{count:null,duration_seconds:null}},total_candidate_duration_seconds:0})",context);
assert.ok(elements.get('device-metrics').textContent.includes('未检测'));
assert.equal(vm.runInContext(`(() => {
 visibleEventTypes=new Set(['总漏气升高']);$('event-filter').value='reviewed';
 const previous=annotations;
 annotations={items:samples.map(e=>({...e,start:2,end:5,channels:['流量']}))};
 let bands=0;
 const ctx={save(){},beginPath(){},rect(){},clip(){},restore(){},fillRect(){bands++},strokeRect(){},setLineDash(){},fillText(){}};
 firstPlotEvents.checked=true;drawEventBands(ctx,300,180,[0,10],['气压1'],true);
 firstPlotEvents.checked=false;drawEventBands(ctx,300,180,[0,10],['气压1'],true);
 annotations=previous;
 return bands;
})()`,context),1,'the first plot projects one filtered event from another channel, then hides it');
console.log('PASS: type/status intersection, all/none selection, profile switching, hidden events preserved and unscanned metrics');
vm.runInContext(`
const separated=[{id:'old',type:'数据缺失',source:'detector',note:'自动筛查 machine-flow-v1',status:'candidate'},
{id:'company',type:'数据缺失',source:'detector',note:'公司规则试验 company-pressure-q-draft-v1',status:'candidate'},
{id:'edited',type:'伪迹/干扰',source:'detector',detection_profile:'company_rules',note:'human changed note',status:'reviewed'},
{id:'manual',type:'疑似暂停',source:'manual',note:'',status:'reviewed'}];
setResultGroup('company');$('display-types-all').onclick();
`,context);
const splitIds=()=>JSON.parse(vm.runInContext('JSON.stringify(visibleEvents(separated).map(e=>e.id))',context));
assert.deepEqual(splitIds(),['company','edited']);
vm.runInContext("$('event-filter').value='reviewed';setResultGroup('manual')",context);
assert.deepEqual(splitIds(),['manual']);
vm.runInContext("setResultGroup('automatic');$('display-types-all').onclick()",context);
assert.deepEqual(splitIds(),['old']);
vm.runInContext("setResultGroup('company')",context);assert.deepEqual(splitIds(),['edited']);
vm.runInContext("setResultGroup('automatic')",context);
vm.runInContext(`
$('amplitude-source').value='csv_flow';setResultGroup('company');$('display-types-all').onclick();
const inputs=[{id:'pressure',source:'detector',type:'公司暂停候选',detection_profile:'company_rules',status:'candidate'},
{id:'csv',source:'detector',type:'公司暂停候选',detection_profile:'company_rules',detection_input:'csv_flow',status:'candidate'}];
`,context);
assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(visibleEvents(inputs).map(e=>e.id))',context)),['csv']);
vm.runInContext("$('amplitude-source').value='pressure_q'",context);
assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(visibleEvents(inputs).map(e=>e.id))',context)),['pressure']);
vm.runInContext("setResultGroup('automatic')",context);
vm.runInContext("$('scan-profile').value='company_rules';$('scan-profile').onchange()",context);
assert.equal(vm.runInContext("$('scan-duration').value",context),7);
assert.equal(vm.runInContext("$('scan-ratio-label').hidden",context),true);
assert.equal(vm.runInContext("$('company-rule-info').hidden",context),false);
assert.equal(vm.runInContext('chosenDetectionTypes.size',context),4);
vm.runInContext("$('scan-profile').value='device_candidates';$('scan-profile').onchange()",context);
vm.runInContext(`
const run={run_id:'r',created_at:'2026-10-29T01:00:00Z',title:'第一轮',archived:true,detected_count:12,settings:{version:'test',event_types:['总漏气升高']}};
`,context);
assert.ok(vm.runInContext('scanLabel(run).includes("已归档")&&scanLabel(run).includes("总漏气升高")&&scanLabel(run).includes("本地")',context));
assert.ok(!vm.runInContext('scanLabel(run,true).includes("总漏气升高")',context),'dropdown must stay compact');
console.log('PASS: compact batch labels, local time, archive state and detailed event types');
vm.runInContext(`meta={file_id:'f'};$('feedback-run').value='r';$('reviewer').value='tester';$('review-start').value='0';$('review-end').value='600';$('review-mode').value='spot';reviewedTypeSelection=new Set(['总漏气升高']);`,context);
const args=JSON.parse(vm.runInContext('JSON.stringify(reviewExportArgs())',context));
assert.deepEqual(args.reviewed_range,[0,600]);assert.deepEqual(args.reviewed_types,['总漏气升高']);assert.equal(args.review_mode,'spot');
vm.runInContext('reviewedTypeSelection.clear()',context);assert.throws(()=>vm.runInContext('reviewExportArgs()',context));
assert.equal((readFileSync('index.html','utf8').match(/id="package-dialog"/g)||[]).length,1);
console.log('PASS: export scope/types are explicit and confirmation dialog exists');
vm.runInContext("$('scan-profile').value='adaptive_test';$('scan-profile').onchange()",context);
assert.equal(vm.runInContext('activeResultGroup',context),'adaptive');
assert.equal(vm.runInContext("$('scan-ratio').value",context),50);
assert.equal(vm.runInContext("$('scan-duration').value",context),7);
assert.equal(vm.runInContext("$('amplitude-source-label').hidden",context),true);
assert.equal(vm.runInContext("$('test-rule-info').hidden",context),false);
vm.runInContext("$('display-types-all').onclick()",context);
assert.deepEqual(JSON.parse(vm.runInContext("JSON.stringify(visibleEvents([{id:1,source:'detector',detection_profile:'adaptive_test',detection_version:'adaptive-flow-test-v2-causal',type:'测试减弱候选',status:'candidate'},{id:2,source:'detector',type:'数据缺失',status:'candidate'}]).map(e=>e.id))",context)),[1]);
vm.runInContext("$('adaptive-version').value='adaptive-flow-test-v2-causal'",context);
assert.deepEqual(JSON.parse(vm.runInContext("JSON.stringify(visibleEvents([{id:'old',source:'detector',detection_profile:'adaptive_test',type:'测试减弱候选',status:'candidate',note:'adaptive-flow-test-v1'},{id:'new',source:'detector',detection_profile:'adaptive_test',detection_version:'adaptive-flow-test-v2-causal',type:'测试减弱候选',status:'candidate'}]).map(e=>e.id))",context)),['new']);
vm.runInContext(`meta=null;$('scan-profile').value='adaptive_multifeature';$('scan-profile').onchange();$('display-types-all').onclick();
const versions=[{id:'v2',source:'detector',detection_profile:'adaptive_test',detection_version:'adaptive-flow-test-v2-causal',type:'测试近消失候选',status:'candidate'},
{id:'v3',source:'detector',detection_profile:'adaptive_multifeature',detection_version:'adaptive-flow-test-v3-multifeature',type:'测试近消失候选',status:'candidate'}];`,context);
assert.equal(vm.runInContext('activeResultGroup',context),'adaptive');
assert.equal(vm.runInContext("$('multi-rule-info').hidden",context),false);
assert.equal(vm.runInContext("$('scan-duration').value",context),7);
assert.equal(vm.runInContext("$('scan-ratio').value",context),50);
assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(visibleEvents(versions).map(e=>e.id))',context)),['v3']);
vm.runInContext("$('adaptive-version').value='all'",context);
assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(visibleEvents(versions).map(e=>e.id))',context)),['v3']);
vm.runInContext("selected=['多特征参考B'];const keyBefore=selectionKey();$('scan-ratio').value=70",context);
assert.equal(vm.runInContext('keyBefore===selectionKey()',context),false,'reference traces must not reuse stale parameters');
console.log('PASS: independent v2/v3 filters, strict profile defaults and parameter-specific trace cache');
