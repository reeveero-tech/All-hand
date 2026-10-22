# PREP audit probe (read-only characterization of current behaviour; not a test gate, not R-176..R-181 work).
# Run from repo root: python3 docs/audit/M8/prep/probe_agent_queue_bounds.py
import asyncio, sys, tracemalloc, inspect
sys.path.insert(0,'.')
from agent_civilization.core.agents.base_agent import BaseAgent
from agent_civilization.core.agents import communication_hub as ch
abstract=sorted(BaseAgent.__abstractmethods__)
ns={m:(lambda self,*a,**k: None) for m in abstract}
async def _ex(self,w): pass
if 'execute' in abstract: ns['execute']=_ex
A=type('ProbeAgent',(BaseAgent,),ns)
hub_cls=next(c for n,c in inspect.getmembers(ch,inspect.isclass) if n=='CommunicationHub')
async def main():
    a=A('probe',hub_cls()); a.is_active=True
    print('abstract methods stubbed:',abstract,'capacity=',a.capacity,'workqueue.maxsize=',a.workqueue.maxsize,'inbox.maxsize=',a._inbox.maxsize)
    tracemalloc.start(); s0=tracemalloc.get_traced_memory()[0]
    N=100_000
    for i in range(N): a.add_work({'task_id':i,'data':{'x':'y'*64}})
    s1=tracemalloc.get_traced_memory()[0]
    print(f'add_work x{N}: qsize={a.workqueue.qsize()} (capacity {a.capacity}) accepted_without_backpressure={a.workqueue.qsize()==N} mem_delta={(s1-s0)/1e6:.1f}MB')
    from agent_civilization.core.agents.base_agent import Message
    for i in range(20_000):
        await a._handle_message(Message(sender='s',recipient='probe',content={'action':'note','i':i}))
    s2=tracemalloc.get_traced_memory()[0]
    print(f"non-task messages x20000 -> memory['messages'] len={len(a.memory['messages'])} mem_delta={(s2-s1)/1e6:.1f}MB (no cap)")
    a.is_active=False; before=a.workqueue.qsize(); a.add_work({'task_id':'late'})
    print('add_work while inactive: silently dropped =',a.workqueue.qsize()==before, '(no return value / no error)')
asyncio.run(main())
