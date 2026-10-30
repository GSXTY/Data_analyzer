const assert=require('node:assert/strict'),vm=require('node:vm'),{readFileSync}=require('node:fs');
const elements=new Map(),storage=new Map();
function element(){return {value:'',textContent:'',clientWidth:1000,children:[],dataset:{},getContext(){return {clearRect(){}}},addEventListener(){},setAttribute(){},replaceChildren(){this.children=[]},append(...items){this.children.push(...items)},querySelectorAll(){return []}}}
const context=vm.createContext({document:{getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id)},createElement:element,createTextNode:text=>({textContent:text})},localStorage:{setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k)||null},window:{addEventListener(){}},URLSearchParams,console,fetch(){return new Promise(()=>{})},requestAnimationFrame(){return 1},setTimeout(){return 1},clearTimeout(){},AbortController,devicePixelRatio:1});
vm.runInContext(readFileSync('viewer.js','utf8'),context);
vm.runInContext(`
meta={file_id:'a',channels:['flow']};selected=['flow'];groups=[['flow']];view=[10,20];displayMode='raw';rememberFileView();
meta={file_id:'b',channels:['flow']};view=[30,50];rememberFileView();
showModeInfo=()=>{};filterChannels=()=>{};loadOverview=()=>{};setView=(a,b)=>{view=[a,b]};
meta={file_id:'a',channels:['flow']};restoreFileView();
`,context);
assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(view)',context)),[10,20]);
vm.runInContext("meta={file_id:'b',channels:['flow']};restoreFileView()",context);
assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(view)',context)),[30,50]);
vm.runInContext("fileLibrary=[{id:1,name:'one.csv',file_id:'a'},{id:2,name:'two.csv',file_id:'b'}];activeLibraryId=2;renderFileLibrary()",context);
assert.equal(elements.get('file-list').children.length,2);
elements.get('file-search').value='two';vm.runInContext('renderFileLibrary()',context);
assert.equal(elements.get('file-list').children.length,1);
assert.ok(readFileSync('index.html','utf8').includes('accept=".csv" multiple'));
console.log('PASS: independent view preferences, file list selection/search and multi-select input');
(async()=>{
 await vm.runInContext(`(async()=>{
 api=async()=>({released:true});$('file-search').value='';fileLibrary=[{id:1,name:'one.csv',file_id:'a',file:{}},{id:2,name:'two.csv',file_id:'b',file:{}},{id:3,name:'three.csv',file_id:'c',file:{}}];activeLibraryId=1;
 const closed=fileLibrary[1];await closeLibraryFile(2);if(fileLibrary.length!==2||closed.file!==null)throw Error('Inactive file must be removed and reference released');
 switchLibraryFile=async()=>{};await closeLibraryFile(1);if(fileLibrary.length!==2)throw Error('Failed switch must not close active file');
 switchLibraryFile=async id=>{activeLibraryId=id};await closeLibraryFile(1);if(activeLibraryId!==3||fileLibrary.length!==1)throw Error('Active close must select remaining file');
 meta={file_id:'c',channels:['flow']};api=async url=>{if(!['/api/close','/api/cache/release'].includes(url))throw Error('Unexpected mutation');return {closed:true}};
 await closeLibraryFile(3);if(meta!==null||annotations!==null||fileLibrary.length!==0)throw Error('Final close must clear the view');
 })()`,context);
 assert.ok(storage.has('respiratory-view-v1:c'),'closing last file must retain its view preferences');
 console.log('PASS: inactive close, active fallback, failure retention, final close and released file references');
})().catch(e=>{console.error(e);process.exitCode=1});
