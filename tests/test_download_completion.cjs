const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
function fixture() {
 const calls=[], jobs=[], changed=[], alarms=[];
 const listener={addListener(){},removeListener(){}};
 const pending={created_epoch:Date.now()/1000,auto_triggered:false,source_url:'https://civitai.red/models/1',metadata:{title:'DeMix'}};
 const item={id:1,state:'complete',exists:true,filename:'C:/Downloads/demix_v1.safetensors',url:'https://civitai.red/api/download/models/3335061',finalUrl:'https://civitai-delivery-worker-prod.example.r2.cloudflarestorage.com/model/11814572/demix.4Htp.safetensors'};
 let live=null, failNext=false, customItems=null, customResult=null;
 const context=vm.createContext({importScripts(){},AbortSignal,URL,Date,console,setTimeout,clearTimeout,crypto:require('node:crypto').webcrypto,
 chrome:{alarms:{onAlarm:{addListener:f=>alarms.push(f)},create:async()=>{}},runtime:{onMessage:listener},tabs:{onUpdated:listener,onActivated:listener,onRemoved:listener,query:async()=>[]},
 storage:{sync:{get:async d=>d},local:{set:async j=>jobs.push(j)}},
 downloads:{onChanged:{addListener:f=>changed.push(f)},search:async q=>{calls.push({search:q});return customItems || [item,{...item,state:'interrupted'},{...item,filename:'C:/Downloads/unconfirmed.crdownload'}];}}},
 fetch:async(url,options)=>{const body=options?.body ? JSON.parse(options.body) : null;calls.push({url,body});
 if(url.endsWith('/await-download-import'))live=pending;
 if(url.endsWith('/complete-pending-download')&&failNext){failNext=false;throw new Error('temporary connection failure');}
 const data=url.endsWith('/complete-pending-download')?(customResult ? customResult(body) : {matched:true,filename:'demix_v1.safetensors',path:'C:/Models/demix_v1.safetensors',target_dir:'C:/Models'}):{pending:live};
 return {ok:true,text:async()=>JSON.stringify(data)};}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../chrome_extension/background.js'),'utf8'),context);
 return {context,calls,jobs,changed,alarms,setItems:x=>{customItems=x},setResult:x=>{customResult=x},failNext:()=>{failNext=true},setPending:()=>{live=pending}};
}
test('manual arm recovers existing renamed file by actual URL and path',async()=>{
 const f=fixture();
 f.context.request={baseUrl:'http://127.0.0.1:8787',downloadUrl:'https://civitai.red/api/download/models/3335061',pageData:{source_url:'https://civitai.red/models/1',title:'DeMix'},selectedCandidate:{suggested_filename:'DeMix.safetensors'}};
 const result=await vm.runInContext('runDownloadAndImport(request)',f.context);
 assert.equal(result.filename,'demix_v1.safetensors');
 const posted=f.calls.find(c=>c.url?.endsWith('/complete-pending-download'));
 assert.equal(posted.body.downloads.length,1);
 assert.equal(posted.body.downloads[0].source_path,'C:/Downloads/demix_v1.safetensors');
 assert.equal(f.jobs.at(-1).loraManagerBridgeJob.status,'complete');
});
test('persistent completion listener works with fresh worker and no activeJobId',async()=>{
 const f=fixture();await new Promise(r=>setImmediate(r));f.setPending();
 assert.equal(f.changed.length,1);
 f.changed[0]({id:1,state:{current:'complete'}});
 await new Promise(r=>setImmediate(r));
 assert.ok(f.calls.some(c=>c.url?.endsWith('/complete-pending-download')));
 assert.equal(f.jobs.at(-1).loraManagerBridgeJob.status,'complete');
});

test('page-only URL cannot create an unmatchable wait',async()=>{
 const f=fixture();
 f.context.request={baseUrl:'http://127.0.0.1:8787',downloadUrl:'https://civitai.red/models/1',pageData:{source_url:'https://civitai.red/models/1'}};
 await assert.rejects(vm.runInContext('runDownloadAndImport(request)',f.context),/取得URL/);
 assert.equal(f.calls.filter(c=>c.url?.endsWith('/await-download-import')).length,0);
});
test('alarm retries completion after a transient server failure',async()=>{
 const f=fixture();await new Promise(r=>setImmediate(r));f.setPending();f.failNext();
 f.changed[0]({id:1,state:{current:'complete'}});
 await new Promise(r=>setImmediate(r));
 assert.equal(f.jobs.length,0);
 f.alarms[0]({name:'lora-manager-pending-download'});
 await new Promise(r=>setImmediate(r));
 assert.equal(f.jobs.at(-1).loraManagerBridgeJob.status,'complete');
});

test('completed matching file beyond first 100 history entries is imported',async()=>{
 const f=fixture();await new Promise(r=>setImmediate(r));f.setPending();
 f.setItems(Array.from({length:101},(_,i)=>({state:'complete',exists:true,filename:`C:/Downloads/${i}.safetensors`,url:'https://example.test/'+i})));
 f.setResult(body=>body.downloads.some(x=>x.source_path.endsWith('/100.safetensors'))?{matched:true,filename:'100.safetensors',path:'C:/Models/100.safetensors'}:{matched:false});
 await vm.runInContext('reconcileCompletedDownloads()',f.context);
 assert.equal(f.calls.filter(c=>c.url?.endsWith('/complete-pending-download')).length,2);
 assert.equal(f.jobs.at(-1).loraManagerBridgeJob.status,'complete');
 assert.equal(f.calls.find(c=>c.search).search.limit,0);
});
test('Chrome interruption and server import error are displayed as errors',async()=>{
 const f=fixture();await new Promise(r=>setImmediate(r));f.setPending();
 f.setItems([{id:8,state:'interrupted',filename:'C:/Downloads/file.safetensors',url:'https://example.test/file'}]);
 f.setResult(()=>({matched:true,status:'error',message:'Download interrupted'}));
 f.changed[0]({id:8,state:{current:'interrupted'}});
 await new Promise(r=>setImmediate(r));
 assert.equal(f.calls.find(c=>c.search).search.id,8);
 assert.equal(f.jobs.at(-1).loraManagerBridgeJob.status,'error');
 assert.equal(f.calls.find(c=>c.url?.endsWith('/complete-pending-download')).body.downloads[0].download_state,'interrupted');
});
