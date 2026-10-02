const {chromium}=require('playwright');
const {spawn}=require('child_process');
const fs=require('fs'),os=require('os'),path=require('path'),assert=require('assert/strict');
(async()=>{
 const kind=process.env.E2E_KIND||'lora';const cdn=process.env.E2E_CDN||'b2';const suffix=kind==='workflow'?'json':'safetensors';
 const profile=fs.mkdtempSync(path.join(os.tmpdir(),'lora-extension-e2e-'));
 const proc=spawn('python',['-X','utf8',path.join(__dirname,'bridge_e2e_server.py')]);
 let context;
 try{
  const port=await new Promise((resolve,reject)=>{proc.stdout.once('data',b=>resolve(Number(b.toString().trim())));proc.on('error',reject)});
  const baseUrl=`http://127.0.0.1:${port}`;
  const extension=path.resolve(__dirname,'../chrome_extension');
  context=await chromium.launchPersistentContext(profile,{headless:true,channel:'chromium',acceptDownloads:true,args:[`--disable-extensions-except=${extension}`,`--load-extension=${extension}`]});
  const worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker');
  await worker.evaluate(baseUrl=>chrome.storage.sync.set({loraManagerBaseUrl:baseUrl}),baseUrl);
  const alarm=await worker.evaluate(()=>chrome.alarms.get('lora-manager-pending-download'));assert.ok(alarm);
  const source='https://civitai.red/models/2935378/fixture';
  const objectKey=`model/3039728/fixture.QIIP.${suffix}`;
  const asset=`https://s3.us-west-004.backblazeb2.com/civitai-modelfiles/${objectKey}`;
  if(process.env.E2E_AUTO==='1') {
    const modelPage=await context.newPage();
    await context.route('https://civitai.red/**', route => {
      const url=new URL(route.request().url());
      if(url.pathname==='/api/trpc/model.getById') return route.fulfill({json:{result:{data:{json:{name:'Automatic fixture',type:kind==='workflow'?'Workflows':kind==='checkpoint'?'Checkpoint':'LORA',modelVersions:[{id:3343038,files:[{name:`Expected.${suffix}`,downloadUrl:asset,type:'Model'}]}]}}}}});
      if(url.pathname.startsWith('/api/')) return route.fulfill({status:404,body:'{}'});
      return route.fulfill({contentType:'text/html',body:'<!doctype html><h1>Automatic fixture</h1>'});
    });
    await modelPage.goto(source);
    const deadline=Date.now()+15000;
    let state;
    do { await new Promise(r=>setTimeout(r,200)); state=await(await fetch(baseUrl+'/api/lora/pending-download')).json(); } while(!state.pending && Date.now()<deadline);
    assert.equal(state.pending?.auto_triggered,true,JSON.stringify(state));
    assert.equal(state.pending.metadata.download_url,asset);
    const epoch=state.pending.created_epoch;
    await modelPage.reload();
    await modelPage.waitForTimeout(700);
    state=await(await fetch(baseUrl+'/api/lora/pending-download')).json();
    assert.equal(state.pending.created_epoch,epoch);
    await context.unroute('https://civitai.red/**');
    await modelPage.close();
    console.log('PASS page opening automatically armed real MV3 extension without popup; reload preserved wait');
  } else {
  const result=await worker.evaluate(({baseUrl,source,asset,kind,suffix})=>runDownloadAndImport({baseUrl,pageData:{title:'Fixture',source_url:source,model_family_hint:kind},downloadUrl:asset,selectedCandidate:{suggested_filename:`Expected.${suffix}`}}),{baseUrl,source,asset,kind,suffix});
  assert.equal(result.pending,true);
  }
  const header=Buffer.from(JSON.stringify({'__metadata__':{'modelspec.title':'E2E fixture'}}));const n=Buffer.alloc(8);n.writeBigUInt64LE(BigInt(header.length));const bytes=kind==='workflow'?Buffer.from(JSON.stringify({version:0.4,nodes:[{id:1,type:'KSampler'}],links:[]})):Buffer.concat([n,header,Buffer.alloc(16)]);
  const downloadApi='https://civitai.red/api/download/models/3343038';
  const delivery=cdn==='b2'?`https://b2.civitai.com/file/civitai-modelfiles/${objectKey}`:cdn==='r2'?`https://civitai-delivery-worker-prod.fixture.r2.cloudflarestorage.com/${objectKey}`:asset;
  const page=await context.newPage();
  const downloadDir=path.join(profile,'Downloads');fs.mkdirSync(downloadDir);
  const cdp=await context.newCDPSession(page);
  await cdp.send('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:downloadDir,eventsEnabled:true});
  // Intercept every redirect hop; never contact the real distribution service.
  cdp.on('Fetch.requestPaused',async ({requestId,request})=>{
   if(request.url===downloadApi) return cdp.send('Fetch.fulfillRequest',{requestId,responseCode:302,responseHeaders:[{name:'Location',value:delivery}]});
   if(request.url===delivery) return cdp.send('Fetch.fulfillRequest',{requestId,responseCode:200,responseHeaders:[{name:'Content-Type',value:'application/octet-stream'},{name:'Content-Disposition',value:`attachment; filename="downloaded_${kind}.${suffix}"`}],body:bytes.toString('base64')});
   return cdp.send('Fetch.failRequest',{requestId,errorReason:'BlockedByClient'});
  });
  await cdp.send('Fetch.enable',{patterns:[{urlPattern:'*'}]});
  const got=page.waitForEvent('download');await page.goto(downloadApi).catch(()=>{});const dl=await got;assert.equal(await dl.failure(),null);
  const deadline=Date.now()+15000;let status;
  do{await new Promise(r=>setTimeout(r,300));status=await(await fetch(baseUrl+'/api/lora/pending-download')).json();}while(status.last_result.status!=='complete'&&Date.now()<deadline);
  if(process.env.EXPECT_B2_FAILURE==='1'){assert.equal(status.last_result.status,'waiting');console.log('BASELINE reproduced: selected S3 asset does not match API -> b2 download');return;}
  if(status.last_result.status!=='complete') console.log('diagnostic',await worker.evaluate(async()=>({downloads:await chrome.downloads.search({limit:5}),job:await chrome.storage.local.get('loraManagerBridgeJob')})));
  assert.equal(status.last_result.status,'complete',JSON.stringify(status.last_result));
  const moved=fs.readFileSync(status.last_result.path);if(kind==='workflow')assert.deepEqual(JSON.parse(moved),JSON.parse(bytes));else assert.deepEqual(moved,bytes);
  const expectedFolder=kind==='workflow'?'workflows':kind==='checkpoint'?'StableDiffusion':'Lora';assert.equal(path.basename(path.dirname(status.last_result.path)),expectedFolder);
  const after=await worker.evaluate(()=>chrome.downloads.search({limit:1}));assert.ok(!fs.existsSync(after[0].filename),'source moved');
  let library; const scanDeadline=Date.now()+10000;
  do {
    library=await(await fetch(baseUrl+'/api/lora/library')).json();
    if(library.items.some(x=>x.path===status.last_result.path)) break;
    await new Promise(r=>setTimeout(r,200));
  } while(Date.now()<scanDeadline);
  assert.ok(library.items.some(x=>x.path===status.last_result.path),JSON.stringify(library));
  console.log(`PASS real MV3 extension ${kind}/${cdn}: alarm; wait armed; API redirect; renamed download; onChanged; HTTP import; correct folder; verified content; Library registration`);
 }finally{
  await context?.close();
  if(proc.exitCode===null) await new Promise(resolve=>{
   const timer=setTimeout(()=>proc.kill(),5000);
   proc.once('exit',()=>{clearTimeout(timer);resolve()});proc.stdin.end('\n');
  });
 }
})().catch(e=>{console.error(e);process.exitCode=1});
