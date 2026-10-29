const assert=require('node:assert/strict'),vm=require('node:vm'),{readFileSync}=require('node:fs');
const elements=new Map();
function element(){return {value:'',textContent:'',clientWidth:1000,children:[],addEventListener(){},replaceChildren(){this.children=[]},append(...items){this.children.push(...items)}}}
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
assert.match(readFileSync('viewer.js','utf8'),/const events=visibleEvents\(annotations\?\.items\|\|\[\]\)/,'waveform bands must use the same type/status filter as the list');
console.log('PASS: type/status intersection, all/none selection, profile switching, hidden events preserved and unscanned metrics');
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
