// Run: node --test tests/test_results_output_ui.cjs (no extra dependencies).
// The output folder setting must appear exactly once — beside 素材/索引 — and the
// results section must stay free of a duplicated output entry; the existing
// folder picker + save chain stays wired for the single remaining entry.
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
test('results section keeps a plain header without duplicating the output entry',()=>{
  const f=fixture();
  const html=f.html();
  assert.match(html,/完成后可播放 \/ 下载/);
  assert.match(html,/还没有成片/);
  assert.doesNotMatch(html,/results-head|results-output|resultsOutputChoose|保存到|仅影响新任务/);
});
test('no product shows the placeholder and no output control',()=>{
  const f=fixture(null);
  assert.match(f.html(),/选择商品后显示/);
  assert.doesNotMatch(f.html(),/resultsOutputChoose/);
});
test('materials area exposes the single output row with the folder picker',()=>{
  const f=fixture();
  vm.runInContext('refreshIdxState=async()=>{};renderMaterials()',f.ctx);
  const html=f.nodes['#secMaterials'].innerHTML;
  assert.match(html,/<span class="plabel">素材<\/span>/);
  assert.match(html,/<span class="plabel">输出<\/span>/);
  assert.match(html,/openPicker\('output'\)/);
  assert.match(html,/D:\\Exports/);
});
test('UNC and HTML metacharacters are escaped in the materials path chip',()=>{
  const f=fixture({id:'a',name:'A',output:{path:'\\\\server\\share\\<img src=x onerror=alert(1)>"&'}});
  vm.runInContext('refreshIdxState=async()=>{};renderMaterials()',f.ctx);
  const html=f.nodes['#secMaterials'].innerHTML;
  assert.doesNotMatch(html,/<img src=x/);
  assert.match(html,/\\\\server\\share/);
  assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;&quot;&amp;/);
});
test('changing selected product refreshes the materials path',()=>{
  const f=fixture();
  vm.runInContext('refreshIdxState=async()=>{};renderMaterials()',f.ctx);
  assert.match(f.nodes['#secMaterials'].innerHTML,/D:\\Exports/);
  vm.runInContext("state.product={id:'b',name:'B',output:{path:'E:/Other'}};renderMaterials()",f.ctx);
  const html=f.nodes['#secMaterials'].innerHTML;
  assert.match(html,/E:\/Other/);
  assert.doesNotMatch(html,/D:\\Exports/);
});
test('confirmed folder pick saves through the existing output API and refreshes the page',async()=>{
  const f=fixture();
  vm.runInContext(`refreshIdxState=async()=>{};closePicker=()=>{state.picker=null};
    api=async(url,opt)=>{globalThis.savedRequest={url,body:JSON.parse(opt.body)};state.product.output={path:JSON.parse(opt.body).path}};
    load=async()=>{await renderMaterials()};
    state.picker={role:'output',path:'E:/Selected'};`,f.ctx);
  await vm.runInContext('confirmPick()',f.ctx);
  assert.equal(f.ctx.savedRequest.url,'/api/products/product-a/output');
  assert.equal(f.ctx.savedRequest.body.path,'E:/Selected');
  assert.match(f.nodes['#secMaterials'].innerHTML,/E:\/Selected/);
  assert.equal(vm.runInContext('state.picker',f.ctx),null);
});
test('completed cards and result links remain intact',()=>{
  const f=fixture();
  vm.runInContext("state.jobs=[{id:'job-a',status:'done',artifact:{},lines_count:1,script_summary:'Example',variant:1,finished_at:1700000000}];renderResults()",f.ctx);
  const html=f.html();
  assert.match(html,/class="resgrid"/);
  assert.match(html,/class="rescard"/);
  assert.match(html,/\/api\/results\/job-a\?product_id=product-a/);
  assert.match(html,/>播放<\/a>/);
  assert.match(html,/>下载<\/a>/);
  assert.doesNotMatch(html,/resultsOutputChoose/);
});
