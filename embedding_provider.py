import aiohttp
import asyncio
import numpy as np
import hashlib
import logging
from typing import List, Dict, Any, Optional
from sentence_transformers import SentenceTransformer
import torch

from session_pool import SessionPool, PooledClient

logger = logging.getLogger("EmbeddingProvider")

class EmbeddingProvider(PooledClient):
    """Provides embeddings using various methods with connection pooling"""
    
    def __init__(self, config: Dict[str, Any], session_pool: Optional[SessionPool] = None):
        super().__init__(session_pool)
        self.config = config.get("embeddings", {})
        self.provider = self.config.get("provider", "sentence-transformers")
        self.model_name = self.config.get("model", "all-MiniLM-L6-v2")
        self.dimension = self.config.get("dimension", 384)
        self.api_key = self.config.get("api_key", "")
        self.endpoint = self.config.get("endpoint", "")
        
        self.providers = {
            "sentence-transformers": self._init_sentence_transformers,
            "openai": self._init_openai,
            "ollama": self._init_ollama,
            "cohere": self._init_cohere,
            "hash": self._init_hash  # Fallback
        }
        
        # Initialize the selected provider
        self._init_provider()
    
    def _init_provider(self):
        """Initialize the selected embedding provider"""
        if self.provider in self.providers:
            self.providers[self.provider]()
        else:
            logger.warning(f"Unknown embedding provider: {self.provider}, using hash fallback")
            self.provider = "hash"
            self._init_hash()
    
    def _init_sentence_transformers(self):
        """Initialize sentence-transformers (runs locally)"""
        try:
            self.model = SentenceTransformer(self.model_name)
            self.dimension = self.model.get_sentence_embedding_dimension()
            logger.info(f"Initialized sentence-transformers with model: {self.model_name}")
        except Exception as e:
            logger.error(f"Failed to initialize sentence-transformers: {e}")
            logger.info("Falling back to hash embeddings")
            self.provider = "hash"
            self._init_hash()
    
    def _init_openai(self):
        """Initialize OpenAI embeddings"""
        if not self.api_key:
            logger.error("OpenAI API key not provided")
            self.provider = "hash"
            self._init_hash()
        else:
            self.endpoint = self.endpoint or "https://api.openai.com/v1/embeddings"
            self.model_name = self.model_name or "text-embedding-ada-002"
            self.dimension = 1536  # OpenAI ada-002 dimension
    
    def _init_ollama(self):
        """Initialize Ollama embeddings"""
        self.endpoint = self.endpoint or "http://localhost:11434/api/embeddings"
        # Common Ollama embedding models
        if self.model_name in ["llama3", "mistral"]:
            self.model_name = "nomic-embed-text"  # Better embedding model for Ollama
    
    def _init_cohere(self):
        """Initialize Cohere embeddings"""
        if not self.api_key:
            logger.error("Cohere API key not provided")
            self.provider = "hash"
            self._init_hash()
        else:
            self.endpoint = self.endpoint or "https://api.cohere.ai/v1/embed"
            self.model_name = self.model_name or "embed-english-v3.0"
            self.dimension = 1024  # Cohere v3 dimension
    
    def _init_hash(self):
        """Initialize hash-based fallback"""
        logger.info("Using hash-based embeddings (fallback)")
    
    async def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Get embeddings for a list of texts"""
        if self.provider == "sentence-transformers":
            return await self._get_sentence_transformer_embeddings(texts)
        elif self.provider == "openai":
            return await self._get_openai_embeddings(texts)
        elif self.provider == "ollama":
            return await self._get_ollama_embeddings(texts)
        elif self.provider == "cohere":
            return await self._get_cohere_embeddings(texts)
        else:
            return [self._get_hash_embedding(text) for text in texts]
    
    async def get_embedding(self, text: str) -> List[float]:
        """Get embedding for a single text"""
        embeddings = await self.get_embeddings([text])
        return embeddings[0]
    
    async def _get_sentence_transformer_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Get embeddings using sentence-transformers"""
        try:
            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            embeddings = await loop.run_in_executor(
                None, 
                lambda: self.model.encode(texts, convert_to_tensor=False)
            )
            return embeddings.tolist()
        except Exception as e:
            logger.error(f"Sentence-transformers error: {e}")
            return [self._get_hash_embedding(text) for text in texts]
    
    async def _get_openai_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Get embeddings using OpenAI API"""
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "input": texts,
                "model": self.model_name
            }
            
            response = await self._request("POST", self.endpoint, json=payload, headers=headers)
            
            if response.status == 200:
                data = await response.json()
                embeddings = [item["embedding"] for item in data["data"]]
                return embeddings
            else:
                logger.error(f"OpenAI API error: {response.status}")
                return [self._get_hash_embedding(text) for text in texts]
                
        except Exception as e:
            logger.error(f"OpenAI embeddings error: {e}")
            return [self._get_hash_embedding(text) for text in texts]
    
    async def _get_ollama_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Get embeddings using Ollama"""
        embeddings = []
        
        for text in texts:
            try:
                async with aiohttp.ClientSession() as session:
                    payload = {
                        "model": self.model_name,
                        "prompt": text
                    }
                    
                    async with session.post(self.endpoint, json=payload) as response:
                        if response.status == 200:
                            data = await response.json()
                            embeddings.append(data.get("embedding", self._get_hash_embedding(text)))
                        else:
                            logger.error(f"Ollama API error: {response.status}")
                            embeddings.append(self._get_hash_embedding(text))
                            
            except Exception as e:
                logger.error(f"Ollama embeddings error: {e}")
                embeddings.append(self._get_hash_embedding(text))
        
        return embeddings
    
    async def _get_cohere_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Get embeddings using Cohere API"""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
                
                payload = {
                    "texts": texts,
                    "model": self.model_name,
                    "input_type": "search_document"
                }
                
                async with session.post(self.endpoint, json=payload, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data["embeddings"]
                    else:
                        logger.error(f"Cohere API error: {response.status}")
                        return [self._get_hash_embedding(text) for text in texts]
                        
        except Exception as e:
            logger.error(f"Cohere embeddings error: {e}")
            return [self._get_hash_embedding(text) for text in texts]
    
    def _get_hash_embedding(self, text: str) -> List[float]:
        """Generate deterministic embedding from text hash"""
        # Create multiple hash values to fill the embedding dimension
        embeddings = []
        
        # Use different hash algorithms for diversity
        hash_funcs = [
            hashlib.sha256,
            hashlib.sha384,
            hashlib.sha512,
            hashlib.md5,
            hashlib.sha1
        ]
        
        for i, hash_func in enumerate(hash_funcs):
            # Create hash with salt for variation
            salted_text = f"{text}_{i}"
            hash_hex = hash_func(salted_text.encode()).hexdigest()
            
            # Convert hex to floats
            for j in range(0, len(hash_hex), 8):
                if len(embeddings) >= self.dimension:
                    break
                    
                hex_chunk = hash_hex[j:j+8]
                # Convert to float between -1 and 1
                value = (int(hex_chunk, 16) / 0xFFFFFFFF) * 2 - 1
                embeddings.append(value)
            
            if len(embeddings) >= self.dimension:
                break
        
        # Pad or truncate to exact dimension
        if len(embeddings) < self.dimension:
            # Pad with normalized values
            embeddings.extend([0.0] * (self.dimension - len(embeddings)))
        else:
            embeddings = embeddings[:self.dimension]
        
        # Normalize the vector
        norm = np.linalg.norm(embeddings)
        if norm > 0:
            embeddings = (np.array(embeddings) / norm).tolist()
        
        return embeddings
    
    def get_dimension(self) -> int:
        """Get the embedding dimension"""
        return self.dimension