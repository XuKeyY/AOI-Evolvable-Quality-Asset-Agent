"""MCP tools proxy the business API; the API alone owns algorithm jobs."""
import os
import time
import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

API_URL = os.getenv('AOI_API_URL', 'http://127.0.0.1:8100').rstrip('/')
mcp = FastMCP('AOI Task Tools', host='0.0.0.0', port=8101, json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
        allowed_hosts=['localhost:*', '127.0.0.1:*', 'host.docker.internal:*'],
        allowed_origins=['http://localhost:*', 'http://127.0.0.1:*', 'http://host.docker.internal:*']))

def api(method, path, body=None):
    with httpx.Client(timeout=60,trust_env=False) as client:
        response = client.request(method, API_URL + path, json=body)
        if response.is_error:
            try:
                detail = response.json().get('detail', response.text)
            except ValueError:
                detail = response.text
            raise ValueError(f'Business API {response.status_code}: {detail}')
        return response.json()

def wait_for_job(job: dict, timeout_seconds: int = 180) -> dict:
    """Resolve an asynchronous business job inside one MCP call for stable agent UX."""
    deadline = time.monotonic() + timeout_seconds
    current = job
    while current.get('status') in ('queued', 'running') and time.monotonic() < deadline:
        time.sleep(1)
        current = api('GET', '/jobs/' + current['id'])
    if current.get('status') in ('queued', 'running'):
        return {**current, 'wait_timed_out': True}
    return current

@mcp.tool()
def create_quality_task(name: str) -> dict:
    """Create a product/process-specific task; files are uploaded via business UI."""
    return api('POST', '/tasks', {'name': name})

@mcp.tool()
def get_asset(asset_id: str) -> dict:
    """Get a registered image, table or document and its durable asset ID and hash."""
    return api('GET', '/assets/' + asset_id)

@mcp.tool()
def get_quality_task(task_id: str) -> dict:
    """Get support image IDs and current dataset revision."""
    return api('GET', '/tasks/' + task_id)

@mcp.tool()
def attach_support_assets(task_id: str, role: str, asset_ids: list[str]) -> dict:
    """Attach normal/defect initial support or validation_normal/validation_defect images without overlap."""
    return api('POST', '/tasks/' + task_id + '/assets', {'role': role, 'asset_ids': asset_ids})

@mcp.tool()
def submit_adaptation(task_id: str, development: bool = False) -> dict:
    """Queue adaptation. Official input is exactly 100 normal + 30 defect images."""
    return api('POST', '/tasks/' + task_id + '/adaptations', {'development': development})

@mcp.tool()
def get_job(job_id: str) -> dict:
    """Get persistent job state, error, log tail, model version or inspection results."""
    return api('GET', '/jobs/' + job_id)

@mcp.tool()
def inspect_images(asset_ids: list[str], task_id: str, mode: str = 'review_test', model_version: str | None = None) -> dict:
    """Detect images with the published model and return the terminal job with final results."""
    job = api('POST', '/inspections', {'asset_ids': asset_ids, 'task_id': task_id, 'mode': mode, 'model_version': model_version})
    return wait_for_job(job)


@mcp.tool()
def review_inspection(record_id: str, label: str, operator: str, note: str = '') -> dict:
    """Append a human normal/defect/uncertain conclusion; only review_test records allow this."""
    return api('POST','/inspection-records/'+record_id+'/reviews',{'label':label,'operator':operator,'note':note})

@mcp.tool()
def correct_initial_label(task_id: str, asset_id: str, label: str, operator: str, note: str = '') -> dict:
    """Audit a correction to an original support image's label."""
    return api('POST','/tasks/'+task_id+'/label-revisions',{'asset_id':asset_id,'label':label,'operator':operator,'note':note})

@mcp.tool()
def preview_evolution_dataset(task_id: str) -> dict:
    """Get cumulative training counts, labels, excluded conflicts and feedback cutoff."""
    return api('GET','/tasks/'+task_id+'/training-preview')

@mcp.tool()
def submit_evolution(task_id: str) -> dict:
    """Queue formal evolution from original support plus cumulative confirmed feedback and immediately return the persistent job ID."""
    return api('POST','/tasks/'+task_id+'/evolutions',{})

@mcp.tool()
def validate_candidate(model_version: str) -> dict:
    """Queue candidate-versus-parent comparison on the frozen validation set and immediately return the persistent job ID."""
    return api('POST','/models/'+model_version+'/validations',{})

@mcp.tool()
def publish_model(task_id: str, model_version: str, operator: str, expected_current: str | None = None) -> dict:
    """Publish only a validated candidate; expected_current protects against stale version switches."""
    return api('POST','/tasks/'+task_id+'/publications',{'model_version':model_version,'operator':operator,'expected_current':expected_current})

@mcp.tool()
def rollback_model(task_id: str, model_version: str, operator: str, expected_current: str) -> dict:
    """Rollback to a previously released version of this task."""
    return api('POST','/tasks/'+task_id+'/rollbacks',{'model_version':model_version,'operator':operator,'expected_current':expected_current})

@mcp.tool()
def get_release_history(task_id: str) -> dict:
    """Get every immutable release and rollback audit record with an explicit count."""
    releases = api('GET','/tasks/'+task_id+'/releases')
    return {"task_id": task_id, "count": len(releases), "releases": releases}

@mcp.tool()
def get_cloud_sync_events() -> list[dict]:
    """Get actual sync status; pending_configuration never means uploaded."""
    return api('GET','/sync-events')

@mcp.tool()
def retry_cloud_sync(event_id: str) -> dict:
    """Retry failed cloud delivery using the same idempotency event ID."""
    return api('POST','/sync-events/'+event_id+'/retry',{})

@mcp.tool()
def parse_asset(asset_id: str, force: bool = False) -> dict:
    """Parse a registered PDF, Word, text, CSV, Excel or image into evidence chunks with source locations."""
    return api('POST',f'/assets/{asset_id}/parse?force={str(force).lower()}',{})

@mcp.tool()
def search_evidence(query: str, asset_ids: list[str] | None = None, limit: int = 10) -> dict:
    """Retrieve source-grounded evidence chunks; results retain asset, page, sheet, row or paragraph locations."""
    return api('POST','/evidence/search',{'query':query,'asset_ids':asset_ids,'limit':limit})

@mcp.tool()
def query_quality_graph(query: str = '', start_node_id: str | None = None, max_hops: int = 3) -> dict:
    """Traverse cross-document product, batch, process, anomaly, standard and action relationships."""
    return api('POST','/quality-graph/query',{'query':query,'start_node_id':start_node_id,'max_hops':max_hops})

@mcp.tool()
def analyze_quality_assets(asset_ids: list[str], query: str, task_id: str | None = None) -> dict:
    """Run retrieval and graph reasoning over heterogeneous assets and persist a decision trace."""
    return api('POST','/quality-analysis',{'asset_ids':asset_ids,'query':query,'task_id':task_id})

@mcp.tool()
def propose_ontology_update(asset_ids: list[str], rationale: str = '', changes: list[dict] | None = None) -> dict:
    """Extract a reviewable ontology/standard-mapping proposal from newly registered evidence."""
    return api('POST','/ontology/proposals',{'asset_ids':asset_ids,'rationale':rationale,'changes':changes or []})

@mcp.tool()
def review_ontology_update(proposal_id: str, decision: str, operator: str, note: str = '') -> dict:
    """Approve or reject a pending ontology proposal with an auditable human decision."""
    return api('POST',f'/ontology/proposals/{proposal_id}/review',{'decision':decision,'operator':operator,'note':note})

@mcp.tool()
def publish_ontology_version(proposal_id: str, operator: str) -> dict:
    """Publish an approved proposal as a new immutable ontology version."""
    return api('POST',f'/ontology/proposals/{proposal_id}/publish',{'operator':operator})

@mcp.tool()
def get_trace(trace_id: str) -> dict:
    """Get the persisted execution inputs, steps, outcome and evidence linkage for audit."""
    return api('GET',f'/traces/{trace_id}')

if __name__ == '__main__':
    mcp.run(transport='streamable-http')
