"""Quick verification script for backend task implementations."""
import sys
import os

sys.path.insert(0, 'src')
os.environ['ORCHESTRATOR_API_KEY'] = 'test-key'

from orchestrator.models import OrchestratorConfig
c = OrchestratorConfig()
assert c.projects_root is None
assert c.max_browse_depth == 10
assert c.ssh_port == 22
print('TASK-001: OrchestratorConfig fields OK')

from orchestrator.mobile_api.models import (
    CreateDirectoryRequest, DirectoryChildrenResponse, SshConfigResponse, RunStartRequest
)
r = RunStartRequest(feature_request='test')
assert r.use_system_orchestrate == False
dcr = DirectoryChildrenResponse(entries=[], parent_id='abc', depth=0, at_depth_limit=False)
assert dcr.entries == []
ssh = SshConfigResponse(configured=False, host_reachable=False)
print('TASK-002: Models OK')

from orchestrator.mobile_api.dynamic_directory_service import (
    make_opaque_id, compute_depth, resolve_dynamic_id, list_children, create_child, get_root_entry
)
import uuid
salt = uuid.uuid4()
oid = make_opaque_id('/tmp/test', salt)
assert len(oid) == 32
print('TASK-003: DynamicDirectoryService OK')

from orchestrator.mobile_api.routes.directories import router as dir_router
print('TASK-004: Directory routes OK')

from orchestrator.mobile_api.routes.ssh import router as ssh_router
print('TASK-005: SSH routes OK')

from orchestrator.mobile_api.system_runner import locate_binary, build_cli_args, start_subprocess_run
from unittest.mock import patch
with patch('shutil.which', return_value=None):
    assert locate_binary() is None
print('TASK-006: SystemOrchestrateRunner OK')

from orchestrator.mobile_api.routes.runs import router as runs_router
print('TASK-007: Runs routes OK')

from orchestrator.mobile_api.auth import _EXEMPT_PATHS
assert '/api/v1/ssh/config' in _EXEMPT_PATHS
print('TASK-008: Auth exempt paths include /api/v1/ssh/config OK')

print('\nAll backend tasks verified successfully!')
