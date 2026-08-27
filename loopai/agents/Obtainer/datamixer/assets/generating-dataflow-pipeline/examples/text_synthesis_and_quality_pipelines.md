# Example: Text Conversation, SFT, and PT Pipelines

## Conversation Synthesis

```text
intent/source text -> ConsistentChatGenerator -> multiple consistent dialogs
```

Use `ConsistentChatGenerator` with `ConsistentChatPrompt`; preserve the source
intent and reject dialogs that drift between turns.

## SFT Synthesis

```text
seed text -> CondorGenerator -> instruction/input/output
  -> CondorRefiner
  -> AlpagasusFilter
```

For local-model bulk synthesis, use `SFTGeneratorSeed`, then output-length,
`SuperfilteringFilter`, and `DeitaQualityFilter` gates.

## Pretraining Text Synthesis

```text
raw_content -> language/cleanup/dedup/blocklist/structural filters
  -> PairQualFilter
  -> Phi4QAGenerator -> generated_content
  -> QuratingFilter
```

## CPU Text Cleaning

Apply refiners before filters: remove emoji, HTML/URLs, and extra spaces; then
MinHash deduplication, blocklist, length/sentence, punctuation, symbol ratio,
watermark, boilerplate, uniqueness, and JavaScript-content filters. For SFT-only
length screening, `WordNumberFilter` may operate directly on `output`.

## Key Notes

- Tune language and length thresholds to the target corpus rather than copying defaults.
- Preserve pre-refinement text hashes for provenance and dedup audits.
- Generated SFT must pass semantic quality filters, not only structural checks.
