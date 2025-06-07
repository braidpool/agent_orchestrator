# Embedding Provider Configuration Guide

The system supports multiple embedding providers for ChromaDB vector search. Choose based on your needs for quality, speed, and cost.

## Available Providers

### 1. **Sentence-Transformers (Recommended)**
Local embedding models that run on your machine. No API key required.

```json
{
  "embeddings": {
    "provider": "sentence-transformers",
    "model": "all-MiniLM-L6-v2"
  }
}
```

**Models:**
- `all-MiniLM-L6-v2` - Fast, good quality (384 dims)
- `all-mpnet-base-v2` - Higher quality (768 dims)
- `all-MiniLM-L12-v2` - Balanced (384 dims)

**Installation:**
```bash
pip install sentence-transformers
```

### 2. **OpenAI**
High-quality embeddings via OpenAI API.

```json
{
  "embeddings": {
    "provider": "openai",
    "api_key": "sk-...",
    "model": "text-embedding-ada-002"
  }
}
```

**Models:**
- `text-embedding-ada-002` - Best quality (1536 dims)
- `text-embedding-3-small` - Cheaper, good quality
- `text-embedding-3-large` - Highest quality

### 3. **Ollama**
Use Ollama's embedding models locally.

```json
{
  "embeddings": {
    "provider": "ollama",
    "endpoint": "http://localhost:11434/api/embeddings",
    "model": "nomic-embed-text"
  }
}
```

**Models:**
- `nomic-embed-text` - Good local embeddings
- `mxbai-embed-large` - Larger, higher quality

**Installation:**
```bash
ollama pull nomic-embed-text
```

### 4. **Cohere**
Cloud-based embeddings with good multilingual support.

```json
{
  "embeddings": {
    "provider": "cohere",
    "api_key": "your-api-key",
    "model": "embed-english-v3.0"
  }
}
```

**Models:**
- `embed-english-v3.0` - English (1024 dims)
- `embed-multilingual-v3.0` - Multilingual support

### 5. **Hash (Fallback)**
Deterministic hash-based embeddings. No dependencies, but lower quality.

```json
{
  "embeddings": {
    "provider": "hash",
    "dimension": 384
  }
}
```

## Performance Comparison

| Provider | Quality | Speed | Cost | Local | Dims |
|----------|---------|--------|------|--------|------|
| Sentence-Transformers | Good | Fast | Free | Yes | 384-768 |
| OpenAI | Excellent | Medium | Paid | No | 1536 |
| Ollama | Good | Fast | Free | Yes | 384-768 |
| Cohere | Very Good | Medium | Paid | No | 1024 |
| Hash | Poor | Very Fast | Free | Yes | Configurable |

## Choosing a Provider

- **For development/testing**: Use sentence-transformers with `all-MiniLM-L6-v2`
- **For production with budget**: Use Ollama with `nomic-embed-text`
- **For best quality**: Use OpenAI with `text-embedding-ada-002`
- **For multilingual**: Use Cohere with `embed-multilingual-v3.0`
- **For offline/airgapped**: Use sentence-transformers or hash

## Switching Providers

You can switch providers by updating `config.json`. The system will handle the transition, but note:

1. Different providers use different embedding dimensions
2. You may need to rebuild your ChromaDB collection when switching
3. Similarity scores may vary between providers

## Troubleshooting

If embeddings fail, the system automatically falls back to hash-based embeddings. Check logs for:

```
tail -f logs/cache.log | grep -i embed
```

Common issues:
- Missing API keys
- Model not installed (Ollama/sentence-transformers)
- Network connectivity (cloud providers)
- Insufficient memory (local models)