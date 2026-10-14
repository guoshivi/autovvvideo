"""AI copywriting for the workbench (feature 1).

Talks to an OpenAI-compatible chat endpoint (DeepSeek by default).  Only the
product copy text the user typed travels out; no media, no voice, no files.
The prompts encode the Qianchuan compliance rules (no price/promo/medical/
absolute claims, no unsourced product claims).
"""
import json, urllib.request, urllib.error
from .ai_endpoint import normalize_endpoint
from .copy_tools import parse_script_variants

class AIError(ValueError): pass

_MAX_TEXT = 3000

_SYSTEM = (
    "你是资深抖音带货口播编导，负责写通用实用品的短视频口播文案。硬性规则：\n"
    "① 只输出文案正文，不要序号、标题、解释、引号、括号说明；\n"
    "② 每句单独一行；全文 6–9 句，每句 9–20 个汉字；\n"
    "③ 一句一个意思，短句为主，读起来有节奏，像真人对着镜头说话；\n"
    "④ 从给的卖点里挑 2–3 个最有画面感的展开写，不要罗列卖点、不要堆形容词；\n"
    "⑤ 第一句用痛点或具体场景抓人；最后一句自然收束（引导评论或关注），不硬催下单；\n"
    "⑥ 只使用我提供的信息，绝不编造成分、功效、数据、价格；没有把握的内容不写；\n"
    "⑦ 禁用：价格类（元/折/赠/送/限时/抢）；医疗与功效类（治疗/缓解/消炎/根治/祛病等）；"
    "绝对化（最/第一/顶级/100%/完美/必须）；拉踩对比；平台违规词；\n"
    "⑧ 不用 emoji，不用连续感叹号，标点干净。"
)

_MODE_EXTRA = {
    'generate': '',
    'polish': "这一次是润色：把用户给的文案改得更顺口、更有节奏、更抓人；保持原意和句子大意，"
              "可以把句子改短改顺；每句单独一行；不要解释。",
    'colloquial': "这一次是口语化：把用户给的文案改得更像真人随口说出来的话；更短的句子、更口语"
                  "的词，去掉书面词；保留原意；每句单独一行；不要解释。",
    'split': "这一次是断句排版：把用户给的口播文案重新断句。不改任何内容和措辞，只调整断句位置："
             "一句一个完整意思；每句 8–22 个汉字；太长的句子在自然停顿处断开，太短的相邻句子可以合并；"
             "每句单独一行，不要序号、不要解释、不要多余的标点。",
    'pack': "这一次是生成发布物料：根据口播文案输出三部分，严格按下面格式，不要任何客套与解释——\n"
            "标题：\n1. ……\n2. ……\n3. ……\n"
            "标签：#…… #……（8 个，与品类/人群/使用场景相关）\n"
            "置顶评论：……（1 条，像店家自己留言，口语化，自然引导互动）\n"
            "标题每条不超过 20 个字、要有钩子；三部分同样遵守全部禁用规则。",
    'audit': "这一次是合规体检：逐句检查用户文案的违规风险。对每个问题输出一行："
             "第X句：「原文片段」——问题类型（价格促销/医疗功效/绝对化用语/无依据宣称/平台违禁词）＋修改建议。"
             "没问题的句子不写；全部无问题时只输出一行：未发现明显违规。不要重写全文、不要多余解释。",
    'fit': "这一次是按目标时长改写：把用户给的口播文案改写成适合指定时长口播的版本。"
           "按调用方提供的实测或估计语速预算字数；"
           "保持原意与核心卖点，可以合并、扩写或精简句子；一句一意、短句为主；"
           "严格遵守全部禁用规则；一句一行，只输出改写后的文案，不要解释。",
    'variants': "这一次是 AI 裂变：把用户文案改写成多份不同开头和表达、但事实核心一致的短稿。"
                "只能使用用户提供的信息，禁止编造功能、成分、数据、价格或医疗效果。"
                "严格只输出 JSON：{\"variants\":[\"稿件1\",\"稿件2\"]}，不要 Markdown、不要解释。",
}

def build_messages(mode, text, brief):
    if mode not in _MODE_EXTRA: raise AIError('未知的 AI 模式')
    system = _SYSTEM + (('\n' + _MODE_EXTRA[mode]) if _MODE_EXTRA[mode] else '')
    brief = brief if isinstance(brief, dict) else {}
    if mode == 'generate':
        name = str(brief.get('name') or '').strip()
        points = str(brief.get('points') or '').strip()
        audience = str(brief.get('audience') or '').strip()
        style = str(brief.get('style') or '').strip()
        if not (name or points):
            raise AIError('先填一下产品名或卖点，再点「AI 生成」')
        user = (f"产品：{name or '未提供'}\n"
                f"卖点：{points or '未提供'}\n"
                f"目标人群：{audience or '普通家庭用户'}\n"
                f"偏好风格：{style or '亲切、生活化'}\n"
                f"请按规则直接输出口播文案正文。")
    elif mode in ('split', 'pack', 'audit', 'fit', 'variants'):
        body = str(text or '').strip()
        if not body:
            raise AIError('先在编辑框里写好文案，再点这个按钮')
        name = str(brief.get('name') or '').strip()
        if mode == 'fit':
            try:
                seconds = int(brief.get('target_seconds') or 0)
            except (TypeError, ValueError):
                seconds = 0
            if not 3 <= seconds <= 600:
                raise AIError('先在设置行里填「目标时长」（3–600 秒），再点「按时长优化」')
            try:speed=float(brief.get('speed') or 1.0)
            except (TypeError,ValueError):raise AIError('语速需在 0.85–1.5 之间') from None
            if not 0.85<=speed<=1.5:raise AIError('语速需在 0.85–1.5 之间')
            cps=brief.get('chars_per_second')
            try:cps=float(cps)
            except (TypeError,ValueError):
                try:cps=4.0*speed
                except (TypeError,ValueError):cps=4.0
            cps=max(1.0,min(8.0,cps))
            budget=brief.get('chars_budget')
            if type(budget) is not int or budget<1:budget=round(seconds*cps)
            user = ((f"产品：{name}\n" if name else '') +
                    f"目标时长：{seconds} 秒（所选语速 {speed:.2f}×，按每秒约 {cps:.2f} 个汉字，扣除停顿及画面固定时长后全文约 {budget} 个汉字）\n文案：\n{body}\n\n请按要求直接输出改写后的文案。")
        elif mode == 'variants':
            try:n=max(2,min(20,int(brief.get('count') or 3)))
            except (TypeError,ValueError):n=3
            user=((f"产品：{name}\n" if name else '')+f"需要 {n} 份稿件。\n文案：\n{body}\n\n严格按 JSON 格式返回。")
        else:
            user = ((f"产品：{name}\n" if name else '') + f"文案：\n{body}\n\n请按要求直接输出。")
    else:
        user = f"文案：\n{str(text or '').strip()}\n\n请按要求改写后直接输出正文。"
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AIError('AI 接口重定向已拒绝，请填写最终 HTTPS 接口地址')

def _post(url, payload, key, timeout=90):
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise AIError(f'AI 服务返回错误（{e.code}），请检查接口、模型及账户额度') from None
    except AIError:
        raise
    except Exception as e:
        raise AIError('连不上 AI 服务，请确认这台 Win 机可以上网') from e

def extract_text(payload):
    try:
        content = payload['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError):
        raise AIError('AI 返回内容不完整，请重试一次')
    if not isinstance(content, str):
        raise AIError('AI 返回内容不完整，请重试一次')
    text = content.strip()
    if text.startswith('```'):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].strip() == '```':
            lines = lines[:-1]
        text = '\n'.join(lines).strip()
    text = '\n'.join(line.rstrip() for line in text.splitlines()).strip()
    if not text:
        raise AIError('AI 没有返回文字内容，请重试一次')
    if len(text) > _MAX_TEXT:
        raise AIError('AI 返回内容过长，请重试一次')
    return text

def run(base_url, api_key, model, mode, text, brief):
    key = str(api_key or '')
    try:
        url = normalize_endpoint(base_url or 'https://api.deepseek.com', bool(key))
    except ValueError as e:
        raise AIError(str(e)) from None
    if not key:
        raise AIError('还没有配置 AI 密钥：在「AI 生成」面板里展开「AI 接口设置」，填一下接口地址和密钥')
    payload = {
        'model': str(model or 'deepseek-chat'),
        'messages': build_messages(mode, text, brief),
        'temperature': {'split': 0.3, 'audit': 0.2, 'fit': 0.8, 'variants': 0.9}.get(mode, 1.0),
        'max_tokens': 1600 if mode in ('pack', 'audit', 'variants') else 800,
        'stream': False,
    }
    data = _post(url, payload, key)
    result=extract_text(data)
    if mode=='variants':
        try: return json.dumps({'variants':parse_script_variants(result)},ensure_ascii=False)
        except ValueError as exc: raise AIError(str(exc)) from None
    return result
