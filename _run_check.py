import sys
sys.path.insert(0, 'src')
import os
os.environ['ORCHESTRATOR_API_KEY'] = 'testkey123'
from orchestrator.mobile_api.app import create_mobile_app
from pathlib import Path
import tempfile
ws = Path(tempfile.mkdtemp())
(ws/'artifacts').mkdir()
(ws/'logs').mkdir()
app = create_mobile_app(ws)
print('APP CREATED OK')
print(type(app))
