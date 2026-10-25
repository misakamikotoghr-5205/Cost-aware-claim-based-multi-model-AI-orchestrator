"""The external skill uses one explicit native runtime and bounded capabilities."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import zipfile

import pytest

ROOT=Path(__file__).resolve().parents[1]


def load(path):
    spec=importlib.util.spec_from_file_location('skill_'+hashlib.sha256(str(path).encode()).hexdigest()[:12],path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


cli=load(ROOT/'skills/misaka-agent/scripts/misaka.py')
bridge=load(ROOT/'skills/misaka-agent/scripts/runtime_bridge.py')


def test_connection_required_does_not_guess_a_path_or_launch(monkeypatch,capsys):
    monkeypatch.setattr(cli.subprocess,'run',lambda *a,**k:pytest.fail('No runtime is configured'))
    assert cli.main(['status'])==2
    assert json.loads(capsys.readouterr().out)['error']=='configuration_required'


def test_named_setup_persists_only_explicit_nonsecret_paths(tmp_path):
    root=tmp_path/'native';(root/'hermes_cli').mkdir(parents=True);(root/'hermes_cli/main.py').write_text('')
    launcher=tmp_path/'hermes';launcher.write_text('')
    home=tmp_path/'home';home.mkdir()
    args=argparse.Namespace(config=str(tmp_path/'connections.json'),connection='local',launcher=str(launcher),root=str(root),home=str(home))
    assert cli.configure(args)['contains_credentials'] is False
    result=json.loads(Path(args.config).read_text())
    assert set(result['connections']['local'])=={'launcher','root','home'}
    selected=argparse.Namespace(config=args.config,connection='local',launcher=None,root=None,home=None)
    cli.select_connection(selected)
    assert selected.home==str(home)


@pytest.mark.parametrize('data',[None,[],{'schema':True,'connections':{}},{'schema':1,'connections':None},
    {'schema':1,'connections':{'local':None}},{'schema':1,'connections':{'local':[]}},
    {'schema':1,'connections':{'local':{'launcher':None,'root':'/x','home':'/x'}}}])
def test_malformed_connection_config_is_structured_and_never_launches(data,tmp_path,monkeypatch,capsys):
    path=tmp_path/'connections.json';path.write_text(json.dumps(data))
    monkeypatch.setattr(cli.subprocess,'run',lambda *a,**k:pytest.fail('Invalid configuration cannot launch anything'))
    assert cli.main(['--config',str(path),'--connection','local','status'])==2
    assert json.loads(capsys.readouterr().out)['ok'] is False


def test_launcher_identity_must_name_the_explicit_root_even_with_foreign_text(tmp_path):
    root=tmp_path/'root'
    cli.validate_runtime_command(['/python','-I','-c',f'import sys; sys.path.insert(0, {str(root)!r})'],root)
    with pytest.raises(ValueError):
        cli.validate_runtime_command(['/python','-I','-c',f'import sys; sys.path.insert(0, {str(tmp_path/"foreign")!r}); note={str(root)!r}'],root)


@pytest.mark.parametrize('path',['https://127.0.0.1:1234','http://example.com:1234','http://127.0.0.2:1234',
    'http://user@127.0.0.1:1234','http://127.0.0.1:1234/path','http://127.0.0.1:1234?key=x'])
def test_ticket_origin_is_fixed_to_discovered_numeric_loopback(path,tmp_path):
    with pytest.raises(ValueError):bridge.validate_endpoint({'runtime_protocol':1,'profile_id':str(tmp_path),'instance_id':'one','api_origin':path},tmp_path)


def test_ticket_profile_must_match_the_explicit_home(tmp_path):
    endpoint={'runtime_protocol':1,'profile_id':str(tmp_path/'other'),'instance_id':'one','api_origin':'http://127.0.0.1:1234'}
    with pytest.raises(ValueError):bridge.validate_endpoint(endpoint,tmp_path)


@pytest.mark.parametrize('body',[{'context':{}},{'backend':{}},{'owner_id':'other'},{'workdir':'/tmp'},{'command':'shell'}])
def test_task_request_cannot_supply_authority_or_a_shell_shortcut(body):
    with pytest.raises(ValueError):cli.operation('task-submit',body,None)


def test_credentials_duplicate_keys_nonfinite_and_oversize_input_are_rejected(tmp_path):
    for raw in [b'{"token":"secret"}',b'{"a":1,"a":2}',b'{"n":NaN}',b' '*65537]:
        path=tmp_path/'request.json';path.write_bytes(raw)
        with pytest.raises(ValueError):cli.input_body(str(path))


def test_submit_cancel_only_use_the_shared_agent_gate():
    body={'task_id':'stable-id','task':'Review','target':'node-a','workspace_alias':'documents','tools':[],'max_actions':1}
    assert cli.operation('task-submit',body,None)==('POST','/agent/fleet',{**body,'action':'submit'})
    assert cli.operation('task-cancel',{},'stable-id')==('POST','/agent/fleet',{'action':'cancel','task_id':'stable-id'})


def test_timeout_preserves_same_task_identity_and_does_not_retry(monkeypatch,tmp_path,capsys):
    path=tmp_path/'task.json';path.write_text(json.dumps({'task_id':'stable-id','target':'node','task':'Review','workspace_alias':'docs'}))
    monkeypatch.setattr(cli,'select_connection',lambda args:None)
    monkeypatch.setattr(cli,'connect',lambda args:object())
    calls=[]
    def interrupted(*args):calls.append(args);raise subprocess.TimeoutExpired('runtime-helper',25)
    monkeypatch.setattr(cli,'call',interrupted)
    assert cli.main(['task-submit','--request',str(path)])==2
    result=json.loads(capsys.readouterr().out)
    assert result['error']=='outcome_unknown' and result['task_id']=='stable-id' and len(calls)==1


def test_skill_zip_is_an_explicit_self_contained_whitelist(tmp_path):
    builder=load(ROOT/'scripts/build_agent_skill.py');target=tmp_path/'skill.zip';builder.build(target)
    with zipfile.ZipFile(target) as archive:
        assert set(archive.namelist())=={'misaka-agent/'+name for name in (*builder.FILES,'manifest.json')}
        manifest=json.loads(archive.read('misaka-agent/manifest.json'))
        for name,digest in manifest['files'].items():assert hashlib.sha256(archive.read('misaka-agent/'+name)).hexdigest()==digest
        assert not manifest['installs_optional_packages']
    second=tmp_path/'second.zip';builder.build(second)
    assert second.read_bytes()==target.read_bytes()


def test_runtime_bridge_operation_allowlist_precedes_ticket_minting(tmp_path,monkeypatch):
    monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    endpoint={'runtime_protocol':1,'profile_id':str(tmp_path),'instance_id':'one','api_origin':'http://127.0.0.1:1234'}
    with pytest.raises(ValueError):bridge.request({'endpoint':endpoint,'home':str(tmp_path),'method':'GET','path':'/../../config'})


@pytest.mark.parametrize('installed,enabled',[(False,False),(True,False)])
def test_optional_entry_cannot_run_without_both_installation_and_native_activation(monkeypatch,capsys,installed,enabled):
    monkeypatch.setattr(cli,'select_connection',lambda args:None)
    monkeypatch.setattr(cli,'connect',lambda args:object())
    calls=[]
    def inventory(*args,**kwargs):
        calls.append((args,kwargs))
        assert args[1:]==('GET','/extensions',{}) and not kwargs
        return {'ok':True,'result':{'extensions':[{'id':'local-judge','installed':installed,'enabled':enabled}]}}
    monkeypatch.setattr(cli,'call',inventory)
    assert cli.main(['judge-test'])==3
    assert json.loads(capsys.readouterr().out)['error']=='extension_unavailable'
    assert len(calls)==1


def test_intent_operations_accept_metadata_only_and_keep_revision_fence():
    assert cli.optional_operation('intent-create',{'context':{'kind':'document'},'keywords':'润色'},None)[1]=='/sessions'
    assert cli.optional_operation('intent-refine',{'revision':1,'keywords':'简洁'},'session-one')[1]=='/sessions/session-one/refine'
    assert cli.optional_operation('intent-commit',{'revision':2,'action_id':'polish'},'session-one')[1]=='/sessions/session-one/commit'
    with pytest.raises(ValueError):cli.optional_operation('intent-create',{'screenshot':'binary'},None)
    with pytest.raises(ValueError):cli.optional_operation('intent-refine',{'revision':1},'../../escape')
    with pytest.raises(ValueError):cli.optional_operation('judge-test',{'base_url':'https://remote.example'},None)


def test_transport_and_lease_credentials_never_leave_the_runtime_bridge():
    raw={'task':{'task_id':'stable','lease_token':'private-node-lease','result':{'verified':True,
        'usage':{'prompt_tokens':128},'headers':{'Authorization':'Bearer private'}}},'ticket':'private-ticket'}
    public=bridge.public_result(raw)
    assert 'private' not in json.dumps(public) and public['task']['task_id']=='stable'
    assert public['task']['result']['verified'] and public['task']['result']['usage']['prompt_tokens']==128
    assert raw['task']['lease_token']=='private-node-lease'  # Does not mutate the node's record.
