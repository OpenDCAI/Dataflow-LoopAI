def check_motivation(motivation: str) -> str:
    """
    check the user's motivation and analyze whether the user wants to:
    * train, 
    * evaluate,
    * obtain data (obtain),
    * config params of model and data (config)
    * simply have a conversation (naive)
    * finish the conversation (finish).
    
    **Args**: The `motivation` should only be 'train', 'evaluate', 'obtain', 'config', 'naive' or 'finish'.
    """
    next_to = 'query_node'
    if motivation == 'train':
        next_to = "train_node"
    elif motivation == 'obtain':
        next_to = "obtain_node"
    elif motivation == 'config':
        next_to = "config_node"
    elif motivation == 'evaluate':
        next_to = "evaluate_node"
    elif motivation == 'finish':
        next_to = "end_node"
    else:
        next_to = "query_node"
    return {
        "motivation": motivation,
        "next_to": next_to
    }
