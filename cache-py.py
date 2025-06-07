import asyncio
import aiohttp
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from pathlib import Path
import hashlib

try:
    import chromadb
    from chromadb.config import Settings
except ImportError:
    print("ChromaDB not installed. Install with: pip install chromadb")
    raise

from base_agent import BaseAgent
from embedding_provider import EmbeddingProvider

class CacheAgent(BaseAgent):
    def __init__(self, config, state_manager):
        super().__init__("cache", config.data, state_manager)
        
        # Initialize embedding provider
        self.embedding_provider = EmbeddingProvider(config.data)
        
        # Initialize ChromaDB
        chroma_path = Path(self.config["chromadb"]["path"])
        chroma_path.mkdir(exist_ok=True)
        
        self.client = chromadb.PersistentClient(
            path=str(chroma_path),
            settings=Settings(anonymized_telemetry=False)
        )
        
        # Create collection with proper dimension
        collection_name = self.config["chromadb"]["collection"]
        
        # Delete and recreate collection if dimension changed
        try:
            existing_collection = self.client.get_collection(collection_name)
            # Check if we need to recreate due to dimension mismatch
            # For now, we'll keep the existing collection
            self.collection = existing_collection
        except:
            # Create new collection
            self.collection = self.client.create_collection(
                name=collection_name,
                metadata={
                    "description": "Web research cache",
                    "embedding_dimension": self.embedding_provider.get_dimension()
                }
            )
        
        # Cache settings
        self.relevance_threshold = 0.7
        self.max_age_days = 30
        
    async def check(self, query: str, job_id: str) -> Dict[str, Any]:
        """Check cache for relevant information with error recovery"""
        self.logger.info(f"Checking cache for job {job_id}")
        
        context = {
            "query": query,
            "job_id": job_id
        }
        
        return await self.execute_with_recovery(self._check_internal, context)
    
    async def _check_internal(self, query: str, job_id: str) -> Dict[str, Any]:
        """Internal cache check logic"""
        # Get query embedding
        query_embedding = await self.embedding_provider.get_embedding(query)
            
        # Search ChromaDB
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=10,
            where={"$and": [
                {"timestamp": {"$gte": self._get_cutoff_timestamp()}}
            ]}
        )
        
        # Process results
        relevant_items = []
        if results and results["documents"]:
            for i, doc in enumerate(results["documents"][0]):
                distance = results["distances"][0][i] if results["distances"] else 1.0
                relevance_score = 1.0 - distance  # Convert distance to similarity
                
                if relevance_score >= self.relevance_threshold:
                    metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                    relevant_items.append({
                        "content": doc,
                        "metadata": metadata,
                        "relevance_score": relevance_score,
                        "cache_id": results["ids"][0][i] if results["ids"] else None
                    })
        
        # Determine if cache is sufficient
        cache_sufficient = await self._evaluate_cache_sufficiency(query, relevant_items)
        
        result = {
            "job_id": job_id,
            "query": query,
            "cache_hits": len(relevant_items),
            "relevant_items": relevant_items,
            "cache_sufficient": cache_sufficient,
            "recommendation": "use_cache" if cache_sufficient else "fetch_new",
            "timestamp": datetime.utcnow().isoformat()
        }
        
        await self.add_result(job_id, result)
        
        return result
    
    async def store(self, job_id: str, data_type: str, data: Dict[str, Any]) -> bool:
        """Store data in cache"""
        self.logger.info(f"Storing {data_type} for job {job_id}")
        
        try:
            # Prepare document
            content = json.dumps(data.get("content", data))
            doc_id = self._generate_id(job_id, data_type, content)
            
            # Get embedding
            embedding = await self.embedding_provider.get_embedding(content[:1000])  # Limit size
            
            # Prepare metadata
            metadata = {
                "job_id": job_id,
                "data_type": data_type,
                "timestamp": datetime.utcnow().isoformat(),
                "url": data.get("url", ""),
                "title": data.get("title", ""),
                "query": data.get("query", "")
            }
            
            # Store in ChromaDB
            self.collection.upsert(
                ids=[doc_id],
                embeddings=[embedding],
                documents=[content],
                metadatas=[metadata]
            )
            
            self.logger.info(f"Successfully stored {doc_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Cache store error: {e}")
            return False
    
    async def _evaluate_cache_sufficiency(self, query: str, cached_items: List[Dict[str, Any]]) -> bool:
        """Use LLM to evaluate if cached data is sufficient"""
        if not cached_items:
            return False
        
        # Prepare cached content summary
        cache_summary = "\n\n".join([
            f"Item {i+1} (relevance: {item['relevance_score']:.2f}):\n{item['content'][:500]}..."
            for i, item in enumerate(cached_items[:5])
        ])
        
        prompt = f"""Evaluate if the cached information is sufficient to answer the user's query.

User Query: {query}

Cached Information:
{cache_summary}

Is this cached information sufficient to provide a comprehensive answer?
Consider:
- Does it directly address the query?
- Is the information recent enough?
- Are there significant gaps?

Respond with JSON containing:
- sufficient: true/false
- confidence: 0.0 to 1.0
- reasoning: brief explanation

Respond only with valid JSON."""

        try:
            from llm_client import LLMClient
            
            llm = LLMClient(self.llm_config, self.session_pool)
            response = await llm.chat([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=200)
            
            evaluation = json.loads(response)
            return evaluation.get("sufficient", False)
                    
        except Exception as e:
            self.logger.error(f"Failed to evaluate cache sufficiency: {e}")
            # Conservative: fetch new data if evaluation fails
            return False
    
    def _generate_id(self, job_id: str, data_type: str, content: str) -> str:
        """Generate unique ID for cache entry"""
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:8]
        return f"{job_id}_{data_type}_{content_hash}"
    
    def _get_cutoff_timestamp(self) -> str:
        """Get timestamp for cache age cutoff"""
        cutoff = datetime.utcnow() - timedelta(days=self.max_age_days)
        return cutoff.isoformat()

async def main():
    """Standalone testing"""
    config = Config()
    state_manager = StateManager()
    await state_manager.start()
    
    cache = CacheAgent(config, state_manager)
    
    # Test storing and retrieving
    test_data = {
        "content": "Quantum computing uses quantum bits or qubits...",
        "url": "https://example.com/quantum",
        "title": "Introduction to Quantum Computing"
    }
    
    # Store
    await cache.store("test-job-123", "web_content", test_data)
    
    # Check
    result = await cache.check("What is quantum computing?", "test-job-124")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    asyncio.run(main())