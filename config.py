import json
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Any, Optional

@dataclass
class LLMConfig:
    host: str
    model: str
    api_key: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 2048

class Config:
    def __init__(self, config_path: str = "config.json"):
        self.config_path = Path(config_path)
        self.data = self._load_config()
        
    def _load_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            return self._default_config()
        
        with open(self.config_path, 'r') as f:
            return json.load(f)
    
    def _default_config(self) -> Dict[str, Any]:
        return {
            "llm_endpoints": {
                "router": {"host": "http://localhost:11434", "model": "llama3"},
                "preparer": {"host": "http://localhost:11434", "model": "llama3"},
                "navigator": {"host": "http://localhost:11434", "model": "llama3"},
                "validator": {"host": "http://localhost:11434", "model": "llama3"},
                "cache": {"host": "http://localhost:11434", "model": "llama3"},
                "summarizer": {"host": "http://localhost:11434", "model": "llama3"},
                "answerer": {"host": "http://localhost:11434", "model": "llama3"}
            },
            "embeddings": {
                "provider": "sentence-transformers",  # Options: sentence-transformers, openai, ollama, cohere, hash
                "model": "all-MiniLM-L6-v2",  # Model name for the provider
                "api_key": "",  # Required for some providers
                "endpoint": "",  # Optional custom endpoint
                "dimension": 384  # Embedding dimension (auto-detected for most providers)
            },
            "chromadb": {
                "path": "./chroma_data",
                "collection": "web_research"
            },
            "sqlite": {
                "path": "./state.db"
            },
            "logging": {
                "level": "INFO",
                "dir": "./logs"
            },
            "web_search": {
                "provider": "searxng",  # Options: searxng, serper, brave, serpapi, duckduckgo
                "api_key": "",  # Required for some providers
                "endpoint": ""  # Optional custom endpoint
            },
            "server": {
                "host": "localhost",
                "port": 8000
            },
            "error_recovery": {
                "enabled": True,
                "default_retry": {
                    "max_attempts": 3,
                    "initial_delay": 1.0,
                    "max_delay": 30.0,
                    "exponential_base": 2.0,
                    "jitter": True
                },
                "agent_retry": {
                    "router": {"max_attempts": 2, "initial_delay": 0.5},
                    "preparer": {"max_attempts": 3, "initial_delay": 1.0},
                    "navigator": {"max_attempts": 2, "initial_delay": 1.0},
                    "validator": {"max_attempts": 2, "initial_delay": 0.5},
                    "cache": {"max_attempts": 2, "initial_delay": 0.5},
                    "summarizer": {"max_attempts": 3, "initial_delay": 2.0},
                    "answerer": {"max_attempts": 3, "initial_delay": 2.0}
                }
            },
            "circuit_breaker": {
                "threshold": 5,
                "timeout": 60
            },
            "connection_pool": {
                "limit": 100,  # Total connection limit
                "limit_per_host": 30,  # Per-host connection limit
                "connect_timeout": 10.0,
                "sock_read_timeout": 30.0,
                "total_timeout": 300.0,
                "keepalive_timeout": 30.0,
                "force_close": False,
                "verify_ssl": True,
                "retry_attempts": 3,
                "retry_delay": 0.5
            }
        }
    
    def get_llm_config(self, agent: str) -> LLMConfig:
        cfg = self.data["llm_endpoints"].get(agent, {})
        return LLMConfig(**cfg)
    
    def save(self):
        with open(self.config_path, 'w') as f:
            json.dump(self.data, f, indent=2)