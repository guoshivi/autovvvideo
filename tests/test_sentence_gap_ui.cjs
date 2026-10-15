const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const root=path.resolve(__dirname,'..');
const source=fs.readFileSync(path.join(root,'static/app.js'),'utf8').split('/* ---------- init ---------- */')[0];

function make(product, overrides={}){
  const nodes={ '#secScript':{innerHTML:''}, '#copy':{value:'甲'}, '#voice':{checked:true}, '#subs':{checked:true},
    '#voiceEngine':{value:'qwen'}, '#voxVoice':{value:'natural_female'}, '#speaker':{value:'Serena'}, '#speed':{value:'1'},
    '#target':{value:''}, '#subSize':{value:'54'}, '#subPos':{value:'380'}, '#subBorder':{value:'3'}, '#shotReuse':{value:'1'},
    '#dedup':{value:'off'}, '#beauty':{checked:false}, '#beautyStrength':{value:'.25'}, '#visualFilter':{value:'original'},
    '#visualFilterStrength':{value:'.35'}, '#sentenceGapMode':{value:'auto'}, '#sentenceGap':{value:''} };
  const ctx=vm.createContext({document:{querySelector:s=>nodes[s]||null},window:{},console,setTimeout:fn=>{fn();return 0},clearTimeout,AbortController,fetch:()=>{throw Error('unexpected fetch')},...overrides});
  vm.runInContext(source,ctx);
  ctx.fixtureProduct=product;
  vm.runInContext('state.product=fixtureProduct;state.products=[fixtureProduct]',ctx);
  return {ctx,nodes};
}

test('sentence gap UI is explicit, adjacent to speed, and disabled with mute',()=>{
  const f=make({id:'a',name:'A',settings:{sentence_gap_seconds:null}});
  vm.runInContext('renderScript()',f.ctx);
  assert.match(f.nodes['#secScript'].innerHTML,/句间停顿/);
  assert.match(f.nodes['#secScript'].innerHTML,/sentenceGapMode/);
  assert.match(f.nodes['#secScript'].innerHTML,/自动/);
  const muted=make({id:'a',name:'A',settings:{voice:false,sentence_gap_seconds:0.5}});
  vm.runInContext('renderScript()',muted.ctx);
  assert.match(muted.nodes['#secScript'].innerHTML,/id="sentenceGapMode"[^>]*disabled/);
  assert.match(muted.nodes['#secScript'].innerHTML,/id="sentenceGap"[^>]*disabled/);
});

test('auto remains null through unrelated save, while manual value persists',async()=>{
  const p={id:'a',name:'A',settings:{sentence_gap_seconds:null}};
  const f=make(p);
  let saved=[];
  f.ctx.api=async(url,opt)=>{saved.push(JSON.parse(opt.body));return {id:'a',settings:saved.at(-1).settings}};
  vm.runInContext('persistSettings()',f.ctx);
  await new Promise(r=>setImmediate(r));
  assert.equal(saved.at(-1).settings.sentence_gap_seconds,null);
  f.nodes['#sentenceGapMode'].value='manual';f.nodes['#sentenceGap'].value='0.47';
  vm.runInContext('persistSettings()',f.ctx);
  await new Promise(r=>setImmediate(r));
  assert.equal(saved.at(-1).settings.sentence_gap_seconds,0.47);
});

test('product switch keeps gap values isolated and rejects invalid UI numbers',()=>{
  const f=make({id:'a',name:'A',settings:{sentence_gap_seconds:0.22}});
  f.nodes['#sentenceGapMode'].value='manual';f.nodes['#sentenceGap'].value='0.22';
  assert.equal(vm.runInContext('collectSettings().sentence_gap_seconds',f.ctx),0.22);
  vm.runInContext("state.product={id:'b',name:'B',settings:{sentence_gap_seconds:null}}",f.ctx);
  f.nodes['#sentenceGapMode'].value='auto';f.nodes['#sentenceGap'].value='';
  assert.equal(vm.runInContext('productSettings().sentence_gap_seconds',f.ctx),null);
  for(const value of ['0.09','1.01','NaN','Infinity','bad','']){
    f.nodes['#sentenceGapMode'].value='manual';f.nodes['#sentenceGap'].value=value;
    assert.throws(()=>vm.runInContext('collectSettings()',f.ctx));
  }
});

test('voice and auto toggles update the live control state',()=>{
  const f=make({id:'a',name:'A',settings:{sentence_gap_seconds:null}});
  f.ctx.api=async()=>({id:'a',settings:{sentence_gap_seconds:null}});
  vm.runInContext('renderScript()',f.ctx);
  f.nodes['#voice'].checked=false;vm.runInContext('onVoiceToggle()',f.ctx);
  assert.equal(f.nodes['#sentenceGapMode'].disabled,true);
  assert.equal(f.nodes['#sentenceGap'].disabled,true);
  f.nodes['#voice'].checked=true;f.nodes['#sentenceGapMode'].disabled=false;f.nodes['#sentenceGapMode'].value='manual';
  vm.runInContext('onSentenceGapModeChange()',f.ctx);
  assert.equal(f.nodes['#sentenceGap'].disabled,false);
  assert.equal(f.nodes['#sentenceGap'].value,'0.10');
  f.nodes['#sentenceGapMode'].value='auto';vm.runInContext('onSentenceGapModeChange()',f.ctx);
  assert.equal(f.nodes['#sentenceGap'].disabled,true);
  assert.equal(f.nodes['#sentenceGap'].value,'');
});

test('pending save is captured per product and late response cannot replace selection',async()=>{
  const a={id:'a',name:'A',settings:{sentence_gap_seconds:null}};
  const b={id:'b',name:'B',settings:{sentence_gap_seconds:null}};
  const f=make(a);
  f.ctx.fixtureA=a;f.ctx.fixtureB=b;vm.runInContext('state.products=[fixtureA,fixtureB]',f.ctx);
  let resolveA;const requests=[];
  f.ctx.api=(url,opt)=>new Promise(resolve=>{requests.push({url,body:JSON.parse(opt.body),resolve})});
  f.nodes['#sentenceGapMode'].value='manual';f.nodes['#sentenceGap'].value='0.31';
  vm.runInContext('persistSettings()',f.ctx);
  await new Promise(r=>setImmediate(r));
  vm.runInContext('state.product=state.products[1]',f.ctx);
  assert.equal(requests.length,1);
  requests[0].resolve({id:'a',settings:{sentence_gap_seconds:0.31}});
  await new Promise(r=>setImmediate(r));
  assert.equal(vm.runInContext('state.product.id',f.ctx),'b');
  assert.equal(vm.runInContext('state.products[0].settings.sentence_gap_seconds',f.ctx),0.31);
});

test('local estimate follows the backend slot rule for short manual gaps',()=>{
  const f=make({id:'a',name:'A',settings:{sentence_gap_seconds:null}});
  f.nodes['#copy'].value='甲\n乙';f.nodes['#sentenceGapMode'].value='manual';f.nodes['#sentenceGap'].value='0.10';
  assert.equal(vm.runInContext('estSeconds()',f.ctx),1);
  f.nodes['#sentenceGapMode'].value='auto';f.nodes['#sentenceGap'].value='';
  assert.equal(vm.runInContext('estSeconds()',f.ctx),1.2);
});
