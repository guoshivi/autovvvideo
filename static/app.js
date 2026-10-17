/* 视频工作台 V2 前端（v9：AI 文案 / 语速 / 目标时长 / 常用稿 / 一键出片 / 封面片头尾 / 结果卡片 / 镜头去重） */
const state={products:[],jobs:[],product:null,noAutoSelect:false,draft:'',draftProductId:null,draftByProduct:{},retrieval:[],roots:[],picker:null,batchMode:'single',batchCount:1,bgm:null,cover:null,intro:null,outro:null,indexReady:false,indexInfo:null,timing:null,aiOpen:false,aiBusy:false,aiHasKey:false,aiOut:null,aiRewriteOpen:true,aiRewrite:null,rewriteByProduct:{},rewriteGeneration:0,lastJobsSig:'',lastIndexSample:null,settingsTimer:null,settingsTimers:{},queuePage:1,resultsPage:1,prefs:{cleanup_after_success:true},worker:null,previewBusy:false,previewAudio:null,cleanUndo:null,voxPresets:null,voxClones:[],voxCloneDir:''};
const $=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(url,opt){opt=opt||{};const ctl=new AbortController();const tid=setTimeout(()=>ctl.abort(),opt.timeoutMs||30000);let r;
try{r=await fetch(url,Object.assign({},opt,{signal:ctl.signal}))}catch(e){clearTimeout(tid);throw Error(e.name==='AbortError'?'请求超时，请再试一次':e.message)}
clearTimeout(tid);let d;try{d=await r.json()}catch{d={error:await r.text()}}if(!r.ok)throw Error(d.error||'请求失败');return d}
function setErr(msg){const e=$('#err');if(!e)return;if(msg){e.hidden=false;e.innerHTML=esc(msg)}else{e.hidden=true;e.innerHTML=''}}
function syncDraft(){const c=$('#copy');if(c){state.draft=c.value;state.draftProductId=state.product?.id||null;if(state.product)state.draftByProduct[state.product.id]=c.value}}
function rootLabel(id){const r=state.roots.find(x=>x.id===Number(id));return r?r.name:'根'+(Number(id)+1)}
function selText(s){if(!s)return '未选择';if(s.path)return esc(s.path);return esc(rootLabel(s.root_id))+' / '+esc(s.token||'根目录')}
function fmtSec(v){if(v==null||!isFinite(v))return '--';return (Math.round(v*10)/10)+' 秒'}
function fmtBytes(v){if(v==null||!isFinite(v))return '--';return v>=1048576?(v/1048576).toFixed(1)+' MB':Math.max(1,Math.round(v/1024))+' KB'}
function fmtClock(ts){if(!ts)return '';const d=new Date(ts*1000);return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')}
function dayKey(ts){const d=new Date(ts*1000);const t=new Date();const y=new Date(Date.now()-86400000);
if(d.toDateString()===t.toDateString())return '今天';if(d.toDateString()===y.toDateString())return '昨天';
return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')}
function toast(msg,kind){msg=String(msg??'');if(kind==='warn')msg=humanMsg(msg);let h=$('#toastHost');if(!h){h=document.createElement('div');h.id='toastHost';document.body.appendChild(h)}
const t=document.createElement('div');t.className='toast '+(kind||'ok');t.textContent=msg;h.appendChild(t);
setTimeout(()=>{t.classList.add('out');setTimeout(()=>t.remove(),300)},2800);t.onclick=()=>{t.classList.add('out');setTimeout(()=>t.remove(),300)}}
function confirmDialog(msg){return new Promise(res=>{let h=$('#modalHost');if(!h){h=document.createElement('div');h.id='modalHost';document.body.appendChild(h)}
h.innerHTML=`<div class="overlay" data-cancel="1"></div><div class="modal small" role="dialog"><div class="modal-body confirm"><p>${esc(msg)}</p></div><div class="modal-foot"><span class="row"><button class="secondary" data-cancel="1">取消</button><button class="primary" data-ok="1">确定</button></span></div></div>`;
h.querySelectorAll('[data-cancel]').forEach(el=>el.onclick=()=>{h.innerHTML='';res(false)});
h.querySelector('[data-ok]').onclick=()=>{h.innerHTML='';res(true)}})}
async function load(){try{setErr('');if(state.draftProductId==null||state.draftProductId===state.product?.id)syncDraft();
  state.products=await api('/api/products');
  if(!state.product&&!state.noAutoSelect&&state.products.length)state.product=state.products[state.products.length-1];
  state.jobs=await api('/api/jobs'+(state.product?'?product_id='+state.product.id:''));
  try{state.prefs=await api('/api/prefs')}catch(e){state.prefs={cleanup_after_success:true}}
  if(state.product)state.product=state.products.find(p=>p.id===state.product.id)||null;
  if(!state.roots.length){try{state.roots=await api('/api/roots')}catch(e){}}
  try{const vv=await api('/api/vox-voices');state.voxPresets=vv.presets||null;state.voxClones=vv.clones||[];state.voxCloneDir=vv.dir||''}catch(e){}
  hydrateSettings();
  renderTop();renderScript();renderQueue();renderResults();
  await renderMaterials();
  state.needReload=false;
}catch(e){state.needReload=true;setErr('服务暂不可用：'+e.message+'（稍等片刻会自动恢复）')}}
/* ---------- 设置：读取/保存（记住上次选择） ---------- */
const DEFAULTS={voice:true,subtitles:true,speaker:'Serena',voice_engine:'qwen',vox_voice:'natural_female',speed:1.0,sentence_gap_seconds:null,target_seconds:0,shot_reuse:1,subtitle_size:54,subtitle_bottom:380,subtitle_border:3,dedup:'off',beauty:false,beauty_strength:0.25,visual_filter:'original',visual_filter_strength:0.35,bgm:false,bgm_path:'',bgm_volume:0.2,cover:null,intro:null,outro:null};
function productSettings(){return Object.assign({},DEFAULTS,state.product&&state.product.settings||{})}
function hydrateSettings(){if(state.product&&state.settingsTimers&&state.settingsTimers[state.product.id])return;
  const s=productSettings();
  if(!state.cover&&s.cover)state.cover=s.cover;if(!state.intro&&s.intro)state.intro=s.intro;if(!state.outro&&s.outro)state.outro=s.outro;state.bgm=null}
function collectSettings(){const s=productSettings();
  const num=v=>Number(v)||0;
  const gapMode=$('#sentenceGapMode'),gapInput=$('#sentenceGap');
  let sentenceGap=s.sentence_gap_seconds??null;
  if(gapMode&&gapMode.value==='manual'){
    const raw=String(gapInput?.value??'').trim(),v=Number(raw);
    if(!raw||!Number.isFinite(v)||v<0.10-1e-9||v>1.00+1e-9)throw Error('句间停顿需为 0.10–1.00 秒');
    sentenceGap=Math.round(v*100)/100;
  }else if(gapMode&&gapMode.value==='auto')sentenceGap=null;
  const cover=state.cover?(state.cover.mode==='file'?(state.cover.path?{mode:'file',path:state.cover.path,...(state.cover.sha256?{sha256:state.cover.sha256}:{})}:null):(state.cover.mode==='frame'?{mode:'frame',seconds:num(state.cover.seconds)||1}:{mode:'auto'})):null;
  const card=v=>v&&v.path?{path:v.path,seconds:num(v.seconds)||3,kind:v.kind,...(v.sha256?{sha256:v.sha256}:{})}:null;
  return {voice:$('#voice')?$('#voice').checked:s.voice,subtitles:$('#subs')?$('#subs').checked:s.subtitles,
    speaker:$('#speaker')?$('#speaker').value:s.speaker,voice_engine:$('#voiceEngine')?$('#voiceEngine').value:(s.voice_engine||'qwen'),vox_voice:$('#voxVoice')?$('#voxVoice').value:(s.vox_voice||'natural_female'),speed:$('#speed')?num($('#speed').value)||1.0:s.speed,sentence_gap_seconds:sentenceGap,
    target_seconds:$('#target')?parseInt($('#target').value||'0',10)||0:s.target_seconds,
    subtitle_size:$('#subSize')?parseInt($('#subSize').value,10):s.subtitle_size,
    subtitle_bottom:$('#subPos')?parseInt($('#subPos').value,10):s.subtitle_bottom,
    subtitle_border:$('#subBorder')?parseInt($('#subBorder').value,10):s.subtitle_border,
    shot_reuse:$('#shotReuse')?parseInt($('#shotReuse').value,10):s.shot_reuse,
    bgm:!!state.bgm,bgm_path:state.bgm?state.bgm.path:'',bgm_volume:$('#bgmVol')?Math.max(0,Math.min(1,(Number($('#bgmVol').value)||0)/100)):s.bgm_volume,
    dedup:$('#dedup')?$('#dedup').value:s.dedup,
    beauty:$('#beauty')?$('#beauty').checked:!!s.beauty,beauty_strength:$('#beautyStrength')?num($('#beautyStrength').value):Number(s.beauty_strength??.25),
    visual_filter:$('#visualFilter')?$('#visualFilter').value:(s.visual_filter||'original'),visual_filter_strength:$('#visualFilterStrength')?num($('#visualFilterStrength').value):Number(s.visual_filter_strength??.35),
    cover:cover,intro:card(state.intro),outro:card(state.outro)}}
function persistSettings(){if(!state.product)return;const productId=state.product.id;let snapshot;
  try{snapshot=collectSettings()}catch(e){toast(e.message,'warn');return}
  const timers=state.settingsTimers||(state.settingsTimers={});clearTimeout(timers[productId]);
  let timer;timer=setTimeout(async()=>{if(timers[productId]===timer)delete timers[productId];try{const p=await api(`/api/products/${productId}/settings`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:snapshot})});
    const i=state.products.findIndex(x=>x.id===productId);if(i>=0)state.products[i]=p;if(state.product&&state.product.id===productId&&!timers[productId])state.product=p;window.__lastPersistErr=''}
  catch(e){if(window.__lastPersistErr!==e.message){window.__lastPersistErr=e.message;toast('设置没保存上：'+e.message,'warn')}}},600);
  timers[productId]=timer;state.settingsTimer=timer}
function renderTop(){const sel=$('#prodSel');if(!sel)return;sel.innerHTML='<option value="">（未选择商品）</option>'+state.products.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('');sel.value=state.product?state.product.id:'';const b=$('#btnArchive');if(b)b.disabled=!state.product}
function idxText(st){if(st.loading)return '正在检查素材状态…';if(st.error)return esc(st.error);if(st.ready)return `索引 ${st.index.count} 个视频，可用于出片${skipSummary(st)}`;const t=(st.tasks||[]).slice(-1)[0];
if(t&&['queued','running'].includes(t.status))return `正在解析素材：已解析 ${t.indexed||0}/${t.total||'?'} 条`+(t.reused?`（复用 ${t.reused} 条）`:'')+(t.skipped?`（跳过 ${t.skipped} 条）`:'')+'，页面别关';
if(t&&t.status==='paused')return `索引已暂停${t.skipped?`（已跳过 ${t.skipped} 条）`:''}：到任务队列点「继续」接着跑，点「取消」停止`;
if(t&&t.status==='done'&&t.skipped)return `上次索引：成功 ${t.indexed||0} 个，跳过 ${t.skipped} 个（${skipReasonText(t.skipped_files)}）——跳过的素材会在下次建索引时重试`;
return '尚无有效索引——选好素材文件夹后点「开始建索引」（第一次会先校验素材，需要一点时间）'}
function indexRunningJob(st){const t=(st.tasks||[]).slice(-1)[0];return t&&['queued','running'].includes(t.status)?t:null}
function indexPausedJob(st){const t=(st.tasks||[]).slice(-1)[0];return t&&t.status==='paused'?t:null}
async function refreshIdxState(){if(!state.product)return;const pid=state.product.id;
  try{const st=await api(`/api/products/${pid}/index`,{timeoutMs:180000});
    if(!state.product||state.product.id!==pid)return;
    state.indexInfo=st;state.indexReady=!!st.ready;
    if(state.indexReady)refreshTiming();
    const t=$('#idxStatus');if(t)t.textContent=idxText(st);
    const run=indexRunningJob(st);const pausedJob=indexPausedJob(st);const b=$('#btnIndex');
    if(b){if(run){b.disabled=true;b.textContent='正在建索引…'}else if(pausedJob){b.disabled=true;b.textContent='索引已暂停（见任务队列）'}else{b.disabled=!state.product.source;b.textContent=st.ready?'更新索引':'开始建索引'}}
    const go=$('#btnGo'),gh=$('#goHint');if(go){go.disabled=!st.ready;if(gh)gh.textContent=st.ready?'':'先在①建立索引，就能一键出片'}
  }catch(e){const t=$('#idxStatus');if(t)t.textContent='素材状态检查失败：'+e.message}}
async function renderMaterials(){const el=$('#secMaterials');if(!el)return;
  if(!state.product){el.innerHTML='<h3><span class="num">1</span>素材与索引</h3><p class="muted">先在右上角创建一个商品，或选择一个已有商品。</p>';return}
  const p=state.product;let st=state.indexInfo||{loading:true};
  try{el.innerHTML=`<h3><span class="num">1</span>素材与索引 <small class="muted">${esc(p.name)}</small></h3><div class="pathrows"><div class="pathrow"><span class="plabel">素材</span><span class="pchip" title="${esc((p.source||{}).path||'')}">${selText(p.source)}</span><button class="secondary" onclick="openPicker('source')" title="选择素材文件夹">选择</button></div><div class="pathrow"><span class="plabel">输出</span><span class="pchip" title="${esc((p.output||{}).path||'')}">${selText(p.output)}</span><button class="secondary" onclick="openPicker('output')" title="选择输出文件夹">选择</button></div></div><div class="idxrow"><button class="primary" id="btnIndex" onclick="indexNow()" ${!p.source?'disabled':''}>${st.ready?'更新索引':'开始建索引'}</button><span class="status" id="idxStatus">${idxText(st)}</span></div>`}catch(e){el.innerHTML='<h3><span class="num">1</span>素材与索引</h3><div class="modal-error">界面渲染出错：'+esc(e.message)+'</div><div class="row"><button class="secondary" onclick="load()">重试</button></div>'}
  refreshIdxState()}
/* ---------- 脚本生产 ---------- */
function splitLines(t){return String(t||'').split(/\n+/).map(s=>s.trim()).filter(Boolean)}
function onBgmVol(){const el=$('#bgmVol');if(!el)return;const v=Math.max(0,Math.min(100,Number(el.value)||0));const b=$('#bgmVolVal');if(b)b.textContent=v+'%';persistSettings()}
function estSeconds(){const ta=$('#copy');const lines=splitLines(ta?ta.value:'');if(!lines.length)return 0;
  const sp=Math.max(.85,Math.min(1.5,Number(($('#speed')||{}).value||1)));const voiceOn=$('#voice')?$('#voice').checked:true;
  let gap=productSettings().sentence_gap_seconds;if($('#sentenceGapMode')?.value==='manual'){const v=Number($('#sentenceGap')?.value);gap=Number.isFinite(v)?v:null}let total=0;for(let i=0;i<lines.length;i++){const n=[...lines[i]].length,cps=4*sp,voiceSeconds=n/cps;const autoSlot=Math.max(voiceSeconds+.18,Math.min(1,voiceSeconds+.35));const slot=voiceOn?(gap!=null&&i<lines.length-1?voiceSeconds+gap:autoSlot):Math.max(2,Math.min(8,Math.max(1.8,n/5)));total+=Math.ceil(slot*30)/30}
  if(state.intro)total+=Number(state.intro.seconds)||0;if(state.outro)total+=Number(state.outro.seconds)||0;
  return Math.round(total*10)/10}
function renderEstimate(){const chip=$('#estChip'),warn=$('#warnChip');if(!chip)return;
  const est=estSeconds();const target=parseInt(($('#target')||{}).value||'0',10)||0;
  const measured=state.timing&&state.timing.estimate;const shown=measured&&Number.isFinite(measured.seconds)?measured.seconds:est;
  chip.textContent=shown?`预计成片约 ${fmtSec(shown)}${measured?(measured.calibrated?'（历史收据校准）':'（语速估计）'):''}`:'预计成片约 -- 秒';
  chip.className='chip'+(target&&est>target?' over':'');
  if(warn){if(measured&&measured.warning){warn.textContent=measured.warning;warn.className='chip warn'}else if(target&&shown){warn.textContent=shown>target?`超出目标 ${target} 秒`:`在目标 ${target} 秒内`;warn.className='chip '+(shown>target?'warn':'okchip')}else{warn.textContent='';warn.className='chip'}}}
let timingVersion=0,lastTimingKey='';
function timingChanged(){timingVersion++;state.timing=null;renderEstimate();clearTimeout(state.timingTimer);state.timingTimer=setTimeout(refreshTiming,900)}
async function refreshTiming(){if(!state.product||!state.indexReady)return;const pid=state.product.id,text=($('#copy')||{}).value||state.draft||'',settings=collectSettings();const key=JSON.stringify([pid,text,settings]);if(key===lastTimingKey&&state.timing)return;const version=++timingVersion;try{const result=await api(`/api/products/${pid}/timing`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,settings}),timeoutMs:60000});if(version!==timingVersion||!state.product||state.product.id!==pid||JSON.stringify([pid,($('#copy')||{}).value||state.draft||'',collectSettings()])!==key)return;state.timing=result;lastTimingKey=key;renderEstimate()}catch(e){if(version===timingVersion){state.timing=null;renderEstimate()}}}
function rewriteState(){const pid=state.product?.id;if(!pid)return null;let x=state.rewriteByProduct[pid]||null;if(!x&&state.aiRewrite&&state.aiRewrite.productId===pid){x=state.aiRewrite;state.rewriteByProduct[pid]=x}state.aiRewrite=x;return x}
function rewriteReasonInput(){const pid=state.product?.id;if(!pid)return;const reason=String(($('#aiRewriteReason')||{}).value||'');if(reason.length>2000){($('#aiRewriteReason')||{}).value=reason.slice(0,2000);return}const x=state.rewriteByProduct[pid]||{productId:pid,source:($('#copy')||{}).value||state.draft||'',result:null,applied:false};x.reason=reason;state.rewriteByProduct[pid]=x;state.aiRewrite=x;state.rewriteGeneration++}
function toggleAiRewritePanel(){state.aiRewriteOpen=!state.aiRewriteOpen;const p=$('#aiRewritePanel');if(p)p.hidden=!state.aiRewriteOpen}
function renderRewritePreview(){const x=rewriteState(),box=$('#rewritePreview'),body=$('#aiRewritePreviewBody'),adopt=$('#btnRewriteAdopt'),undo=$('#btnRewriteUndo'),ta=$('#copy');if(!box)return;if(!x||!x.result||x.invalid||!ta||(ta.value!==x.source&&ta.value!==x.result)){box.hidden=true;return}box.hidden=false;if(body)body.textContent=x.result;if(adopt)adopt.disabled=!!x.applied;if(undo)undo.disabled=!x.applied}
function renderScript(){const el=$('#secScript');if(!el)return;
  if(!state.product){el.innerHTML='<h3><span class="num">2</span>脚本生产</h3><p class="muted">选择商品后可用。</p>';return}
  const s=productSettings();const engine=s.voice_engine||'qwen';const text=(state.draft!==''||state.draftProductId===state.product.id)?state.draft:(state.product.copy?.text||'');
  const coverName=state.cover?(state.cover.mode==='file'?(state.cover.name||String(state.cover.path||'').split(/[\\/]/).pop()):(state.cover.mode==='frame'?`第 ${state.cover.seconds} 秒`:'自动（第 1 秒）')):'';
  const introName=state.intro?(state.intro.name||String(state.intro.path||'').split(/[\\/]/).pop()):'';
  const outroName=state.outro?(state.outro.name||String(state.outro.path||'').split(/[\\/]/).pop()):'';
  el.innerHTML=`<h3><span class="num">2</span>脚本生产 <small class="muted">${esc(state.product.name)}</small></h3>
<label for="copy">口播文案 <span class="muted">（一行一句；多条脚本之间用单独一行 --- 分隔）</span></label>
<textarea id="copy" rows="8" oninput="onCopyInput()">${esc(text)}</textarea>
<div class="ai-row"><button class="secondary" id="btnAIGen" onclick="aiTogglePanel()">✨ AI 生成</button><button class="secondary" id="btnAIRewrite" onclick="toggleAiRewritePanel()" ${state.aiBusy?'disabled':''}>AI 洗文案</button><button class="secondary" id="btnAIPolish" onclick="aiRun('polish')">润色</button><button class="secondary" id="btnAIColloq" onclick="aiRun('colloquial')">改口语化</button><button class="secondary" id="btnAISplit" onclick="aiRun('split')">AI 断句</button><button class="secondary" id="btnAIFission" onclick="aiRun('variants')">AI 裂变 N 稿</button><button class="secondary" id="btnAIClean" onclick="cleanCopyLocal()">清理标点</button><button class="secondary" id="btnAIUndo" onclick="undoClean()" style="${state.cleanUndo===null?'display:none':''}">撤销清理</button><button class="secondary" id="btnAIPack" onclick="aiRun('pack')">投放包</button><button class="secondary" id="btnAIAudit" onclick="aiRun('audit')">合规体检</button><button class="secondary" id="btnAIFit" onclick="aiRun('fit')">按时长优化</button><span id="aiStatus" class="muted"></span></div>
<div id="aiRewritePanel" class="ai-rewrite-panel" ${state.aiRewriteOpen?'':'hidden'}><label for="aiRewriteReason">拒审原因或改写要求（可选）</label><textarea id="aiRewriteReason" rows="2" maxlength="2000" placeholder="例如：平台提示功效夸大，可不填" oninput="rewriteReasonInput()">${esc((rewriteState()||{}).reason||'')}</textarea><div class="row"><button class="primary" id="btnAIRewriteStart" onclick="aiRun('rewrite')" ${state.aiBusy?'disabled':''}>开始优化</button><span class="muted">仅优化文字，不保证平台审核结果</span></div><div id="rewriteHint" class="muted">只发送文案、产品名和这段要求</div><div id="rewritePreview" class="ai-rewrite-preview" hidden><div class="row"><b>AI 洗文案预览</b><span class="muted">未采用，不会改变编辑框</span></div><pre id="aiRewritePreviewBody"></pre><div class="row"><button class="primary small" id="btnRewriteAdopt" onclick="adoptRewrite()">采用新稿</button><button class="secondary small" id="btnRewriteUndo" onclick="undoRewrite()">撤销采用</button></div></div></div>
<div id="aiOut" class="ai-out" hidden><div class="row"><b id="aiOutTitle"></b><span class="row"><button class="secondary small" onclick="copyAiOut()">复制全部</button><button class="secondary small" onclick="closeAiOut()">收起</button></span></div><pre id="aiOutBody"></pre></div>
<div id="aiPanel" class="ai-panel" ${state.aiOpen?'':'hidden'}><div class="advgrid"><label>产品名 <input id="aiName" placeholder="例如：厨房收纳架" value="${esc(state.product.name)}"></label><label class="wide">卖点 <textarea id="aiPoints" rows="2" placeholder="一行一个，例如：分格收纳、可拆洗、适合厨房台面"></textarea></label><label>人群 <input id="aiAudience" placeholder="例如：30-50 岁上班族"></label><label>风格 <input id="aiStyle" placeholder="例如：亲切、生活化"></label></div><div class="row"><button class="primary" id="btnAIGenerateStart" onclick="aiRun('generate')">开始生成</button><button class="secondary" onclick="aiTogglePanel()">收起</button></div><div class="ai-krow"><details class="adv" open><summary>AI 接口设置（填一次就记住）</summary><div class="advgrid"><label>接口地址 <input id="aiBaseUrl" placeholder="https://api.deepseek.com"></label><label>密钥 <span class="row"><input id="aiKey" type="password" placeholder="${state.aiHasKey?'已配置（留空不改动）':'未配置'}"><button class="secondary small" onclick="saveAiSettings()">保存</button><button class="secondary small" id="aiKeyClear" onclick="clearAiKey()" style="${state.aiHasKey?'':'display:none'}">清除密钥</button></span></label><span class="muted wide">密钥只保存在这台 Win 机上，不会显示回页面；留空则保持原密钥不变。使用 AI 时，文案和产品信息会发送给所填写的服务商。</span></div><details><summary>高级：模型名称（可选）</summary><label>模型 <input id="aiModel" placeholder="服务商提供的模型 ID；留空保持原值"></label></details></details></div></div>
<div class="settings-row"><label><input id="voice" type="checkbox" ${s.voice?'checked':''} onchange="onVoiceToggle()"> 语音</label><label><input id="subs" type="checkbox" ${s.subtitles?'checked':''} onchange="onSettingChange()"> 字幕</label><label class="voice-engine-label">配音 <select id="voiceEngine" onchange="onEngineChange()"><option value="qwen" ${engine==='qwen'?'selected':''}>Qwen（原配音）</option><option value="vox" ${engine==='vox'?'selected':''}>Vox（新配音·试用）</option></select></label><label class="slider-label">语速 <input id="speed" type="range" min="0.85" max="1.5" step="0.05" value="${Number(s.speed)||1}" oninput="onSpeedInput()"> <b id="speedVal">${(Number(s.speed)||1).toFixed(2).replace(/0$/,'').replace(/\.$/,'.0')}×</b></label><label>句间停顿 <select id="sentenceGapMode" ${s.voice?'':'disabled'} onchange="onSentenceGapModeChange()"><option value="auto" ${s.sentence_gap_seconds==null?'selected':''}>自动（0.18–0.35 秒）</option><option value="manual" ${s.sentence_gap_seconds!=null?'selected':''}>手动</option></select> <input id="sentenceGap" type="number" min="0.10" max="1.00" step="0.01" value="${s.sentence_gap_seconds==null?'':Number(s.sentence_gap_seconds).toFixed(2)}" placeholder="0.10–1.00" ${s.voice?'':'disabled'} oninput="onSentenceGapInput()"> 秒</label><label>目标时长 <input id="target" type="number" min="0" max="3600" step="1" value="${Number(s.target_seconds)||''}" placeholder="不设" oninput="renderEstimate();persistSettings()"> 秒</label></div>
<div class="settings-row voice-row">
<label class="engine-control qwen-control" style="${engine==='qwen'?'':'display:none'}">Qwen 说话人 <select id="speaker" onchange="onSettingChange()"><option value="Serena" ${s.speaker==='Serena'?'selected':''}>Serena（自然女声）</option><option value="Vivian" ${s.speaker==='Vivian'?'selected':''}>Vivian（清晰女声）</option><option value="Uncle_Fu" ${s.speaker==='Uncle_Fu'?'selected':''}>Uncle Fu（成熟男声）</option></select></label>
<label class="engine-control vox-control" style="${engine==='vox'?'':'display:none'}">Vox 声音 <select id="voxVoice" onchange="onSettingChange()">${voxVoiceOptions(s.vox_voice)}</select><button class="secondary small" onclick="refreshVoxVoices()" title="重新扫描克隆文件夹">刷新音色</button><span class="muted" title="${esc(state.voxCloneDir)}">丢音频到克隆文件夹即可添加：${esc(state.voxCloneDir||'读取中…')}</span></label><button class="secondary small" id="btnVoicePreview" onclick="previewVoice()" ${state.previewBusy?'disabled':''}>${state.previewBusy?'正在试听…':'试听当前配音'}</button><span id="voicePreviewStatus" class="muted"></span><audio id="voicePreviewAudio" controls preload="none" style="${state.previewAudio?'':'display:none'}" src="${state.previewAudio?esc(state.previewAudio):''}"></audio>
</div>
<div class="estline"><span id="estChip" class="chip">预计成片约 -- 秒</span><span id="warnChip" class="chip"></span></div>
<div class="row go-row"><button class="primary big" id="btnGo" onclick="makeJobs()" ${state.indexReady?'':'disabled'}>开始出片</button><span id="goHint" class="muted">${state.indexReady?'':'先在①建立索引，就能一键出片'}</span></div>
<details class="adv"><summary>高级设置（去重 / 镜头 / BGM / 字幕样式 / 封面 / 片头片尾）</summary><div class="advgrid">
<label>去重 <select id="dedup" onchange="persistSettings()"><option value="off" ${s.dedup==='off'?'selected':''}>关闭</option><option value="light" ${s.dedup==='light'?'selected':''}>轻度</option><option value="double" ${s.dedup==='double'?'selected':''}>双重（保持文字方向）</option></select></label>
<label>人物轻美颜 <span class="row"><input id="beauty" type="checkbox" ${s.beauty?'checked':''} onchange="persistSettings()"><input id="beautyStrength" type="range" min="0" max="1" step="0.05" value="${Number(s.beauty_strength??.25)}" oninput="persistSettings()"></span></label>
<label>画面滤镜 <span class="row"><select id="visualFilter" onchange="persistSettings()"><option value="original" ${s.visual_filter==='original'?'selected':''}>原画</option><option value="clear" ${s.visual_filter==='clear'?'selected':''}>清透</option><option value="warm" ${s.visual_filter==='warm'?'selected':''}>暖色</option><option value="cinema" ${s.visual_filter==='cinema'?'selected':''}>电影</option></select><input id="visualFilterStrength" type="range" min="0" max="1" step="0.05" value="${Number(s.visual_filter_strength??.35)}" oninput="persistSettings()"></span></label>
<label>同镜头重复上限 <select id="shotReuse" onchange="persistSettings()"><option value="0" ${Number(s.shot_reuse)===0?'selected':''}>不限</option><option value="1" ${Number(s.shot_reuse)===1?'selected':''}>1 次</option><option value="2" ${Number(s.shot_reuse)===2?'selected':''}>2 次</option><option value="3" ${Number(s.shot_reuse)===3?'selected':''}>3 次</option></select></label>
<label>BGM <span class="row"><button class="secondary small" id="bgmBtn" onclick="openPicker('bgm')">${state.bgm?('BGM：'+esc(state.bgm.name||'已选')):'选择 BGM 文件'}</button><button class="secondary small" id="bgmClear" onclick="clearBgm()" style="${state.bgm?'':'display:none'}">清除</button></span></label>
<label>BGM 音量 <span class="row"><input id="bgmVol" type="range" min="0" max="100" step="5" value="${Math.round(Number(s.bgm_volume??0.2)*100)}" oninput="onBgmVol()"> <b id="bgmVolVal">${Math.round(Number(s.bgm_volume??0.2)*100)}%</b></span></label>
<label>字幕大小 <select id="subSize" onchange="persistSettings()"><option value="32" ${Number(s.subtitle_size)===32?'selected':''}>更小</option><option value="42" ${Number(s.subtitle_size)===42?'selected':''}>小</option><option value="54" ${Number(s.subtitle_size)===54?'selected':''}>中</option><option value="66" ${Number(s.subtitle_size)===66?'selected':''}>大</option></select></label>
<label>字幕位置 <select id="subPos" onchange="persistSettings()"><option value="560" ${Number(s.subtitle_bottom)===560?'selected':''}>靠上</option><option value="380" ${Number(s.subtitle_bottom)===380?'selected':''}>居中</option><option value="240" ${Number(s.subtitle_bottom)===240?'selected':''}>靠下</option></select></label>
<label>字幕描边 <select id="subBorder" onchange="persistSettings()"><option value="0" ${Number(s.subtitle_border)===0?'selected':''}>无</option><option value="3" ${Number(s.subtitle_border)===3?'selected':''}>细</option><option value="5" ${Number(s.subtitle_border)===5?'selected':''}>粗</option></select></label>
<label>封面 <span class="row"><select id="coverMode" onchange="onCoverMode()"><option value="off" ${!state.cover?'selected':''}>关闭</option><option value="auto" ${state.cover&&state.cover.mode==='auto'?'selected':''}>自动（第 1 秒）</option><option value="frame" ${state.cover&&state.cover.mode==='frame'?'selected':''}>指定秒数</option><option value="file" ${state.cover&&state.cover.mode==='file'?'selected':''}>图片文件</option></select><input id="coverSec" type="number" min="0" max="3600" step="1" style="width:64px;${state.cover&&state.cover.mode==='frame'?'':'display:none'}" value="${state.cover&&state.cover.mode==='frame'?state.cover.seconds:1}" oninput="onCoverSec()"><button class="secondary small" id="coverPick" style="${state.cover&&state.cover.mode==='file'?'':'display:none'}" onclick="openPicker('cover')">选择图片</button><button class="secondary small" id="coverUploadBtn" style="${state.cover&&state.cover.mode==='file'?'':'display:none'}" onclick="document.getElementById('coverUpload').click()">上传图片</button><input type="file" id="coverUpload" accept="image/png,image/jpeg,image/webp" style="display:none" onchange="coverUpload(event)"><span class="muted" id="coverName">${esc(coverName)}</span></span></label>
<label>片头卡 <span class="row"><input type="checkbox" id="introOn" ${state.intro?'checked':''} onchange="onCardToggle('intro')"><button class="secondary small" id="introPick" onclick="openPicker('intro')">选择图片/视频</button><select id="introSec" onchange="onCardSec('intro')"><option value="2" ${state.intro&&Number(state.intro.seconds)===2?'selected':''}>2 秒</option><option value="3" ${!state.intro||Number(state.intro.seconds)===3?'selected':''}>3 秒</option><option value="5" ${state.intro&&Number(state.intro.seconds)===5?'selected':''}>5 秒</option></select><button class="secondary small" onclick="clearCard('intro')" style="${state.intro?'':'display:none'}">清除</button><span class="muted" id="introName">${esc(introName)}</span></span></label>
<label>片尾卡 <span class="row"><input type="checkbox" id="outroOn" ${state.outro?'checked':''} onchange="onCardToggle('outro')"><button class="secondary small" id="outroPick" onclick="openPicker('outro')">选择图片/视频</button><select id="outroSec" onchange="onCardSec('outro')"><option value="2" ${state.outro&&Number(state.outro.seconds)===2?'selected':''}>2 秒</option><option value="3" ${!state.outro||Number(state.outro.seconds)===3?'selected':''}>3 秒</option><option value="5" ${state.outro&&Number(state.outro.seconds)===5?'selected':''}>5 秒</option></select><button class="secondary small" onclick="clearCard('outro')" style="${state.outro?'':'display:none'}">清除</button><span class="muted" id="outroName">${esc(outroName)}</span></span></label>
</div></details>
<details class="adv"><summary>批量出片 · 常用稿 · 检索预览</summary>
<div class="batch-panel"><b>批量方式</b><label class="radio"><input type="radio" name="bmode" value="single" ${state.batchMode==='single'?'checked':''} onchange="setBatchMode('single')"> 本条脚本出多版</label><label class="radio"><input type="radio" name="bmode" value="multi" ${state.batchMode==='multi'?'checked':''} onchange="setBatchMode('multi')"> 多条脚本，每条各出一条</label><span id="batchExtra"></span><span id="batchInfo" class="muted"></span></div>
<div class="copylib"><div class="row"><b>常用稿</b><button class="secondary small" onclick="saveVariant()">把当前文案存为常用稿</button><span class="muted">换设备也能用，点「载入」放回编辑框</span></div><div class="copylist" id="copyList"></div></div>
<div class="row"><button class="secondary" onclick="saveCopy()">保存脚本</button><button class="secondary" onclick="previewRetrieval()">检索预览</button></div>
<div id="retrieval" class="retrieval">${state.retrieval.map(x=>`<div><b>第${x.sentence_id+1}句</b> ${esc(x.line)}<br><small>${x.candidates.map(c=>esc(c.path.split(/[\\/]/).pop())+' '+Number(c.score).toFixed(3)).join(' · ')}</small></div>`).join('')}</div>
</details>`;
  renderBatchBox();renderCopyList();renderEstimate();syncSentenceGapUI();renderRewritePreview();aiBusySet(state.aiBusy,state.aiBusyMessage)}
function syncSentenceGapUI(){const voice=$('#voice'),mode=$('#sentenceGapMode'),input=$('#sentenceGap');const muted=!!voice&&!voice.checked;const auto=!mode||mode.value==='auto';if(mode)mode.disabled=muted;if(input){input.disabled=muted||auto;if(auto)input.value=''}}
function onVoiceToggle(){syncSentenceGapUI();timingChanged();persistSettings()}
function onSentenceGapModeChange(){const mode=$('#sentenceGapMode'),input=$('#sentenceGap');if(mode&&mode.value==='manual'&&input&&!String(input.value||'').trim())input.value='0.10';if(mode&&mode.value==='auto'&&input)input.value='';syncSentenceGapUI();timingChanged();persistSettings()}
function onSettingChange(){timingChanged();persistSettings()}
function onSpeedInput(){const v=Number(($('#speed')||{}).value||1);const b=$('#speedVal');if(b)b.textContent=(v).toFixed(2).replace(/0$/,'').replace(/\.$/,'.0')+'×';timingChanged();persistSettings()}
function onSentenceGapInput(){timingChanged();persistSettings()}
function onEngineChange(){syncDraft();state.product.settings=collectSettings();state.previewAudio=null;timingChanged();persistSettings();renderScript()}
function voicePreviewText(){let text=String(($('#copy')||{}).value||'').trim();if(!text)text='这是当前配音的试听片段，确认声音后再开始出片。';text=text.split(/\n+/).map(x=>x.trim()).filter(Boolean)[0]||text;return [...text].slice(0,120).join('')}
function voicePreviewKey(s,text){return JSON.stringify([state.product?.id||null,s.voice_engine||'qwen',s.speaker,s.vox_voice,Number(s.speed)||1,text])}
const VOX_PRESETS_FALLBACK=[['natural_female','自然分享女声'],['bright_female','清晰活力女声（不喊叫）'],['calm_male','沉稳成熟男声'],['story_female','温暖叙事女声']];
function voxVoiceOptions(sel){const base=(state.voxPresets&&state.voxPresets.length)?state.voxPresets.map(p=>[p.id,p.label]):VOX_PRESETS_FALLBACK;const clones=(state.voxClones||[]).map(c=>[c.id,c.label]);let opts=base.concat(clones);if(sel&&!opts.some(o=>o[0]===sel))opts=[[sel,sel+'（文件未找到）']].concat(opts);return opts.map(o=>`<option value="${esc(o[0])}" ${sel===o[0]?'selected':''}>${esc(o[1])}</option>`).join('')}
async function refreshVoxVoices(){try{const vv=await api('/api/vox-voices');state.voxPresets=vv.presets||state.voxPresets;state.voxClones=vv.clones||[];state.voxCloneDir=vv.dir||'';renderScript();toast('音色列表已刷新','ok')}catch(e){toast('刷新失败：'+e.message,'warn')}}
async function previewVoice(){
  if(state.previewBusy)return;
  const btn=$('#btnVoicePreview'),status=$('#voicePreviewStatus');
  const s=collectSettings(),text=voicePreviewText(),key=voicePreviewKey(s,text);
  state.previewBusy=true;if(btn){btn.disabled=true;btn.textContent='正在试听…'}if(status)status.textContent=s.voice_engine==='vox'?'首次试听会准备本地声音，请稍候…':'正在生成试听…';
  try{const r=await api('/api/voice-preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({engine:s.voice_engine||'qwen',voice:s.voice_engine==='vox'?(s.vox_voice||'natural_female'):(s.speaker||'Serena'),text,speed:Number(s.speed)||1}),timeoutMs:600000});
    const currentStatus=$('#voicePreviewStatus');
    if(key!==voicePreviewKey(collectSettings(),voicePreviewText())){if(currentStatus)currentStatus.textContent='设置或文案已改变，请重新试听';return}
    state.previewAudio=r.audio_url;const a=$('#voicePreviewAudio');if(a){a.src=r.audio_url;a.hidden=false;a.style.display='';a.load();try{await a.play()}catch(e){}}if(currentStatus)currentStatus.textContent=r.cold_start?'首次试听已生成，参考声音已保存在本机':'试听已生成，可再次播放';}
  catch(e){const currentStatus=$('#voicePreviewStatus');if(currentStatus)currentStatus.textContent='试听失败：'+humanMsg(e.message);toast(e.message,'warn')}
  finally{state.previewBusy=false;const b=$('#btnVoicePreview');if(b){b.disabled=false;b.textContent='试听当前配音'}}
}
function onCoverMode(){const m=$('#coverMode').value;
  if(m==='off')state.cover=null;else if(m==='auto')state.cover={mode:'auto'};
  else if(m==='frame')state.cover={mode:'frame',seconds:Number(($('#coverSec')||{}).value)||1};
  else if(m==='file'){state.cover=state.cover&&state.cover.mode==='file'?state.cover:null;if(!state.cover)toast('点「选择图片」或「上传图片」挑一张封面','ok')}
  const sec=$('#coverSec');if(sec)sec.style.display=m==='frame'?'':'none';
  const pk=$('#coverPick');if(pk)pk.style.display=m==='file'?'':'none';
  const up=$('#coverUploadBtn');if(up)up.style.display=m==='file'?'':'none';
  renderCoverName();persistSettings()}
function onCoverSec(){if(state.cover&&state.cover.mode==='frame'){state.cover.seconds=Number(($('#coverSec')||{}).value)||1;renderCoverName();renderEstimate();persistSettings()}}
function renderCoverName(){const el=$('#coverName');if(!el)return;el.textContent=state.cover?(state.cover.mode==='file'?(state.cover.name||String(state.cover.path||'').split(/[\\/]/).pop()):(state.cover.mode==='frame'?`第 ${state.cover.seconds} 秒`:'自动（第 1 秒）')):''}
async function coverUpload(ev){const f=ev.target.files&&ev.target.files[0];ev.target.value='';if(!f||!state.product)return;
  if(f.size>12*1024*1024)return toast('图片需小于 12MB','warn');
  try{const r=await api(`/api/products/${state.product.id}/cover-file`,{method:'POST',headers:{'Content-Type':f.type||'image/jpeg'},body:f,timeoutMs:60000});
    state.cover={mode:'file',path:r.path,sha256:r.sha256,name:r.name};renderCoverName();persistSettings();toast('封面已上传')}
  catch(e){toast(e.message,'warn')}}
function onCardToggle(role){if(role==='intro'){if($('#introOn').checked){if(!state.intro){toast('点「选择图片/视频」挑一个片头卡','ok');$('#introOn').checked=false}}else state.intro=null}
  if(role==='outro'){if($('#outroOn').checked){if(!state.outro){toast('点「选择图片/视频」挑一个片尾卡','ok');$('#outroOn').checked=false}}else state.outro=null}
  persistSettings()}
function onCardSec(role){const v=Number($(role==='intro'?'#introSec':'#outroSec').value)||3;
  if(role==='intro'&&state.intro)state.intro.seconds=v;if(role==='outro'&&state.outro)state.outro.seconds=v;
  renderEstimate();persistSettings()}
function clearCard(role){if(role==='intro'){state.intro=null;$('#introOn').checked=false}
  if(role==='outro'){state.outro=null;$('#outroOn').checked=false}
  renderScript();persistSettings()}
/* ---------- AI 写文案 ---------- */
function aiTogglePanel(){state.aiOpen=!state.aiOpen;const p=$('#aiPanel');if(p)p.hidden=!state.aiOpen;if(state.aiOpen)loadAiSettings()}
async function loadAiSettings(){try{const d=await api('/api/ai-settings');state.aiHasKey=!!d.has_key;
  const u=$('#aiBaseUrl');if(u&&d.base_url)u.value=d.base_url;
  const m=$('#aiModel');if(m)m.value=d.model||'';
  const k=$('#aiKey');if(k)k.placeholder=state.aiHasKey?'已配置（留空不改动）':'未配置';
  const c=$('#aiKeyClear');if(c)c.style.display=state.aiHasKey?'':'none'}catch(e){}}
async function saveAiSettings(){const u=($('#aiBaseUrl')||{}).value||'';const k=($('#aiKey')||{}).value||'';
  try{const d=await api('/api/ai-settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({base_url:u,api_key:k,model:($('#aiModel')||{}).value||''})});
    state.aiHasKey=!!d.has_key;const kk=$('#aiKey');if(kk){kk.value='';kk.placeholder=state.aiHasKey?'已配置（留空不改动）':'未配置'}
    const c=$('#aiKeyClear');if(c)c.style.display=state.aiHasKey?'':'none';toast('AI 设置已保存')}catch(e){toast(e.message,'warn')}}
async function clearAiKey(){const ok=await confirmDialog('清除已保存的 AI 密钥？');if(!ok)return;
  try{const d=await api('/api/ai-settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({clear_key:true})});
    state.aiHasKey=!!d.has_key;const c=$('#aiKeyClear');if(c)c.style.display=state.aiHasKey?'':'none';
    const kk=$('#aiKey');if(kk)kk.placeholder=state.aiHasKey?'已配置（留空不改动）':'未配置';toast('已清除密钥')}catch(e){toast(e.message,'warn')}}
function aiBusySet(busy,msg){state.aiBusy=busy;state.aiBusyMessage=busy?(msg||'正在生成…'):'';const st=$('#aiStatus');if(st)st.textContent=state.aiBusyMessage;
  ['btnAIGen','btnAIGenerateStart','btnAIRewrite','btnAIRewriteStart','btnAIPolish','btnAIColloq','btnAISplit','btnAIFission','btnAIClean','btnAIPack','btnAIAudit','btnAIFit'].forEach(id=>{const b=$('#'+id);if(b)b.disabled=busy})}
function invalidateRewritePreviews(){state.rewriteGeneration++;for(const x of Object.values(state.rewriteByProduct)){if(x)x.invalid=true}if(state.aiRewrite)state.aiRewrite.invalid=true;renderRewritePreview()}
function renderAiOut(){const box=$('#aiOut');if(!box)return;const o=state.aiOut;if(!o){box.hidden=true;return}
  box.hidden=false;const t=$('#aiOutTitle');if(t)t.textContent=o.title;const b=$('#aiOutBody');if(b)b.textContent=o.text}
function closeAiOut(){state.aiOut=null;renderAiOut()}
function rewriteCurrentMatches(x){const ta=$('#copy');return !!(x&&state.product&&x.productId===state.product.id&&ta&&ta.value===x.source&&!x.invalid)}
function adoptRewrite(){const x=rewriteState();if(!rewriteCurrentMatches(x)||!x.result)return toast('原稿或商品已改变，请重新优化','warn');const ta=$('#copy');x.adoptedSource=x.source;x.applied=true;ta.value=x.result;syncDraft();state.rewriteGeneration++;renderRewritePreview();renderEstimate();toast('已采用新稿，可继续手动修改')}
function undoRewrite(){const x=rewriteState();const ta=$('#copy');if(!x||!x.applied||!ta||!state.product||x.productId!==state.product.id)return toast('没有可撤销的采用结果','warn');if(ta.value!==x.result||state.draft!==x.result)return toast('采用后的文案已被手动修改，未覆盖当前内容','warn');ta.value=x.source;x.applied=false;syncDraft();state.rewriteGeneration++;renderRewritePreview();renderEstimate();toast('已撤销采用，恢复原稿')}
async function aiRunRewrite(){if(!state.product||state.aiBusy)return;const ta=$('#copy');if(!ta)return;const pid=state.product.id,source=ta.value,reason=String(($('#aiRewriteReason')||{}).value||'');if(!source.trim())return toast('先在编辑框里写好文案，再点「AI 洗文案」','warn');if(source.length>3000)return toast('文案不能超过 3000 字，请先缩短','warn');if(reason.length>2000)return toast('拒审原因不能超过 2000 字，请先缩短','warn');
  invalidateRewritePreviews();const generation=state.rewriteGeneration;state.rewriteByProduct[pid]={productId:pid,source,reason,result:null,applied:false};state.aiRewrite=state.rewriteByProduct[pid];
  aiBusySet(true,'正在优化文案…');
  try{const provider=await api('/api/ai-settings');if(state.rewriteGeneration!==generation||!state.product||state.product.id!==pid||($('#copy')||{}).value!==source)return toast('原稿或商品已改变，请重新优化','warn');if(!provider||!provider.has_key)return toast('还没有配置 AI 密钥，请先在「AI 接口设置」里保存','warn');if(state.aiConsent!==provider.base_url){const ok=await confirmDialog('使用 AI 会把本次文案、产品名和改写要求发送给你填写的服务商（'+provider.base_url+'）。确认后继续。');if(!ok)return;state.aiConsent=provider.base_url}if(state.rewriteGeneration!==generation||!state.product||state.product.id!==pid||($('#copy')||{}).value!==source)return toast('原稿或商品已改变，请重新优化','warn');const r=await api(`/api/products/${pid}/ai-copy`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:'rewrite',text:source,brief:{name:state.product.name||'',reason}}),timeoutMs:120000});if(state.rewriteGeneration!==generation||!state.product||state.product.id!==pid||($('#copy')||{}).value!==source)return toast('文案或商品已改变，旧结果未采用','warn');if(!r||typeof r.text!=='string'||!r.text.trim()||r.text.length>3000)throw Error('AI 返回内容不完整，请重试一次');const x=state.rewriteByProduct[pid];x.result=r.text.trim();x.invalid=false;state.aiRewrite=x;renderScript();toast('已生成预览，请确认是否采用')}
  catch(e){toast(e.message,'warn')}
  finally{aiBusySet(false)}}
function cleanCopyLocal(){const ta=$('#copy');if(!ta||!ta.value.trim())return toast('编辑框还是空的','warn');
  const src=ta.value;let out=src.replace(/[\u{1F000}-\u{1FAFF}\u2600-\u27BF]/gu,'').replace(/([，。！？!?；;：:,])\1+/g,'$1');
  out=out.split(/\r?\n/).flatMap(line=>{if(/^\s*-{3,}\s*$/.test(line))return ['---'];let s=line.trim();if(!s)return [];return s.split(/(?<=[。！？!?；;])\s*/).map(x=>x.replace(/[。！？!?；;：:，,、]+$/,'').trim()).filter(Boolean)}).join('\n');
  if(out===src)return toast('没有发现需要清理的标点');state.cleanUndo=src;ta.value=out;syncDraft();renderScript();toast('已清理标点，可点撤销恢复原稿')}
function undoClean(){if(state.cleanUndo===null)return;const ta=$('#copy');if(ta){ta.value=state.cleanUndo;syncDraft();state.cleanUndo=null;renderScript();toast('已撤销清理')}}
async function copyAiOut(){if(!state.aiOut)return;const text=state.aiOut.text;
  try{await navigator.clipboard.writeText(text);toast('已复制')}catch(e){
    try{const ta=document.createElement('textarea');ta.value=text;document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove();toast('已复制')}catch(e2){toast('复制失败，请手动选择文字复制','warn')}}}
async function aiRun(mode){if(mode==='rewrite')return aiRunRewrite();if(!state.product||state.aiBusy)return;const ta=$('#copy');if(!ta)return;
  const label={generate:'生成',polish:'润色',colloquial:'改口语化',split:'断句',variants:'裂变',pack:'投放包',audit:'合规体检',fit:'按时长改写'}[mode]||mode;
  aiBusySet(true,'正在'+label+'（约 10 秒）…');
  try{invalidateRewritePreviews();let body={mode:mode};
  if(mode==='generate'){body.brief={name:($('#aiName')||{}).value||'',points:($('#aiPoints')||{}).value||'',audience:($('#aiAudience')||{}).value||'',style:($('#aiStyle')||{}).value||''};
    if(!body.brief.name.trim()&&!body.brief.points.trim())return toast('先填一下产品名或卖点','warn');
    if(ta.value.trim()){const ok=await confirmDialog('会用生成结果替换编辑框里的文案，继续？');if(!ok)return}}
  else{if(!ta.value.trim())return toast('先在编辑框里写好文案，再点「'+label+'」','warn');
    body.text=ta.value;body.brief={name:($('#aiName')||{}).value||state.product.name||''}}
  if(mode==='fit'){const tv=parseInt(($('#target')||{}).value||'0',10)||0;if(tv<3||tv>600)return toast('先在设置行「目标时长」里填 3–600 秒，再点「按时长优化」','warn');if(!state.indexReady)return toast('按时长优化需要先建立索引','warn');body.brief.target_seconds=tv;body.settings=collectSettings()}
  if(mode==='variants'){const raw=prompt('需要几份文案？','3');if(raw===null)return;const n=Number(raw);if(!Number.isInteger(n)||n<2||n>20)return toast('裂变数量需为 2–20 的整数','warn');body.brief.count=n}
  const provider=await api('/api/ai-settings');
  if(state.aiConsent!==provider.base_url){const ok=await confirmDialog('使用 AI 会把本次文案、产品名和卖点发送给你填写的服务商（'+provider.base_url+'）。确认后继续。');if(!ok)return;state.aiConsent=provider.base_url}
  const r=await api(`/api/products/${state.product.id}/ai-copy`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),timeoutMs:120000});
    if(mode==='pack'||mode==='audit'){state.aiOut={title:mode==='pack'?'投放包（标题 / 标签 / 置顶评论）':'合规体检结果',text:r.text};renderAiOut();toast(mode==='pack'?'投放包已生成，可直接复制':'体检完成，结果已显示')}
    else if(mode==='variants'){if(!Array.isArray(r.variants)||!r.variants.length)throw Error('AI 没有返回可用裂变稿');const count=`${r.produced??r.variants.length}/${r.requested??r.variants.length}`;state.aiOut={title:`AI 裂变结果（${count} 份${r.partial?'，数量不足':''}）`,text:r.variants.map((x,i)=>`稿${i+1}\n${x}`).join('\n\n')+(r.partial?`\n\n数量不足：${r.reason||'有效稿件不足'}`:'')};renderAiOut();const ok=await confirmDialog(`已得到 ${count} 份稿${r.partial?`，${r.reason||'数量不足'}`:''}。替换编辑框并用 --- 分隔？`);if(ok){ta.value=r.variants.join('\n---\n');syncDraft();renderBatchBox();renderEstimate();toast('裂变稿已放入编辑框，可继续修改')}}
    else if(mode==='split'||mode==='fit'){ta.value=r.text;syncDraft();renderEstimate();toast(mode==='fit'?'已按目标时长改写，可以手动微调':'已按口播节奏重新断句，可以手动微调')}
    else{ta.value=r.text;syncDraft();renderEstimate();toast('已填入编辑框，可以手动修改')}}
  catch(e){toast(e.message,'warn')}
  finally{aiBusySet(false)}}
/* ---------- 常用稿 ---------- */
function renderCopyList(){const el=$('#copyList');if(!el)return;const vs=(state.product&&state.product.copy&&state.product.copy.variants)||[];
  if(!vs.length){el.innerHTML='<p class="muted">还没有常用稿——写好一条文案后点上面「存为常用稿」。</p>';return}
  el.innerHTML=vs.map((v,i)=>{const first=splitLines(v)[0]||'';const label=first.length>22?first.slice(0,22)+'…':first;
    return `<div class="copyrow"><span class="copytext" title="${esc(v)}"><b>${i+1}.</b> ${esc(label)} <span class="muted">${splitLines(v).length} 句</span></span><span class="row"><button class="secondary small" onclick="loadVariant(${i})">载入</button><button class="secondary small" onclick="deleteVariant(${i})">删除</button></span></div>`}).join('')}
async function saveVariant(){if(!state.product)return;const ta=$('#copy');const text=ta?ta.value:'';if(!text.trim())return toast('编辑框还是空的','warn');
  const vs=((state.product.copy||{}).variants||[]).slice();if(vs.some(v=>v===text))return toast('这条已经在常用稿里了');
  if(vs.length>=20)return toast('常用稿最多 20 条，先删掉不用的','warn');
  vs.push(text);
  try{const p=await api(`/api/products/${state.product.id}/copy`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:text,variants:vs})});
    state.product=p;renderCopyList();toast('已存为常用稿')}catch(e){toast(e.message,'warn')}}
async function loadVariant(i){const vs=(state.product.copy||{}).variants||[];if(!vs[i])return;
  const ta=$('#copy');if(ta){ta.value=vs[i];syncDraft();renderEstimate()}
  toast('已载入编辑框，确定后点「开始出片」')}
async function deleteVariant(i){const vs=((state.product.copy||{}).variants||[]).slice();if(!vs[i])return;
  const ok=await confirmDialog('删除这条常用稿？删除后无法恢复。');if(!ok)return;
  vs.splice(i,1);
  try{const p=await api(`/api/products/${state.product.id}/copy`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:($('#copy')||{}).value||state.product.copy.text||'',variants:vs})});
    state.product=p;renderCopyList();toast('已删除')}catch(e){toast(e.message,'warn')}}
/* ---------- 弹窗选择器（文件夹 / BGM / 图片 / 图片视频） ---------- */
function ensureModal(){let h=$('#modalHost');if(!h){h=document.createElement('div');h.id='modalHost';document.body.appendChild(h);document.addEventListener('keydown',e=>{if(e.key==='Escape'&&state.picker)closePicker()})}return h}
function closePicker(){const h=$('#modalHost');if(h)h.innerHTML='';state.picker=null}
const PICK_KIND={source:'folder',output:'folder',bgm:'audio',cover:'image',intro:'media',outro:'media'};
async function openPicker(role){if(!state.product)return toast('请先选择商品','warn');
  const kind=PICK_KIND[role]||'folder';const cur=state.product[role];
  const start=kind==='folder'?((cur&&cur.path)||''):((state.product.source||{}).path||'');
  state.picker={role,kind,path:start,listing:null,error:'',_seq:0};renderPicker();
  await pickerList(start);
  if(state.picker&&state.picker.error&&state.picker.path){await pickerList('')}}
function pickerJump(){const pk=state.picker;if(!pk)return;const inp=$('#pkPath');if(!inp)return;
  let v=inp.value.trim();if(!v)return;
  if(/^[a-zA-Z]:$/.test(v))v=v+'/';
  if(/^[a-zA-Z]:[\\/]/.test(v)||v.startsWith('\\\\')||v.startsWith('/')){pickerList(v)}
  else{toast('请输入完整路径，例如：D:\\素材 或 \\\\server\\share','warn')}}
async function pickerList(path){const pk=state.picker;if(!pk)return;pk.path=path||'';pk.listing=null;pk.error='';renderPicker();
  const seq=++pk._seq;const pick=pk.kind==='folder'?'':pk.kind;
  try{const d=await api('/api/browse?path='+encodeURIComponent(path||'')+(pick?('&pick='+pick):''));if(state.picker!==pk||pk._seq!==seq)return;pk.listing=d;pk.path=d.path||''}
  catch(e){if(state.picker!==pk||pk._seq!==seq)return;pk.listing=null;pk.error=e.message}
  renderPicker()}
function renderPicker(){const pk=state.picker;if(!pk)return;const h=ensureModal();const roleName=pk.kind==='folder'?(pk.role==='source'?'素材文件夹':'输出文件夹'):(pk.role==='bgm'?'BGM 文件':pk.role==='cover'?'封面图片':(pk.role==='intro'?'片头卡':'片尾卡'));
  let body;if(pk.error)body=`<div class="modal-error">${esc(pk.error)}</div>`;else if(!pk.listing)body='<p class="muted">加载中…</p>';
  else{const dirs=pk.listing.directories||[];const files=(pk.listing.files||[]).concat(pk.kind==='audio'?(pk.listing.audio_files||[]).filter(a=>!(pk.listing.files||[]).some(b=>b.path===a.path)):[]);
    body=`<div class="dirlist">${dirs.map(d=>`<div class="dirrow"><span>${/^[A-Za-z]:[\\/]$/.test(d.path)?'💽':'📁'} ${esc(d.name)}</span><button class="secondary" data-nav="${esc(d.path)}">进入</button></div>`).join('')}${files.map(f=>`<div class="dirrow"><span>${(/\.(jpe?g|png|webp|bmp)$/i.test(f.name)?'🖼 ':/\.(mp4|mov|m4v|avi|mkv|webm|wmv|ts|mts)$/i.test(f.name)?'🎬 ':pk.kind==='audio'?'🎵 ':'📄 ')}${esc(f.name)}</span><button class="secondary" data-pickfile="${esc(f.path)}">选择</button></div>`).join('')}${(!dirs.length&&!files.length)?'<p class="muted">（没有子文件夹'+(pk.kind==='folder'?'':'，也没有可用文件')+'）</p>':''}</div>`}
  const cur=pk.path||'此电脑（Win 机）';
  const hint=pk.kind==='folder'?'浏览的是这台 Win 机的本机目录（C盘、D盘、自定义文件夹、NAS 共享都可以选）；索引会读取所选文件夹及其所有子文件夹里的视频。':(pk.kind==='audio'?'浏览的是这台 Win 机的本机目录；进入文件夹后，点音频文件右侧「选择」（支持 mp3 / wav / m4a / aac / flac，也可粘贴路径直达）。':(pk.kind==='image'?'进入文件夹后，点图片右侧「选择」（支持 jpg / png / webp），也可以直接粘贴路径。':'进入文件夹后，点图片或视频右侧「选择」（图片 jpg/png/webp；视频 mp4/mov 等），也可以直接粘贴路径。'));
  h.innerHTML=`<div class="overlay" data-close="1"></div><div class="modal" role="dialog" aria-label="选择${roleName}"><div class="modal-head"><b>选择${roleName}</b><button class="x" data-close="1" aria-label="关闭">✕</button></div><div class="jumprow"><button class="secondary" data-home="1" title="回到此电脑">🏠</button><input id="pkPath" placeholder="直接粘贴任意路径（如 D:\\素材 或 \\\\server\\share），回车跳转"><button class="secondary" data-jump="1">跳转</button></div><span class="muted pickhint">${hint}</span><div class="pickpath">当前位置：${esc(cur)}</div><div class="modal-body">${body}</div><div class="modal-foot"><span class="row"><button class="secondary" data-up="1" ${pk.listing&&pk.listing.parent!==null?'':'disabled'}>上一层</button>${pk.kind==='folder'?`<button class="primary" data-pick="1" ${pk.path?'':'disabled'}>✓ 选择此文件夹</button>`:''}</span></div></div>`;
  h.querySelectorAll('[data-close]').forEach(el=>el.onclick=()=>{closePicker()});
  h.querySelectorAll('[data-nav]').forEach(el=>el.onclick=()=>pickerList(el.dataset.nav));
  h.querySelectorAll('[data-pickfile]').forEach(el=>el.onclick=()=>confirmFilePick(el.dataset.pickfile));
  const hb=h.querySelector('[data-home]');if(hb)hb.onclick=()=>pickerList('');
  const jb=h.querySelector('[data-jump]');if(jb)jb.onclick=pickerJump;
  const pi=h.querySelector('#pkPath');if(pi)pi.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();pickerJump()}};
  const up=h.querySelector('[data-up]');if(up)up.onclick=()=>{const p2=state.picker;if(p2&&p2.listing&&p2.listing.parent!==null)pickerList(p2.listing.parent)};
  const pick=h.querySelector('[data-pick]');if(pick)pick.onclick=confirmPick}
async function confirmPick(){const pk=state.picker;if(!pk||!pk.path)return;
  try{await api(`/api/products/${state.product.id}/${pk.role}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:pk.path})});closePicker();await load()}catch(e){toast(e.message,'warn')}}
function fileKind(name){return /\.(jpe?g|png|webp|bmp)$/i.test(name)?'image':'video'}
async function confirmFilePick(path){const pk=state.picker;if(!pk||!state.product)return;const name=String(path).split(/[\\/]/).pop();
  try{
    if(pk.role==='bgm'){const r=await api(`/api/products/${state.product.id}/bgm`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});
      state.bgm=r;const b=$('#bgmBtn');if(b)b.textContent='BGM：'+(r.name||name);const cl=$('#bgmClear');if(cl)cl.style.display='';closePicker();persistSettings();toast('BGM 已选择')}
    else if(pk.role==='cover'){state.cover={mode:'file',path:path,name:name};closePicker();renderCoverName();persistSettings();toast('封面已选择')}
    else if(pk.role==='intro'||pk.role==='outro'){const sec=Number($(pk.role==='intro'?'#introSec':'#outroSec').value)||3;
      const card={path:path,name:name,seconds:sec,kind:fileKind(name)};
      if(pk.role==='intro'){state.intro=card;const c=$('#introOn');if(c)c.checked=true;const n=$('#introName');if(n)n.textContent=name}
      else{state.outro=card;const c=$('#outroOn');if(c)c.checked=true;const n=$('#outroName');if(n)n.textContent=name}
      closePicker();renderEstimate();persistSettings();toast((pk.role==='intro'?'片头卡':'片尾卡')+'已选择')}
  }catch(e){toast(e.message,'warn')}}
function clearBgm(){state.bgm=null;const b=$('#bgmBtn');if(b)b.textContent='选择 BGM 文件';const cl=$('#bgmClear');if(cl)cl.style.display='none';persistSettings()}
/* ---------- 素材与索引动作 ---------- */
async function indexNow(){const btn=$('#btnIndex'),st=$('#idxStatus');
  try{if(btn){btn.disabled=true;btn.textContent='正在校验素材，请稍候…'}
    if(st)st.textContent='正在校验素材指纹（需要完整读一遍所选文件夹，先别关页面）…';
    await api(`/api/products/${state.product.id}/index`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',timeoutMs:600000});
    idxDirty=true;idxAt=0;await load()}catch(e){toast(e.message,'warn');if(btn){btn.disabled=false;btn.textContent='开始建索引'}}}
/* ---------- 脚本与批量 ---------- */
function parseBatchScripts(t){return String(t||'').split(/\n\s*-{3,}\s*\n/).map(s=>s.trim()).filter(Boolean)}
function setBatchMode(m){state.batchMode=m;renderBatchBox()}
function onCopyInput(){syncDraft();state.rewriteGeneration++;const x=rewriteState();if(x){x.invalid=true;x.applied=false;renderRewritePreview()}if(state.batchMode==='multi')renderBatchBox();timingChanged()}
function fmtMinutes(min){if(min<60)return min+' 分钟';const h=Math.floor(min/60),m=min%60;return m?`${h} 小时 ${m} 分钟`:`${h} 小时`}
function renderBatchBox(){const ex=$('#batchExtra'),info=$('#batchInfo');if(!ex||!info)return;
  if(state.batchMode==='single'){ex.innerHTML=`数量 <input id="batchCount" type="number" min="1" max="500" value="${state.batchCount||1}" oninput="onBatchCount()"> <span class="muted">同一条脚本生成几个版</span>`;info.textContent='';onBatchCount()}
  else{ex.innerHTML='';info.textContent=`已识别 ${parseBatchScripts(($('#copy')||{}).value).length} 条脚本`}}
function onBatchCount(){const el=$('#batchCount'),info=$('#batchInfo');if(!el||!info)return;
  let v=Math.max(1,Math.min(500,parseInt(el.value,10)||1));state.batchCount=v;
  info.textContent=`${v} 版 ≈ 共约 ${fmtMinutes(v*2)}渲染（每版约 2 分钟）`}
async function saveCopy(){const c=$('#copy');if(!c||!state.product)return;state.draft=c.value;
  await api(`/api/products/${state.product.id}/copy`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:state.draft})});toast('已保存')}
async function previewRetrieval(){try{state.retrieval=await api('/api/retrieval',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({product_id:state.product.id,text:($('#copy')||{}).value}) ,timeoutMs:600000});const r=$('#retrieval');if(r)r.innerHTML=state.retrieval.map(x=>`<div><b>第${x.sentence_id+1}句</b> ${esc(x.line)}<br><small>${x.candidates.map(c=>esc(c.path.split(/[\\/]/).pop())+' '+Number(c.score).toFixed(3)).join(' · ')}</small></div>`).join('')}catch(e){toast(e.message,'warn')}}
async function makeJobs(){if(!state.product)return;const bj=$('#btnGo'),info=$('#batchInfo'),gh=$('#goHint'),wait='正在为每句挑选镜头（首次要加载模型，约 1 分钟，页面别关）…';
  syncDraft();
  const settings=collectSettings();
  if(bj){bj.disabled=true;bj.textContent='准备中…'}if(gh)gh.textContent='正在为每句挑选镜头，页面别关…';
  try{await api(`/api/products/${state.product.id}/copy`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:($('#copy')||{}).value||''})})}catch(e){}
  const url=`/api/products/${state.product.id}/jobs`;
  try{
    if(state.batchMode==='multi'){const scripts=parseBatchScripts(($('#copy')||{}).value);if(!scripts.length)return toast('没识别到脚本：多条脚本之间请用单独一行 --- 分隔','warn');
      const ok=await confirmDialog(`将按 ${scripts.length} 条脚本分别创建任务，继续？`);if(!ok)return;
      if(bj)bj.disabled=true;
      for(let i=0;i<scripts.length;i++){if(info)info.textContent=`创建中 ${i+1}/${scripts.length} · ${wait}`;try{await api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({count:1,text:scripts[i],settings:settings}),timeoutMs:600000})}catch(e){toast(`第 ${i+1} 条创建失败：${e.message}（前 ${i} 条已入队）`,'warn');break}}}
    else{const count=Math.max(1,Math.min(500,Number(($('#batchCount')||{}).value)||1));state.batchCount=count;if(bj)bj.disabled=true;if(info)info.textContent=wait;
      await api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({count:count,settings:settings}),timeoutMs:600000})}
    persistSettings();
    toast(state.batchMode==='multi'?'已创建任务，进度见③任务队列':'已加入队列，进度见③任务队列');
    idxDirty=true;idxAt=0;await refreshLive()}
  catch(e){toast(e.message,'warn')}
  finally{if(bj){bj.disabled=false;bj.textContent='开始出片'}if(gh)gh.textContent=state.indexReady?'':'先在①建立索引，就能一键出片';if(info&&state.batchMode==='single')onBatchCount()}}
/* ---------- 任务队列 / 结果 ---------- */
const STAGE_CN={starting:'准备中',embedding:'解析素材',indexed:'解析完成',voice:'配音中',render:'剪辑中',export:'导出中',done:'完成',failed:'失败',cancelled:'已取消',restart:'已中断',queued:'排队中',paused:'已暂停',resuming:'继续中'};
const STATUS_CN={queued:'排队中',running:'进行中',done:'已完成',failed:'失败',cancelled:'已取消',interrupted:'已中断',paused:'已暂停'};
function cn(m,s){return m[s]||s||''}
function tagCls(s){return s==='running'?'run':s==='done'?'ok':s==='failed'?'bad':s==='paused'?'pause':(s==='cancelled'||s==='interrupted')?'off':''}
function skipReasonText(rows){const uniq=[...new Set((rows||[]).map(x=>x&&(x.reason_cn||x.reason)).filter(Boolean))];return uniq.length?uniq.join('、'):'原因见服务端记录'}
function skipSummary(st){const sk=(st&&st.index&&st.index.skipped)||[];if(!sk.length)return '';const by={};sk.forEach(x=>{const k=x.reason_cn||'读取失败';by[k]=(by[k]||0)+1});return `；跳过 ${sk.length} 个（${Object.keys(by).map(k=>k+' '+by[k]+' 个').join('，')}）`}
function jobLine(j){let extra='';
  if(['running','paused'].includes(j.status)&&j.stage==='embedding'&&Number.isInteger(j.indexed))extra=` · 已解析 ${j.indexed}/${j.total||'?'}`+(j.reused?`（复用 ${j.reused}）`:'')+(j.skipped?`（跳过 ${j.skipped}）`:'');
  if(j.status==='done'&&Number.isInteger(j.indexed))extra=` · 成功 ${j.indexed}/${j.total||'?'}`+(j.skipped?`（跳过 ${j.skipped}：${esc(skipReasonText(j.skipped_files))}）`:'');
  if(j.stage==='render'&&Number.isInteger(j.progress))extra='';
  const v=Number.isInteger(j.variant)?' · v'+j.variant:'';
  return `${j.id.slice(0,8)}${v} · ${esc(cn(STAGE_CN,j.stage))} · ${j.progress||0}%${extra}`}
function humanMsg(m){m=String(m||'').trim();if(!m)return '';
  const rules=[[/CUDA unavailable|no CPU\/fake/i,'这台机器现在用不了 GPU：可能被游戏或其它任务占着，稍后再试'],
   [/generation changed|changed during build|snapshot changed|obsolete|generation mismatch/i,'素材文件夹改过了，需要重新建索引'],
   [/select source|select output|请先选/i,'请先选好素材文件夹和输出文件夹'],
   [/script empty|脚本为空/i,'脚本是空的'],
   [/no indexed candidate/i,'有句子没有匹配到镜头，请检查索引或换句脚本'],
   [/verify\//i,'成片质检没通过，请把这条反馈给技术'],
   [/video specs/i,'成片规格检查没过（帧率/分辨率异常），请把这条反馈给技术'],
   [/audio levels/i,'成片音量检查没过（音量异常），请把这条反馈给技术'],
   [/PCM correlation|codec\/phase|phase search/i,'成片音频质检没过，请把这条反馈给技术'],
   [/voice\//i,'配音环节出错'],
   [/BGM/i,'BGM 文件不可用，请重新选择'],
   [/export\//i,'导出环节出错'],
   [/render\//i,'剪辑环节出错'],
   [/index\//i,'建索引环节出错'],
   [/请求超时/i,'请求超时，请再试一次'],
   [/未知的 AI 模式/,'服务端还在旧版本：新功能需要服务重启后才生效，稍后再试'],
   [/Failed to fetch|NetworkError|fetch failed|ERR_/i,'连不上服务，请检查网络或稍后重试']];
  for(const [re,msg] of rules){if(re.test(m))return msg}
  return m}
function humanError(j){const e=(j.error||{});return humanMsg(e.message||e.check||e.stage||'')}
const QUEUE_PER_PAGE=10;
const QUEUE_FILTERS=[['all','全部'],['active','进行中'],['failed','失败'],['done','完成']];
function queueFilterJobs(){const f=state.queueFilter||'all';const j=state.jobs;
  if(f==='active')return j.filter(x=>['queued','running','paused'].includes(x.status));
  if(f==='failed')return j.filter(x=>x.status==='failed');
  if(f==='done')return j.filter(x=>x.status==='done');
  return j}
function queueSetFilter(f){state.queueFilter=f;state.queuePage=1;renderQueue()}
function queueGo(d){const pages=Math.max(1,Math.ceil(queueFilterJobs().length/QUEUE_PER_PAGE));state.queuePage=Math.min(pages,Math.max(1,(state.queuePage||1)+d));renderQueue()}
/* paused jobs are held by the server, not merely hidden: 继续 hands them back.
   暂停 is a request: 索引在文件边界、出片在步骤边界停下，状态变「已暂停」后再点「继续」。 */
function jobActions(j){if(!state.product)return '';
  const btn=(label,fn)=>`<button class="secondary small" onclick="${fn}('${j.id}')">${label}</button>`;
  if(j.status==='running')return btn('暂停','pauseJob')+btn('取消','cancelJob');
  if(j.status==='paused')return btn('继续','resumeJob')+btn('取消','cancelJob');
  if(j.status==='queued')return btn('暂停','pauseJob')+btn('取消','cancelJob');
  return ''}
function renderQueue(){const el=$('#secQueue');if(!el)return;
  const all=state.jobs,f=state.queueFilter||'all';
  const cnt={all:all.length,active:all.filter(x=>['queued','running','paused'].includes(x.status)).length,failed:all.filter(x=>x.status==='failed').length,done:all.filter(x=>x.status==='done').length};
  const jobs=[...queueFilterJobs()].sort((a,b)=>(b.created_at||0)-(a.created_at||0));
  const total=jobs.length;const pages=Math.max(1,Math.ceil(total/QUEUE_PER_PAGE));
  const page=Math.min(pages,Math.max(1,state.queuePage||1));state.queuePage=page;
  const slice=jobs.slice((page-1)*QUEUE_PER_PAGE,page*QUEUE_PER_PAGE);
  const chips=QUEUE_FILTERS.map(([k,label])=>`<button class="qfbtn${f===k?' active':''}" onclick="queueSetFilter('${k}')">${label} <b>${cnt[k]}</b></button>`).join('');
  const pager=`<div class="qfilter">${chips}</div><div class="pager"><span class="muted small">共 ${total} 条</span>${pages>1?`<button class="secondary small" onclick="queueGo(-1)" ${page<=1?'disabled':''}>‹ 上一页</button><span class="small pageno">第 ${page}/${pages} 页</span><button class="secondary small" onclick="queueGo(1)" ${page>=pages?'disabled':''}>下一页 ›</button>`:''}</div>`;
  const queued=all.filter(j=>j.status==='queued').length,terminal=all.filter(j=>['done','failed','cancelled','interrupted'].includes(j.status)).length,w=state.worker||{};const disk=w.disk||{};
  const status=!w.alive&&state.worker?`<div class="errline">出片线程已停止：需要重启服务（或联系技术支持），已停放的任务可先取消</div>`:disk.ok===false?`<div class="skipline">磁盘空间不足（剩 ${(Number(disk.free_bytes||0)/1073741824).toFixed(1)} GB）：已暂停接单，清理空间后会自动继续</div>`:disk.free_bytes!=null?`<div class="muted small">磁盘剩余 ${(Number(disk.free_bytes)/1073741824).toFixed(1)} GB</div>`:'';
  const tools=`<div class="row queue-tools">${queued?`<button class="secondary small" onclick="cancelAllQueued()">取消全部排队中 (${queued})</button>`:''}<label class="muted small" title="导出文件核验并持久化完成后，保留导出文件并删除任务目录中的本地成片临时文件；失败会保留文件，报告、封面和少量音频仍保留"><input type="checkbox" ${state.prefs.cleanup_after_success?'checked':''} onchange="toggleCleanup(this)"> 出片成功核验后保留导出、清理本地成片临时文件（失败保留）</label><button class="secondary small" onclick="cleanupRecords()" ${terminal?'':'disabled'}>清理已结束记录</button></div>`;
  el.innerHTML=`<h3><span class="num">3</span>任务队列 <small class="muted">自动刷新</small></h3>${status}${tools}${pager}<div class="list">${slice.map(j=>`<div class="item" title="${esc((j.error||{}).message||'')}"><span class="it-main">${jobLine(j)}${['running','queued','paused'].includes(j.status)?`<span class="pbar"><i style="width:${Math.max(2,j.progress||0)}%"></i></span>`:''}</span><span class="row">${j.script_summary&&j.lines_count?`<span class="muted small">「${esc(j.script_summary)}」</span>`:''}<b class="tag ${tagCls(j.status)}">${esc(cn(STATUS_CN,j.status))}</b>${jobActions(j)}</span>${j.cleanup&&j.cleanup.files>0?`<div class="muted small">· 已清理 ${fmtBytes(j.cleanup.bytes)}</div>`:''}${j.status==='paused'?`<div class="skipline">暂停点＝文件/步骤边界：正在跑的那一步会先结束，暂停期间不再开始新步骤；点「继续」接着跑</div>`:''}${j.status==='failed'&&j.error?`<div class="errline">失败原因：${esc(humanError(j))}</div>`:''}${j.skipped_files&&j.skipped_files.length?`<div class="skipline">跳过 ${j.skipped_files.length} 个素材：${esc(j.skipped_files.map(s=>s.name+'（'+(s.reason_cn||s.reason||'读取失败')+'）').join('、'))} · 下次建索引会自动重试</div>`:''}</div>`).join('')||`<p class="muted">${f==='all'?'暂无任务——创建后会在这里逐条显示进度':'当前筛选下没有任务'}</p>`}</div>`}
function resultJobs(){return state.jobs.filter(j=>j.status==='done'&&j.artifact&&j.lines_count!==undefined).sort((a,b)=>{
  const ft=(b.finished_at||b.created_at||0)-(a.finished_at||a.created_at||0);if(ft)return ft;
  const ct=(b.created_at||0)-(a.created_at||0);if(ct)return ct;
  return String(b.id||'').localeCompare(String(a.id||''));
})}
function resultGroups(rows){const byKey={};(rows||resultJobs()).forEach(j=>{const ts=j.finished_at||j.created_at||0,dk=dayKey(ts),key=dk+'|'+(j.script_summary||'')+'|'+(j.lines_count||0);let g=byKey[key];if(!g)g=byKey[key]={key,summary:j.script_summary||'（无标题）',lines:j.lines_count||0,items:[],date:dk,finished_at:ts};g.items.push(j);g.finished_at=Math.max(g.finished_at,ts)});
  return Object.values(byKey).sort((a,b)=>(b.finished_at||0)-(a.finished_at||0)||String(b.key).localeCompare(String(a.key)))}
function renderResults(){const el=$('#secResults');if(!el)return;
  if(!state.product){el.innerHTML='<h3><span class="num">4</span>结果</h3><p class="muted">选择商品后显示。</p>';return}
  const rows=resultJobs(),total=rows.length,pages=Math.max(1,Math.ceil(total/12));const requested=Number.parseInt(state.resultsPage,10);state.resultsPage=Math.min(pages,Math.max(1,Number.isFinite(requested)?requested:1));const page=state.resultsPage,slice=rows.slice((page-1)*12,page*12),groups=resultGroups(slice);
  const pager=`<div class="pager"><span class="muted small">共 ${total} 条成片</span><button class="secondary small" onclick="resultsGo(-1)" ${page<=1?'disabled':''}>‹ 上一页</button><span class="small pageno">第 ${page}/${pages} 页</span><button class="secondary small" onclick="resultsGo(1)" ${page>=pages?'disabled':''}>下一页 ›</button></div>`;
  el.innerHTML=`<h3><span class="num">4</span>结果 <small class="muted">完成后可播放 / 下载</small></h3>${total?pager:`<p class="muted">还没有成片——出片完成后会在这里以卡片显示。</p>`}${groups.map(d=>`<div class="daygroup"><div class="resgroup"><span class="chip">${esc(d.date)}</span>${resCard(d)}</div></div>`).join('')}${total?pager:''}`}
function resultsGo(d){const pages=Math.max(1,Math.ceil(resultJobs().length/12));const current=Number.parseInt(state.resultsPage,10);state.resultsPage=Math.min(pages,Math.max(1,(Number.isFinite(current)?current:1)+d));renderResults()}
function resCard(g){const first=g.items[0];const vcount=g.items.length;
  return `<div class="rescard"><div class="resthumb"><img loading="lazy" src="/api/thumbs/${first.id}?product_id=${state.product.id}&v=${Math.floor((first.finished_at||0)*1000)}" alt="" onerror="this.closest('.resthumb').classList.add('noimg')"></div><div class="resbody"><div class="restitle">「${esc(g.summary)}」 <span class="muted">${g.lines} 句 · ${vcount} 版</span></div>${g.items.map(j=>`<div class="verrow"><span class="vchip">v${j.variant||0}</span><span class="vmeta">${fmtSec(j.duration)} · ${fmtBytes(j.bytes)} · ${fmtClock(j.finished_at)}</span><span class="vacts"><a class="miniact" href="/api/results/${j.id}?product_id=${state.product.id}" target="_blank" rel="noopener">播放</a><a class="miniact" href="/api/results/${j.id}?product_id=${state.product.id}" download="job-${j.id.slice(0,8)}.mp4">下载</a><button class="miniact" onclick="openFolder('${j.id}')">打开文件夹</button><a class="miniact" href="/api/reports/${j.id}?product_id=${state.product.id}" target="_blank" rel="noopener">报告</a></span></div>`).join('')}</div></div>`}
async function openFolder(jid){try{const r=await api(`/api/products/${state.product.id}/open-folder`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:jid})});
  toast(r.opened?'已在 Win 机上打开文件夹：'+r.path:'文件夹：'+r.path)}catch(e){toast(e.message,'warn')}}
let liveBusy=false,reloadAt=0,idxDirty=true,idxAt=0;
async function refreshLive(){if(liveBusy)return;liveBusy=true;
  try{state.jobs=await api('/api/jobs'+(state.product?'?product_id='+state.product.id:''));try{state.worker=await api('/api/worker')}catch(e){};
    renderQueue();const sig=JSON.stringify(state.jobs.filter(j=>j.status==='done').map(j=>[j.id,j.finished_at]));if(sig!==state.lastJobsSig){state.lastJobsSig=sig;renderResults()}
    if(state.product&&(idxDirty||Date.now()-idxAt>20000)){idxDirty=false;idxAt=Date.now();await refreshIdxState()}
    if(state.needReload&&Date.now()-reloadAt>8000){reloadAt=Date.now();await load()}
  }catch(e){state.needReload=true}
  finally{liveBusy=false}}
async function createProduct(){const n=$('#newName').value.trim();if(!n)return;try{const p=await api('/api/products',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n})});$('#newName').value='';state.product=p;state.previewAudio=null;state.draft='';await load();toast('商品已创建')}catch(e){toast(e.message,'warn')}}
async function cancelJob(id){const ok=await confirmDialog('取消这个任务？会尽快停下；建索引已完成的文件会保留，下次可续跑。');if(!ok)return;
  try{await api(`/api/products/${state.product.id}/cancel`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:id})});await refreshLive();toast('已取消')}catch(e){toast(e.message,'warn')}}
async function cancelAllQueued(){const n=state.jobs.filter(j=>j.status==='queued').length;if(!n||!state.product)return;if(!await confirmDialog(`确定取消当前商品全部 ${n} 条排队中的任务？`))return;try{const r=await api(`/api/products/${state.product.id}/cancel-queued`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});await refreshLive();toast(`已取消 ${r.cancelled} 条`)}catch(e){toast(e.message,'warn')}}
async function toggleCleanup(el){const old=state.prefs.cleanup_after_success;try{state.prefs=await api('/api/prefs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cleanup_after_success:!!el.checked})})}catch(e){el.checked=old;state.prefs.cleanup_after_success=old;toast(e.message,'warn')}}
async function cleanupRecords(){if(!state.product)return;try{const d=await api(`/api/products/${state.product.id}/cleanup-records`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dry_run:true})});if(!d.total)return;const ok=await confirmDialog(`将清理 ${d.total} 条已结束记录（完成 ${d.counts.done} / 失败 ${d.counts.failed} / 已取消 ${d.counts.cancelled} / 已中断 ${d.counts.interrupted}），并删除它们在本机的任务文件；NAS 与输出文件夹的成片不受影响。继续？`);if(!ok)return;const r=await api(`/api/products/${state.product.id}/cleanup-records`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dry_run:false})});await refreshLive();toast(`已清理 ${Object.keys(r.removed||{}).length} 条，释放约 ${(Number(r.freed_bytes||0)/1048576).toFixed(1)} MB`)}catch(e){toast(e.message,'warn')}}
async function archiveProduct(){if(!state.product)return;if(!await confirmDialog(`从商品列表删除「${state.product.name}」？任务记录与文件会保留在本机，可恢复。`))return;try{const id=state.product.id;await api(`/api/products/${id}/archive`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});state.product=null;state.noAutoSelect=true;state.resultsPage=1;await load();toast('商品已删除')}catch(e){toast(e.message,'warn')}}
async function pauseJob(id){
  try{await api(`/api/products/${state.product.id}/pause`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:id})});await refreshLive();
    const j=state.jobs.find(x=>x.id===id)||{};
    toast(j.status==='paused'?'已暂停，点「继续」接着跑':'已请求暂停：会在当前文件/步骤结束后停住（状态变「已暂停」后再点「继续」）')}
  catch(e){toast(e.message,'warn')}}
async function resumeJob(id){
  try{await api(`/api/products/${state.product.id}/resume`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:id})});await refreshLive();toast('已继续')}catch(e){toast(e.message,'warn')}}
/* ---------- init ---------- */
$('#prodSel').onchange=e=>{syncDraft();state.noAutoSelect=false;const next=state.products.find(p=>p.id===e.target.value)||null;state.product=next;state.resultsPage=1;state.previewAudio=null;state.draft=next?(state.draftByProduct[next.id]??(next.copy?.text||'')):'';state.draftProductId=next?.id||null;state.retrieval=[];state.bgm=null;state.cover=null;state.intro=null;state.outro=null;state.indexInfo=null;state.indexReady=false;state.rewriteGeneration++;state.aiRewrite=null;idxDirty=true;idxAt=0;renderScript();return load()};
$('#newName').addEventListener('keydown',e=>{if(e.key==='Enter')createProduct()});
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();if($('#btnGo')&&!$('#btnGo').disabled)makeJobs()}});
load();setInterval(()=>{if(document.visibilityState==='visible')refreshLive()},2000);
