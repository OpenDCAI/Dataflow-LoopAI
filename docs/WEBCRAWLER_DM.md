# Web crawler removal notice

The lite Obtainer workflow does not crawl web pages. Dataset acquisition is
limited to Hugging Face Hub datasets and is handled by
`dataset-acquisition-agent`: search, per-dataset JSONL normalization, separate
lake ingestion, and one final index build.

The former crawler implementation and its DataMixer WebAgent campaign have
been removed. The lite Obtainer workflow does not crawl web pages; dataset
acquisition is limited to Hugging Face Hub datasets through
`dataset-acquisition-agent`.
