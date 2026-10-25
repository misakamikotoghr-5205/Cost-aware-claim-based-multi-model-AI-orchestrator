#!/usr/bin/env python3
"""Build the independently installable, credential-free Agent Skills ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
FILES=("SKILL.md","README.md","LICENSE","scripts/misaka.py","scripts/runtime_bridge.py",
       "references/commands.md","references/transport.md")


def build(output):
    source=ROOT/'skills/misaka-agent'
    manifest=json.loads((source/'manifest.json').read_text(encoding='utf8'))
    data={}
    for relative in FILES:
        path=source/relative
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent!=ROOT.parent):
            raise ValueError('Skill sources must not be symlinks')
        data[relative]=path.read_bytes()
    manifest['files']={name:hashlib.sha256(body).hexdigest() for name,body in data.items()}
    data['manifest.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,body in sorted(data.items()):
            info=zipfile.ZipInfo('misaka-agent/'+name,date_time=(2020,1,1,0,0,0))
            info.create_system=3
            info.external_attr=0o100644 << 16
            info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,body,compresslevel=9)
    return {'archive':str(output.resolve()),'files':len(data),'version':manifest['version'],
            'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'dist/skills/misaka-agent-0.2.0.zip')
    print(json.dumps(build(parser.parse_args().output),indent=2))
