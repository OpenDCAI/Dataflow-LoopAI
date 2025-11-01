def check_motivation(motivation: str) -> str:
    """
    check the user's motivation and analyze whether the user wants to train, evaluate, obtain data (obtain) or simply have a conversation (naive).
    
    **Args**: The `motivation` should only be 'train', 'evaluate', 'obtain' or 'naive'.
    """
    return f"The user wants to {motivation}."
