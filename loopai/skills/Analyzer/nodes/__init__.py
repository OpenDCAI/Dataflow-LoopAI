"""Lazy Analyzer node exports."""

__all__ = [
    "eval_model_node",
    "analyze_result_node",
    "draw_conclusion_node",
    "metric_recommend_node",
    "metric_score_node",
    "analyze_metric_report_node",
]


def __getattr__(name):
    if name == "eval_model_node":
        from .eval_model import eval_model_node
        return eval_model_node
    if name == "analyze_result_node":
        from .analyze_result import analyze_result_node
        return analyze_result_node
    if name == "draw_conclusion_node":
        from .draw_conclusion import draw_conclusion_node
        return draw_conclusion_node
    if name == "metric_recommend_node":
        from .metric_recommend_node import metric_recommend_node
        return metric_recommend_node
    if name == "metric_score_node":
        from .metric_score_node import metric_score_node
        return metric_score_node
    if name == "analyze_metric_report_node":
        from .analyze_metric_report_node import analyze_metric_report_node
        return analyze_metric_report_node
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
