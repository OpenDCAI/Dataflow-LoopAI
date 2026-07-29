"""DataMixer web-agent plugin layer."""
from .base import (
    WebAgent,
    WebAgentSpec,
    available,
    create,
    is_registered,
    param_info,
    register,
)
from .webcrawler_dm import (
    DomainDataAcquisitionAgent,
    WebCrawlerDMConfig,
    WebCrawlerDMAgent,
)
from .campaign import (
    CampaignConfig,
    CampaignQueue,
    ExpandedQuery,
    LLMQueryExpander,
    WebAgentCampaignRunner,
)

__all__ = [
    "WebAgent",
    "WebAgentSpec",
    "WebCrawlerDMConfig",
    "WebCrawlerDMAgent",
    "DomainDataAcquisitionAgent",
    "CampaignConfig",
    "CampaignQueue",
    "ExpandedQuery",
    "LLMQueryExpander",
    "WebAgentCampaignRunner",
    "available",
    "create",
    "is_registered",
    "param_info",
    "register",
]
