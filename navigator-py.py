            await self.add_result(job_id, result)
            await self.update_status("healthy", True)import asyncio
import json
import logging
from typing import Dict, Any, List, Set
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from base_agent import BaseAgent
from llm_client import LLMClient

class NavigatorAgent(BaseAgent):
    def __init__(self, config, state_manager):
        super().__init__("navigator", config.data, state_manager)
        
        self.url_blacklist: Set[str] = set()
        self.max_retries = 3
        self.retry_delay = 1.0  # Initial delay in seconds
        
    async def process(self, query: str, job_id: str, search_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Process search results and identify URLs to fetch"""
        self.logger.info(f"Navigating search results for job {job_id}")
        
        try:
            # Extract URLs from search results
            available_urls = self._extract_urls(search_results)
            
            # Use LLM to select relevant URLs and suggest new searches
            navigation_plan = await self._create_navigation_plan(query, search_results, available_urls)
            
            # Filter out blacklisted URLs
            selected_urls = [
                url for url in navigation_plan.get("urls_to_fetch", [])
                if url not in self.url_blacklist
            ]
            
            # Add URLs with retry logic metadata
            urls_with_metadata = [
                {
                    "url": url,
                    "priority": self._calculate_priority(url, navigation_plan),
                    "max_retries": self.max_retries,
                    "retry_delay": self.retry_delay
                }
                for url in selected_urls
            ]
            
            result = {
                "job_id": job_id,
                "query": query,
                "urls_to_fetch": urls_with_metadata,
                "additional_searches": navigation_plan.get("additional_searches", []),
                "navigation_strategy": navigation_plan.get("strategy", ""),
                "timestamp": datetime.utcnow().isoformat()
            }
            
            await self.add_result(job_id, result)
            await self.update_status("healthy", True)
            
            return result
            
        except Exception as e:
            self.logger.error(f"Navigator error: {e}")
            await self.update_status("error", False)
            
            # Fallback: return top URLs from search results
            return self._fallback_navigation(query, job_id, search_results)
    
    def _extract_urls(self, search_results: List[Dict[str, Any]]) -> List[str]:
        """Extract all URLs from search results"""
        urls = []
        for result in search_results:
            if isinstance(result, dict):
                if "url" in result:
                    urls.append(result["url"])
                elif "link" in result:
                    urls.append(result["link"])
                # Handle nested results
                if "results" in result:
                    for item in result["results"]:
                        if isinstance(item, dict) and "url" in item:
                            urls.append(item["url"])
        return urls
    
    async def _create_navigation_plan(self, query: str, search_results: List[Dict[str, Any]], 
                                     available_urls: List[str]) -> Dict[str, Any]:
        """Use LLM to create navigation plan"""
        # Prepare search results summary
        results_summary = self._summarize_search_results(search_results)
        
        prompt = f"""Given the user's query and search results, create a navigation plan.

User Query: {query}

Search Results Summary:
{results_summary}

Available URLs:
{json.dumps(available_urls[:20], indent=2)}

Create a navigation plan with:
1. urls_to_fetch: List of most relevant URLs (max 10) ordered by relevance
2. additional_searches: List of new search queries if current results are insufficient (max 3)
3. strategy: Brief explanation of your selection criteria

Consider:
- Source credibility and authority
- Content relevance to the query
- Recency for time-sensitive topics
- Diversity of perspectives

Respond with JSON containing: urls_to_fetch, additional_searches, strategy

Respond only with valid JSON."""

        try:
            llm = LLMClient(self.llm_config, self.session_pool)
            response = await llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=500
            )
            
            return json.loads(response)
        except (KeyError, json.JSONDecodeError) as e:
            self.logger.error(f"Failed to parse navigation plan: {e}")
            raise
    
    def _summarize_search_results(self, search_results: List[Dict[str, Any]]) -> str:
        """Create a summary of search results for LLM context"""
        summary_parts = []
        
        for i, result in enumerate(search_results[:10]):  # Limit to first 10
            if isinstance(result, dict):
                title = result.get("title", "No title")
                snippet = result.get("snippet", result.get("description", "No description"))
                url = result.get("url", result.get("link", "No URL"))
                
                summary_parts.append(f"{i+1}. {title}\n   {snippet[:200]}...\n   URL: {url}")
        
        return "\n\n".join(summary_parts)
    
    def _calculate_priority(self, url: str, navigation_plan: Dict[str, Any]) -> int:
        """Calculate URL priority based on position in plan"""
        urls_list = navigation_plan.get("urls_to_fetch", [])
        try:
            # Higher priority (lower number) for URLs earlier in the list
            return urls_list.index(url) + 1
        except ValueError:
            return 99  # Low priority if not in list
    
    def _fallback_navigation(self, query: str, job_id: str, search_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Fallback when LLM fails: return top URLs"""
        urls = self._extract_urls(search_results)
        
        # Simple heuristic: take first 5 URLs
        selected_urls = [
            {
                "url": url,
                "priority": i + 1,
                "max_retries": self.max_retries,
                "retry_delay": self.retry_delay
            }
            for i, url in enumerate(urls[:5])
        ]
        
        return {
            "job_id": job_id,
            "query": query,
            "urls_to_fetch": selected_urls,
            "additional_searches": [],
            "navigation_strategy": "Fallback: selected top 5 search results",
            "timestamp": datetime.utcnow().isoformat(),
            "fallback": True
        }
    
    async def add_to_blacklist(self, url: str, reason: str = ""):
        """Add URL to blacklist"""
        self.url_blacklist.add(url)
        self.logger.info(f"Blacklisted URL: {url} - Reason: {reason}")

async def main():
    """Standalone testing"""
    config = Config()
    state_manager = StateManager()
    await state_manager.start()
    
    navigator = NavigatorAgent(config, state_manager)
    
    # Mock search results
    mock_results = [
        {
            "title": "Quantum Computing Breakthrough",
            "snippet": "Recent advances in quantum computing...",
            "url": "https://example.com/quantum-breakthrough"
        },
        {
            "title": "Introduction to Quantum Computing",
            "snippet": "Learn the basics of quantum computing...",
            "url": "https://example.com/quantum-intro"
        }
    ]
    
    result = await navigator.process(
        "What are the latest developments in quantum computing?",
        "test-job-123",
        mock_results
    )
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    asyncio.run(main())