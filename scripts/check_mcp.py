"""Check MCP initialization, tool discovery and API error propagation."""
import asyncio

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


URL = "http://127.0.0.1:8101/mcp"
MAX_ATTEMPTS = 3


def local_http_client(*, headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
    """Do not send localhost MCP traffic through HTTP(S)_PROXY."""
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        auth=auth,
        trust_env=False,
    )


async def check_once() -> list[str]:
    async with streamablehttp_client(
        URL, httpx_client_factory=local_http_client
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            required = {
                "submit_adaptation", "get_job", "inspect_images", "review_inspection",
                "submit_evolution", "validate_candidate", "publish_model", "rollback_model",
                "parse_asset", "search_evidence", "query_quality_graph",
                "analyze_quality_assets", "propose_ontology_update",
                "review_ontology_update", "publish_ontology_version", "get_trace",
            }
            assert required <= names, f"Missing MCP tools: {sorted(required - names)}"
            missing = await session.call_tool(
                "get_asset", {"asset_id": "connectivity_probe_missing_asset"}
            )
            assert missing.isError, "Missing asset must produce an explicit tool error"
            assert any(
                "Business API 404" in getattr(item, "text", "")
                for item in missing.content
            ), "MCP request did not reach the business API"
            return sorted(names)


async def main() -> None:
    last_error: BaseException | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            names = await check_once()
            print("MCP initialization, tool discovery and API proxy: passed")
            print(names)
            return
        except BaseException as exc:
            last_error = exc
            if attempt == MAX_ATTEMPTS:
                raise
            print(f"MCP check attempt {attempt}/{MAX_ATTEMPTS} failed; retrying...")
            await asyncio.sleep(attempt)
    raise RuntimeError("MCP check failed") from last_error


if __name__ == "__main__":
    asyncio.run(main())
