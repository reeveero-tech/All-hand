# PREP audit probe (read-only characterization of current behaviour; not a test gate, not R-176..R-181 work).
# Run from repo root: python3 docs/audit/M8/prep/probe_communication_hub.py
import asyncio, sys, time, tracemalloc
sys.path.insert(0, '.')
from agent_civilization.core.agents.base_agent import BaseAgent, Message
from agent_civilization.core.agents.communication_hub import CommunicationHub

A = type('ProbeAgent', (BaseAgent,), {})


async def main():
    hub = CommunicationHub()
    # H1: delivery to a registered but inactive agent
    idle = A('idle', hub); await hub.register_agent(idle)
    r = await hub.send_message(Message(sender='s', recipient='idle', content={'action': 'note'}))
    print(f"H1 inactive recipient: DeliveryResult.success={r.success} inbox_size={idle._inbox.qsize()} "
          f"stats.delivered={hub.get_stats()['messages_delivered']} -> reported delivered but dropped={r.success and idle._inbox.qsize()==0}")
    # H2: broadcast with zero active recipients still reports success
    r2 = await hub.send_message(Message(sender='s', recipient='broadcast', content={'action': 'note'}))
    print(f"H2 broadcast, 0 active recipients: success={r2.success} counted_as_delivered={hub.get_stats()['messages_delivered']-1} actually_queued={idle._inbox.qsize()}")
    # H3: broadcast echoes to the sender
    hub2 = CommunicationHub(); s = A('sender', hub2); s.is_active = True; await hub2.register_agent(s)
    await hub2.send_message(Message(sender='sender', recipient='broadcast', content={'action': 'note'}))
    print(f"H3 broadcast echo to sender: sender_inbox={s._inbox.qsize()}")
    # H4: subscribe() stores callbacks, nothing ever invokes them
    called = []
    await hub2.subscribe('agent_registered', lambda *a: called.append(a))
    await hub2.register_agent(A('late', hub2))
    print(f"H4 subscriber callbacks invoked after register: {len(called)}")
    # H6: broadcast puts the SAME Message object into every inbox (shared mutable reference)
    h6 = CommunicationHub(); xs = [A(f'r{i}', h6) for i in range(3)]
    for a in xs: a.is_active = True; await h6.register_agent(a)
    await h6.send_message(Message(sender='z', recipient='broadcast', content={'action': 'note'}))
    got = [a._inbox.get_nowait() for a in xs]; got[0].content['mutated_by'] = 'r0'
    print(f"H6 broadcast aliasing: same object in all inboxes={all(g is got[0] for g in got)}; mutation by r0 visible to r2={got[2].content.get('mutated_by')=='r0'}")
    # H5: broadcast cost and memory vs N active agents (one message each)
    for n in (1_000, 10_000):
        h = CommunicationHub()
        for i in range(n):
            a = A(f'a{i}', h); a.is_active = True; await h.register_agent(a)
        tracemalloc.start(); m0 = tracemalloc.get_traced_memory()[0]; t0 = time.perf_counter()
        for _ in range(10):
            await h.send_message(Message(sender='x', recipient='broadcast', content={'action': 'note'}))
        dt = (time.perf_counter() - t0) / 10; m1 = tracemalloc.get_traced_memory()[0]; tracemalloc.stop()
        print(f"H5 N={n}: broadcast avg {dt*1000:.1f} ms, lock acquisitions per broadcast (by code reading: 1 + N) = {n+1}, "
              f"queued refs after 10 broadcasts = {sum(a._inbox.qsize() for a in h.agents.values())}, mem_delta={(m1-m0)/1e6:.1f}MB")

asyncio.run(main())
