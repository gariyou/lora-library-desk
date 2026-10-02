const {test}=require('node:test');
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const root=process.env.LORA_ROOT || path.resolve(__dirname,'..');
function fixture(){
  const url='https://civitai.red/models/123?modelVersionId=456';
  const sender={tab:{id:7},frameId:0,url};
  const saved={},posts=[],searches=[],listeners=[];
  let pending=null,offline=false,items=[],extracts=0,tabUrl=url;
  let data={source_url:url,title:'Auto fixture',model_family_hint:'lora',download_candidates:[{url:'https://civitai.red/api/download/models/456',suggested_filename:'fixture.safetensors'}]};
  const listener={addListener(){}};
  const context=vm.createContext({importScripts(){},extractPageMetadata(){},AbortSignal,URL,Date,console,setTimeout,clearTimeout,crypto:require('node:crypto').webcrypto,
    chrome:{runtime:{onMessage:{addListener:f=>listeners.push(f)}},alarms:{onAlarm:listener,create:async()=>{}},
      tabs:{get:async()=>({active:true,url:tabUrl})},scripting:{executeScript:async()=>{extracts++;return [{result:data}]}},
      storage:{sync:{get:async d=>d},session:{get:async()=>saved,set:async x=>Object.assign(saved,x)},local:{set:async()=>{}}},
      downloads:{onChanged:listener,search:async q=>{searches.push(q);return items},download:async()=>{throw Error('must not start a download')}}},
    fetch:async(url,options)=>{
      if(offline)throw Error('offline');
      if(url.endsWith('/await-download-import')){const body=JSON.parse(options.body);posts.push(body);pending={...body,source_url:body.metadata.source_url,created_epoch:Date.now()/1000};}
      if(url.endsWith('/complete-pending-download'))posts.push(JSON.parse(options.body));
      return {ok:true,text:async()=>JSON.stringify({pending})};
    }
  });
  vm.runInContext(fs.readFileSync(path.join(root,'chrome_extension/background.js'),'utf8'),context);
  const run=()=>{context.message={url};context.sender=sender;return vm.runInContext('autoWaitForPage(message,sender)',context)};
  return {run,context,posts,searches,sender,listeners,url,setOffline:x=>offline=x,setData:x=>data=x,setPending:x=>pending=x,setItems:x=>items=x,extracts:()=>extracts,setTabUrl:x=>tabUrl=x};
}
test('page automatically arms once; cancel and worker-side dedupe prevent rearming',async()=>{
  const f=fixture();await new Promise(r=>setImmediate(r));assert.equal((await f.run()).handled,true);
  assert.equal(f.posts.length,1);assert.equal(f.posts[0].auto_triggered,true);assert.equal(f.posts[0].expected_filename,'fixture.safetensors');
  f.setPending(null);assert.equal((await f.run()).handled,true);assert.equal(f.posts.length,1);
});
test('offline does not extract or launch; recovers when app comes online',async()=>{
  const f=fixture();await new Promise(r=>setImmediate(r));f.setOffline(true);await assert.rejects(f.run(),/offline/);assert.equal(f.extracts(),0);
  f.setOffline(false);assert.equal((await f.run()).handled,true);assert.equal(f.posts.length,1);
});
test('another wait stays intact; old tab does not queue takeover after completion',async()=>{
  const f=fixture();await new Promise(r=>setImmediate(r));f.setPending({created_epoch:1});assert.equal((await f.run()).handled,true);
  f.setPending(null);await f.run();assert.equal(f.posts.length,0);
});
test('missing candidates, stale page, non-model page and subframe cannot arm',async()=>{
  const f=fixture();await new Promise(r=>setImmediate(r));f.setTabUrl('https://civitai.red/models/999');assert.equal((await f.run()).handled,false);
  f.setTabUrl(f.url);f.setData({source_url:f.url,download_candidates:[]});assert.equal((await f.run()).handled,false);
  f.sender.frameId=1;assert.equal((await f.run()).handled,false);f.sender.frameId=0;f.sender.url='https://evil.test/models/123';assert.equal((await f.run()).handled,false);
  assert.equal(f.posts.length,0);
});
test('automatic wait ignores completed downloads from before page visit',async()=>{
  const f=fixture();await new Promise(r=>setImmediate(r));f.setItems([{state:'complete',exists:true,filename:'C:/Downloads/old.safetensors',url:'https://civitai.red/api/download/models/456',startTime:new Date(Date.now()-60000).toISOString()}]);
  await f.run();assert.equal(f.posts.length,1);assert.ok(Date.parse(f.searches.at(-1).startedAfter)>Date.now()-10000);
});
test('simultaneous pages cannot both start an automatic wait',async()=>{
  const f=fixture();await new Promise(r=>setImmediate(r));await Promise.all([f.run(),f.run()]);assert.equal(f.posts.length,1);
});
