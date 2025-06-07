# Agent Orchestrator

This is an "Agent Orchestrator" written by Claude Opus 4. The [initial prompt
was](https://claude.ai/chat/2fd25e3d-e3dd-4330-a0d4-6b6017a4cec8):

```
I want to write a set of python servers that acts as an agent orchestrator for LLMs. We will use multiple LLMs, possibly running on multiple hosts. They are all instances of either Ollama or Llama.cpp or otherwise OpenAI-API compatible and I want to be able to configure which host and which model is used for each agent. The agents may be run in parallel on multiple machines, independently. The agents are as follows:
1. Preparer: receives the user's initial query and creates a list of web searches it wants to perform.
2. Navigator: receives the user's initial query and the results of the web searches. Given the context in the web search results, it creates a list of URLs or new searches it wants to perform.
3. Summarizer: receives the user's initial query and the contents of the web pages indicated by the Navigator. Its job is to reduce, refine, and summarize the web search results as concisely as possible while retaining accuracy. References to the original sources must be retained in its output.
4. Answerer: receives the user's initial query and the output of the Summarizer. Its job is to answer the user's query with references.

All agents should operate asynchronously and independently. They should use asyncio in python. They may use each other as a "tool" such that if the Summarizer or Answerer feels the retrieved data is insufficient to answer the query, further research may be performed.

First I want you to give me suggestions about the above architecture. Are the 4 agents I've outlined sufficient? What architectural choices would you suggest? We will consider these suggestions before proceeding to write the agents.
```

I then asked to make suggestions, accepted some of those suggestions, and asked
it to write the code. Then I asked it for problems in the code, for which it
found 20 issues, which I asked it to fix one by one. After may times asking it
to continue when it hit limits, it eventually hit its length limit for a single
chat and stopped.

The code in this repository is the output. It's clearly got some half-finished
edits (e.g. in main) and the filenames are wrong (-py.py). The initial commit in
this repository is the exact output of Claude (28 files!!!).
