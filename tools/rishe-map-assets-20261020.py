#!/usr/bin/env python3
"""Restore original Rishe logo, hero, fonts and product media; no database changes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlparse, unquote

VOL = Path("/var/lib/docker/volumes/rishestore_pars_brand_assets/_data")
WP = VOL / "wp-content/uploads"
SAFE = Path("/opt/rishestore-pars/legacy-20261020")
OVERRIDES = {
 "147": "https://rishe.store/wp-content/uploads/2026/04/IMG_20260427_193354-300x300.jpg",
 "249": "https://rishe.store/wp-content/uploads/2026/05/ذرت-1-300x300.png",
 "422": "https://rishe.store/wp-content/uploads/2026/05/نعنا-خشک-1-1-600x600.png",
 "423": "https://rishe.store/wp-content/uploads/2026/05/پرک-گندم-1-1-600x600.png",
 "424": "https://rishe.store/wp-content/uploads/2026/05/پرک-جو-1-1-600x600.png",
}
BRAND = {
 "logo.png": "2026/08/Asset-5@300x.png",
 "hero.png": "2026/06/back-1.png",
 "fonts/danesh.ttf": "2026/08/danesh.ttf",
 "fonts/daneshbd.ttf": "2026/08/daneshbd.ttf",
 "fonts/golpayegani-bold.ttf": "2026/08/SD-Golpayegani-Bold.ttf",
 "fonts/golpayegani-grunge.ttf": "2026/08/SD-Golpayegani-Grunge.ttf",
}
def source(url):
    path=unquote(urlparse(str(url)).path)
    key="/wp-content/uploads/"
    if key not in path: return None
    rel=path.split(key,1)[1]
    return WP/rel

def copy_original(src,dest):
    if src is None or not src.is_file() or src.stat().st_size==0:
        return False
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.is_file() and dest.stat().st_size==src.stat().st_size:
        return True
    if dest.is_file() and dest.parts[-2]=="fonts":
        b=SAFE/"temporary-font-backup"/dest.name
        b.parent.mkdir(parents=True,exist_ok=True)
        if not b.exists(): shutil.copy2(dest,b)
    temp=dest.with_name(dest.name+".restore-temp")
    shutil.copy2(src,temp)
    os.chmod(temp,0o644)
    os.replace(temp,dest)
    return True

def main():
    assert VOL.is_dir() and WP.is_dir()
    results={}
    for dest,rel in BRAND.items():
        results[dest]=copy_original(WP/rel,VOL/dest)
    raw=subprocess.run(["docker","exec","rishestore_pars-app-1","cat","/app/data/product-content.json"],capture_output=True,text=True,check=True)
    content=json.loads(raw.stdout)
    present=[]
    missing=[]
    for legacy_id,item in content.items():
        urls=[OVERRIDES.get(legacy_id),item.get("hero",{}).get("image_url")]
        urls=[u for u in urls if u]
        if urls:
            url=urls[0]
            suffix=Path(unquote(urlparse(url).path)).suffix.lower() or ".jpg"
            target=VOL/"products"/("legacy-"+legacy_id+suffix)
            found=next((source(u) for u in urls if source(u) is not None and source(u).is_file()),None)
            (present if copy_original(found,target) else missing).append("product:"+legacy_id)
        narrative=(item.get("narrative") or {}).get("image_url")
        hero=(item.get("hero") or {}).get("image_url")
        if narrative and narrative!=hero:
            suffix=Path(unquote(urlparse(narrative).path)).suffix.lower() or ".jpg"
            found=source(narrative)
            target=VOL/"products"/("legacy-"+legacy_id+"-story"+suffix)
            (present if copy_original(found,target) else missing).append("story:"+legacy_id)
    print("BRAND_RESTORED=",results)
    print("PRODUCT_STORY_FILES_COPIED=",len(present))
    print("PRODUCT_STORY_FILES_MISSING=",missing)

if __name__=="__main__":
    main()
