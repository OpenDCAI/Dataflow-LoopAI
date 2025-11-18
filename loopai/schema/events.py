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
    custom_info: Optional[dict] = None
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

    def set_custom_info(self, key: str, info: dict):
        """
        Set the custom info.

        Args:
            key (str): The key of the custom info.
            info (dict): The custom info to be set.
        """
        if not self.custom_info:
            self.custom_info = {}
        if key not in self.custom_info:
            self.custom_info[key] = []
        self.custom_info[key].append(info)

    def text(self):
        """
        Convert dataclass fields to readable text representation.
        """
        lines = []
        other_state_info = []
        msgs = []
        for f in fields(self):
            if f.name not in ['state', 'stream_message', 'node_path', 'custom_info']:
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
    
    def get_obtainer_events(self) -> list:
        """
        Get all obtainer-related custom events from custom_info.
        
        Returns:
            list: List of obtainer event dictionaries (from StreamEvent or ObtainerEvent).
        """
        if not self.custom_info:
            return []
        
        obtainer_events = []
        for key, events in self.custom_info.items():
            # Check if key is related to obtainer
            if isinstance(key, str) and ('obtain' in key.lower() or 'obtainer' in key.lower()):
                obtainer_events.extend(events)
            # Also check events themselves
            for event in events:
                if isinstance(event, dict):
                    current = str(event.get('current', '')).lower()
                    if any(keyword in current for keyword in ['obtain', 'websearch', 'download', 'postprocess']):
                        if event not in obtainer_events:
                            obtainer_events.append(event)
        
        return obtainer_events


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


@dataclass
class ObtainerEvent:
    """
    Obtainer-specific event for monitoring data acquisition workflow.
    This event is sent via langgraph's stream_writer and captured in AgentEvent.custom_info.
    """
    # Node/Stage information
    node: str  # e.g., 'start_node', 'websearch_node', 'download_node', 'postprocess_node', 'end_node'
    stage: Optional[str] = None  # e.g., 'websearch_workflow', 'download_workflow'
    
    # Progress tracking
    progress: Optional[float] = None  # 0.0 to 1.0
    progress_num: Optional[int] = None
    total: Optional[int] = None
    
    # Status and messages
    status: Optional[str] = None  # e.g., 'started', 'in_progress', 'completed', 'failed'
    message: Optional[str] = None
    
    # WebSearch specific data
    user_query: Optional[str] = None
    research_queries: Optional[list] = None
    research_summary: Optional[str] = None
    urls_found: Optional[int] = None
    urls_visited: Optional[int] = None
    subtasks_generated: Optional[int] = None
    
    # Download specific data
    download_task_index: Optional[int] = None
    download_task_total: Optional[int] = None
    download_method: Optional[str] = None  # 'huggingface', 'kaggle', 'web'
    download_path: Optional[str] = None
    download_success: Optional[bool] = None
    completed_downloads: Optional[int] = None
    failed_downloads: Optional[int] = None
    
    # PostProcess specific data
    category: Optional[str] = None  # 'PT' or 'SFT'
    total_records_processed: Optional[int] = None
    processed_sources_count: Optional[int] = None
    output_dir: Optional[str] = None
    
    # Error information
    has_exception: Optional[bool] = None
    exception: Optional[str] = None
    
    # Additional custom data
    custom_data: Optional[dict] = None
    
    def json(self):
        """
        Convert dataclass fields to JSON representation.
        """
        results = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if value is not None:
                results[f.name] = value
        return results
    
    def to_stream_event(self) -> StreamEvent:
        """
        Convert ObtainerEvent to StreamEvent for compatibility with existing stream_writer.
        """
        # Build message from available fields
        message_parts = []
        if self.message:
            message_parts.append(self.message)
        if self.status:
            message_parts.append(f"Status: {self.status}")
        
        # Build data dict from all non-None fields
        data = {}
        for f in fields(self):
            if f.name not in ['node', 'stage', 'progress', 'progress_num', 'total', 'status', 'message']:
                value = getattr(self, f.name)
                if value is not None:
                    data[f.name] = value
        
        # Add custom_data if present
        if self.custom_data:
            data.update(self.custom_data)
        
        return StreamEvent(
            current=self.node or self.stage or 'obtainer',
            progress=self.progress,
            progress_num=self.progress_num,
            total=self.total,
            message=' | '.join(message_parts) if message_parts else None,
            data=data if data else None
        )