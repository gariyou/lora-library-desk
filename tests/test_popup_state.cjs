const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const fs=require('fs'),path=require('path');
let browser;
before(async()=>{browser=await chromium.launch({headless:true})});
after(async()=>{await browser.close()});
async function fixture(t){
 const context=await browser.newContext();t.after(()=>context.close());
 const page=await context.newPage();let pending=null,last_result={},calls=0,failed=false,release=null,delay=false;
 const root=path.resolve(__dirname,'../chrome_extension');
 await context.route('**/*',async route=>{
  const u=new URL(route.request().url());
  if(u.pathname==='/api/lora/pending-download'){
   if(failed)return route.fulfill({status:503,json:{error:'offline'}});
   const snapshot={pending,last_result};
   if(delay){delay=false;await new Promise(r=>{release=r})}
   return route.fulfill({json:snapshot}).catch(()=>{});
  }
  if(u.pathname==='/api/lora/cancel-pending-download'){pending=null;last_result={status:'cancelled',message:'取り込み待機を解除しました。'};return route.fulfill({json:{ok:true,cancelled:true}})}
  if(u.pathname==='/api/lora/config')return route.fulfill({json:{watch_dirs:['C:/Models/Lora']}});
  if(u.pathname==='/api/lora/recover-downloads') return route.fulfill({json:{status:'idle',results:[]}});
  const name=path.basename(u.pathname)||'popup.html';return route.fulfill({body:fs.readFileSync(path.join(root,name)),contentType:name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html'});
 });
 await page.exposeFunction('bridgeArm',async()=>{calls++;pending={created_epoch:12345,expected_filename:'test.safetensors'};return {ok:true,result:{pending:true,message:'待機を開始しました'}}});
 await page.addInitScript(()=>{window.chrome={tabs:{query:async()=>[{id:7,url:'https://civitai.com/models/123'}]},scripting:{executeScript:async()=>[{result:{title:'Waiting check',source_url:'https://civitai.com/models/123',source_host:'civitai.com',model_family_hint:'lora',download_candidates:[{url:'https://civitai.com/api/download/models/456',suggested_filename:'test.safetensors'}]}}]},storage:{onChanged:{addListener(){}},sync:{get:async x=>x,set:async()=>{}},local:{get:async x=>x}},runtime:{sendMessage:async()=>window.bridgeArm()}}});
 return {page,open:()=>page.goto('http://127.0.0.1:8787/popup.html'),calls:()=>calls,setFailed:v=>{failed=v},delayNext:()=>{delay=true},release:()=>release?.(),released:()=>Boolean(release),finish:status=>{pending=null;last_result={status,filename:'test.safetensors',message:'fixture result'}}};
}
const enabled=page=>page.waitForFunction(()=>!document.querySelector('#download-import').disabled);
const waiting=page=>page.waitForFunction(()=>document.querySelector('#download-import').textContent==='待機中'&&document.querySelector('#download-import').disabled);
test('waiting blocks duplicates, survives reopening, cancellation unlocks',async t=>{
 const f=await fixture(t);await f.open();await enabled(f.page);
 await f.page.evaluate(()=>{let b=document.querySelector('#download-import');b.click();b.click()});
 await waiting(f.page);assert.equal(f.calls(),1);await f.page.reload();await waiting(f.page);
 await f.page.locator('#cancel-pending').click();await enabled(f.page);assert.equal(f.calls(),1);
 assert.equal(await f.page.locator('#cancel-pending').isVisible(),false);
});
test('completion and failure unlock waiting button',async t=>{
 const f=await fixture(t);await f.open();await enabled(f.page);
 for(const status of ['complete','error']){
  await f.page.locator('#download-import').click();await waiting(f.page);f.finish(status);
  await f.page.evaluate(()=>refreshPendingState());await enabled(f.page);
 }
 assert.equal(f.calls(),2);
});
test('unreachable server keeps state unknown and button disabled until recovery',async t=>{
 const f=await fixture(t);f.setFailed(true);await f.open();
 await f.page.waitForFunction(()=>document.querySelector('#status-line').textContent.includes('確認できません'));
 assert.equal(await f.page.locator('#download-import').isDisabled(),true);
 f.setFailed(false);await f.page.evaluate(()=>refreshPendingState());await enabled(f.page);
});
test('late pre-click poll cannot unlock an active wait',async t=>{
 const f=await fixture(t);await f.open();await enabled(f.page);f.delayNext();
 await f.page.evaluate(()=>{void refreshPendingState()});
 await f.page.locator('#download-import').click();await waiting(f.page);
 assert.equal(f.released(),true);f.release();
 await f.page.waitForFunction(()=>!state.pendingRefreshing);
 assert.equal(await f.page.locator('#download-import').isDisabled(),true);
 assert.equal(f.calls(),1);
});
