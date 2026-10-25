"""Quota telemetry trust boundaries: no synthetic account entitlement."""
from contextvars import ContextVar
import json
from pathlib import Path
import threading
import time

import pytest
from src.quota_dashboard import QuotaDashboard, account, freshen, parse_response, official_origin, fetch_native


def test_official_responses_keep_units_and_model_scopes():
    codex = parse_response('openai-codex', {'rate_limit': {'primary_window': {'used_percent': 12.5, 'reset_at': 500}},
        'additional_rate_limits': [{'limit_name':'code-review','rate_limit':{'primary_window':{'used_percent':100,'reset_at':600}}}]},observed_at=100)
    assert [(w['used'],w['remaining'],w['unit'],w['scope']) for w in codex['windows']] == [(12.5,87.5,'percent','account'),(100,0,'percent','model')]
    balance = parse_response('openrouter',{'data':{'total_credits':20,'total_usage':4}},observed_at=100)
    assert balance['windows'][0]['remaining']==16 and balance['windows'][0]['unit']=='USD'
    missing = parse_response('openrouter',{'data':{'total_usage':4}},observed_at=100)
    assert missing['state']=='unknown' and missing['windows']==[]
    deepseek = parse_response('deepseek',{'is_available':True,'balance_infos':[{'currency':'CNY','total_balance':'12.50'}]},observed_at=100)
    assert deepseek['windows'][0]['remaining']==12.5 and deepseek['windows'][0]['capacity'] is None
    anthropic = parse_response('anthropic',{'five_hour':{'utilization':.5,'resets_at':'2026-10-26T00:00:00Z'}},observed_at=100)
    assert anthropic['windows'][0]['used']==.5  # percentage response is never multiplied into an invented 50%


@pytest.mark.parametrize('url',['http://openrouter.ai/api/v1','https://openrouter.ai.evil.test','https://openrouter.ai:444',
    'https://user:pass@openrouter.ai/api/v1','https://openrouter.ai/api/v1?key=x','https://localhost'])
def test_official_origin_rejects_wrong_binding(url):
    with pytest.raises(ValueError):official_origin('openrouter',url)


def test_unofficial_or_unconfigured_origin_never_uses_transport(monkeypatch):
    monkeypatch.setattr('src.quota_dashboard.official_get',lambda *a:pytest.fail('credential sent to unsupported origin'))
    entry={'provider':'openrouter','identity':'A','api_key':'test-fixture-secret','base_url':'https://proxy.example/v1'}
    result=fetch_native(entry,100)
    assert result['state']=='unknown' and not result['windows']
    assert 'test-fixture-secret' not in json.dumps(result)


def test_import_requires_actual_time_and_never_returns_credential_payload(tmp_path):
    dashboard=QuotaDashboard(tmp_path/'home',reader=lambda:[],clock=lambda:1000)
    p=tmp_path/'quota.json';p.write_text(json.dumps({'data':{'total_credits':20,'total_usage':4}}))
    with pytest.raises(ValueError):dashboard.import_file({'format':'openrouter','path':str(p)})
    view=dashboard.import_file({'format':'openrouter','path':str(p),'observed_at':500})
    assert view['accounts'][0]['state']=='stale' and view['accounts'][0]['observed_at']==500
    view=dashboard.import_file({'format':'openrouter','path':str(p),'observed_at':500})
    assert len(view['accounts'])==1 and view['accounts'][0]['observed_at']==500
    p.write_text(json.dumps({'data':{'total_credits':20,'total_usage':4},'access_token':'never-echo-this'}))
    with pytest.raises(ValueError) as error:dashboard.import_file({'format':'openrouter','path':str(p),'observed_at':1000})
    assert 'never-echo-this' not in str(error.value)
    assert 'never-echo-this' not in dashboard.path.read_text(encoding='utf-8')
    credentials=tmp_path/'auth.json';credentials.write_text('{}')
    with pytest.raises(ValueError):dashboard.import_file({'format':'codex','path':str(credentials),'observed_at':1000})


def test_get_never_fetches_refresh_keeps_context_and_profiles_separate(tmp_path):
    context=ContextVar('profile',default='wrong')
    seen=[];complete=threading.Event()
    def fetch(entry,now):
        seen.append(context.get());complete.set()
        return account('openrouter','A',observed_at=now,windows=[{'label':'credits','unit':'USD','remaining':4,'reset_at':None,'state':'ready'}])
    dashboard=QuotaDashboard(tmp_path/'one',reader=lambda:[{'provider':'openrouter','identity':'A'}],fetcher=fetch,clock=lambda:100)
    other=QuotaDashboard(tmp_path/'two',reader=lambda:[],clock=lambda:100)
    assert dashboard.view()['accounts'][0]['state']=='unknown' and not seen
    token=context.set('one')
    try:dashboard.refresh()
    finally:context.reset(token)
    assert complete.wait(2)
    deadline=time.monotonic()+2
    while dashboard.refreshing and time.monotonic()<deadline:time.sleep(.01)
    assert seen==['one'] and dashboard.view()['accounts'][0]['windows'][0]['remaining']==4
    assert other.view()['accounts']==[]
    assert context.get()=='wrong'


def test_model_expiry_does_not_claim_the_account_expired():
    result=parse_response('openai-codex',{'rate_limit':{'primary_window':{'used_percent':25,'reset_at':500}},
        'additional_rate_limits':[{'rate_limit':{'primary_window':{'used_percent':100,'reset_at':90}}}]},observed_at=100)
    viewed=freshen(result,101)
    assert viewed['state']=='ready' and viewed['windows'][1]['state']=='expired'


def test_normalized_decimal_strings_retain_capacity_used_and_original_time(tmp_path):
    dashboard=QuotaDashboard(tmp_path/'home',reader=lambda:[],clock=lambda:1000)
    p=tmp_path/'quota.json';p.write_text(json.dumps({'schema_version':1,'source':'verified-import','observations':[
        {'account':'actual-import','model':'*','unit':'USD','remaining':'8.50','capacity':'10.00','observed_at':999}]}))
    view=dashboard.import_file({'format':'normalized-v1','path':str(p)})
    assert view['accounts'][0]['windows'][0]['capacity']==10
    assert view['accounts'][0]['windows'][0]['used']==1.5
    assert view['accounts'][0]['observed_at']==999


def test_anthropic_api_key_never_queries_subscription_endpoint(monkeypatch):
    monkeypatch.setattr('src.quota_dashboard.official_get',lambda *a,**kw:pytest.fail('API key is not OAuth usage entitlement'))
    result=fetch_native({'provider':'anthropic','identity':'A','base_url':'https://api.anthropic.com',
                         'api_key':'sk-ant-api03-synthetic-fixture'},100)
    assert result['state']=='unknown' and not result['windows']
    assert 'sk-ant-api03-synthetic-fixture' not in json.dumps(result)


def test_normalized_shorter_expiry_is_preserved(tmp_path):
    now=[100]
    dashboard=QuotaDashboard(tmp_path/'home',reader=lambda:[],clock=lambda:now[0])
    p=tmp_path/'quota.json';p.write_text(json.dumps({'schema_version':1,'source':'actual-snapshot','observations':[
        {'account':'A','model':'*','unit':'requests','remaining':2,'observed_at':100,'max_age_seconds':10}]}))
    dashboard.import_file({'format':'normalized-v1','path':str(p)})
    now[0]=111
    assert dashboard.view()['accounts'][0]['state']=='stale'
    assert dashboard.view()['accounts'][0]['observed_at']==100


def test_refresh_account_ceiling_does_not_send_unbounded_queries(tmp_path,monkeypatch):
    monkeypatch.setattr('src.quota_dashboard.MAX_REFRESH_ACCOUNTS',2)
    entries=[{'provider':'unsupported','identity':str(i)} for i in range(4)]
    calls=[]
    def fetch(entry,now):
        calls.append(entry['identity']);return account(entry['provider'],entry['identity'],reason='unknown')
    dashboard=QuotaDashboard(tmp_path,reader=lambda:entries,fetcher=fetch)
    dashboard.refresh()
    deadline=time.monotonic()+2
    while dashboard.refreshing and time.monotonic()<deadline:time.sleep(.01)
    assert calls==['0','1']
    assert len(dashboard.view()['accounts'])==4
    assert all(row['state']=='unknown' for row in dashboard.view()['accounts'])


def test_refresh_deadline_stops_new_provider_queries_without_waiting(tmp_path):
    tick=[0];calls=[]
    entries=[{'provider':'unsupported','identity':str(i)} for i in range(3)]
    def fetch(entry,now):
        calls.append(entry['identity']);tick[0]=31
        return account(entry['provider'],entry['identity'],reason='unknown')
    dashboard=QuotaDashboard(tmp_path,reader=lambda:entries,fetcher=fetch,monotonic=lambda:tick[0])
    dashboard.refresh()
    deadline=time.monotonic()+2
    while dashboard.refreshing and time.monotonic()<deadline:time.sleep(.01)
    assert calls==['0']
    assert len(dashboard.view()['accounts'])==3
