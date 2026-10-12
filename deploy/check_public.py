#!/usr/bin/env python3
import argparse,re,subprocess,sys
from pathlib import Path
PATTERNS=[('private_ip',re.compile(r'192\.168\.31\.[0-9]{1,3}')),('username',re.compile(r'hetuluoshu')),('old_path',re.compile(r'video-pipeline|/Users/[^/]+/tools')),('secret',re.compile(r'(?i)(?:sk-|ghp_|xox[baprs]-)[A-Za-z0-9_-]{8,}'))]
SENSITIVE={'.env','.pem','.key','credentials','config/workbench.config.json'}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--staged',action='store_true');a=ap.parse_args();cmd=['git','diff','--cached','--name-only'] if a.staged else ['git','ls-files'];p=subprocess.run(cmd,capture_output=True,text=True);files=[Path(x) for x in p.stdout.splitlines() if x]
 bad=[]
 for f in files:
  if any(part in SENSITIVE or f.name in SENSITIVE for part in f.parts):bad.append((str(f),0,'sensitive file'));continue
  try:
   if f.stat().st_size>50*1024*1024:bad.append((str(f),0,'large media'));continue
   text=f.read_text(encoding='utf8',errors='ignore')
  except Exception:continue
  for i,line in enumerate(text.splitlines(),1):
   for name,rx in PATTERNS:
    if rx.search(line) and f.name!='check_public.py':bad.append((str(f),i,name))
 print('\n'.join(f'{x[0]}:{x[1]}:{x[2]}' for x in bad));return 1 if bad else 0
if __name__=='__main__':raise SystemExit(main())
