import asyncio
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path
import hashlib
from urllib.parse import urlparse

from base_agent import BaseAgent
from llm_client import LLMClient

class ValidatorAgent(BaseAgent):
    def __init__(self, config, state_manager):
        super().__init__("validator", config.data, state_manager)
        
        # Quality thresholds
        self.min_content_length = 100
        self.min_quality_score = 0.3
        
    async def process(self, query: str, job_id: str, 
                     fetched_content: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Validate and filter fetched content"""
        self.logger.info(f"Validating {len(fetched_content)} items for job {job_id}")
        
        try:
            validated_items = []
            
            for item in fetched_content:
                # Basic validation
                if not self._basic_validation(item):
                    continue
                
                # LLM-based quality assessment
                quality_assessment = await self._assess_quality(query, item)
                
                if quality_assessment["quality_score"] >= self.min_quality_score:
                    validated_item = {
                        **item,
                        "quality_assessment": quality_assessment,
                        "content_hash": self._hash_content(item.get("content", "")),
                        "validated_at": datetime.utcnow().isoformat()
                    }
                    validated_items.append(validated_item)
                else:
                    self.logger.info(
                        f"Rejected {item.get('url', 'unknown')} - "
                        f"Quality score: {quality_assessment['quality_score']}"
                    )
            
            result = {
                "job_id": job_id,
                "query": query,
                "validated_content": validated_items,
                "total_processed": len(fetched_content),
                "total_validated": len(validated_items),
                "rejection_rate": 1 - (len(validated_items) / max(len(fetched_content), 1)),
                "timestamp": datetime.utcnow().isoformat()
            }
            
            await self.add_result(job_id, result)
            await self.update_status("healthy", True)
            
            return result
            
        except Exception as e:
            self.logger.error(f"Validator error: {e}")
            await self.update_status("error", False)
            
            # Fallback: basic filtering only
            return self._fallback_validation(query, job_id, fetched_content)
    
    def _basic_validation(self, item: Dict[str, Any]) -> bool:
        """Perform basic content validation"""
        # Check if content exists
        content = item.get("content", "")
        if not content or len(content) < self.min_content_length:
            return False
        
        # Check URL validity
        url = item.get("url", "")
        if not url:
            return False
        
        try:
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                return False
        except Exception:
            return False
        
        # Check for common error patterns
        error_patterns = [
            "404 not found",
            "403 forbidden",
            "access denied",
            "page not found",
            "error loading page"
        ]
        
        content_lower = content.lower()
        for pattern in error_patterns:
            if pattern in content_lower[:500]:  # Check first 500 chars
                return False
        
        return True
    
    async def _assess_quality(self, query: str, item: Dict[str, Any]) -> Dict[str, Any]:
        """Use LLM to assess content quality and relevance"""
        content_preview = item.get("content", "")[:2000]  # Limit content size
        
        prompt = f"""Assess the quality and relevance of this web content for answering the user's query.

User Query: {query}

URL: {item.get('url', 'Unknown')}
Title: {item.get('title', 'No title')}

Content Preview:
{content_preview}

Provide a quality assessment with:
- quality_score: 0.0 to 1.0 (relevance and reliability)
- source_credibility: "high" | "medium" | "low"
- content_type: "primary_source" | "news" | "opinion" | "reference" | "commercial"
- relevance_explanation: Brief explanation
- potential_issues: List any concerns (bias, outdated, incomplete)

Respond only with valid JSON."""

        try:
            llm = LLMClient(self.llm_config, self.session_pool)
            response = await llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=300
            )
            
            return json.loads(response)
        except (KeyError, json.JSONDecodeError) as e:
            self.logger.error(f"Failed to parse quality assessment: {e}")
            # Return default assessment
            return {
                "quality_score": 0.5,
                "source_credibility": "medium",
                "content_type": "unknown",
                "relevance_explanation": "Could not assess",
                "potential_issues": ["Assessment failed"]
            }
    
    def _hash_content(self, content: str) -> str:
        """Generate hash of content for deduplication"""
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def _fallback_validation(self, query: str, job_id: str, 
                           fetched_content: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Fallback: basic filtering without LLM"""
        validated_items = []
        
        for item in fetched_content:
            if self._basic_validation(item):
                validated_item = {
                    **item,
                    "quality_assessment": {
                        "quality_score": 0.5,
                        "source_credibility": "unknown",
                        "content_type": "unknown",
                        "relevance_explanation": "Basic validation only",
                        "potential_issues": []
                    },
                    "content_hash": self._hash_content(item.get("content", "")),
                    "validated_at": datetime.utcnow().isoformat()
                }
                validated_items.append(validated_item)
        
        return {
            "job_id": job_id,
            "query": query,
            "validated_content": validated_items,
            "total_processed": len(fetched_content),
            "total_validated": len(validated_items),
            "rejection_rate": 1 - (len(validated_items) / max(len(fetched_content), 1)),
            "timestamp": datetime.utcnow().isoformat(),
            "fallback": True
        }

async def main():
    """Standalone testing"""
    config = Config()
    state_manager = StateManager()
    await state_manager.start()
    
    validator = ValidatorAgent(config, state_manager)
    
    # Mock fetched content
    mock_content = [
        {
            "url": "https://example.com/quantum",
            "title": "Quantum Computing Advances",
            "content": "Recent breakthroughs in quantum computing have shown..." + "x" * 500
        },
        {
            "url": "https://spam.com/ads",
            "title": "Buy Quantum Products",
            "content": "404 Not Found"
        }
    ]
    
    result = await validator.process(
        "What are the latest developments in quantum computing?",
        "test-job-123",
        mock_content
    )
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    asyncio.run(main())