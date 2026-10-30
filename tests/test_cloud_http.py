import io
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from PIL import Image
import uvicorn
from api.cloud_receiver import create_receiver
from services.asset_service import AssetService
from services.cloud_service import CloudSyncService
from services.store import Store

class CloudHTTPTests(unittest.TestCase):
    def test_real_http_image_delivery_without_external_proxy(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            sock=socket.socket()
            sock.bind(('127.0.0.1',0))
            sock.listen()
            port=sock.getsockname()[1]
            server=uvicorn.Server(uvicorn.Config(create_receiver(root/'receiver'),log_level='error',lifespan='on'))
            thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True)
            thread.start()
            cloud=None
            try:
                for _ in range(100):
                    if server.started:
                        break
                    time.sleep(0.05)
                self.assertTrue(server.started)
                store=Store(root/'sender')
                image=io.BytesIO()
                Image.new('RGB',(8,8),(1,2,3)).save(image,format='PNG')
                image.seek(0)
                asset=AssetService(store).register(image,'fixture.png')
                cloud=CloudSyncService(store,{'enabled':True,'background':False,'endpoint':f'http://127.0.0.1:{port}/events'},threading.RLock())
                event=cloud.enqueue('inspection',{'fixture':True,'mode':'machine_test'},
                    [{k:asset[k] for k in ('asset_id','path','sha256')}])
                store.put('sync_event',event)
                result=cloud.sync_one(event['id'])
                self.assertEqual(result['status'],'synced',result)
                received=Store(root/'receiver').get('cloud_event',event['id'])
                self.assertEqual(received['files'][0]['sha256'],asset['sha256'])
                self.assertEqual(received['payload']['payload']['mode'],'machine_test')
            finally:
                if cloud:
                    cloud.close()
                server.should_exit=True
                thread.join(timeout=5)
                sock.close()

