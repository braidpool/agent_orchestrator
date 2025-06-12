import asyncio
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path
import re
import aiohttp

from base_agent import BaseAgent
from llm_client import LLMClient
from tool_protocol import AgentToolkit

class SummarizerAgent(BaseAgent):
    def __init__(self, config, state_manager):
        super().__init__("summarizer", config.data, state_manager)
        
        # Chunking settings
        self.chunk_size = 3000  # Characters per chunk
        self.chunk_overlap = 200  # Overlap between chunks
        self.max_chunks_per_doc = 10
        
    def _register_agent_tools(self):
        """Register summarizer-specific tools"""
        if self.tool_registry:
            self.tool_registry.register_tool(
                "summarizer",
                "request_specific_info",
                self._handle_specific_info_request,
                "Request specific information to fill gaps"
            )
        
    async def process(self, query: str, job_id: str, 
                     validated_content: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Summarize validated content"""
        self.logger.info(f"Summarizing {len(validated_content)} items for job {job_id}")
        
        try:
            summaries = []
            
            for item in validated_content:
                # Process each document
                doc_summary = await self._summarize_document(query, item)
                
                if doc_summary:
                    summaries.append({
                        "url": item.get("url", ""),
                        "title": item.get("title", ""),
                        "summary": doc_summary["summary"],
                        "key_points": doc_summary.get("key_points", []),
                        "relevance_score": doc_summary.get("relevance_score", 0.5),
                        "source_metadata": {
                            "quality_assessment": item.get("quality_assessment", {}),
                            "content_hash": item.get("content_hash", ""),
                            "validated_at": item.get("validated_at", "")
                        }
                    })
            
            # Create consolidated summary if multiple documents
            consolidated = None
            if len(summaries) > 1:
                consolidated = await self._create_consolidated_summary(query, summaries)
            
            # Check if more research needed
            needs_more_research = await self._evaluate_completeness(query, summaries)
            
            # If insufficient, try to get more information
            if needs_more_research and self.tool_registry:
                self.logger.info("Summaries insufficient, requesting more research")
                
                # Identify what's missing
                gaps = await self._identify_information_gaps(query, summaries)
                
                try:
                    # Request more searches from preparer
                    additional_searches = await self.call_tool(
                        "preparer",
                        "generate_followup_searches",
                        {
                            "query": query,
                            "current_summaries": summaries,
                            "identified_gaps": gaps
                        }
                    )
                    
                    # Add to result so orchestrator can act on it
                    needs_more_research = {
                        "required": True,
                        "gaps": gaps,
                        "suggested_searches": additional_searches.get("search_queries", [])
                    }
                    
                except Exception as e:
                    self.logger.error(f"Failed to request additional searches: {e}")
                    needs_more_research = {"required": True, "gaps": gaps}
            
            result = {
                "job_id": job_id,
                "query": query,
                "document_summaries": summaries,
                "consolidated_summary": consolidated,
                "total_documents": len(validated_content),
                "total_summarized": len(summaries),
                "needs_more_research": needs_more_research,
                "timestamp": datetime.utcnow().isoformat()
            }
            
            await self.add_result(job_id, result)
            await self.update_status("healthy", True)
            
            return result
            
        except Exception as e:
            self.logger.error(f"Summarizer error: {e}")
            await self.update_status("error", False)
            
            return self._fallback_summary(query, job_id, validated_content)
    
    async def _summarize_document(self, query: str, document: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Summarize a single document"""
        content = document.get("content", "")
        if not content:
            return None
        
        # Chunk large documents
        chunks = self._chunk_text(content)
        
        if len(chunks) == 1:
            # Small document - single summary
            return await self._summarize_chunk(query, chunks[0], document)
        else:
            # Large document - chunk and combine
            chunk_summaries = []
            for i, chunk in enumerate(chunks[:self.max_chunks_per_doc]):
                summary = await self._summarize_chunk(
                    query, chunk, document, chunk_num=i+1, total_chunks=len(chunks)
                )
                if summary:
                    chunk_summaries.append(summary)
            
            # Combine chunk summaries
            if chunk_summaries:
                return await self._combine_chunk_summaries(query, chunk_summaries, document)
        
        return None
    
    def _chunk_text(self, text: str) -> List[str]:
        """Split text into overlapping chunks"""
        chunks = []
        start = 0
        
        while start < len(text):
            end = start + self.chunk_size
            
            # Try to break at sentence boundary
            if end < len(text):
                # Look for sentence end
                sentence_end = text.rfind('. ', start + self.chunk_size - self.chunk_overlap, end)
                if sentence_end != -1:
                    end = sentence_end + 1
            
            chunks.append(text[start:end])
            start = end - self.chunk_overlap
        
        return chunks
    
    async def _summarize_chunk(self, query: str, chunk: str, document: Dict[str, Any],
                              chunk_num: int = 1, total_chunks: int = 1) -> Optional[Dict[str, Any]]:
        """Summarize a single chunk"""
        chunk_info = f"(Part {chunk_num} of {total_chunks})" if total_chunks > 1 else ""
        
        prompt = f"""Summarize this content focusing on information relevant to the user's query.

User Query: {query}

Source: {document.get('title', 'Unknown')} {chunk_info}
URL: {document.get('url', 'Unknown')}

Content:
{chunk}

Provide a summary with:
- summary: Concise summary focusing on query-relevant information (2-3 paragraphs)
- key_points: List of 3-5 key points most relevant to the query
- relevance_score: 0.0 to 1.0 rating of content relevance to query
- quotes: List of 1-2 important quotes with context (if any)

IMPORTANT: Preserve source attribution. Include phrases like "According to [source]" or "The article states".

Respond only with valid JSON."""

        try:
            llm = LLMClient(self.llm_config, self.session_pool)
            response = await llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=500
            )
            
            return json.loads(response)
        except Exception as e:
            self.logger.error(f"Failed to summarize chunk: {e}")
            return None
    
    async def _combine_chunk_summaries(self, query: str, chunk_summaries: List[Dict[str, Any]], 
                                     document: Dict[str, Any]) -> Dict[str, Any]:
        """Combine multiple chunk summaries into one"""
        combined_text = "\n\n".join([s["summary"] for s in chunk_summaries if s])
        all_key_points = []
        for s in chunk_summaries:
            if s and "key_points" in s:
                all_key_points.extend(s["key_points"])
        
        prompt = f"""Combine these chunk summaries into a cohesive summary for the user's query.

User Query: {query}

Source: {document.get('title', 'Unknown')}
URL: {document.get('url', 'Unknown')}

Chunk Summaries:
{combined_text}

All Key Points:
{json.dumps(all_key_points, indent=2)}

Create a unified summary with:
- summary: Cohesive 2-3 paragraph summary
- key_points: Top 5 most important points
- relevance_score: Overall relevance to query (0.0 to 1.0)

Maintain source attribution throughout.

Respond only with valid JSON."""

        try:
            llm = LLMClient(self.llm_config, self.session_pool)
            response = await llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=600
            )
            
            return json.loads(response)
                    
        except Exception as e:
            self.logger.error(f"Failed to combine summaries: {e}")
            # Fallback: return first chunk summary
            return chunk_summaries[0] if chunk_summaries else None
    
    async def _create_consolidated_summary(self, query: str, summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Create a consolidated summary from multiple documents"""
        # Prepare summaries text
        summaries_text = "\n\n".join([
            f"Source: {s['title']}\nURL: {s['url']}\nSummary: {s['summary']}"
            for s in summaries[:10]  # Limit to prevent context overflow
        ])
        
        prompt = f"""Create a consolidated summary that synthesizes information from multiple sources.

User Query: {query}

Individual Summaries:
{summaries_text}

Create a comprehensive synthesis that:
1. Identifies common themes and findings
2. Notes any contradictions or differing viewpoints
3. Maintains source attribution
4. Focuses on answering the user's query

Provide:
- synthesis: Comprehensive 3-4 paragraph synthesis
- main_findings: List of key findings across sources
- source_consensus: Areas of agreement/disagreement
- gaps: Any notable information gaps

Respond only with valid JSON."""

        try:
            llm = LLMClient(self.llm_config, self.session_pool)
            response = await llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=800
            )
            
            return json.loads(response)
                    
        except Exception as e:
            self.logger.error(f"Failed to create consolidated summary: {e}")
            return None
    
    async def _evaluate_completeness(self, query: str, summaries: List[Dict[str, Any]]) -> bool:
        """Evaluate if more research is needed"""
        if not summaries:
            return True
        
        # Prepare evaluation context
        summary_overview = "\n".join([
            f"- {s['title']}: Relevance {s.get('relevance_score', 0.5):.1f}"
            for s in summaries[:10]
        ])
        
        prompt = f"""Evaluate if the summarized information adequately answers the user's query.

User Query: {query}

Available Summaries ({len(summaries)} documents):
{summary_overview}

Key findings across sources:
{self._extract_key_findings(summaries)}

Does this information:
1. Fully answer the user's query?
2. Provide sufficient depth and breadth?
3. Include credible, recent sources?

Respond with JSON:
- needs_more_research: true/false
- reasoning: Brief explanation
- missing_aspects: List of what's missing (if any)

Respond only with valid JSON."""

        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.llm_config.host}/v1/chat/completions"
                
                payload = {
                    "model": self.llm_config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 200
                }
                
                headers = {}
                if self.llm_config.api_key:
                    headers["Authorization"] = f"Bearer {self.llm_config.api_key}"
                
                async with session.post(url, json=payload, headers=headers) as response:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    evaluation = json.loads(content)
                    return evaluation.get("needs_more_research", False)
                    
        except Exception as e:
            self.logger.error(f"Failed to evaluate completeness: {e}")
    async def _identify_information_gaps(self, query: str, summaries: List[Dict[str, Any]]) -> List[str]:
        """Identify what information is missing"""
        summary_text = "\n".join([s.get("summary", "") for s in summaries[:5]])
        
        prompt = f"""Analyze what information is missing to fully answer the user's query.

User Query: {query}

Current Information Summary:
{summary_text[:2000]}

Identify specific gaps or missing information needed to provide a complete answer.

Respond with JSON:
- gaps: list of specific missing information
- priority_gaps: top 3 most important gaps

Respond only with valid JSON."""

        try:
            llm = LLMClient(self.llm_config, self.session_pool)
            response = await llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=300
            )
            
            result = json.loads(response)
            return result.get("priority_gaps", result.get("gaps", []))
            
        except Exception as e:
            self.logger.error(f"Failed to identify gaps: {e}")
            return ["additional context needed"]
    
    async def _handle_specific_info_request(self, tool_call) -> Dict[str, Any]:
        """Handle requests for specific information"""
        topic = tool_call.parameters.get("topic", "")
        context = tool_call.parameters.get("context", {})
        
        return {
            "status": "acknowledged",
            "topic": topic,
            "recommendation": "Search for specific sources on this topic"
        }
    
    def _extract_key_findings(self, summaries: List[Dict[str, Any]]) -> str:
        """Extract key findings from summaries"""
        findings = []
        for s in summaries[:5]:
            if "key_points" in s and s["key_points"]:
                findings.extend(s["key_points"][:2])
        
        return "\n".join(f"- {f}" for f in findings[:10])
    
    def _fallback_summary(self, query: str, job_id: str, 
                         validated_content: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Basic summarization without LLM"""
        summaries = []
        
        for item in validated_content[:5]:  # Process only first 5
            content = item.get("content", "")[:1000]  # First 1000 chars
            
            summaries.append({
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "summary": f"Content preview: {content}...",
                "key_points": ["Content available but not summarized"],
                "relevance_score": 0.5
            })
        
        return {
            "job_id": job_id,
            "query": query,
            "document_summaries": summaries,
            "consolidated_summary": None,
            "total_documents": len(validated_content),
            "total_summarized": len(summaries),
            "needs_more_research": True,
            "timestamp": datetime.utcnow().isoformat(),
            "fallback": True
        }

async def main():
    """Standalone testing"""
    config = Config()
    state_manager = StateManager()
    await state_manager.start()
    
    summarizer = SummarizerAgent(config, state_manager)
    
    # Mock validated content
    mock_content = [{
        "url": "https://example.com/quantum",
        "title": "Quantum Computing Breakthrough",
        "content": "Recent advances in quantum computing have demonstrated..." + "x" * 2000,
        "quality_assessment": {"quality_score": 0.9}
    }]
    
    result = await summarizer.process(
        "What are the latest developments in quantum computing?",
        "test-job-123",
        mock_content
    )
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    asyncio.run(main())