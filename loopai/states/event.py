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
    state: Optional[Any] = None
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

    def update(self, chunk):
        """
        Update the state with the given chunk.
        
        Args:
            chunk (Any): The chunk to update the state with. 
            ```
            e.g. {'messages': [HumanMessage(content='你好', additional_kwargs={}, response_metadata={}, id='0a834fe0-ee4b-49a2-996f-32d6fdc679c3'), AIMessage(content='你好！我是一个智能 Agent 助理，能够帮助您完成各种任务。', additional_kwargs={}, response_metadata={'finish_reason': 'stop', 'model_name': 'deepseek-chat', 'system_fingerprint': 'fp_ffc7281d48_prod0820_fp8_kvcache'}, id='run--90052c42-32d0-4282-8d98-4c52374c5363', usage_metadata={'input_tokens': 548, 'output_tokens': 82, 'total_tokens': 630, 'input_token_details': {'cache_read': 512}, 'output_token_details': {}})]}
            ```
        """
        self.state = chunk

    def text(self):
        """
        Convert dataclass fields to readable text representation.
        """
        lines = []
        other_state_info = []
        msgs = []
        for f in fields(self):
            if f.name not in ['state', 'stream_message', 'node_path']:
                value = getattr(self, f.name)
                lines.append(f"{f.name}: {value}")
            elif f.name == 'node_path':
                lines.append(f"{f.name}: {'->'.join(self.node_path)}")
            elif f.name == 'state':
                for key in self.state:
                    if key != 'messages':
                        other_state_info.append(f"{key}: {self.state[key]}")
                    else:
                        for msg in self.state[key]:
                            if type(msg) == dict:
                                msgs.append(
                                    f"{msg['role'].upper()}: {msg['content']}")
                            else:
                                msgs.append(
                                    f"{msg.type.upper()}: {msg.content}")
        if self.stream_message:
            msgs.append(f"AI: {self.stream_message.content}")
        lines.append('='*10 + 'State' + '='*10)
        lines.extend(other_state_info)
        lines.append('='*10 + 'Messages' + '='*10)
        lines.extend(msgs)
        return "\n".join(lines)
    
    def __str__(self):
        return self.text()
    
    def json(self):
        """
        Convert dataclass fields to JSON representation.
        """
        results = {}
        for f in fields(self):
            if f.name not in ['state', 'stream_message']:
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
        
