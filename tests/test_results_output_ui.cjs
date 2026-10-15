// Run: node --test tests/test_results_output_ui.cjs (no extra dependencies).
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'..');
const source=fs.readFileSync(path.join(root,'static/app.js'),'utf8').split('/* ---------- init ---------- */')[0];
function fixture(product={id:'product-a',name:'Example',output:{path:'D:\\Exports'}}){
  const nodes={'#secResults':{innerHTML:''},'#secMaterials':{innerHTML:''}};
  const ctx=vm.createContext({document:{querySelector:s=>nodes[s]||null},console,setTimeout,clearTimeout,AbortController});
  vm.runInContext(source,ctx);
  ctx.fixtureProduct=product;
  vm.runInContext('state.product=fixtureProduct;state.jobs=[];renderResults()',ctx);
  return {ctx,nodes,html:()=>nodes['#secResults'].innerHTML};
}
test('results header exposes the current output and existing folder picker',()=>{
  const f=fixture();
  assert.match(f.html(),/class="results-head"/);
  assert.match(f.html(),/id="resultsOutputPath"/);
  assert.match(f.html(),/D:\\Exports/);
  assert.match(f.html(),/id="resultsOutputChoose"[^>]*onclick="openPicker\('output'\)"/);
  assert.match(f.html(),/保存到/);
  assert.match(f.html(),/仅影响新任务/);
  assert.match(f.html(),/商品名 \/ 年月日 \/ 商品名 序号\.mp4/);
});
test('folder picker remains discoverable before the first completed video',()=>{
  const f=fixture();assert.match(f.html(),/还没有成片/);assert.match(f.html(),/>更改<\/button>/);
});
test('unselected output offers a clear choice rather than claiming a default',()=>{
  const f=fixture({id:'a',name:'A'});assert.match(f.html(),/未选择/);assert.match(f.html(),/>选择文件夹<\/button>/);
});
test('no product does not expose an actionable save control',()=>{
  const f=fixture(null);assert.match(f.html(),/选择商品后显示/);assert.doesNotMatch(f.html(),/resultsOutputChoose/);
});
test('UNC and HTML metacharacters are escaped in text and tooltip',()=>{
  const f=fixture({id:'a',name:'A',output:{path:'\\\\server\\share\\<img src=x onerror=alert(1)>"&'}});
  assert.doesNotMatch(f.html(),/<img src=x/);assert.match(f.html(),/\\\\server\\share/);
  assert.match(f.html(),/&lt;img src=x onerror=alert\(1\)&gt;&quot;&amp;/);
});
test('changing selected product refreshes the result path',()=>{
  const f=fixture();vm.runInContext("state.product={id:'b',name:'B',output:{path:'E:/Other'}};renderResults()",f.ctx);
  assert.match(f.html(),/E:\/Other/);assert.doesNotMatch(f.html(),/D:\\Exports/);
});
test('completed cards and result links remain intact',()=>{
  const f=fixture();vm.runInContext("state.jobs=[{id:'job-a',status:'done',artifact:{},lines_count:1,script_summary:'Example',variant:1,finished_at:1700000000}];renderResults()",f.ctx);
  assert.match(f.html(),/class="daygroup"/);assert.match(f.html(),/\/api\/results\/job-a\?product_id=product-a/);
  assert.match(f.html(),/>播放<\/a>/);assert.match(f.html(),/>下载<\/a>/);assert.match(f.html(),/resultsOutputChoose/);
});
test('both entries rerender together on the existing confirmed-save path',async()=>{
  const f=fixture();
  vm.runInContext(`refreshIdxState=async()=>{};closePicker=()=>{state.picker=null};
    api=async(url,opt)=>{globalThis.savedRequest={url,body:JSON.parse(opt.body)};state.product.output={path:JSON.parse(opt.body).path}};
    load=async()=>{await renderMaterials();renderResults()};
    state.picker={role:'output',path:'E:/Selected'};`,f.ctx);
  await vm.runInContext('confirmPick()',f.ctx);
  assert.equal(f.ctx.savedRequest.url,'/api/products/product-a/output');
  assert.equal(f.ctx.savedRequest.body.path,'E:/Selected');
  assert.match(f.html(),/E:\/Selected/);assert.match(f.nodes['#secMaterials'].innerHTML,/E:\/Selected/);
  assert.equal(vm.runInContext('state.picker',f.ctx),null);
});
