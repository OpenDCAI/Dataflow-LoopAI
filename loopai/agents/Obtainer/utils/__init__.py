__all__ = [
    'RAGManager', 
    'WebTools', 
    'QueryGenerator', 
    'SummaryAgent', 
    'HuggingFaceManager',
    'HuggingFaceDecisionAgent',
    'WebPageReader',
    'URLSelector',
    'CategoryClassifier',
    'ObtainQueryNormalizer',
    'TaskDecomposer',
    'PlaywrightBrowserManager',
    'PlaywrightActionTools',
    'WebPageActionAgent',
    'WebPageDataSaver',
    'resolve_obtainer_codex_provider',
]


def __getattr__(name):
    if name == "RAGManager":
        from .rag_manager import RAGManager
        return RAGManager
    if name == "WebTools":
        from .web_tools import WebTools
        return WebTools
    if name == "QueryGenerator":
        from .query_generator import QueryGenerator
        return QueryGenerator
    if name == "SummaryAgent":
        from .summary_agent import SummaryAgent
        return SummaryAgent
    if name == "HuggingFaceManager":
        from .hf_manager import HuggingFaceManager
        return HuggingFaceManager
    if name == "HuggingFaceDecisionAgent":
        from .hf_decision_agent import HuggingFaceDecisionAgent
        return HuggingFaceDecisionAgent
    if name == "WebPageReader":
        from .webpage_reader import WebPageReader
        return WebPageReader
    if name == "URLSelector":
        from .url_selector import URLSelector
        return URLSelector
    if name in {"CategoryClassifier", "ObtainQueryNormalizer", "TaskDecomposer"}:
        from .category_classifier import CategoryClassifier, ObtainQueryNormalizer, TaskDecomposer
        return {
            "CategoryClassifier": CategoryClassifier,
            "ObtainQueryNormalizer": ObtainQueryNormalizer,
            "TaskDecomposer": TaskDecomposer,
        }[name]
    if name == "PlaywrightBrowserManager":
        from .playwright_manager import PlaywrightBrowserManager
        return PlaywrightBrowserManager
    if name == "PlaywrightActionTools":
        from .playwright_tools import PlaywrightActionTools
        return PlaywrightActionTools
    if name == "WebPageActionAgent":
        from .webpage_action_agent import WebPageActionAgent
        return WebPageActionAgent
    if name == "WebPageDataSaver":
        from .webpage_data_saver import WebPageDataSaver
        return WebPageDataSaver
    if name == "resolve_obtainer_codex_provider":
        from .model_pool import resolve_obtainer_codex_provider
        return resolve_obtainer_codex_provider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
