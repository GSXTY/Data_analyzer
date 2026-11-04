const assert=require('node:assert/strict'),vm=require('node:vm'),{readFileSync}=require('node:fs');
const elements=new Map(),saved=new Map();
function element(){return {value:'',textContent:'',clientWidth:1000,children:[],dataset:{},style:{},checked:false,addEventListener(){},setAttribute(){},replaceChildren(){this.children=[]},append(...v){this.children.push(...v)},querySelectorAll(){return []}}}
const context=vm.createContext({document:{getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id)},createElement:element,createTextNode:text=>({textContent:text})},localStorage:{getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v)},window:{addEventListener(){}},fetch(){return new Promise(()=>{})},URLSearchParams,AbortController,requestAnimationFrame(){return 1},setTimeout(){return 1},clearTimeout(){},console,devicePixelRatio:1});
vm.runInContext(readFileSync('viewer.js','utf8'),context);
vm.runInContext(`
meta={file_id:'a',start:'2026-01-01 00:00:00',raw_duration:200,dynamic_baseline_version:'dynamic-baseline-comparison-v1',channels:['气压1','气压2','控制压力','流量','漏气量','温度1','风机档位',...allDynamicNames,...multiNames,'测试幅度A（5秒鲁棒）','公司A（5秒极差）','公司公式Q（试验）','流量波动幅度A（5秒极差）']};
selected=['气压1','流量'];groups=[['气压1'],['流量']];
$('scan-profile').value='dynamic_baseline';$('baseline-method').value='centered_rms';$('baseline-window').value='5';
for(const name of meta.channels){const input={dataset:{name},checked:selected.includes(name)},label={hidden:false,querySelector:()=>input};$('channels').append(label)}
render=()=>{};loadWave=()=>{};showModeInfo=()=>{};loadOverview=()=>{};setView=(a,b)=>view=[a,b];
annotations={items:[{id:'manual',source:'manual',status:'reviewed',type:'其他',start:10,end:20,channels:['流量'],note:'keep'}]};
filterChannels();
`,context);
const read=code=>vm.runInContext(code,context),list=()=>JSON.parse(read("JSON.stringify($('channels').children.filter(l=>!l.hidden).map(l=>l.querySelector('input').dataset.name))"));
assert(list().includes('流量'));assert(list().includes('动态5秒强度'));
for(const hidden of ['温度1','风机档位','动态2秒强度','动态8秒强度','多特征参考B'])assert(!list().includes(hidden));
const before=read('JSON.stringify(annotations.items)');
read("$('show-company-a').onclick()");
assert.equal(read('JSON.stringify(annotations.items)'),before,'showing curves never saves events');
assert.equal(read('dynamicMethod()'),'centered_rms');
assert.equal(read("selected.includes('动态5秒参考')"),true);
read("$('baseline-window').value='8';$('baseline-window').onchange()");
assert.equal(read("selected.includes('动态5秒参考')"),false);assert.equal(read("selected.includes('动态8秒参考')"),true);
assert(!list().includes('动态5秒强度'));assert(list().includes('动态8秒强度'));
read("$('show-all-channels').checked=true;$('show-all-channels').onchange()");assert(list().includes('温度1'));
read("$('show-all-channels').checked=false;$('show-all-channels').onchange();$('channel-search').value='温度';$('channel-search').oninput()");assert.deepEqual(list(),['温度1'],'search can find optional raw channels without expanding the whole list');
read("$('channel-search').value='';$('channel-search').oninput();$('selected-only').checked=true;$('selected-only').onchange()");
assert(list().every(n=>JSON.parse(read('JSON.stringify(selected)')).includes(n)));
read("$('selected-only').checked=false;$('scan-profile').value='adaptive_multifeature';$('scan-profile').onchange()");
assert.equal(read('selected.some(n=>allDynamicNames.includes(n))'),false,'old algorithm curves are removed on scheme switch');
assert(list().includes('多特征参考B'));assert(!list().includes('动态8秒强度'));
read("$('scan-profile').value='device_candidates';selected=['流量'];view=[10,20];rememberFileView()");
const key='respiratory-view-v1:a';assert.equal(JSON.parse(saved.get(key)).channelScopeVersion,1);
saved.set(key,JSON.stringify({view:[20,40],selected:['流量','温度1','动态2秒强度'],groups:[['流量','温度1','动态2秒强度']]}));
read('restoreFileView()');assert.deepEqual(JSON.parse(read('JSON.stringify(selected)')),['流量'],'legacy all-channel preferences must not restore obsolete algorithm plots or unrelated fields');
const html=readFileSync('index.html','utf8');assert(!html.includes('id="dynamic-panel"'));assert(!html.includes('id="dynamic-method"'));assert(html.includes('id="show-all-channels"'));
assert(!readFileSync('viewer.js','utf8').includes('runDynamic'));
console.log('PASS: one algorithm entry, matching plots without preview controls, relevant channel defaults, optional search/all, scheme cleanup and legacy preference migration');
