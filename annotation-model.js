/* Shared validation and persistence for manual waveform annotations. */
(function(root){
 'use strict';
 const types=['疑似暂停','疑似低通气','漏气','波形异常','伪迹/干扰','其他','呼吸波动减弱','总漏气升高','数据缺失','信号异常','流量波动近消失候选','流量波动减弱候选'];
 class AnnotationModel{
  constructor(meta,storage){this.meta=meta;this.storage=storage;this.key='respiratory-events-v1:'+meta.file_id;this.items=[]}
  validate(event){
   if(!event||typeof event!=='object')throw Error('事件格式不正确');
   const start=event.start,end=event.end;
   if(typeof start!=='number'||typeof end!=='number'||!Number.isFinite(start)||!Number.isFinite(end)||start<0||end<=start)throw Error('结束时间必须晚于起始时间，起始时间不能小于0');
   if(!['raw','engineering'].includes(event.mode))throw Error('事件缺少有效的时间轴模式');
   const limit=event.mode==='engineering'?this.meta.display_duration:this.meta.raw_duration;
   if(end>limit+1e-6)throw Error('事件超出文件时间范围');
   if(!types.includes(event.type))throw Error('不支持的事件类型');
   if(!Array.isArray(event.channels)||!event.channels.length||event.channels.some(c=>!this.meta.channels.includes(c)))throw Error('请至少选择一个有效通道');
   if(typeof event.note!=='string'||event.note.length>2000)throw Error('备注最多2000字');
   if(typeof event.id!=='string'||!event.id||event.id.length>100)throw Error('事件编号无效');
   const source=event.source||'manual',status=event.status||(['agent','detector'].includes(source)?'candidate':'reviewed');
   if(!['manual','agent','detector'].includes(source)||!['candidate','reviewed','ignored'].includes(status))throw Error('事件来源或状态无效');
   return {id:event.id,start,end,type:event.type,note:event.note,channels:[...new Set(event.channels)],mode:event.mode,source,status,updated_at:typeof event.updated_at==='string'?event.updated_at:new Date().toISOString()};
  }
  commit(items){const checked=items.map(x=>this.validate(x));if(new Set(checked.map(x=>x.id)).size!==checked.length)throw Error('事件编号重复');this.storage.setItem(this.key,JSON.stringify({version:1,file_id:this.meta.file_id,events:checked}));this.items=checked}
  load(){const raw=this.storage.getItem(this.key);if(!raw)return;const packet=JSON.parse(raw);if(packet.version!==1||packet.file_id!==this.meta.file_id||!Array.isArray(packet.events))throw Error('保存的标记文件格式不兼容');const checked=packet.events.map(e=>this.validate(e));if(new Set(checked.map(e=>e.id)).size!==checked.length)throw Error('保存的事件编号重复');this.items=checked}
  save(event){const item=this.validate(event),items=this.items.filter(x=>x.id!==item.id);items.push(item);this.commit(items)}
  remove(id){this.commit(this.items.filter(x=>x.id!==id))}
  export(){return {version:1,file_id:this.meta.file_id,recording:{name:this.meta.name,start:this.meta.start,rows:this.meta.rows},events:this.items}}
  import(packet){if(packet?.version!==1||packet.file_id!==this.meta.file_id||!Array.isArray(packet.events))throw Error('请导入同一份 CSV 导出的事件 JSON 文件');const merged=new Map(this.items.map(x=>[x.id,x]));for(const event of packet.events){const valid=this.validate(event);if(merged.has(valid.id)&&JSON.stringify(merged.get(valid.id))!==JSON.stringify(valid))throw Error('同编号事件内容冲突，请先修改或删除对应事件');merged.set(valid.id,valid)}this.commit([...merged.values()])}
 }
 root.AnnotationModel=AnnotationModel;root.annotationTypes=types;
 function compareReviewed(packet,meta){
  if(packet?.kind!=='reviewed'||![1,2].includes(packet.version)||packet.file_id!==meta.file_id||packet.automatic?.file_id!==meta.file_id||packet.run_id!==packet.automatic.run_id||!Array.isArray(packet.events)||!Array.isArray(packet.automatic.events))throw Error('请选择与当前CSV匹配的人工校验JSON');
  const model=new AnnotationModel(meta,{setItem(){}}),final=packet.events.map(e=>model.validate(e)),auto=packet.automatic.events.map(e=>model.validate(e));
  const current=new Map(final.map(e=>[e.id,e])),links=packet.automatic.event_links||{},linked=new Set(),rows=[];
  for(const original of auto){const id=links[original.id]||original.id;linked.add(id);const e=current.get(id);let result;
   if(!e)result='已删除（不能自动认定误报）';
   else if(e.status==='ignored')result='已忽略 / 误报候选';
   else if(e.status==='candidate')result='尚未确认';
   else result=[e.type!==original.type?'类型调整':'',e.start!==original.start||e.end!==original.end?'边界调整':''].filter(Boolean).join('、')||'已确认一致';
   const range=packet.reviewed_range;if(!Array.isArray(range)||original.start<range[0]||original.end>range[1])result='已检查范围外 · '+result;
   rows.push({original,event:e||original,result});
  }
  for(const e of final)if(!linked.has(e.id)&&e.source==='manual')rows.push({original:null,event:e,result:'人工新增 / 漏检复核线索'});
  return rows;
 }
 root.compareReviewed=compareReviewed;
 if(typeof module!=='undefined')module.exports={AnnotationModel,types,compareReviewed};
})(globalThis);
