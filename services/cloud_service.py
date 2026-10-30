"""Durable, at-least-once cloud outbox with idempotency and ordered review events."""
import json
import os
import threading
import time
from contextlib import ExitStack
from pathlib import Path
import httpx
from services.asset_service import sha256
from services.store import identifier, now

class CloudSyncService:
    def __init__(self, store, config, lock, transport=None):
        self.store, self.config, self.lock = store, config, lock
        self.transport = transport
        self.stop = threading.Event()
        self.thread = None
        for event in store.all('sync_event'):
            if event['status'] == 'syncing':
                event.update(status='pending', next_attempt_at=0)
                store.put('sync_event',event)
        if config.get('enabled') and config.get('background', True):
            self.thread = threading.Thread(target=self._loop, daemon=True, name='cloud-sync')
            self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=40)

    def enqueue(self, kind, payload, attachments, depends_on=None):
        event_id = identifier('sync')
        event = {'id':event_id,'event_id':event_id,'kind':kind,'payload':payload,
            'attachments':attachments,'depends_on':depends_on,'status':'pending' if self.config.get('enabled') else 'pending_configuration',
            'attempts':0,'cycle_attempts':0,'next_attempt_at':0,'created_at':now(),'error':None}
        return event

    def _send(self, event):
        headers = {'Idempotency-Key':event['id']}
        token_env = self.config.get('token_env','AOI_CLOUD_TOKEN')
        token = os.getenv(token_env)
        if token:
            headers['Authorization']='Bearer '+token
        with ExitStack() as stack:
            files=[]
            for attachment in event['attachments']:
                path=Path(attachment['path'])
                if not path.is_file() or sha256(path)!=attachment['sha256']:
                    raise ValueError('Cloud attachment missing or changed')
                stream=stack.enter_context(path.open('rb'))
                files.append(('files',(attachment['asset_id']+path.suffix,stream,'application/octet-stream')))
            with httpx.Client(timeout=self.config.get('timeout_seconds',30),transport=self.transport,trust_env=self.config.get('trust_env',False)) as client:
                response=client.post(self.config['endpoint'],headers=headers,
                    data={'event':json.dumps({k:event[k] for k in ('id','event_id','kind','payload','depends_on')},ensure_ascii=False)},files=files)
                response.raise_for_status()
                ack=response.json()
                if ack.get('event_id')!=event['id'] or ack.get('status') not in ('accepted','duplicate'):
                    raise ValueError('Cloud acknowledgement does not match event')
                return ack

    def sync_one(self,event_id,force=False):
        with self.lock:
            event=self.store.get('sync_event',event_id)
            if event['status'] in ('synced','syncing'):
                return event
            if not self.config.get('enabled') or not self.config.get('endpoint'):
                event.update(status='pending_configuration',error='Cloud endpoint is not configured')
                return self.store.put('sync_event',event)
            if event['depends_on'] and self.store.get('sync_event',event['depends_on'])['status']!='synced':
                return event
            if not force and (event['status']=='failed' or event['next_attempt_at']>time.time()):
                return event
            if force:
                event['cycle_attempts']=0
            event.update(status='syncing',attempts=event['attempts']+1,cycle_attempts=event['cycle_attempts']+1)
            self.store.put('sync_event',event)
        try:
            ack=self._send(event)
            event.update(status='synced',synced_at=now(),error=None,ack=ack)
        except Exception as exc:
            exhausted=event['cycle_attempts']>=self.config.get('max_attempts',8)
            event.update(status='failed' if exhausted else 'retry_wait',error=str(exc),
                next_attempt_at=time.time()+min(300,2**min(event['cycle_attempts'],8)))
        with self.lock:
            return self.store.put('sync_event',event)

    def _loop(self):
        while not self.stop.is_set():
            for event in reversed(self.store.all('sync_event')):
                if self.stop.is_set():
                    break
                if event['status'] not in ('synced','failed','syncing'):
                    self.sync_one(event['id'])
            self.stop.wait(5)
