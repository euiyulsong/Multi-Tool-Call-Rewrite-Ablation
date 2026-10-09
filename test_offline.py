import asyncio, json, tempfile
from pathlib import Path
from types import SimpleNamespace
import benchmark as b

class Fake:
    def __init__(self,a): pass
    async def call(self,system,payload,kind,indices,tag):
        if kind=='route': value=[0,1]
        elif kind=='rewrite': value=[{'idx':i,'query':'query'+str(i)} for i in indices]
        else: value=[{'arguments':{'x':payload['tool']['idx']}}]
        b.validate(value,kind,indices)
        return {'value':value,'error':None,'seconds':.01,'usage':[],'attempts':1}

def test():
    for invalid in [None,['0'],[0,0],[2]]:
        try: b.validate(invalid,'route',[0,1])
        except ValueError: pass
        else: raise AssertionError(invalid)
    assert b.exact_calls([{'x':1},{'x':2}],[{'x':2},{'x':1}])
    assert not b.exact_calls([{'x':1}],[{'x':'1'}])
    with tempfile.TemporaryDirectory() as d:
        a=SimpleNamespace(data=d+'/data.jsonl',out=d+'/out',model='mock',effort='none',route='predicted',seed=42,concurrency=4,repeats=1)
        row={'id':'a','split':'single','messages':[{'role':'user','content':'test'}],'tools':[{'idx':0},{'idx':1}],'gold_idx':[0,1],'gold_arguments':{'0':[{'x':0}],'1':[{'x':1}]}}
        Path(a.data).write_text(json.dumps(row)); b.API=Fake
        asyncio.run(b.run(a)); asyncio.run(b.run(a))
        rows=[json.loads(x) for x in Path(a.out+'/results.jsonl').read_text().splitlines()]
        assert len(rows)==3 and all(r['all_gold_correct'] for r in rows)
        assert len(json.loads(Path(a.out+'/summary.json').read_text()))==3
    print('Offline checks passed')
if __name__=='__main__': test()
