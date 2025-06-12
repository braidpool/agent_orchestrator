import asyncio
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path

from base_agent import BaseAgent
from llm_client import LLMClient

class AnswererAgent(BaseAgent):
    def __init__(self, config, state_manager):
        super().__init__("answerer", config.data, state_manager)
        
        # Answer generation settings
        self.min_confidence_threshold = 0.6
        self.max_answer_length = 2000
        
    def _register_agent_tools(self):
        """Register answerer-specific tools"""
        if self.tool_registry:
            self.tool_registry.register_tool(
                "answerer",
                "improve_answer",
                self._improve_answer,
                "Improve answer with additional context"
            )
        
    async def process(self, query: str, job_id: str, 
                     summarized_data: Dict[str, Any],
                     conversation_context: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generate final answer with citations"""
        self.logger.info(f"Generating answer for job {job_id}")
        
        try:
            # Extract summaries and metadata
            doc_summaries = summarized_data.get("document_summaries", [])
            consolidated = summarized_data.get("consolidated_summary", {})
            needs_more = summarized_data.get("needs_more_research", False)
            
            # Check if we have sufficient information
            if not doc_summaries and not consolidated:
                return self._insufficient_data_response(query, job_id)
            
            # Generate the answer
            answer_data = await self._generate_answer(
                query, doc_summaries, consolidated, conversation_context
            )
            
            # Extract citations
            citations = self._extract_citations(doc_summaries, answer_data)
            
            # Evaluate answer confidence
            confidence = await self._evaluate_answer_confidence(
                query, answer_data["answer"], citations
            )
            
            # Determine if more research is needed
            suggest_more_research = (
                needs_more or 
                confidence < self.min_confidence_threshold or
                answer_data.get("gaps_identified", False)
            )
            
            result = {
                "job_id": job_id,
                "query": query,
                "answer": answer_data["answer"],
                "citations": citations,
                "confidence_score": confidence,
                "answer_type": answer_data.get("answer_type", "comprehensive"),
                "key_insights": answer_data.get("key_insights", []),
                "suggest_more_research": suggest_more_research,
                "follow_up_questions": answer_data.get("follow_up_questions", []),
                "timestamp": datetime.utcnow().isoformat()
            }

            # If low confidence, try to improve the answer
            if confidence < self.min_confidence_threshold and self.tool_registry:
                self.logger.info(f"Low confidence ({confidence:.2f}), attempting to improve answer")

                # Use tool calling to request help
                help_result = await self.evaluate_and_request_help(
                    {"confidence_score": confidence, "answer": answer_data["answer"]},
                    self.min_confidence_threshold
                )

                if help_result and help_result.get("help_needed"):
                    # Log what help is needed
                    for help_request in help_result["help_needed"]:
                        self.logger.info(f"Help needed from {help_request['agent']}: {help_request['reason']}")

                    # Add to result for orchestrator
                    result["help_requests"] = help_result["help_needed"]
            
            await self.add_result(job_id, result)
            await self.update_status("healthy", True)
            
            return result
            
        except Exception as e:
            self.logger.error(f"Answerer error: {e}")
            await self.update_status("error", False)
            
            return self._error_response(query, job_id, str(e))
    
    async def _generate_answer(self, query: str, doc_summaries: List[Dict[str, Any]], 
                              consolidated: Dict[str, Any],
                              conversation_context: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generate comprehensive answer using LLM"""
        
        # Prepare context
        context = self._prepare_context(doc_summaries, consolidated)
        conversation_history = self._format_conversation_history(conversation_context)
        
        prompt = f"""Generate a comprehensive answer to the user's query based on the research summaries.

User Query: {query}

{conversation_history}

Research Context:
{context}

Instructions:
1. Provide a clear, direct answer to the query
2. Use information from the summaries, citing sources appropriately
3. Structure the answer logically with clear paragraphs
4. Identify any gaps or limitations in the available information
5. Suggest follow-up questions if relevant
6. Maintain a conversational but informative tone

For citations, use this format: [Source Title](URL)

Provide response as JSON with:
- answer: The complete answer text with inline citations
- answer_type: "comprehensive" | "partial" | "speculative"
- key_insights: List of 3-5 main insights
- gaps_identified: true/false
- follow_up_questions: List of 0-3 relevant follow-up questions

Respond only with valid JSON."""

        try:
            llm = LLMClient(self.llm_config, self.session_pool)
            response = await llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=self.max_answer_length
            )
            
            try:
                return json.loads(response)
            except json.JSONDecodeError:
                # Fallback to simple text answer
                return {
                    "answer": response,
                    "answer_type": "partial",
                    "key_insights": [],
                    "gaps_identified": True,
                    "follow_up_questions": []
                }
        except Exception as e:
            self.logger.error(f"Error generating answer: {e}")
            raise
    
    def _prepare_context(self, doc_summaries: List[Dict[str, Any]], 
                        consolidated: Dict[str, Any]) -> str:
        """Prepare research context for answer generation"""
        context_parts = []
        
        # Add consolidated summary if available
        if consolidated and "synthesis" in consolidated:
            context_parts.append(f"Overall Synthesis:\n{consolidated['synthesis']}\n")
            
            if "main_findings" in consolidated:
                findings = "\n".join(f"- {f}" for f in consolidated["main_findings"])
                context_parts.append(f"Main Findings:\n{findings}\n")
        
        # Add individual summaries
        if doc_summaries:
            context_parts.append("Individual Source Summaries:")
            
            for i, summary in enumerate(doc_summaries[:10], 1):  # Limit to prevent overflow
                source_text = f"\n{i}. {summary['title']}"
                source_text += f"\n   URL: {summary['url']}"
                source_text += f"\n   Summary: {summary['summary']}"
                
                if summary.get("key_points"):
                    points = "\n   ".join(f"- {p}" for p in summary["key_points"][:3])
                    source_text += f"\n   Key Points:\n   {points}"
                
                context_parts.append(source_text)
        
        return "\n".join(context_parts)
    
    def _format_conversation_history(self, context: List[Dict[str, Any]] = None) -> str:
        """Format conversation history if available"""
        if not context:
            return ""
        
        history = "Previous Conversation:\n"
        for turn in context[-3:]:  # Last 3 turns
            role = turn.get("role", "user")
            content = turn.get("content", "")[:200]  # Truncate long messages
            history += f"{role.capitalize()}: {content}...\n"
        
        return history + "\n"
    
    def _extract_citations(self, doc_summaries: List[Dict[str, Any]], 
                          answer_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract and format citations from answer"""
        citations = []
        used_urls = set()
        
        answer_text = answer_data.get("answer", "")
        
        for summary in doc_summaries:
            url = summary.get("url", "")
            title = summary.get("title", "")
            
            # Check if this source was referenced in the answer
            if url and (url in answer_text or title in answer_text):
                if url not in used_urls:
                    citations.append({
                        "title": title,
                        "url": url,
                        "relevance_score": summary.get("relevance_score", 0.5),
                        "quality_assessment": summary.get("source_metadata", {}).get("quality_assessment", {})
                    })
                    used_urls.add(url)
        
        return citations
    
    async def _evaluate_answer_confidence(self, query: str, answer: str, 
                                        citations: List[Dict[str, Any]]) -> float:
        """Evaluate confidence in the answer"""
        prompt = f"""Evaluate the quality and confidence level of this answer.

Query: {query}

Answer: {answer}

Number of citations: {len(citations)}

Evaluate:
1. Does the answer directly address the query?
2. Is it well-supported by citations?
3. Is the reasoning clear and logical?
4. Are there any obvious gaps or uncertainties?

Provide a confidence score from 0.0 to 1.0 and brief reasoning.

Respond with JSON: {{"confidence": 0.0-1.0, "reasoning": "..."}}"""

        try:
            llm = LLMClient(self.llm_config, self.session_pool)
            response = await llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=100
            )
            
            evaluation = json.loads(response)
            return evaluation.get("confidence", 0.7)
                    
        except Exception as e:
            self.logger.error(f"Failed to evaluate confidence: {e}")
            # Default confidence based on citations
            return min(0.5 + len(citations) * 0.1, 0.9)
    
    def _insufficient_data_response(self, query: str, job_id: str) -> Dict[str, Any]:
        """Response when insufficient data available"""
        return {
            "job_id": job_id,
            "query": query,
            "answer": "I don't have sufficient information to answer your query. This could be because:\n\n" +
                     "1. The search didn't return relevant results\n" +
                     "2. The available sources don't address your specific question\n" +
                     "3. The topic might require more specialized research\n\n" +
                     "Would you like me to try a different search approach or refine the query?",
            "citations": [],
            "confidence_score": 0.0,
            "answer_type": "insufficient_data",
            "key_insights": [],
            "suggest_more_research": True,
            "follow_up_questions": [
                "Would you like me to search with different keywords?",
                "Can you provide more context about what you're looking for?",
                "Should I focus on a specific aspect of your question?"
            ],
            "timestamp": datetime.utcnow().isoformat()
        }
    
    async def _improve_answer(self, tool_call) -> Dict[str, Any]:
        """Improve an answer with additional context"""
        original_answer = tool_call.parameters.get("original_answer", "")
        additional_context = tool_call.parameters.get("additional_context", "")
        improvement_focus = tool_call.parameters.get("improvement_focus", [])
        
        prompt = f"""Improve this answer with additional context.

Original Answer:
{original_answer}

Additional Context:
{additional_context}

Focus on improving:
{json.dumps(improvement_focus)}

Provide an improved, more comprehensive answer that incorporates the new information.

Respond with JSON:
- improved_answer: The enhanced answer
- improvements_made: List of specific improvements
- confidence_boost: Estimated confidence increase (0.0 to 0.3)

Respond only with valid JSON."""

        try:
            llm = LLMClient(self.llm_config, self.session_pool)
            response = await llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=1000
            )
            
            return json.loads(response)
            
        except Exception as e:
            self.logger.error(f"Failed to improve answer: {e}")
            return {
                "improved_answer": original_answer,
                "improvements_made": [],
                "confidence_boost": 0.0
            }
    
    def _error_response(self, query: str, job_id: str, error: str) -> Dict[str, Any]:
        """Response when error occurs"""
        return {
            "job_id": job_id,
            "query": query,
            "answer": f"I encountered an error while generating the answer. Please try again or rephrase your query.",
            "citations": [],
            "confidence_score": 0.0,
            "answer_type": "error",
            "key_insights": [],
            "suggest_more_research": True,
            "follow_up_questions": [],
            "error": error,
            "timestamp": datetime.utcnow().isoformat()
        }

async def main():
    """Standalone testing"""
    config = Config()
    state_manager = StateManager()
    await state_manager.start()
    
    answerer = AnswererAgent(config, state_manager)
    
    # Mock summarized data
    mock_data = {
        "document_summaries": [{
            "url": "https://example.com/quantum",
            "title": "Quantum Computing Breakthrough",
            "summary": "Researchers have achieved a major breakthrough...",
            "key_points": ["New qubit design", "Improved error rates"],
            "relevance_score": 0.9
        }],
        "consolidated_summary": {
            "synthesis": "Recent advances show promising developments...",
            "main_findings": ["Quantum supremacy achieved", "Commercial applications emerging"]
        }
    }
    
    result = await answerer.process(
        "What are the latest developments in quantum computing?",
        "test-job-123",
        mock_data
    )
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    asyncio.run(main())