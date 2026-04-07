set -x

nproc_per_node=${N_GPUS_PER_NODE:-1}

torchrun --standalone --nnodes=${NNODES:-1} --nproc_per_node=$nproc_per_node \
    -m verl.trainer.sft_trainer \
    data.train_files="${TRAIN_FILES}" \
    data.val_files="${VAL_FILES}" \
    data.train_batch_size=${TRAIN_BATCH_SIZE:-128} \
    data.pad_mode=no_padding \
    data.truncation=error \
    data.use_dynamic_bsz=True \
    data.max_token_len_per_gpu=${MAX_TOKEN_LEN_PER_GPU:-8192} \
    data.messages_key=messages \
    data.max_length=${MAX_LENGTH:-1024} \
    model=hf_model \
    model.path=${MODEL_PATH} \
    model.use_remove_padding=True \
    engine=automodel \
    engine.distributed_strategy=fsdp2 \
    optim=automodel \
    optim.lr=${LEARNING_RATE:-1e-5} \
    optim.lr_warmup_steps_ratio=0.2 \
    optim.weight_decay=0.1 \
    optim.lr_scheduler_type=cosine \
    trainer.default_local_dir=${OUTPUT_DIR:-checkpoints/verl_sft} \
    trainer.project_name='${PROJECT_NAME:-verl_sft}' \
    trainer.experiment_name='${EXPERIMENT_NAME:-sft_train}' \
    trainer.total_epochs=${TOTAL_EPOCHS:-4} \
    trainer.test_freq=${TEST_FREQ:--1} \
    trainer.save_freq=${SAVE_FREQ:--1} \
    trainer.logger=console \
    trainer.seed=1 \
    trainer.resume_mode=disable $@
