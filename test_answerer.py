import asyncio
from unittest.mock import AsyncMock

from answerer import AnswererAgent
from config import Config

class DummyStateManager:
    def __init__(self):
        self.results = []
        self.status_updates = []

    async def add_agent_result(self, job_id, agent, result):
        self.results.append((job_id, agent, result))

    async def update_agent_status(self, agent, status, success=True):
        self.status_updates.append((agent, status, success))


class DummyToolRegistry:
    def register_tool(self, *args, **kwargs):
        pass


def test_process_appends_help_requests():
    state_manager = DummyStateManager()
    config = Config()
    agent = AnswererAgent(config, state_manager)
    agent.tool_registry = DummyToolRegistry()

    agent._generate_answer = AsyncMock(return_value={
        "answer": "test answer",
        "answer_type": "comprehensive",
        "key_insights": [],
        "follow_up_questions": []
    })
    agent._extract_citations = lambda docs, answer: [{"title": "t", "url": "u"}]
    agent._evaluate_answer_confidence = AsyncMock(return_value=0.5)
    agent.evaluate_and_request_help = AsyncMock(return_value={
        "help_needed": [{"agent": "validator", "action": "deep_validate", "reason": "low_confidence"}]
    })

    summarized_data = {"document_summaries": [{"title": "t", "url": "u", "summary": "s"}]}

    result = asyncio.run(agent.process("q", "job", summarized_data))

    assert result["help_requests"] == [{"agent": "validator", "action": "deep_validate", "reason": "low_confidence"}]
    assert state_manager.results

