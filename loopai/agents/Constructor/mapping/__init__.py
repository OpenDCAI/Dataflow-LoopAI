"""
Mapping Subgraph for converting intermediate format data to target format.

This subgraph handles:
1. User inquiry for format selection
2. Preset format selection (non-LLM)
3. Custom format generation (LLM)
4. Format confirmation
5. Data mapping (script-based for presets, LLM-based for custom)
6. Result summary
"""

from .mapping_subgraph import MappingSubgraph, create_mapping_subgraph

__all__ = ['MappingSubgraph', 'create_mapping_subgraph']

