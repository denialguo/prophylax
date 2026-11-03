# Third-party notices

The [MIT license](LICENSE) covers code written for Prophylax. It grants no rights to anything below,
which stays under its own terms. This is a summary, not legal advice; check each project's own terms.

## Not distributed by this repository

These are installed or called separately.

| Component | How it is used | License |
|---|---|---|
| [Stockfish](https://stockfishchess.org/) | External engine, run as a separate process | GPL-3.0 |
| [python-chess](https://github.com/niklasf/python-chess) (`chess`) | Python dependency, imported at run time | GPL-3.0-or-later |
| [google-adk](https://github.com/google/adk-python) | Python dependency | Apache-2.0 |
| [LiteLLM](https://github.com/BerriAI/litellm), [Pydantic](https://github.com/pydantic/pydantic), [jsonschema](https://github.com/python-jsonschema/jsonschema), [pytest](https://github.com/pytest-dev/pytest) | Python dependencies | MIT |

Other packages are installed through these; `uv.lock` lists them.

**python-chess is GPL.** Prophylax's own source is offered under MIT, and this repository does not
include python-chess. Anyone distributing Prophylax combined with python-chess, for example as a
bundled application, should expect the GPL to apply to that combined distribution.

## QUEEN

[QUEEN](https://huggingface.co/princeton-nlp/queen_hce-4) (`princeton-nlp/queen_hce-4`) is an external
model that Prophylax evaluates. **This repository does not include its weights or code, and nothing here
grants rights to the model.** Its model card states that upstream component terms apply and that no
blanket license is asserted for the combined release. Check those terms before using it.

## Stored model outputs

These files hold text QUEEN generated, kept unaltered as evaluation evidence. They are not Prophylax-authored
code, and the MIT license is not offered for them:

- `evals/queen_benchmark/outputs/`
- `evals/queen_heldout/raw/`

## LLM providers

Narration can call Gemini or Groq, under your own account and those providers' terms.
