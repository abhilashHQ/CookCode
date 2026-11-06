# Third-party credit

## Sutra

https://github.com/cneuralnetwork/sutra, Apache License 2.0.

The M12 provider work (`src/cookcode/providers/catalog.py`, decision 0054) takes three things from Sutra's `src/providers/catalog.ts` and `src/providers/registry.ts`: the idea of a data catalog of OpenAI-compatible presets, the default local ports and setup notes for Ollama, llama.cpp, LM Studio and vLLM, and the rule that endpoints on a local host need no key. No Sutra code is copied. CookCode's catalog, discovery and window handling are a separate Python design, and the context-window approach differs on purpose (0054, "Options considered").
