from typing import TypedDict, Any, List
from langgraph.graph import MessagesState


class LoopAIState(MessagesState):
    task_id: str
    mined_data: str  # to defined the path of mined data
    output_dir: str  # to defined the path of output directory
    # judger state attributes
    eval_model_path: str  # to defined the path of model to be evaluated and post-trained
    eval_base_url: str  # to defined the base url of model to be evaluated and post-trained
    eval_api_key: str  # to defined the api key of model to be evaluated and post-trained
    # to defined the temperature of model to be evaluated and post-trained
    eval_temperature: float = 0
    # to defined the top_p of model to be evaluated and post-trained
    eval_top_p: float = 0.95
    eval_test_case_path: str  # to defined the path of test case to be evaluated
    eval_problem_path: str  # to defined the path of problem to be evaluated
    eval_result_path: str  # to defined the path of result of model to be evaluated
    eval_batch_size: int = 20  # to defined the batch size of model to be evaluated
    # analyzer state attributes
    analyze_task_type: str = 'code'
    analyze_batch_size: int = 20  # to defined the batch size of model to be analyzed
    analyze_model_path: str  # to defined the path of model to be analyzed and post-trained
    analyze_base_url: str  # to defined the base url of model to be analyzed and post-trained
    analyze_api_key: str  # to defined the api key of model to be analyzed and post-trained
    analyze_temperature: float = 0 # to defined the temperature of model to be analyzed and post-trained
    analyze_top_p: float = 0.95 # to defined the top_p of model to be analyzed and post-trained
    output_brief: bool  # whether to output brief analysis
    analyze_output_result_path: str  # the path of result to be outputted
    analyze_output_summary_path: str  # the path of summary to be outputted
    analyze_sampling_top_k: int = 5  # the number of failure examples to be sampled
    analyze_output_report_json_path: str  # the path of report to be outputted
    analyze_output_report_text_path: str  # the path of report to be outputted
    output_suggestion: bool # whether to output suggestion
    analyze_output_suggestion_path: str  # the path of suggestion to be outputted
    extra: List[str]

    summary_path: str
    oj_path: str

    judge_json: str
    judge_txt: str
    final_report_json: str
    final_report_txt: str
    
    # trainer state attributes
    train_dataset_path: str  # to defined the path of training dataset (json/jsonl format)
    train_task_description: str  # to defined the task description for training
    train_config_template_path: str  # to defined the path of llamafactory config template
    train_config_output_path: str  # to defined the path of generated training config
    train_output_dir: str  # to defined the output directory for training
    train_model_name: str  # to defined the base model name for training
    train_use_swanlab: bool = True  # whether to use swanlab for monitoring
    train_swanlab_project: str  # to defined the swanlab project name
    
    # trainer workflow state fields
    data_check_passed: bool = False  # whether data check passed
    data_check_result: dict = {}  # data check result details
    data_check_report_path: str = ""  # path to data check report
    data_check_error: str = ""  # data check error message
    config_generation_success: bool = False  # whether config generation succeeded
    config_explanation_path: str = ""  # path to config explanation
    config_generation_error: str = ""  # config generation error message
    training_success: bool = False  # whether training succeeded
    training_execution_time: float = 0.0  # training execution time
    training_task_id: str = ""  # remote training task id
    training_final_status: dict = {}  # final training status
    training_log_path: str = ""  # path to training log
    training_report_path: str = ""  # path to training report
    training_error: str = ""  # training error message
    training_service_url: str = "http://localhost:8000"  # training service URL
    
    update_model_path: str  # to defined the save path of the post-trained model

    current: str  # to defined the current task, e.g. train, evaluate, obtain, naive
    next_to: str  # to defined the next task, e.g. train, evaluate, obtain, naive
