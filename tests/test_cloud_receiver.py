import json
import tempfile
import unittest
from fastapi.testclient import TestClient
from api.cloud_receiver import create_receiver

class ReceiverTests(unittest.TestCase):
    def test_upload_ack_duplicate_and_conflict(self):
        with tempfile.TemporaryDirectory() as folder:
            with TestClient(create_receiver(folder)) as client:
                payload={'event_id':'sync_fixture123','kind':'inspection','payload':{'mode':'machine_test'}}
                def send(value):
                    return client.post('/events',data={'event':json.dumps(value)},
                        headers={'Idempotency-Key':'sync_fixture123'},files={'files':('asset.png',b'fixture-image','application/octet-stream')})
                first=send(payload)
                self.assertEqual(first.json(),{'event_id':'sync_fixture123','status':'accepted'})
                self.assertEqual(send(payload).json()['status'],'duplicate')
                payload['payload']['mode']='review_test'
                self.assertEqual(send(payload).status_code,409)

    def test_mismatched_idempotency_id_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with TestClient(create_receiver(folder)) as client:
                response=client.post('/events',data={'event':json.dumps({'event_id':'sync_example'})},
                    headers={'Idempotency-Key':'wrong'})
                self.assertEqual(response.status_code,400)

