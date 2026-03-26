import yaml

with open('config/default.yaml') as f:
    cfg = yaml.safe_load(f)

rc = cfg.get('research_cache', {})
print('research_cache keys:', list(rc.keys()))
print('enabled:', rc.get('enabled'))
print('global_dir:', rc.get('global_dir'))
print('local_dir:', rc.get('local_dir'))
print('server_path:', repr(rc.get('server_path')))
print('base_ttl_days:', rc.get('base_ttl_days'))
print('volatile_ttl_days:', rc.get('volatile_ttl_days'))
print('max_entries:', rc.get('max_entries'))
print('max_inject_bytes:', rc.get('max_inject_bytes'))
print('inject_into_phases:', rc.get('inject_into_phases'))
print('auto_extract:', rc.get('auto_extract'))
print('cleanup_mcp_config:', rc.get('cleanup_mcp_config'))
print()
print('Key count:', len(rc))

required = ['enabled','global_dir','local_dir','server_path','base_ttl_days',
            'volatile_ttl_days','max_entries','max_inject_bytes',
            'inject_into_phases','auto_extract','cleanup_mcp_config']
missing = [k for k in required if k not in rc]
print('Missing keys:', missing if missing else 'NONE')

keys = list(cfg.keys())
tr_idx = keys.index('test_runner')
rc_idx = keys.index('research_cache')
print(f'test_runner index: {tr_idx}, research_cache index: {rc_idx}')
print('Immediately after test_runner:', rc_idx == tr_idx + 1)

expected = {
    'enabled': True,
    'global_dir': '~/.orchestrator/research',
    'local_dir': '.knowledge/research',
    'server_path': '',
    'base_ttl_days': 90,
    'volatile_ttl_days': 7,
    'max_entries': 500,
    'max_inject_bytes': 2048,
    'inject_into_phases': ['pm', 'architect', 'principal_engineer'],
    'auto_extract': True,
    'cleanup_mcp_config': True,
}
mismatches = [(k, expected[k], rc.get(k)) for k in expected if rc.get(k) != expected[k]]
print('Value mismatches:', mismatches if mismatches else 'NONE')
