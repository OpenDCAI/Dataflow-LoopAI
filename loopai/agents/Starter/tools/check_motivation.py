from langchain_core.tools import tool

@tool
def check_motivation(motivation: str) -> dict:
    """
    Determine the next workflow node based on the user's motivation.

    Motivation must be one of:
    ['chat', 'train', 'judge', 'analyze', 'obtain', 'webcrawler', 'config', 'finish']

    Returns:
        {
            "motivation": "<motivation>",
            "next_to": "<target_node>"
        }
    """
    
    mapping = {
        "train": "train_node",
        "judge": "judge_node",
        "analyze": "analyze_node",
        "obtain": "obtain_node",
        "webcrawler": "webcrawler_node",
        "config": "config_node",
        "finish": "end_node",
        "chat": "query_node",
    }

    next_to = mapping.get(motivation, "query_node")

    return {
        "motivation": motivation,
        "next_to": next_to
    }
