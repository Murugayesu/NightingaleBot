import asyncio
import json

from nursing_bot.db.connection import get_session
from nursing_bot.retrieval.hybrid import HybridRetriever
from nursing_bot.retrieval.synonyms import DomainSynonymExpander


async def main():
    retriever = HybridRetriever(None)
    filters = {"document_id": "81a2df30-378e-4658-a403-36f043cbd3cc"}
    async with get_session() as session:
        strict = await retriever._run_sparse_search(
            session, DomainSynonymExpander.expand_query("team mates of pharmascan"), filters, 10
        )
        relaxed = await retriever._run_relaxed_sparse_search(
            session, "team collaborators pharmascan", filters, 10
        )
        unrelated = await retriever._run_relaxed_sparse_search(
            session, "pharmascan quantum bananas", filters, 10
        )
        assert strict
        assert relaxed
        assert not unrelated
        print(json.dumps({
            "team_query_matches": len(strict),
            "relaxed_matches": len(relaxed),
            "unrelated_matches": len(unrelated),
            "first_match": strict[0]["content"][:300],
        }, indent=2))


asyncio.run(main())
