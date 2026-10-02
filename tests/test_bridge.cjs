// Run: node --test tests/test_bridge.cjs (Playwright must be available).
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'chrome_extension/page_metadata.js'), 'utf8');
const extractor = source.slice(source.indexOf('async function extractPageMetadata('));
let browser;
before(async () => { browser = await chromium.launch({headless:true,
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? {executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE} : {})}); });
after(async () => {await browser?.close();});
async function fixture(t, {url='https://civitai.com/models/123', html='', api={}}={}) {
  const context = await browser.newContext();
  t.after(() => context.close());
  const page = await context.newPage();
  // Intercept all traffic: no production API, download or user data is touched.
  await context.route('**/*', route => {
    const requestUrl = route.request().url();
    if (requestUrl.includes('/api/trpc/')) {
      const method = new URL(requestUrl).pathname.split('/').pop();
      return route.fulfill({status:api[method] ? 200 : 503, contentType:'application/json',
        body:JSON.stringify({result:{data:{json:api[method] || {}}}})});
    }
    return route.fulfill({contentType:'text/html',body:`<!doctype html><html><head><title>Fixture LoRA</title></head><body>${html}</body></html>`});
  });
  await page.goto(url);
  // Standalone serialized function, as passed to chrome.scripting.executeScript.
  const extract = () => page.evaluate(`(${extractor})()`);
  return {page,context,extract};
}
test('model API failure with no video still returns metadata and download', async t => {
  const {extract} = await fixture(t,{html:'<h1>Test LoRA</h1><a href="/api/download/models/456">Download</a>'});
  const result = await extract();
  assert.equal(result.title,'Test LoRA');
  assert.equal(result.download_candidates[0].url,'https://civitai.com/api/download/models/456');
  assert.equal(result.preview_video_url,'');
});
test('selected model version, family and filename survive extraction', async t => {
  const {extract} = await fixture(t,{url:'https://civitai.com/models/123?modelVersionId=456',api:{
    'model.getById':{name:'API LoRA',type:'LORA',description:'<b>Description</b>',creator:{username:'Creator'},
      modelVersions:[{id:111,files:[]},{id:456,baseModel:'Anima',files:[{name:'test.safetensors',downloadUrl:'/api/download/models/456',type:'Model'}]}]}
  }});
  const result = await extract();
  assert.equal(result.title,'API LoRA');
  assert.equal(result.model_family_hint,'lora');
  assert.equal(result.base_model,'Anima');
  assert.equal(result.description,'Description');
  assert.equal(result.download_candidates[0].suggested_filename,'test.safetensors');
});
test('relative video URL and HTML entities normalize correctly', async t => {
  const {extract} = await fixture(t,{html:'<meta property="og:video" content="/preview.mp4?a=1&amp;b=2">'});
  const result = await extract();
  assert.equal(result.preview_video_url,'https://civitai.com/preview.mp4?a=1&b=2');
  assert.equal(result.preview_media_kind,'video');
});
test('video source element is collected', async t => {
  const {extract} = await fixture(t,{html:'<video><source src="/sample.webm"></video>'});
  assert.equal((await extract()).preview_video_url,'https://civitai.com/sample.webm');
});
test('civitai.red image fallback survives API failure', async t => {
  const {extract} = await fixture(t,{url:'https://civitai.red/images/123',html:'<meta property="og:image" content="/preview.jpg">'});
  const result = await extract();
  assert.equal(result.preview_image_url,'https://civitai.red/preview.jpg');
  assert.equal(result.preview_media_kind,'image');
});
test('image API prompt, resources and media are preserved', async t => {
  const {extract} = await fixture(t,{url:'https://civitai.com/images/123',api:{
    'image.get':{url:'asset-id',name:'sample.jpg',type:'image',mimeType:'image/jpeg'},
    'image.getGenerationData':{meta:{prompt:'test prompt',negativePrompt:'test negative',resources:[{modelName:'Test LoRA',modelId:123,modelVersionId:456,trainedWords:'testtrigger'}]}}
  }});
  const result = await extract();
  assert.equal(result.prompt,'test prompt');
  assert.equal(result.resources_used[0].model_id,'123');
  assert.deepEqual(result.triggers,['testtrigger']);
  assert.equal(result.preview_media_kind,'image');
});
test('generic HTTPS works while data and javascript media are rejected', async t => {
  const {extract} = await fixture(t,{url:'https://example.test/model',html:'<meta property="og:video" content="javascript:void(0)"><meta property="og:image" content="data:image/png;base64,AA">'});
  const result = await extract();
  assert.equal(result.title,'Fixture LoRA');
  assert.equal(result.preview_video_url,'');
  assert.equal(result.preview_image_url,'');
});
test('popup renders extraction and sends the same page payload', async t => {
  const {extract} = await fixture(t,{html:'<h1>Popup test LoRA</h1><a href="/api/download/models/456">Download</a>'});
  const result = await extract();
  const context = await browser.newContext();
  t.after(() => context.close());
  const popup = await context.newPage();
  let sent;
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/lora/browser-imports') {
      sent = route.request().postDataJSON();
      return route.fulfill({contentType:'application/json',body:'{"ok":true}'});
    }
    if (url.pathname === '/api/lora/config') return route.fulfill({contentType:'application/json',body:JSON.stringify({watch_dirs:['C:/test/Lora']})});
    if (url.pathname.startsWith('/api/')) return route.fulfill({body:'{}'});
  if(url.pathname==='/api/lora/recover-downloads') return route.fulfill({json:{status:'idle',results:[]}});
    const name = path.basename(url.pathname) || 'popup.html';
    return route.fulfill({body:fs.readFileSync(path.join(root,'chrome_extension',name)),contentType:name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html'});
  });
  await popup.addInitScript(data => {
    window.chrome = {
      tabs:{query:async()=>[{id:7,url:data.source_url}]},
      scripting:{executeScript:async()=>[{result:data}]},
      storage:{onChanged:{addListener(){}},sync:{get:async defaults=>defaults,set:async()=>{}},local:{get:async defaults=>defaults}}
    };
  },result);
  await popup.goto('http://127.0.0.1:8787/popup.html');
  await popup.waitForFunction(()=>document.querySelector('#page-title').textContent==='Popup test LoRA');
  assert.equal(await popup.locator('#download-pill').innerText(),'1件');
  await popup.locator('#send-import').click();
  await popup.waitForFunction(()=>document.querySelector('#status-line').textContent.includes('送信しました'));
  assert.equal(sent.source_url,result.source_url);
  assert.equal(sent.title,result.title);
  assert.equal(sent.download_candidates.length,1);
});
test('Workflow API preserves both ZIP variants and classifies Workflows', async t => {
  const {extract} = await fixture(t,{url:'https://civitai.com/models/2426853/anima-workflows',api:{
    'model.getById':{name:'Anima Workflows',type:'Workflows',modelVersions:[{id:3326453,baseModel:'Anima',files:[
      {name:'animaWorkflows_v90_3212228.zip',downloadUrl:'/api/download/models/3326453?fileId=3212228',type:'Archive'},
      {name:'animaWorkflows_v90_3212234.zip',downloadUrl:'/api/download/models/3326453?fileId=3212234',type:'Archive'}]}]}
  }});
  const data = await extract();
  assert.equal(data.model_family_hint,'workflow');
  assert.equal(data.download_candidates.length,2);
  assert.equal(data.download_candidates[1].suggested_filename,'animaWorkflows_v90_3212234.zip');
});

test('background preserves ZIP filename and arms manual import without starting download', async () => {
  const vm = require('node:vm');
  const posted=[];
  const listener={addListener(){},removeListener(){}};
  const context=vm.createContext({importScripts(){},AbortSignal,URL,console,setTimeout,clearTimeout,Date,crypto:require('node:crypto').webcrypto,
    chrome:{alarms:{onAlarm:listener,create:async()=>{}},runtime:{onMessage:listener},tabs:{onUpdated:listener,onActivated:listener,onRemoved:listener,query:async()=>[]},
      storage:{sync:{get:async defaults=>defaults},local:{set:async()=>{}}},
      downloads:{onChanged:listener,search:async()=>[],download:async()=>{throw new Error('Civitai must remain manual');}}},
    fetch:async(url,options)=>{posted.push({url,body:JSON.parse(options.body)}); return {ok:true,text:async()=>JSON.stringify({ok:true,pending:{downloads_dir:'C:/Downloads'}})};}
  });
  vm.runInContext(fs.readFileSync(path.join(root,'chrome_extension/background.js'),'utf8'),context);
  context.request={baseUrl:'http://127.0.0.1:8787',downloadUrl:'https://civitai.com/api/download/models/3326453?fileId=3212234',
    selectedCandidate:{suggested_filename:'animaWorkflows_v90_3212234.zip'},pageData:{source_url:'https://civitai.com/models/2426853/anima-workflows',source_host:'civitai.com',title:'Anima Workflows',model_family_hint:'workflow'}};
  const result=await vm.runInContext('runDownloadAndImport(request)',context);
  assert.equal(result.pending,true);
  assert.equal(posted[0].body.expected_filename,'animaWorkflows_v90_3212234.zip');
  assert.equal(posted[0].body.metadata.model_family_hint,'workflow');
});
test('selected version trainedWords override adjacent AIR metadata', async t => {
 const {extract}=await fixture(t,{url:'https://civitai.red/models/123?modelVersionId=456',html:'<span>Trigger Words</span><div><span>AIR</span><code>civitai:123@456+789</code></div>',api:{'model.getById':{name:'Model',modelVersions:[{id:111,trainedWords:['wrong version'],files:[]},{id:456,trainedWords:['body writing'],files:[]}]}}});
 assert.deepEqual((await extract()).triggers,['body writing']);
});
test('missing explicit version never downloads the latest version instead',async t=>{
 const {extract}=await fixture(t,{url:'https://civitai.red/models/123?modelVersionId=999',html:'<a href="/api/download/models/111">Download</a>',api:{'model.getById':{name:'Model',modelVersions:[{id:111,files:[{name:'wrong.safetensors',downloadUrl:'/api/download/models/111'}]}]}}});
 assert.deepEqual((await extract()).download_candidates,[]);
});
test('API-empty trainedWords stays empty and does not absorb AIR identifiers',async t=>{
 const {extract}=await fixture(t,{html:'<span>Trigger Words</span><div><code>civitai:123@456</code></div>',api:{'model.getById':{name:'Model',modelVersions:[{id:456,trainedWords:[],files:[]}]}}});
 assert.deepEqual((await extract()).triggers,[]);
});
test('DOM fallback reads only adjacent trigger values, excluding AIR panels',async t=>{
 const good=await fixture(t,{html:'<div><span>Trigger Words</span><div><button>body writing</button></div></div><div>AIR <code>civitai:123@456</code></div>'});
 assert.deepEqual((await good.extract()).triggers,['body writing']);
 const bad=await fixture(t,{html:'<span>Trigger Words</span><div><span>AIR</span><code>civitai:123@456</code></div>'});
 assert.deepEqual((await bad.extract()).triggers,[]);
});
