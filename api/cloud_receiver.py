"""Local receiver for cloud protocol integration tests; this is not a remote cloud."""
import hashlib
import json
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI,File,Form,Header,HTTPException,UploadFile
from services.store import Store

def create_receiver(data_dir=None):
    root=Path(data_dir or os.getenv('AOI_RECEIVER_DATA',Path(__file__).resolve().parents[1]/'data/local_receiver'))
    lock=threading.Lock()
    @asynccontextmanager
    async def lifespan(app):
        app.state.store=Store(root)
        yield
    app=FastAPI(title='AOI Local Cloud Protocol Receiver',lifespan=lifespan)
    @app.post('/events')
    def receive(event:str=Form(...),files:list[UploadFile]=File(default=[]),idempotency_key:str|None=Header(default=None)):
        try:
            payload=json.loads(event)
            event_id=payload['event_id']
            if not event_id.startswith('sync_') or not event_id[5:].isalnum() or idempotency_key!=event_id:
                raise HTTPException(400,'Invalid event or idempotency ID')
            with lock:
                store=app.state.store
                try:
                    previous=store.get('cloud_event',event_id)
                    if previous['payload']!=payload:
                        raise HTTPException(409,'Idempotency ID reused with different payload')
                    return {'event_id':event_id,'status':'duplicate'}
                except KeyError:
                    pass
                folder=root/'events'/event_id
                folder.mkdir(parents=True,exist_ok=True)
                received=[]
                for upload in files:
                    filename=Path(upload.filename or 'image').name
                    path=folder/filename
                    h=hashlib.sha256()
                    total=0
                    with path.open('wb') as output:
                        while block:=upload.file.read(1024*1024):
                            total+=len(block)
                            if total>50*1024*1024:
                                raise HTTPException(413,'Image exceeds 50 MiB')
                            output.write(block)
                            h.update(block)
                    received.append({'filename':filename,'sha256':h.hexdigest()})
                store.put('cloud_event',{'id':event_id,'payload':payload,'files':received})
                return {'event_id':event_id,'status':'accepted'}
        except (KeyError,ValueError) as exc:
            raise HTTPException(400,'Invalid cloud event') from exc
        finally:
            for upload in files:
                upload.file.close()
    return app

app=create_receiver()

