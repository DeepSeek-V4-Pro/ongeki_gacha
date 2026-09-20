"""打包已提取的51条养成语音，保留原有试听状态。"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from ..growth_core import MAIN_CHARACTER_IDS

def build(source,output):
    source=source.resolve();output=output.resolve()
    data=json.loads((source/'event_voice_catalog.json').read_text(encoding='utf8'))
    rows=data['voices']
    expected={(cid,event) for cid in MAIN_CHARACTER_IDS for event in ('gift_small','gift_large','level_up')}
    if len(rows)!=51 or {(r['character_id'],r['event']) for r in rows}!=expected:
        raise ValueError('养成语音覆盖不完整')
    files=[]
    for row in rows:
        original=(source/row['path']).resolve();target=(output/row['path']).resolve()
        if not original.is_relative_to(source) or not target.is_relative_to(output):raise ValueError('语音路径越界')
        if hashlib.sha256(original.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('语音散列不符')
        files.append((original,target))
    for original,target in files:
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original,target)
    (output/'event_voice_catalog.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(f'Packaged {len(files)} event voices; listening status preserved')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=Path(__file__).parents[1]/'assets/growth')
    args=parser.parse_args();build(args.source,args.output)
