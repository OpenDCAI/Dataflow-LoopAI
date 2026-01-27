import queue
import time
from multiprocessing import Queue
from loopai.agents import StarterAgent


def extract_state(sg, config, running=True) -> dict:
    thread_states = sg.get_state(config)
    agent_event = sg.agent_event
    state = agent_event.state

    if thread_states.interrupts:
        interrupt_value = thread_states.interrupts[0].value
    else:
        interrupt_value = None
    current = None
    if state is not None and 'current' in state:
        current = state['current']
    stream_message = agent_event.stream_message
    event_streaming = 'not_ready'
    if agent_event.custom_info:
        stream_message_state = agent_event.custom_info.get('llm_node', {}).get('data', {}).get('stream_message_state', 'not_ready')
        event_streaming = stream_message_state
    return {
        "running": running,
        "event_streaming": event_streaming, # the agent is yielding event_streaming messages
        "waiting_llm": stream_message is not None, # the agent is waiting for the LLM response, in starter_agent, when the update event is trigger, the stream_message is set as None. This attribute is used to check if the agent is ready for next input.
        "current": current,
        "running_tasks": agent_event.running_tasks,
        "interrupt_value": interrupt_value,
        "state": state,
        "custom_info": agent_event.custom_info,
        "updated_custom_info": agent_event.updated_custom_info,
        "stream_message": stream_message
    }


def agent_worker(cmd_q: Queue, state_q: Queue, sg_init_args: dict):
    """
    cmd_q   : 主进程 → Agent（query / input / stop）
    state_q : Agent → 主进程（状态快照）
    """

    sg = StarterAgent(**sg_init_args)
    sg.init_graph()

    config = {"configurable": {"thread_id": "default"}}

    while True:
        try:
            cmd = cmd_q.get(timeout=0.1)
        except queue.Empty:
            continue

        if cmd["type"] == "START":
            
            sg.start(default_state=cmd["default_state"], config=config)
            thread_states = sg.get_state(config)

            while thread_states.interrupts:
                # 输出状态
                state_q.put(extract_state(sg, config, True))
                sub_cmd = cmd_q.get()
                if sub_cmd["type"] == "INPUT":
                    query = sub_cmd["text"]
                    print('get', query)
                elif sub_cmd["type"] == "STOP":
                    return

                for chunk in sg(
                    query,
                    config=config
                ):
                    state_q.put(extract_state(sg, config, True))
                print('finished a round of query')
                
                thread_states = sg.get_state(config)
            
            print('jump out of the while loop')

            state_q.put(extract_state(sg, config, False))

        elif cmd["type"] == "STOP":
            state_q.put(extract_state(sg, config, False))
            return