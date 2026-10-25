#!/usr/bin/env python3
"""Stdlib host-agent CLI; no PATH/account discovery and no new agent loop."""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys

MAX_INPUT = 65536
MAX_OUTPUT = 2 * 1024 * 1024
PROFILE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
TASK_ID = re.compile(r"[A-Za-z0-9._-]{1,160}\Z")
SECRET_KEYS = {"api_key", "apikey", "token", "access_token", "refresh_token", "authorization", "password",
               "cookie", "cookies", "headers", "credentials", "environment", "env", "secrets", "ticket"}
COMMANDS = ("status", "quota", "quota-refresh", "extensions", "route-preview", "nodes-list", "nodes-select",
            "task-submit", "task-status", "task-cancel", "worker-status", "setup",
            "models", "models-refresh", "judge-test", "intent-create", "intent-refine", "intent-commit")
OPTIONAL={'models':'free-models','models-refresh':'free-models','judge-test':'local-judge',
          'intent-create':'smart-pointer','intent-refine':'smart-pointer','intent-commit':'smart-pointer'}


class ConfigurationRequired(ValueError):
    pass


def configuration_path(value):
    if value:
        return Path(value).expanduser().resolve()
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_CONFIG_HOME")
    return (Path(base).expanduser() if base else Path.home()/".config")/"misaka-agent/connections.json"


def load_connections(path):
    with path.open('rb') as handle:raw=handle.read(MAX_INPUT+1)
    if len(raw)>MAX_INPUT:raise ValueError('Connection configuration is too large')
    data=parse_json(raw);safe_json(data)
    if (not isinstance(data,dict) or set(data)!={'schema','connections'} or type(data['schema']) is not int
            or data['schema']!=1 or not isinstance(data['connections'],dict)):
        raise ValueError('Invalid connection configuration')
    for name,values in data['connections'].items():
        if (not isinstance(name,str) or not PROFILE.fullmatch(name) or not isinstance(values,dict)
                or set(values)!={'launcher','root','home'}
                or any(not isinstance(value,str) or not value or not Path(value).expanduser().is_absolute() for value in values.values())):
            raise ValueError('Connection paths must be explicit absolute strings')
    return data


def configure(args):
    path=configuration_path(args.config)
    if not args.connection or not PROFILE.fullmatch(args.connection):
        raise ValueError("setup requires a bounded connection name")
    values={}
    for name in ("launcher","root","home"):
        value=getattr(args,name)
        if not value or not Path(value).expanduser().is_absolute():
            raise ValueError("setup requires explicit absolute launcher/root/home paths")
        values[name]=str(Path(value).expanduser().resolve(strict=True))
    if not Path(values['launcher']).is_file() or not (Path(values['root'])/'hermes_cli/main.py').is_file() or not Path(values['home']).is_dir():
        raise ValueError("Selected installation does not exist")
    data={"schema":1,"connections":{}}
    if path.exists():
        data=load_connections(path)
    data['connections'][args.connection]=values
    encoded=json.dumps(data,ensure_ascii=False,indent=2)+'\n'
    if len(encoded.encode())>MAX_INPUT:raise ValueError('Connection configuration is too large')
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'.'+os.urandom(6).hex()+'.tmp')
    try:
        temporary.write_text(encoded,encoding='utf8')
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)
    return {"ok":True,"connection":args.connection,"configuration_file":str(path),"contains_credentials":False}


def select_connection(args):
    if all(getattr(args,name) for name in ('launcher','root','home')):
        return
    if any(getattr(args,name) for name in ('launcher','root','home')) or not args.connection:
        raise ConfigurationRequired()
    path=configuration_path(args.config)
    try:
        data=load_connections(path)
        values=data['connections'][args.connection]
    except (OSError,KeyError):
        raise ConfigurationRequired() from None
    if set(values)!={'launcher','root','home'}:
        raise ValueError("Connection configuration contains unsupported fields")
    for name,value in values.items():setattr(args,name,value)


def safe_json(value, depth=0):
    if depth > 12:
        raise ValueError("JSON nesting exceeds the request limit")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or key.lower() in SECRET_KEYS or key.lower().endswith(("_token", "_password", "_api_key")):
                raise ValueError("Requests must contain task data, not credentials")
            safe_json(item, depth+1)
    elif isinstance(value, list):
        if len(value) > 1000:
            raise ValueError("JSON list exceeds the request limit")
        for item in value:
            safe_json(item, depth+1)


def parse_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("Nonfinite JSON is refused")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def input_body(path):
    if not path:
        return {}
    if path == "-":
        raw = sys.stdin.buffer.read(MAX_INPUT+1)
    else:
        with Path(path).open("rb") as handle:
            raw = handle.read(MAX_INPUT+1)
    if len(raw) > MAX_INPUT:
        raise ValueError("Request exceeds 64 KiB")
    value = parse_json(raw)
    if not isinstance(value, dict):
        raise ValueError("Request must be a JSON object")
    safe_json(value)
    return value


def operation(command, body, task_id):
    simple = {"quota": ("GET", "/quota/dashboard"), "quota-refresh": ("POST", "/quota/refresh"),
              "extensions": ("GET", "/extensions"), "worker-status": ("GET", "/worker")}
    if command in simple:
        if body:
            raise ValueError("This command does not accept request fields")
        return (*simple[command], {})
    if command == "route-preview":
        if set(body) - {"task_id", "goal", "title", "type", "task_type", "requires", "value_level", "toolsets"}:
            raise ValueError("Unknown route-preview field")
        title=body.get('goal',body.get('title'))
        if not isinstance(title,str) or not title.strip() or len(title)>16384:
            raise ValueError('Provide a bounded current task for route preview')
        for key in ('requires','toolsets'):
            values=body.get(key,[])
            if not isinstance(values,list) or len(values)>32 or any(not isinstance(value,str) or len(value)>160 for value in values):
                raise ValueError('Route requirements and toolsets must be bounded name lists')
        return "POST", "/route", {"task": body}
    action = {"nodes-list":"list", "nodes-select":"select", "task-submit":"submit", "task-status":"status", "task-cancel":"cancel"}[command]
    allowed = {"list": set(), "select": {"tools","skills","executors","models","platform","max_actions","workspace_alias"},
               "submit": {"task_id","target","task","workspace_alias","relative_dir","tools","max_actions","tool_calls","task_card"},
               "status": set(), "cancel": set()}[action]
    if set(body) - allowed:
        raise ValueError("Unknown fleet request field; remote shell and absolute workdir are unavailable")
    if action in {"status","cancel"}:
        if not task_id or not TASK_ID.fullmatch(task_id):
            raise ValueError("Provide a stable --task-id")
        body = {"task_id": task_id}
    if action == "submit" and (not isinstance(body.get("task_id"),str) or not TASK_ID.fullmatch(body["task_id"])):
        raise ValueError("task-submit requires a stable task_id")
    return "POST", "/agent/fleet", {**body, "action": action}


def optional_operation(command,body,session_id):
    if command in {'models','models-refresh','judge-test'}:
        if body:raise ValueError('This command uses only existing extension settings')
        return ('GET','/models',{}) if command=='models' else ('POST','/refresh' if command=='models-refresh' else '/test',{})
    allowed={'intent-create':{'context','keywords'},'intent-refine':{'revision','keywords','reject_action_ids','context'},
             'intent-commit':{'revision','action_id'}}[command]
    if set(body)-allowed:raise ValueError('Intent commands accept metadata/keywords/revision only, never screenshots')
    if command=='intent-create':return 'POST','/sessions',body
    if not session_id or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',session_id):raise ValueError('Provide a bounded --session-id')
    return 'POST','/sessions/'+session_id+('/refine' if command=='intent-refine' else '/commit'),body


def run_json(argv, *, cwd, env, timeout):
    result = subprocess.run(argv, cwd=cwd, env=env, input=None, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    if result.returncode or len(result.stdout) > MAX_OUTPUT:
        raise ValueError("Selected launcher did not provide a valid runtime response")
    return parse_json(result.stdout)


def validate_runtime_command(runtime,root):
    if (not isinstance(runtime,list) or len(runtime)<4 or not all(isinstance(value,str) for value in runtime)
            or runtime[1:3]!=['-I','-c']):
        raise ValueError('Launcher did not report the canonical installation-bound runtime command')
    tree=ast.parse(runtime[3])
    inserted=[]
    for node in ast.walk(tree):
        if (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='insert'
                and isinstance(node.func.value,ast.Attribute) and node.func.value.attr=='path'
                and isinstance(node.func.value.value,ast.Name) and node.func.value.value.id=='sys'):
            if (len(node.args)!=2 or not isinstance(node.args[0],ast.Constant) or node.args[0].value!=0
                    or not isinstance(node.args[1],ast.Constant) or not isinstance(node.args[1].value,str)):
                raise ValueError('Unverifiable launcher source installation')
            inserted.append(os.path.normcase(str(Path(node.args[1].value).resolve())))
    if inserted!=[os.path.normcase(str(root.resolve()))]:
        raise ValueError('Launcher and explicit source root do not match')


def connect(args):
    launcher, root, home = (Path(value).expanduser() for value in (args.launcher,args.root,args.home))
    if not all(path.is_absolute() for path in (launcher,root,home)):
        raise ValueError("launcher, root and home must be explicit absolute paths")
    launcher, root, home = launcher.resolve(strict=True), root.resolve(strict=True), home.resolve(strict=True)
    if not launcher.is_file() or not (root/"hermes_cli/main.py").is_file() or not home.is_dir():
        raise ValueError("Selected installation or home does not exist")
    if not PROFILE.fullmatch(args.profile):
        raise ValueError("Invalid explicit profile name")
    expected = home if args.profile == "default" else home/"profiles"/args.profile
    expected = expected.resolve(strict=True)
    if not expected.is_dir() or not expected.is_relative_to(home):
        raise ValueError("Selected profile escapes the explicit home")
    env = os.environ.copy()
    for name in ("PYTHONHOME","PYTHONPATH","VIRTUAL_ENV","HERMES_PROFILE","HERMES_PROFILE_NAME","HERMES_TUI_GATEWAY_URL"):
        env.pop(name,None)
    env["HERMES_HOME"] = str(home)
    env['HERMES_INSTALL_ROOT']=str(root)
    runtime = run_json([str(launcher),"--print-runtime-command"],cwd=root,env=env,timeout=75)
    validate_runtime_command(runtime,root)
    python = Path(runtime[0])
    if not python.is_absolute() or not python.is_file():
        raise ValueError("Launcher reported an invalid runtime interpreter")
    ready = run_json([str(launcher),"--profile",args.profile,"gateway","ensure","--json","--timeout","60"],cwd=root,env=env,timeout=75)
    if not isinstance(ready,dict) or ready.get("state") != "ready" or not isinstance(ready.get("endpoint"),dict):
        raise ValueError("Selected native gateway is not ready")
    endpoint = ready["endpoint"]
    if (not isinstance(endpoint.get('profile_id'),str)
            or os.path.normcase(str(Path(endpoint['profile_id']).resolve())) != os.path.normcase(str(expected))
            or endpoint.get("runtime_protocol") != 1):
        raise ValueError("Gateway profile or protocol does not match the explicit selection")
    if ready.get("client_code_sha") and endpoint.get("code_sha") and ready["client_code_sha"] != endpoint["code_sha"]:
        raise ValueError("Gateway code changed; restart the selected gateway before attaching")
    env["HERMES_HOME"], env["PYTHONPATH"] = str(expected), str(root)
    return python, root, env, endpoint, expected


def call(connection, method, path, body, *, plugin='orchestrator-core'):
    python,root,env,endpoint,home = connection
    payload = {"endpoint":endpoint,"home":str(home),"method":method,"path":path,"body":body,'plugin':plugin}
    script = Path(__file__).with_name("runtime_bridge.py")
    completed = subprocess.run([str(python),"-P","-u",str(script)],cwd=root,env=env,
        input=json.dumps(payload,allow_nan=False).encode(),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=25)
    if completed.returncode or len(completed.stdout)>MAX_OUTPUT:
        return {"ok":False,"error":"native_request_unconfirmed"}
    result = parse_json(completed.stdout)
    if result.get("ok"):
        value = result.get("result",{})
        if isinstance(value,dict) and "error" in value:
            return {"ok":False,"error":"fleet_request_refused_or_unconfirmed","result":value}
        if path == "/extensions":
            for item in value.get("extensions",[]):
                item["availability"] = "available" if item.get("installed") and item.get("enabled") else "unavailable"
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launcher")
    parser.add_argument("--root")
    parser.add_argument("--home",help="Explicit Hermes root; named profiles resolve below its profiles directory")
    parser.add_argument("--connection",help="Explicitly configured connection name")
    parser.add_argument("--config",help="Nonsecret named-connection JSON file")
    parser.add_argument("--profile",default="default")
    parser.add_argument("command",choices=COMMANDS)
    parser.add_argument("--request",help="Bounded task JSON file, or - for stdin")
    parser.add_argument("--task-id")
    parser.add_argument("--session-id")
    args=parser.parse_args(argv)
    dispatched=False
    body={}
    try:
        if args.command=='setup':
            print(json.dumps(configure(args),ensure_ascii=False));return 0
        select_connection(args)
        body=input_body(args.request)
        spec=(optional_operation(args.command,body,args.session_id) if args.command in OPTIONAL else
              None if args.command=="status" else operation(args.command,body,args.task_id))
        if args.command=="status" and body:
            raise ValueError("status does not accept request fields")
        connection=connect(args)
        if args.command=="status":
            extensions=call(connection,"GET","/extensions",{})
            result={"ok":extensions.get("ok",False),"runtime":{"state":"ready","profile":args.profile},"core":extensions}
        elif args.command in OPTIONAL:
            inventory=call(connection,'GET','/extensions',{})
            plugin=OPTIONAL[args.command]
            row=next((item for item in inventory.get('result',{}).get('extensions',[]) if item.get('id')==plugin),{})
            if not inventory.get('ok'):result=inventory
            elif not(row.get('installed') and row.get('enabled')):
                result={'ok':False,'error':'extension_unavailable','extension':plugin,
                        'installed':bool(row.get('installed')),'native_enabled':bool(row.get('enabled'))}
            else:result=call(connection,*spec,plugin=plugin)
        else:
            dispatched=args.command in {'task-submit','task-cancel'}
            result=call(connection,*spec)
        if args.command in {'task-submit','task-cancel'} and not result.get('ok'):
            definite=result.get('result',{}).get('submitted') is False or result.get('http_status') in {400,401,403,404}
            if not definite:
                result.update(error='outcome_unknown',outcome_unknown=True,
                    task_id=body.get('task_id',args.task_id),recovery='Keep this same task_id and query task-status. Do not resubmit with a new ID.')
        print(json.dumps(result,ensure_ascii=False,allow_nan=False))
        return 0 if result.get("ok") else 3
    except ConfigurationRequired:
        print(json.dumps({'ok':False,'error':'configuration_required','help':'Run setup with explicit --launcher, --root, --home and --connection; no PATH or account is guessed.'}))
        return 2
    except (ValueError,OSError,subprocess.SubprocessError,TypeError,KeyError,AttributeError,RecursionError,SyntaxError):
        # Neither launcher stderr, private ticket replies nor arbitrary decoder
        # snippets are public diagnostics; a mutation may already have committed.
        result={"ok":False,"error":"operation_unconfirmed_or_configuration_invalid"}
        if dispatched:
            result.update(error='outcome_unknown',outcome_unknown=True,task_id=body.get('task_id',args.task_id),
                recovery='Keep this same task_id and query task-status. Do not resubmit with a new ID.')
        print(json.dumps(result))
        return 2


if __name__=="__main__":raise SystemExit(main())
