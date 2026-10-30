import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import FastAPI,File,HTTPException,UploadFile
from fastapi.responses import FileResponse,JSONResponse
from pydantic import BaseModel,Field
from services.aoi_service import AOIService
ROOT=Path(__file__).resolve().parents[1]

class TaskRequest(BaseModel):
    name:str=Field(min_length=1,max_length=200)
class AttachRequest(BaseModel):
    role:Literal['normal','defect','validation_normal','validation_defect']
    asset_ids:list[str]=Field(min_length=1)
class AdaptRequest(BaseModel):
    development:bool=False
class InspectRequest(BaseModel):
    asset_ids:list[str]=Field(min_length=1)
    task_id:str|None=None
    model_version:str|None=None
    mode:Literal['review_test','machine_test']='review_test'
class ReviewRequest(BaseModel):
    label:Literal['normal','defect','uncertain']
    operator:str=Field(min_length=1,max_length=100)
    note:str=Field(default='',max_length=2000)
class LabelRequest(ReviewRequest):
    asset_id:str
    label:Literal['normal','defect']
class ReleaseRequest(BaseModel):
    model_version:str
    operator:str=Field(min_length=1,max_length=100)
    expected_current:str|None=None
class EvidenceRequest(BaseModel):
    query:str=Field(min_length=1,max_length=500)
    asset_ids:list[str]|None=None
    limit:int=Field(default=10,ge=1,le=100)
class GraphQueryRequest(BaseModel):
    query:str=''
    start_node_id:str|None=None
    max_hops:int=Field(default=3,ge=1,le=5)
class QualityAnalysisRequest(BaseModel):
    asset_ids:list[str]=Field(min_length=1)
    query:str=Field(min_length=1,max_length=500)
    task_id:str|None=None
class OntologyProposalRequest(BaseModel):
    asset_ids:list[str]=Field(min_length=1)
    rationale:str=Field(default='',max_length=2000)
    changes:list[dict]=Field(default_factory=list)
class OntologyReviewRequest(BaseModel):
    decision:Literal['approve','reject']
    operator:str=Field(min_length=1,max_length=100)
    note:str=Field(default='',max_length=2000)
class OntologyPublishRequest(BaseModel):
    operator:str=Field(min_length=1,max_length=100)

def create_app(config=None,service=None):
    config=config or json.loads(Path(os.getenv('AOI_AGENT_CONFIG',ROOT/'config.json')).read_text(encoding='utf-8-sig'))
    @asynccontextmanager
    async def lifespan(app):
        app.state.aoi=service or AOIService(config)
        try:
            yield
        finally:
            app.state.aoi.close()
    app=FastAPI(title='Evolvable Quality Asset Agent API',version='0.3.2',lifespan=lifespan)
    @app.exception_handler(KeyError)
    async def missing(request,exc):
        return JSONResponse(status_code=404,content={'detail':str(exc)})
    @app.exception_handler(ValueError)
    async def invalid(request,exc):
        return JSONResponse(status_code=400,content={'detail':str(exc)})
    @app.get('/',include_in_schema=False)
    def home():
        return FileResponse(ROOT/'api/index.html')
    @app.get('/health',operation_id='health')
    def health():
        acceptance_report=Path(config.get('data_dir',ROOT/'data'))/'core_migration_validation.json'
        return {'status':'ok','version':'0.3.2','capability_version':'knowledge-flow-v2-ocr','cloud_configured':bool(config.get('cloud',{}).get('enabled') and config.get('cloud',{}).get('endpoint')),
            'algorithm_family':config.get('algorithm_family','unknown'),
            'real_algorithm_acceptance':'passed' if acceptance_report.is_file() else 'pending',
            'acceptance_report':str(acceptance_report)}
    @app.post('/assets/upload',operation_id='upload_asset')
    def upload_asset(file:UploadFile=File(...)):
        try:
            return app.state.aoi.assets.register(file.file,file.filename or '')
        finally:
            file.file.close()
    @app.get('/assets/{asset_id}',operation_id='get_asset')
    def get_asset(asset_id:str):
        return app.state.aoi.assets.resolve(asset_id)
    @app.get('/assets/{asset_id}/image',include_in_schema=False)
    def image(asset_id:str):
        return FileResponse(app.state.aoi.assets.resolve(asset_id)['path'])
    @app.get('/assets/{asset_id}/download',operation_id='download_asset')
    def download_asset(asset_id:str):
        asset=app.state.aoi.assets.resolve(asset_id)
        return FileResponse(asset['path'],filename=asset['original_name'],media_type=asset.get('mime_type'))
    @app.post('/assets/{asset_id}/parse',operation_id='parse_asset')
    def parse_asset(asset_id:str,force:bool=False):
        parsed=app.state.aoi.documents.parse(asset_id,force)
        graph=app.state.aoi.graph.ingest(asset_id)
        return {**parsed,'graph':{'entity_count':len(graph['entities']),'edge_count':len(graph['edges'])}}
    @app.get('/assets/{asset_id}/understanding',operation_id='get_asset_understanding')
    def understanding(asset_id:str):
        return app.state.aoi.documents.parse(asset_id)
    @app.post('/evidence/search',operation_id='search_evidence')
    def evidence(body:EvidenceRequest):
        return app.state.aoi.graph.search_evidence(body.query,body.asset_ids,body.limit)
    @app.post('/quality-graph/query',operation_id='query_quality_graph')
    def graph_query(body:GraphQueryRequest):
        return app.state.aoi.graph.query(body.query,body.start_node_id,body.max_hops)
    @app.post('/quality-analysis',operation_id='analyze_quality_assets')
    def quality_analysis(body:QualityAnalysisRequest):
        return app.state.aoi.graph.analyze_quality_assets(body.asset_ids,body.query,body.task_id)
    @app.post('/ontology/proposals',operation_id='propose_ontology_update')
    def propose(body:OntologyProposalRequest):
        return app.state.aoi.graph.propose_ontology_update(body.asset_ids,body.rationale,body.changes)
    @app.get('/ontology/proposals',operation_id='list_ontology_proposals')
    def proposals():
        return app.state.aoi.store.all('ontology_proposal')
    @app.post('/ontology/proposals/{proposal_id}/review',operation_id='review_ontology_update')
    def review_ontology(proposal_id:str,body:OntologyReviewRequest):
        return app.state.aoi.graph.review_proposal(proposal_id,body.decision,body.operator,body.note)
    @app.post('/ontology/proposals/{proposal_id}/publish',operation_id='publish_ontology_version')
    def publish_ontology(proposal_id:str,body:OntologyPublishRequest):
        return app.state.aoi.graph.publish(proposal_id,body.operator)
    @app.get('/ontology/versions',operation_id='list_ontology_versions')
    def ontology_versions():
        return app.state.aoi.store.all('ontology_version')
    @app.get('/traces',operation_id='list_traces')
    def traces():
        return app.state.aoi.traces.list()
    @app.get('/traces/{trace_id}',operation_id='get_trace')
    def trace(trace_id:str):
        return app.state.aoi.traces.get(trace_id)
    @app.get('/tasks',operation_id='list_quality_tasks')
    def tasks():
        return [app.state.aoi.get_task(t['id']) for t in app.state.aoi.store.all('task')]
    @app.post('/tasks',operation_id='create_quality_task')
    def create_task(body:TaskRequest):
        return app.state.aoi.create_task(body.name)
    @app.get('/tasks/{task_id}',operation_id='get_quality_task')
    def get_task(task_id:str):
        return app.state.aoi.get_task(task_id)
    @app.post('/tasks/{task_id}/assets',operation_id='attach_dataset_assets')
    def attach(task_id:str,body:AttachRequest):
        return app.state.aoi.attach(task_id,body.role,body.asset_ids)
    @app.post('/tasks/{task_id}/adaptations',status_code=202,operation_id='submit_initial_adaptation')
    def adapt(task_id:str,body:AdaptRequest):
        return app.state.aoi.submit_initial_adaptation(task_id,body.development)
    @app.post('/tasks/{task_id}/evolutions',status_code=202,operation_id='submit_evolution')
    def evolve(task_id:str):
        return app.state.aoi.submit_evolution(task_id)
    @app.get('/tasks/{task_id}/training-preview',operation_id='preview_evolution_dataset')
    def preview(task_id:str):
        result=app.state.aoi.training_snapshot(task_id)
        return {**result,'items':[{k:a[k] for k in ('asset_id','label','source','label_sequence')} for a in result['items']]}
    @app.get('/tasks/{task_id}/feedback',operation_id='get_feedback_dataset')
    def feedback(task_id:str):
        app.state.aoi.get_task(task_id)
        reviews=[r for r in app.state.aoi.store.all('review') if r['task_id']==task_id]
        snapshot=app.state.aoi.training_snapshot(task_id)
        return {'history':reviews,'effective_samples':[{k:a[k] for k in ('asset_id','label','label_sequence')} for a in snapshot['items'] if a['source']=='feedback'],
            'excluded':snapshot['excluded'],'feedback_cutoff':snapshot['feedback_cutoff']}
    @app.post('/tasks/{task_id}/label-revisions',operation_id='correct_initial_label')
    def correct(task_id:str,body:LabelRequest):
        return app.state.aoi.revise_initial_label(task_id,body.asset_id,body.label,body.operator,body.note)
    @app.get('/tasks/{task_id}/label-revisions',operation_id='get_label_history')
    def label_history(task_id:str):
        app.state.aoi.get_task(task_id)
        return [r for r in app.state.aoi.store.all('label_revision') if r['task_id']==task_id]
    @app.get('/jobs',operation_id='list_jobs')
    def jobs():
        return app.state.aoi.store.all('job')
    @app.get('/jobs/{job_id}',operation_id='get_job')
    def job(job_id:str):
        return app.state.aoi.get_status(job_id)
    @app.get('/models',operation_id='list_quality_models')
    def models():
        return app.state.aoi.store.all('model')
    @app.post('/models/{model_version}/validations',status_code=202,operation_id='validate_candidate')
    def validate(model_version:str):
        return app.state.aoi.submit_validation(model_version)
    @app.post('/tasks/{task_id}/publications',operation_id='publish_model')
    def publish(task_id:str,body:ReleaseRequest):
        return app.state.aoi.versions.publish(task_id,body.model_version,body.operator,body.expected_current)
    @app.post('/tasks/{task_id}/rollbacks',operation_id='rollback_model')
    def rollback(task_id:str,body:ReleaseRequest):
        return app.state.aoi.versions.rollback(task_id,body.model_version,body.operator,body.expected_current)
    @app.get('/tasks/{task_id}/releases',operation_id='get_release_history')
    def releases(task_id:str):
        app.state.aoi.get_task(task_id)
        return [r for r in app.state.aoi.store.all('release') if r['task_id']==task_id]
    @app.post('/inspections',status_code=202,operation_id='inspect_images')
    def inspect(body:InspectRequest):
        return app.state.aoi.inspect_images(body.asset_ids,body.model_version,body.mode,body.task_id)
    @app.get('/inspections/{inspection_id}',operation_id='get_inspection')
    def get_inspection(inspection_id:str):
        result=app.state.aoi.get_status(inspection_id)
        if result['kind']!='inspect':
            raise HTTPException(404,'Not an inspection')
        return result
    @app.get('/inspection-records',operation_id='list_inspection_records')
    def records(task_id:str|None=None):
        return [r for r in app.state.aoi.store.all('inspection_record') if task_id is None or r['task_id']==task_id]
    @app.post('/inspection-records/{record_id}/reviews',operation_id='review_inspection')
    def review(record_id:str,body:ReviewRequest):
        return app.state.aoi.review(record_id,body.label,body.operator,body.note)
    @app.get('/inspection-records/{record_id}/reviews',operation_id='get_review_history')
    def review_history(record_id:str):
        app.state.aoi.store.get('inspection_record',record_id)
        return [r for r in app.state.aoi.store.all('review') if r['record_id']==record_id]
    @app.get('/sync-events',operation_id='list_cloud_sync_events')
    def sync_events():
        return app.state.aoi.store.all('sync_event')
    @app.post('/sync-events/{event_id}/retry',operation_id='retry_cloud_sync')
    def retry(event_id:str):
        return app.state.aoi.cloud.sync_one(event_id,True)
    @app.get('/jobs/{job_id}/predictions.csv',include_in_schema=False)
    def csv(job_id:str):
        result=app.state.aoi.store.get('job',job_id)
        path=Path(result['output_dir'])/'predictions.csv'
        if result['status']!='succeeded' or not path.is_file():
            raise HTTPException(404,'Predictions not available')
        return FileResponse(path,filename='predictions.csv')
    return app

app=create_app()

