import asyncio
import json
import logging
from typing import Dict, Any, List
from datetime import datetime
import uuid

from base_agent import BaseAgent
from llm_client import LLMClient

class RouterAgent(BaseAgent):
    def __init__(self, config, state_manager):
        super().__init__("router", config.data, state_manager)
        
    async def process(self, query: str, job_id: str = None) -> Dict[str, Any]:
        """Route query to appropriate agent pipeline with error recovery"""
        if not job_id:
            job_id = str(uuid.uuid4())
            
        self.logger.info(f"Processing query: {query[:100]}... Job ID: {job_id}")
        
        # Use error recovery wrapper
        context = {
            "query": query,
            "job_id": job_id
        }
        
        return await self.execute_with_recovery(self._process_internal, context)
    
    async def _process_internal(self, query: str, job_id: str) -> Dict[str, Any]:
        """Internal processing logic"""
        # Check circuit breaker
        if self.circuit_open:
            self.logger.warning("Circuit breaker open, using default routing")
            return self._default_routing(query, job_id)
        
        # Call LLM to analyze query
        analysis = await self._analyze_query(query)
        
        # Determine routing based on analysis
        routing = self._determine_routing(analysis)
        
        result = {
            "job_id": job_id,
            "query": query,
            "analysis": analysis,
            "routing": routing,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        await self.add_result(job_id, result)
        
        return result
    
    async def _analyze_query(self, query: str) -> Dict[str, Any]:
        """Use LLM to analyze query complexity and type"""
        prompt = f"""Analyze this query and determine its complexity and type.

Query: {query}

Respond with JSON containing:
- complexity: "simple" | "moderate" | "complex"
- type: "factual" | "analytical" | "comparative" | "exploratory"
- requires_web_search: true | false
- requires_deep_research: true | false
- confidence: 0.0 to 1.0

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
            self.logger.error(f"Failed to parse LLM response: {e}")
            raise
    
    def _determine_routing(self, analysis: Dict[str, Any]) -> List[str]:
        """Determine which agents to use based on analysis"""
        complexity = analysis.get("complexity", "moderate")
        requires_web = analysis.get("requires_web_search", True)
        requires_deep = analysis.get("requires_deep_research", False)
        
        # Simple queries might just need cache + answerer
        if complexity == "simple" and not requires_web:
            return ["cache", "answerer"]
        
        # Complex queries need full pipeline
        if complexity == "complex" or requires_deep:
            return ["cache", "preparer", "navigator", "validator", "summarizer", "answerer"]
        
        # Moderate queries
        if requires_web:
            return ["cache", "preparer", "navigator", "validator", "summarizer", "answerer"]
        else:
            return ["cache", "summarizer", "answerer"]
    
    def _default_routing(self, query: str, job_id: str) -> Dict[str, Any]:
        """Fallback routing when LLM fails"""
        return {
            "job_id": job_id,
            "query": query,
            "analysis": {
                "complexity": "moderate",
                "type": "unknown",
                "requires_web_search": True,
                "confidence": 0.0
            },
            "routing": ["cache", "preparer", "navigator", "validator", "summarizer", "answerer"],
            "timestamp": datetime.utcnow().isoformat(),
            "fallback": True
        }

async def main():
    """Standalone testing"""
    config = Config()
    state_manager = StateManager()
    await state_manager.start()
    
    router = RouterAgent(config, state_manager)
    
    # Test query
    result = await router.process("What is the capital of France?")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    asyncio.run(main())