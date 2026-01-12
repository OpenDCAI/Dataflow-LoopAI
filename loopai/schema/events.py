from langchain_core.messages import AIMessage
from dataclasses import dataclass, fields
from typing import Any, Optional


@dataclass
class AgentEvent:
    '''
    AgentEvent is used to store the event of agent during the execution.
    '''
    stream_mode: Optional[str] = None
    node: Optional[str] = None
    node_path: Optional[list] = None
    running_tasks: Optional[list] = None
    state: Optional[Any] = None
    updated_state: Optional[Any] = None
    custom_info: Optional[dict] = None
    updated_custom_info: Optional[dict] = None
    stream_message: Optional[AIMessage] = None

    def set_stream_message(self, msg_chunk):
        """
        Set the stream message content.

        Args:
            msg_chunk (AIMessage): The message chunk to be set.
        """
        if not self.stream_message:
            self.stream_message = AIMessage(content="")
        self.stream_message.content += msg_chunk.content

    def clear_stream_message(self):
        """
        Clear the stream message content.
        """
        self.stream_message = None

    def set_path(self, node: str):
        """
        Set the node path.

        Args:
            node (str): The node to be set.
        """
        if not self.node_path:
            self.node_path = []
        self.node_path.append(node)

    def clear_path(self):
        """
        Clear the node path.
        """
        self.node_path = None

    def update_state(self, chunk):
        """
        Update the state with the given chunk.

        Args:
            chunk (Any): The chunk to update the state with. 
            ```
            e.g. {'messages': [HumanMessage(content='你好', additional_kwargs={}, response_metadata={}, id='0a834fe0-ee4b-49a2-996f-32d6fdc679c3'), AIMessage(content='你好！我是一个智能 Agent 助理，能够帮助您完成各种任务。', additional_kwargs={}, response_metadata={'finish_reason': 'stop', 'model_name': 'deepseek-chat', 'system_fingerprint': 'fp_ffc7281d48_prod0820_fp8_kvcache'}, id='run--90052c42-32d0-4282-8d98-4c52374c5363', usage_metadata={'input_tokens': 548, 'output_tokens': 82, 'total_tokens': 630, 'input_token_details': {'cache_read': 512}, 'output_token_details': {}})]}
            ```
        """
        diff = {}
        if self.state is not None:
            for key in chunk:
                if key not in self.state:
                    diff[key] = chunk[key]
                else:
                    if self.state[key] != chunk[key]:
                        diff[key] = chunk[key]
        self.state = chunk
        self.updated_state = diff

    def set_custom_info(self, key: str, info: dict):
        """
        Set the custom info.

        Args:
            key (str): The key of the custom info.
            info (dict): The custom info to be set.
        """
        if not self.custom_info:
            self.custom_info = {}
        current_key = info.get('current', 'unknown_key')
        if current_key not in self.custom_info:
            self.custom_info[current_key] = info
        else:
            skip_key = ['data']
            for k in info:
                if k not in skip_key:
                    self.custom_info[current_key][k] = info[k]
                else:
                    if not self.custom_info[current_key][k]:
                        self.custom_info[current_key][k] = info[k]
                    else:
                        for k_key in info[k]:
                            self.custom_info[current_key][k][k_key] = info[k][k_key]
        self.updated_custom_info = {key: info}
    
    def set_running_tasks(self, tasks):
        """
        Set the running tasks.

        Args:
            tasks (list): The running subgraph tasks to be set.
        """
        result = []
        for task in tasks:
            result.append(task.name)
        self.running_tasks = result

    def text(self, only_updated=False):
        """
        Convert dataclass fields to readable text representation.
        """
        lines = []
        other_state_info = []
        msgs = []
        display_state = 'updated_state' if only_updated else 'state'

        def print_normal(key, val):
            return (f"{key}: {val}", "blue")

        def print_path():
            if not self.node_path:
                return ('', 'yellow')
            return (f"🧭 Node Path: {'->'.join(self.node_path)}", "yellow")
        
        def print_list(items: list):
            if not items:
                return ('', 'yellow')
            return (f"🧭 Running Tasks: {','.join(items)}", "blue")

        def print_custom_info(obj: dict):
            msgs = []
            for key in obj:
                if 'message' in obj[key]:
                    msgs.append(f"🔧 {key}: {obj[key]['message']}")
                if 'progress' in obj[key] and obj[key]['progress'] is not None:
                    msgs.append(f"🔧 {key}: {obj[key]['progress'] * 100}")
            return ('\n'.join(msgs), "purple")

        def print_title(title: str):
            return (f"{10*'='}{title}={10*'='}", "magenta")

        def print_msg(msg: Any):
            role = 'ai'
            content = ''
            if type(msg) == dict:
                role = msg['role']
                content = msg['content']
            else:
                role = msg.type
                content = msg.content
            color = "red"
            if role == 'ai':
                color = "green"
            elif role == 'human':
                color = "cyan"
            return (f"● {role.upper()}: {content}", color)

        for f in fields(self):
            if f.name not in ['state', 'updated_state', 'running_tasks', 'stream_message', 'node_path', 'custom_info', 'updated_custom_info']:
                value = getattr(self, f.name)
                lines.append(print_normal(f.name, value))
            elif f.name == 'node_path':
                lines.append(print_path())
            elif f.name == 'running_tasks':
                lines.append(print_list(self.running_tasks))
            elif f.name == 'updated_custom_info':
                lines.append(print_custom_info(self.updated_custom_info))
            elif f.name == display_state:
                self_state = getattr(self, display_state)
                if not self_state:
                    continue
                for key in self_state:
                    if key != 'messages':
                        other_state_info.append(
                            print_normal(key, self_state[key]))
                for msg in self.state['messages']:
                    msgs.append(print_msg(msg))

        if self.stream_message:
            msgs.append(print_msg(self.stream_message))
        lines.append(print_title('🆕 Updated State'))
        lines.extend(other_state_info)
        lines.append(print_title('💭 Messages'))
        lines.extend(msgs)
        lines.append(print_title('🔧 Updated Custom Info'))
        lines.append(print_custom_info(self.updated_custom_info))
        return lines

    def __str__(self):
        return self.text()

    def json(self):
        """
        Convert dataclass fields to JSON representation.
        """
        results = {}
        for f in fields(self):
            if f.name not in ['state', 'stream_message', 'custom_info']:
                results[f.name] = getattr(self, f.name)
            elif f.name == 'state':
                results['state'] = {}
                for key in self.state:
                    if key != 'messages':
                        results['state'][key] = self.state[key]
                    else:
                        results['state'][key] = []
                        for msg in self.state[key]:
                            if type(msg) == dict:
                                results['state'][key].append(
                                    f"Role: {msg['role']}, Content: {msg['content']}")
                            else:
                                results['state'][key].append(
                                    f"Role: {msg.type}, Content: {msg.content}")
        if self.stream_message:
            results['stream_message'] = self.stream_message.content

        return results

    def get_custom_info(self, key: str = None):
        """
        Get the custom info.

        Args:
            key (str): The key of the custom info.

        Returns:
            list: The custom info list.
        """
        if not self.custom_info:
            return []
        if key not in self.custom_info:
            if key is None:
                return self.custom_info
            else:
                return []
        return self.custom_info[key]


@dataclass
class StreamEvent:
    """
    Stream event.
    """
    current: str
    progress: Optional[float] = None
    progress_num: Optional[int] = None
    total: Optional[int] = None
    message: Optional[str] = None
    data: Optional[Any] = None

    def __init__(self, current: str, progress: Optional[float] = None, progress_num: Optional[int] = None, total: Optional[int] = None, message: Optional[str] = None, data: Optional[Any] = None):
        self.current = current
        self.progress = progress
        self.progress_num = progress_num
        self.total = total
        self.message = message
        self.data = data

    def json(self):
        """
        Convert dataclass fields to JSON representation.
        """
        results = {}
        for f in fields(self):
            results[f.name] = getattr(self, f.name)
        return results
