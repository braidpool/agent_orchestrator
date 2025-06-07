import asyncio
import json
import logging
from typing import Dict, Any, List
from datetime import datetime
from pathlib import Path

from base_agent import BaseAgent
from llm_client import LLMClient

class PreparerAgent(BaseAgent):
    def __init__(self, config, state_manager):
        super().__init__("preparer", config.data, state_manager)
    
    def _register_agent_tools(self):
        """Register preparer-specific tools"""
        if self.tool_registry:
            self.tool_registry.register_tool(
                "preparer",
                "generate_followup_searches",
                self._generate_followup_searches,
                "Generate follow-up search queries based on gaps"
            )
    
    async def process(self, query: str, job_id: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Generate search queries and classify query type"""
        self.logger.info(f"Preparing searches for job {job_id}")
        
        try:
            if self.circuit_open:
                self.logger.warning("Circuit breaker open, using basic search")
                return self._basic_search_queries(query, job_id)
            
            # Get query classification and search queries from LLM
            classification = await self._classify_query(query)
            search_queries = await self._generate_search_queries(query, classification)
            
            # Reset circuit breaker on success
            self.consecutive_failures = 0
            await self.update_status("healthy", True)
            
            result = {
                "job_id": job_id,
                "query": query,
                "classification": classification,
                "search_queries": search_queries,
                "timestamp": datetime.utcnow().isoformat()
            }
            
            await self.add_result(job_id, result)
            
            return result
            
        except Exception as e:
            self.logger.error(f"Preparer error: {e}")
            self.consecutive_failures += 1
            
            if self.consecutive_failures >= self.circuit_breaker_threshold:
                self.circuit_open = True
                self.logger.error("Circuit breaker opened")
            
            await self.update_status("error", False)
            
            return self._basic_search_queries(query, job_id)
    
    async def _classify_query(self, query: str) -> Dict[str, Any]:
        """Classify the query type"""
        prompt = f"""Classify this query to help guide web search strategy.

Query: {query}

Provide JSON with:
- primary_intent: "factual" | "analytical" | "comparative" | "exploratory" | "instructional"
- domains: list of relevant domains (e.g., ["technology", "science", "business"])
- temporal_relevance: "current" | "recent" | "historical" | "timeless"
- expected_source_types: list of source types (e.g., ["news", "academic", "official", "community"])

Respond only with valid JSON."""

        try:
            llm = LLMClient(self.llm_config, self.session_pool)
            response = await llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200
            )
            
            return json.loads(response)
        except (KeyError, json.JSONDecodeError) as e:
            self.logger.error(f"Failed to parse classification: {e}")
            raise
    
    async def _generate_search_queries(self, query: str, classification: Dict[str, Any]) -> List[str]:
        """Generate optimized search queries"""
        prompt = f"""Generate web search queries to answer this question comprehensively.

Original query: {query}
Classification: {json.dumps(classification)}

Create 3-5 search queries that:
1. Cover different aspects of the question
2. Use effective search operators when needed
3. Target the expected source types
4. Are concise but specific

Respond with JSON containing:
- queries: list of search query strings
- search_strategy: brief explanation of the approach

Respond only with valid JSON."""

        try:
            llm = LLMClient(self.llm_config, self.session_pool)
            response = await llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=300
            )
            
            parsed = json.loads(response)
            return parsed.get("queries", [query])
        except (KeyError, json.JSONDecodeError) as e:
            self.logger.error(f"Failed to parse search queries: {e}")
            raise
    
    def _basic_search_queries(self, query: str, job_id: str) -> Dict[str, Any]:
        """Fallback: generate basic search queries without LLM"""
        # Simple heuristic: use the original query and a few variations
        queries = [
            query,
            f"{query} site:wikipedia.org",
            f"{query} latest news",
            f"{query} explained"
        ]
        
        return {
            "job_id": job_id,
            "query": query,
            "classification": {
                "primary_intent": "unknown",
                "domains": [],
                "temporal_relevance": "unknown",
                "expected_source_types": ["general"]
            },
            "search_queries": queries[:3],  # Limit to 3
            "timestamp": datetime.utcnow().isoformat(),
            "fallback": True
        }
    
    async def _generate_followup_searches(self, tool_call) -> Dict[str, Any]:
        """Generate follow-up searches based on identified gaps"""
        query = tool_call.parameters.get("query", "")
        gaps = tool_call.parameters.get("identified_gaps", [])
        current_summaries = tool_call.parameters.get("current_summaries", [])
        
        # Create context from current summaries
        context = "\n".join([s.get("summary", "")[:200] for s in current_summaries[:3]])
        
        prompt = f"""Generate targeted search queries to fill information gaps.

Original Query: {query}

Current Information Context:
{context}

Information Gaps:
{json.dumps(gaps, indent=2)}

Generate 3-5 specific search queries that will help fill these gaps.
Make queries specific and targeted to find the missing information.

Respond with JSON:
- search_queries: list of search query strings
- strategy: explanation of search approach

Respond only with valid JSON."""

        try:
            llm = LLMClient(self.llm_config, self.session_pool)
            response = await llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=300
            )
            
            result = json.loads(response)
            return result
            
        except Exception as e:
            self.logger.error(f"Failed to generate follow-up searches: {e}")
            # Fallback: simple gap-based queries
            return {
                "search_queries": [f"{query} {gap}" for gap in gaps[:3]],
                "strategy": "Direct gap-based search (fallback)"
            }

async def main():
    """Standalone testing"""
    config = Config()
    state_manager = StateManager()
    await state_manager.start()
    
    preparer = PreparerAgent(config, state_manager)
    
    # Test query
    result = await preparer.process(
        "What are the latest developments in quantum computing?",
        "test-job-123"
    )
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    asyncio.run(main())