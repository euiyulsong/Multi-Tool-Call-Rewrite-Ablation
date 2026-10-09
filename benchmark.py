"""Controlled multi-tool query-rewrite ablation. Python 3.10+."""
import argparse, asyncio, hashlib, json, random, time, statistics
from pathlib import Path

VARIANTS = ['joint', 'per_tool_with_list', 'per_tool_local']
def dumps(x): return json.dumps(x, ensure_ascii=False, sort_keys=True)
def digest(x): return hashlib.sha256(dumps(x).encode()).hexdigest()
def decoded(x): return json.loads(x) if isinstance(x,str) else x

def validate(x, kind, indices):
    if kind == 'route':
        if not isinstance(x,list) or any(type(i) is not int or i not in indices for i in x) or len(set(x))!=len(x):
            raise ValueError('Router must return unique integer idx list')
    elif kind == 'rewrite':
        if not isinstance(x,list) or any(not isinstance(v,dict) or set(v)!={'idx','query'} or type(v['idx']) is not int or not isinstance(v['query'],str) or not v['query'].strip() or '\n' in v['query'] for v in x):
            raise ValueError('Invalid rewrite records')
        if sorted(v['idx'] for v in x)!=sorted(indices): raise ValueError('Missing/extra/duplicate idx')
    elif not isinstance(x,list) or any(not isinstance(v,dict) or set(v)!={'arguments'} or not isinstance(v['arguments'],dict) for v in x):
        raise ValueError('Invalid argument reconstruction')
    return x

class API:
    def __init__(self,a):
        from openai import AsyncOpenAI
        self.client=AsyncOpenAI(max_retries=0, timeout=120)
        self.a=a; self.sem=asyncio.Semaphore(a.concurrency)
        self.cache=Path(a.out)/'cache'; self.cache.mkdir(parents=True,exist_ok=True)
    async def call(self,system,payload,kind,indices,tag):
        key=digest([self.a.model,self.a.effort,system,payload,kind,indices,tag])
        path=self.cache/(key+'.json')
        if path.exists(): return json.loads(path.read_text())
        start=time.perf_counter(); usage=[]; errors=[]
        for attempt in range(3):
            try:
                async with self.sem:
                    r=await self.client.responses.create(model=self.a.model, reasoning={'effort':self.a.effort},
                        instructions=system+' Return JSON only; no Markdown.',input=dumps(payload),max_output_tokens=4096)
                usage.append(r.usage.model_dump())
                if r.status!='completed': raise ValueError('Incomplete response: '+str(r.status))
                value=validate(json.loads(r.output_text),kind,indices)
                result={'value':value,'error':None,'seconds':time.perf_counter()-start,'usage':usage,'attempts':attempt+1}
                path.write_text(dumps(result)); return result
            except Exception as e:
                errors.append(type(e).__name__+': '+str(e))
                if attempt<2: await asyncio.sleep(2**attempt)
        return {'value':None,'error':errors,'seconds':time.perf_counter()-start,'usage':usage,'attempts':3}

ROUTE='Use conversation context to select tools needed ONLY for the LAST user request. Return only a list of tool idx integers. Do not output names, arguments, or explanations. Do not execute historical requests again.'
REWRITE='Use conversation context to rewrite ONLY the LAST user request for each target tool. Produce a self-contained, one-line natural-language query per target idx. Include all relevant explicit argument values and resolve references using history. Preserve constraints, negation, units and dates. Exclude work belonging to other tools. Never invent values or tool results. If a result is unavailable, retain the dependency explicitly. Do not output argument JSON or call tools. Return [{"idx": integer, "query": string}], exactly one record per target idx. Multiple calls to the same tool must all be represented in its query.'
DECODE='Given ONLY this tool schema and standalone query, reconstruct every requested invocation of this tool. Return [{"arguments": {...}}]. Use schema parameter names. Never invent values. If unresolved use the string "UNRESOLVED". No access to original conversation.'

def parse_hermes(raw):
    import re
    candidates=[]; skipped=0
    for r in raw:
        try:
            tools=decoded(r['tools'])
            tools=[t.get('function',t) for t in tools]
            names=[t['name'] for t in tools]
            if len(names)!=len(set(names)): raise ValueError('duplicate tool names')
            history=[]; users=0
            for m in r['conversations']:
                role={'human':'user','gpt':'assistant'}.get(m['from'],m['from'])
                if role=='system': continue
                if role=='user': users+=1
                text=m['value']
                if role=='assistant' and history and history[-1]['role']=='user':
                    blocks=re.findall(r'<tool_call>(.*?)</tool_call>',text,re.S)
                    if blocks:
                        calls=[json.loads(c) for c in blocks]
                        if any(c['name'] not in names or not isinstance(decoded(c['arguments']),dict) for c in calls):
                            raise ValueError('invalid calls')
                        gold=sorted(set(names.index(c['name']) for c in calls))
                        if len(gold)>=2:
                            sid=str(r.get('id',digest(r)[:16]))+':u'+str(users)
                            candidates.append({'source_id':sid,'tools':[dict(t,idx=i) for i,t in enumerate(tools)],
                                'gold_idx':gold,'gold_arguments':{str(i):[decoded(c['arguments']) for c in calls if c['name']==names[i]] for i in gold},
                                'messages':list(history),'native_users':users})
                history.append({'role':role,'content':text})
        except (ValueError,TypeError,KeyError): skipped+=1
    return candidates,skipped

def prepare(a):
    # Public raw HTTP: no HF login, no inherited HF_TOKEN.
    import urllib.request
    repo='NousResearch/hermes-function-calling-v1'
    revision=a.revision
    if a.raw_file:
        payload=Path(a.raw_file).read_bytes(); revision='local'
    else:
        cache=Path(a.source_cache);cache.mkdir(parents=True,exist_ok=True)
        if revision=='main':
            with urllib.request.urlopen('https://huggingface.co/api/datasets/'+repo,timeout=60) as r:
                revision=json.load(r)['sha']
        path=cache/(revision+'-func-calling-singleturn.json')
        if not path.exists():
            url='https://huggingface.co/datasets/'+repo+'/resolve/'+revision+'/func-calling-singleturn.json'
            try:
                with urllib.request.urlopen(url,timeout=120) as r: payload=r.read()
            except Exception as e:
                raise RuntimeError('Public Hermes download failed. Retry, or use --raw-file /path/to/func-calling-singleturn.json') from e
            json.loads(payload)  # Reject incomplete/non-JSON downloads before cache write.
            temp=path.with_suffix('.tmp');temp.write_bytes(payload);temp.replace(path)
        payload=path.read_bytes()
    candidates,skipped=parse_hermes(json.loads(payload))
    random.Random(a.seed).shuffle(candidates)
    unique={}
    for r in candidates: unique.setdefault(digest([r['messages'],r['tools'],r['gold_arguments']]),r)
    chosen=list(unique.values())[:a.n]
    if len(chosen)<a.n: raise RuntimeError(f'Only {len(chosen)} usable multi-tool samples; lower --n')
    rows=[]
    for common in chosen:
        sid=common['source_id']; messages=common['messages']
        rows.append(dict(common,id=sid+':single',split='single' if common['native_users']==1 else 'multi_native'))
        if common['native_users']==1:
            rows.append(dict(common,id=sid+':multi',split='multi_derived',messages=messages+[
                {'role':'assistant','content':'I have noted your request. No tools have been called yet.'},
                {'role':'user','content':'Please carry out that request now, keeping all the details and constraints I gave you.'}]))
    Path(a.data).parent.mkdir(parents=True,exist_ok=True)
    Path(a.data).write_text('\n'.join(dumps(x) for x in rows)+'\n')
    manifest={'dataset':repo,'revision':revision,'source_sha256':hashlib.sha256(payload).hexdigest(),
        'seed':a.seed,'source_samples':len(chosen),'rows':len(rows),'available':len(unique),'skipped_rows':skipped,
        'sha256':digest(rows),'multi_turn':'derived reference-back, not native multi-user conversation'}
    Path(a.data+'.manifest.json').write_text(json.dumps(manifest,indent=2))
    print('Prepared',len(rows),'rows;',len(unique),'available; revision',revision)

def exact_calls(pred,gold):
    # Unordered multiset, preserves repeated calls. Strict types and values.
    return sorted(dumps(v) for v in pred)==sorted(dumps(v) for v in gold)

def aggregate(records):
    groups={}
    for r in records:
        groups.setdefault((r['split'],r['variant']),[]).append(r)
    out=[]
    for (split,v),rs in sorted(groups.items()):
        times=sorted(r['rewrite_seconds'] for r in rs)
        out.append({'split':split,'variant':v,'n':len(rs),'router_exact':statistics.mean(r['router_exact'] for r in rs),
            'rewrite_valid_rate':statistics.mean(r['rewrite_valid'] for r in rs),
            'selected_tool_argument_exact':sum(r['tool_correct'] for r in rs)/max(1,sum(r['tool_total'] for r in rs)),
            'all_gold_arguments_exact':statistics.mean(r['all_gold_correct'] for r in rs),
            'rewrite_seconds_mean':statistics.mean(times),'rewrite_seconds_p95':times[min(len(times)-1,int(.95*len(times)))],
            'rewrite_input_tokens_mean':statistics.mean(r['input_tokens'] for r in rs),'rewrite_output_tokens_mean':statistics.mean(r['output_tokens'] for r in rs)})
    return out

async def run(a):
    api=API(a); rows=[json.loads(x) for x in Path(a.data).read_text().splitlines() if x.strip()]
    config={'model':a.model,'effort':a.effort,'route':a.route,'seed':a.seed,'data_hash':digest(rows),'version':1,'concurrency':a.concurrency}
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    cfg=out/'config.json'
    if cfg.exists() and json.loads(cfg.read_text())!=config: raise ValueError('Output config differs. Use new --out directory.')
    cfg.write_text(dumps(config)); log=out/'results.jsonl'
    records=[json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    done={(r['id'],r['variant'],r['repeat']) for r in records}
    for rep in range(a.repeats):
      for row in rows:
        tools=row['tools']; allidx=[t['idx'] for t in tools]
        route=await api.call(ROUTE,{'messages':row['messages'],'tools':tools},'route',allidx,[row['id'],rep,'router']) if a.route=='predicted' else {'value':row['gold_idx'],'error':None}
        indices=route['value'] or []; selected=[t for t in tools if t['idx'] in indices]
        variants=VARIANTS.copy(); random.Random(a.seed+rep+int(digest(row['id'])[:8],16)).shuffle(variants)
        for variant in variants:
            if (row['id'],variant,rep) in done: continue
            started=time.perf_counter()
            if variant=='joint':
                rew=[await api.call(REWRITE,{'messages':row['messages'],'target_tools':selected},'rewrite',indices,[row['id'],rep,variant]) ] if indices else []
            else:
                async def one(t):
                    payload={'messages':row['messages'],'target_tools':[t]}
                    if variant=='per_tool_with_list': payload['router_selected_tools']=selected
                    return await api.call(REWRITE,payload,'rewrite',[t['idx']],[row['id'],rep,variant,t['idx']])
                rew=await asyncio.gather(*(one(t) for t in selected))
            elapsed=time.perf_counter()-started
            # Cache contains original timings; parallel critical path is max. Warm/resumed runs are not re-timed.
            if rew: elapsed=max(r['seconds'] for r in rew)
            queries=[q for r in rew for q in (r['value'] or [])]
            async def evaluate(q):
                t=next(t for t in selected if t['idx']==q['idx'])
                z=await api.call(DECODE,{'tool':t,'query':q['query']},'decode',[],[row['id'],rep,'decoder',q['idx']])
                return q['idx'], z
            decoded_queries=await asyncio.gather(*(evaluate(q) for q in queries))
            correct={i: z['error'] is None and exact_calls([v['arguments'] for v in z['value']],row['gold_arguments'].get(str(i),[])) for i,z in decoded_queries}
            usage=[u for r in rew for u in r['usage']]
            rec={'id':row['id'],'source_id':row.get('source_id',row['id']),'split':row['split'],'repeat':rep,'variant':variant,
                'gold_idx':row['gold_idx'],'selected_idx':indices,'router_exact':route['error'] is None and sorted(indices)==sorted(row['gold_idx']),
                'rewrite_valid':route['error'] is None and all(r['error'] is None for r in rew),'queries':queries,'rewrite_details':rew,
                'decoder_details':decoded_queries,'tool_correct':sum(correct.values()),'tool_total':len(indices),
                'all_gold_correct':route['error'] is None and sorted(indices)==sorted(row['gold_idx']) and all(correct.get(i,False) for i in row['gold_idx']),
                'rewrite_seconds':elapsed,'input_tokens':sum(u.get('input_tokens',0) for u in usage),'output_tokens':sum(u.get('output_tokens',0) for u in usage)}
            with log.open('a') as f: f.write(dumps(rec)+'\n')
            records.append(rec); done.add((row['id'],variant,rep)); print(row['id'],variant,'gold_exact=',rec['all_gold_correct'],flush=True)
            (out/'summary.json').write_text(json.dumps(aggregate(records),indent=2))

def main():
    p=argparse.ArgumentParser(); p.add_argument('command',choices=['prepare','run']); p.add_argument('--data',default='data/samples.jsonl');p.add_argument('--revision',default='main');p.add_argument('--raw-file');p.add_argument('--source-cache',default='data/source_cache');p.add_argument('--n',type=int,default=100);p.add_argument('--seed',type=int,default=42);p.add_argument('--model',default='gpt-6-luna');p.add_argument('--effort',default='none');p.add_argument('--concurrency',type=int,default=4);p.add_argument('--repeats',type=int,default=1);p.add_argument('--route',choices=['oracle','predicted'],default='oracle');p.add_argument('--out',default='results/oracle');a=p.parse_args()
    if a.n<1 or a.concurrency<1 or a.repeats<1: p.error('n/concurrency/repeats must be positive')
    if a.command=='prepare': prepare(a)
    else: asyncio.run(run(a))
if __name__=='__main__': main()
