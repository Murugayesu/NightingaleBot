import asyncio
import json

from nursing_bot.api.chat import load_session_history
from nursing_bot.api.deps import get_pipeline_dep
from nursing_bot.db.connection import get_session
from nursing_bot.services.embedding_model import get_active_llm_provider


async def main():
    async with get_session() as session:
        llm = await get_active_llm_provider(session)
        pipeline = get_pipeline_dep(llm)
        history = await load_session_history(session, "2e6b017b-6ff9-42af-8be1-dee729a60d55")
        query = "what is pharmascan"
        standalone = await pipeline.condenser.condense(query, history)
        response = await pipeline.aquery(
            session,
            query,
            history=history,
            filters={"document_id": "81a2df30-378e-4658-a403-36f043cbd3cc"},
        )
        print(json.dumps({
            "history_messages": len(history),
            "standalone_query": standalone,
            "gate_passed": response.gate_passed,
            "is_grounded": response.is_grounded,
            "top_score": response.top_score,
            "citation_count": len(response.citations),
            "answer": response.answer[:1800],
        }, indent=2))


asyncio.run(main())
