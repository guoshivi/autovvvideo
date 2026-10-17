// Run explicitly: node --test tests/test_copy_rewrite_ui.cjs
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'..','static','app.js'),'utf8').split('/* ---------- init ---------- */')[0];

function fixture(){
  const nodes={
    '#copy':{value:'原稿 A'}, '#secScript':{innerHTML:''}, '#aiStatus':{textContent:''},
    '#aiOut':{hidden:true}, '#aiOutTitle':{textContent:''}, '#aiOutBody':{textContent:''},
    '#aiPanel':{hidden:true}, '#aiName':{value:'商品A'}, '#aiRewriteReason':{value:''},
    '#rewritePreview':{hidden:true}, '#aiRewritePreviewBody':{textContent:''},
    '#btnRewriteAdopt':{disabled:false}, '#btnRewriteUndo':{disabled:true}, '#rewriteLen':{textContent:''},
  };
  const ctx=vm.createContext({
    document:{querySelector:s=>nodes[s]||null,createElement:()=>({}),body:{appendChild(){}}},
    console,setTimeout,clearTimeout,AbortController,fetch:async()=>({ok:true,json:async()=>({})}),
  });
  vm.runInContext(source,ctx);
  vm.runInContext('toast=()=>{}',ctx);
  vm.runInContext("state.product={id:'p1',name:'商品A',copy:{text:'原稿 A'}};state.draft='原稿 A';",ctx);
  return {ctx,nodes};
}

test('script UI exposes AI rewrite entry and does not put result in textarea',()=>{
  const f=fixture();
  vm.runInContext('renderScript()',f.ctx);
  assert.match(f.nodes['#secScript'].innerHTML,/AI 洗文案/);
  assert.match(f.nodes['#secScript'].innerHTML,/仅优化文字，不保证平台审核结果/);
  assert.match(f.nodes['#secScript'].innerHTML,/拒审原因或改写要求/);
  assert.equal(f.nodes['#copy'].value,'原稿 A');
});

test('rewrite result can be adopted and undone only while draft is unchanged',()=>{
  const f=fixture();
  vm.runInContext("state.aiRewrite={productId:'p1',source:'原稿 A',result:'新稿 A'};state.aiOut={title:'AI 洗文案预览',text:'新稿 A'};renderAiOut();",f.ctx);
  vm.runInContext('adoptRewrite()',f.ctx);
  assert.equal(f.nodes['#copy'].value,'新稿 A');
  vm.runInContext('undoRewrite()',f.ctx);
  assert.equal(f.nodes['#copy'].value,'原稿 A');
  vm.runInContext("state.aiRewrite={productId:'p1',source:'原稿 A',result:'新稿 A'};state.draft='新稿 A';state.product={id:'p1',name:'商品A',copy:{text:'原稿 A'}};",f.ctx);
  vm.runInContext("document.querySelector('#copy').value='手工新稿';syncDraft();undoRewrite()",f.ctx);
  assert.equal(f.nodes['#copy'].value,'手工新稿');
});

test('rewrite request rejects duplicate clicks and stale product result',async()=>{
  const f=fixture();
  vm.runInContext("state.aiBusy=true;state.product={id:'p1',name:'商品A',copy:{text:'原稿 A'}};",f.ctx);
  await vm.runInContext("aiRun('rewrite')",f.ctx);
  assert.equal(vm.runInContext('state.aiBusy',f.ctx),true);
  vm.runInContext("state.aiBusy=false;state.product={id:'p2',name:'商品B',copy:{text:'B'}};state.draft='B';state.aiRewrite={productId:'p1',source:'原稿 A',result:'旧结果'};renderScript()",f.ctx);
  assert.doesNotMatch(f.nodes['#secScript'].innerHTML,/旧结果/);
});

test('rewrite sends only allowed text fields and releases lock after config failure',async()=>{
  const f=fixture();
  f.nodes['#aiRewriteReason'].value='平台提示表达夸大';
  const calls=[];
  f.ctx.api=async(url,opt)=>{
    calls.push({url,opt});
    if(url==='/api/ai-settings')return {base_url:'https://stub.example',has_key:true};
    return {text:'优化后文案'};
  };
  f.ctx.confirmDialog=async()=>true;
  await vm.runInContext("aiRun('rewrite')",f.ctx);
  const req=calls.find(x=>x.url.includes('/ai-copy'));
  assert.ok(req);
  const body=JSON.parse(req.opt.body);
  assert.deepEqual(Object.keys(body).sort(),['brief','mode','text']);
  assert.deepEqual(Object.keys(body.brief).sort(),['name','reason']);
  assert.equal(body.text,'原稿 A');
  assert.equal(body.brief.name,'商品A');
  assert.equal(body.brief.reason,'平台提示表达夸大');

  const failed=fixture();
  failed.ctx.api=async()=>{throw Error('设置读取失败')};
  await vm.runInContext("aiRun('rewrite')",failed.ctx);
  assert.equal(vm.runInContext('state.aiBusy',failed.ctx),false);
});

test('rewrite rendering escapes product and reason text',()=>{
  const f=fixture();
  vm.runInContext("state.product={id:'x',name:'<img src=x onerror=alert(1)>',copy:{text:'原稿'}};state.draft='原稿';state.rewriteByProduct.x={productId:'x',source:'原稿',reason:'<script>alert(1)</script>',result:'新稿'};renderScript()",f.ctx);
  assert.doesNotMatch(f.nodes['#secScript'].innerHTML,/<img src=x/);
  assert.doesNotMatch(f.nodes['#secScript'].innerHTML,/<script>alert/);
  assert.match(f.nodes['#secScript'].innerHTML,/&lt;img src=x/);
});

// R2: derive the preview/button nodes from production HTML, not invented IDs.
function r2Fixture(){
  const f=fixture();let html='';
  const tracked=new Set(['copy','aiName','aiRewriteReason','rewritePreview','aiRewritePreviewBody','btnRewriteAdopt','btnRewriteUndo','aiStatus','btnAIGenerateStart','btnAIRewriteStart','btnAIPolish','btnAIAudit']);
  Object.defineProperty(f.nodes['#secScript'],'innerHTML',{get:()=>html,set(value){
    html=value;for(const id of tracked)delete f.nodes['#'+id];
    for(const m of value.matchAll(/<([a-z]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){
      const [,tag,attrs,id]=m;if(!tracked.has(id))continue;
      const text=tag==='textarea'?value.slice(m.index+m[0].length).split('</textarea>')[0]:(attrs.match(/\bvalue="([^"]*)"/)||[])[1]||'';
      f.nodes['#'+id]={value:text,hidden:/\bhidden\b/.test(attrs),disabled:/\bdisabled\b/.test(attrs),textContent:'',onclick:(attrs.match(/onclick="([^"]*)"/)||[])[1]};
    }
  }});
  f.ctx.setTimeout=()=>1;f.ctx.clearTimeout=()=>{};
  f.run=s=>vm.runInContext(s,f.ctx);
  f.ctx.api=async url=>url==='/api/ai-settings'?{has_key:true,base_url:'https://synthetic.invalid'}:{text:'新稿 A'};
  f.ctx.confirmDialog=async()=>true;
  f.run('renderScript()');return f;
}
function r2Deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
async function r2Flush(){for(let i=0;i<15;i++)await Promise.resolve()}

test('R2 actual template preview is visible; actual adopt and undo handlers work',async()=>{
  const f=r2Fixture();await f.run("aiRun('rewrite')");
  assert.equal(f.nodes['#rewritePreview'].hidden,false);
  assert.equal(f.nodes['#aiRewritePreviewBody'].textContent,'新稿 A');
  assert.equal(f.nodes['#copy'].value,'原稿 A');
  assert.equal(f.nodes['#btnRewriteAdopt'].disabled,false);
  f.run(f.nodes['#btnRewriteAdopt'].onclick);assert.equal(f.nodes['#copy'].value,'新稿 A');
  assert.equal(f.nodes['#btnRewriteUndo'].disabled,false);
  f.run(f.nodes['#btnRewriteUndo'].onclick);assert.equal(f.nodes['#copy'].value,'原稿 A');
});

for(const order of [['audit','rewrite'],['rewrite','audit']])for(const stage of ['settings','consent'])test(`R2 shared lock ${order.join(' then ')} at ${stage}; peak=1 and final unlock`,async()=>{
  const f=r2Fixture(),gate=r2Deferred(),model=r2Deferred();let pending=0,peak=0,settings=0;const modes=[];
  f.ctx.api=async(url,opt)=>{
    if(url==='/api/ai-settings'){settings++;if(stage==='settings')await gate.promise;return {has_key:true,base_url:'https://synthetic.invalid'}}
    modes.push(JSON.parse(opt.body).mode);pending++;peak=Math.max(peak,pending);try{return await model.promise}finally{pending--}
  };
  f.ctx.confirmDialog=async()=>{if(stage==='consent')await gate.promise;return true};
  const first=f.run(`aiRun('${order[0]}')`);await r2Flush();
  assert.equal(f.run('state.aiBusy'),true);
  await f.run(`aiRun('${order[1]}')`);assert.equal(settings,1);assert.equal(modes.length,0);
  f.run('renderScript()');for(const id of ['btnAIRewriteStart','btnAIPolish','btnAIAudit','btnAIGenerateStart'])assert.equal(f.nodes['#'+id].disabled,true);
  gate.resolve();await r2Flush();assert.equal(pending,1);assert.equal(f.run('state.aiBusy'),true);
  await f.run(`aiRun('${order[1]}')`);assert.deepEqual(modes,[order[0]]);
  model.resolve({text:'新稿 A'});await first;assert.equal(pending,0);assert.equal(f.run('state.aiBusy'),false);
  await f.run(`aiRun('${order[1]}')`);assert.deepEqual(modes,order);assert.equal(peak,1);assert.equal(pending,0);assert.equal(f.run('state.aiBusy'),false);
});

test('R2 generate replacement confirmation holds lock before settings',async()=>{
  const f=r2Fixture(),gate=r2Deferred();let calls=0;
  f.ctx.confirmDialog=()=>gate.promise;f.ctx.api=async()=>{calls++;return {has_key:true,base_url:'https://synthetic.invalid'}};
  const p=f.run("aiRun('generate')");await r2Flush();assert.equal(f.run('state.aiBusy'),true);
  await f.run("aiRun('rewrite')");assert.equal(calls,0);gate.resolve(false);await p;assert.equal(f.run('state.aiBusy'),false);
});

for(const kind of ['settings-failure','consent-failure','consent-cancel','model-failure'])test(`R2 legacy ${kind} unlocks`,async()=>{
  const f=r2Fixture();f.ctx.api=async url=>{if(kind==='settings-failure')throw Error('settings');if(url==='/api/ai-settings')return {base_url:'https://synthetic.invalid'};throw Error('model')};
  f.ctx.confirmDialog=async()=>{if(kind==='consent-failure')throw Error('consent');return kind!=='consent-cancel'};
  await f.run("aiRun('audit')");assert.equal(f.run('state.aiBusy'),false);assert.equal(f.nodes['#copy'].value,'原稿 A');
});

test('R2 any new AI operation invalidates previous adopt authority immediately',async()=>{
  const f=r2Fixture();await f.run("aiRun('rewrite')");const gate=r2Deferred();f.ctx.api=()=>gate.promise;
  const p=f.run("aiRun('audit')");f.run('adoptRewrite()');assert.equal(f.nodes['#copy'].value,'原稿 A');assert.equal(f.nodes['#rewritePreview'].hidden,true);
  gate.reject(Error('settings'));await p;f.run('adoptRewrite()');assert.equal(f.nodes['#copy'].value,'原稿 A');
});

test('R2 edit then restore during model request invalidates generation',async()=>{
  const f=r2Fixture(),model=r2Deferred();f.ctx.api=async url=>url==='/api/ai-settings'?{has_key:true,base_url:'https://synthetic.invalid'}:model.promise;
  const p=f.run("aiRun('rewrite')");await r2Flush();
  for(const text of ['编辑中','原稿 A']){f.nodes['#copy'].value=text;f.run('onCopyInput()')}
  model.resolve({text:'不应接受'});await p;assert.equal(f.run('state.aiRewrite.result'),null);assert.equal(f.nodes['#copy'].value,'原稿 A');assert.equal(f.run('state.aiBusy'),false);
});

// R3: execute the production onchange AND load, with asynchronous API fixtures.
function r3Fixture(){
  const f=r2Fixture();f.nodes['#prodSel']={value:'p1'};
  const products=[{id:'p1',name:'商品A',copy:{text:'原稿 A'}},{id:'p2',name:'商品B',copy:{text:'原稿 B'}}];
  f.ctx.products=products;f.run('state.products=products;state.product=products[0];syncDraft()');
  f.calls=[];f.productGates=[];
  f.ctx.api=async(url,opt)=>{
    f.calls.push(url);
    if(url==='/api/products'){const gate=f.productGates.shift();if(gate)await gate.promise;return structuredClone(products)}
    if(url.startsWith('/api/jobs'))return [];
    if(url==='/api/roots')return [];
    if(url==='/api/vox-voices')return {presets:[],clones:[]};
    if(url==='/api/prefs')return {cleanup_after_success:true};
    if(url==='/api/ai-settings')return {has_key:true,base_url:'https://synthetic.invalid'};
    if(url.endsWith('/ai-copy'))return {text:'新稿 A'};
    throw Error('unexpected API '+url);
  };
  f.ctx.renderMaterials=async()=>{};
  const init=fs.readFileSync(path.join(__dirname,'..','static','app.js'),'utf8').split('/* ---------- init ---------- */')[1].trim().split('\n')[0];
  f.run(init);
  f.select=id=>f.nodes['#prodSel'].onchange({target:{value:id}});
  f.edit=text=>{f.nodes['#copy'].value=text;f.run('onCopyInput()')};
  return f;
}

test('R3 real onchange-load-render keeps manual rewrite separate from saved product copy',async()=>{
  const f=r3Fixture();await f.run("aiRun('rewrite')");
  f.run('adoptRewrite();undoRewrite();adoptRewrite()');f.edit('A 手改稿');
  await f.select('p2');await r2Flush();
  assert.equal(f.nodes['#copy'].value,'原稿 B');
  assert.equal(f.run('state.product.copy.text'),'原稿 B');
  f.edit('B 独立草稿');await f.select('p1');await r2Flush();
  assert.equal(f.nodes['#copy'].value,'A 手改稿');
  await f.select('p2');await r2Flush();assert.equal(f.nodes['#copy'].value,'B 独立草稿');
  assert.equal(f.calls.filter(x=>x==='/api/products').length,3);
  assert.equal(f.calls.some(x=>x.startsWith('/api/jobs')),true);
  assert.deepEqual(JSON.parse(f.run('JSON.stringify(state.draftByProduct)')),{p1:'A 手改稿',p2:'B 独立草稿'});
});

test('R3 rapid A-B-A during pending real loads preserves each draft, including latest input',async()=>{
  const f=r3Fixture(),first=r2Deferred(),second=r2Deferred();f.edit('A 独立草稿');
  f.productGates.push(first,second);const b=f.select('p2');
  assert.equal(f.nodes['#copy'].value,'原稿 B');f.edit('B 独立草稿');
  const a=f.select('p1');assert.equal(f.nodes['#copy'].value,'A 独立草稿');f.edit('A 等待时编辑');
  second.resolve();await a;await r2Flush();first.resolve();await b;await r2Flush();
  assert.equal(f.run('state.product.id'),'p1');assert.equal(f.nodes['#copy'].value,'A 等待时编辑');
  await f.select('p2');await r2Flush();assert.equal(f.nodes['#copy'].value,'B 独立草稿');
});

test('R3 explicitly empty draft does not resurrect saved text through switch or real load',async()=>{
  const f=r3Fixture();f.edit('');await f.select('p2');await r2Flush();
  assert.equal(f.nodes['#copy'].value,'原稿 B');f.edit('B 独立草稿');
  await f.select('p1');await r2Flush();assert.equal(f.nodes['#copy'].value,'');
  await f.run('load()');assert.equal(f.nodes['#copy'].value,'');
  assert.equal(f.run('state.product.copy.text'),'原稿 A');assert.equal(f.run('state.draftByProduct.p1'),'');
});

test('R4 rewrite preview shows source and result length for voiceover duration',()=>{
  const f=fixture();
  f.nodes['#copy'].value='原稿内容需要保留原来的篇幅';
  vm.runInContext("state.aiRewrite={productId:'p1',source:'原稿内容需要保留原来的篇幅',result:'改写以后也要保持差不多的篇幅',applied:false};renderRewritePreview()",f.ctx);
  assert.equal(f.nodes['#rewritePreview'].hidden,false);
  assert.equal(f.nodes['#rewriteLen'].textContent,'原稿 13 字 → 新稿 14 字');
  vm.runInContext('state.aiRewrite.invalid=true;renderRewritePreview()',f.ctx);
  assert.equal(f.nodes['#rewriteLen'].textContent,'');
});
