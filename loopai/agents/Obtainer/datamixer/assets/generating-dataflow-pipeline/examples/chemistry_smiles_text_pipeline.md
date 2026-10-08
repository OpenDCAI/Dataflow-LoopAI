# Example: Chemistry SMILES Text Pipeline

## Use Case

Extract molecular SMILES strings from chemistry text and verify them against a
reference representation. Inputs require textual `text` and `abbreviations`.

## Operator Decision

```json
{
  "ops": ["ExtractSmilesFromTextGenerator", "SmilesEquivalenceDatasetEvaluator"],
  "field_flow": "text+abbreviations -> synth_smiles -> molecular-equivalence result",
  "reason": "Use chemistry-native parsing and equivalence evaluation rather than generic text matching."
}
```

## Pipeline Core

```python
extractor.run(
    storage=storage.step(),
    input_content_key="text",
    input_abbreviation_key="abbreviations",
    output_key="synth_smiles",
)
equivalence_evaluator.run(storage=storage.step())
```

Use `ExtractSmilesFromTextGenerator` with `ExtractSmilesFromTextPrompt`, followed
by `SmilesEquivalenceDatasetEvaluator`.

## Key Notes

- SMILES string equality is not molecular equivalence; always use the native evaluator.
- Preserve abbreviation expansion and source text for auditability.
- This is a text/chemical-string pipeline and does not process molecular images.
