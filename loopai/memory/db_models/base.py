from tortoise import fields, models
from tortoise.models import Model


class CheckpointModel(Model):
    thread_id = fields.CharField(max_length=255)
    checkpoint_ns = fields.CharField(max_length=255, default="")
    checkpoint_id = fields.CharField(max_length=255)
    parent_checkpoint_id = fields.CharField(max_length=255, null=True)
    type = fields.CharField(max_length=255, null=True)
    checkpoint = fields.BinaryField(null=True)
    metadata = fields.BinaryField(null=True)

    class Meta:
        table = "checkpoints"
        unique_together = (("thread_id", "checkpoint_ns", "checkpoint_id"),)


class WriteModel(Model):
    thread_id = fields.CharField(max_length=255)
    checkpoint_ns = fields.CharField(max_length=255, default="")
    checkpoint_id = fields.CharField(max_length=255)
    task_id = fields.CharField(max_length=255)
    idx = fields.IntField()
    channel = fields.CharField(max_length=255)
    type = fields.CharField(max_length=255, null=True)
    value = fields.BinaryField(null=True)

    class Meta:
        table = "writes"
        unique_together = (
            ("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"),
        )
